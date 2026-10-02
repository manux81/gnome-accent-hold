#!/usr/bin/env python3
"""macOS-style press-and-hold accents implemented as an IBus engine."""

import os
import time

import gi
gi.require_version("IBus", "1.0")
from gi.repository import GLib, IBus

BUS_NAME = "org.gnome.AccentHold"
ENGINE_NAME = "accent-hold"
HOLD_MS = 400
LOG_PATH = os.environ.get("ACCENT_HOLD_LOG", "/tmp/accent-hold.log")

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
            ("SURROUNDING_TEXT", 16),
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

    def _clear_state(self):
        self.cancel_timer()
        self.pending_char = None
        self.pending_keyval = None
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
        self._clear_state()

    def _commit(self, text):
        self.commit_text(IBus.Text.new_from_string(text))

    def replace_immediate_char(self, replacement):
        # The plain character was inserted natively by the application (the
        # engine forwarded the original PRESS). Replace it in place: prefer
        # surrounding-text deletion when the client exposes useful
        # surrounding text, else fall back to a synthetic BackSpace forwarded
        # through IBus (covers terminals and other clients without
        # surrounding-text support). Focus stays in the application because
        # the candidate UI is the native IBus panel, so both paths act on the
        # correct input context.
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

        if have_surrounding and surrounding_value and surrounding_cursor > 0:
            self._debug("REPLACE ACTION delete_surrounding_text(-1, 1)")
            try:
                self.delete_surrounding_text(-1, 1)
            except Exception as exc:
                self._debug(f"DELETE ERROR {exc!r}")
            self._debug(f"REPLACE ACTION commit({replacement!r})")
            self._commit(replacement)
            self._debug("REPLACE END via surrounding-text")
            return

        self._debug("REPLACE ACTION fallback BackSpace")
        # keycode 0 lets the client interpret the event via keyval; a
        # hardcoded X11 keycode is not valid on Wayland.
        self.forward_key_event(IBus.KEY_BackSpace, 0, 0)
        self.forward_key_event(
            IBus.KEY_BackSpace,
            0,
            IBus.ModifierType.RELEASE_MASK,
        )
        self._debug(f"REPLACE ACTION commit({replacement!r})")
        self._commit(replacement)
        self._debug("REPLACE END via BackSpace")

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
        # Hide the panel first so no stale candidates remain visible while
        # the replacement is delivered to the application.
        self.close_popup()
        self._clear_state()
        self.replace_immediate_char(replacement)

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
            # Any other key keeps the original letter and closes the popup;
            # return False so the key itself inserts normally.
            self._debug("OTHER KEY keep original, pass through")
            self.close_popup()
            self._clear_state()
            return False

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
            return False

        char = IBus.keyval_to_unicode(keyval) or ""
        if isinstance(char, int):
            char = chr(char) if char else ""
        if char in ACCENTS:
            if self.pending_keyval == keyval:
                self._debug(f"PENDING REPEAT SWALLOWED keyval={keyval}")
                return True
            if self.pending_char is not None:
                # A previous pending hold never resolved; its character was
                # inserted natively, so just stop tracking it.
                self._clear_state()
            # FORWARD-FIRST PATH: return False so the application inserts the
            # character natively right now (zero latency, works even where
            # commit_text would never land, e.g. browser-based consoles).
            # The engine only arms hold detection; replacement happens later
            # via delete_surrounding_text/BackSpace + commit.
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
        return False

    def do_destroy(self):
        self.cancel_all()
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
