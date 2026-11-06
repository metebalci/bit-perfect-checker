# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The hole counter of firmware/app/stream.c on the host: lost, missed, holes,
maxhole and doubled on the stream stop line, and lost on the result lines.

The USB interrupt takes each packet's microframe from what it read at its
entry: the microframe number and the endpoint's EONUM bit, the parity of the
microframe the packet came in. An entry in the packet's microframe P or in
P+1 gives P; an entry in P+2 or later is the late re-arm of a loss, which
keeps the net count; an entry before the packet (the interrupt entered for
something else and served the packet's completion too) is caught by a fresh
read. So a late interrupt entry neither counts a hole nor a doubled
microframe, and only microframes without a packet count as lost. Built with
the host tool firmware/app/host/stream_tool.c; needs a host C compiler (gcc
or cc); skipped without one."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
CC = shutil.which("gcc") or shutil.which("cc")
SOURCES = [
    "app/host/stream_tool.c",
    "app/stream.c",
    "bpc1/bpc1.c",
    "bpc1/bpc1_checker.c",
    "lib/tinyusb/src/common/tusb_fifo.c",
]

with open(os.path.join(FW, "app", "tusb_config.h")) as f:
    FIFO_BYTES = int(re.search(r"#define CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ\s+(\d+)", f.read()).group(1))

START = 2000  # the first packet's microframe
WRAP = 0x4000  # the controller's microframe number wraps here


def build(directory, fw):
    exe = os.path.join(directory, "stream_tool")
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
           f"-DCFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ={FIFO_BYTES}"]
    for d in ["app/host/stub", "lib/tinyusb/src", "app", "include", "bpc1"]:
        cmd += ["-I", os.path.join(fw, d)]
    cmd += ["-o", exe] + [os.path.join(fw, s) for s in SOURCES]
    subprocess.run(cmd, check=True)
    return exe


def fields(line):
    return dict(m.groups() for m in re.finditer(r"(\w+)=(\S+)", line))


@unittest.skipUnless(CC, "no host C compiler")
class StreamHoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build(cls.tmp.name, FW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def stream(self, packets):
        """Runs the tool on packets, each a microframe p or a tuple (p, entry[, now]).
        Returns the stream stop line's fields, the result lines' lost values and
        the number of streams started."""
        text = "".join(" ".join(str(v) for v in (p if isinstance(p, tuple) else (p,))) + "\n" for p in packets)
        out = subprocess.run([self.exe], input=text, capture_output=True, text=True, check=True).stdout
        stops = [fields(line) for line in out.splitlines() if " stream stop " in line]
        results = [int(fields(line)["lost"]) for line in out.splitlines() if " result " in line]
        starts = sum(1 for line in out.splitlines() if " stream start " in line)
        self.assertEqual(len(stops), 1, out)
        stop = {k: int(stops[0][k]) for k in ("lost", "missed", "holes", "maxhole", "doubled", "pkts")}
        self.assertEqual(sum(results), stop["lost"], "result lines add up to the stop line")
        return stop, results, starts

    def check(self, packets, lost, holes=None, maxhole=None, results=None):
        stop, got_results, starts = self.stream(packets)
        self.assertEqual(starts, 1)
        self.assertEqual(stop["pkts"], len(packets))
        self.assertEqual(stop["lost"], lost)
        self.assertEqual(stop["missed"], lost)
        if holes is not None:
            self.assertEqual(stop["holes"], holes)
        self.assertEqual(stop["doubled"], 0)
        if maxhole is not None:
            self.assertEqual(stop["maxhole"], maxhole)
        if results is not None:
            self.assertEqual(got_results, results)
        return stop

    def test_entry_in_the_packets_microframe(self):
        self.check(list(range(START, START + 800)), lost=0, holes=0, maxhole=0, results=[0])

    def test_entry_in_the_next_microframe(self):
        # Every 50th packet's interrupt enters in P+1: nothing lost
        packets = [(p, p + 1) if (p - START) % 50 == 25 else p for p in range(START, START + 800)]
        self.check(packets, lost=0, holes=0, maxhole=0, results=[0])

    def test_first_packet_entry_in_the_next_microframe(self):
        # Once lost=-1 on the stream's first result and its stop line
        packets = [(START, START + 1)] + list(range(START + 1, START + 800))
        self.check(packets, lost=0, holes=0, results=[0])

    def test_last_packet_entry_in_the_next_microframe(self):
        # Once a hole of 1 that no doubled microframe took back
        last = START + 799
        packets = list(range(START, last)) + [(last, last + 1)]
        self.check(packets, lost=0, holes=0, results=[0])

    def test_entry_two_microframes_late(self):
        # The re-arm comes in P+2, so P+1 and P+2 go by: 2 lost, counted as
        # one hole before the packet instead of after it
        k = START + 400
        packets = list(range(START, k)) + [(k, k + 2)] + list(range(k + 3, START + 800))
        self.check(packets, lost=2, holes=1, maxhole=2, results=[2])

    def test_entry_three_microframes_late(self):
        k = START + 400
        packets = list(range(START, k)) + [(k, k + 3)] + list(range(k + 4, START + 800))
        self.check(packets, lost=3, results=[3])

    def test_entry_before_the_packet(self):
        # The interrupt entered in P-1 and served the packet that completed in
        # P in the same pass: the entry's values give P-2, a fresh read P
        k = START + 400
        packets = list(range(START, k)) + [(k, k - 1, k)] + list(range(k + 1, START + 800))
        self.check(packets, lost=0, holes=0, results=[0])

    def test_entry_before_the_first_packet_after_a_hole(self):
        k = START + 400
        packets = list(range(START, k)) + [(k + 2, k + 1, k + 2)] + list(range(k + 3, START + 800))
        self.check(packets, lost=2, holes=1, maxhole=2, results=[2])

    def test_host_skips(self):
        skipped = {START + 100, START + 101, START + 500}
        packets = [p for p in range(START, START + 800) if p not in skipped]
        self.check(packets, lost=3, holes=2, maxhole=2, results=[3])

    def test_wrap(self):
        # Across 16383 -> 0: an entry in P+1 at 16383, an entry before the
        # packet at 0, and one microframe without a packet
        first = WRAP - 400
        packets = []
        for p in range(first, WRAP + 400):
            if p == WRAP - 1:
                packets.append((p, p + 1))
            elif p == WRAP:
                packets.append((p, p - 1, p))
            elif p == WRAP + 5:
                continue
            else:
                packets.append(p)
        self.check(packets, lost=1, holes=1, maxhole=1, results=[1])

    def test_late_rearms(self):
        # Every 100th re-arm late, as the test build's inject command makes
        # it: the interrupt enters in P, runs into P+1, and P+1 goes by
        packets, injections, p = [], 0, START
        while p < START + 2000:
            if (p - START) % 100 == 50:
                packets.append((p, p, p + 1))
                injections += 1
                p += 2
            else:
                packets.append(p)
                p += 1
        self.check(packets, lost=injections, holes=injections, maxhole=1)


if __name__ == "__main__":
    unittest.main()
