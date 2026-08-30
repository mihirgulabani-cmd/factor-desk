#!/bin/bash
# Installs a launchd job that runs nse_pull.py every weekday MORNING at 08:30 (your Mac's local time),
# so overnight announcements, insider trades, bulk/block deals, delivery % and today's results
# calendar are all collected before the market opens. If the Mac is asleep at 08:30, it runs
# the moment you open the laptop.
#   bash ~/Documents/GitHub/factor-desk/install_nse_morning.sh          # install / update
#   bash ~/Documents/GitHub/factor-desk/install_nse_morning.sh --remove # uninstall
# Change the time:  HOUR=7 MIN=45 bash install_nse_morning.sh
# Logs: ~/screener_data/nse_pull.log
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.mihir.nsepull"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/screener_data/nse_pull.log"
PY="$(command -v python3)"
HOUR="${HOUR:-8}"; MIN="${MIN:-30}"

if [ "$1" == "--remove" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/screener_data/bin"
# copy the runner scripts OUT of Documents/Downloads (macOS blocks background jobs there)
for f in morning_pull.sh nse_pull.py nse_results.py pack_nse.py; do
  cp "$HERE/$f" "$HOME/screener_data/bin/$f"
done
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$HOME/screener_data/bin/morning_pull.sh</string>
  </array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$(dirname "$PY"):/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key><array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
echo "installed $LABEL — runs nse_pull.py weekdays at $HOUR:$MIN, log: $LOG"
echo "test it now with:  launchctl kickstart -k gui/$(id -u)/$LABEL"
