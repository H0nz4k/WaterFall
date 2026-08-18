#!/usr/bin/env bash
# WaterFall — instalace na Raspberry Pi (čisté Raspberry Pi OS).
#
#   sudo ./install.sh           # první instalace, uživatel hw
#   sudo ./install.sh --update  # jen soubory + restart služby
#
# Uživatel služby: hw  (přepiš: WATERFALL_USER=pi sudo ./install.sh)
# Relé: BCM 17 (fyzický pin 11), active-low, default POWER ON.
set -euo pipefail

APP_DIR=/opt/waterfall
DATA_ROOT=/var/lib/waterfall
SERVICE_USER="${WATERFALL_USER:-hw}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="full"
if [[ "${1:-}" == "--update" ]]; then
  MODE="update"
fi

VERSION="0.4.17"
if [[ -f "$SOURCE_DIR/VERSION" ]]; then
  VERSION="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"
fi

if [[ $EUID -ne 0 ]]; then
  echo "Spusť přes sudo: sudo ./install.sh [--update]"
  exit 1
fi

echo "== WaterFall v${VERSION} (${MODE}) =="
echo "User: $SERVICE_USER"
echo "Zdroj: $SOURCE_DIR"

apt_ok() {
  apt-cache show "$1" >/dev/null 2>&1
}

install_pkgs() {
  local pkgs=()
  local p
  for p in "$@"; do
    if apt_ok "$p"; then
      pkgs+=("$p")
    else
      echo "balík $p v této OS není, přeskakuji"
    fi
  done
  if [[ ${#pkgs[@]} -gt 0 ]]; then
    apt install -y "${pkgs[@]}"
  fi
}

ensure_user() {
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "Zakládám uživatele $SERVICE_USER"
    adduser --disabled-password --gecos "WaterFall" "$SERVICE_USER"
    echo "Uživatel $SERVICE_USER nemá heslo. Nastav: sudo passwd $SERVICE_USER"
  fi
  if getent group sudo >/dev/null; then
    usermod -aG sudo "$SERVICE_USER" || true
  fi
  local grp
  for grp in dialout gpio bluetooth plugdev spi i2c video input wireshark netdev; do
    if getent group "$grp" >/dev/null; then
      usermod -aG "$grp" "$SERVICE_USER"
    fi
  done
}

install_os() {
  export DEBIAN_FRONTEND=noninteractive
  apt update
  echo "wireshark-common wireshark-common/install-setuid boolean true" | debconf-set-selections || true

  install_pkgs \
    ca-certificates curl git usbutils udev \
    python3 python3-venv python3-pip python3-full python3-dev \
    python3-gpiozero python3-lgpio python3-rpi-lgpio python3-libgpiod \
    python3-serial python3-spidev \
    bluez rfkill libglib2.0-0 \
    iw tshark libcap2-bin \
    build-essential pkg-config

  if systemctl list-unit-files ModemManager.service >/dev/null 2>&1; then
    systemctl disable --now ModemManager.service 2>/dev/null || true
    systemctl mask ModemManager.service 2>/dev/null || true
    echo "ModemManager vypnutý (nesmí sahat na CDC sondy / TWN4)."
  fi

  if systemctl list-unit-files bluetooth.service >/dev/null 2>&1; then
    systemctl enable --now bluetooth.service 2>/dev/null || true
  fi
  rfkill unblock bluetooth 2>/dev/null || true
  rfkill unblock gpio 2>/dev/null || true
}

install_udev() {
  local f
  for f in \
    "$SOURCE_DIR/udev/99-openvusion-rf-probe.rules" \
    "$SOURCE_DIR/udev/99-waterfall-lab.rules"
  do
    if [[ -f "$f" ]]; then
      cp "$f" "/etc/udev/rules.d/$(basename "$f")"
    fi
  done
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger 2>/dev/null || true
  echo "udev: RF Probe 2fe3:0001, Elatec TWN4 09d8, gpiochip → skupina gpio."
}

copy_tree() {
  mkdir -p "$APP_DIR" \
    "$DATA_ROOT/captures" \
    "$DATA_ROOT/experiments" \
    "$DATA_ROOT/capture/exports" \
    "$DATA_ROOT/pcap"

  copy_if_present() {
    local src="$1"
    local dest="$2"
    if [[ -e "$src" ]]; then
      cp -a "$src" "$dest"
    fi
  }

  rm -rf "$APP_DIR/app"
  copy_if_present "$SOURCE_DIR/app" "$APP_DIR/app"
  copy_if_present "$SOURCE_DIR/requirements.txt" "$APP_DIR/requirements.txt"
  copy_if_present "$SOURCE_DIR/config.example.json" "$APP_DIR/config.example.json"
  copy_if_present "$SOURCE_DIR/run_server.py" "$APP_DIR/run_server.py"
  copy_if_present "$SOURCE_DIR/VERSION" "$APP_DIR/VERSION"
  copy_if_present "$SOURCE_DIR/watch_probe_state.sh" "$APP_DIR/watch_probe_state.sh"
  copy_if_present "$SOURCE_DIR/TROUBLESHOOTING.md" "$APP_DIR/TROUBLESHOOTING.md"
  copy_if_present "$SOURCE_DIR/GUIDE.md" "$APP_DIR/GUIDE.md"
  copy_if_present "$SOURCE_DIR/INSTALL_RPI.md" "$APP_DIR/INSTALL_RPI.md"
  copy_if_present "$SOURCE_DIR/udev" "$APP_DIR/udev"
  chmod +x "$APP_DIR/watch_probe_state.sh" 2>/dev/null || true
}

seed_config() {
  if [[ -f "$APP_DIR/config.json" ]]; then
    echo "Existující $APP_DIR/config.json zachován."
    return
  fi
  cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
  python3 - <<'PY'
import glob, json
from pathlib import Path
p = Path("/opt/waterfall/config.json")
c = json.loads(p.read_text(encoding="utf-8"))
c.setdefault("relay", {})
c["relay"]["enabled"] = True
c["relay"]["gpio_bcm"] = 17
c["relay"]["active_low"] = True
c["relay"]["default_power_on"] = True
ids = (
    glob.glob("/dev/serial/by-id/*TWN4*")
    + glob.glob("/dev/serial/by-id/*twn4*")
    + glob.glob("/dev/serial/by-id/*Elatec*")
    + glob.glob("/dev/serial/by-id/*ELATEC*")
)
c.setdefault("nfc_reader", {})
if ids:
    c["nfc_reader"]["enabled"] = True
    c["nfc_reader"]["serial_port"] = ids[0]
    print("TWN4:", ids[0])
else:
    c["nfc_reader"]["enabled"] = False
    c["nfc_reader"]["serial_port"] = ""
    print("TWN4 nepřipojen — nfc_reader.enabled=false (zapni v configu až bude USB).")
c.setdefault("rf_probe", {})
c["rf_probe"]["enabled"] = True
c["rf_probe"]["serial_port"] = "auto"
p.write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  echo "Vytvořen nový $APP_DIR/config.json (relé BCM 17 active-low)."
}

install_python() {
  if [[ "$MODE" == "full" || ! -x "$APP_DIR/.venv/bin/python" ]]; then
    rm -rf "$APP_DIR/.venv"
    python3 -m venv --system-site-packages "$APP_DIR/.venv"
    "$APP_DIR/.venv/bin/pip" install --upgrade pip
  fi
  "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

  local ela
  for ela in \
    "/home/$SERVICE_USER/OpenVusion/tools/ElaTool" \
    "$SOURCE_DIR/../../ElaTool" \
    "$SOURCE_DIR/../ElaTool"
  do
    if [[ -f "$ela/pyproject.toml" || -f "$ela/setup.py" ]]; then
      echo "Nalezen ElaTool: $ela"
      "$APP_DIR/.venv/bin/pip" install -e "$ela" || true
      break
    fi
  done
}

pin_factory() {
  if "$APP_DIR/.venv/bin/python" -c "import lgpio" >/dev/null 2>&1; then
    echo lgpio
  else
    echo ""
  fi
}

write_service() {
  local factory
  factory="$(pin_factory)"
  local factory_line=""
  if [[ -n "$factory" ]]; then
    factory_line="Environment=GPIOZERO_PIN_FACTORY=${factory}"
  fi
  cat > /etc/systemd/system/waterfall.service <<EOF
[Unit]
Description=WaterFall v${VERSION} RF Capture Center
After=network.target bluetooth.target
Wants=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
SupplementaryGroups=dialout gpio bluetooth plugdev spi i2c netdev
WorkingDirectory=${APP_DIR}
Environment=WATERFALL_CONFIG=${APP_DIR}/config.json
${factory_line}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/run_server.py
Restart=on-failure
RestartSec=2
AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable waterfall.service
  systemctl restart waterfall.service
}

ensure_user
if [[ "$MODE" == "full" ]]; then
  install_os
fi
copy_tree
install_udev
seed_config
install_python
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR" "$DATA_ROOT"
write_service

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Instalace / update hotový (v${VERSION})."
echo "User:   $SERVICE_USER"
echo "Config: $APP_DIR/config.json"
echo "Relé:   BCM 17  = fyzický pin 11 (IN) · GND pin 6 · VCC 5V pin 2"
echo "        active-low: POWER ON = GPIO LOW. Default po startu: štítek NAPÁJENÝ."
echo "Stav:   sudo systemctl status waterfall --no-pager"
echo "Log:    journalctl -u waterfall -f"
echo "Web:    http://${HOST_IP:-<IP>}:8088/   nebo http://$(hostname).local:8088/"
if [[ "$MODE" == "full" ]]; then
  echo
  echo "Po čisté instalaci jednou rebootni, ať se projeví skupiny gpio/dialout."
  echo "Zapoj nRF52840 dongle a (volitelně) TWN4 až po rebootu, nebo ted a restartuj službu."
fi
