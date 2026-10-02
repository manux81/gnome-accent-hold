"""State-machine tests which do not require PyGObject on the test host.

Semantics under test (Wayland-safe architecture):

* Normal typing is forwarded to the client (return False): the application
  inserts characters natively, so IME-unaware surfaces such as
  browser-based server consoles keep receiving every keystroke.
* The engine only observes supported letters to arm hold detection.
* Holding a supported letter publishes a native IBus lookup table; the
  engine creates no Gtk top-level surface.
* Choosing a candidate deletes the natively inserted plain character via
  surrounding-text deletion (or a forwarded BackSpace fallback) and commits
  the replacement.
* Escape / other keys keep the natively inserted character with no commit
  from the engine.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


class ModifierType:
    RELEASE_MASK = 1 << 30
    CONTROL_MASK = 1 << 2
    MOD1_MASK = 1 << 3
    SUPER_MASK = 1 << 26


class Text:
    def __init__(self, value):
        self.value = value

    @classmethod
    def new_from_string(cls, value):
        return cls(value)

    def get_text(self):
        return self.value


class LookupTable:
    def __init__(self):
        self.candidates = []
        self.labels = []
        self.cursor = 0

    @classmethod
    def new(cls, *_args):
        return cls()

    def set_orientation(self, _orientation):
        pass

    def append_candidate(self, text):
        self.candidates.append(text.value)

    def append_label(self, text):
        self.labels.append(text.value)

    def set_cursor_pos(self, cursor):
        self.cursor = cursor


class Engine:
    def __init__(self, **_kwargs):
        self.commits = []
        self.forwards = []
        self.deletes = []
        self.lookup_visible = False

    def commit_text(self, text):
        self.commits.append(text.value)

    def update_lookup_table(self, table, visible):
        self.table = table
        self.lookup_visible = visible

    def show_lookup_table(self):
        self.lookup_visible = True

    def hide_lookup_table(self):
        self.lookup_visible = False

    def delete_surrounding_text(self, offset, nchars):
        self.deletes.append((offset, nchars))

    def forward_key_event(self, keyval, keycode, state):
        self.forwards.append((keyval, keycode, state))

    def get_surrounding_text(self):
        return getattr(self, "_surrounding", None)

    def set_property(self, *_args):
        raise TypeError("construct-only property in stub")

    def do_destroy(self):
        pass


class Factory:
    def __init__(self, **_kwargs):
        pass


class FakeIBus(types.ModuleType):
    Engine = Engine
    Factory = Factory
    Text = Text
    LookupTable = LookupTable
    ModifierType = ModifierType
    Orientation = types.SimpleNamespace(HORIZONTAL=0)
    KEY_1 = ord("1")
    KEY_9 = ord("9")
    KEY_KP_1 = 0xFFB1
    KEY_KP_2 = 0xFFB2
    KEY_KP_3 = 0xFFB3
    KEY_KP_9 = 0xFFB9
    KEY_exclam = ord("!")
    KEY_at = ord("@")
    KEY_numbersign = ord("#")
    KEY_dollar = ord("$")
    KEY_percent = ord("%")
    KEY_asciicircum = ord("^")
    KEY_ampersand = ord("&")
    KEY_asterisk = ord("*")
    KEY_parenleft = ord("(")
    KEY_Left = 0xFF51
    KEY_Up = 0xFF52
    KEY_Right = 0xFF53
    KEY_Down = 0xFF54
    KEY_Return = 0xFF0D
    KEY_KP_Enter = 0xFF8D
    KEY_space = ord(" ")
    KEY_Escape = 0xFF1B
    KEY_BackSpace = 0xFF08
    KEY_Shift_L = 0xFFE1
    KEY_Shift_R = 0xFFE2
    KEY_Control_L = 0xFFE3
    KEY_Control_R = 0xFFE4
    KEY_Alt_L = 0xFFE9
    KEY_Super_L = 0xFFEB
    KEY_Caps_Lock = 0xFFE5

    @staticmethod
    def keyval_to_unicode(keyval):
        return chr(keyval) if 0 <= keyval <= 0x10FFFF else ""

    @staticmethod
    def keyval_name(keyval):
        return str(keyval)


class FakeGLib(types.ModuleType):
    SOURCE_REMOVE = False
    next_timer = 1
    pending = {}

    @classmethod
    def timeout_add(cls, milliseconds, callback, *args):
        timer = cls.next_timer
        cls.next_timer += 1
        cls.pending[timer] = (milliseconds, callback, args)
        return timer

    @classmethod
    def source_remove(cls, timer):
        cls.pending.pop(timer, None)
        return True

    @classmethod
    def fire(cls, timer):
        _milliseconds, callback, args = cls.pending.pop(timer)
        return callback(*args)


gi = types.ModuleType("gi")
gi.require_version = lambda *_args: None
repository = types.ModuleType("gi.repository")
repository.IBus = FakeIBus("IBus")
repository.GLib = FakeGLib("GLib")
gi.repository = repository
sys.modules.setdefault("gi", gi)
sys.modules.setdefault("gi.repository", repository)

path = pathlib.Path(__file__).parents[1] / "src" / "engine.py"
spec = importlib.util.spec_from_file_location("accent_hold_engine", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = module.AccentHoldEngine(None, "/test")
        self.engine._debug = lambda _message: None
        # By default expose surrounding text so replacement uses deletion.
        text = Text("xa")
        self.engine._surrounding = (text, 2, 2)

    def press(self, keyval, state=0):
        return self.engine.do_process_key_event(keyval, 38, state)

    def release(self, keyval):
        return self.press(keyval, ModifierType.RELEASE_MASK)

    def hold_a(self):
        # Forward-first: the PRESS reaches the client (False) while the
        # engine arms hold detection.
        self.assertFalse(self.press(ord("a")))
        self.engine.open_popup()

    def test_accentable_letters_are_forwarded_not_swallowed(self):
        # Regression test for IME-unaware surfaces (e.g. browser-based
        # server consoles): every accentable letter must reach the client
        # natively, with no engine-side commit that could be dropped.
        for char in "aeiouscdlnrtzAEIOUS":
            self.setUp()
            self.assertFalse(
                self.press(ord(char)), f"letter {char!r} must pass through"
            )
            self.assertEqual([], self.engine.commits)
            self.assertFalse(self.release(ord(char)))
            self.assertEqual([], self.engine.commits)
            self.assertIsNone(self.engine.pending_char)

    def test_quick_press_leaves_insertion_to_client(self):
        self.assertFalse(self.press(ord("a")))
        self.assertEqual([], self.engine.commits)
        self.assertFalse(self.release(ord("a")))
        self.assertEqual([], self.engine.commits)
        self.assertIsNone(self.engine.pending_char)

    def test_hold_repeat_press_is_swallowed_but_release_passes(self):
        self.assertFalse(self.press(ord("a")))
        # Autorepeat while held: swallowed so the client inserts only once.
        self.assertTrue(self.press(ord("a")))
        self.assertEqual(1, self.engine.repeat_extra)
        self.assertEqual([], self.engine.commits)
        self.assertFalse(self.release(ord("a")))
        self.assertIsNone(self.engine.pending_char)

    def test_number_row_and_keypad_choose_same_commit_path(self):
        for keyval in (ord("2"), FakeIBus.KEY_KP_2):
            self.setUp()
            self.hold_a()
            self.assertTrue(self.press(keyval))
            # Only the replacement commit comes from the engine; the plain
            # "a" was inserted natively by the client.
            self.assertEqual(["á"], self.engine.commits)
            self.assertEqual([(-1, 1)], self.engine.deletes)
            self.assertFalse(self.engine.lookup_visible)

    def test_shifted_digit_selects_while_shift_held(self):
        self.hold_a()
        self.assertTrue(self.press(FakeIBus.KEY_at))
        self.assertEqual(["á"], self.engine.commits)

    def test_lookup_table_uses_native_panel(self):
        self.assertFalse(self.press(ord("a")))
        self.engine.open_popup()
        self.assertTrue(self.engine.lookup_visible)
        self.assertEqual(
            ["à", "á", "â", "ä", "ǎ", "æ", "ã", "å", "ā"],
            self.engine.table.candidates,
        )
        self.assertEqual(
            [str(i) for i in range(1, 10)], self.engine.table.labels
        )

    def test_arrows_enter_escape_and_mouse(self):
        self.hold_a()
        self.press(FakeIBus.KEY_Right)
        self.press(FakeIBus.KEY_Return)
        self.assertEqual(["á"], self.engine.commits)

        self.setUp()
        self.hold_a()
        self.engine.do_candidate_clicked(2, 1, 0)
        self.assertEqual(["â"], self.engine.commits)

        self.setUp()
        self.hold_a()
        self.press(FakeIBus.KEY_Escape)
        # Escape keeps the natively inserted original: no engine commit.
        self.assertEqual([], self.engine.commits)
        self.assertFalse(self.engine.lookup_visible)

    def test_other_key_keeps_original_and_passes_through(self):
        self.hold_a()
        self.assertFalse(self.press(ord("b")))
        self.assertEqual([], self.engine.commits)
        self.assertFalse(self.engine.lookup_visible)

    def test_held_release_during_popup_is_forwarded(self):
        self.hold_a()
        # The original PRESS was forwarded, so the RELEASE goes through too
        # while the popup stays up for the actual selection.
        self.assertFalse(self.release(ord("a")))
        self.assertTrue(self.engine.popup_active)

    def test_press_after_choice_starts_a_fresh_forwarded_hold(self):
        self.hold_a()
        self.press(ord("1"))
        self.assertEqual(["à"], self.engine.commits)
        # Popup is closed: pressing "a" is a new keystroke for the client.
        self.assertFalse(self.press(ord("a")))
        self.assertEqual(["à"], self.engine.commits)
        self.assertFalse(self.release(ord("a")))
        self.assertEqual(["à"], self.engine.commits)

    def run_commit(self):
        timer = self.engine.commit_timer_id
        self.assertNotEqual(0, timer, "expected a scheduled commit")
        FakeGLib.fire(timer)

    def test_backspace_fallback_without_surrounding_text(self):
        self.engine._surrounding = None
        self.engine.surrounding_text = ""
        self.hold_a()
        self.assertTrue(self.press(ord("1")))
        # Wayland-safe fallback carries a real hardware keycode (evdev 14
        # by default): keycode 0 is ignored by Wayland clients.
        self.assertIn(
            (FakeIBus.KEY_BackSpace, 14, 0), self.engine.forwards
        )
        # The commit waits for the BackSpace round-trip (COMMIT_DELAY_MS).
        self.assertEqual([], self.engine.commits)
        self.run_commit()
        self.assertEqual(["à"], self.engine.commits)

    def test_observed_backspace_keycode_is_reused(self):
        self.engine._surrounding = None
        self.engine.surrounding_text = ""
        self.press(FakeIBus.KEY_BackSpace)
        self.hold_a()
        self.assertTrue(self.press(ord("1")))
        presses = [f for f in self.engine.forwards if f[0] == FakeIBus.KEY_BackSpace]
        self.assertTrue(presses)
        self.assertEqual(38, presses[0][1])
        self.assertEqual([], self.engine.commits)
        self.run_commit()
        self.assertEqual(["à"], self.engine.commits)

    def test_empty_surrounding_query_uses_backspace_fallback(self):
        # Even with the SURROUNDING_TEXT capability bit set, an empty query
        # means the client may ignore deletions (observed "aà"), so the
        # engine uses the BackSpace fallback with a real keycode.
        self.engine._surrounding = None
        self.engine.surrounding_text = ""
        self.engine.do_set_capabilities(0x29)
        self.hold_a()
        self.assertTrue(self.press(ord("1")))
        self.assertEqual([], self.engine.deletes)
        self.assertIn(
            (FakeIBus.KEY_BackSpace, 14, 0), self.engine.forwards
        )
        self.assertEqual([], self.engine.commits)
        self.run_commit()
        self.assertEqual(["à"], self.engine.commits)

    def test_pending_commit_cancelled_by_cancel_all(self):
        self.engine._surrounding = None
        self.engine.surrounding_text = ""
        self.hold_a()
        self.assertTrue(self.press(ord("1")))
        timer = self.engine.commit_timer_id
        self.assertNotEqual(0, timer)
        self.engine.cancel_all()
        self.assertEqual(0, self.engine.commit_timer_id)
        self.assertNotIn(timer, FakeGLib.pending)
        self.assertEqual([], self.engine.commits)

    def test_capabilities_are_recorded(self):
        self.engine.do_set_capabilities(0x1F)
        self.assertTrue(self.engine.caps_known)
        self.assertEqual(0x1F, self.engine.capabilities)

    def test_shortcuts_pass_through(self):
        self.assertFalse(self.press(ord("c"), ModifierType.CONTROL_MASK))
        self.assertEqual([], self.engine.commits)

    def test_leaked_repeats_are_deleted_on_replace(self):
        # Wayland inserts swallowed repeats anyway: choosing must delete
        # the initial char plus every leaked repeat (BackSpace path).
        self.engine._surrounding = None
        self.engine.surrounding_text = ""
        self.hold_a()
        self.assertTrue(self.press(ord("a")))
        self.assertTrue(self.press(ord("a")))
        self.assertTrue(self.press(ord("2")))
        presses = [f for f in self.engine.forwards if f[0] == FakeIBus.KEY_BackSpace]
        self.assertEqual(6, len(presses))
        self.assertEqual([], self.engine.commits)
        self.run_commit()
        self.assertEqual(["á"], self.engine.commits)

    def test_delete_path_clamped_to_reported_buffer(self):
        # Query reports ("xa", cursor=2) but 1+2 repeats leaked: delete is
        # clamped to the 2 chars actually present.
        self.hold_a()
        self.assertTrue(self.press(ord("a")))
        self.assertTrue(self.press(ord("a")))
        self.assertTrue(self.press(ord("2")))
        self.assertEqual([(-2, 2)], self.engine.deletes)
        self.assertEqual(["á"], self.engine.commits)

    def test_repeat_suppressed_while_hold_armed_and_restored(self):
        class FakeSettings:
            repeat = True

            @classmethod
            def new(cls, _schema):
                return cls()

            def get_boolean(self, _key):
                return FakeSettings.repeat

            def set_boolean(self, _key, value):
                FakeSettings.repeat = value

            @staticmethod
            def sync():
                pass

        class FakeGio:
            Settings = FakeSettings

        self.engine._keyboard_settings = staticmethod(
            lambda: FakeSettings.new("org.gnome.desktop.peripherals.keyboard")
        )
        module.Gio = FakeGio
        try:
            FakeSettings.repeat = True
            # Suppression is engine-lifetime scoped, (re)asserted on focus.
            self.engine.do_focus_in()
            self.assertTrue(FakeSettings.repeat is False)
            # Ordinary keystrokes never toggle it back and forth.
            self.assertFalse(self.press(ord("a")))
            self.assertTrue(FakeSettings.repeat is False)
            self.assertFalse(self.release(ord("a")))
            self.assertTrue(FakeSettings.repeat is False)
            # Hold path: suppression survives until engine teardown.
            self.hold_a()
            self.assertTrue(FakeSettings.repeat is False)
            self.assertTrue(self.press(ord("1")))
            self.assertEqual(["à"], self.engine.commits)
            self.assertTrue(FakeSettings.repeat is False)
            self.engine.do_destroy()
            self.assertTrue(FakeSettings.repeat is True)
        finally:
            module.Gio = None
            FakeSettings.repeat = True

    def test_repeat_restore_is_noop_without_gio(self):
        module.Gio = None
        self.assertFalse(self.press(ord("a")))
        self.assertFalse(self.engine._repeat_suppressed)
        self.assertFalse(self.release(ord("a")))

    def test_repeat_suppressed_immediately_on_press(self):
        # Hard requirement: a held accentable key yields exactly one char,
        # so suppression is already in place before any keystroke (asserted
        # on focus) and keystrokes never toggle it. Writes are async.
        class FakeSettings:
            repeat = True
            syncs = 0

            @classmethod
            def new(cls, _schema):
                return cls()

            def get_boolean(self, _key):
                return FakeSettings.repeat

            def set_boolean(self, _key, value):
                FakeSettings.repeat = value

            @staticmethod
            def sync():
                FakeSettings.syncs += 1

        class FakeGio:
            Settings = FakeSettings

        self.engine._keyboard_settings = staticmethod(
            lambda: FakeSettings.new("org.gnome.desktop.peripherals.keyboard")
        )
        module.Gio = FakeGio
        try:
            FakeSettings.repeat = True
            FakeSettings.syncs = 0
            self.engine.do_focus_in()
            self.assertTrue(FakeSettings.repeat is False)
            self.assertFalse(self.press(ord("a")))
            self.assertTrue(FakeSettings.repeat is False)
            self.assertEqual(0, FakeSettings.syncs)
            self.assertFalse(self.release(ord("a")))
            self.assertTrue(FakeSettings.repeat is False)
            self.assertEqual(0, FakeSettings.syncs)
        finally:
            module.Gio = None
            FakeSettings.repeat = True

    def test_repeat_restore_respects_user_disabled_repeat(self):
        class FakeSettings:
            repeat = False

            @classmethod
            def new(cls, _schema):
                return cls()

            def get_boolean(self, _key):
                return FakeSettings.repeat

            def set_boolean(self, _key, value):
                FakeSettings.repeat = value

        class FakeGio:
            Settings = FakeSettings

        self.engine._keyboard_settings = staticmethod(
            lambda: FakeSettings.new("org.gnome.desktop.peripherals.keyboard")
        )
        module.Gio = FakeGio
        try:
            self.assertFalse(self.press(ord("a")))
            self.assertFalse(self.release(ord("a")))
            self.assertTrue(FakeSettings.repeat is False)
        finally:
            module.Gio = None

    def test_accentable_keys_never_engine_repeat(self):
        self.assertFalse(self.press(ord("a")))
        self.assertEqual(0, self.engine.keyrepeat_timer_id)
        self.engine.open_popup()
        self.assertEqual(0, self.engine.keyrepeat_timer_id)
        self.assertFalse(self.release(ord("a")))

    def test_plain_key_engine_repeats_while_held(self):
        self.assertFalse(self.press(ord("b")))
        timer = self.engine.keyrepeat_timer_id
        self.assertNotEqual(0, timer)
        FakeGLib.fire(timer)
        self.assertEqual([(ord("b"), 38, 0)], self.engine.forwards)
        # Rearmed for the next interval.
        self.assertNotEqual(0, self.engine.keyrepeat_timer_id)
        self.assertFalse(self.release(ord("b")))
        self.assertEqual(0, self.engine.keyrepeat_timer_id)

    def test_shortcuts_and_modifiers_do_not_repeat(self):
        self.assertFalse(self.press(ord("c"), ModifierType.CONTROL_MASK))
        self.assertEqual(0, self.engine.keyrepeat_timer_id)
        self.assertFalse(self.press(FakeIBus.KEY_Shift_L))
        self.assertEqual(0, self.engine.keyrepeat_timer_id)
        self.assertFalse(self.release(FakeIBus.KEY_Shift_L))

    def test_new_press_takes_over_repeat(self):
        self.assertFalse(self.press(ord("b")))
        first = self.engine.keyrepeat_timer_id
        self.assertNotEqual(0, first)
        self.assertFalse(self.press(ord("x")))
        self.assertNotIn(first, FakeGLib.pending)
        self.assertNotEqual(0, self.engine.keyrepeat_timer_id)
        self.assertFalse(self.release(ord("x")))
        self.assertEqual(0, self.engine.keyrepeat_timer_id)


if __name__ == "__main__":
    unittest.main()
