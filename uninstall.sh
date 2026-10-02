#!/usr/bin/env bash
set -euo pipefail

ENGINE_DIR="$HOME/.local/libexec/gnome-accent-hold"
COMPONENT_DIR="$HOME/.local/share/ibus/component"
USER_COMPONENT="$COMPONENT_DIR/accent-hold.xml"
LEGACY_SYSTEM_COMPONENT="/usr/share/ibus/component/accent-hold.xml"
AUTOSTART="$HOME/.config/autostart/gnome-accent-hold.desktop"

echo "Removing GNOME Accent Hold..."

pkill -f "$ENGINE_DIR/engine.py" 2>/dev/null || true

rm -rf "$ENGINE_DIR"

rm -f "$USER_COMPONENT"
rm -f "$AUTOSTART"

if command -v ibus >/dev/null 2>&1; then
    SYSTEM_COMPONENT_DIR="/usr/share/ibus/component"
    if command -v pkg-config >/dev/null 2>&1; then
        IBUS_DATA_DIR="$(pkg-config --variable=datadir ibus-1.0 2>/dev/null || true)"
        if [[ -n "$IBUS_DATA_DIR" ]]; then
            SYSTEM_COMPONENT_DIR="$IBUS_DATA_DIR/ibus/component"
        fi
    fi

    if [[ -n "${IBUS_COMPONENT_PATH:-}" ]]; then
        CACHE_COMPONENT_PATH="$COMPONENT_DIR:$IBUS_COMPONENT_PATH"
    else
        CACHE_COMPONENT_PATH="$COMPONENT_DIR:$SYSTEM_COMPONENT_DIR"
    fi

    echo "Refreshing the IBus user registry cache..."
    IBUS_COMPONENT_PATH="$CACHE_COMPONENT_PATH" ibus write-cache
fi

if [[ -f "$LEGACY_SYSTEM_COMPONENT" ]]; then
    echo
    echo "NOTE: A component from an older installation still exists at:"
    echo "  $LEGACY_SYSTEM_COMPONENT"
    echo "Remove it once with: sudo rm -f '$LEGACY_SYSTEM_COMPONENT'"
fi

echo
echo "GNOME Accent Hold removed."
echo "Restart IBus or log out/in to refresh the engine list."
