#!/usr/bin/env bash
set -euo pipefail

ENGINE_DIR="$HOME/.local/libexec/gnome-accent-hold"
SYSTEM_COMPONENT="/usr/share/ibus/component/accent-hold.xml"
USER_COMPONENT="$HOME/.local/share/ibus/component/accent-hold.xml"
AUTOSTART="$HOME/.config/autostart/gnome-accent-hold.desktop"

echo "Removing GNOME Accent Hold..."

pkill -f "$ENGINE_DIR/engine.py" 2>/dev/null || true

rm -rf "$ENGINE_DIR"

# Remove both locations, including files left by older releases
rm -f "$USER_COMPONENT"
rm -f "$AUTOSTART"

if [[ -f "$SYSTEM_COMPONENT" ]]; then
    echo "Removing IBus component (sudo required)..."
    sudo rm -f "$SYSTEM_COMPONENT"
fi

echo
echo "GNOME Accent Hold removed."
echo "Restart IBus or log out/in to refresh the engine list."
