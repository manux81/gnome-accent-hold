#!/usr/bin/env bash
set -euo pipefail

ENGINE_NAME="accent-hold"
ENGINE_DIR="$HOME/.local/libexec/gnome-accent-hold"
ENGINE_PATH="$ENGINE_DIR/engine.py"
SYSTEM_COMPONENT="/usr/share/ibus/component/accent-hold.xml"
USER_COMPONENT="$HOME/.local/share/ibus/component/accent-hold.xml"

cd "$(dirname "$0")"

[[ -f src/engine.py ]] || {
    echo "ERROR: src/engine.py not found"
    exit 1
}

[[ -f data/accent-hold.xml ]] || {
    echo "ERROR: data/accent-hold.xml not found"
    exit 1
}

echo "Installing GNOME Accent Hold..."

# Install engine per-user
mkdir -p "$ENGINE_DIR"
cp src/engine.py "$ENGINE_PATH"
chmod +x "$ENGINE_PATH"

# Generate component XML with the real engine path
TMP_XML="$(mktemp)"
trap 'rm -f "$TMP_XML"' EXIT

sed "s|@ENGINE_PATH@|$ENGINE_PATH|g" \
    data/accent-hold.xml > "$TMP_XML"

# Remove old per-user registration to avoid duplicates
rm -f "$USER_COMPONENT"

# Install the IBus component where IBus 1.5.x reliably discovers it
echo "Installing IBus component (sudo required)..."
sudo install -m 0644 \
    "$TMP_XML" \
    "$SYSTEM_COMPONENT"

echo
echo "GNOME Accent Hold installed."
echo "Engine:    $ENGINE_PATH"
echo "Component: $SYSTEM_COMPONENT"
echo
echo "Restart IBus or log out/in, then select:"
echo "  $ENGINE_NAME"
