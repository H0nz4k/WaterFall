#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp)"
python3 - "$HERE/config.example.json" "$TMP" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
cfg = json.load(open(src, encoding="utf-8"))
cfg["mock_mode"] = True
cfg["nfc_reader"]["enabled"] = False
cfg["relay"]["enabled"] = False
cfg["ble_observer"]["enabled"] = False
cfg["wifi_monitor"]["enabled"] = False
cfg["csv_dir"] = "/tmp/waterfall/captures"
cfg["experiment_dir"] = "/tmp/waterfall/experiments"
cfg["capture"]["db_path"] = "/tmp/waterfall/capture/waterfall.sqlite3"
cfg["capture"]["export_dir"] = "/tmp/waterfall/capture/exports"
json.dump(cfg, open(dst, "w", encoding="utf-8"), indent=2)
PY
export WATERFALL_CONFIG="$TMP"
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8088
