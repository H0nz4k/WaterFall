#!/usr/bin/env bash
# Z PC (Git Bash) nahraje WaterFall na Raspberry a spustí upgrade.
#
#   ./push_to_pi.sh pi@192.168.1.50
#   ./push_to_pi.sh pi@192.168.1.50 --full
#
# --full  první instalace (apt + nové venv)
# bez něj jen zkopíruje soubory, doinstaluje pip balíčky a restartuje službu.
# config.json na Pi se nepřepisuje. Lokální config.json se neposílá.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=""
MODE="update"
for arg in "$@"; do
  case "$arg" in
    --full) MODE="full" ;;
    --update) MODE="update" ;;
    -*) echo "Neznámý přepínač: $arg" >&2; exit 2 ;;
    *) TARGET="$arg" ;;
  esac
done
TARGET="${TARGET:-${WATERFALL_PI:-}}"

if [[ -z "$TARGET" ]]; then
  echo "Použití:"
  echo "  $0 pi@192.168.1.50"
  echo "  $0 pi@192.168.1.50 --full"
  echo "  export WATERFALL_PI=pi@192.168.1.50 && $0"
  exit 2
fi

REMOTE_DIR="${WATERFALL_REMOTE_DIR:-~/waterfall-src}"
INSTALL_ARG="--update"
if [[ "$MODE" == "full" ]]; then
  INSTALL_ARG=""
fi

echo "== WaterFall → $TARGET =="
echo "Zdroj:  $HERE"
echo "Cíl:    $REMOTE_DIR"
echo "Režim:  ${INSTALL_ARG:-full}"

ssh -o BatchMode=yes "$TARGET" "mkdir -p $REMOTE_DIR" 2>/dev/null || ssh -t "$TARGET" "mkdir -p $REMOTE_DIR"

EXCLUDES=(
  --exclude .git
  --exclude .venv
  --exclude config.json
  --exclude __pycache__
  --exclude '*.pyc'
  --exclude .idea
  --exclude .vscode
  --exclude '*.sqlite3'
  --exclude '*.sqlite3-wal'
  --exclude '*.sqlite3-shm'
)

if command -v rsync >/dev/null 2>&1; then
  rsync -az --delete "${EXCLUDES[@]}" "$HERE/" "$TARGET:$REMOTE_DIR/"
else
  echo "rsync není v PATH, používám scp (pomalejší)."
  ssh -t "$TARGET" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"
  scp -r \
    "$HERE/app" \
    "$HERE/requirements.txt" \
    "$HERE/config.example.json" \
    "$HERE/run_server.py" \
    "$HERE/install.sh" \
    "$HERE/VERSION" \
    "$HERE/watch_probe_state.sh" \
    "$HERE/TROUBLESHOOTING.md" \
    "$TARGET:$REMOTE_DIR/"
fi

echo "== Upgrade na Pi =="
# -t kvůli heslu na sudo
ssh -t "$TARGET" "chmod +x $REMOTE_DIR/install.sh $REMOTE_DIR/watch_probe_state.sh && sudo $REMOTE_DIR/install.sh $INSTALL_ARG"

echo
echo "Hotovo. Web: http://${TARGET#*@}:8088/"
echo "Stav:  ssh $TARGET 'sudo systemctl status waterfall --no-pager'"
echo "Log:   ssh $TARGET 'journalctl -u waterfall -f'"
