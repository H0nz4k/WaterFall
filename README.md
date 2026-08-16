# WaterFall

Webový RF capture / analyzer pro výzkum **OpenVusion** (SES-imagotag VUSION a okolní 2,4 GHz pásmo).

**Verze:** `0.4.7` · soubor [`VERSION`](VERSION) · historie v [`CHANGELOG.md`](CHANGELOG.md)  
**Sonda:** [OpenVusion RF Probe](https://github.com/H0nz4k/OpenVusion_RF_Probe) 0.7.2 na nRF52840 Dongle  
**Host:** Raspberry Pi / Linux · web na portu `8088`

WaterFall ukáže, **co se děje v pásmu 2400–2500 MHz**, uloží to a u každé události řekne, jestli známe zařízení, nebo jen energii. Ovládání je schválně jednoduché — podobné Wiresharku: START capture, filtr, klik na řádek, detail.

> Silný signál na 2453 MHz **není** automaticky „VUSION paket“. RSSI z nRF52840 neurčí MAC, UID ani protokol. Identita vzniká jen z dekódovaného BLE, 802.11 nebo NFC.

## Co umí

- živé spektrum a waterfall 2400–2500 MHz (limit rádia nRF52840)
- **Špičky** — automatický seznam silných signálů, pojmenování, **Držet (WATCH)**
- Capture Center (SQLite) + JSONL / SQLite snapshot
- katalog zařízení z BLE / Wi-Fi / NFC
- PCAP / PCAPNG inspector přes `tshark`
- ovládání sondy: SCAN, ONCE, RANGE, DWELL, STEP, RSSIMODE, WATCH
- volitelně BLE observer, 802.11 monitor, TWN4, GPIO relé

Sonda **jen měří energii**. Neumí packet RX proprietárního VUSION protokolu a **nevysílá**.

Když nahoře **PROBE bliká** ONLINE/OFFLINE a spektrum přitom žije, jde o USB reconnect: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).  
Build a flash donglu: [BUILD_FLASH.md](https://github.com/H0nz4k/OpenVusion_RF_Probe/blob/main/BUILD_FLASH.md).

## Rychlý start

1. Sonda online → **SCAN**
2. Klik do spektra na špičku → **Zapsat** nebo počkej, až se zapíše sama
3. V **Špičkách** ji pojmenuj a klikni **Držet**
4. **START** capture, v Capture otevři detail
5. **STOP** a ulož JSONL

## Update z PC přes SSH

Z Git Bash ve složce WaterFall (tenhle repo):

```bash
chmod +x push_to_pi.sh
./push_to_pi.sh pi@192.168.1.50
```

Nahraje zdroj na Pi do `~/waterfall-src` a spustí `sudo ./install.sh --update`.  
`config.json` na Raspberry se **nepřepíše**. První instalace:

```bash
./push_to_pi.sh pi@192.168.1.50 --full
```

Trvalý cíl: `export WATERFALL_PI=pi@192.168.1.50`

## Instalace na Raspberry Pi

```bash
git clone https://github.com/H0nz4k/WaterFall.git
cd WaterFall
chmod +x install.sh
sudo ./install.sh
```

Instalátor dá aplikaci do `/opt/waterfall`, data do `/var/lib/waterfall` a založí `waterfall.service`.

```bash
sudo systemctl status waterfall --no-pager
journalctl -u waterfall -f
```

Web: `http://<IP_RPI>:8088/`  
Config: `/opt/waterfall/config.json` (vznikne z `config.example.json`, existující se nepřepisuje)

### Lokální / mock

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# v config.json nastav "mock_mode": true, pokud nemáš sondu
python run_server.py
```

## Verzování

- aktuální číslo je v [`VERSION`](VERSION) a v `app/main.py` (`VERSION`)
- každá viditelná změna patří do [`CHANGELOG.md`](CHANGELOG.md) nahoře, ve formátu `X.Y.Z — YYYY-MM-DD`
- tag v gitu: `v0.4.5`

| Část | Význam |
|---|---|
| major | nekompatibilní změna protokolu / dat |
| minor | nová funkce (Špičky, Capture, PCAP…) |
| patch | UI, opravy, dokumentace |

Firmware sondy se verzuje zvlášť. WaterFall 0.4.5 očekává Probe **0.7.2** (`WATERFALL_COMPAT=0.4.1`).

## Config — důležité

V `config.example.json` nech sériová čísla a UID prázdné. Do ostrého `config.json` doplň:

- `rf_probe.serial_number` jen pokud chceš konkrétní dongle
- `nfc_reader.serial_port` a `expected_uid` podle TWN4
- `wifi_monitor.interface` jen u samostatného monitor-mode adaptéru, ne u management Wi-Fi

## Limity

WaterFall ani nRF52840 Dongle nejsou SDR. Energy režim z neznámého CC2510 provozu nezíská payload, MAC štítku ani klíč. Nejdřív musí být známé PHY, teprve potom dává smysl packet RX a případně zpětná komunikace.

## Licence

Výzkumný projekt OpenVusion. Používej jen na vlastním hardwaru a v souladu s místními předpisy pro rádiový provoz.
