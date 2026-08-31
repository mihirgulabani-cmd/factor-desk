#!/bin/bash
# The 8:30 weekday-morning collection run (called by launchd via install_nse_morning.sh):
#   1. nse_pull.py        — bhavcopy/delivery %, F&O OI, announcements, insider, deals, calendar, ASM, shareholding
#   2. nse_results.py     — refresh filed quarterly results for the 150 stalest names
#   3. nse_results.py --pack  — rebuild results_panel.csv.gz
# Log: ~/screener_data/nse_pull.log
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
echo "===== morning pull $(date) ====="
"$PY" "$HERE/nse_pull.py"
"$PY" "$HERE/nse_results.py"
"$PY" "$HERE/nse_results.py" --pack
[ -f "$HERE/mb_radar.py" ] && "$PY" "$HERE/mb_radar.py" --refresh
echo "===== morning pull done $(date) ====="
