#!/bin/bash
# Installs a launchd job that runs refresh.sh every weekday at 18:30 (your Mac's local time).
#   bash ~/Downloads/factor-desk/install_daily.sh          # install / update
#   bash ~/Downloads/factor-desk/install_daily.sh --remove # uninstall
# Logs: ~/Downloads/screener_data/refresh.log
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.mihir.factordesk"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Downloads/screener_data/refresh.log"
PY="$(command -v python3)"
HOUR="${HOUR:-18}"; MIN="${MIN:-30}"

if [ "$1" == "--remove" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Downloads/screener_data"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$HERE/refresh.sh</string><string>--quiet</string>
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
echo "installed $LABEL — runs weekdays at $HOUR:$MIN, log: $LOG"
echo "test it now with:  launchctl kickstart -k gui/$(id -u)/$LABEL"
