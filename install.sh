#!/usr/bin/env bash
set -euo pipefail

ENGINE_NAME="accent-hold"
ENGINE_DIR="$HOME/.local/libexec/gnome-accent-hold"
ENGINE_PATH="$ENGINE_DIR/engine.py"
COMPONENT_DIR="$HOME/.local/share/ibus/component"
USER_COMPONENT="$COMPONENT_DIR/accent-hold.xml"
LEGACY_SYSTEM_COMPONENT="/usr/share/ibus/component/accent-hold.xml"

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

# Install the component per-user. IBus does not scan this directory by
# default, so refresh its user registry cache with the custom path below.
mkdir -p "$COMPONENT_DIR"
install -m 0644 \
    "$TMP_XML" \
    "$USER_COMPONENT"

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
else
    echo "WARNING: 'ibus' was not found; the IBus registry cache was not refreshed."
fi

if [[ -f "$LEGACY_SYSTEM_COMPONENT" ]]; then
    echo
    echo "NOTE: A component from an older installation still exists at:"
    echo "  $LEGACY_SYSTEM_COMPONENT"
    echo "Remove it once with: sudo rm -f '$LEGACY_SYSTEM_COMPONENT'"
fi

echo
echo "GNOME Accent Hold installed."
echo "Engine:    $ENGINE_PATH"
echo "Component: $USER_COMPONENT"
echo
echo "Restart IBus or log out/in, then select:"
echo "  $ENGINE_NAME"
