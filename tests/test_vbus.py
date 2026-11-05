# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The VBUS debounce (firmware/app/vbus.c) on the host: a pulled cable is
reported once, VBUS_MS after VBUS went away (the device then starts a new
run); its re-plug, shorter dips, bounces while plugging and VBUS absent or
present from boot report nothing. Built with the host tool
firmware/app/host/vbus_tool.c; needs a host C compiler (gcc or cc); skipped
without one."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
CC = shutil.which("gcc") or shutil.which("cc")
SOURCES = ["app/host/vbus_tool.c", "app/vbus.c"]

with open(os.path.join(FW, "app", "vbus.h")) as f:
    VBUS_MS = int(re.search(r"#define VBUS_MS (\d+)u", f.read()).group(1))


def build(directory, fw):
    exe = os.path.join(directory, "vbus_tool")
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.join(fw, "app")]
    cmd += ["-o", exe] + [os.path.join(fw, s) for s in SOURCES]
    subprocess.run(cmd, check=True)
    return exe


def levels(segments, start=0, step=1):
    """Polls every step ms from start: segments is a list of (ms, level)."""
    polls, t = [], start
    for ms, level in segments:
        for _ in range(0, ms, step):
            polls.append(((t & 0xFFFFFFFF), level))
            t += step
    return polls


@unittest.skipUnless(CC, "no host C compiler")
class Vbus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build(cls.tmp.name, FW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def events(self, polls):
        text = "".join(f"{ms} {level}\n" for ms, level in polls)
        out = subprocess.run([self.exe], input=text, capture_output=True, text=True, check=True).stdout
        return [(line.split()[0], int(line.split()[1])) for line in out.splitlines()]

    def test_debounce_is_in_range(self):
        # Long enough for a plug's bounce, short enough to show at once
        self.assertGreaterEqual(VBUS_MS, 20)
        self.assertLessEqual(VBUS_MS, 50)

    def test_pull_reports_once(self):
        polls = levels([(1000, 1), (2000, 0)])
        self.assertEqual(self.events(polls), [("gone", 1000 + VBUS_MS)])

    def test_replug_reports_nothing(self):
        polls = levels([(1000, 1), (1000, 0), (2000, 1)])
        self.assertEqual(self.events(polls), [("gone", 1000 + VBUS_MS)])

    def test_absent_from_boot_reports_nothing(self):
        self.assertEqual(self.events(levels([(3000, 0)])), [])

    def test_present_from_boot_reports_nothing(self):
        # Boot with the cable in: the boot's own run, no second one
        self.assertEqual(self.events(levels([(3000, 1)])), [])

    def test_first_plug_after_boot_reports_nothing(self):
        # Absent at boot, then plugged in: no disconnect came before it
        self.assertEqual(self.events(levels([(500, 0), (2000, 1)])), [])

    def test_short_dips_report_nothing(self):
        segments = [(1000, 1)] + [(VBUS_MS - 1, 0), (5, 1)] * 20 + [(1000, 1)]
        self.assertEqual(self.events(levels(segments)), [])

    def test_short_returns_report_nothing(self):
        # Unplugged, with blips of VBUS shorter than VBUS_MS: no re-plug
        segments = [(1000, 1), (1000, 0)] + [(VBUS_MS - 1, 1), (5, 0)] * 20 + [(1000, 0)]
        self.assertEqual(self.events(levels(segments)), [("gone", 1000 + VBUS_MS)])

    def test_plug_bounce_then_pull(self):
        # Absent at boot, bouncing on plugging, then present; pulled at t
        segments = [(500, 0)] + [(3, 1), (4, 0)] * 10 + [(1000, 1)]
        t = sum(ms for ms, _ in segments)
        polls = levels(segments + [(1000, 0)])
        self.assertEqual(self.events(polls), [("gone", t + VBUS_MS)])

    def test_pull_bounce(self):
        # The contacts bounce as the plug leaves: counted from the last bounce
        segments = [(1000, 1)] + [(4, 0), (3, 1)] * 5 + [(1000, 0)]
        t = 1000 + 7 * 5
        self.assertEqual(self.events(levels(segments)), [("gone", t + VBUS_MS)])

    def test_replug_bounce(self):
        # The re-plug bounces, then the cable is pulled again: one report
        # per pull, counted from the end of the re-plug's bounce
        segments = [(1000, 1), (1000, 0)] + [(3, 1), (4, 0)] * 5 + [(1000, 1), (1000, 0)]
        t = 2000 + 7 * 5 + 1000
        self.assertEqual(self.events(levels(segments)), [("gone", 1000 + VBUS_MS), ("gone", t + VBUS_MS)])

    def test_pull_twice(self):
        polls = levels([(1000, 1), (1000, 0), (1000, 1), (1000, 0)])
        self.assertEqual(self.events(polls), [("gone", 1000 + VBUS_MS), ("gone", 3000 + VBUS_MS)])

    def test_board_millis_wraps(self):
        # Pulled 20 ms before board_millis() wraps: reported after it
        start = (1 << 32) - 1000 - 20
        polls = levels([(1000, 1), (1000, 0)], start=start)
        self.assertEqual(self.events(polls), [("gone", (start + 1000 + VBUS_MS) & 0xFFFFFFFF)])

    def test_slow_main_loop(self):
        # A main loop that polls only every 7 ms still reports once, at the
        # first poll VBUS_MS after it saw VBUS gone
        polls = levels([(1001, 1), (1001, 0), (1001, 1)], step=7)
        seen = next(ms for ms, level in polls if level == 0)
        gone = next(ms for ms, level in polls if level == 0 and ms - seen >= VBUS_MS)
        self.assertEqual(self.events(polls), [("gone", gone)])


if __name__ == "__main__":
    unittest.main()
