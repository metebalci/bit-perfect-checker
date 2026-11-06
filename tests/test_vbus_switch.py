# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The BPC1 generator's VBUS switch (firmware/bpc1-generator/vbus_switch.c)
on the host. The switch goes on only while the port's VBUS input sees no
VBUS. VBUS already there (another host drives the cable) is a fault, logged
once: the switch stays off until the input has read no VBUS for 1 s without
a break, then goes on, however long that takes. Overcurrent while the switch
is on is a fault too: the switch stays off and is tried again 1 s later, at
most three times; after the last overcurrent it stays off until reset. Built
with the host tool firmware/bpc1-generator/host/vbus_switch_tool.c; needs a
host C compiler (gcc or cc); skipped without one."""

import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "firmware", "bpc1-generator")
CC = shutil.which("gcc") or shutil.which("cc")
SOURCES = ["host/vbus_switch_tool.c", "vbus_switch.c"]

with open(os.path.join(GEN, "vbus_switch.h")) as f:
    _h = f.read()
RETRIES = int(re.search(r"#define VBUS_SWITCH_RETRIES\s+(\d+)u", _h).group(1))
RETRY_MS = int(re.search(r"#define VBUS_SWITCH_RETRY_MS\s+(\d+)u", _h).group(1))
ABSENT_MS = int(re.search(r"#define VBUS_SWITCH_ABSENT_MS\s+(\d+)u", _h).group(1))


def build(directory, gen):
    exe = os.path.join(directory, "vbus_switch_tool")
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", gen]
    cmd += ["-o", exe] + [os.path.join(gen, s) for s in SOURCES]
    subprocess.run(cmd, check=True)
    return exe


def polls(start, end, present, oc=0, step=10):
    """A poll every step ms from start up to, not including, end."""
    return [(t, present, oc) for t in range(start, end, step)]


@unittest.skipUnless(CC, "no host C compiler")
class VbusSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build(cls.tmp.name, GEN)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, lines):
        """lines: (ms, present, overcurrent), (ms, "refuse") or (ms, "state");
        the first is the start. Returns [(word, ms)] and the state words."""
        text = "".join(" ".join(str(x) for x in line) + "\n" for line in lines)
        out = subprocess.run([self.exe], input=text, capture_output=True, text=True,
                             check=True).stdout
        events, states = [], []
        for line in out.splitlines():
            word, value = line.split()
            if word == "state":
                states.append(value)
            else:
                events.append((word, int(value)))
        return events, states

    def test_on_at_start_without_vbus(self):
        events, states = self.run_tool([(0, 0, 0), (5, 1, 0), (10, "state")])
        self.assertEqual(events, [("on", 0)])
        self.assertEqual(states, ["on"])

    def test_vbus_present_for_good_one_fault_never_on(self):
        # CN19 stays on a hub: one fault, then nothing, however long
        events, states = self.run_tool(polls(0, 60000, 1) + [(60000, "state")])
        self.assertEqual(events, [("fault", 0)])
        self.assertEqual(states, ["fault"])

    def test_vbus_present_then_gone_switches_on_after_absent_time(self):
        # The cable moves from the hub to a device after 10 s: one fault,
        # then on once VBUS has been away for ABSENT_MS, without a reset
        gone = 10000
        events, _ = self.run_tool(polls(0, gone, 1) + polls(gone, gone + 3000, 0, step=1))
        self.assertEqual(events, [("fault", 0), ("on", gone + ABSENT_MS)])

    def test_absent_time_must_be_unbroken(self):
        # Away for less than ABSENT_MS twice: the time starts over each time
        lines = ([(0, 1, 0)] + polls(100, 900, 0) + polls(900, 1000, 1)
                 + polls(1000, 1990, 0) + polls(1990, 2000, 1) + polls(2000, 4000, 0, step=1))
        events, _ = self.run_tool(lines)
        self.assertEqual(events, [("fault", 0), ("on", 2000 + ABSENT_MS)])

    def test_absent_one_ms_short(self):
        events, states = self.run_tool([(0, 1, 0), (1, 0, 0), (ABSENT_MS, 0, 0), (ABSENT_MS, "state"),
                                        (ABSENT_MS + 1, 0, 0)])
        self.assertEqual(events, [("fault", 0), ("on", ABSENT_MS + 1)])
        self.assertEqual(states, ["fault"])

    def test_overcurrent_turns_off_and_retries(self):
        t = 5000
        events, states = self.run_tool([(0, 0, 0), (t, 1, 1), (t + 1, 0, 0), (t + 1, "state"),
                                        (t + RETRY_MS - 1, 0, 0), (t + RETRY_MS, 0, 0),
                                        (t + RETRY_MS, "state")])
        self.assertEqual(events, [("on", 0), ("fault", t), ("on", t + RETRY_MS)])
        self.assertEqual(states, ["fault", "on"])

    def test_overcurrent_three_retries_then_off(self):
        lines, t = [(0, 0, 0)], 100
        for _ in range(RETRIES + 1):
            lines += [(t, 1, 1), (t + RETRY_MS, 0, 0)]
            t += RETRY_MS + 100
        events, states = self.run_tool(lines + [(t + 10 * RETRY_MS, 0, 0), (t, "state")])
        self.assertEqual([w for w, _ in events],
                         ["on"] + ["fault", "on"] * RETRIES + ["fault", "off"])
        self.assertEqual(events[-1][1], events[-2][1])
        self.assertEqual(states, ["off"])

    def test_vbus_present_at_an_overcurrent_retry(self):
        # At the retry another host's VBUS is there: one fault for it, then
        # on after ABSENT_MS without VBUS
        t = 100
        lines = [(0, 0, 0), (t, 1, 1)] + polls(t + RETRY_MS, t + 5000, 1) \
            + polls(t + 5000, t + 7000, 0, step=1)
        events, _ = self.run_tool(lines)
        self.assertEqual(events, [("on", 0), ("fault", t), ("fault", t + RETRY_MS),
                                  ("on", t + 5000 + ABSENT_MS)])

    def test_overcurrent_while_off_is_ignored(self):
        events, _ = self.run_tool([(0, 1, 0), (100, 0, 1), (200, 0, 1)])
        self.assertEqual(events, [("fault", 0)])

    def test_refused_by_the_board(self):
        # VBUS appeared between the poll and the switch: the board refuses,
        # which counts as VBUS present
        events, states = self.run_tool([(0, 0, 0), (0, "refuse"), (0, "state"), (1, 0, 0),
                                        (ABSENT_MS, 0, 0), (ABSENT_MS + 1, 0, 0)])
        self.assertEqual(events, [("on", 0), ("fault", 0), ("on", ABSENT_MS + 1)])
        self.assertEqual(states, ["fault"])

    def test_vbus_present_never_counts_down(self):
        # VBUS from elsewhere many times over (refused switch-ons): each is
        # one fault and ends with on, never with off; overcurrent still has
        # all its retries after that
        lines, t, n = [(0, 0, 0)], 0, 2 * (RETRIES + 1)
        for _ in range(n):
            lines += [(t, "refuse")] + polls(t + 1, t + ABSENT_MS + 2, 0, step=1)
            t += ABSENT_MS + 1
        events, _ = self.run_tool(lines)
        self.assertEqual([w for w, _ in events], ["on"] + ["fault", "on"] * n)
        t += 10
        for _ in range(RETRIES + 1):
            lines += [(t, 1, 1), (t + RETRY_MS, 0, 0)]
            t += RETRY_MS + 100
        events, _ = self.run_tool(lines)
        self.assertEqual([w for w, _ in events][1 + 2 * n:],
                         ["fault", "on"] * RETRIES + ["fault", "off"])

    def test_millis_wrap(self):
        t0 = 0xFFFFFFFF - 300
        w = lambda t: t & 0xFFFFFFFF  # noqa: E731
        events, _ = self.run_tool([(t0, 1, 0), (w(t0 + 1), 0, 0), (w(t0 + ABSENT_MS), 0, 0),
                                   (w(t0 + 1 + ABSENT_MS), 0, 0)])
        self.assertEqual(events, [("fault", t0), ("on", w(t0 + 1 + ABSENT_MS))])

    def test_random_sequences_keep_the_rules(self):
        rng = random.Random(1)
        for seed in range(200):
            with self.subTest(seed=seed):
                # VBUS from elsewhere comes and goes; while the switch is on,
                # the input sees its own VBUS, which the switch ignores
                lines, t, present = [], 0, rng.random() < 0.5
                for _ in range(400):
                    t += rng.choice((1, 10, 100, 400, 999, 1000))
                    if rng.random() < 0.05:
                        present = not present
                    lines.append((t, int(present), int(rng.random() < 0.02)))
                events, _ = self.run_tool(lines)
                at = {ms: (p, oc) for ms, p, oc in lines}
                times = [ms for ms, _, _ in lines]
                words = [w for w, _ in events]
                for k, (w, ms) in enumerate(events):
                    if w != "on":
                        continue
                    # Never on while VBUS is already there
                    self.assertEqual(at[ms][0], 0, f"on at {ms} with VBUS present")
                    if k == 0:
                        continue
                    # After a fault: a full retry time later at least
                    self.assertEqual(events[k - 1][0], "fault")
                    self.assertGreaterEqual(ms - events[k - 1][1], RETRY_MS)
                    # After VBUS was present: ABSENT_MS of polls without it
                    if at[events[k - 1][1]][0] == 1 and at[events[k - 1][1]][1] == 0:
                        i = times.index(ms)
                        j = i
                        while j > 0 and at[times[j - 1]][0] == 0 and times[j - 1] > events[k - 1][1]:
                            j -= 1
                        self.assertGreaterEqual(ms - times[j], ABSENT_MS)
                # At most two faults in a row: an overcurrent, then VBUS
                # present at its retry
                for k in range(1, len(events)):
                    if words[k] == "fault" and words[k - 1] == "fault":
                        self.assertEqual(at[events[k - 1][1]][1], 1)
                        self.assertTrue(k < 2 or words[k - 2] != "fault")
                # Nothing after off
                if "off" in words:
                    self.assertEqual(words.index("off"), len(words) - 1)


if __name__ == "__main__":
    unittest.main()
