from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable


COMPANY_NAMES = {
    0x004C: "Apple",
    0x0006: "Microsoft",
    0x0059: "Nordic Semiconductor",
}


class BleWatcher(threading.Thread):
    """BLE advertisement observer using BlueZ/Bleak.

    The observer records only advertisements delivered by the local Bluetooth
    controller. BLE addresses can be randomized; an address is therefore an
    observed identifier, not proof of a persistent physical identity.
    """

    def __init__(
        self,
        emit: Callable,
        device_cb: Callable,
        state_cb: Callable | None = None,
        scan_mode: str = "active",
        allow_active_fallback: bool = False,
    ):
        super().__init__(daemon=True)
        self.emit = emit
        self.device_cb = device_cb
        self.state_cb = state_cb
        self.scan_mode = scan_mode if scan_mode in {"active", "passive"} else "active"
        self.allow_active_fallback = bool(allow_active_fallback)
        self.stop_event = threading.Event()
        self.connected = False
        self.error = ""

    def _state(self):
        if self.state_cb:
            self.state_cb(self.connected, self.error)

    @staticmethod
    def _hexmap(data: dict) -> dict[str, str]:
        return {f"0x{int(k):04X}": bytes(v).hex().upper() for k, v in (data or {}).items()}

    def _detection(self, device, adv):
        try:
            manufacturer_data = self._hexmap(getattr(adv, "manufacturer_data", {}) or {})
            service_data = {
                str(k): bytes(v).hex().upper()
                for k, v in (getattr(adv, "service_data", {}) or {}).items()
            }
            ids = list((getattr(adv, "manufacturer_data", {}) or {}).keys())
            vendor = ", ".join(COMPANY_NAMES.get(int(i), f"Company 0x{int(i):04X}") for i in ids)
            address = str(getattr(device, "address", "") or "")
            name = str(getattr(adv, "local_name", "") or getattr(device, "name", "") or "")
            rssi = getattr(adv, "rssi", None)
            tx_power = getattr(adv, "tx_power", None)
            service_uuids = list(getattr(adv, "service_uuids", []) or [])
            device_id = f"BLE:{address}" if address else "BLE:UNKNOWN"
            meta = {
                "address": address,
                "name": name,
                "manufacturer_data": manufacturer_data,
                "service_data": service_data,
                "service_uuids": service_uuids,
                "tx_power": tx_power,
                "platform_details": getattr(device, "details", None),
                "identity_warning": "BLE adresa může být randomizovaná; není to zaručená trvalá identita zařízení.",
            }
            summary = f"BLE ADV {address or '?'} {name or ''}".strip()
            self.emit(
                source="BLE_HCI",
                protocol="BLE",
                event_type="BLE_ADV",
                rssi_dbm=rssi,
                device_id=device_id,
                summary=summary,
                metadata=meta,
            )
            self.device_cb(
                device_id=device_id,
                source="BLE_HCI",
                protocol="BLE",
                address=address,
                name=name,
                vendor=vendor,
                kind="BLE advertiser",
                rssi_dbm=rssi,
                metadata=meta,
            )
        except Exception as exc:
            self.error = f"BLE callback: {type(exc).__name__}: {exc}"
            self._state()

    async def _run_async(self):
        from bleak import BleakScanner

        try:
            scanner = BleakScanner(
                detection_callback=self._detection,
                scanning_mode=self.scan_mode,
            )
            await scanner.start()
        except Exception:
            if self.scan_mode != "passive" or not self.allow_active_fallback:
                raise
            scanner = BleakScanner(
                detection_callback=self._detection,
                scanning_mode="active",
            )
            await scanner.start()
            self.error = "Passive BLE scan nebyl dostupný; použit explicitně povolený active fallback."
        self.connected = True
        self.error = ""
        self._state()
        try:
            while not self.stop_event.is_set():
                await asyncio.sleep(0.25)
        finally:
            await scanner.stop()
            self.connected = False
            self._state()

    def run(self):
        while not self.stop_event.is_set():
            try:
                asyncio.run(self._run_async())
                break
            except Exception as exc:
                self.connected = False
                self.error = f"{type(exc).__name__}: {exc}"
                self._state()
                time.sleep(3.0)
