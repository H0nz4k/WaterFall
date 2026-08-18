# WaterFall — čistá instalace na Raspberry Pi

Účet služby je **`hw`**. Relé štítku je **BCM 17** (active-low).

## 1. OS

1. Raspberry Pi Imager → Raspberry Pi OS **Lite 64-bit** (Bookworm).
2. V Imageru: uživatel **`hw`**, heslo, SSH zapnuté, Wi-Fi pokud nemáš Ethernet.
3. První boot, SSH: `ssh hw@hw.local`

## 2. Instalace

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/H0nz4k/WaterFall.git
cd WaterFall
sudo ./install.sh
sudo reboot
```

Skript nainstaluje Python, gpiozero/lgpio, BlueZ, tshark, udev, vypne ModemManager,
založí `/opt/waterfall`, data v `/var/lib/waterfall` a službu `waterfall` pod `hw`.

Update z už nainstalovaného stromu (nebo z PC přes `push_to_pi.sh`):

```bash
sudo ./install.sh --update
```

`config.json` se při update **nepřepisuje**.

## 3. Relé — zapojení

Typický modul relé (IN, GND, VCC), **active-low** (LOW = sepnuto):

| Relé | Raspberry Pi | BCM / pin |
|---|---|---|
| IN | GPIO17 | **BCM 17 · fyzický pin 11** |
| GND | GND | pin 6 (nebo 9, 14, 20, 25, 30, 34, 39) |
| VCC | 5V | pin 2 nebo 4 |

Po startu služby je default **POWER ON** (štítek napájený). V webu nahoře **POWER ON / OFF**.

V `/opt/waterfall/config.json`:

```json
"relay": {
  "enabled": true,
  "gpio_bcm": 17,
  "active_low": true,
  "default_power_on": true
}
```

Když relé spíná obráceně, přepni `active_low` na `false` a `sudo systemctl restart waterfall`.

## 4. USB

- **nRF52840 OpenVusion RF Probe** — VID/PID `2fe3:0001`, port `auto`.
- **Elatec TWN4** — pokud visí v USB při první instalaci, skript ho zapíše do configu. Jinak NFC nechá vypnuté; doplň `nfc_reader.serial_port` na `/dev/serial/by-id/usb-…TWN4…` a `"enabled": true`.

```bash
ls -l /dev/serial/by-id/
sudo systemctl restart waterfall
```

## 5. Kontrola

```bash
sudo systemctl status waterfall --no-pager
journalctl -u waterfall -n 80 --no-pager
```

Web: `http://<IP>:8088/` — verze nahoře, odznak **Štítek ON/OFF**, sonda zelená po zapojení donglu.

## 6. Co skript záměrně nedělá

- Neflashuje nRF52840 (to je DFU z PC, viz Probe `BUILD_FLASH.md`).
- Nezapíná Wi-Fi monitor mode (druhá karta, CAP_NET_RAW).
- Neinstaluje ElaTool, pokud není v `~/OpenVusion/tools/ElaTool` — NFC UID čtečka ho potřebuje.
