# WaterFall — když PROBE bliká ONLINE / OFFLINE

Firmware **0.7.2 nech tak, jak je**, pokud spektrum žije (sweep ~2 Hz, data age < 1 s, vidíš regiony).
Blikání znamená: sériové spojení se shodí a WaterFall se po **1,5 s** znovu připojí.

Od 0.4.6 má odznak zůstat **zelený**, dokud tečou sweep data (stáří do 3 s). Červená je až když data opravdu přestanou. Krátký USB glitch se zkusí znovu přečíst.

Od **0.4.8** host čeká na celý řádek až do `\n` (až 3 s). Starý `readline()` timeout 0,25 s vracel utržený `SWEEP`; zbytek `,2431:-105` se pak slepil s `PING` a firmware hlásil `ERR unknown command`. WaterFall teď ten zbytek zahodí a port kvůli tomu nezavírá (`PROBE GLITCH` v logu, ne `PROBE DROP`).

Reflash donglu **není nutný** — 0.4.8 to spraví na hostu. Novější firmware skládá `SWEEP` do bufferu a pošle ho najednou (méně utržených řádků). USB produkt zůstává `v0.7.2`.

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

Typický log při reconnect bouři:

```text
ERR unknown command: ,2431:-105,2432::PING
SerialException: device reports readiness to read but returned no data
  (device disconnected or multiple access on port?)
```

Linux CDC má po `open()` chvíli zapnuté ECHO. Zbytek `SWEEP` se odrazí zpět do firmware a slepí se s `PING`. Zavření portu shodí DTR, firmware zastaví SCAN, WaterFall se znovu připojí a pošle další `PING`. Od 0.4.7 se port kvůli tomuto glitchi nezavírá a ECHO se vypne hned. Od 0.4.8 se navíc skládá celý řádek a po znovuotevření se SCAN/WATCH vždy obnoví.

Na Raspberry taky často **ModemManager** sahá na `ttyACM`. `install.sh` od 0.4.7 dává udev pravidlo `ID_MM_DEVICE_IGNORE` pro VID/PID `2fe3:0001`.

Write timeout může souviset i s tím, že starší 0.7.2 posílala `SWEEP` byte po bytu během samplování. Aktuální zdroj skládá řádek a teprve pak TX (s yield každých 64 B). WaterFall 0.4.8 to na hostu dohání skládáním do `\n`.

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
