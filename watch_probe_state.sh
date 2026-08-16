#!/usr/bin/env bash
# Sleduje /api/state, dokud PROBE nepřeblikne. Ctrl+C ukončí.
set -euo pipefail
URL="${1:-http://127.0.0.1:8088/api/state}"
echo "Sleduji $URL  (Ctrl+C)"
while true; do
    curl -s "$URL" | python3 -c '
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
