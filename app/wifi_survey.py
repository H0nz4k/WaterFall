from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

log = logging.getLogger("waterfall.wifi_survey")

WIFI_2G_MIN = 2400
WIFI_2G_MAX = 2500


def channel_center_mhz(channel: int) -> int | None:
    if 1 <= channel <= 13:
        return 2407 + channel * 5
    if channel == 14:
        return 2484
    return None


def freq_to_channel(freq: int | None) -> int | None:
    if freq is None:
        return None
    if 2412 <= freq <= 2472:
        ch = int(round((freq - 2407) / 5))
        if 1 <= ch <= 13:
            return ch
    if freq == 2484:
        return 14
    return None


def split_nmcli(line: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    esc = False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _run(argv: list[str], timeout: float = 12.0) -> str:
    r = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(err or f"{argv[0]} exit {r.returncode}")
    return r.stdout or ""


def _parse_freq(raw: str) -> int | None:
    if not raw:
        return None
    m = re.search(r"(\d{4,5})", raw.replace(",", "."))
    return int(m.group(1)) if m else None


def parse_nmcli_list(text: str) -> list[dict[str, Any]]:
    aps: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = split_nmcli(line)
        if len(parts) < 5:
            continue
        in_use, ssid, bssid, chan_s, freq_s = parts[:5]
        signal_s = parts[5] if len(parts) > 5 else ""
        try:
            channel = int(chan_s) if chan_s else 0
        except ValueError:
            channel = 0
        freq = _parse_freq(freq_s)
        if freq is None and channel:
            freq = channel_center_mhz(channel)
        if channel <= 0 and freq:
            channel = freq_to_channel(freq) or 0
        try:
            rssi = int(signal_s)
            # nmcli SIGNAL is 0-100 quality; keep as quality, also store as hint
            quality = rssi
        except ValueError:
            quality = None
        aps.append(_normalize_ap(
            ssid=ssid,
            bssid=bssid,
            channel=channel,
            freq_mhz=freq,
            quality=quality,
            in_use=in_use.strip() in ("*", "yes", "ano", "true"),
            source="nmcli",
        ))
    return [a for a in aps if a]


def parse_iw_dump(text: str) -> list[dict[str, Any]]:
    aps: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}

    def flush():
        if cur.get("bssid"):
            aps.append(_normalize_ap(
                ssid=cur.get("ssid") or "",
                bssid=cur.get("bssid") or "",
                channel=int(cur.get("channel") or 0),
                freq_mhz=cur.get("freq"),
                quality=None,
                rssi_dbm=cur.get("rssi"),
                in_use=False,
                source="iw",
            ))

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("BSS ") and ":" in s:
            flush()
            cur = {"bssid": s.split()[1].split("(")[0]}
            continue
        if s.startswith("freq:"):
            cur["freq"] = _parse_freq(s)
        elif s.startswith("signal:"):
            m = re.search(r"(-?\d+(?:\.\d+)?)", s)
            if m:
                cur["rssi"] = int(float(m.group(1)))
        elif s.startswith("SSID:"):
            cur["ssid"] = s[5:].strip()
        elif "DS Parameter set: channel" in s or s.startswith("DS Parameter set:"):
            m = re.search(r"channel\s+(\d+)", s)
            if m:
                cur["channel"] = int(m.group(1))
    flush()
    return [a for a in aps if a]


def _normalize_ap(
    *,
    ssid: str,
    bssid: str,
    channel: int,
    freq_mhz: int | None,
    quality: int | None = None,
    rssi_dbm: int | None = None,
    in_use: bool,
    source: str,
) -> dict[str, Any] | None:
    bssid = (bssid or "").upper().replace("-", ":")
    if not bssid or bssid in ("00:00:00:00:00:00", "(UNKNOWN)"):
        if not ssid:
            return None
    if freq_mhz is None and channel:
        freq_mhz = channel_center_mhz(channel)
    if not freq_mhz:
        return None
    in_2g = WIFI_2G_MIN <= freq_mhz <= WIFI_2G_MAX
    width = 20 if in_2g else 20
    start = max(WIFI_2G_MIN, int(freq_mhz) - 10) if in_2g else None
    end = min(WIFI_2G_MAX, int(freq_mhz) + 10) if in_2g else None
    label = ssid.strip() or f"skrytá {bssid[-8:]}"
    return {
        "ssid": ssid.strip(),
        "label": label,
        "bssid": bssid,
        "channel": channel or freq_to_channel(int(freq_mhz)) or 0,
        "freq_mhz": int(freq_mhz),
        "start_mhz": start,
        "end_mhz": end,
        "width_mhz": width,
        "quality": quality,
        "rssi_dbm": rssi_dbm,
        "in_use": bool(in_use),
        "in_2g": in_2g,
        "source": source,
    }


class WifiSurveyWatcher(threading.Thread):
    """Managed-mode AP survey (SSID + channel). Does not put wlan0 into monitor mode."""

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        interval_s: float = 25.0,
        interface: str = "auto",
        mock: bool = False,
    ):
        super().__init__(daemon=True)
        self.emit = emit
        self.interval_s = max(8.0, float(interval_s))
        self.interface = interface
        self.mock = mock
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.aps: list[dict[str, Any]] = []
        self.error = ""
        self.ok = False
        self.iface_used = ""
        self.last_iso = ""
        self.backend = ""

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": self.ok,
                "error": self.error,
                "iface": self.iface_used,
                "backend": self.backend,
                "aps": list(self.aps),
            }

    def _publish(self):
        self.emit({"type": "wifi_aps", **self.snapshot()})

    def _detect_iface(self) -> str:
        if self.interface and self.interface != "auto":
            return self.interface
        try:
            text = _run(["iw", "dev"], timeout=4)
        except Exception:
            return "wlan0"
        current = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("Interface "):
                current = s.split()[1]
            if s.startswith("type ") and current:
                kind = s.split()[1]
                if kind in ("managed", "station"):
                    return current
        return current or "wlan0"

    def _scan_nmcli(self, rescan: bool) -> list[dict[str, Any]]:
        if not shutil.which("nmcli"):
            raise RuntimeError("nmcli není v PATH")
        if rescan:
            try:
                _run(["nmcli", "device", "wifi", "rescan"], timeout=15)
                time.sleep(1.2)
            except Exception as exc:
                log.debug("wifi rescan: %s", exc)
        out = _run(
            ["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,CHAN,FREQ,SIGNAL", "device", "wifi", "list"],
            timeout=12,
        )
        self.backend = "nmcli"
        return parse_nmcli_list(out)

    def _scan_iw(self) -> list[dict[str, Any]]:
        iface = self._detect_iface()
        self.iface_used = iface
        if not shutil.which("iw"):
            raise RuntimeError("ani nmcli, ani iw")
        # dump uses last scan; does not require monitor mode
        try:
            out = _run(["iw", "dev", iface, "scan", "dump"], timeout=8)
        except Exception:
            out = _run(["iw", "dev", iface, "scan"], timeout=20)
        self.backend = "iw"
        return parse_iw_dump(out)

    def _scan_once(self, rescan: bool) -> list[dict[str, Any]]:
        if self.mock:
            self.backend = "mock"
            self.iface_used = "mock"
            return [_normalize_ap(
                ssid="Mock-AP",
                bssid="02:00:00:00:00:01",
                channel=6,
                freq_mhz=2437,
                quality=80,
                in_use=True,
                source="mock",
            )]
        try:
            aps = self._scan_nmcli(rescan)
            self.iface_used = self.iface_used or "nmcli"
            return aps
        except Exception as nm_exc:
            try:
                aps = self._scan_iw()
                self.error = ""
                return aps
            except Exception as iw_exc:
                raise RuntimeError(f"nmcli: {nm_exc}; iw: {iw_exc}") from iw_exc

    def run(self):
        first = True
        while not self.stop_event.is_set():
            try:
                aps = [a for a in self._scan_once(rescan=not first) if a]
                # unique by BSSID, keep strongest / in-use
                by: dict[str, dict[str, Any]] = {}
                for ap in aps:
                    key = ap["bssid"] or ap["label"]
                    old = by.get(key)
                    if old is None or ap["in_use"] or (ap.get("quality") or -1) > (old.get("quality") or -1):
                        by[key] = ap
                ranked = sorted(
                    by.values(),
                    key=lambda a: (not a["in_use"], -(a.get("quality") or a.get("rssi_dbm") or -120)),
                )
                with self.lock:
                    self.aps = ranked
                    self.ok = True
                    self.error = ""
                    self.last_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._publish()
            except Exception as exc:
                with self.lock:
                    self.ok = False
                    self.error = str(exc)[:240]
                log.warning("wifi survey: %s", exc)
                self._publish()
            first = False
            self.stop_event.wait(self.interval_s)
