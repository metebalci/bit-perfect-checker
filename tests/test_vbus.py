# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The VBUS debounce (firmware/app/vbus.c) on the host. A pulled cable is
reported once, VBUS_MS after VBUS went away (the device then starts a new
run), and the D+ pull-up goes off with it; VBUS back for VBUS_MS turns the
pull-up on again. With VBUS at start the pull-up, which tusb_init() turned
on, is logged as connecting at once; without VBUS at start it goes off at
once, and the first plug turns it on without reporting a pull. Shorter dips and
bounces while plugging change nothing. A change of formats holds the
pull-up off for a while (vbus_hold()); it comes on again after that, once
VBUS is there, even after a dip too short to count, and only once. Built with the host tool
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


def hold(polls, t, ms):
    """polls with a change of formats at t, after that ms's poll: the pull-up off for ms."""
    i = next(k for k, (at, _) in enumerate(polls) if at == t)
    return polls[:i + 1] + [(t, f"hold {ms}")] + polls[i + 1:]


HOLD_MS = 300  # uac2.c's RECONNECT_MS

# A start with VBUS present: the pull-up is on (tusb_init()), and logged
BOOT_LOG = ["pull-up on", "usb connecting"]


def boot(start=0):
    return [("connect", start & 0xFFFFFFFF)]


def pull(ms):
    """What a pull detected at ms does: report it, then the pull-up off."""
    return [("gone", ms & 0xFFFFFFFF), ("disconnect", ms & 0xFFFFFFFF)]


@unittest.skipUnless(CC, "no host C compiler")
class Vbus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build(cls.tmp.name, FW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, lines):
        text = "".join(f"{ms} {word}\n" for ms, word in lines)
        return subprocess.run([self.exe], input=text, capture_output=True, text=True, check=True).stdout

    def events(self, polls):
        """The action flags: the first poll is vbus_start(), the rest vbus_poll()."""
        out = self.run_tool(polls)
        return [(line.split()[0], int(line.split()[1])) for line in out.splitlines() if not line.startswith("|")]

    def log(self, lines):
        """The lines vbus_act() and the stand-ins log, in order."""
        return [line[2:] for line in self.run_tool(lines).splitlines() if line.startswith("|")]

    def test_pull_during_a_stream_logs_in_order(self):
        # The cut file's lines, then usb disconnected, then the new run
        lines = [(0, 1), (100, "mount"), (200, "stream")] + levels([(1000, 1), (1000, 0)], start=300)
        self.assertEqual(self.log(lines), BOOT_LOG + ["usb mounted", "stream stop reason=reset",
                                                      "usb disconnected", "run start", "pull-up off"])

    def test_reset_before_vbus_gone_still_logs_disconnected(self):
        # A host that resets the bus as the plug leaves: the device is no
        # longer set up when VBUS goes, and the pull is still logged
        lines = [(0, 1), (100, "mount"), (200, "stream")] + levels([(1000, 1)], start=300)
        lines += [(1300, "reset")] + levels([(1000, 0)], start=1300)
        self.assertEqual(self.log(lines), BOOT_LOG + ["usb mounted", "stream stop reason=reset", "usb reset",
                                                      "usb disconnected", "run start", "pull-up off"])

    def test_pull_when_never_set_up_logs_disconnected(self):
        # VBUS from a charger or a host that never enumerated: still a pull
        lines = levels([(1000, 1), (1000, 0)])
        self.assertEqual(self.log(lines), BOOT_LOG + ["usb disconnected", "run start", "pull-up off"])

    def test_replug_logs_connecting(self):
        lines = levels([(1000, 1), (1000, 0), (1000, 1)])
        self.assertEqual(self.log(lines), BOOT_LOG + ["usb disconnected", "run start", "pull-up off",
                                                      "pull-up on", "usb connecting"])

    def test_boot_without_vbus_logs_nothing_until_plugged(self):
        lines = levels([(1000, 0), (1000, 1)])
        self.assertEqual(self.log(lines), ["pull-up off", "pull-up on", "usb connecting"])

    def test_debounce_is_in_range(self):
        # Long enough for a plug's bounce, short enough to show at once
        self.assertGreaterEqual(VBUS_MS, 20)
        self.assertLessEqual(VBUS_MS, 50)

    def test_present_from_boot_logs_the_attach(self):
        # Boot with the cable in: tusb_init() connected, boot started a run;
        # the attach is logged at once, and nothing follows
        self.assertEqual(self.events(levels([(3000, 1)])), boot())
        self.assertEqual(self.log(levels([(3000, 1)])), BOOT_LOG)

    def test_absent_from_boot_disconnects_at_once(self):
        self.assertEqual(self.events(levels([(3000, 0)])), [("disconnect", 0)])

    def test_unplugged_just_after_boot(self):
        # VBUS present at start but gone before it held VBUS_MS: the pull-up
        # goes off, and there was no connection to report a pull of
        self.assertEqual(self.events(levels([(VBUS_MS - 20, 1), (2000, 0)])),
                         boot() + [("disconnect", VBUS_MS - 20 + VBUS_MS)])

    def test_first_plug_after_boot_connects(self):
        # Absent at boot, then plugged in: no pull to report
        self.assertEqual(self.events(levels([(500, 0), (2000, 1)])),
                         [("disconnect", 0), ("connect", 500 + VBUS_MS)])

    def test_pull(self):
        self.assertEqual(self.events(levels([(1000, 1), (2000, 0)])), boot() + pull(1000 + VBUS_MS))

    def test_replug_connects(self):
        polls = levels([(1000, 1), (1000, 0), (2000, 1)])
        self.assertEqual(self.events(polls), boot() + pull(1000 + VBUS_MS) + [("connect", 2000 + VBUS_MS)])

    def test_short_dips_do_nothing(self):
        segments = [(1000, 1)] + [(VBUS_MS - 1, 0), (5, 1)] * 20 + [(1000, 1)]
        self.assertEqual(self.events(levels(segments)), boot())

    def test_short_returns_do_nothing(self):
        # Unplugged, with blips of VBUS shorter than VBUS_MS: no re-plug
        segments = [(1000, 1), (1000, 0)] + [(VBUS_MS - 1, 1), (5, 0)] * 20 + [(1000, 0)]
        self.assertEqual(self.events(levels(segments)), boot() + pull(1000 + VBUS_MS))

    def test_plug_bounce_then_pull(self):
        # Absent at boot, bouncing on plugging, then present; pulled at t
        bounce = [(3, 1), (4, 0)] * 10
        segments = [(500, 0)] + bounce + [(1000, 1)]
        plugged = 500 + 7 * 10
        t = sum(ms for ms, _ in segments)
        polls = levels(segments + [(1000, 0)])
        self.assertEqual(self.events(polls), [("disconnect", 0), ("connect", plugged + VBUS_MS)] + pull(t + VBUS_MS))

    def test_pull_bounce(self):
        # The contacts bounce as the plug leaves: counted from the last bounce
        segments = [(1000, 1)] + [(4, 0), (3, 1)] * 5 + [(1000, 0)]
        self.assertEqual(self.events(levels(segments)), boot() + pull(1000 + 7 * 5 + VBUS_MS))

    def test_replug_bounce_then_pull(self):
        segments = [(1000, 1), (1000, 0)] + [(3, 1), (4, 0)] * 5 + [(1000, 1), (1000, 0)]
        plugged = 2000 + 7 * 5
        self.assertEqual(self.events(levels(segments)),
                         boot() + pull(1000 + VBUS_MS) + [("connect", plugged + VBUS_MS)]
                         + pull(plugged + 1000 + VBUS_MS))

    def test_pull_twice(self):
        polls = levels([(1000, 1), (1000, 0), (1000, 1), (1000, 0)])
        self.assertEqual(self.events(polls),
                         boot() + pull(1000 + VBUS_MS) + [("connect", 2000 + VBUS_MS)] + pull(3000 + VBUS_MS))

    def test_board_millis_wraps(self):
        # Pulled 20 ms before board_millis() wraps: reported after it
        start = (1 << 32) - 1000 - 20
        polls = levels([(1000, 1), (1000, 0)], start=start)
        self.assertEqual(self.events(polls), boot(start) + pull(start + 1000 + VBUS_MS))

    def test_slow_main_loop(self):
        # A main loop that polls only every 7 ms still acts once on each
        # change, at the first poll VBUS_MS after it saw it
        polls = levels([(1001, 1), (1001, 0), (1001, 1)], step=7)
        seen = next(ms for ms, level in polls if level == 0)
        gone = next(ms for ms, level in polls if level == 0 and ms - seen >= VBUS_MS)
        back_seen = next(ms for ms, level in polls if level == 1 and ms > gone)
        back = next(ms for ms, level in polls if level == 1 and ms > gone and ms - back_seen >= VBUS_MS)
        self.assertEqual(self.events(polls), boot() + pull(gone) + [("connect", back)])

    def test_hold_reconnects(self):
        polls = hold(levels([(3000, 1)]), 1000, HOLD_MS)
        self.assertEqual(self.events(polls), boot() + [("disconnect", 1000), ("connect", 1000 + HOLD_MS)])
        self.assertEqual(self.log(polls), BOOT_LOG + ["pull-up off", "pull-up on", "usb connecting"])

    def test_hold_waits_through_a_short_dip(self):
        # VBUS dips, too briefly to count, as the hold ends: the pull-up comes
        # on when VBUS is back, not before and not never
        back = 1000 + HOLD_MS + 10
        polls = hold(levels([(1000 + HOLD_MS - 20, 1), (30, 0), (2000, 1)]), 1000, HOLD_MS)
        self.assertEqual(self.events(polls), boot() + [("disconnect", 1000), ("connect", back)])

    def test_hold_with_vbus_gone_does_nothing(self):
        # Formats changed with the cable out: the pull-up is off already and
        # comes on when VBUS is back, once
        polls = hold(levels([(1000, 1), (1000, 0), (2000, 1)]), 1500, HOLD_MS)
        self.assertEqual(self.events(polls), boot() + pull(1000 + VBUS_MS) + [("connect", 2000 + VBUS_MS)])
        self.assertEqual(self.log(polls)[len(BOOT_LOG):].count("usb connecting"), 1)

    def test_pull_during_a_hold(self):
        # Pulled while held: reported once, and VBUS back connects once, after
        # its debounce
        polls = hold(levels([(1100, 1), (1000, 0), (2000, 1)]), 1000, HOLD_MS)
        self.assertEqual(self.events(polls),
                         boot() + [("disconnect", 1000), ("gone", 1100 + VBUS_MS), ("connect", 2100 + VBUS_MS)])
        self.assertEqual(self.log(polls), BOOT_LOG + ["pull-up off", "usb disconnected", "run start", "pull-up on",
                                                      "usb connecting"])

    def test_pull_and_bouncing_replug_during_a_hold(self):
        # Out and back in within the hold: the pull-up waits for the debounce
        # and comes on once
        bounce = [(3, 1), (4, 0)] * 5
        segments = [(1050, 1), (100, 0)] + bounce + [(3000, 1)]
        plugged = 1050 + 100 + 7 * 5
        polls = hold(levels(segments), 1000, HOLD_MS)
        self.assertEqual(self.events(polls),
                         boot() + [("disconnect", 1000), ("gone", 1050 + VBUS_MS), ("connect", plugged + VBUS_MS)])

    def test_unplugged_at_the_hold_end_then_back(self):
        # VBUS goes as the hold ends and is still out when it would count:
        # a pull, and the pull-up stays off until VBUS is back
        out = 1000 + HOLD_MS - 5
        polls = hold(levels([(out, 1), (500, 0), (1000, 1)]), 1000, HOLD_MS)
        self.assertEqual(self.events(polls),
                         boot() + [("disconnect", 1000), ("gone", out + VBUS_MS), ("connect", out + 500 + VBUS_MS)])

    def test_second_hold_extends(self):
        polls = hold(hold(levels([(3000, 1)]), 1000, HOLD_MS), 1200, HOLD_MS)
        self.assertEqual(self.events(polls), boot() + [("disconnect", 1000), ("connect", 1200 + HOLD_MS)])

    def test_hold_across_the_wrap(self):
        start = (1 << 32) - 1000
        t = (start + 900) & 0xFFFFFFFF
        polls = hold(levels([(3000, 1)], start=start), t, HOLD_MS)
        self.assertEqual(self.events(polls),
                         boot(start) + [("disconnect", t), ("connect", (t + HOLD_MS) & 0xFFFFFFFF)])

    def test_hold_just_after_boot(self):
        # Within VBUS_MS of boot, before the level counts: still the hold's time
        polls = hold(levels([(3000, 1)]), 10, HOLD_MS)
        self.assertEqual(self.events(polls), boot() + [("disconnect", 10), ("connect", 10 + HOLD_MS)])


if __name__ == "__main__":
    unittest.main()
