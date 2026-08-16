#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/waterfall
DATA_ROOT=/var/lib/waterfall
SERVICE_USER="${SUDO_USER:-$USER}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="full"
if [[ "${1:-}" == "--update" ]]; then
  MODE="update"
fi

VERSION="0.4.5"
if [[ -f "$SOURCE_DIR/VERSION" ]]; then
  VERSION="$(tr -d '[:space:]' < "$SOURCE_DIR/VERSION")"
fi

if [[ $EUID -ne 0 ]]; then echo "Spusť přes sudo: sudo ./install.sh [--update]"; exit 1; fi

echo "== WaterFall v${VERSION} (${MODE}) =="
echo "User: $SERVICE_USER"
echo "Zdroj: $SOURCE_DIR"

if [[ "$MODE" == "full" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt update
  apt install -y python3 python3-venv python3-pip python3-gpiozero bluez iw tshark
fi

mkdir -p "$APP_DIR" "$DATA_ROOT/captures" "$DATA_ROOT/experiments" "$DATA_ROOT/capture/exports" "$DATA_ROOT/pcap"

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
copy_if_present "$SOURCE_DIR/udev" "$APP_DIR/udev"
chmod +x "$APP_DIR/watch_probe_state.sh" 2>/dev/null || true

UDEV_SRC="$SOURCE_DIR/udev/99-openvusion-rf-probe.rules"
if [[ -f "$UDEV_SRC" ]]; then
  cp "$UDEV_SRC" /etc/udev/rules.d/99-openvusion-rf-probe.rules
  udevadm control --reload-rules 2>/dev/null || true
  udevadm trigger 2>/dev/null || true
  echo "udev: ModemManager ignoruje OpenVusion RF Probe (2fe3:0001)."
fi

if [[ ! -f "$APP_DIR/config.json" ]]; then
  cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
  echo "Vytvořen nový $APP_DIR/config.json"
else
  echo "Existující $APP_DIR/config.json zachován."
fi

if [[ "$MODE" == "full" || ! -x "$APP_DIR/.venv/bin/python" ]]; then
  rm -rf "$APP_DIR/.venv"
  python3 -m venv --system-site-packages "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/pip" install --upgrade pip
fi
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

ELATOOL_CANDIDATES=("/home/$SERVICE_USER/OpenVusion/tools/ElaTool" "$SOURCE_DIR/../../ElaTool")
for ELA in "${ELATOOL_CANDIDATES[@]}"; do
  if [[ -f "$ELA/pyproject.toml" || -f "$ELA/setup.py" ]]; then
    echo "Nalezen ElaTool: $ELA"
    "$APP_DIR/.venv/bin/pip" install -e "$ELA" || true
    break
  fi
done

chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR" "$DATA_ROOT"
for grp in dialout gpio bluetooth; do
  if getent group "$grp" >/dev/null; then usermod -aG "$grp" "$SERVICE_USER"; fi
done

cat > /etc/systemd/system/waterfall.service <<EOF
[Unit]
Description=WaterFall v${VERSION} RF Capture Center
After=network.target bluetooth.target
Wants=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
Environment=WATERFALL_CONFIG=$APP_DIR/config.json
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/run_server.py
Restart=on-failure
RestartSec=2
# CAP_NET_RAW je potřeba pouze pokud v configu zapneš experimentální 802.11 monitor-mode observer.
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable waterfall.service
systemctl restart waterfall.service

echo
echo "Instalace / update hotový (v${VERSION})."
echo "Config: $APP_DIR/config.json"
echo "Stav: sudo systemctl status waterfall --no-pager"
echo "Log:  journalctl -u waterfall -f"
echo "Web:  http://<IP_RPI>:8088/"
if [[ "$MODE" == "full" ]]; then
  echo "Po prvním přidání do dialout/gpio/bluetooth doporučuji reboot."
fi
