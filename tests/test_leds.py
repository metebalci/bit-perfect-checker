# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The LEDs (firmware/app/leds.c) on the host. The start-up LED test:
leds_test() lights red and returns at once, and leds_task() in the main
loop runs the rest (red, green, then blue, 0.3 s each, each logged and
shown on a display), so that the main loop, the USB stack's task included,
keeps running while it lasts; after it, the LEDs show the status, and a USB
fault lights all three. A board without blue shows blue on its display
only, and its test ends after green. The LED table for every USB state,
stream state and set of the run's flags, and the blue LED's double blink at
Full-Speed. Built with the host tool firmware/app/host/leds_tool.c; needs a
host C compiler (gcc or cc); skipped without one."""

import itertools
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
CC = shutil.which("gcc") or shutil.which("cc")
SOURCES = ["app/host/leds_tool.c", "app/leds.c"]
TEST_MS = 300
COLOURS = ("red", "green", "blue")

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


def build(directory, fw):
    exe = os.path.join(directory, "leds_tool")
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.join(fw, "app"),
           "-I", os.path.join(fw, "include"), "-I", os.path.join(fw, "app", "host", "stub")]
    cmd += ["-o", exe] + [os.path.join(fw, s) for s in SOURCES]
    subprocess.run(cmd, check=True)
    return exe


def status_leds(t, passed):
    """The LEDs after the test, not connected (leds.h): blue blinks fast,
    green shows a run of BIT_PERFECT results, red is off."""
    return (0, 1 if passed else 0, 1 if (t // 125) % 2 else 0)


def verdict(st):
    return next((v for v in ORDER if st[FLAG[v]]), None)


def full_speed(st):
    """Set up at Full-Speed, where the device offers no audio"""
    return not st["streaming"] and st["usb"] != 0 and not st["hs"]


def pattern(values):
    """One LED's values, one per ms from t = 0, as "off", "on" or "blink
    <longest lit run in ms>"."""
    if not any(values):
        return "off"
    if all(values):
        return "on"
    longest = max(len(list(run)) for lit, run in itertools.groupby(values) if lit)
    return f"blink {longest}"


def runs(values):
    """One LED's values as (lit, ms) runs."""
    return [(lit, len(list(run))) for lit, run in itertools.groupby(values)]


def states(events, end):
    """The LEDs at every ms in [0, end), from the tool's "leds" lines."""
    changes = {t: tuple(int(c) for c in rest) for t, kind, rest in events if kind == "leds"}
    out, cur = [], (0, 0, 0)
    for t in range(end):
        cur = changes.get(t, cur)
        out.append(cur)
    return out


@unittest.skipUnless(CC, "no host C compiler")
class LedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build(cls.tmp.name, FW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, commands):
        """(ms, kind, rest) per line; a leds_test() that waits on the clock
        never returns, and times out here."""
        out = subprocess.run([self.exe], input="".join(c + "\n" for c in commands), capture_output=True,
                             text=True, check=True, timeout=10).stdout
        events = []
        for line in out.splitlines():
            t, kind, rest = line.split(" ", 2)
            events.append((int(t), kind, rest))
        return events

    def lines(self, events, kind):
        return [(t, rest) for t, k, rest in events if k == kind]

    def test_returns_at_once_with_red_lit(self):
        # Nothing after leds_test() but red: the clock has not moved
        self.assertEqual(self.run_tool(["test"]), [(0, "display", "red"), (0, "log", "led red"),
                                                   (0, "leds", "100")])

    def test_each_led_for_its_time_then_the_status(self):
        events = self.run_tool(["pass", "test", "run 2000"])
        self.assertEqual(self.lines(events, "log"), [(i * TEST_MS, "led " + c) for i, c in enumerate(COLOURS)])
        self.assertEqual(self.lines(events, "display"), [(i * TEST_MS, c) for i, c in enumerate(COLOURS)])
        got = states(events, 2000)
        want = [(1, 0, 0)] * TEST_MS + [(0, 1, 0)] * TEST_MS + [(0, 0, 1)] * TEST_MS
        want += [status_leds(t, True) for t in range(3 * TEST_MS, 2000)]
        self.assertEqual(got, want)

    def test_board_without_blue(self):
        # Blue on the display as green ends, no line for it, and the test
        # ends there
        events = self.run_tool(["absent blue", "test", "run 2000"])
        self.assertEqual(self.lines(events, "log"), [(0, "led red"), (TEST_MS, "led green")])
        self.assertEqual(self.lines(events, "display"), [(0, "red"), (TEST_MS, "green"), (2 * TEST_MS, "blue")])
        got = states(events, 2000)
        want = [(1, 0, 0)] * TEST_MS + [(0, 1, 0)] * TEST_MS
        want += [(0, 0, 0)] * (2000 - 2 * TEST_MS)  # status_leds() without blue, no pass
        self.assertEqual(got, want)

    def test_slow_main_loop(self):
        # A pass every 7 ms: each LED goes at the first pass TEST_MS after
        # it lit, so the LEDs change at 301, 602 and 903 ms
        events = self.run_tool(["pass", "test", "run 2002 7"])
        self.assertEqual([t for t, _ in self.lines(events, "log")], [0, 301, 602])
        want, cur = [], None
        for t in range(2002):
            if t % 7 == 0:
                cur = (1, 0, 0) if t < 301 else (0, 1, 0) if t < 602 else (0, 0, 1) if t < 903 else \
                    status_leds(t, True)
            want.append(cur)
        self.assertEqual(states(events, 2002), want)

    def test_usb_fault(self):
        # A USB stack that did not start, or lost an event: all three steady,
        # whatever the run's results
        events = self.run_tool(["pass", "fault", "run 1000"])
        self.assertEqual(states(events, 1000), [(1, 1, 1)] * 1000)

    def leds_for(self, st, ms):
        """The LEDs over ms from t = 0 with the status st, without the test:
        {colour: [0 or 1 per ms]}."""
        events = self.run_tool(["status " + " ".join(f"{k}={v}" for k, v in st.items()), f"run {ms}"])
        got = states(events, ms)
        return {c: [s[i] for s in got] for i, c in enumerate(COLOURS)}

    def test_led_table(self):
        """Every USB state at both speeds, streaming or not, with all 16 sets
        of the run's flags: blue shows the stream and the USB state, red and
        green the run's highest verdict; at Full-Speed blue double-blinks and
        green and red stay off."""
        for usb, hs, streaming, flags in itertools.product((0, 1, 2), (0, 1), (0, 1), range(16)):
            st = {"usb": usb, "hs": hs, "streaming": streaming, "pass": flags & 1, "fail": flags >> 1 & 1,
                  "unknown": flags >> 2 & 1, "overrun": flags >> 3 & 1}
            with self.subTest(**st):
                leds = {c: pattern(v) for c, v in self.leds_for(st, 2000).items()}
                if full_speed(st):
                    state = "full speed"
                else:
                    state = "streaming" if st["streaming"] else "mounted" if st["usb"] else "not mounted"
                self.assertEqual(leds["blue"], BLUE_LED[state])
                want = LEDS[None] if full_speed(st) else LEDS[verdict(st)]
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
                self.assertEqual(runs(self.leds_for(st, 2400)["blue"]), want)

    def test_no_test_shows_the_status_at_once(self):
        events = self.run_tool(["run 1000"])
        self.assertEqual(self.lines(events, "log"), [])
        self.assertEqual(self.lines(events, "display"), [])
        self.assertEqual(states(events, 1000), [status_leds(t, False) for t in range(1000)])


if __name__ == "__main__":
    unittest.main()
