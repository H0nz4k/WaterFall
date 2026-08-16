from __future__ import annotations

import threading
import time
from typing import Callable


def freq_to_channel(freq: int | None) -> int | None:
    if freq is None:
        return None
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if freq == 2484:
        return 14
    return None


class WifiMonitorWatcher(threading.Thread):
    """Optional passive 802.11 monitor-mode capture via Scapy.

    This path is deliberately disabled by default. It requires an interface
    already placed into monitor mode and OS permissions for raw capture.
    Unlike the nRF energy survey, these are real 802.11 frames and therefore
    can expose MAC addresses, SSIDs and raw frame bytes.
    """

    def __init__(self, interface: str, emit: Callable, device_cb: Callable, state_cb: Callable | None = None):
        super().__init__(daemon=True)
        self.interface = interface
        self.emit = emit
        self.device_cb = device_cb
        self.state_cb = state_cb
        self.stop_event = threading.Event()
        self.connected = False
        self.error = ""

    def _state(self):
        if self.state_cb:
            self.state_cb(self.connected, self.error)

    def _packet(self, pkt):
        try:
            from scapy.layers.dot11 import Dot11, Dot11Beacon, Dot11ProbeResp, Dot11Elt, RadioTap
            if not pkt.haslayer(Dot11):
                return
            d = pkt[Dot11]
            src = d.addr2 or d.addr3 or ""
            dst = d.addr1 or ""
            bssid = d.addr3 or ""
            rssi = None
            freq = None
            if pkt.haslayer(RadioTap):
                rt = pkt[RadioTap]
                rssi = getattr(rt, "dBm_AntSignal", None)
                freq = getattr(rt, "ChannelFrequency", None)
                try:
                    freq = int(freq) if freq is not None else None
                except Exception:
                    freq = None
            ssid = ""
            if pkt.haslayer(Dot11Beacon) or pkt.haslayer(Dot11ProbeResp):
                elt = pkt.getlayer(Dot11Elt)
                while elt is not None:
                    if getattr(elt, "ID", None) == 0:
                        raw = bytes(getattr(elt, "info", b""))
                        ssid = raw.decode("utf-8", errors="replace")
                        break
                    elt = elt.payload.getlayer(Dot11Elt) if getattr(elt, "payload", None) else None
            subtype = f"type={getattr(d,'type',None)} subtype={getattr(d,'subtype',None)}"
            channel = freq_to_channel(freq)
            raw_hex = bytes(pkt).hex().upper()
            device_id = f"WIFI:{src}" if src else None
            meta = {
                "src": src,
                "dst": dst,
                "bssid": bssid,
                "ssid": ssid,
                "frame_type": getattr(d, "type", None),
                "frame_subtype": getattr(d, "subtype", None),
                "raw_frame_note": "Raw 802.11/Radiotap bytes captured from monitor-mode interface.",
            }
            self.emit(
                source="WIFI_MONITOR",
                protocol="WIFI_80211",
                event_type="WIFI_FRAME",
                direction="RX",
                freq_mhz=freq,
                channel=channel,
                rssi_dbm=rssi,
                device_id=device_id,
                peer_id=dst or None,
                summary=f"802.11 {subtype} {src or '?'} -> {dst or '?'} {ssid}".strip(),
                payload_hex=raw_hex,
                metadata=meta,
            )
            if device_id:
                self.device_cb(
                    device_id=device_id,
                    source="WIFI_MONITOR",
                    protocol="WIFI_80211",
                    address=src,
                    name=ssid,
                    kind="802.11 transmitter",
                    rssi_dbm=rssi,
                    freq_mhz=freq,
                    channel=channel,
                    metadata=meta,
                )
        except Exception as exc:
            self.error = f"Wi-Fi packet parser: {type(exc).__name__}: {exc}"
            self._state()

    def run(self):
        try:
            from scapy.all import sniff
            self.connected = True
            self.error = ""
            self._state()
            while not self.stop_event.is_set():
                sniff(
                    iface=self.interface,
                    prn=self._packet,
                    store=False,
                    timeout=1,
                    monitor=True,
                )
        except Exception as exc:
            self.connected = False
            self.error = f"{type(exc).__name__}: {exc}"
            self._state()
        finally:
            self.connected = False
            self._state()
