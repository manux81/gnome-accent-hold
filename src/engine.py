#!/usr/bin/env python3
# Accent Hold v0.9.3-experimental
# IBus engine: immediate typing + macOS-like custom popup (numbers below letters)

import gi
gi.require_version("IBus", "1.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import IBus, GLib, Gtk, Gdk
import cairo

BUS_NAME = "org.gnome.AccentHold"
ENGINE_NAME = "accent-hold"
HOLD_MS = 400

ACCENTS = {
    "a": ["à", "á", "â", "ä", "ǎ", "æ", "ã", "å", "ā"],
    "e": ["è", "é", "ê", "ë", "ě", "ẽ", "ē", "ė", "ę"],
    "i": ["ì", "í", "î", "ï", "ǐ", "ĩ", "ī", "ı", "į"],
    "o": ["ò", "ó", "ô", "ö", "ǒ", "œ", "ø", "õ", "ō"],
    "u": ["ù", "ú", "û", "ü", "ǔ", "ũ", "ū", "ű", "ů"],
    "w": ["ŵ"],
    "r": ["ř"],
    "t": ["ț", "ť", "þ"],
    "y": ["ý", "ŷ", "ÿ"],
    "c": ["ç", "ć", "č", "ċ"],
    "n": ["ñ", "ń", "ņ", "ň"],
    "k": ["ķ"],
    "h": ["ħ"],
    "g": ["ğ", "ġ"],
    "d": ["ď", "ð"],
    "z": ["ź", "ž", "ż"],

    "A": ["À", "Á", "Â", "Ä", "Ǎ", "Æ", "Ã", "Å", "Ā"],
    "E": ["È", "É", "Ê", "Ë", "Ě", "Ẽ", "Ē", "Ė", "Ę"],
    "I": ["Ì", "Í", "Î", "Ï", "Ǐ", "Ĩ", "Ī", "İ", "Į"],
    "O": ["Ò", "Ó", "Ô", "Ö", "Ǒ", "Œ", "Ø", "Õ", "Ō"],
    "U": ["Ù", "Ú", "Û", "Ü", "Ǔ", "Ũ", "Ū", "Ű", "Ů"],
    "W": ["Ŵ"],
    "R": ["Ř"],
    "T": ["Ț", "Ť", "Þ"],
    "Y": ["Ý", "Ŷ", "Ÿ"],
    "C": ["Ç", "Ć", "Č", "Ċ"],
    "N": ["Ñ", "Ń", "Ņ", "Ň"],
    "K": ["Ķ"],
    "H": ["Ħ"],
    "G": ["Ğ", "Ġ"],
    "D": ["Ď", "Ð"],
    "Z": ["Ź", "Ž", "Ż"],
}


class AccentPopup(Gtk.Window):
    """Compact macOS-inspired candidate popup with numbers below glyphs."""

    CSS = b"""
    window.accent-popup {
        background-color: transparent;
        border: 0;
        border-radius: 10px;
    }
    button.accent-cell {
        background-image: none;
        background-color: transparent;
        color: #202124;
        border: 0;
        border-radius: 6px;
        box-shadow: none;
        padding: 4px 7px 3px 7px;
        margin: 1px;
        min-width: 31px;
        min-height: 40px;
    }
    button.accent-cell:hover,
    button.accent-cell:selected,
    button.accent-selected {
        background-image: none;
        background-color: rgba(53, 132, 228, 0.96);
        color: white;
    }
    label.accent-glyph {
        font-size: 19px;
        font-weight: 500;
        color: #202124;
    }
    label.accent-number {
        font-size: 9px;
        color: #5f6368;
    }
    button.accent-selected,
    button.accent-selected label {
        background-image: none;
        background-color: rgba(53, 132, 228, 0.96);
        color: white;
    }
    """

    def __init__(self, on_choose, debug=None):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.on_choose = on_choose
        self.debug = debug
        self.buttons = []
        self.selected = 0

        self.set_name("accent-popup")
        self.get_style_context().add_class("accent-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)

        # Request an alpha-capable visual before the GDK window is realized.
        rgba_screen = self.get_screen()
        if rgba_screen is not None:
            rgba_visual = rgba_screen.get_rgba_visual()
            if rgba_visual is not None and rgba_screen.is_composited():
                self.set_visual(rgba_visual)
                self.set_app_paintable(True)
                if self.debug:
                    self.debug("RGBA VISUAL enabled")
            elif self.debug:
                self.debug(
                    f"RGBA VISUAL unavailable composited={rgba_screen.is_composited()}"
                )
        self.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)

        provider = Gtk.CssProvider()
        provider.load_from_data(self.CSS)
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._css_provider = provider

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        outer.set_border_width(3)
        self.add(outer)
        self.box = outer
        self.connect("draw", self._draw_transparent_background)

    def _draw_transparent_background(self, widget, cr):
        # v0.8.10: paint the glass panel ourselves. This makes alpha explicit
        # instead of depending on GTK theme/CSS background compositing.
        try:
            alloc = self.get_allocation()
            w = float(alloc.width)
            h = float(alloc.height)
            r = 10.0
            inset = 0.5

            cr.save()
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.paint()
            cr.restore()

            def rounded_rect(x, y, width, height, radius):
                x2 = x + width
                y2 = y + height
                cr.new_sub_path()
                cr.arc(x2 - radius, y + radius, radius, -1.5707963268, 0)
                cr.arc(x2 - radius, y2 - radius, radius, 0, 1.5707963268)
                cr.arc(x + radius, y2 - radius, radius, 1.5707963268, 3.1415926536)
                cr.arc(x + radius, y + radius, radius, 3.1415926536, 4.7123889804)
                cr.close_path()

            cr.save()
            cr.set_operator(cairo.OPERATOR_OVER)
            rounded_rect(inset, inset, max(0.0, w - 1.0), max(0.0, h - 1.0), r)

            # Frosted-gray tuning: enough alpha to feel translucent while
            # keeping background text subdued.
            cr.set_source_rgba(0.86, 0.86, 0.86, 0.88)
            cr.fill_preserve()

            cr.set_source_rgba(0.27, 0.27, 0.27, 0.24)
            cr.set_line_width(1.0)
            cr.stroke()
            cr.restore()

        except Exception as exc:
            if self.debug:
                self.debug(f"RGBA DRAW ERROR {exc!r}")
        return False

    def _clear(self):
        for child in list(self.box.get_children()):
            self.box.remove(child)
            child.destroy()
        self.buttons = []

    def show_candidates(self, candidates, x=None, y=None):
        self._clear()
        self.selected = 0

        for idx, char in enumerate(candidates):
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_can_focus(False)
            button.get_style_context().add_class("accent-cell")

            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            glyph = Gtk.Label(label=char)
            glyph.get_style_context().add_class("accent-glyph")

            number = Gtk.Label(label=str(idx + 1))
            number.get_style_context().add_class("accent-number")

            col.pack_start(glyph, True, True, 0)
            col.pack_start(number, False, False, 0)
            button.add(col)
            button.connect("clicked", lambda _b, i=idx: self.on_choose(i))

            self.box.pack_start(button, False, False, 0)
            self.buttons.append(button)

        self.show_all()
        self.select(0)

        # IBus cursor coordinates are normally screen/root coordinates on X11.
        # Clamp to the monitor workarea so the popup never ends up off-screen.
        display = Gdk.Display.get_default()
        screen = Gdk.Screen.get_default()

        # Cursor coordinates supplied by IBus are preferred.  On some X11
        # clients (notably terminals) they may be missing or client-relative.
        # We log both cursor and pointer positions so this can be diagnosed
        # without guessing offsets.
        mx = my = None
        if display is not None:
            seat = display.get_default_seat()
            pointer = seat.get_pointer() if seat else None
            if pointer is not None:
                _scr, mx, my = pointer.get_position()

        # IBus/X11 cursor locations can be in device-pixel coordinates while
        # Gtk.Window.move() works in GDK logical monitor coordinates. Convert
        # the caret to the coordinate space of the monitor that contains it.
        cursor_ok = x is not None and y is not None
        scale = 1
        monitor_index = -1

        if cursor_ok and screen is not None:
            try:
                nmon = screen.get_n_monitors()
                # First try raw coordinates against physicalized monitor bounds.
                for i in range(nmon):
                    g = screen.get_monitor_geometry(i)
                    sc = max(1, screen.get_monitor_scale_factor(i))
                    gx, gy = g.x * sc, g.y * sc
                    gw, gh = g.width * sc, g.height * sc
                    if gx <= x < gx + gw and gy <= y < gy + gh:
                        monitor_index = i
                        scale = sc
                        break

                if monitor_index < 0:
                    monitor_index = screen.get_monitor_at_point(int(x), int(y))
                    if monitor_index >= 0:
                        scale = max(1, screen.get_monitor_scale_factor(monitor_index))
            except Exception as exc:
                self.debug and self.debug(f"SCALE DETECT ERROR {exc!r}")
                scale = 1

            if scale > 1 and monitor_index >= 0:
                g = screen.get_monitor_geometry(monitor_index)
                # Preserve the logical monitor origin; scale only the offset
                # inside that monitor.
                physical_origin_x = g.x * scale
                physical_origin_y = g.y * scale
                px = g.x + (x - physical_origin_x) / scale
                py = g.y + (y - physical_origin_y) / scale + 4
                source = f"ibus-caret-scale{scale}"
            else:
                px, py = x, y + 4
                source = "ibus-caret-scale1"

            px, py = int(round(px)), int(round(py))
        elif mx is not None and my is not None:
            px, py = int(mx + 10), int(my + 18)
            source = "pointer-fallback"
        else:
            px, py = 20, 20
            source = "fallback"

        self.realize()
        req = self.get_preferred_size()[1]
        width, height = req.width, req.height

        if screen is not None:
            monitor = screen.get_monitor_at_point(px, py)
            work = screen.get_monitor_workarea(monitor)
            px = max(work.x + 4, min(px, work.x + work.width - width - 4))
            # Prefer below caret; if no room, show above it.
            if py + height > work.y + work.height - 4:
                py = max(work.y + 4, py - height - 28)

        self.move(px, py)
        if self.debug:
            self.debug(
                f"POPUP POS source={source} monitor={monitor_index} scale={scale} "
                f"cursor=({x},{y}) pointer=({mx},{my}) final=({px},{py}) "
                f"size=({width},{height})"
            )

    def select(self, index):
        if not self.buttons:
            return
        self.selected = index % len(self.buttons)
        for i, button in enumerate(self.buttons):
            ctx = button.get_style_context()
            if i == self.selected:
                ctx.add_class("accent-selected")
            else:
                ctx.remove_class("accent-selected")

    def hide_popup(self):
        self.hide()
        self._clear()


class AccentHoldEngine(IBus.Engine):
    def __init__(self, connection, object_path):
        super().__init__(connection=connection, object_path=object_path)

        self.pending_char = None
        self.pending_keyval = None
        self.timer_id = 0
        self.popup_active = False
        self.candidates = []
        self.selected = 0

        self.cursor_x = None
        self.cursor_y = None
        self.cursor_w = 0
        self.cursor_h = 0

        self.popup = None
        self._debug("ENGINE CREATED")

    def _debug(self, message):
        try:
            with open("/tmp/accent-hold.log", "a", encoding="utf-8") as f:
                f.write(f"{GLib.get_monotonic_time()} {message}\n")
                f.flush()
        except Exception:
            pass

    def do_set_cursor_location(self, x, y, w, h):
        self._debug(f"CURSOR x={x} y={y} w={w} h={h}")
        if x == 0 and y == 0 and w == 0 and h == 0:
            self._debug("CURSOR IGNORED empty rectangle")
            return
        self.cursor_x = x
        self.cursor_y = y
        self.cursor_w = w
        self.cursor_h = h

    def cancel_timer(self):
        if self.timer_id:
            self._debug(f"TIMER CANCEL id={self.timer_id} char={self.pending_char!r}")
            GLib.source_remove(self.timer_id)
            self.timer_id = 0

    def reset_pending(self):
        self.cancel_timer()
        self.pending_char = None
        self.pending_keyval = None

    def close_popup(self):
        self.popup_active = False
        self.candidates = []
        self.selected = 0
        if self.popup is not None:
            self.popup.hide_popup()

    def cancel_all(self):
        self.close_popup()
        self.reset_pending()

    def commit(self, text):
        self.commit_text(IBus.Text.new_from_string(text))

    def replace_immediate_char(self, replacement):
        # v0.9.3 experimental replacement:
        # Prefer IBus surrounding-text deletion when the client exposes
        # useful surrounding text. Fall back to the known-good synthetic
        # BackSpace path for clients such as terminals.
        self._debug(f"REPLACE BEGIN replacement={replacement!r}")

        try:
            result = self.get_surrounding_text()
            self._debug(f"SURROUNDING raw={result!r}")

            if result is not None:
                text, cursor_pos, anchor_pos = result
                value = text.get_text() if text is not None else ""

                self._debug(
                    f"SURROUNDING text={value!r} "
                    f"cursor={cursor_pos} anchor={anchor_pos}"
                )

                if value and cursor_pos > 0:
                    self._debug("REPLACE ACTION delete_surrounding_text(-1, 1)")
                    self.delete_surrounding_text(-1, 1)
                    self._debug(f"REPLACE ACTION commit({replacement!r})")
                    self.commit(replacement)
                    self._debug("REPLACE END via surrounding-text")
                    return

        except Exception as exc:
            self._debug(f"SURROUNDING/DELETE ERROR {exc!r}")

        self._debug("REPLACE ACTION fallback BackSpace")
        self.forward_key_event(
            IBus.KEY_BackSpace,
            14,
            0,
        )
        self.forward_key_event(
            IBus.KEY_BackSpace,
            14,
            IBus.ModifierType.RELEASE_MASK,
        )
        self._debug(f"REPLACE ACTION commit({replacement!r})")
        self.commit(replacement)
        self._debug("REPLACE END via BackSpace")

    def ensure_popup(self):
        if self.popup is None:
            self._debug("POPUP CREATE")
            self.popup = AccentPopup(self.choose_candidate, self._debug)
        return self.popup

    def open_popup(self):
        self._debug(f"TIMER FIRED char={self.pending_char!r} keyval={self.pending_keyval!r}")
        self.timer_id = 0

        if not self.pending_char or self.pending_char not in ACCENTS:
            return False

        self.candidates = ACCENTS[self.pending_char]
        self.selected = 0
        self.popup_active = True

        x = self.cursor_x
        y = None
        if self.cursor_y is not None:
            y = self.cursor_y + max(0, self.cursor_h)

        self._debug(
            f"CARET USE x={self.cursor_x} y={self.cursor_y} "
            f"w={self.cursor_w} h={self.cursor_h} bottom={y}"
        )
        self.ensure_popup().show_candidates(self.candidates, x, y)
        return False

    def choose_candidate(self, index):
        if not self.popup_active or not (0 <= index < len(self.candidates)):
            return

        replacement = self.candidates[index]
        self.replace_immediate_char(replacement)
        self.close_popup()
        self.reset_pending()

    def move_selection(self, delta):
        if not self.popup_active or not self.candidates:
            return
        self.selected = (self.selected + delta) % len(self.candidates)
        if self.popup is not None:
            self.popup.select(self.selected)

    def do_focus_out(self):
        self.cancel_all()

    def do_reset(self):
        self.cancel_all()

    def do_destroy(self):
        self.cancel_all()
        try:
            if self.popup is not None:
                self.popup.destroy()
        except Exception:
            pass
        super().do_destroy()

    def do_process_key_event(self, keyval, keycode, state):
        release = bool(state & IBus.ModifierType.RELEASE_MASK)

        # Never intercept Ctrl/Alt/Super shortcuts. Shift is deliberately allowed.
        shortcut_mask = (
            IBus.ModifierType.CONTROL_MASK
            | IBus.ModifierType.MOD1_MASK
            | IBus.ModifierType.SUPER_MASK
        )
        if state & shortcut_mask:
            self.cancel_all()
            return False

        # Candidate popup controls.
        if self.popup_active:
            # X11 autorepeat sends more PRESS events for the original held key.
            # Never let those presses restart the timer or create more letters.
            if self.pending_keyval is not None and keyval == self.pending_keyval:
                if release:
                    self._debug(
                        f"HELD RELEASE keyval={keyval}; popup remains active"
                    )
                else:
                    self._debug(f"REPEAT SWALLOWED keyval={keyval}")
                return True

            if release:
                return True

            if IBus.KEY_1 <= keyval <= IBus.KEY_9:
                idx = keyval - IBus.KEY_1
                if idx < len(self.candidates):
                    self.choose_candidate(idx)
                return True

            if keyval in (IBus.KEY_Left, IBus.KEY_Up):
                self.move_selection(-1)
                return True

            if keyval in (IBus.KEY_Right, IBus.KEY_Down):
                self.move_selection(+1)
                return True

            if keyval in (IBus.KEY_Return, IBus.KEY_KP_Enter, IBus.KEY_space):
                self.choose_candidate(self.selected)
                return True

            if keyval == IBus.KEY_Escape:
                # Original plain letter was already committed: just keep it.
                self.close_popup()
                self.reset_pending()
                return True

            # Any other key keeps the original letter and closes the popup.
            self.close_popup()
            self.reset_pending()
            return False

        # RELEASE of the letter being held: consume it, but do not commit anything.
        # The normal character was already committed on PRESS.
        if release:
            if self.pending_keyval is not None and keyval == self.pending_keyval:
                self._debug(
                    f"RELEASE pending char={self.pending_char!r} "
                    f"keyval={keyval} timer_id={self.timer_id}"
                )
                self.reset_pending()
                return True
            return False

        char = IBus.keyval_to_unicode(keyval) or ""
        if isinstance(char, int):
            char = chr(char) if char else ""

        if char in ACCENTS:
            # Ignore hardware/autorepeat PRESS events while the same key is held.
            if self.pending_keyval == keyval:
                return True

            # If another pending key somehow exists, stop tracking it.
            self.reset_pending()

            # ZERO-LATENCY PATH:
            # commit immediately on PRESS, exactly when normal typing expects it.
            self.commit(char)

            self.pending_char = char
            self.pending_keyval = keyval
            self.timer_id = GLib.timeout_add(HOLD_MS, self.open_popup)
            self._debug(
                f"TIMER START id={self.timer_id} char={char!r} "
                f"keyval={keyval} keycode={keycode} state={int(state)}"
            )
            return True

        # Non-accentable key: don't interfere.
        if self.pending_char is not None:
            self.reset_pending()

        return False


class AccentHoldFactory(IBus.Factory):
    def __init__(self, bus):
        super().__init__(
            connection=bus.get_connection(),
            object_path="/org/freedesktop/IBus/Factory",
        )
        self.bus = bus
        self.counter = 0

    def do_create_engine(self, engine_name):
        if engine_name != ENGINE_NAME:
            return None

        self.counter += 1
        object_path = (
            "/org/freedesktop/IBus/AccentHold/"
            f"Engine{self.counter}"
        )
        return AccentHoldEngine(
            self.bus.get_connection(),
            object_path,
        )


def main():
    IBus.init()
    bus = IBus.Bus()
    if not bus.is_connected():
        raise RuntimeError("Cannot connect to IBus")

    factory = AccentHoldFactory(bus)
    bus.request_name(BUS_NAME, 0)

    print("Accent Hold v0.9.3-experimental ready", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
