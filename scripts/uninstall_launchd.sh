#!/bin/bash
# Stops and removes the LaunchAgent. The database in data/ and the logs are left in place.
set -euo pipefail

LABEL="local.ecobox-sarj-takip"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || echo "Servis zaten yüklü değildi."
rm -f "$TARGET"
echo "Kaldırıldı: $TARGET (veri ve loglar duruyor)"
