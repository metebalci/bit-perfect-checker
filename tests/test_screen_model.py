# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The status screen's model (firmware/app/screen_model.c) and the LEDs
(firmware/app/leds.c) on the host: the band rule and the LED table for every
USB state, stream state and set of the run's flags, the small screen's file
and version texts, and the large screen's detail rows. Built with the host tool firmware/app/host/screen_tool.c;
needs a host C compiler (gcc or cc); skipped without one."""

import itertools
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
CC = shutil.which("gcc") or shutil.which("cc")
SOURCES = ["app/host/screen_tool.c", "app/screen_model.c", "app/leds.c", "bpc1/bpc1_checker.c", "bpc1/bpc1.c"]
COLS = 30
# The firmware version's longest usual form (cmake/version.cmake)
VERSION = "v1.2.0-6b94cda-dirty"

# bpc1_verdict_t and bpc1_end_t
UNKNOWN_SIGNAL, BIT_PERFECT, NOT_BIT_PERFECT, OVERRUN = range(4)
END_LAST, END_STOP = 1, 4

RED, GREEN, BLUE = 0xFF0000, 0x00FF00, 0x0000FF


# The run's highest verdict: the first held, in the LEDs' order
ORDER = ("NOT_BIT_PERFECT", "UNKNOWN_SIGNAL", "OVERRUN", "BIT_PERFECT")
FLAG = {"NOT_BIT_PERFECT": "fail", "UNKNOWN_SIGNAL": "unknown", "OVERRUN": "overrun", "BIT_PERFECT": "pass"}
# The LEDs for each (firmware/README.md, LEDs): (red, green)
LEDS = {"NOT_BIT_PERFECT": ("on", "off"), "UNKNOWN_SIGNAL": ("blink 500", "off"),
        "OVERRUN": ("blink 125", "off"), "BIT_PERFECT": ("off", "on"), None: ("off", "off")}
BLUE_LED = {"streaming": "on", "mounted": "blink 500", "not mounted": "blink 125", "full speed": "blink 100"}
# The blue LED at Full-Speed, as (lit, ms) runs from t = 0: a double blink,
# two flashes of 100 ms 150 ms apart, then 850 ms dark, every 1.2 s
DOUBLE_BLINK = [(1, 100), (0, 150), (1, 100), (0, 850)]


def centre(text, width):
    """text centred in width columns, the odd space on the right"""
    return (" " * ((width - len(text)) // 2) + text).ljust(width)


def build(directory, fw=FW):
    exe = os.path.join(directory, "screen_tool")
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror"]
    cmd += ["-I", os.path.join(fw, "include"), "-I", os.path.join(fw, "app"), "-I", os.path.join(fw, "bpc1")]
    cmd += ["-o", exe] + [os.path.join(fw, s) for s in SOURCES]
    subprocess.run(cmd, check=True)
    return exe


def verdict(st):
    return next((v for v in ORDER if st[FLAG[v]]), None)


HOURGLASS_NONE, HOURGLASS_BLINK, HOURGLASS_STEADY = range(3)

# The band's lines while the device is set up at Full-Speed, where it offers
# no audio: a tall band, always red
FULL_SPEED_WORD = "NEEDS|HIGH-SPEED|USB PORT"


def expected_band(st):
    """The band rule: (word, colour, hourglass with packets moving, without).
    The word says what is going on; the frame's colour is the run's state:
    red once anything failed, green while every result is BIT_PERFECT, else
    blue. The hourglass blinks beside WAITING, and beside CHECKING while
    packets arrive, steady when they stop. At Full-Speed the device offers
    no audio, so it waits for nothing: FULL_SPEED_WORD in three lines of a
    tall band, red whatever the run, without an hourglass. Returns (word,
    colour, hourglass, tall)."""
    if not st["streaming"] and st["usb"] != 0 and not st["hs"]:
        return FULL_SPEED_WORD, RED, (HOURGLASS_NONE, HOURGLASS_NONE), 1
    if st["streaming"]:
        word, hourglass = "CHECKING", (HOURGLASS_BLINK, HOURGLASS_STEADY)
    elif st["usb"] != 0:
        word, hourglass = "WAITING", (HOURGLASS_BLINK, HOURGLASS_BLINK)
    else:
        word, hourglass = "NOT CONNECTED", (HOURGLASS_NONE, HOURGLASS_NONE)
    failed = st["fail"] or st["unknown"] or st["overrun"]
    colour = RED if failed else GREEN if st["pass"] else BLUE
    return word, colour, hourglass, 0


def expected_labels(st):
    """One line of 20 columns: UNKNOWN left, OVERRUN right, each a column in."""
    left = " UNKNOWN" if st["unknown"] else ""
    right = "OVERRUN " if st["overrun"] else ""
    return left + " " * (20 - len(left) - len(right)) + right


@unittest.skipUnless(CC, "no host C compiler")
class ScreenModel(unittest.TestCase):
    fw = FW
    exe = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        if cls.exe is None:
            cls.exe = build(cls.tmp.name, cls.fw)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, lines):
        return subprocess.run([self.exe], input="\n".join(lines) + "\n", capture_output=True, text=True,
                              check=True).stdout.splitlines()

    def screens(self, statuses, fw=VERSION):
        """[(band, rows, leds)] for each status, a dict of status fields."""
        q = []
        for st in statuses:
            q.append("status " + " ".join(f"{k}={v}" for k, v in st.items()))
            q += [f"model {fw}", "leds"]
        out = self.run_tool(q)
        per = 1 + 4 + 9 + 4
        self.assertEqual(len(out), per * len(statuses))
        res = []
        for i in range(len(statuses)):
            o = out[i * per:(i + 1) * per]
            _, state, colour, moving, stalled, tall, word = o[0].split(" ", 6)
            band = {"state": int(state), "colour": int(colour, 16), "hourglass": (int(moving), int(stalled)),
                    "tall": int(tall), "word": word}
            texts = []
            for r, tag in zip(o[1:5], ("labels|", "file|", "file|", "fw|")):
                self.assertTrue(r.startswith(tag) and r.endswith("|"), r)
                texts.append(r[len(tag):-1])
            band["labels"], band["file"], band["fw"] = texts[0], texts[1:3], texts[3]
            rows = []
            for r in o[5:14]:
                self.assertTrue(r.startswith("row|") and r.endswith("|"), r)
                rows.append(r[4:-1])
            leds = {}
            for line in o[14:17]:
                _, name, pattern = line.split(" ", 2)
                leds[name] = pattern
            lit, apart, apart_red = (int(w.split("=")[1]) for w in o[17].split()[1:])
            band["lit"], band["apart_from_blue"], band["blink_apart_from_red"] = lit, apart, apart_red
            res.append((band, rows, leds))
        return res

    def screen(self, **st):
        return self.screens([st])[0]

    @staticmethod
    def all_statuses():
        """Every USB state at both speeds, streaming or not, and all 16 sets
        of the run's flags."""
        return [{"usb": usb, "hs": hs, "streaming": streaming, "pass": flags & 1, "fail": flags >> 1 & 1,
                 "unknown": flags >> 2 & 1, "overrun": flags >> 3 & 1}
                for usb, hs, streaming, flags in itertools.product((0, 1, 2), (0, 1), (0, 1), range(16))]

    @staticmethod
    def full_speed(st):
        """Set up at Full-Speed, where the device offers no audio"""
        return not st["streaming"] and st["usb"] != 0 and not st["hs"]

    def test_leds(self):
        """leds.c for every combination: blue shows the stream and the USB
        state, red and green the run's highest verdict; at Full-Speed blue
        double-blinks and green and red stay off."""
        statuses = self.all_statuses()
        for st, (_, _, leds) in zip(statuses, self.screens(statuses)):
            with self.subTest(**st):
                if self.full_speed(st):
                    state = "full speed"
                else:
                    state = "streaming" if st["streaming"] else "mounted" if st["usb"] else "not mounted"
                self.assertEqual(leds["blue"], BLUE_LED[state])
                want = LEDS[None] if self.full_speed(st) else LEDS[verdict(st)]
                self.assertEqual((leds["red"], leds["green"]), want)

    def test_double_blink(self):
        """At Full-Speed, mounted or suspended, whatever the run: the blue
        LED's double blink, run by run over 2.4 s from t = 0; the slow blink
        (500 ms dark, 500 ms lit) and the fast one (125 ms each) keep theirs."""
        cases = [({"usb": usb, "hs": 0, "fail": fail}, DOUBLE_BLINK * 2) for usb in (1, 2) for fail in (0, 1)]
        cases += [({"usb": 1, "hs": 1}, [(0, 500), (1, 500)] * 2 + [(0, 400)]),
                  ({"usb": 0, "hs": 0}, [(0, 125), (1, 125)] * 9 + [(0, 125), (1, 25)])]
        for st, want in cases:
            with self.subTest(**st):
                out = self.run_tool(["status " + " ".join(f"{k}={v}" for k, v in st.items()), "trace blue 2400"])
                self.assertEqual(out[0].split()[:2], ["trace", "blue"])
                runs = [tuple(int(v) for v in r.split(":")) for r in out[0].split()[2:]]
                self.assertEqual(runs, want)

    def test_band(self):
        """The band and the labels for every combination of USB state,
        stream, packets moving or not, and the run's flags; the hourglass
        blinks on the LEDs' 500 ms phase."""
        statuses = self.all_statuses()
        seen = set()
        for st, (band, _, leds) in zip(statuses, self.screens(statuses)):
            with self.subTest(**st):
                word, colour, hourglass, tall = expected_band(st)
                self.assertEqual((band["word"], band["colour"], band["hourglass"], band["tall"]),
                                 (word, colour, hourglass, tall))
                self.assertEqual(band["labels"], expected_labels(st))
                # the screen's blink is the red LED's slow blink (UNKNOWN_SIGNAL)
                if st["unknown"] and not st["fail"] and word != FULL_SPEED_WORD:
                    self.assertEqual(band["blink_apart_from_red"], 0)
                # the hourglass, packets moving: lit half the time; in WAITING
                # on the blue LED's phase, lit together with it
                self.assertEqual(band["lit"], 0 if word in ("NOT CONNECTED", FULL_SPEED_WORD) else 1000)
                if word == "WAITING":
                    self.assertEqual(band["apart_from_blue"], 0)
                # the frame's colour is the run's as the LEDs show it; at
                # Full-Speed red, with green and red off
                if word == FULL_SPEED_WORD:
                    self.assertEqual((leds["red"], leds["green"]), ("off", "off"))
                else:
                    self.assertEqual(colour == RED, leds["red"] != "off")
                    self.assertEqual(colour == GREEN, leds["green"] == "on")
                seen.add((word, colour))
        # three words in three colours, and Full-Speed's in red
        self.assertEqual(len(seen), 10)

    def test_file_and_version(self):
        """The small screen's texts: the file as its header gives it, its kind
        as it arrives over depth/rate in kHz or the DSD rate, centred in 15
        columns (32/705.6 is the widest, 8); while a stream is open the locked file, else the last
        result's if it belongs to the run that runs now (blank after a run start); the version
        centred in 30."""
        pcm, dsd, sweep = 1, 2, 5
        cases = [
            ({"streaming": 1, "file_kind": pcm, "file_rate": 96000, "file_depth": 24}, ("PCM", "24/96")),
            ({"streaming": 1, "file_kind": pcm, "file_rate": 705600, "file_depth": 32}, ("PCM", "32/705.6")),
            ({"streaming": 1, "file_kind": pcm, "file_rate": 768000, "file_depth": 32}, ("PCM", "32/768")),
            ({"streaming": 1, "file_kind": dsd, "file_rate": 5644800, "file_depth": 1}, ("DoP", "DSD128")),
            ({"streaming": 1, "file_kind": dsd, "file_rate": 11289600, "file_depth": 1}, ("DoP", "DSD256")),
            ({"streaming": 1, "file_kind": sweep, "file_rate": 44100, "file_depth": 16}, ("PCM SWEEP", "16/44.1")),
            # open, not locked yet: nothing, not the last result's file
            ({"streaming": 1, "last": 1, "last_kind": pcm, "last_rate": 48000, "last_depth": 16}, ("", "")),
            # no stream: the last result's file, if it belongs to the run
            ({"last": 1, "last_in_run": 1, "last_kind": dsd, "last_rate": 2822400, "last_depth": 1,
              "file_kind": pcm, "file_rate": 48000, "file_depth": 16}, ("DoP", "DSD64")),
            ({"last": 1, "last_in_run": 1, "last_kind": pcm, "last_rate": 48000, "last_depth": 16}, ("PCM", "16/48")),
            # a run started since (B1, a tap, mark, a pulled cable): nothing
            ({"last": 1, "last_in_run": 0, "last_kind": pcm, "last_rate": 48000, "last_depth": 16}, ("", "")),
            ({"last": 1, "last_in_run": 0, "last_kind": dsd, "last_rate": 2822400, "last_depth": 1}, ("", "")),
            # the last result was UNKNOWN_SIGNAL (no file), or there is none
            ({"last": 1, "last_in_run": 1, "last_kind": 0}, ("", "")),
            ({}, ("", "")),
        ]
        statuses = [dict(usb=1, hs=1, **st) for st, _ in cases]
        for (st, want), (band, _, _) in zip(cases, self.screens(statuses)):
            with self.subTest(**st):
                self.assertEqual(band["file"], [centre(w, 15) for w in want])
                self.assertEqual(band["fw"], centre(VERSION, 30))
        # A longer hash, should git ever use one: cut at the 30th column
        band, _, _ = self.screens([{"usb": 1}], fw=f"v1.2.0-{'0123456789abcdef' * 2}-dirty")[0]
        self.assertEqual(band["fw"], "v1.2.0-0123456789abcdef0123456")

    def test_rows_44k1_and_705k6(self):
        _, rows, _ = self.screen(usb=1, hs=1, streaming=1, id=12, rate=44100, alt=1, slot=2, res=16, bits=16)
        self.assertEqual(rows[0], "stream 12".ljust(22) + "usb high")
        self.assertEqual(rows[1], "44.1 kHz 16/2 PCM bits 16".ljust(COLS))
        self.assertEqual(rows[2], "lost 0  ovf 0   results_lost 0")
        _, rows, _ = self.screen(usb=1, hs=1, streaming=1, id=13, rate=705600, alt=2, slot=4, res=24, bits=24, lost=4,
                                 ovf=1)
        self.assertEqual(rows[0], "stream 13".ljust(22) + "usb high")
        self.assertEqual(rows[1], "705.6 kHz 24/4 PCM bits 24".ljust(COLS))
        self.assertEqual(rows[2], "lost 4  ovf 1   results_lost 0")

    def test_dop_and_dsd_names(self):
        """DoP at the three BPC1 DSD rates names the DSD rate; DoP at another
        rate, or PCM at a DSD rate, does not."""
        cases = [(176400, True, "DSD64"), (352800, True, "DSD128"), (705600, True, "DSD256"),
                 (192000, True, None), (88200, True, None), (705600, False, None)]
        statuses = [{"usb": 1, "hs": 1, "streaming": 1, "id": 3, "rate": r, "alt": 3, "slot": 4, "res": 32, "bits": 24,
                     "dop": int(d)} for r, d, _ in cases]
        for (rate, dop, name), (_, rows, _) in zip(cases, self.screens(statuses)):
            with self.subTest(rate=rate, dop=dop):
                self.assertEqual(rows[0], ("stream 3 " + name if name else "stream 3").ljust(22) + "usb high")
                khz = f"{rate / 1000:g}"
                self.assertEqual(rows[1], f"{khz} kHz 32/4 {'DoP' if dop else 'PCM'} bits 24".ljust(COLS))
        # No alt setting open, or DoP not measured yet: no DSD name
        _, rows, _ = self.screen(usb=1, hs=1, id=3, rate=705600, alt=0, bits=24, dop=1)
        self.assertEqual(rows[0], "stream 3".ljust(22) + "usb high")
        _, rows, _ = self.screen(usb=1, hs=1, streaming=1, id=3, rate=705600, alt=3, slot=4, res=32, dop=1)
        self.assertEqual(rows[0], "stream 3".ljust(22) + "usb high")

    def test_slot_formats(self):
        for res, slot in ((16, 2), (24, 4), (32, 4), (24, 3)):
            with self.subTest(res=res, slot=slot):
                _, rows, _ = self.screen(usb=1, hs=1, streaming=1, rate=96000, alt=1, slot=slot, res=res, bits=res)
                self.assertEqual(rows[1], f"96 kHz {res}/{slot} PCM bits {res}".ljust(COLS))
        # bits not measured yet: no transport
        _, rows, _ = self.screen(usb=1, hs=1, streaming=1, rate=768000, alt=3, slot=4, res=32)
        self.assertEqual(rows[1], "768 kHz 32/4".ljust(COLS))

    def test_settings_without_an_alt_setting(self):
        _, rows, _ = self.screen(usb=0, allow_silence=1, rate=48000)
        self.assertEqual(rows[0], "usb not connected".rjust(COLS))
        # The version does not fit beside the setting: cut at the 30th column
        self.assertEqual(rows[1], "allow_silence true v1.2.0-6b94")
        _, rows, _ = self.screen(usb=2, hs=1)
        self.assertEqual(rows[0], "usb suspended".rjust(COLS))
        self.assertEqual(rows[1], "allow_silence false v1.2.0-6b9")
        # A shorter right-hand part than the version's is right-aligned
        _, rows, _ = self.screens([{"usb": 1, "hs": 1}], fw="1.2.0")[0]
        self.assertEqual(rows[1], "allow_silence false".ljust(25) + "1.2.0")
        _, rows, _ = self.screen(usb=1, hs=0)
        self.assertEqual(rows[0], "usb full".rjust(COLS))

    def test_run_counts_and_last(self):
        _, rows, _ = self.screen(usb=1, hs=1, count1=17, count2=1, count0=2, count3=3, results_lost=5, last=1,
                                 last_in_run=1, last_verdict=NOT_BIT_PERFECT, last_end=END_STOP, last_lost=3, last_ovf=0,
                                 last_diff="2.048")
        self.assertEqual(rows[2], "lost 0  ovf 0   results_lost 5")
        self.assertEqual(rows[3:7], ["BIT-PERFECT".ljust(28) + "17", "NOT BIT-PERFECT".ljust(29) + "1",
                                     "UNKNOWN SIGNAL".ljust(29) + "2", "OVERRUN".ljust(29) + "3"])
        self.assertEqual(rows[7], "last NOT BIT-PERFECT end STOP ")
        self.assertEqual(rows[8], "lost 3  ovf 0  diff 2.048".ljust(COLS))
        _, rows, _ = self.screen(usb=1, hs=1, last=1, last_in_run=1, last_verdict=BIT_PERFECT, last_end=END_LAST,
                                 last_lost=-2, last_diff="-")
        self.assertEqual(rows[7], "last BIT-PERFECT end LAST".ljust(COLS))
        self.assertEqual(rows[8], "lost -2  ovf 0  diff -".ljust(COLS))
        # The last result is from the run before (a run started since): blank
        _, rows, _ = self.screen(usb=1, hs=1, last=1, last_in_run=0, last_verdict=BIT_PERFECT, last_end=END_LAST,
                                 last_lost=-2, last_diff="-")
        self.assertEqual(rows[7:9], [" " * COLS, " " * COLS])
        # The file with a difference comes before the last result
        _, rows, _ = self.screen(usb=1, hs=1, streaming=1, fail=1, last=1, last_verdict=BIT_PERFECT,
                                 last_end=END_LAST, last_diff="-", file_diff="123.456")
        self.assertEqual(rows[7], "file NOT BIT-PERFECT".ljust(COLS))
        self.assertEqual(rows[8], "diff 123.456".ljust(COLS))
        # No result yet
        _, rows, _ = self.screen(usb=1, hs=1)
        self.assertEqual(rows[7:9], [" " * COLS, " " * COLS])

    def test_30_column_cut(self):
        big = 4294967295
        band, rows, _ = self.screen(usb=2, hs=1, streaming=1, id=big, dop=1, rate=705600, alt=4, slot=3, res=24, bits=24,
                                    lost=big, ovf=big, results_lost=big, count1=big, count2=big, count0=big,
                                    count3=big, last=1, last_in_run=1, last_verdict=OVERRUN, last_end=END_STOP,
                                    last_lost=-2147483648, last_ovf=big, last_diff="1234.567", fail=1)
        for r in rows:
            self.assertEqual(len(r), COLS, r)
        self.assertEqual((band["word"], band["colour"]), ("CHECKING", RED))
        self.assertEqual(rows[0], "stream 4294967295 DSD256 usb s")
        self.assertEqual(rows[2], "lost 4294967295  ovf 429496729")
        self.assertEqual(rows[3], "BIT-PERFECT".ljust(20) + "4294967295")
        self.assertEqual(rows[4], "NOT BIT-PERFECT     4294967295")
        self.assertEqual(rows[7], "last OVERRUN end STOP".ljust(COLS))
        self.assertEqual(rows[8], "lost -2147483648  ovf 42949672")
        # The shortest version form: cut at the 30th column
        _, rows, _ = self.screens([{"usb": 1, "hs": 1}], fw="v1.2.0-unknown")[0]
        self.assertEqual(rows[1], "allow_silence false v1.2.0-unk")


if __name__ == "__main__":
    unittest.main()
