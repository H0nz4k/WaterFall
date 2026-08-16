from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
import random
import re
import statistics
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # mock/development mode can run without pyserial
    serial = None
    list_ports = None
from fastapi import Body, FastAPI, File, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .ble_watch import BleWatcher
from .capture_store import CaptureStore
from .events import EventRecorder
from .nfc_watch import NfcWatcher
from .pcap_tools import PcapInspector
from .relay import RelayController
from .rf_analysis import detect_regions
from .wifi_watch import WifiMonitorWatcher


VERSION = "0.4.5"
log = logging.getLogger("waterfall")
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
CONFIG_PATH = Path(
    os.environ.get(
        "WATERFALL_CONFIG",
        os.environ.get("OPENVUSION_RF_CONFIG", BASE_DIR / "config.json"),
    )
)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        example = BASE_DIR / "config.example.json"
        if example.exists():
            return json.loads(example.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Config nenalezen: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = load_config()
HISTORY_LIMIT = max(30, int(CONFIG.get("history_sweeps", 240)))


@dataclass
class State:
    probe_connected: bool = False
    probe_port: str = ""
    probe_product: str = ""
    probe_serial: str = ""
    probe_error: str = ""
    firmware: str = ""
    scan_enabled: bool = False
    watch_enabled: bool = False
    watch_freq_mhz: int | None = None
    watch_threshold_dbm: int | None = None
    watch_sample_us: int | None = None
    nfc_connected: bool = False
    nfc_error: str = ""
    nfc_present: bool = False
    nfc_uid: str = ""
    ble_connected: bool = False
    ble_error: str = ""
    wifi_connected: bool = False
    wifi_error: str = ""
    relay_available: bool = False
    relay_power_on: bool = False
    sweep_count: int = 0
    burst_count: int = 0
    recording: bool = False
    recording_file: str = ""
    experiment_recording: bool = False
    experiment_file: str = ""
    capture_active: bool = False
    capture_session_id: int | None = None
    capture_db: str = ""
    last_rx_iso: str = ""
    last_line: str = ""
    mock_mode: bool = False
    peak_count: int = 0


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def send(self, payload: dict[str, Any]):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(payload), self.loop)


class CsvWriter:
    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.fp = None
        self.writer = None
        self.path: Path | None = None

    def start(self) -> str:
        with self.lock:
            if self.fp:
                return str(self.path)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.path = self.directory / f"waterfall_rf_{stamp}.csv"
            self.fp = self.path.open("w", newline="", encoding="utf-8")
            self.writer = csv.writer(self.fp)
            self.writer.writerow([
                "host_time", "sweep", "device_ms", "freq_mhz", "rssi_dbm",
                "peak_freq_mhz", "peak_rssi_dbm", "median_dbm", "mean_dbm",
            ])
            self.fp.flush()
            return str(self.path)

    def stop(self):
        with self.lock:
            if self.fp:
                self.fp.flush()
                self.fp.close()
            self.fp = None
            self.writer = None

    def write(self, sweep: dict[str, Any]):
        with self.lock:
            if not self.writer:
                return
            summary = sweep.get("summary", {})
            for p in sweep["points"]:
                self.writer.writerow([
                    sweep["host_time"], sweep["sweep"], sweep["device_ms"],
                    p["freq_mhz"], p["rssi_dbm"],
                    summary.get("peak_freq_mhz", ""), summary.get("peak_rssi_dbm", ""),
                    summary.get("median_dbm", ""), summary.get("mean_dbm", ""),
                ])
            self.fp.flush()


hub = Hub()
state = State(mock_mode=bool(CONFIG.get("mock_mode", False)))
csv_writer = CsvWriter(CONFIG.get("csv_dir", "/var/lib/waterfall/captures"))
events = EventRecorder(CONFIG.get("experiment_dir", "/var/lib/waterfall/experiments"))
history: deque[dict[str, Any]] = deque(maxlen=HISTORY_LIMIT)
history_lock = threading.Lock()

capture_cfg = CONFIG.get("capture", {})
capture_db = capture_cfg.get("db_path", "/var/lib/waterfall/capture/waterfall.sqlite3")
capture_store = CaptureStore(capture_db)
state.capture_db = str(capture_store.path)
state.peak_count = capture_store.peak_count()

pcap_cfg = CONFIG.get("pcap", {})
pcap_inspector = PcapInspector(
    pcap_cfg.get("directory", "/var/lib/waterfall/pcap")
)

runtime_settings = {
    "rf_region_threshold_db": float(CONFIG.get("analysis", {}).get("rf_region_threshold_db", 12.0)),
    "capture_sweeps": bool(CONFIG.get("analysis", {}).get("capture_sweeps", True)),
    "capture_regions": bool(CONFIG.get("analysis", {}).get("capture_regions", True)),
}

relay_cfg = CONFIG.get("relay", {})
relay = (
    RelayController(
        relay_cfg.get("gpio_bcm", 17),
        relay_cfg.get("active_low", True),
        relay_cfg.get("default_power_on", True),
    )
    if relay_cfg.get("enabled", False)
    else None
)
if relay:
    state.relay_available = relay.status.available
    state.relay_power_on = relay.status.power_on


def broadcast_state():
    hub.send({"type": "state", "state": asdict(state)})


def capture_event(**kwargs) -> int | None:
    event_id = capture_store.add_event(**kwargs)
    if event_id is not None:
        payload = {"id": event_id, **kwargs}
        payload.setdefault("host_time", datetime.now().isoformat(timespec="milliseconds"))
        hub.send({"type": "capture_event", "event": payload})
    return event_id


def upsert_device(**kwargs):
    capture_store.upsert_device(**kwargs)
    hub.send({"type": "device_update", "device": kwargs})


def note_peak(**kwargs) -> dict[str, Any]:
    peak = capture_store.upsert_peak(**kwargs)
    state.peak_count = capture_store.peak_count()
    hub.send({"type": "peak_update", "peak": peak, "count": state.peak_count})
    return peak


def emit_marker(kind: str, label: str, source: str = "SERVER", **data):
    marker = events.emit(kind, label, source, **data)
    capture_event(
        source=source,
        protocol="MARKER",
        event_type=kind,
        host_time=marker.host_time,
        summary=label,
        raw_text=json.dumps(data, ensure_ascii=False) if data else None,
        metadata=data,
    )
    hub.send({"type": "marker", "marker": asdict(marker)})
    return marker


def update_nfc_state(connected: bool, error: str, present: bool, uid: str):
    state.nfc_connected = bool(connected)
    state.nfc_error = error or ""
    state.nfc_present = bool(present)
    state.nfc_uid = uid or ""
    if uid:
        upsert_device(
            device_id=f"NFC:{uid}", source="TWN4", protocol="NFC", address=uid,
            name="VUSION tag" if uid == CONFIG.get("nfc_reader", {}).get("expected_uid") else "",
            kind="NFC tag", metadata={"present": bool(present)},
        )
    broadcast_state()


def update_ble_state(connected: bool, error: str):
    state.ble_connected = bool(connected)
    state.ble_error = error or ""
    broadcast_state()


def update_wifi_state(connected: bool, error: str):
    state.wifi_connected = bool(connected)
    state.wifi_error = error or ""
    broadcast_state()


def summarize_points(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [p["rssi_dbm"] for p in points]
    if not values:
        return {}
    peak = max(points, key=lambda p: p["rssi_dbm"])
    med = statistics.median(values)
    mean = statistics.fmean(values)
    ordered = sorted(values)
    p90_idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.9) - 1))
    return {
        "peak_freq_mhz": peak["freq_mhz"],
        "peak_rssi_dbm": peak["rssi_dbm"],
        "median_dbm": round(med, 2),
        "mean_dbm": round(mean, 2),
        "p90_dbm": ordered[p90_idx],
        "min_dbm": min(values), "max_dbm": max(values),
        "dynamic_range_db": round(max(values) - min(values), 2),
        "bins": len(values),
        "above_median_10db": sum(1 for v in values if v >= med + 10),
        "above_median_20db": sum(1 for v in values if v >= med + 20),
    }


def register_sweep(sweep: dict[str, Any]):
    sweep["summary"] = summarize_points(sweep["points"])
    sweep["regions"] = detect_regions(
        sweep["points"], threshold_db=float(runtime_settings["rf_region_threshold_db"])
    )
    with history_lock:
        history.append(sweep)
    state.sweep_count = int(sweep["sweep"])
    state.last_rx_iso = sweep["host_time"]
    csv_writer.write(sweep)

    if runtime_settings["capture_sweeps"]:
        s = sweep["summary"]
        capture_event(
            source="NRF52840", protocol="RF_ENERGY", event_type="RF_SWEEP",
            host_time=sweep["host_time"], freq_mhz=s.get("peak_freq_mhz"),
            rssi_dbm=s.get("peak_rssi_dbm"),
            summary=f"Sweep #{sweep['sweep']} peak {s.get('peak_freq_mhz')} MHz {s.get('peak_rssi_dbm')} dBm",
            raw_text=sweep.get("raw_line"),
            metadata={
                "device_ms": sweep["device_ms"], "summary": s,
                "points": sweep["points"], "rf_errors": sweep.get("rf_errors", []),
                "interpretation": "Sekvenční RSSI/energy sweep; není to dekódovaný paket.",
            },
        )

    if runtime_settings["capture_regions"]:
        for region in sweep["regions"]:
            capture_event(
                source="RF_ANALYZER", protocol=region["protocol_hint"],
                event_type="RF_REGION", host_time=sweep["host_time"],
                freq_mhz=region["peak_freq_mhz"], rssi_dbm=region["peak_rssi_dbm"],
                summary=f"{region['label']} {region['start_mhz']}–{region['end_mhz']} MHz",
                metadata=region,
            )

    for region in sweep["regions"]:
        note_peak(
            freq_mhz=int(region["peak_freq_mhz"]),
            rssi_dbm=region.get("peak_rssi_dbm"),
            guess=region.get("label", ""),
            protocol_hint=region.get("protocol_hint", ""),
            explanation=region.get("explanation", ""),
            width_mhz=region.get("width_mhz"),
        )

    hub.send(sweep)


def register_burst(burst: dict[str, Any]):
    state.burst_count = max(state.burst_count, int(burst.get("seq", 0)))
    state.last_rx_iso = burst["host_time"]
    capture_event(
        source="NRF52840", protocol="RF_ENERGY", event_type="RF_BURST",
        host_time=burst["host_time"], freq_mhz=burst["freq_mhz"],
        rssi_dbm=burst["peak_rssi_dbm"],
        summary=(f"RF burst #{burst['seq']} {burst['freq_mhz']} MHz peak "
                 f"{burst['peak_rssi_dbm']} dBm, {burst['duration_us']} us"),
        raw_text=burst.get("raw_line"), metadata=burst,
    )
    note_peak(
        freq_mhz=int(burst["freq_mhz"]),
        rssi_dbm=burst.get("peak_rssi_dbm"),
        guess="WATCH burst",
        protocol_hint="RF_ENERGY",
        explanation="Nadprahová energie na hlídané frekvenci. Identita zařízení z toho neplyne.",
        burst=True,
    )
    hub.send({"type": "burst", **burst})


def parse_vid_pid(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return int(str(value), 16)


def discover_probe(cfg: dict[str, Any]) -> dict[str, str]:
    requested = str(cfg.get("serial_port", "auto"))
    if requested and requested.lower() != "auto":
        return {"device": requested, "product": "", "serial_number": ""}
    vid = parse_vid_pid(cfg.get("vid"), 0x2FE3)
    pid = parse_vid_pid(cfg.get("pid"), 0x0001)
    product_prefix = str(cfg.get("product_prefix", "OpenVusion RF Probe"))
    wanted_serial = str(cfg.get("serial_number", "")).strip()
    if list_ports is None:
        raise RuntimeError("pyserial není nainstalován")
    matches = []
    for p in list_ports.comports():
        if p.vid != vid or p.pid != pid:
            continue
        if product_prefix and not (p.product or p.description or "").startswith(product_prefix):
            continue
        if wanted_serial and (p.serial_number or "") != wanted_serial:
            continue
        matches.append(p)
    if len(matches) != 1:
        desc = ", ".join(
            f"{p.device}:{p.product or p.description}:{p.serial_number}" for p in matches
        ) or "žádný"
        raise RuntimeError(
            f"RF probe auto-detect očekává 1 zařízení {vid:04x}:{pid:04x}, "
            f"nalezeno {len(matches)} ({desc})"
        )
    p = matches[0]
    return {"device": p.device, "product": p.product or p.description or "", "serial_number": p.serial_number or ""}


class ProbeWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q: deque[str] = deque()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    def command(self, value: str):
        with self.lock:
            self.q.append(value)

    def commands(self) -> list[str]:
        out = []
        with self.lock:
            while self.q:
                out.append(self.q.popleft())
        return out

    @staticmethod
    def parse_sweep(line: str) -> dict[str, Any] | None:
        if not line.startswith("SWEEP,"):
            return None
        try:
            parts = line.split(",")
            if len(parts) < 4:
                return None
            points, errors = [], []
            for item in parts[3:]:
                if ":" not in item:
                    continue
                f, r = item.split(":", 1)
                freq = int(f)
                if r.startswith("ERR"):
                    errors.append({"freq_mhz": freq, "error": r})
                else:
                    points.append({"freq_mhz": freq, "rssi_dbm": int(r)})
            if not points:
                return None
            return {
                "type": "sweep", "host_time": datetime.now().isoformat(timespec="milliseconds"),
                "sweep": int(parts[1]), "device_ms": int(parts[2]), "points": points,
                "rf_errors": errors, "raw_line": line,
            }
        except Exception:
            return None

    @staticmethod
    def parse_burst(line: str) -> dict[str, Any] | None:
        if not line.startswith("BURST,"):
            return None
        try:
            p = line.split(",")
            if len(p) != 9:
                return None
            return {
                "host_time": datetime.now().isoformat(timespec="milliseconds"),
                "seq": int(p[1]), "start_ms": int(p[2]), "freq_mhz": int(p[3]),
                "threshold_dbm": int(p[4]), "peak_rssi_dbm": int(p[5]),
                "avg_rssi_dbm": int(p[6]), "duration_us": int(p[7]), "samples": int(p[8]),
                "raw_line": line,
            }
        except Exception:
            return None

    def _send(self, ser: serial.Serial, cmd: str):
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()

    def run(self):
        cfg = CONFIG.get("rf_probe", {})
        if serial is None:
            state.probe_error = "pyserial není nainstalován"
            broadcast_state()
            return
        if not cfg.get("enabled", True):
            return
        while not self.stop_event.is_set():
            try:
                found = discover_probe(cfg)
                port = found["device"]
                state.probe_port, state.probe_product, state.probe_serial = port, found["product"], found["serial_number"]
                with serial.Serial(
                    port, int(cfg.get("baudrate", 115200)), timeout=0.25, write_timeout=2.0,
                    rtscts=False, dsrdtr=False, xonxoff=False,
                ) as ser:
                    try: ser.dtr = True
                    except Exception: pass
                    try: ser.rts = False
                    except Exception: pass
                    time.sleep(max(0.1, int(cfg.get("connect_delay_ms", 350)) / 1000.0))
                    ser.reset_input_buffer()
                    state.probe_connected = True
                    state.probe_error = ""
                    broadcast_state()
                    emit_marker("RF_PROBE_ONLINE", f"{port} {found['product']}".strip(), "NRF52840", serial=found["serial_number"])
                    self._send(ser, "PING")
                    time.sleep(0.05)
                    self._send(ser, "INFO")
                    if cfg.get("auto_scan", True):
                        time.sleep(0.15)
                        self._send(ser, "SCAN START")

                    while not self.stop_event.is_set():
                        for cmd in self.commands():
                            self._send(ser, cmd)
                        raw = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        state.last_line = line
                        sweep = self.parse_sweep(line)
                        if sweep:
                            register_sweep(sweep)
                            continue
                        burst = self.parse_burst(line)
                        if burst:
                            register_burst(burst)
                            continue
                        if line.startswith("FW="):
                            state.firmware = line[3:]
                            broadcast_state()
                        elif line.startswith("PONG"):
                            hub.send({"type": "log", "line": line})
                        elif line.startswith("OK SCAN=ON"):
                            state.scan_enabled = True
                            state.watch_enabled = False
                            broadcast_state(); hub.send({"type": "log", "line": line})
                        elif line.startswith("OK SCAN=OFF"):
                            state.scan_enabled = False
                            broadcast_state(); hub.send({"type": "log", "line": line})
                        elif line.startswith("OK WATCH=ON"):
                            state.watch_enabled = True
                            state.scan_enabled = False
                            m = re.search(r"FREQ=(\d+).*THRESH=(-?\d+).*SAMPLE_US=(\d+)", line, re.I)
                            if m:
                                state.watch_freq_mhz = int(m.group(1)); state.watch_threshold_dbm = int(m.group(2)); state.watch_sample_us = int(m.group(3))
                            broadcast_state(); hub.send({"type": "log", "line": line})
                        elif line.startswith("OK WATCH=OFF"):
                            state.watch_enabled = False
                            broadcast_state(); hub.send({"type": "log", "line": line})
                        else:
                            hub.send({"type": "log", "line": line})
            except Exception as exc:
                state.probe_connected = False
                state.scan_enabled = False
                state.watch_enabled = False
                state.probe_error = f"{type(exc).__name__}: {exc}"
                log.warning("PROBE DROP %s", state.probe_error)
                hub.send({"type": "log", "line": f"PROBE DROP {state.probe_error}"})
                broadcast_state()
                time.sleep(1.5)


class MockWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.sweep = 0
        self.scanning = True
        self.watching = False
        self.watch_freq = 2453
        self.watch_threshold = -75
        self.watch_sample_us = 200
        self.burst_seq = 0
        self.start_ms = time.monotonic()

    def command(self, value: str):
        cmd = value.upper()
        if cmd == "SCAN START":
            self.scanning = True; self.watching = False; state.scan_enabled = True; state.watch_enabled = False
        elif cmd == "SCAN STOP":
            self.scanning = False; state.scan_enabled = False
        elif cmd.startswith("WATCH START"):
            p = value.split()
            if len(p) >= 5:
                self.watch_freq = int(p[2]); self.watch_threshold = int(p[3]); self.watch_sample_us = int(p[4])
            self.scanning = False; self.watching = True
            state.scan_enabled = False; state.watch_enabled = True
            state.watch_freq_mhz = self.watch_freq; state.watch_threshold_dbm = self.watch_threshold; state.watch_sample_us = self.watch_sample_us
        elif cmd == "WATCH STOP":
            self.watching = False; state.watch_enabled = False
        elif cmd == "ONCE":
            self._sweep_once()
        hub.send({"type": "log", "line": f"MOCK {value}"})
        broadcast_state()

    def _sweep_once(self):
        self.sweep += 1
        points = []
        pulse = (self.sweep % 18) in (5, 6)
        for freq in range(2400, 2501):
            noise = random.gauss(-101, 1.5)
            wifi = 18 * math.exp(-((freq - 2437) ** 2) / (2 * 7.0 ** 2)) if 2422 <= freq <= 2452 else 0.0
            narrow = 38 * math.exp(-((freq - 2453) ** 2) / (2 * 0.8 ** 2)) if pulse else 0.0
            rssi = int(max(-110, min(-25, noise + wifi + narrow)))
            points.append({"freq_mhz": freq, "rssi_dbm": rssi})
        register_sweep({
            "type": "sweep", "host_time": datetime.now().isoformat(timespec="milliseconds"),
            "sweep": self.sweep, "device_ms": int((time.monotonic() - self.start_ms) * 1000),
            "points": points, "rf_errors": [], "raw_line": "MOCK SWEEP",
        })

    def run(self):
        state.probe_connected = True; state.probe_port = "MOCK"; state.probe_product = "OpenVusion RF Probe MOCK"
        state.probe_serial = "MOCK"; state.firmware = "OpenVusion_RF_Probe_v0.7.1_MOCK"; state.scan_enabled = True
        broadcast_state()
        while not self.stop_event.is_set():
            if self.scanning:
                self._sweep_once(); time.sleep(0.45)
            elif self.watching:
                time.sleep(0.7)
                self.burst_seq += 1
                duration = random.randint(350, 6500)
                peak = random.randint(-68, -42)
                samples = max(2, duration // max(50, self.watch_sample_us))
                register_burst({
                    "host_time": datetime.now().isoformat(timespec="milliseconds"),
                    "seq": self.burst_seq, "start_ms": int((time.monotonic()-self.start_ms)*1000),
                    "freq_mhz": self.watch_freq, "threshold_dbm": self.watch_threshold,
                    "peak_rssi_dbm": peak, "avg_rssi_dbm": peak-random.randint(2,10),
                    "duration_us": duration, "samples": samples, "raw_line": "MOCK BURST",
                })
            else:
                time.sleep(0.1)


probe = MockWorker() if CONFIG.get("mock_mode", False) else ProbeWorker()
nfc: NfcWatcher | None = None
ble: BleWatcher | None = None
wifi: WifiMonitorWatcher | None = None


def start_ble_watcher() -> tuple[bool, str]:
    global ble
    bcfg = CONFIG.get("ble_observer", {})
    if CONFIG.get("mock_mode", False):
        return False, "BLE observer je v mock režimu vypnutý"
    if not bcfg.get("enabled", False):
        return False, "BLE observer je zakázaný v configu"
    if ble is not None and ble.is_alive():
        return True, "BLE observer už běží"
    ble = BleWatcher(
        emit=capture_event,
        device_cb=upsert_device,
        state_cb=update_ble_state,
        scan_mode=str(bcfg.get("scan_mode", "active")),
        allow_active_fallback=bool(bcfg.get("allow_active_fallback", False)),
    )
    ble.start()
    emit_marker(
        "BLE_OBSERVER_START",
        f"BLE {bcfg.get('scan_mode', 'active')}",
        "WEB",
    )
    return True, "BLE observer spuštěn"


def stop_ble_watcher() -> tuple[bool, str]:
    global ble
    if ble is None or not ble.is_alive():
        state.ble_connected = False
        broadcast_state()
        return True, "BLE observer už stojí"
    ble.stop_event.set()
    emit_marker("BLE_OBSERVER_STOP", "BLE observer stop", "WEB")
    return True, "BLE observer zastavován"


def start_wifi_watcher() -> tuple[bool, str]:
    global wifi
    wcfg = CONFIG.get("wifi_monitor", {})
    if CONFIG.get("mock_mode", False):
        return False, "Wi-Fi monitor je v mock režimu vypnutý"
    if not wcfg.get("enabled", False):
        return False, "Wi-Fi monitor je zakázaný v configu"
    if wifi is not None and wifi.is_alive():
        return True, "Wi-Fi monitor už běží"
    wifi = WifiMonitorWatcher(
        interface=wcfg.get("interface", "wlan1mon"),
        emit=capture_event,
        device_cb=upsert_device,
        state_cb=update_wifi_state,
    )
    wifi.start()
    emit_marker(
        "WIFI_MONITOR_START",
        str(wcfg.get("interface", "wlan1mon")),
        "WEB",
    )
    return True, "Wi-Fi monitor spuštěn"


def stop_wifi_watcher() -> tuple[bool, str]:
    global wifi
    if wifi is None or not wifi.is_alive():
        state.wifi_connected = False
        broadcast_state()
        return True, "Wi-Fi monitor už stojí"
    wifi.stop_event.set()
    emit_marker("WIFI_MONITOR_STOP", "Wi-Fi monitor stop", "WEB")
    return True, "Wi-Fi monitor zastavován"


app = FastAPI(title=f"WaterFall v{VERSION}")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def startup():
    global nfc
    hub.loop = asyncio.get_running_loop()
    probe.start()

    ncfg = CONFIG.get("nfc_reader", {})
    if ncfg.get("enabled", False):
        nfc = NfcWatcher(
            port=ncfg["serial_port"], baudrate=int(ncfg.get("baudrate", 9600)),
            expected_uid=ncfg.get("expected_uid", ""), poll_interval_ms=int(ncfg.get("poll_interval_ms", 80)),
            emit=emit_marker, state_cb=update_nfc_state,
        )
        nfc.start()

    bcfg = CONFIG.get("ble_observer", {})
    if bcfg.get("enabled", False) and bcfg.get("auto_start", False):
        start_ble_watcher()

    wcfg = CONFIG.get("wifi_monitor", {})
    if wcfg.get("enabled", False) and wcfg.get("auto_start", False):
        start_wifi_watcher()

    emit_marker("SERVER_START", f"WaterFall v{VERSION}")


@app.on_event("shutdown")
async def shutdown():
    probe.stop_event.set()
    if nfc: nfc.stop_event.set()
    if ble: ble.stop_event.set()
    if wifi: wifi.stop_event.set()
    csv_writer.stop()
    events.stop()
    if capture_store.active_session_id is not None:
        capture_store.stop_session()


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def api_state():
    return JSONResponse(asdict(state))


@app.get("/api/history")
async def api_history():
    with history_lock:
        return JSONResponse(list(history))


@app.get("/api/health")
async def health():
    return {"ok": True, "version": VERSION, "probe_connected": state.probe_connected,
            "sweep_count": state.sweep_count, "capture_active": state.capture_active}


@app.get("/api/settings")
async def get_settings():
    return {
        "runtime": runtime_settings,
        "rf_probe": CONFIG.get("rf_probe", {}),
        "ble_observer": CONFIG.get("ble_observer", {}),
        "wifi_monitor": CONFIG.get("wifi_monitor", {}),
        "pcap": {**CONFIG.get("pcap", {}), **pcap_inspector.capabilities()},
        "limits": {"energy_identity": "RSSI samo o sobě neurčuje zařízení ani paket."},
    }


@app.post("/api/settings/runtime")
async def set_runtime_settings(payload: dict[str, Any] = Body(default={})):
    if "rf_region_threshold_db" in payload:
        v = float(payload["rf_region_threshold_db"])
        if not 3 <= v <= 60:
            return JSONResponse({"ok": False, "error": "rf_region_threshold_db musí být 3..60"}, status_code=400)
        runtime_settings["rf_region_threshold_db"] = v
    for k in ("capture_sweeps", "capture_regions"):
        if k in payload:
            runtime_settings[k] = bool(payload[k])
    return {"ok": True, "runtime": runtime_settings}


@app.post("/api/command/{command}")
async def command(command: str):
    mapping = {"scan_start": "SCAN START", "scan_stop": "SCAN STOP", "once": "ONCE", "ping": "PING", "info": "INFO", "watch_stop": "WATCH STOP"}
    if command not in mapping:
        return JSONResponse({"ok": False, "error": "unknown command"}, status_code=404)
    probe.command(mapping[command])
    return {"ok": True}


@app.post("/api/range/{first}/{last}")
async def set_range(first: int, last: int):
    if not (0 <= first <= last <= 100):
        return JSONResponse({"ok": False, "error": "range musí splňovat 0 <= first <= last <= 100"}, status_code=400)
    probe.command(f"RANGE {first} {last}")
    return {"ok": True, "first": first, "last": last}


@app.post("/api/dwell/{ms}")
async def set_dwell(ms: int):
    if not (1 <= ms <= 100):
        return JSONResponse({"ok": False, "error": "dwell musí být 1..100 ms"}, status_code=400)
    probe.command(f"DWELL {ms}")
    return {"ok": True, "ms": ms}


@app.post("/api/step/{mhz}")
async def set_step(mhz: int):
    if mhz not in (1, 2, 5, 10):
        return JSONResponse({"ok": False, "error": "step musí být 1, 2, 5 nebo 10 MHz"}, status_code=400)
    probe.command(f"STEP {mhz}")
    return {"ok": True, "mhz": mhz}


@app.post("/api/rssimode/{mode}")
async def set_rssi_mode(mode: str):
    mode = mode.upper()
    if mode not in ("LAST", "MAX", "AVG"):
        return JSONResponse({"ok": False, "error": "mode musí být LAST, MAX nebo AVG"}, status_code=400)
    probe.command(f"RSSIMODE {mode}")
    return {"ok": True, "mode": mode}


@app.post("/api/watch/start/{freq_mhz}/{threshold_dbm}/{sample_us}")
async def watch_start(freq_mhz: int, threshold_dbm: int, sample_us: int):
    if not (2400 <= freq_mhz <= 2500):
        return JSONResponse({"ok": False, "error": "freq musí být 2400..2500 MHz"}, status_code=400)
    if not (-110 <= threshold_dbm <= -20):
        return JSONResponse({"ok": False, "error": "threshold musí být -110..-20 dBm"}, status_code=400)
    if not (100 <= sample_us <= 10000):
        return JSONResponse({"ok": False, "error": "sample_us musí být 100..10000"}, status_code=400)
    probe.command(f"WATCH START {freq_mhz} {threshold_dbm} {sample_us}")
    return {"ok": True, "freq_mhz": freq_mhz, "threshold_dbm": threshold_dbm, "sample_us": sample_us}



@app.post("/api/ble/start")
async def ble_start():
    ok, message = start_ble_watcher()
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=409)
    return {"ok": True, "message": message}


@app.post("/api/ble/stop")
async def ble_stop():
    ok, message = stop_ble_watcher()
    return {"ok": ok, "message": message}


@app.post("/api/wifi/start")
async def wifi_start():
    ok, message = start_wifi_watcher()
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=409)
    return {"ok": True, "message": message}


@app.post("/api/wifi/stop")
async def wifi_stop():
    ok, message = stop_wifi_watcher()
    return {"ok": ok, "message": message}


@app.get("/api/pcap/capabilities")
async def pcap_capabilities():
    return {
        "enabled": bool(CONFIG.get("pcap", {}).get("enabled", True)),
        **pcap_inspector.capabilities(),
    }


@app.get("/api/pcap/files")
async def pcap_files():
    return {"items": pcap_inspector.list_files(), **pcap_inspector.capabilities()}


@app.post("/api/pcap/import")
async def pcap_import(file: UploadFile = File(...)):
    if not CONFIG.get("pcap", {}).get("enabled", True):
        return JSONResponse({"ok": False, "error": "PCAP inspector je zakázaný"}, status_code=403)
    try:
        data = await file.read()
        target = pcap_inspector.save_bytes(file.filename or "capture.pcapng", data)
        emit_marker(
            "PCAP_IMPORT",
            target.name,
            "WEB",
            size=len(data),
        )
        return {"ok": True, "name": target.name, "size": len(data)}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/api/pcap/{name}/packets")
async def pcap_packets(
    name: str,
    display_filter: str = "",
    limit: int = Query(default=500, ge=1, le=5000),
):
    try:
        return {
            "items": pcap_inspector.packet_list(name, display_filter, limit),
            "name": name,
            "display_filter": display_filter,
        }
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "capture nenalezen"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/api/pcap/{name}/frame/{frame_number}")
async def pcap_frame(name: str, frame_number: int):
    try:
        return pcap_inspector.frame_detail(name, frame_number)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "capture nenalezen"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/api/pcap/{name}/download")
async def pcap_download(name: str):
    try:
        target = pcap_inspector.get_path(name)
        return FileResponse(target, filename=target.name)
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "capture nenalezen"}, status_code=404)


@app.delete("/api/pcap/{name}")
async def pcap_delete(name: str):
    try:
        pcap_inspector.delete(name)
        return {"ok": True}
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "capture nenalezen"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/record/start")
async def rec_start():
    path = csv_writer.start(); state.recording = True; state.recording_file = path
    emit_marker("RF_RECORD_START", Path(path).name, "WEB")
    await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "path": path}


@app.post("/api/record/stop")
async def rec_stop():
    csv_writer.stop(); emit_marker("RF_RECORD_STOP", Path(state.recording_file).name if state.recording_file else "", "WEB")
    state.recording = False; await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "path": state.recording_file}


@app.get("/api/record/download")
async def rec_download():
    p = Path(state.recording_file)
    if not state.recording_file or not p.exists():
        return JSONResponse({"ok": False, "error": "CSV zatím neexistuje"}, status_code=404)
    return FileResponse(p, filename=p.name)


@app.post("/api/experiment/start")
async def exp_start():
    path = events.start(); state.experiment_recording = True; state.experiment_file = path
    emit_marker("EXPERIMENT_START", Path(path).name)
    await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "path": path}


@app.post("/api/experiment/stop")
async def exp_stop():
    emit_marker("EXPERIMENT_STOP", "manual"); events.stop(); state.experiment_recording = False
    await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "path": state.experiment_file}


@app.get("/api/experiment/download")
async def exp_download():
    p = Path(state.experiment_file)
    if not state.experiment_file or not p.exists():
        return JSONResponse({"ok": False, "error": "experiment JSONL zatím neexistuje"}, status_code=404)
    return FileResponse(p, filename=p.name)


@app.post("/api/capture/start")
async def capture_start(payload: dict[str, Any] | None = Body(default=None)):
    payload = payload or {}
    session = capture_store.start_session(str(payload.get("label", "")), str(payload.get("notes", "")))
    state.capture_active = True; state.capture_session_id = int(session["id"])
    capture_event(source="WEB", protocol="CONTROL", event_type="CAPTURE_START", summary=session.get("label") or f"Capture #{session['id']}", metadata={"notes": session.get("notes", "")})
    await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "session": session}


@app.post("/api/capture/stop")
async def capture_stop():
    capture_event(source="WEB", protocol="CONTROL", event_type="CAPTURE_STOP", summary=f"Capture #{state.capture_session_id} stop")
    session = capture_store.stop_session(); state.capture_active = False; state.capture_session_id = None
    await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "session": session}


@app.get("/api/capture/sessions")
async def capture_sessions(limit: int = 100):
    return {"items": capture_store.list_sessions(limit)}


@app.get("/api/capture/events")
async def capture_events(
    session_id: int | None = None, source: str = "", protocol: str = "", event_type: str = "",
    device_id: str = "", q: str = "", min_rssi: float | None = None,
    freq_min: float | None = None, freq_max: float | None = None,
    limit: int = Query(default=500, ge=1, le=5000), offset: int = Query(default=0, ge=0),
):
    return capture_store.list_events(
        session_id=session_id, source=source, protocol=protocol, event_type=event_type,
        device_id=device_id, q=q, min_rssi=min_rssi, freq_min=freq_min, freq_max=freq_max,
        limit=limit, offset=offset,
    )


@app.get("/api/capture/event/{event_id}")
async def capture_event_detail(event_id: int):
    row = capture_store.get_event(event_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "event nenalezen"}, status_code=404)
    return row


@app.get("/api/devices")
async def devices(protocol: str = "", q: str = "", limit: int = 500):
    return {"items": capture_store.list_devices(protocol=protocol, q=q, limit=limit)}


@app.get("/api/peaks")
async def peaks(q: str = "", limit: int = 500):
    return {"items": capture_store.list_peaks(q=q, limit=limit), "count": capture_store.peak_count()}


@app.post("/api/peaks")
async def peaks_add(payload: dict[str, Any] = Body(default={})):
    try:
        freq = int(payload.get("freq_mhz"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "freq_mhz musí být číslo"}, status_code=400)
    if not (2400 <= freq <= 2500):
        return JSONResponse({"ok": False, "error": "freq musí být 2400..2500 MHz"}, status_code=400)
    rssi = payload.get("rssi_dbm")
    peak = note_peak(
        freq_mhz=freq,
        rssi_dbm=float(rssi) if rssi is not None and rssi != "" else None,
        guess=str(payload.get("guess") or "Ručně zapsaná špička"),
        protocol_hint=str(payload.get("protocol_hint") or "UNKNOWN_RF"),
        explanation="Přidaná z spektra. Stále jde o energii, ne o dekódované zařízení.",
        name=str(payload.get("name") or ""),
    )
    return {"ok": True, "peak": peak}


@app.post("/api/peaks/clear")
async def peaks_clear():
    removed = capture_store.clear_peaks()
    state.peak_count = 0
    hub.send({"type": "peak_update", "cleared": True, "count": 0})
    return {"ok": True, "removed": removed}


@app.post("/api/peaks/{freq_mhz}")
async def peaks_update(freq_mhz: int, payload: dict[str, Any] = Body(default={})):
    if not (2400 <= freq_mhz <= 2500):
        return JSONResponse({"ok": False, "error": "freq musí být 2400..2500 MHz"}, status_code=400)
    row = capture_store.update_peak(
        freq_mhz,
        name=None if "name" not in payload else str(payload.get("name") or ""),
        notes=None if "notes" not in payload else str(payload.get("notes") or ""),
    )
    if row is None:
        return JSONResponse({"ok": False, "error": "špička není v seznamu"}, status_code=404)
    hub.send({"type": "peak_update", "peak": row, "count": capture_store.peak_count()})
    return {"ok": True, "peak": row}


@app.post("/api/peaks/{freq_mhz}/delete")
async def peaks_delete(freq_mhz: int):
    if not capture_store.delete_peak(freq_mhz):
        return JSONResponse({"ok": False, "error": "špička není v seznamu"}, status_code=404)
    state.peak_count = capture_store.peak_count()
    hub.send({"type": "peak_update", "deleted": freq_mhz, "count": state.peak_count})
    return {"ok": True, "count": state.peak_count}


@app.get("/api/capture/export/{session_id}.jsonl")
async def capture_export_jsonl(session_id: int):
    target = Path(CONFIG.get("capture", {}).get("export_dir", "/var/lib/waterfall/capture/exports")) / f"capture_{session_id}.jsonl"
    capture_store.export_jsonl(session_id, target)
    return FileResponse(target, filename=target.name)


@app.get("/api/capture/database")
async def capture_database():
    target = Path(CONFIG.get("capture", {}).get("export_dir", "/var/lib/waterfall/capture/exports")) / "waterfall_snapshot.sqlite3"
    capture_store.snapshot_db(target)
    return FileResponse(target, filename=target.name)


@app.post("/api/marker/{label}")
async def marker(label: str):
    emit_marker("USER_MARKER", label, "WEB")
    return {"ok": True}


@app.post("/api/relay/{action}")
async def relay_action(action: str):
    if not relay or not relay.status.available:
        return JSONResponse({"ok": False, "error": relay.status.error if relay else "relay unavailable"}, status_code=503)
    if action == "on":
        relay.set_power(True); state.relay_power_on = True; emit_marker("POWER_ON", "VUSION POWER ON", "GPIO", gpio=relay.gpio_bcm)
    elif action == "off":
        relay.set_power(False); state.relay_power_on = False; emit_marker("POWER_OFF", "VUSION POWER OFF", "GPIO", gpio=relay.gpio_bcm)
    else:
        return JSONResponse({"ok": False, "error": "unknown relay action"}, status_code=404)
    await hub.broadcast({"type": "state", "state": asdict(state)})
    return {"ok": True, "power_on": state.relay_power_on}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.connect(ws)
    await ws.send_json({"type": "state", "state": asdict(state)})
    with history_lock:
        if history:
            await ws.send_json({"type": "history", "sweeps": list(history)})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)
