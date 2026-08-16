from __future__ import annotations

from typing import Any


def wifi_channel_for_freq(freq_mhz: float) -> int | None:
    if 2412 <= freq_mhz <= 2472:
        ch = round((freq_mhz - 2407) / 5)
        if 1 <= ch <= 13 and abs((2407 + ch * 5) - freq_mhz) <= 3:
            return ch
    if abs(freq_mhz - 2484) <= 3:
        return 14
    return None


def classify_region(start: int, end: int, peak_freq: int, peak_rssi: float) -> dict[str, Any]:
    width = end - start + 1
    center = (start + end) / 2
    wifi_ch = wifi_channel_for_freq(center)

    if width >= 8 and wifi_ch is not None:
        return {
            "label": f"Wi-Fi channel {wifi_ch}-like candidate",
            "protocol_hint": "WIFI_LIKE",
            "confidence": "medium",
            "explanation": "Široká energetická oblast leží poblíž středu Wi-Fi kanálu. Jde pouze o RF inference, nikoli dekódovaný 802.11 rámec.",
        }

    ble_adv = min((2402, 2426, 2480), key=lambda f: abs(f - peak_freq))
    if width <= 4 and abs(ble_adv - peak_freq) <= 2:
        return {
            "label": f"BLE advertising-channel-like candidate ({ble_adv} MHz)",
            "protocol_hint": "BLE_ADV_LIKE",
            "confidence": "low",
            "explanation": "Úzký energetický zásah je blízko BLE advertising frekvence. Bez dekódovaného BLE paketu nelze určit zařízení ani protokol.",
        }

    return {
        "label": "Unknown / proprietary RF energy",
        "protocol_hint": "UNKNOWN_RF",
        "confidence": "low",
        "explanation": "Energetická událost nemá dost informací pro spolehlivé přiřazení protokolu nebo zařízení.",
    }


def detect_regions(points: list[dict[str, Any]], threshold_db: float = 12.0) -> list[dict[str, Any]]:
    if not points:
        return []
    vals = sorted(float(p["rssi_dbm"]) for p in points)
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    threshold = median + threshold_db

    active = [p for p in points if float(p["rssi_dbm"]) >= threshold]
    if not active:
        return []

    regions = []
    current = [active[0]]
    for p in active[1:]:
        if int(p["freq_mhz"]) <= int(current[-1]["freq_mhz"]) + 2:
            current.append(p)
        else:
            regions.append(current)
            current = [p]
    regions.append(current)

    out = []
    for region in regions:
        peak = max(region, key=lambda x: float(x["rssi_dbm"]))
        start = int(region[0]["freq_mhz"])
        end = int(region[-1]["freq_mhz"])
        cls = classify_region(start, end, int(peak["freq_mhz"]), float(peak["rssi_dbm"]))
        out.append({
            "start_mhz": start,
            "end_mhz": end,
            "width_mhz": end - start + 1,
            "peak_freq_mhz": int(peak["freq_mhz"]),
            "peak_rssi_dbm": float(peak["rssi_dbm"]),
            "median_dbm": median,
            "threshold_dbm": threshold,
            **cls,
        })
    return out
