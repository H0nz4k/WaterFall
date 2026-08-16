#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/waterfall
DATA_ROOT=/var/lib/waterfall
SERVICE_USER="${SUDO_USER:-$USER}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Spusť přes sudo: sudo ./install.sh"; exit 1; fi

echo "== WaterFall v0.4.3 =="
echo "User: $SERVICE_USER"
export DEBIAN_FRONTEND=noninteractive
apt update
apt install -y python3 python3-venv python3-pip python3-gpiozero bluez iw tshark
mkdir -p "$APP_DIR" "$DATA_ROOT/captures" "$DATA_ROOT/experiments" "$DATA_ROOT/capture/exports" "$DATA_ROOT/pcap"
cp -a "$SOURCE_DIR/app" "$SOURCE_DIR/requirements.txt" "$SOURCE_DIR/config.example.json" "$SOURCE_DIR/run_server.py" "$APP_DIR/"

if [[ ! -f "$APP_DIR/config.json" ]]; then
  cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
  echo "Vytvořen nový $APP_DIR/config.json"
else
  echo "Existující $APP_DIR/config.json zachován. POZOR: po upgrade 0.3 -> 0.4 doplň sekce capture/analysis/ble_observer/wifi_monitor podle config.example.json."
fi

rm -rf "$APP_DIR/.venv"
python3 -m venv --system-site-packages "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
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
Description=WaterFall v0.4.3 RF Capture Center
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
echo "Instalace / update hotový."
echo "Config: $APP_DIR/config.json"
echo "Stav: sudo systemctl status waterfall --no-pager"
echo "Log:  journalctl -u waterfall -f"
echo "Web:  http://<IP_RPI>:8088/"
echo "Po prvním přidání do dialout/gpio/bluetooth doporučuji reboot."
