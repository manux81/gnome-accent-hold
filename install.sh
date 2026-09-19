#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_DIR="$HOME/.local/libexec/gnome-accent-hold"
COMPONENT_DIR="$HOME/.local/share/ibus/component"
AUTOSTART_DIR="$HOME/.config/autostart"

ENGINE_DST="$ENGINE_DIR/engine.py"
COMPONENT_DST="$COMPONENT_DIR/accent-hold.xml"
AUTOSTART_DST="$AUTOSTART_DIR/gnome-accent-hold.desktop"

command -v ibus >/dev/null 2>&1 || {
    echo "Error: ibus is not installed or not in PATH." >&2
    exit 1
}

mkdir -p "$ENGINE_DIR" "$COMPONENT_DIR" "$AUTOSTART_DIR"

install -m 0755 "$ROOT/src/engine.py" "$ENGINE_DST"

# The IBus component requires a concrete executable path.
sed "s|@ENGINE_PATH@|$ENGINE_DST|g" \
    "$ROOT/data/accent-hold.xml" > "$COMPONENT_DST"
chmod 0644 "$COMPONENT_DST"

cat > "$AUTOSTART_DST" <<'EOF'
[Desktop Entry]
Type=Application
Name=GNOME Accent Hold
Comment=Enable macOS-style accent hold
Exec=sh -c 'sleep 3 && ibus engine accent-hold'
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

echo "Installed GNOME Accent Hold 0.9.0."
echo "  Engine:    $ENGINE_DST"
echo "  Component: $COMPONENT_DST"
echo "  Autostart: $AUTOSTART_DST"
echo
echo "Refreshing IBus..."

ibus restart >/dev/null 2>&1 || true
sleep 2

if ibus engine accent-hold >/dev/null 2>&1; then
    echo "Accent Hold is active."
else
    echo "IBus has not activated accent-hold yet."
    echo "Log out/in once, or run: ibus engine accent-hold"
fi
