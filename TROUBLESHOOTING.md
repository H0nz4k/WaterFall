# WaterFall — když PROBE bliká ONLINE / OFFLINE

Firmware **0.7.2 nech tak, jak je**, pokud spektrum žije (sweep ~2 Hz, data age < 1 s, vidíš regiony).
Blikání znamená: sériové spojení se shodí a WaterFall se po **1,5 s** znovu připojí.

`PROBE OFFLINE` se nastaví jen při výjimce v RF Probe workeru.

| Co vidíš | Význam |
|---|---|
| ONLINE + Data age ~0,4 s | spojení drží, sweep teče |
| OFFLINE + Data age několik sekund | krátký výpadek / reconnect |
| spektrum se po chvíli zase plní | transport, ne RF základ |

Typické chyby (tři různé scénáře):

```text
SerialException: device reports readiness to read but returned no data
SerialTimeoutException: Write timeout
OSError: [Errno 5] Input/output error
```

Write timeout / USB disconnect může souviset s tím, že 0.7.2 posílá celý `SWEEP` přes `uart_poll_out()` byte po bytu (dlouhá řádka, ~2× za sekundu). To se nemění, dokud není potvrzený `probe_error`.

---

## 1. Živý log ve webu

Záložka **Nastavení → Živý log**. Od 0.4.4 se tam při pádu vypíše `PROBE DROP …`.

Stejný text jde i do journalu:

```bash
journalctl -u waterfall -f
```

Hledej `PROBE DROP`.

---

## 2. Sledovat /api/state (15–30 s)

Na Raspberry, dokud několikrát nepřeblikne OFFLINE, pak Ctrl+C:

```bash
chmod +x watch_probe_state.sh
./watch_probe_state.sh
```

Nebo ručně:

```bash
while true; do
    curl -s http://127.0.0.1:8088/api/state | \
    python3 -c '
import json,sys,datetime
s=json.load(sys.stdin)
print(
    datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3],
    "ONLINE=" + str(s.get("probe_connected")),
    "SCAN=" + str(s.get("scan_enabled")),
    "SWEEP=" + str(s.get("sweep_count")),
    "ERR=" + repr(s.get("probe_error")),
    "LAST=" + repr((s.get("last_line") or "")[:100])
)
'
    sleep 0.25
done
```

Pošli kus výstupu kolem `ONLINE=False` — podle `ERR=` se pozná, který ze tří scénářů to je.

---

## 3. Build a flash donglu

Opakovatelný postup je ve firmware: [BUILD_FLASH.md](https://github.com/H0nz4k/OpenVusion_RF_Probe/blob/main/BUILD_FLASH.md).
