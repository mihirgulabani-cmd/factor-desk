#!/bin/bash
# One-shot refresh: pull data → score → build the desk.
#   bash ~/Downloads/factor-desk/refresh.sh            # incremental: prices daily, fundamentals rolling (≤80 names/run, 30-day max age)
#   bash ~/Downloads/factor-desk/refresh.sh --full     # wipe the fundamentals cache and re-pull everything (~45 min)
#   bash ~/Downloads/factor-desk/refresh.sh --quiet    # no browser open (used by the scheduled job)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="$HOME/Downloads/screener_data"
QUIET=0
for a in "$@"; do
  [ "$a" == "--full" ] && rm -rf "$DATA/fundamentals" "$DATA/mcap.csv" "$DATA/universe_all.csv"
  [ "$a" == "--quiet" ] && QUIET=1
done
echo "===== refresh $(date) ====="
python3 "$HERE/build_dataset.py" --out "$DATA"
python3 "$HERE/screener_model.py" --data "$DATA"
python3 "$HERE/build_html.py" --data "$DATA" --template "$HERE/model_template.html"
echo "Open: $DATA/NSE-Factor-Desk.html"
[ "$QUIET" == "1" ] || open "$DATA/NSE-Factor-Desk.html" 2>/dev/null || true
