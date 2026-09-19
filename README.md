# GNOME Accent Hold

macOS-style long-press accents for a US keyboard on GNOME using IBus.

Hold an accentable letter to open a compact candidate popup. Choose with the
number keys or arrow keys and Enter. A normal tap remains immediate.

## Features

- Fast US-keyboard typing path
- Long-press accent popup
- Number shortcuts shown below candidates
- Arrow-key navigation and Enter selection
- Uppercase accents with Shift
- Ctrl/Alt/Super shortcuts pass through normally
- X11 multi-monitor/scaling-aware popup positioning
- Semi-transparent popup
- Automatic activation after GNOME login

## Repository layout

```text
gnome-accent-hold/
├── src/
│   └── engine.py
├── data/
│   └── accent-hold.xml
├── install.sh
├── uninstall.sh
├── README.md
└── LICENSE
```

## Requirements

- GNOME
- IBus
- Python 3
- PyGObject / GTK 3
- pycairo
- X11 (the current popup positioning logic was developed and tested on X11)

On Ubuntu/Debian the required packages are typically available as:

```bash
sudo apt install ibus python3-gi gir1.2-gtk-3.0 python3-cairo
```

## Install

From the repository root:

```bash
chmod +x install.sh uninstall.sh
./install.sh
```

The installer uses only the current user's home directory:

- `~/.local/libexec/gnome-accent-hold/engine.py`
- `~/.local/share/ibus/component/accent-hold.xml`
- `~/.config/autostart/gnome-accent-hold.desktop`

It then asks IBus to activate `accent-hold`.

Check with:

```bash
ibus engine
```

Expected output:

```text
accent-hold
```

The GNOME autostart entry selects the engine again shortly after future logins.

## Uninstall

```bash
./uninstall.sh
```

## Development

After editing `src/engine.py`:

```bash
./install.sh
```

to reinstall the current working tree.

## Version

0.9.0
