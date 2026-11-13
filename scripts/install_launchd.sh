#!/bin/bash
# Installs and starts the LaunchAgent that keeps `python3 -m ecobox run` alive.
set -euo pipefail

LABEL="local.ecobox-sarj-takip"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/launchd/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ ! -x /opt/homebrew/bin/python3 ]]; then
    echo "/opt/homebrew/bin/python3 bulunamadı." >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data" "$HOME/Library/LaunchAgents"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$TEMPLATE" > "$TARGET"
plutil -lint "$TARGET"

# Replace an already loaded copy.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"

echo "Kuruldu: $TARGET"
echo "Durum:   launchctl print $DOMAIN/$LABEL | head -20"
echo "Loglar:  $PROJECT_DIR/logs/"
