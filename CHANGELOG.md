# WaterFall CHANGELOG

Formát: `X.Y.Z — YYYY-MM-DD`. Aktuální verze je v [`VERSION`](VERSION).
Git tag odpovídá verzi (`v0.4.4`).

## 0.4.4 — 2026-08-17

- pád USB sondy se zapíše do živého logu i journalu jako `PROBE DROP …`;
- odznak Sonda ukáže poslední `probe_error` po najetí myší;
- `watch_probe_state.sh` a [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) pro blikající ONLINE/OFFLINE.

## 0.4.3 — 2026-08-17

- záložka **Špičky**: automatický seznam nadprahových signálů z RF oblastí a WATCH burstů;
- ruční zápis klikem do spektra;
- pojmenování, poznámka, smazání;
- tlačítko **Držet (WATCH)** zaměří sondu na tu frekvenci;
- pokud BLE/Wi-Fi zařízení hlásí podobnou MHz, ukáže se jako „možná souvisí“;
- bez vysílání — komunikace se špičkou pořád není.

## 0.4.2 — 2026-08-17

- UI zjednodušené na Wireshark-styl: pořád viditelný pruh Capture / SCAN / WATCH / filtr;
- klik do spektra nebo na 1 MHz buňku vybere frekvenci (filtr v capture nebo WATCH);
- Capture má čipy Burst / Energie / BLE / Wi-Fi / NFC / Odhady;
- detail události nejdřív řekne lidsky, co to je a zda známe zařízení;
- záložka Zařízení má kartu a skok do capture;
- záložka Nápověda popisuje všechna tlačítka, typy událostí a ukládání;
- ovládání sondy, observerů a relé je v Nastavení, ne v hlavní liště.

## 0.4.1 — 2026-08-12

- přidán **PCAP / PCAPNG Inspector** přes `tshark`;
- upload, seznam, download a detail capture souborů;
- Wireshark display filter přímo ve webu;
- frame detail přes `tshark -V -x` včetně hex bytes;
- BLE observer je nově ručně ovladatelný a defaultně `auto_start=false`;
- explicitní active/passive BLE scan režim a řízený active fallback;
- ruční START/STOP pro experimentální 802.11 monitor-mode observer;
- instalátor doplněn o `tshark` a `/var/lib/waterfall/pcap`;
- zachováno striktní rozlišení: nRF RSSI energy inference ≠ packet decode.

## 0.4.0 — 2026-08-12

- nový **Capture Center** se SQLite session/event databází;
- packet/event detail panel;
- display filtry podle session/source/protocol/type/device/text/RSSI/frekvence;
- JSONL export capture session;
- bezpečný SQLite backup snapshot;
- persistentní katalog pozorovaných zařízení/identifikátorů;
- nový RF region analyzer s konzervativní `Wi-Fi-like`, `BLE-adv-like` a unknown inference;
- integrace `RF_BURST` z OpenVusion RF Probe 0.7.0;
- WATCH graf a živý burst log;
- BLE observer přes BlueZ/Bleak;
- experimentální skutečný 802.11 monitor-mode observer přes Scapy;
- raw 802.11 frame hex v Capture Center;
- runtime analysis settings;
- nové UI záložky Spectrum / Capture Center / Devices / Control & Settings;
- instalátor doplněn o BlueZ, `iw`, capture directories a CAP_NET_RAW pro volitelný monitor-mode observer;
- dokumentace capability boundaries: energy inference ≠ decoded packet/device identity;
- mock acceptance test ověřil capture, RF regions, WATCH burst, JSONL a SQLite snapshot.

## 0.3.0 — 2026-08-11

- první verzovaná WaterFall release;
- live 2400–2500 MHz spectrum;
- waterfall, heatgrid, peak/noise trend;
- baseline a live RF controls;
- TWN4, GPIO relay, CSV a experiment JSONL;
- stabilní RF Probe auto-detekce přes VID/PID/SN.
