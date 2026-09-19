#!/usr/bin/env bash
set -euo pipefail

ENGINE_DIR="$HOME/.local/libexec/gnome-accent-hold"
COMPONENT="$HOME/.local/share/ibus/component/accent-hold.xml"
AUTOSTART="$HOME/.config/autostart/gnome-accent-hold.desktop"

# Switch away before removing the engine when possible.
ibus engine xkb:us::eng >/dev/null 2>&1 || true

rm -rf "$ENGINE_DIR"
rm -f "$COMPONENT"
rm -f "$AUTOSTART"

echo "Removed GNOME Accent Hold user installation."

if [ -e /usr/share/ibus/component/accent-hold.xml ]; then
    echo
    echo "An old development component still exists system-wide:"
    echo "  /usr/share/ibus/component/accent-hold.xml"
    echo "If it belongs to this project, remove it with:"
    echo "  sudo rm /usr/share/ibus/component/accent-hold.xml"
fi

ibus restart >/dev/null 2>&1 || true
