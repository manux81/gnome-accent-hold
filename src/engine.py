#!/usr/bin/python3

import gi

gi.require_version("IBus", "1.0")

from gi.repository import IBus, GLib


BUS_NAME = "org.gnome.AccentHold"

# Tempo necessario per attivare il popup.
# Poi potremo renderlo configurabile.
HOLD_DELAY_MS = 400


class AccentHoldEngine(IBus.Engine):

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

    def __init__(self, connection, object_path):
        super().__init__(
            connection=connection,
            object_path=object_path,
        )

        self.pending_key = None
        self.pending_keycode = None

        self.hold_timer_id = None
        self.hold_triggered = False

        self.lookup_table = None
        self.candidates = []
        self.popup_active = False

        print("AccentHoldEngine created", flush=True)

    # ---------------------------------------------------------
    # Basic utilities
    # ---------------------------------------------------------

    def commit(self, text):
        print(f"COMMIT {text!r}", flush=True)

        self.commit_text(
            IBus.Text.new_from_string(text)
        )

    def cancel_timer(self):
        if self.hold_timer_id is not None:
            GLib.source_remove(self.hold_timer_id)
            self.hold_timer_id = None

    def reset_pending(self):
        self.cancel_timer()

        self.pending_key = None
        self.pending_keycode = None
        self.hold_triggered = False

    def hide_candidates(self):
        if self.popup_active:
            self.hide_lookup_table()

        self.lookup_table = None
        self.candidates = []
        self.popup_active = False

    # ---------------------------------------------------------
    # Long press timer
    # ---------------------------------------------------------

    def start_hold_timer(self):
        self.cancel_timer()

        self.hold_timer_id = GLib.timeout_add(
            HOLD_DELAY_MS,
            self.on_hold_timeout,
        )

    def on_hold_timeout(self):
        self.hold_timer_id = None

        if self.pending_key is None:
            return False

        self.hold_triggered = True

        print(
            f"HOLD TRIGGERED {self.pending_key!r}",
            flush=True,
        )

        self.show_candidates(self.pending_key)

        # False = timer one-shot
        return False

    # ---------------------------------------------------------
    # Candidate popup
    # ---------------------------------------------------------

    def show_candidates(self, letter):
        self.candidates = self.ACCENTS[letter]

        table = IBus.LookupTable.new(
            9,
            0,
            True,
            True,
        )

        table.set_orientation(
            IBus.Orientation.HORIZONTAL
        )

        for candidate in self.candidates:
            table.append_candidate(
                IBus.Text.new_from_string(candidate)
            )

        self.lookup_table = table
        self.popup_active = True

        print(
            f"SHOW {letter!r}: {self.candidates}",
            flush=True,
        )

        self.update_lookup_table(
            table,
            True,
        )

    def commit_candidate(self, index):
        if not self.popup_active:
            return

        if not 0 <= index < len(self.candidates):
            return

        candidate = self.candidates[index]

        print(
            f"SELECT {index}: {candidate!r}",
            flush=True,
        )

        self.commit(candidate)

        self.hide_candidates()
        self.reset_pending()

    def commit_current_candidate(self):
        if (
            not self.popup_active
            or self.lookup_table is None
        ):
            return

        self.commit_candidate(
            self.lookup_table.get_cursor_pos()
        )

    # ---------------------------------------------------------
    # Navigation
    # ---------------------------------------------------------

    def move_left(self):
        if self.lookup_table is None:
            return

        self.lookup_table.cursor_up()

        self.update_lookup_table(
            self.lookup_table,
            True,
        )

    def move_right(self):
        if self.lookup_table is None:
            return

        self.lookup_table.cursor_down()

        self.update_lookup_table(
            self.lookup_table,
            True,
        )

    # ---------------------------------------------------------
    # Keyboard
    # ---------------------------------------------------------

    def do_process_key_event(
        self,
        keyval,
        keycode,
        state,
    ):
        name = IBus.keyval_name(keyval)

        release = bool(
            state & IBus.ModifierType.RELEASE_MASK
        )

        # Never steal application/desktop shortcuts. Shift is deliberately
        # excluded because it is used for uppercase accented characters.
        shortcut_mask = (
            IBus.ModifierType.CONTROL_MASK
            | IBus.ModifierType.MOD1_MASK
            | IBus.ModifierType.SUPER_MASK
        )

        if state & shortcut_mask:
            self.cancel_timer()
            if self.popup_active:
                self.hide_candidates()
            self.reset_pending()
            return False


        # =====================================================
        # RELEASE
        # =====================================================

        if release:

            # Release della vocale che ha iniziato tutto.
            if (
                self.pending_key is not None
                and keycode == self.pending_keycode
            ):
                self.cancel_timer()

                # TAP:
                # rilasciata prima dello scadere del timer.
                if not self.hold_triggered:
                    key = self.pending_key

                    self.pending_key = None
                    self.pending_keycode = None

                    self.commit(key)

                # HOLD:
                # NON chiudiamo il popup.
                # La vocale rimane pending per identificare
                # eventuali repeat residui.
                return True

            # Release dei controlli del popup.
            if self.popup_active:
                if (
                    name in (
                        "Left",
                        "Right",
                        "Return",
                        "KP_Enter",
                        "Escape",
                    )
                    or name in "123456789"
                ):
                    return True

            return False

        # =====================================================
        # POPUP ATTIVO
        # =====================================================

        if self.popup_active:

            # IMPORTANTISSIMO:
            # assorbe tutti i repeat della vocale originale.
            if (
                self.pending_key is not None
                and name == self.pending_key
                and keycode == self.pending_keycode
            ):
                return True

            if name in "123456789":
                self.commit_candidate(
                    int(name) - 1
                )
                return True

            if name == "Left":
                self.move_left()
                return True

            if name == "Right":
                self.move_right()
                return True

            if name in ("Return", "KP_Enter"):
                self.commit_current_candidate()
                return True

            if name == "Escape":
                print("CANCEL", flush=True)

                self.hide_candidates()
                self.reset_pending()

                return True

            # Altro carattere:
            # annulla popup e lascia passare il nuovo tasto.
            self.hide_candidates()
            self.reset_pending()

            return False

        # =====================================================
        # VOCALE
        # =====================================================

        if name in self.ACCENTS:

            # Prima pressione reale.
            if self.pending_key is None:
                self.pending_key = name
                self.pending_keycode = keycode
                self.hold_triggered = False

                self.start_hold_timer()

                return True

            # Autorepeat prima che scada il timer.
            #
            # Deve essere ASSORBITO.
            # Non deve diventare testo e non deve
            # influenzare il rilevamento del long press.
            if (
                name == self.pending_key
                and keycode == self.pending_keycode
            ):
                return True

        # =====================================================
        # ALTRO TASTO MENTRE UNA VOCALE È PENDING
        # =====================================================

        if self.pending_key is not None:

            self.cancel_timer()

            # Se non siamo ancora entrati nel long press,
            # la vocale era semplicemente un tap.
            if not self.hold_triggered:
                key = self.pending_key

                self.pending_key = None
                self.pending_keycode = None

                self.commit(key)

            else:
                self.hide_candidates()

            self.reset_pending()

        return False

    # ---------------------------------------------------------
    # Mouse candidate
    # ---------------------------------------------------------

    def do_candidate_clicked(
        self,
        index,
        button,
        state,
    ):
        self.commit_candidate(index)

    # ---------------------------------------------------------
    # Reset
    # ---------------------------------------------------------

    def do_reset(self):
        self.hide_candidates()
        self.reset_pending()

    def do_focus_out(self):
        self.hide_candidates()
        self.reset_pending()


class AccentHoldFactory(IBus.Factory):

    def __init__(self, bus):
        super().__init__(
            connection=bus.get_connection(),
            object_path="/org/freedesktop/IBus/Factory",
        )

        self.bus = bus
        self.counter = 0

    def do_create_engine(self, engine_name):
        self.counter += 1

        object_path = (
            "/org/freedesktop/IBus/AccentHold/"
            f"Engine{self.counter}"
        )

        print(
            f"Creating {engine_name}: {object_path}",
            flush=True,
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

    bus.request_name(
        BUS_NAME,
        0,
    )

    print(
        "Accent Hold v0.4 ready",
        flush=True,
    )

    loop = GLib.MainLoop()

    try:
        loop.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
