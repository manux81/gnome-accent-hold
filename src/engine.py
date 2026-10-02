#!/usr/bin/env python3
"""macOS-style press-and-hold accents implemented as an IBus engine."""

import os
import time

import gi
gi.require_version("IBus", "1.0")
from gi.repository import GLib, IBus

try:
    from gi.repository import Gio
except Exception:  # pragma: no cover - headless test stubs
    Gio = None

BUS_NAME = "org.gnome.AccentHold"
ENGINE_NAME = "accent-hold"
HOLD_MS = 400
LOG_PATH = os.environ.get("ACCENT_HOLD_LOG", "/tmp/accent-hold.log")
# evdev KEY_BACKSPACE, the hardware keycode on any standard PC keyboard.
# Forwarded BackSpace events must carry a real keycode: keycode 0 is
# silently ignored by Wayland clients.
BACKSPACE_KEYCODE_DEFAULT = 14
# Delay between the forwarded BackSpace and the replacement commit. The
# BackSpace round-trips through the client (in a terminal: app -> pty ->
# shell -> redraw) while commit_text lands immediately; committing too early
# lets the BackSpace eat the fresh accent ("a" instead of "à", randomly).
COMMIT_DELAY_MS = 120
# Fallback key-repeat timing (used when the desktop settings are
# unreadable). Normally read from org.gnome.desktop.peripherals.keyboard
# (delay / repeat-interval).
KEY_REPEAT_DELAY_MS = 500
KEY_REPEAT_INTERVAL_MS = 30

ACCENTS = {
    "a": ["à", "á", "â", "ä", "ǎ", "æ", "ã", "å", "ā"],
    "e": ["è", "é", "ê", "ë", "ě", "ẽ", "ē", "ė", "ę"],
    "i": ["ì", "í", "î", "ï", "ǐ", "ĩ", "ī", "ı", "į"],
    "o": ["ò", "ó", "ô", "ö", "ǒ", "œ", "ø", "õ", "ō"],
    "u": ["ù", "ú", "û", "ü", "ǔ", "ũ", "ū", "ű", "ů"],
    "w": ["ŵ"], "r": ["ř"], "t": ["ț", "ť", "þ"],
    "y": ["ý", "ŷ", "ÿ"], "c": ["ç", "ć", "č", "ċ"],
    "n": ["ñ", "ń", "ņ", "ň"], "k": ["ķ"], "h": ["ħ"],
    "g": ["ğ", "ġ"], "d": ["ď", "ð"],
    "s": ["ß", "ş", "ș", "ś", "š"],
    "l": ["ł", "ļ", "ľ"], "z": ["ź", "ž", "ż"],
    "A": ["À", "Á", "Â", "Ä", "Ǎ", "Æ", "Ã", "Å", "Ā"],
    "E": ["È", "É", "Ê", "Ë", "Ě", "Ẽ", "Ē", "Ė", "Ę"],
    "I": ["Ì", "Í", "Î", "Ï", "Ǐ", "Ĩ", "Ī", "İ", "Į"],
    "O": ["Ò", "Ó", "Ô", "Ö", "Ǒ", "Œ", "Ø", "Õ", "Ō"],
    "U": ["Ù", "Ú", "Û", "Ü", "Ǔ", "Ũ", "Ū", "Ű", "Ů"],
    "W": ["Ŵ"], "R": ["Ř"], "T": ["Ț", "Ť", "Þ"],
    "Y": ["Ý", "Ŷ", "Ÿ"], "C": ["Ç", "Ć", "Č", "Ċ"],
    "N": ["Ñ", "Ń", "Ņ", "Ň"], "K": ["Ķ"], "H": ["Ħ"],
    "G": ["Ğ", "Ġ"], "D": ["Ď", "Ð"],
    "S": ["ẞ", "Ś", "Š", "Ş", "Ș"],
    "L": ["Ł", "Ļ", "Ľ"], "Z": ["Ź", "Ž", "Ż"],
}


class AccentHoldEngine(IBus.Engine):
    """Forward-first plus a native IBus lookup table for the hold menu.

    Normal typing is never swallowed: PRESS/RELEASE of ordinary keys are
    forwarded to the application (return False), which inserts them
    natively. The engine only *observes* supported letters to arm hold
    detection. This keeps clients that bypass IME commits working — e.g.
    browser canvases or web-based server consoles where commit_text would
    never land and an intercepted key would be lost entirely.

    When a supported letter is held, the native IBus panel shows candidates
    (IBus.LookupTable; no Gtk top-level surface, so no focus steal or tiling
    side effects on Wayland). Choosing a candidate deletes the natively
    inserted plain character (surrounding-text deletion or a forwarded
    BackSpace fallback) and commits the replacement.

    Key repeat: on Wayland, autorepeat events reach the client even when the
    engine consumes them, so a held key would always leak extra characters
    ("aa" + menu). Like macOS press-and-hold, the engine therefore keeps the
    compositor key repeat switched off for its whole lifetime and
    reimplements repeat itself, but ONLY for non-accentable keys
    (BackSpace, arrows, plain consonants, ...). A held accentable letter
    produces exactly one character plus the picker — never repeats.
    """

    def __init__(self, connection, object_path):
        # Ask clients to expose/push surrounding text (notably on Wayland,
        # where delete_surrounding_text() needs client support). The property
        # is construct-only on some IBus versions, so prefer passing it at
        # construction time with a set_property() fallback.
        try:
            super().__init__(
                connection=connection,
                object_path=object_path,
                active_surrounding_text=True,
            )
        except TypeError:
            super().__init__(connection=connection, object_path=object_path)
            try:
                self.set_property("active-surrounding-text", True)
            except Exception:
                pass
        self.pending_char = None
        self.pending_keyval = None
        self.timer_id = 0
        self.popup_active = False
        self.candidates = []
        self.selected = 0
        self.lookup_table = None
        # Last surrounding-text push from the client (see
        # do_set_surrounding_text); fallback signal when a synchronous
        # get_surrounding_text() query returns nothing.
        self.surrounding_text = ""
        self.surrounding_cursor = 0
        self.surrounding_anchor = 0
        # Client capabilities reported via do_set_capabilities (0 until the
        # first report). Logged for diagnostics; the popup is intentionally
        # NOT gated on them because the lookup table is rendered by the
        # IBus panel, not by the client toolkit.
        self.capabilities = 0
        self.caps_known = False
        # Last hardware keycode observed for BackSpace (evdev 14 on PC
        # keyboards); used so the replacement fallback forwards a usable
        # key event instead of keycode 0, which Wayland clients ignore.
        self.backspace_keycode = BACKSPACE_KEYCODE_DEFAULT
        # Pending delayed replacement commit (BackSpace fallback path).
        self.commit_timer_id = 0
        # Compositor key repeat stays off while this engine lives (see
        # class docstring); repeat for ordinary keys is reimplemented
        # below with keyrepeat_timer_id.
        self._repeat_suppressed = False
        self._repeat_was_enabled = True
        self._suppress_repeat()
        # Engine-side repeat of the currently held non-accentable key.
        self.keyrepeat_timer_id = 0
        self.keyrepeat_key = None
        # Autorepeat PRESS events observed (and swallowed) while the hold is
        # armed or the popup is open. Kept as a safety net: with the
        # compositor repeat off there should be none, but if any leak the
        # replacement deletes 1 + repeat_extra chars.
        self.repeat_extra = 0
        self._debug("ENGINE CREATED ui=ibus-lookup-table replacement=forward-first")

    def _debug(self, message):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as log:
                log.write(f"{time.monotonic_ns() // 1000} {message}\n")
        except OSError:
            pass

    def _selection_repr(self):
        if self.popup_active and 0 <= self.selected < len(self.candidates):
            return repr(self.candidates[self.selected])
        return "None"

    def _log_event(self, keyval, keycode, state, release):
        try:
            keyname = IBus.keyval_name(keyval)
        except Exception:
            keyname = None
        self._debug(
            f"KEY keyval={keyval} name={keyname!r} keycode={keycode} "
            f"state=0x{int(state):x} release={release} "
            f"popup_active={self.popup_active} pending={self.pending_char!r} "
            f"pending_keyval={self.pending_keyval!r} selected={self._selection_repr()}"
        )

    def do_set_cursor_location(self, x, y, w, h):
        self._debug(f"CURSOR x={x} y={y} w={w} h={h}")
        # No manual positioning: the IBus panel anchors the native lookup
        # table at this location on both X11 and Wayland.

    def do_set_surrounding_text(self, text, cursor_index, anchor_pos):
        try:
            value = text.get_text() if text is not None else ""
        except Exception:
            value = ""
        self.surrounding_text = value or ""
        self.surrounding_cursor = cursor_index
        self.surrounding_anchor = anchor_pos
        self._debug(
            f"SURROUNDING PUSH text={self.surrounding_text!r} "
            f"cursor={cursor_index} anchor={anchor_pos}"
        )

    @staticmethod
    def _decode_capabilities(caps):
        # Best-effort decode for the debug log. Flag values are resolved
        # from the introspection enum when available (note the historical
        # "Capabilites" misspelling) and fall back to the classic bit
        # positions otherwise.
        cap_cls = getattr(IBus, "Capabilites", None) or getattr(
            IBus, "Capabilities", None
        )
        names = []
        for attr, fallback in (
            ("PREEDIT_TEXT", 1),
            ("AUXILIARY_TEXT", 2),
            ("LOOKUP_TABLE", 4),
            ("FOCUS", 8),
            ("PROPERTY", 16),
            ("SURROUNDING_TEXT", 32),
        ):
            try:
                bit = int(getattr(cap_cls, attr, fallback))
            except Exception:
                bit = fallback
            try:
                if int(caps) & bit:
                    names.append(attr)
            except Exception:
                pass
        return names

    def do_set_capabilities(self, caps):
        try:
            self.capabilities = int(caps)
        except Exception:
            self.capabilities = 0
        self.caps_known = True
        decoded = self._decode_capabilities(self.capabilities)
        self._debug(
            f"CAPABILITIES caps=0x{self.capabilities:x} "
            f"flags={decoded if decoded else 'none-or-unknown'}"
        )

    def do_focus_in(self):
        self._debug("FOCUS IN")
        # Re-assert: the user may have toggled repeat manually meanwhile.
        self._suppress_repeat()

    def do_focus_out(self):
        self._debug(
            f"FOCUS OUT pending={self.pending_char!r} timer_id={self.timer_id} "
            f"popup_active={self.popup_active}"
        )
        if self.pending_char is not None:
            self._debug("FOCUS OUT IGNORED during active interaction")
            return
        self.cancel_all()

    def do_reset(self):
        self._debug(
            f"RESET pending={self.pending_char!r} timer_id={self.timer_id} "
            f"popup_active={self.popup_active}"
        )
        if self.pending_char is not None:
            self._debug("RESET IGNORED during active interaction")
            return
        self.cancel_all()

    def cancel_timer(self):
        if self.timer_id:
            self._debug(f"TIMER CANCEL id={self.timer_id}")
            GLib.source_remove(self.timer_id)
            self.timer_id = 0

    @staticmethod
    def _keyboard_settings():
        if Gio is None:
            return None
        try:
            return Gio.Settings.new("org.gnome.desktop.peripherals.keyboard")
        except Exception:
            return None

    def _suppress_repeat(self):
        # Persistent while the engine lives (macOS press-and-hold model):
        # the compositor must never generate repeats, otherwise held keys
        # leak extra characters past the engine. Async write (~0.1ms), and
        # the user's original value is restored on destroy.
        if self._repeat_suppressed:
            return
        settings = self._keyboard_settings()
        if settings is None:
            return
        try:
            self._repeat_was_enabled = bool(settings.get_boolean("repeat"))
            if self._repeat_was_enabled:
                settings.set_boolean("repeat", False)
                self._debug("REPEAT SUPPRESSED via gsettings (async)")
            self._repeat_suppressed = True
        except Exception as exc:
            self._debug(f"REPEAT SUPPRESS ERROR {exc!r}")

    def _restore_repeat(self):
        # Only on engine teardown; never during typing.
        if not self._repeat_suppressed:
            return
        self._repeat_suppressed = False
        if not self._repeat_was_enabled:
            return
        settings = self._keyboard_settings()
        if settings is None:
            return
        try:
            settings.set_boolean("repeat", True)
            self._debug("REPEAT RESTORED via gsettings (async)")
        except Exception as exc:
            self._debug(f"REPEAT RESTORE ERROR {exc!r}")

    def _key_repeat_timing(self):
        delay, interval = KEY_REPEAT_DELAY_MS, KEY_REPEAT_INTERVAL_MS
        settings = self._keyboard_settings()
        if settings is not None:
            try:
                delay = int(settings.get_uint("delay"))
            except Exception:
                pass
            try:
                interval = int(settings.get_uint("repeat-interval"))
            except Exception:
                pass
        delay = max(50, delay)
        interval = max(1, min(interval, delay))
        return delay, interval

    @staticmethod
    def _is_repeatable(keyval):
        for attr in (
            "KEY_Shift_L", "KEY_Shift_R",
            "KEY_Control_L", "KEY_Control_R",
            "KEY_Alt_L", "KEY_Alt_R",
            "KEY_Super_L", "KEY_Super_R",
            "KEY_Caps_Lock", "KEY_Num_Lock", "KEY_Scroll_Lock",
            "KEY_ISO_Level3_Shift", "KEY_ISO_Level5_Shift",
            "KEY_Mode_switch", "KEY_Multi_key",
        ):
            try:
                candidate = getattr(IBus, attr, None)
            except Exception:
                candidate = None
            if candidate is not None and keyval == candidate:
                return False
        return True

    def _start_key_repeat(self, keyval, keycode, state):
        self._cancel_key_repeat()
        if not self._is_repeatable(keyval):
            return
        try:
            press_state = int(state) & ~int(
                IBus.ModifierType.RELEASE_MASK
            )
        except Exception:
            press_state = 0
        self.keyrepeat_key = (keyval, keycode, press_state)
        delay, _interval = self._key_repeat_timing()
        self._debug(f"KEYREPEAT ARM keyval={keyval} in {delay}ms")
        self.keyrepeat_timer_id = GLib.timeout_add(
            delay, self._on_key_repeat_fire
        )

    def _cancel_key_repeat(self):
        if self.keyrepeat_timer_id:
            try:
                GLib.source_remove(self.keyrepeat_timer_id)
            except Exception:
                pass
            self.keyrepeat_timer_id = 0
        self.keyrepeat_key = None

    def _on_key_repeat_fire(self):
        if self.keyrepeat_key is None:
            self.keyrepeat_timer_id = 0
            return GLib.SOURCE_REMOVE
        keyval, keycode, press_state = self.keyrepeat_key
        self._debug(f"KEYREPEAT FIRE keyval={keyval}")
        try:
            self.forward_key_event(keyval, keycode, press_state)
        except Exception as exc:
            self._debug(f"KEYREPEAT FORWARD ERROR {exc!r}")
        _delay, interval = self._key_repeat_timing()
        self.keyrepeat_timer_id = GLib.timeout_add(
            interval, self._on_key_repeat_fire
        )
        return GLib.SOURCE_REMOVE

    def _clear_state(self):
        self.cancel_timer()
        self._cancel_key_repeat()
        self.pending_char = None
        self.pending_keyval = None
        self.repeat_extra = 0
        self.candidates = []
        self.selected = 0

    def close_popup(self):
        if self.popup_active:
            self._debug("LOOKUP HIDE")
            try:
                self.hide_lookup_table()
            except Exception as exc:
                self._debug(f"LOOKUP HIDE ERROR {exc!r}")
        self.popup_active = False
        self.lookup_table = None

    def cancel_all(self):
        self.close_popup()
        self._cancel_commit()
        self._clear_state()

    def _cancel_commit(self):
        if self.commit_timer_id:
            try:
                GLib.source_remove(self.commit_timer_id)
            except Exception:
                pass
            self.commit_timer_id = 0

    def _schedule_commit(self, text, extra_delay_ms=0):
        self._cancel_commit()

        def _do_commit():
            self.commit_timer_id = 0
            self._debug(f"REPLACE COMMIT commit({text!r})")
            self._commit(text)
            return GLib.SOURCE_REMOVE

        delay = COMMIT_DELAY_MS + extra_delay_ms
        self.commit_timer_id = GLib.timeout_add(delay, _do_commit)
        self._debug(
            f"REPLACE SCHEDULED commit({text!r}) in {delay}ms "
            f"id={self.commit_timer_id}"
        )

    def _commit(self, text):
        self.commit_text(IBus.Text.new_from_string(text))

    def replace_immediate_char(self, replacement, count=1):
        # The plain character was inserted natively by the application (the
        # engine forwarded the original PRESS). Replace it in place.
        # delete_surrounding_text() is only attempted when a surrounding
        # query actually returned usable text: several clients advertise
        # SURROUNDING_TEXT yet ignore the deletion (observed: query returns
        # empty, delete is a no-op, commit appends -> "aà"). The synthetic
        # BackSpace fallback works wherever a physical BackSpace works, so
        # it is the default whenever the buffer contents are unconfirmed.
        # Focus stays in the application because the candidate UI is the
        # native IBus panel, so both paths act on the correct context.
        self._debug(f"REPLACE BEGIN replacement={replacement!r}")

        surrounding_value = ""
        surrounding_cursor = 0
        have_surrounding = False
        try:
            result = self.get_surrounding_text()
            self._debug(f"SURROUNDING raw={result!r}")
            if result is not None:
                text, cursor_pos, anchor_pos = result
                surrounding_value = text.get_text() if text is not None else ""
                surrounding_cursor = cursor_pos
                have_surrounding = True
                self._debug(
                    f"SURROUNDING text={surrounding_value!r} "
                    f"cursor={cursor_pos} anchor={anchor_pos}"
                )
        except Exception as exc:
            self._debug(f"SURROUNDING QUERY ERROR {exc!r}")

        if not have_surrounding and self.surrounding_text:
            surrounding_value = self.surrounding_text
            surrounding_cursor = self.surrounding_cursor
            have_surrounding = True
            self._debug(
                f"SURROUNDING cached text={surrounding_value!r} "
                f"cursor={surrounding_cursor} anchor={self.surrounding_anchor}"
            )

        query_usable = bool(
            have_surrounding and surrounding_value and surrounding_cursor > 0
        )
        if query_usable:
            # Never delete past the start of the reported buffer.
            count = max(1, min(count, surrounding_cursor))
            self._debug(f"REPLACE ACTION delete_surrounding_text({-count}, {count})")
            try:
                self.delete_surrounding_text(-count, count)
            except Exception as exc:
                self._debug(f"DELETE ERROR {exc!r}")
            self._debug(f"REPLACE ACTION commit({replacement!r})")
            self._commit(replacement)
            self._debug("REPLACE END via surrounding-text")
            return

        self._debug(
            f"REPLACE ACTION fallback {count}x BackSpace "
            f"keycode={self.backspace_keycode}"
        )
        for _ in range(count):
            self.forward_key_event(IBus.KEY_BackSpace, self.backspace_keycode, 0)
            self.forward_key_event(
                IBus.KEY_BackSpace,
                self.backspace_keycode,
                IBus.ModifierType.RELEASE_MASK,
            )
        # Commit is delayed so the client processes the BackSpaces first;
        # see COMMIT_DELAY_MS, plus a bit more per extra deleted char.
        self._schedule_commit(replacement, 50 * (count - 1))
        self._debug("REPLACE END via BackSpace (commit scheduled)")

    def _build_lookup_table(self):
        table = IBus.LookupTable.new(len(self.candidates), 0, True, True)
        table.set_orientation(IBus.Orientation.HORIZONTAL)
        for index, candidate in enumerate(self.candidates):
            table.append_candidate(IBus.Text.new_from_string(candidate))
            table.append_label(IBus.Text.new_from_string(str(index + 1)))
        table.set_cursor_pos(self.selected)
        return table

    def open_popup(self):
        self._debug(
            f"TIMER FIRED char={self.pending_char!r} keyval={self.pending_keyval!r}"
        )
        self.timer_id = 0
        if self.pending_char not in ACCENTS:
            return GLib.SOURCE_REMOVE
        self.candidates = ACCENTS[self.pending_char]
        self.selected = 0
        self.popup_active = True
        self.lookup_table = self._build_lookup_table()
        self.update_lookup_table(self.lookup_table, True)
        self.show_lookup_table()
        self._debug(
            f"LOOKUP SHOW candidates={self.candidates!r} selected={self._selection_repr()}"
        )
        return GLib.SOURCE_REMOVE

    def choose_candidate(self, index, source="keyboard"):
        self._debug(
            f"CHOOSE source={source} index={index} popup_active={self.popup_active} "
            f"pending={self.pending_char!r} selected={self._selection_repr()}"
        )
        if not self.popup_active or not (0 <= index < len(self.candidates)):
            self._debug("CHOOSE IGNORED invalid state/index")
            return
        replacement = self.candidates[index]
        # Capture how many plain chars the client inserted (initial forward
        # plus leaked repeats) before the state is cleared.
        count = 1 + self.repeat_extra
        # Hide the panel first so no stale candidates remain visible while
        # the replacement is delivered to the application.
        self.close_popup()
        self._clear_state()
        self.replace_immediate_char(replacement, count)

    def do_candidate_clicked(self, index, button, state):
        self._debug(
            f"CANDIDATE CLICK index={index} button={button} state=0x{int(state):x}"
        )
        # The native panel reports mouse selection here without moving input
        # focus, so replacement acts on the original input context.
        self.choose_candidate(index, "mouse")

    def move_selection(self, delta, source="keyboard"):
        if not self.popup_active or not self.candidates:
            return
        if self.lookup_table is None:
            return
        self.selected = (self.selected + delta) % len(self.candidates)
        self.lookup_table.set_cursor_pos(self.selected)
        self.update_lookup_table(self.lookup_table, True)
        self._debug(
            f"SELECT source={source} delta={delta} index={self.selected} "
            f"candidate={self._selection_repr()}"
        )

    def do_cursor_up(self):
        self.move_selection(-1, "panel")

    def do_cursor_down(self):
        self.move_selection(1, "panel")

    def do_page_up(self):
        self.move_selection(-1, "panel-page")

    def do_page_down(self):
        self.move_selection(1, "panel-page")

    @staticmethod
    def _number_index(keyval):
        if IBus.KEY_1 <= keyval <= IBus.KEY_9:
            return keyval - IBus.KEY_1
        kp_1 = getattr(IBus, "KEY_KP_1", None)
        kp_9 = getattr(IBus, "KEY_KP_9", None)
        if kp_1 is not None and kp_9 is not None and kp_1 <= keyval <= kp_9:
            return keyval - kp_1
        # Shifted US digits, so selection still works when Shift is held
        # from an uppercase long-press (Shift+1 produces KEY_exclam, ...).
        shifted = {
            getattr(IBus, "KEY_exclam", None): 0,
            getattr(IBus, "KEY_at", None): 1,
            getattr(IBus, "KEY_numbersign", None): 2,
            getattr(IBus, "KEY_dollar", None): 3,
            getattr(IBus, "KEY_percent", None): 4,
            getattr(IBus, "KEY_asciicircum", None): 5,
            getattr(IBus, "KEY_ampersand", None): 6,
            getattr(IBus, "KEY_asterisk", None): 7,
            getattr(IBus, "KEY_parenleft", None): 8,
        }
        return shifted.get(keyval)

    def _is_selection_control(self, keyval):
        if self._number_index(keyval) is not None:
            return True
        return keyval in (
            IBus.KEY_Left,
            IBus.KEY_Right,
            IBus.KEY_Up,
            IBus.KEY_Down,
            IBus.KEY_Return,
            IBus.KEY_KP_Enter,
            IBus.KEY_space,
            IBus.KEY_Escape,
        )

    def do_process_key_event(self, keyval, keycode, state):
        release = bool(state & IBus.ModifierType.RELEASE_MASK)
        self._log_event(keyval, keycode, state, release)
        shortcut_mask = (
            IBus.ModifierType.CONTROL_MASK
            | IBus.ModifierType.MOD1_MASK
            | IBus.ModifierType.SUPER_MASK
        )
        if state & shortcut_mask:
            # Shortcuts pass through untouched; a natively inserted plain
            # character (if any) stays as is.
            self.cancel_all()
            return False

        if self.popup_active:
            # Autorepeat sends more PRESS events for the original held key.
            # Never let those restart the timer or insert more letters. The
            # original PRESS was forwarded to the client, so its RELEASE is
            # forwarded as well; only the synthetic repeats are swallowed.
            if keyval == self.pending_keyval:
                if release:
                    self._debug(
                        f"HELD RELEASE forwarded keyval={keyval}; "
                        "popup remains active"
                    )
                    return False
                self._debug(f"REPEAT SWALLOWED keyval={keyval}")
                self.repeat_extra += 1
                return True
            if release:
                # Swallow releases of keys consumed on PRESS; pass through
                # anything else so the client sees its release.
                if self._is_selection_control(keyval):
                    return True
                return False
            index = self._number_index(keyval)
            if index is not None:
                if index < len(self.candidates):
                    self.choose_candidate(index, "number")
                else:
                    self._debug(
                        f"DIGIT OUT OF RANGE index={index} "
                        f"n={len(self.candidates)}"
                    )
                return True
            if keyval in (IBus.KEY_Left, IBus.KEY_Up):
                self.move_selection(-1)
                return True
            if keyval in (IBus.KEY_Right, IBus.KEY_Down):
                self.move_selection(1)
                return True
            if keyval in (IBus.KEY_Return, IBus.KEY_KP_Enter, IBus.KEY_space):
                self.choose_candidate(self.selected, "confirm")
                return True
            if keyval == IBus.KEY_Escape:
                # The original plain letter was inserted natively: keep it.
                self._debug("ESCAPE keep original")
                self.close_popup()
                self._clear_state()
                return True
            # Any other key keeps the original letter and closes the popup,
            # then behaves like a fresh press (hold detection for
            # accentable keys, engine repeat otherwise).
            self._debug("OTHER KEY keep original, pass through")
            self.close_popup()
            self._clear_state()
            return self._handle_fresh_press(keyval, keycode, state)

        if release:
            if self.pending_keyval is not None and keyval == self.pending_keyval:
                # Quick tap: the character was inserted natively on PRESS
                # (forwarded, not committed by the engine), so just drop the
                # pending hold state and let the RELEASE through.
                self._debug(
                    f"RELEASE quick-tap keep native char={self.pending_char!r}"
                )
                self._clear_state()
                return False
            self._cancel_key_repeat()
            return False

        return self._handle_fresh_press(keyval, keycode, state)

    def _handle_fresh_press(self, keyval, keycode, state):
        self._cancel_key_repeat()
        char = IBus.keyval_to_unicode(keyval) or ""
        if isinstance(char, int):
            char = chr(char) if char else ""
        if keyval == IBus.KEY_BackSpace:
            # Remember the real hardware keycode so the replacement
            # fallback can forward a BackSpace the client honors.
            self.backspace_keycode = keycode
        if char in ACCENTS:
            if self.pending_keyval == keyval:
                self._debug(f"PENDING REPEAT SWALLOWED keyval={keyval}")
                self.repeat_extra += 1
                return True
            if self.pending_char is not None:
                # A previous pending hold never resolved; its character was
                # inserted natively, so just stop tracking it.
                self._clear_state()
            # FORWARD-FIRST PATH: return False so the application inserts the
            # character natively right now (zero latency, works even where
            # commit_text would never land, e.g. browser-based consoles).
            # The engine only arms hold detection; replacement happens later
            # via delete_surrounding_text/BackSpace + commit. Accentable
            # keys never engine-repeat: hold shows the picker instead.
            # (Compositor repeat is off engine-wide, so exactly one char
            # lands.)
            self.pending_char = char
            self.pending_keyval = keyval
            self.timer_id = GLib.timeout_add(HOLD_MS, self.open_popup)
            self._debug(
                f"TIMER START id={self.timer_id} char={char!r} "
                f"keyval={keyval} keycode={keycode} state=0x{int(state):x} "
                "forwarded=True"
            )
            return False
        if self.pending_char is not None:
            self._clear_state()
        # Ordinary key: the client inserts it natively (return False) and
        # the engine repeats it while held (compositor repeat is off).
        self._start_key_repeat(keyval, keycode, state)
        return False

    def do_destroy(self):
        self.cancel_all()
        self._restore_repeat()
        if Gio is not None:
            try:
                Gio.Settings.sync()
            except Exception:
                pass
        super().do_destroy()


class AccentHoldFactory(IBus.Factory):
    def __init__(self, bus):
        super().__init__(connection=bus.get_connection(), object_path="/org/freedesktop/IBus/Factory")
        self.bus = bus
        self.counter = 0

    def do_create_engine(self, engine_name):
        if engine_name != ENGINE_NAME:
            return None
        self.counter += 1
        return AccentHoldEngine(
            self.bus.get_connection(),
            f"/org/freedesktop/IBus/AccentHold/Engine{self.counter}",
        )


def main():
    IBus.init()
    bus = IBus.Bus()
    if not bus.is_connected():
        raise RuntimeError("Cannot connect to IBus")
    factory = AccentHoldFactory(bus)
    bus.request_name(BUS_NAME, 0)
    print("Accent Hold v1.0.0 ready (native IBus candidate UI)", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
