#!/bin/bash
# Installs and starts the LaunchAgent that keeps `python3 -m ecobox run` alive.
# Uses the project's .venv interpreter when it exists (charts need it), else Homebrew Python.
set -euo pipefail

LABEL="local.ecobox-sarj-takip"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/launchd/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ -x "$PROJECT_DIR/.venv/bin/python3" ]]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
elif [[ -x /opt/homebrew/bin/python3 ]]; then
    PYTHON=/opt/homebrew/bin/python3
    echo "Not: .venv yok; grafikler için önce scripts/setup_venv.sh çalıştırın."
else
    echo "Ne $PROJECT_DIR/.venv/bin/python3 ne /opt/homebrew/bin/python3 bulundu." >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/data" "$HOME/Library/LaunchAgents"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" -e "s|__PYTHON__|$PYTHON|g" "$TEMPLATE" > "$TARGET"
plutil -lint "$TARGET"

# Replace an already loaded copy.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
# bootout returns before the job is gone; bootstrapping too early fails with error 5.
while launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; do sleep 1; done
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"

echo "Kuruldu: $TARGET"
echo "Python:  $PYTHON"
echo "Durum:   launchctl print $DOMAIN/$LABEL | head -20"
echo "Loglar:  $PROJECT_DIR/logs/"
