# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The stream reader and its accounting on the host: firmware/app/stream.c
and firmware/app/u2ps_glue.c against u2ps's device stack, through u2ps's PC
test kit, which plays the device port and the host (firmware/app/host/
stream_tool.c). Each packet stays in the buffer of class memory the glue
lent; the main loop reads it there and gives the buffers back in order.

u2ps reports the microframes without a packet before the packet that ends
them, with a reason: the host sent none (NO_PACKET), the device re-armed late
(LATE), a corrupted packet (CORRUPTED); these count as lost and missed, LATE
also as late= on the stream stop line; CORRUPTED has no field of its own
(the DWC2 core drops such a packet unseen). A zero-length
packet counts as a missed microframe. Packets u2ps had no buffer for, because
the main loop still held every one (NO_BUFFER), count as overflows: the
checker hears of them before the next packet's frames, and the file is
OVERRUN (docs/bpc1.md 12.6). The kit's port, as u2ps's DWC2 port, keeps
one buffer armed (U2PS_DPORT_ISO_OUT_RESERVE), so the app holds all but
one; a packet that comes while it does is a NO_BUFFER hole of its own
microframe, reported before the next packet, or at a stream's end before
the stream stops. The host tools lend 7760 bytes of class memory: 20
buffers at 16/2 (alt 1), 19 of them held at most, 10 at 24/4 and 32/4; the
big one 2048 at 16/2.

Every run must end with the kit's PASS (no contract or port violation, no
sanitizer report) and u2ps holding no packet for the app. Needs CMake and a
host C compiler; skipped without them."""

import os
import re
import subprocess
import tempfile
import unittest

import numpy as np

from generator import spec
from tests import helpers as H
from tests import host_tools

START = 2000  # the first packet's microframe
WRAP = 0x4000  # u2ps's microframe numbers wrap here
BUFFERS_16_2 = 20  # class memory 7760 bytes at the 388-byte stride (firmware/app/host/CMakeLists.txt)
HELD_16_2 = BUFFERS_16_2 - 1  # the port keeps one armed (U2PS_DPORT_ISO_OUT_RESERVE)


def fields(line):
    return dict(m.groups() for m in re.finditer(r"(\w+)=(\S+)", line))


@unittest.skipUnless(host_tools.AVAILABLE, "no CMake or host C compiler")
class StreamHoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tools = host_tools.build(cls.tmp.name, ["stream_tool", "stream_tool_big"])
        cls.exe, cls.exe_big = tools["stream_tool"], tools["stream_tool_big"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, packets, data=None, alt=None, big=False, usb_fault=0):
        """Runs the tool on packets, each a microframe p or a line of the
        tool's input such as "zlp <p>". Returns the stream stop lines'
        fields, the result lines' fields and the whole output. Checks the
        kit's verdict, that u2ps holds no packet at the end, that the result
        lines of each stream add up to its stop line, and that u2ps's
        counters show no release error and no lost event."""
        text = "".join(str(p) + "\n" for p in packets)
        cmd = [self.exe_big if big else self.exe] + ([data or "-"] if data or alt else []) + ([str(alt)] if alt else [])
        r = subprocess.run(cmd, input=text, capture_output=True, text=True)
        out = r.stdout
        self.assertEqual(r.returncode, 0, out[-3000:] + r.stderr[-3000:])
        self.assertIn("u2ps testkit: PASS", out)
        self.assertRegex(out, r"\n\d+ held 0\n")
        self.assertRegex(out, r"\n\d+ usb_fault %d\n" % usb_fault)
        stops = [fields(line) for line in out.splitlines() if " stream stop " in line]
        # the tool's line after each stop: whether the stream's last result
        # holds its end (the engine): not after a file's LAST block inside
        # its extent, nor in allowed silence that gave no result
        end_held = [line.split()[-1] == "1" for line in out.splitlines() if " end_held " in line]
        self.assertEqual(len(end_held), len(stops), out[-2000:])
        results = [fields(line) for line in out.splitlines() if " result " in line]
        for counters in (fields(line) for line in out.splitlines() if " usb counters " in line):
            self.assertEqual(counters["release_errors"], "0", out[-2000:])
            if not usb_fault:
                self.assertEqual(counters["ev_lost"], "0", out[-2000:])
        for stop, held in zip(stops, end_held):
            mine = [x for x in results if x["stream"] == stop["id"]]
            for key in ("lost", "ovf"):
                if not held or not mine:
                    # losses and overflows after the last file's LAST
                    # block, where the stream ended, are on the stop line
                    # only, and so are those of a stream whose checker got
                    # no frame (a segment cut unread): no result
                    self.assertLessEqual(sum(int(x[key]) for x in mine), int(stop[key]), "result lines add up")
                else:
                    self.assertEqual(sum(int(x[key]) for x in mine), int(stop[key]), "result lines add up")
        # The overflows and the verdict land on the same result: the one
        # that holds the lost packets (12.6 verdict 2)
        for x in results:
            if x["verdict"] == "OVERRUN":
                self.assertGreater(int(x["ovf"]), 0, "an OVERRUN result counts its overflows: " + str(x))
            elif int(x["ovf"]) > 0:
                self.assertEqual(x["verdict"], "NOT_BIT_PERFECT", "overflows on a judged result: " + str(x))
        return stops, results, out

    def stream(self, packets):
        """As run_tool(), for one stream: its stop line's numbers."""
        stops, results, out = self.run_tool(packets)
        self.assertEqual(len(stops), 1, out[-2000:])
        return {k: int(stops[0][k]) for k in ("lost", "missed", "holes", "maxhole", "doubled", "late",
                                              "ovf", "pkts", "frames")}, results

    def check(self, packets, lost, holes=None, maxhole=None, late=0):
        stop, results = self.stream(packets)
        self.assertEqual(stop["pkts"], sum(1 for p in packets if isinstance(p, int)))
        self.assertEqual((stop["lost"], stop["missed"], stop["doubled"], stop["ovf"]), (lost, lost, 0, 0))
        self.assertEqual(stop["late"], late)
        if holes is not None:
            self.assertEqual(stop["holes"], holes)
        if maxhole is not None:
            self.assertEqual(stop["maxhole"], maxhole)
        return stop, results

    # Holes by reason

    def test_no_hole(self):
        stop, results = self.check(list(range(START, START + 800)), lost=0, holes=0, maxhole=0)
        self.assertEqual([r["lost"] for r in results], ["0"])
        # 800 microframes of 6 frames: 100 ms at 48 kHz on the host's clock
        stops, _, _ = self.run_tool(list(range(START, START + 800)))
        self.assertEqual((stops[0]["dur"], stops[0]["rate"]), ("0.100", "48000.00"))

    def test_host_skips(self):
        skipped = {START + 100, START + 101, START + 500}
        self.check([p for p in range(START, START + 800) if p not in skipped], lost=3, holes=2, maxhole=2)

    def test_wrap(self):
        # Across 16383 -> 0, with one microframe without a packet after it
        self.check([p for p in range(WRAP - 400, WRAP + 400) if p != WRAP + 5], lost=1, holes=1, maxhole=1)

    def test_late(self):
        # The device re-armed late: two microframes LATE, one hole
        k = START + 400
        packets = list(range(START, k)) + ["late 2"] + list(range(k + 2, START + 800))
        self.check(packets, lost=2, holes=1, maxhole=2, late=2)

    def test_corrupted(self):
        k = START + 400
        packets = list(range(START, k)) + ["crc 1"] + list(range(k + 1, START + 800))
        self.check(packets, lost=1, holes=1, maxhole=1)
        # no field of its own on the stop line, nor u2ps's count on the
        # counters line (always 0 on the DWC2 ports)
        _, _, out = self.run_tool(packets)
        stop = next(l for l in out.splitlines() if " stream stop " in l)
        self.assertNotIn(" crc=", stop)
        self.assertRegex(stop, r" late=0 ovf=0 ")

    def test_hole_in_parts(self):
        # u2ps may report one hole in several calls (at the ticks of a long
        # gap): the same totals, one hole, its whole length the longest
        k = START + 400
        one = list(range(START, k)) + ["late 4"] + list(range(k + 4, START + 800))
        # the tool's parts go straight to the glue, past u2ps, which then
        # sees no gap: the packets go on in the next microframe
        parts = list(range(START, k)) + ["parts 1 4 late"] + list(range(k, START + 796))
        for packets in (one, parts):
            stop, _ = self.stream(packets)
            self.assertEqual((stop["lost"], stop["missed"], stop["late"], stop["holes"], stop["maxhole"]), (4, 4, 4, 1, 4))
        nobuf = list(range(START, k)) + ["parts 3 2 nobuf"] + list(range(k, START + 794))
        stop, _ = self.stream(nobuf)
        self.assertEqual((stop["ovf"], stop["lost"], stop["holes"]), (6, 0, 0))

    def test_long_hole_is_no_pause(self):
        # 60 ms of LATE, CORRUPTED or NO_BUFFER microframes while the host
        # sends: no pause (u2ps_app_on_pause()), so the stream goes on, one
        # stream with the whole hole
        k = START + 400
        for why, want in (("late", ("480", "480", "0")), ("crc", ("480", "0", "0")), ("nobuf", ("0", "0", "480"))):
            with self.subTest(why=why):
                packets = list(range(START, k)) + ["%s 480" % why, "skip 480"] + list(range(k + 480, k + 880))
                # then a host pause of 600 microframes: the idle time counts
                # from the last packet, the hole before it no part of it
                packets += list(range(k + 1480, k + 1580))
                stops, _, out = self.run_tool(packets)
                self.assertEqual([(s["reason"], s["pkts"], s["lost"], s["late"], s["ovf"]) for s in stops],
                                 [("idle", "800") + want, ("idle", "100", "0", "0", "0")], out[-2000:])

    def test_idle_after_overflows_with_gaps(self):
        # 19 packets held, then 20 that find no buffer, a microframe apart:
        # u2ps reports their NO_BUFFER and the NO_PACKET microframes
        # between them at its long gap's ticks; only the NO_BUFFER ones are
        # no part of the gap, so the stream stops for idle 420 microframes
        # after the main loop last saw a packet, just before the next one
        k = START + 400
        packets = list(range(START, k)) + ["hold 39"] + list(range(k, k + 19)) + list(range(k + 20, k + 60, 2))
        packets += list(range(k + 488, k + 688))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"], s["ovf"]) for s in stops], [("idle", "419", "20"), ("idle", "200", "0")],
                         out[-2000:])

    def test_counters_past_32_bits(self):
        # One hole of 2^31 + 32767 microframes, and one of more than 2^32,
        # in u2ps's reports of 65535 (about 3.1 and 6.2 days): every count
        # in full, on the stop line and the result line
        k = START + 400
        # in a BPC1 file, which takes the loss on its line (the data goes on)
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "past_32_bits.raw")
        f = START + len(pcm) // 48
        packets = list(range(START, f)) + ["parts 65535 32769 late"] + list(range(f, START + len(pcm) // 24 + 3000))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["kind"], r["lost"]) for r in results], [("PCM", str(65535 * 32769)), ("NONE", "0")],
                         out[-2000:])
        for n, why in ((32769, "late"), (65538, "late"), (65538, "crc"), (65538, "nobuf")):
            with self.subTest(n=n, why=why):
                total = str(65535 * n)
                packets = list(range(START, k)) + ["parts 65535 %d %s" % (n, why)] + list(range(k, k + 400))
                stops, results, out = self.run_tool(packets)
                stop = stops[0]
                if why == "nobuf":
                    self.assertEqual((stop["ovf"], stop["lost"]), (total, "0"), out[-2000:])
                    self.assertEqual([(r["verdict"], r["ovf"]) for r in results], [("OVERRUN", total)])
                else:
                    self.assertEqual((stop["lost"], stop["missed"], stop["holes"], stop["maxhole"]),
                                     (total, total, "1", total), out[-2000:])
                    self.assertEqual(stop["late"], total if why == "late" else "0", out[-2000:])
                    self.assertEqual([r["lost"] for r in results], [total])

    def test_tail_hole_counted(self):
        # A LATE hole after a stream's last packet, which u2ps reports at
        # its tick's long gap: missed and a hole of the stream it ended
        packets = list(range(START, START + 400)) + ["parts 1 2 late", "idle 600"]
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["missed"], s["late"], s["holes"], s["maxhole"]) for s in stops],
                         [("idle", "2", "2", "1", "2")], out[-2000:])

    def test_reasons_in_one_hole(self):
        # One microframe NO_PACKET and one LATE before the same packet: one
        # hole of 2, one of them late
        k = START + 400
        packets = list(range(START, k)) + ["late 1"] + list(range(k + 2, START + 800))
        self.check(packets, lost=2, holes=1, maxhole=2, late=1)

    def test_zero_length_packet(self):
        # A zero-length packet carries no frames: its microframe counts as
        # one without a packet, in the same hole as those around it
        k = START + 400
        packets = list(range(START, k)) + [f"zlp {k}"] + list(range(k + 1, START + 800))
        stop, _ = self.stream(packets)
        self.assertEqual((stop["pkts"], stop["lost"], stop["holes"], stop["maxhole"]), (799, 1, 1, 1))
        packets = list(range(START, k)) + [f"zlp {k + 1}"] + list(range(k + 3, START + 800))
        stop, _ = self.stream(packets)
        self.assertEqual((stop["lost"], stop["holes"], stop["maxhole"]), (3, 1, 3))
        # Three apart, as measured on 1.1.1 (docs/measurements.md 2.11)
        zlps = {START + 100, START + 300, START + 500}
        packets = [f"zlp {p}" if p in zlps else p for p in range(START, START + 800)]
        stop, _ = self.stream(packets)
        self.assertEqual((stop["pkts"], stop["lost"], stop["missed"], stop["holes"], stop["maxhole"]),
                         (797, 3, 3, 3, 1))

    def test_zero_length_packets_across_the_wrap(self):
        # 16384 zero-length packets in a row, as many as u2ps's microframe
        # numbers count before they wrap: each moves the stream's
        # microframe count on, so its duration and rate hold them all
        packets = list(range(START, START + 400)) + ["zlp %d" % p for p in range(START + 400, START + 400 + 16384)]
        packets += list(range(START + 16784, START + 17184))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["dur"], s["rate"], s["lost"], s["holes"], s["maxhole"]) for s in stops],
                         [("2.148", "2234.63", "16384", "1", "16384")], out[-2000:])

    def test_zero_length_packet_after_a_pause(self):
        # A zero-length packet first after a pause, while the main loop
        # holds the stream before it: a missed microframe of that stream,
        # its microframes not that stream's
        packets = list(range(START, START + 100)) + ["hold 12"] + list(range(START + 100, START + 105))
        packets += ["zlp %d" % (START + 600)] + list(range(START + 601, START + 900))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"], s["dur"], s["lost"]) for s in stops],
                         [("pause", "105", "0.013", "1"), ("idle", "299", "0.037", "0")], out[-2000:])

    def test_zero_length_packets_held(self):
        # Zero-length packets take a buffer each until the main loop reads
        # them
        packets = list(range(START, START + 100)) + ["hold %d" % HELD_16_2]
        packets += [f"zlp {p}" if p % 2 else p for p in range(START + 100, START + 100 + HELD_16_2)]
        packets += list(range(START + 120, START + 400))
        stop, _ = self.stream(packets)
        self.assertEqual((stop["lost"], stop["ovf"], stop["holes"]), (10, 0, 10))

    # Overflows: every buffer held

    def test_every_buffer_held(self):
        # 30 packets while the main loop does not run: the first 19 take
        # every buffer it can hold, the last 11 find none and come as an
        # overflow
        packets = list(range(START, START + 400)) + ["hold 30"] + list(range(START + 400, START + 800))
        stops, results, out = self.run_tool(packets)
        stop = stops[0]
        self.assertEqual((stop["ovf"], stop["lost"], stop["missed"], stop["pkts"]), ("11", "0", "0", "789"))
        # A stretch of silence with an overflow is OVERRUN (12.6)
        self.assertEqual([r["verdict"] for r in results], ["OVERRUN"])
        # At most every held buffer's packet waited: 19 of 24 bytes
        self.assertRegex(out, r"\n\d+ fifo_max 456\n")

    def test_overflow_in_packets_not_read(self):
        # An overflow before a packet the main loop has not read when the
        # alt setting changes: still told to the checker, and counted
        packets = list(range(START, START + 100)) + ["hold 25"] + list(range(START + 100, START + 125))
        packets += ["idle 1", "hold 1", START + 126, "alt 1"] + list(range(START + 400, START + 600))
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["ovf"], s["pkts"]) for s in stops], [("alt", "6", "120"), ("idle", "0", "200")],
                         out[-2000:])
        self.assertEqual([r["verdict"] for r in results if r["stream"] == stops[0]["id"]], ["OVERRUN"])
        # read after the stop, X included
        self.check_all_read(stops[0])

    def test_nobuf_hole(self):
        # u2ps's NO_BUFFER, as the port reports it: an overflow, not a loss
        k = START + 400
        packets = list(range(START, k)) + ["nobuf 3"] + list(range(k + 3, START + 800))
        stop, results = self.stream(packets)
        self.assertEqual((stop["ovf"], stop["lost"], stop["holes"]), (3, 0, 0))
        self.assertEqual([r["verdict"] for r in results], ["OVERRUN"])

    def test_counters(self):
        # u2ps's counters since boot, logged when one changes: none here
        _, _, out = self.run_tool(list(range(START, START + 800)))
        self.assertNotIn(" usb counters ", out)
        # a packet in the same microframe as the one before (u2ps's
        # uframe_back): one line
        _, _, out = self.run_tool(list(range(START, START + 400)) + list(range(START + 399, START + 800)))
        counters = [fields(line) for line in out.splitlines() if " usb counters " in line]
        self.assertEqual([c["uframe_back"] for c in counters], ["1"], out[-2000:])
        # the fields in this order, no corrupted= (always 0 on the DWC2 ports)
        self.assertEqual([list(c) for c in counters],
                         [["late_completions", "late", "no_buffer", "fb_recoveries", "errors", "release_errors",
                           "uframe_back", "ev_lost"]])

    # Overflows at a stream's end: the packets that found every buffer held
    # come as a NO_BUFFER hole with no packet after it in the stream

    def end_overflow(self, ending, reasons):
        # 19 packets held, then 10 that find no buffer, the stream's tail
        packets = list(range(START, START + 100)) + ["hold %d" % (HELD_16_2 + 10)]
        packets += list(range(START + 100, START + 110 + HELD_16_2)) + ending
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], reasons, out[-2000:])
        self.assertEqual([r["verdict"] for r in results if r["stream"] == stops[0]["id"]], ["OVERRUN"])
        return stops

    def test_overflow_then_alt_0(self):
        self.end_overflow(["alt 0"], [("alt", "10")])

    def test_overflow_then_alt_1(self):
        self.end_overflow(["alt 1"] + list(range(START + 500, START + 700)), [("alt", "10"), ("idle", "0")])

    def test_overflow_then_alt_0_slow_close(self):
        # The port ends the close only after main-loop passes, as DWC2 does:
        # the tail's overflows come then, before u2ps's on_stream_stop, and
        # still make the file OVERRUN
        for passes in (1, 5):
            with self.subTest(passes=passes):
                self.end_overflow(["closeslow %d" % passes, "alt 0"], [("alt", "10")])

    def test_overflow_then_set_configuration_slow_close(self):
        self.end_overflow(["closeslow 3", "setconfig", "alt 1"] + list(range(START + 500, START + 700)),
                          [("alt", "10"), ("idle", "0")])

    def test_overflow_then_detach(self):
        # A formats change detaches the device (u2ps_dev_connect(false)):
        # u2ps reports the tail in the main loop, before the stream stops
        self.end_overflow(["formats 16/2,24/3"] + list(range(START + 500, START + 700)),
                          [("reset", "10"), ("idle", "0")])

    def test_overflow_then_vbus_loss(self):
        # VBUS goes: u2ps reports the tail in the main loop
        # (uac2_unplug()), before the stream stops
        self.end_overflow(["unplug", "plug"] + list(range(START + 500, START + 700)), [("reset", "10"), ("idle", "0")])

    def test_overflow_then_set_configuration_0(self):
        # SET_CONFIGURATION 0 ends the configuration at once, and u2ps
        # closes the stream after (u2ps_dev.h): the tail's overflows, which
        # come at the close's end, still count in the stream, and it stops
        # as at alt 0, also when the close takes main-loop passes
        for close in ([], ["closeslow 3"]):
            with self.subTest(close=close):
                stops = self.end_overflow(close + ["setconfig 0", "setconfig 1", "alt 1"]
                                          + list(range(START + 500, START + 700)), [("alt", "10"), ("idle", "0")])
                self.check_all_read(stops[0])
                # the device is no longer set up at once, and alt 0 comes
                # when u2ps has closed the stream, then the stream's lines:
                # u2ps's own order (u2ps_dev.h), whether the close ends
                # before the main loop sees the end or after
                out = self.run_tool(list(range(START, START + 100)) + close + ["setconfig 0"])[2]
                lines = [l.split(" ", 1)[1] for l in out.splitlines()]
                i = lines.index("stream start id=1 rate=48000 alt=1 slot=2 res=16 allow_silence=false")
                self.assertEqual([lines[i + 1], lines[i + 2], lines[i + 3].split()[0], lines[i + 4].split()[:4]],
                                 ["usb disconnected", "alt 0", "result", ["stream", "stop", "id=1", "reason=alt"]],
                                 out[-2000:])

    def test_overflow_then_reset(self):
        self.end_overflow(["reset"] + list(range(START + 500, START + 700)), [("reset", "10"), ("idle", "0")])

    # A host pause after the stall: u2ps reports the overflow at its tick's
    # long gap, and the stream stops for idle only after that tick
    def test_overflow_then_idle(self):
        stops = self.end_overflow(["idle 500"] + list(range(START + 1000, START + 1200)),
                                  [("idle", "10"), ("idle", "0")])
        self.check_all_read(stops[0])

    def test_overflow_then_pause(self):
        # A host pause after it: the stream stops for idle with the overflow
        self.end_overflow(list(range(START + 700, START + 900)), [("idle", "10"), ("idle", "0")])

    def test_file_overflow_then_pause(self):
        # The board's order: a BPC1 file mid-body, every buffer held while
        # the main loop stalls, 10 packets that find none, then a host
        # pause of 200 ms with the alt setting kept. u2ps reports the
        # NO_BUFFER hole with no packet after it in the stream, so the
        # overflow reaches the checker at the stop, where the frames read
        # end: the file is OVERRUN, not NOT_BIT_PERFECT at the overflow
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "overflow_pause.raw")
        n = len(pcm) // 24
        k = n // 2
        held = list(range(START, START + k)) + ["hold %d" % (HELD_16_2 + 10)]
        held += list(range(START + k, START + k + HELD_16_2 + 10))
        after = list(range(START + k + HELD_16_2 + 10 + 1600, START + n + 40))
        for name, middle in (("main loop back before the pause", []),
                             ("main loop held through the pause", ["slowonce 1600"])):
            with self.subTest(name):
                stops, results, out = self.run_tool(held + middle + after, path, 1)
                self.assertEqual([(s["reason"], s["ovf"]) for s in stops[:1]], [("idle" if not middle else "pause", "10")],
                                 out[-2000:])
                self.assertEqual([(r["verdict"], r["diff"], r["ovf"]) for r in results if r["stream"] == stops[0]["id"]],
                                 [("OVERRUN", "-", "10")], out[-2000:])

    def test_overflow_at_a_file_boundary(self):
        # Two gapless files, the boundary at a packet's edge, and the first
        # 10 packets of the second find no buffer: the overflow stands
        # before the second file's first frame received, so its verdict and
        # its count are the second file's (12.6 verdict 2)
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, "boundary_1.raw")
        path = os.path.join(self.tmp.name, "boundary_2.raw")
        with open(path, "wb") as f:
            f.write(pcm + pcm)
        full, rest = divmod(len(pcm), 24)  # the first file: full packets, then one of rest bytes
        self.assertEqual(rest % 4, 0)
        k = START + full - HELD_16_2 + 1
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 10)] + list(range(k, START + full))
        packets += ["len %d" % rest, START + full, "len 24"] + list(range(START + full + 1, START + full + 11))
        packets += list(range(START + full + 11, START + 2 * full + 40))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["ovf"]) for r in results], [("BIT_PERFECT", "0"), ("OVERRUN", "10")],
                         out[-2000:])

    def test_overflow_in_the_padding(self):
        # A file, then host padding of silence shorter than a block, its
        # last 10 packets finding no buffer at the stream's end: the
        # overflow is after the file's LAST block, so it is on no result
        # line, only on the stream stop line
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, "padding_1.raw")
        path = os.path.join(self.tmp.name, "padding_2.raw")
        full = len(pcm) // 24 + 1  # the file's packets, the last one padded with silence
        with open(path, "wb") as f:
            f.write(pcm)
        end = START + full + 100  # 100 packets of padding
        k = end - 10 - HELD_16_2
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 10)] + list(range(k, end))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["kind"], r["ovf"]) for r in results], [("BIT_PERFECT", "PCM", "0")], out[-2000:])
        self.assertEqual([s["ovf"] for s in stops], ["10"])

    def test_lost_in_the_padding(self):
        # A file, then host padding of silence shorter than a block with a
        # hole of 3 microframes in it: the lost packets are after the
        # file's LAST block, so they are on no result line, only on the
        # stream stop line
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "padding_lost.raw")
        full = len(pcm) // 24 + 1  # the file's packets, the last one padded with silence
        k = START + full + 50
        packets = list(range(START, k)) + list(range(k + 3, START + full + 100))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["kind"], r["lost"]) for r in results], [("BIT_PERFECT", "PCM", "0")], out[-2000:])
        self.assertEqual([(s["lost"], s["holes"]) for s in stops], [("3", "1")])

    def padding_overflow(self, name, pad_frames):
        """A file, then pad_frames of silence received, the last 10 packets
        sent after them finding no buffer at the stream's end."""
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, name)
        total = len(pcm) // 4 + pad_frames  # frames received
        full, rest = divmod(total, 6)
        k = START + full + 1 - HELD_16_2
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 10)] + list(range(k, START + full))
        packets += (["len %d" % (4 * rest), START + full, "len 24"] if rest else [START + full])
        packets += list(range(START + full + 1, START + full + 11))
        return self.run_tool(packets, path, 1)

    def test_overflow_where_the_extent_ends(self):
        # The padding ends exactly where the file's extent does, a block
        # after its LAST block: the overflow is still after that block and
        # inside the extent, on the stream stop line only
        B = H.short_config(spec.KIND_PCM, 48000, 16).block_len
        stops, results, out = self.padding_overflow("extent_end.raw", B)
        self.assertEqual([(r["verdict"], r["ovf"]) for r in results], [("BIT_PERFECT", "0")], out[-2000:])
        self.assertEqual([s["ovf"] for s in stops], ["10"])

    def test_overflow_after_long_padding(self):
        # Two blocks of padding: the stream leaves the file's extent, so the
        # overflow at its end is the stretch's after the file, which is
        # OVERRUN and counts it
        B = H.short_config(spec.KIND_PCM, 48000, 16).block_len
        stops, results, out = self.padding_overflow("long_padding.raw", 2 * B)
        self.assertEqual([(r["verdict"], r["kind"], r["ovf"]) for r in results],
                         [("BIT_PERFECT", "PCM", "0"), ("OVERRUN", "NONE", "10")], out[-2000:])

    def test_overflow_in_the_last_block(self):
        # The main loop stalls inside the file's LAST block, 10 packets
        # find no buffer, then alt 0: the overflow is before units of the
        # file that never came, so the file is OVERRUN (end LAST: its LAST
        # X-sync arrived) and its line counts the overflow
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "last_block.raw")
        B = H.short_config(spec.KIND_PCM, 48000, 16).block_len
        end = START + (len(pcm) // 4 - B // 2) // 6  # half a block before the file's end
        k = end - 10 - HELD_16_2
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 10)] + list(range(k, end)) + ["alt 0"]
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["end"], r["ovf"]) for r in results], [("OVERRUN", "LAST", "10")], out[-2000:])
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("alt", "10")])

    def test_lost_at_a_file_boundary(self):
        # Two gapless files, the boundary at a packet's edge, and 3
        # microframes without a packet right there: the loss stands before
        # the second file's first frame, so it is on the second file's line
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, "lost_boundary_1.raw")
        path = os.path.join(self.tmp.name, "lost_boundary_2.raw")
        with open(path, "wb") as f:
            f.write(pcm + pcm)
        full, rest = divmod(len(pcm), 24)
        packets = list(range(START, START + full)) + ["len %d" % rest, START + full, "len 24"]
        packets += list(range(START + full + 4, START + 2 * full + 40))  # 3 microframes without a packet
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["lost"]) for r in results], [("BIT_PERFECT", "0"), ("BIT_PERFECT", "3")], out[-2000:])

    def cut_then_file(self, name):
        """The short file cut at the end of block 26, its first tail block,
        before its LAST block, then the whole file again: the data file and
        the packets of the cut file."""
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, name + "_1.raw")
        B = H.short_config(spec.KIND_PCM, 48000, 16).block_len
        cut = 27 * B  # frames, a packet's edge
        path = os.path.join(self.tmp.name, name + "_2.raw")
        with open(path, "wb") as f:
            f.write(pcm[:4 * cut] + pcm)
        return pcm, path, cut // 6

    def test_overflow_at_a_cut(self):
        # A file cut in its tail, at a packet's edge, then the file again,
        # whose first 10 packets find no buffer: the first file did not
        # complete its LAST block, so it holds the units up to the next
        # file's first X-sync seen, and the overflow before them (12.6)
        pcm, path, full = self.cut_then_file("ovf_cut")
        k = START + full - HELD_16_2
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 10)] + list(range(k, START + full))
        packets += list(range(START + full, START + full + 10))
        packets += list(range(START + full + 10, START + full + len(pcm) // 24 + 40))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["end"], r["ovf"]) for r in results],
                         [("OVERRUN", "BACK", "10"), ("BIT_PERFECT", "LAST", "0")], out[-2000:])

    def test_lost_at_a_cut(self):
        # The same cut, the next file whole, and 3 microframes without a
        # packet right at the cut: the loss stands before the next file's
        # first X-sync, which the cut file holds, so it is on its line
        pcm, path, full = self.cut_then_file("lost_cut")
        packets = list(range(START, START + full)) + list(range(START + full + 3, START + full + len(pcm) // 24 + 43))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["end"], r["lost"]) for r in results],
                         [("BIT_PERFECT", "BACK", "3"), ("BIT_PERFECT", "LAST", "0")], out[-2000:])

    def test_overflow_at_a_count_cut(self):
        # A file whose lead-in R-header spells 15 blocks with a valid CRC,
        # its LAST X-sync at block 15, then the file again, gapless, whose
        # first packet finds no buffer: the first file is not complete (its
        # first R-header's count is not its LAST block's index + 1), so it
        # holds the overflow at the next file's first X-sync seen; with its
        # own count it is complete and the next file takes it (12.6)
        from tests.test_bpc1_checker import with_count
        cfg = H.Config(kind=spec.KIND_PCM, rate=48000, depth=16, lead_blocks=10, body_blocks=4, tail_blocks=2)
        w = H.pcm_words(cfg)
        for name, first, want in (("count 15", with_count(w, cfg, 8, 15), [("NOT_BIT_PERFECT", "1"), ("BIT_PERFECT", "0")]),
                                  ("its own count", w, [("BIT_PERFECT", "0"), ("OVERRUN", "1")])):
            with self.subTest(name):
                one, two = (first >> 16).astype("<i2").tobytes(), (w >> 16).astype("<i2").tobytes()
                path = os.path.join(self.tmp.name, "count_cut_%s.raw" % name.replace(" ", "_"))
                with open(path, "wb") as f:
                    f.write(one + two)
                full, rest = divmod(len(one), 24)
                self.assertEqual(rest, 16)
                k = START + full - HELD_16_2 + 1
                packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 1)] + list(range(k, START + full))
                packets += ["len %d" % rest, START + full, "len 24", START + full + 1]
                packets += list(range(START + full + 2, START + 2 * full + 40))
                stops, results, out = self.run_tool(packets, path, 1)
                self.assertEqual([(r["verdict"], r["ovf"]) for r in results], want, out[-2000:])

    def test_lost_at_the_end_of_a_cut_file(self):
        # A file that stops in its tail, after its first tail X-sync, and a
        # LATE hole of 2 after its last packet: the file is cut at the
        # stream's end and holds what came there, so its line counts the
        # loss (12.6)
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, "cut_end_1.raw")
        n = 107094  # frames: 600 after block 26's tail X-sync, a packet's edge
        path = os.path.join(self.tmp.name, "cut_end_2.raw")
        with open(path, "wb") as f:
            f.write(pcm[:4 * n])
        packets = list(range(START, START + n // 6)) + ["parts 1 2 late", "idle 600"]
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["end"], r["lost"]) for r in results], [("BIT_PERFECT", "STOP", "2")], out[-2000:])
        self.assertEqual([s["lost"] for s in stops], ["2"])

    def test_lost_in_allowed_silence(self):
        # A file whose LAST X-sync was lost, so its line waits for the
        # stop, then allowed silence of 1.5 blocks that runs past its
        # extent, with a hole of 3 microframes after the extent: the loss
        # touched no result (the silence gave none), so it is on the stream
        # stop line only
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, "silence_lost_1.raw")
        B = H.short_config(spec.KIND_PCM, 48000, 16).block_len
        x = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2).copy()
        x[len(x) - B:len(x) - B + 8] = 0  # the LAST X-sync
        path = os.path.join(self.tmp.name, "silence_lost_2.raw")
        with open(path, "wb") as f:
            f.write(x.tobytes())
        full = len(pcm) // 24 + 1
        pad = (B + B // 2) // 6  # packets of silence
        k = START + full + pad * 3 // 4  # the hole: 1.125 blocks after the file, past its extent
        packets = ["silence on"] + list(range(START, k)) + list(range(k + 3, START + full + pad + 3))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["end"], r["lost"]) for r in results], [("BIT_PERFECT", "STOP", "0")], out[-2000:])
        self.assertEqual([s["lost"] for s in stops], ["3"])

    def test_overflow_before_a_file(self):
        # Silence, then a file whose extent starts a block before its first
        # frame (12.6): 10 packets of silence find no buffer, so that the
        # overflow is told one frame before the extent, in the stretch of
        # unknown signal, which is OVERRUN and takes the count; the file is
        # judged
        pcm, _ = self.bpc1(None, 16, 1, "<i2", 16, "after_silence_1.raw")
        B = H.short_config(spec.KIND_PCM, 48000, 16).block_len
        path = os.path.join(self.tmp.name, "after_silence_2.raw")
        with open(path, "wb") as f:
            # packet 200 starts a frame before the extent as received
            # (after the 10 packets lost)
            f.write(bytes(4 * (6 * 200 + 1 + B)) + pcm)
        k = START + 190 - HELD_16_2
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 10)] + list(range(k, START + 200))
        packets += list(range(START + 200, START + 200 + (B + len(pcm) // 4) // 6 + 40))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["ovf"]) for r in results], [("OVERRUN", "10"), ("BIT_PERFECT", "0")],
                         out[-2000:])

    def test_overflow_with_the_pause(self):
        # u2ps as it is: a NO_BUFFER hole reported with the pause, before
        # the packet after it, while the main loop does not run, counts in
        # the stream the pause ended
        packets = list(range(START, START + 400)) + ["hold %d" % HELD_16_2] + list(range(START + 400, START + 418))
        packets += ["nobuf 5", START + 920] + list(range(START + 921, START + 1100))
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("pause", "5"), ("idle", "0")], out[-2000:])
        self.assertEqual([r["verdict"] for r in results], ["OVERRUN", "UNKNOWN_SIGNAL"])

    # Segments: pause, rate change, alt change, reset

    def test_idle_with_packets_not_read(self):
        # A main loop slower than the backlog takes to read (2000 packets,
        # 12.5 ms a pass, 256 packets a pass): the stream stops for idle
        # only once every packet is read
        packets = list(range(START, START + 100)) + ["hold 2000"] + list(range(START + 100, START + 2100))
        packets += ["slow 100", "idle 1000"]
        stops, _, out = self.run_tool(packets, big=True)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("idle", "2100")], out[-2000:])
        self.check_all_read(stops[0])

    def test_rate_change_answered_before_the_backlog_is_read(self):
        # The host's SET CUR is answered in the pass that takes it; the
        # packets of the old rate are read over the next passes, at most 256
        # in one
        packets = list(range(START, START + 100)) + ["hold 2000"] + list(range(START + 100, START + 2100))
        packets += ["rate 96000"] + list(range(START + 2500, START + 2900))
        stops, results, out = self.run_tool(packets, big=True)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("rate", "2100"), ("idle", "400")], out[-2000:])
        starts = [fields(l)["rate"] for l in out.splitlines() if " stream start " in l]
        self.assertEqual(starts, ["48000", "96000"])
        # the old stream's result lines keep its rate
        self.assertEqual(sorted(set((r["stream"], r["rate"]) for r in results)), [("1", "48000"), ("2", "96000")])
        self.check_all_read(stops[0])
        lines = out.splitlines()
        at_rate = next(i for i, l in enumerate(lines) if l.endswith(" rate 96000"))
        at_stop = next(i for i, l in enumerate(lines) if " stream stop " in l)
        self.assertLess(at_rate, at_stop)

    def test_rate_change_after_a_pause_not_read(self):
        # A pause, then a rate change, while the main loop has not reached
        # the segment after the pause: that segment came at the old rate,
        # and only the packets after the change at the new one
        packets = list(range(START, START + 100)) + ["hold 10"] + list(range(START + 100, START + 105))
        packets += list(range(START + 600, START + 605)) + ["rate 96000"] + list(range(START + 1000, START + 1300))
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("pause", "105"), ("rate", "5"), ("idle", "300")],
                         out[-2000:])
        starts = [fields(l)["rate"] for l in out.splitlines() if " stream start " in l]
        self.assertEqual(starts, ["48000", "48000", "96000"])

    def test_segments_beyond_the_counters(self):
        # More pauses than the interrupt keeps segment counters for, all
        # while the main loop does not run: the boundaries it cannot keep
        # are lost events, a USB fault
        packets = list(range(START, START + 100)) + ["hold 20"]
        for i in range(10):
            packets += list(range(START + 600 * (i + 1), START + 600 * (i + 1) + 2))
        packets += list(range(START + 6602, START + 6800))
        stops, _, out = self.run_tool(packets, usb_fault=1)
        counters = [fields(l) for l in out.splitlines() if " usb counters " in l]
        self.assertEqual(counters[-1]["ev_lost"], "3", out[-2000:])
        # the first stream and 7 more held; the last pause, after the hold,
        # begins one more
        self.assertEqual(len(stops), 9, out[-2000:])
        # every packet is a stream's, or an overflow: the 6 of no segment,
        # in the last stream held, which stops for idle before that pause
        # ends
        self.assertEqual(sum(int(s["pkts"]) + int(s["ovf"]) for s in stops), 100 + 20 + 198)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops[-2:]], [("idle", "6"), ("idle", "0")], out[-2000:])

    def test_zero_length_packets_of_no_segment(self):
        # As above, a zero-length packet after the first 2 segment starts
        # lost: those packets are no segment's, so they count in none, not
        # as missed microframes of the newest segment, which the main loop
        # stops reading before them; the 4 others of no segment count as
        # overflows with the last one, which found no buffer
        packets = list(range(START, START + 100)) + ["hold 20"]
        for i in range(10):
            b = START + 600 * (i + 1)
            packets += [b, "zlp %d" % (b + 1) if i in (7, 8) else b + 1]
        packets += list(range(START + 6602, START + 6800))
        stops, _, out = self.run_tool(packets, usb_fault=1)
        self.assertEqual(len(stops), 9, out[-2000:])
        self.assertEqual(sum(int(s["pkts"]) + int(s["ovf"]) for s in stops), 100 + 18 + 198)
        self.assertEqual([(s["lost"], s["missed"], s["holes"]) for s in stops], [("0", "0", "0")] * 9, out[-2000:])
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops[-2:]], [("idle", "4"), ("idle", "0")], out[-2000:])

    def test_zero_length_packets_between_runs(self):
        # u2ps counts zero-length packets as packets, so 450 of them make no
        # pause and no long gap: the stream goes on, and they are one hole
        # of 450 missed microframes
        packets = list(range(START, START + 400)) + ["zlp %d" % p for p in range(START + 400, START + 850)]
        packets += list(range(START + 850, START + 1250))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"], s["lost"], s["holes"], s["maxhole"]) for s in stops],
                         [("idle", "800", "450", "1", "450")], out[-2000:])

    def test_zero_length_packets_then_pause(self):
        # Zero-length packets, then nothing for 60 ms: the stream stops for
        # idle 50 ms after the last packet of any length, and the next run
        # is a new one
        packets = list(range(START, START + 400)) + ["zlp %d" % p for p in range(START + 400, START + 450)]
        packets += list(range(START + 950, START + 1350))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"], s["lost"]) for s in stops], [("idle", "400", "50"), ("idle", "400", "0")],
                         out[-2000:])

    def test_three_segments_in_one_pass(self):
        # Two pauses while the main loop does not run: every segment gets
        # its stream, read in full
        packets = list(range(START, START + 100)) + ["hold 15"] + list(range(START + 100, START + 105))
        packets += list(range(START + 600, START + 605)) + list(range(START + 1100, START + 1105))
        packets += list(range(START + 1105, START + 1400))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("pause", "105"), ("pause", "5"), ("idle", "300")],
                         out[-2000:])
        for stop in stops:
            self.check_all_read(stop)

    def test_set_configuration(self):
        # SET_CONFIGURATION 1 while configured sets alt 0: the stream stops
        # for that, and the host can select the alt setting again
        packets = list(range(START, START + 200)) + ["setconfig", "alt 1"] + list(range(START + 500, START + 700))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("alt", "200"), ("idle", "200")], out[-2000:])
        # only then, not at the enumeration's SET_CONFIGURATION
        self.assertEqual(out.count(" alt 0\n"), 1)

    def rate_as_the_host_reads_it(self, packets, expected, usb_fault=0):
        """The rate of each stream that starts is the one GET CUR read
        before it: what u2ps reports to the host"""
        stops, _, out = self.run_tool(packets, usb_fault=usb_fault)
        host = None
        starts = []
        for line in out.splitlines():
            if " get_cur " in line:
                host = line.split()[-1]
            elif " stream start " in line:
                starts.append((fields(line)["rate"], host))
        self.assertEqual(starts, [(expected, expected)], out[-3000:])

    def test_rate_answered(self):
        # The main loop answers the deferred SET CUR: both take the new rate
        self.rate_as_the_host_reads_it(["alt 0", "ratenowait 96000", "idle 8", "getcur", "alt 1"]
                                       + list(range(START + 500, START + 700)), "96000")

    def test_rate_cancelled_by_a_reset(self):
        # A bus reset before the main loop answers the deferred SET CUR:
        # u2ps keeps the old rate, and so does the stream
        self.rate_as_the_host_reads_it(["ratenowait 96000", "reset", "getcur"] + list(range(START + 500, START + 700)),
                                       "48000")

    def test_rate_cancelled_by_a_setup(self):
        # A new SETUP (GET CUR) before the main loop answers it: the same
        self.rate_as_the_host_reads_it(["alt 0", "ratenowait 96000", "getcur", "idle 8", "alt 1"]
                                       + list(range(START + 500, START + 700)), "48000")

    def test_rate_with_the_event_queue_full(self):
        # No room for the event that defers the SET CUR (a lost event, a
        # USB fault): u2ps must not take a rate the stream never hears of
        self.rate_as_the_host_reads_it(["alt 0", "evflood 31", "ratenowait 96000", "getcur", "idle 8", "alt 1"]
                                       + list(range(START + 500, START + 700)), "48000", usb_fault=1)

    def test_rate_change_without_packets_after(self):
        # The host sets another rate and sends nothing at it yet: the stream
        # ends for the rate, not for idle
        packets = list(range(START, START + 200)) + ["rate 96000", "idle 500"]
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("rate", "200")], out[-2000:])

    def test_many_streams(self):
        # More streams than the interrupt keeps counters for at once, one
        # after the other: each its own
        packets = []
        for i in range(12):
            packets += list(range(START + 1000 * i, START + 1000 * i + 100))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("idle", "100")] * 12, out[-2000:])

    def test_part_frame_not_kept(self):
        # A stream that ends in the middle of a frame (16/2 packets of 23
        # bytes): the next stream starts on a whole frame, so a file in it
        # is bit-perfect
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "short_part_end.raw")
        with open(path, "wb") as f:
            f.write(bytes(69) + pcm)
        n = len(pcm) // 24
        packets = ["len 23"] + list(range(START, START + 3)) + ["len 24"] + list(range(START + 600, START + 600 + n + 40))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual(len(stops), 2, out[-2000:])
        self.assertIn("BIT_PERFECT", [r["verdict"] for r in results if r["stream"] == stops[1]["id"]], out[-2000:])

    def test_open_refused(self):
        # The port refused the class memory: a USB fault
        _, _, out = self.run_tool(["openfault"], usb_fault=1)
        self.assertIn(" usb fault open reason=", out)

    def test_pause(self):
        # More than STREAM_IDLE_MS (400 microframes) without a packet: two
        # streams, nothing lost
        packets = list(range(START, START + 800)) + list(range(START + 1300, START + 2100))
        stops, _, out = self.run_tool(packets)
        self.assertEqual(len(stops), 2, out[-2000:])
        self.assertEqual([s["lost"] for s in stops], ["0", "0"])
        self.assertEqual([s["pkts"] for s in stops], ["800", "800"])

    def check_all_read(self, stop, read=True):
        """Every frame of a stream of silence reached the checker (read), or
        some did not: the frames it took, all outside every file, against
        the frames that arrived."""
        if read:
            self.assertEqual(stop["outside"], stop["frames"])
        else:
            self.assertLess(int(stop["outside"]), int(stop["frames"]))

    def test_idle_at_the_pause_boundary(self):
        # 400 microframes without a packet: not a pause for u2ps (more than
        # U2PS_GAP_PAUSE), but the stream's idle time (STREAM_IDLE_MS): the
        # main loop stops the stream, and the next packet begins a new one
        packets = list(range(START, START + 800)) + list(range(START + 1200, START + 2000))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"], s["lost"]) for s in stops], [("idle", "800", "0"), ("idle", "800", "0")],
                         out[-2000:])

    def test_pause_with_packets_not_read(self):
        # A pause while the main loop holds packets of the stream before it:
        # those go to the old stream's checker before the new stream starts
        packets = list(range(START, START + 800)) + ["hold %d" % HELD_16_2] + list(range(START + 800, START + 810))
        packets += list(range(START + 1300, START + 1310)) + list(range(START + 1310, START + 2100))
        stops, results, out = self.run_tool(packets)
        self.assertEqual(len(stops), 2, out[-2000:])
        self.assertEqual([(s["reason"], s["pkts"], s["lost"], s["ovf"]) for s in stops],
                         [("pause", "810", "0", "0"), ("idle", "800", "0", "0")])
        for stop in stops:
            self.check_all_read(stop)

    def test_rate_change_reads_first(self):
        # The host sets another rate while packets wait: they go to the old
        # rate's checker first
        packets = list(range(START, START + 800)) + ["hold 15"] + list(range(START + 800, START + 815))
        packets += ["rate 96000"] + list(range(START + 900, START + 1300))
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("rate", "815"), ("idle", "400")], out[-2000:])
        self.check_all_read(stops[0])

    def test_alt_change_reads_packets_first(self):
        # The host selects another alt setting while packets wait: they are
        # checked before the new stream opens, which then starts with every
        # buffer free
        packets = list(range(START, START + 800)) + ["hold 15"] + list(range(START + 800, START + 815))
        packets += ["alt 2"] + ["len 48"] + list(range(START + 900, START + 1300))
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"], s["ovf"]) for s in stops], [("alt", "815", "0"), ("idle", "400", "0")],
                         out[-2000:])
        self.check_all_read(stops[0])
        self.check_all_read(stops[1])
        # stream 1's lines name its own alt setting and slots
        self.assertEqual(sorted(set((r["stream"], r["alt"], r["slot"]) for r in results)), [("1", "1", "2"), ("2", "2", "4")])

    def test_alt_change_with_nothing_to_read(self):
        # No packet waits: the new stream opens at once, and SET_INTERFACE
        # is answered in the next microframe
        packets = list(range(START, START + 200)) + ["alt 2", "len 48"] + list(range(START + 300, START + 500))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([s["reason"] for s in stops], ["alt", "idle"], out[-2000:])
        waits = [int(l.split()[-1]) for l in out.splitlines() if " answered after " in l]
        self.assertEqual(waits[1:], [1])

    def stall_at_end(self, ending, big=False, slow=0, file=True, usb_fault=0):
        """A stream whose last packets the main loop holds when the host
        ends it: the BPC1 file of 16/2 (or silence), the last HELD_16_2
        packets held (and with big, 2000), then the ending."""
        held = 2000 if big else HELD_16_2
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "short_end.raw")
        n = len(pcm) // 24 + 2
        packets = list(range(START, START + n - held)) + ["hold %d" % held] + list(range(START + n - held, START + n))
        packets += (["slow %d" % slow] if slow else []) + ending
        return self.run_tool(packets, path if file else None, 1, big=big, usb_fault=usb_fault)

    def test_file_end_held_then_alt_0(self):
        # The file's last blocks, its body's end included, are in the 2000
        # packets held at alt 0: checked after the stop, and the file is
        # bit-perfect, not cut
        stops, results, out = self.stall_at_end(["alt 0", "idle 600"], big=True)
        self.assertEqual([(s["reason"], s["ovf"], s["lost"]) for s in stops], [("alt", "0", "0")], out[-2000:])
        self.assertEqual([r["verdict"] for r in results if r["verdict"] != "UNKNOWN_SIGNAL"], ["BIT_PERFECT"])

    def test_file_end_held_then_new_stream(self):
        # Alt 0, then a new stream at once: the held packets are checked
        # before the new stream opens, the file is bit-perfect, and the
        # host's SET_INTERFACE is answered within 50 ms
        stops, results, out = self.stall_at_end(["alt 0", "alt 1"] + list(range(START + 30000, START + 30400)), big=True)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("alt", "0"), ("idle", "0")], out[-2000:])
        self.assertEqual([r["verdict"] for r in results if r["stream"] == "1" and r["verdict"] != "UNKNOWN_SIGNAL"],
                         ["BIT_PERFECT"])

    def test_backlog_cut_by_new_stream(self):
        # 2000 packets of silence held, a slow main loop (4 ms a pass, 256
        # packets), alt 0 and a new stream at once: what is not read when
        # the new stream must open counts as overflow at its place, so the
        # stretch is OVERRUN; the new stream starts cleanly, and
        # SET_INTERFACE is answered within u2ps's 30 ms hold
        stops, results, out = self.stall_at_end(["alt 0", "alt 1", "slow 0"] + list(range(START + 30000, START + 30400)),
                                                big=True, slow=32, file=False)
        self.assertEqual([s["reason"] for s in stops], ["alt", "idle"], out[-2000:])
        self.assertGreater(int(stops[0]["ovf"]), 0)
        self.assertEqual((stops[1]["ovf"], stops[1]["lost"], stops[1]["pkts"]), ("0", "0", "400"))
        self.assertEqual([r["verdict"] for r in results if r["stream"] == "1"], ["OVERRUN"])
        self.assertEqual(sum(int(r["ovf"]) for r in results if r["stream"] == "1"), int(stops[0]["ovf"]))
        # each packet not read is one overflow
        self.assertEqual(int(stops[0]["ovf"]) * 6, int(stops[0]["frames"]) - int(stops[0]["outside"]))
        waits = [int(l.split()[-1]) for l in out.splitlines() if " answered after " in l]
        self.assertTrue(waits and max(waits) <= 30 * 8, waits)
        self.assertNotIn("usb fault start", out)

    def test_packet_queued_after_the_cut(self):
        # Two packets the stack never lent come right after the cut (a
        # fault): the glue lights the fault and drops them before the open,
        # with their count, so no buffer goes back to the stack that it did
        # not lend (run_tool() checks release_errors=0 and nothing held).
        # The interrupt counted them in the new stream's segment: 402
        # packets, 12 frames not read
        stops, results, out = self.stall_at_end(["stray 2", "alt 0", "alt 1", "slow 0"] + list(range(START + 30000, START + 30400)),
                                                big=True, slow=32, file=False, usb_fault=1)
        self.assertRegex(out, r"\n\d+ usb fault queue not empty dropped=2\n", out[-2000:])
        self.assertEqual([s["reason"] for s in stops], ["alt", "idle"], out[-2000:])
        self.assertEqual((stops[1]["ovf"], stops[1]["lost"], stops[1]["pkts"], stops[1]["frames"]), ("0", "0", "402", "2412"))

    def test_backlog_with_zero_length_packets_cut(self):
        # The same with every other packet zero-length: only the packets
        # with audio count as overflows when cut
        packets = list(range(START, START + 100)) + ["hold 2000"]
        packets += ["zlp %d" % p if p % 2 else p for p in range(START + 100, START + 2100)]
        packets += ["slow 32", "alt 0", "alt 1", "slow 0"] + list(range(START + 30000, START + 30400))
        stops, results, out = self.run_tool(packets, big=True)
        self.assertGreater(int(stops[0]["ovf"]), 0, out[-2000:])
        self.assertEqual(int(stops[0]["ovf"]) * 6, int(stops[0]["frames"]) - int(stops[0]["outside"]))

    def test_reset_while_checking(self):
        # A bus reset while the packets of a stopped stream are still being
        # checked: they are still checked, and the stream keeps the reason
        # it ended for (the first end); a main loop back to speed reads
        # them all before the next stream's open
        stops, results, out = self.stall_at_end(["alt 0", "reset", "slow 0", "alt 1"] + list(range(START + 30000, START + 30400)),
                                                big=True, slow=32, file=False)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("alt", "0"), ("idle", "0")], out[-2000:])
        self.check_all_read(stops[0])
        # the main loop running only its USB task from the reset to the open
        # (a stream task too slow for the start's wait, START_CHECK_MS): the
        # open cuts them, as overflows
        stops, results, out = self.stall_at_end(["alt 0", "waitonly 100000", "reset", "alt 1", "waitonly 0", "slow 0"]
                                                + list(range(START + 30000, START + 30400)), big=True, slow=32, file=False)
        self.assertEqual([s["reason"] for s in stops], ["alt", "idle"], out[-2000:])
        self.assertGreater(int(stops[0]["ovf"]), 0)
        self.assertEqual(int(stops[0]["ovf"]) * 6, int(stops[0]["frames"]) - int(stops[0]["outside"]))

    def test_start_cancelled_while_checking(self):
        # The host selects alt 1 and, before the glue opens it, alt 0 or
        # resets the bus: u2ps cancels the start, and the glue opens
        # nothing (no fault line)
        for cancel in (["alt 0"], ["reset"], ["setconfig"], ["unplug", "plug"], ["formats 16/2,24/3"]):
            with self.subTest(cancel=cancel):
                stops, results, out = self.stall_at_end(["alt 0", "altnowait 1"] + cancel + ["slow 0", "idle 2000"],
                                                        big=True, slow=32, file=False)
                self.assertNotIn("usb fault start", out)

    def segments_held(self, ending, alt=1, length=24, run=6):
        """Three runs of `run` packets, 500 microframes apart (two pauses),
        all while the main loop does not run (within the buffers it can
        hold), then the ending: three segments of the stream wait."""
        packets = ["len %d" % length] + list(range(START, START + 100)) + ["hold %d" % (3 * run)]
        for i in range(3):
            packets += list(range(START + 100 + 500 * i, START + 100 + run + 500 * i))
        return self.run_tool(packets + ending, None, alt)

    def test_segments_held_then_alt_0(self):
        # Every held segment is its own stream, read in full at its own alt
        # setting and slots, ended by the pause after it, the last by alt 0
        stops, results, out = self.segments_held(["alt 0", "idle 600"])
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("pause", "106"), ("pause", "6"), ("alt", "6")],
                         out[-2000:])
        for stop in stops:
            self.check_all_read(stop)
        self.assertEqual(set((r["alt"], r["slot"], r["res"]) for r in results), {("1", "2", "16")})

    def test_segments_held_then_other_slot(self):
        # The same, then alt 2 (24 bits in 4 bytes): the held segments keep
        # alt 1's 2-byte slots, the new stream has alt 2's
        stops, results, out = self.segments_held(["alt 2", "len 48"] + list(range(START + 3000, START + 3200)))
        self.assertEqual([s["reason"] for s in stops], ["pause", "pause", "alt", "idle"], out[-2000:])
        for stop in stops:
            self.check_all_read(stop)
        self.assertEqual(sorted(set((r["stream"], r["alt"], r["slot"]) for r in results)),
                         [("1", "1", "2"), ("2", "1", "2"), ("3", "1", "2"), ("4", "2", "4")])

    def test_segments_held_then_same_slot(self):
        # Alt 2 (24/4) held, then alt 3 (32/4): the same slots, but the held
        # segments keep alt 2's resolution
        stops, results, out = self.segments_held(["alt 3"] + list(range(START + 3000, START + 3200)), alt=2, length=48, run=3)
        self.assertEqual([s["reason"] for s in stops], ["pause", "pause", "alt", "idle"], out[-2000:])
        self.assertEqual(sorted(set((r["stream"], r["alt"], r["res"]) for r in results)),
                         [("1", "2", "24"), ("2", "2", "24"), ("3", "2", "24"), ("4", "3", "32")])

    def test_segments_cut(self):
        # A backlog over a pause, cut by a new stream: every queued packet
        # goes, its audio packets counted as overflows, and the new stream
        # gets clean buffers (no release error, nothing held at the end)
        packets = list(range(START, START + 100)) + ["hold 2000"] + list(range(START + 100, START + 1100))
        packets += list(range(START + 1600, START + 2600))
        packets += ["slow 32", "alt 0", "alt 1", "slow 0"] + list(range(START + 30000, START + 30400))
        stops, results, out = self.run_tool(packets, big=True)
        self.assertEqual(stops[-1]["reason"], "idle", out[-2000:])
        self.assertEqual((stops[-1]["ovf"], stops[-1]["lost"], stops[-1]["pkts"]), ("0", "0", "400"))
        # the cut stream's packets not read, and those of a segment that
        # never began (2100 packets came)
        unread = sum(int(s["frames"]) - int(s["outside"]) for s in stops[:-1]) // 6
        unread += 2100 - sum(int(s["pkts"]) for s in stops[:-1])
        self.assertGreater(unread, 0)
        self.assertEqual(sum(int(s["ovf"]) for s in stops[:-1]), unread)
        self.assertIn("usb_fault 0", out)
        self.assertNotIn("usb fault", out)

    def test_reset_while_checking_segments(self):
        # A bus reset while a backlog over a pause is still being checked
        # after alt 0, the main loop running only its USB task from then to
        # the open: the stream read then is older than the alt change; the
        # open cuts it all the same, every queued packet counted as
        # overflow, and each segment keeps its reason
        packets = list(range(START, START + 100)) + ["hold 2000"] + list(range(START + 100, START + 1100))
        packets += list(range(START + 1600, START + 2600))
        packets += ["slow 32", "alt 0", "waitonly 100000", "reset", "alt 1", "waitonly 0", "slow 0"]
        packets += list(range(START + 30000, START + 30400))
        stops, results, out = self.run_tool(packets, big=True)
        self.assertNotIn("reset", [s["reason"] for s in stops], out[-2000:])
        self.assertEqual((stops[-1]["reason"], stops[-1]["ovf"]), ("idle", "0"))
        # the cut stream's packets not read, and those of a segment that
        # never began (2100 packets came)
        unread = sum(int(s["frames"]) - int(s["outside"]) for s in stops[:-1]) // 6
        unread += 2100 - sum(int(s["pkts"]) for s in stops[:-1])
        self.assertGreater(unread, 0)
        self.assertEqual(sum(int(s["ovf"]) for s in stops[:-1]), unread)
        self.assertIn("usb_fault 0", out)

    def test_setup_after_the_clock_read(self):
        # The SETUP comes inside the USB task's pass, after it read the
        # clock, with the tick moved on: the start's time is a millisecond
        # after the pass's, and the alt switch still waits for the packets
        # unread (ovf 0)
        packets = list(range(START, START + 800)) + ["hold 15"] + list(range(START + 800, START + 815))
        packets += ["altinpass 2", "len 48"] + list(range(START + 900, START + 1300))
        stops, results, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("alt", "0"), ("idle", "0")], out[-2000:])
        self.check_all_read(stops[0])
        self.assertIn("usb_fault 0", out)

    def test_long_pass_at_the_setup(self):
        # Passes of 10 ms, and a 30 ms one between the host's SET_INTERFACE
        # and the next USB task: the wait counts from the SETUP, so the
        # answer still comes within 50 ms (USB 2.0 9.2.6.4), without
        # u2ps's start timeout
        stops, results, out = self.stall_at_end(["alt 0", "slowonce 240", "alt 1", "slow 0"]
                                                + list(range(START + 30000, START + 30400)),
                                                big=True, slow=80, file=False)
        waits = [int(l.split()[-1]) for l in out.splitlines() if " answered after " in l]
        self.assertTrue(waits and max(waits) <= 50 * 8, waits)
        self.assertNotIn("usb fault start", out)

    def test_backlog_read_after_alt_0(self):
        # The same backlog, alt 0 and no new stream: every packet is
        # checked, in bounded passes, and nothing overflows
        stops, results, out = self.stall_at_end(["alt 0", "idle 2000"], big=True, slow=64)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("alt", "0")], out[-2000:])
        self.assertEqual([r["verdict"] for r in results if r["verdict"] != "UNKNOWN_SIGNAL"], ["BIT_PERFECT"])

    def test_overflow_then_file_end_held(self):
        # Held packets with an overflow before them, then alt 0: every held
        # packet is checked, and the file is OVERRUN by the overflow only
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "short_end_ovf.raw")
        n = len(pcm) // 24
        k = START + n - 40
        packets = list(range(START, k)) + ["hold %d" % (HELD_16_2 + 5)] + list(range(k, k + HELD_16_2 + 5))
        packets += ["idle 1", "hold %d" % HELD_16_2] + list(range(k + HELD_16_2 + 5, START + n + 2)) + ["alt 0", "idle 600"]
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(s["reason"], s["ovf"]) for s in stops], [("alt", "5")], out[-2000:])
        self.assertEqual([r["verdict"] for r in results if r["verdict"] != "UNKNOWN_SIGNAL"], ["OVERRUN"])

    def test_alt_change_with_every_buffer_held(self):
        # Every buffer held when the stream stops: u2ps takes them back, and
        # the next stream gets every buffer again
        packets = list(range(START, START + 100)) + ["hold %d" % HELD_16_2]
        packets += list(range(START + 100, START + 100 + HELD_16_2)) + ["alt 1"]
        packets += ["hold %d" % HELD_16_2] + list(range(START + 300, START + 700))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["ovf"], s["lost"]) for s in stops], [("alt", "0", "0"), ("idle", "0", "0")],
                         out[-2000:])

    def test_alt_zero(self):
        # The host closes the stream (alt 0) with packets waiting
        packets = list(range(START, START + 100)) + ["hold 5"] + list(range(START + 100, START + 105)) + ["alt 0"]
        packets += ["alt 1"] + list(range(START + 300, START + 700))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("alt", "105"), ("idle", "400")], out[-2000:])

    def test_reset_with_every_buffer_held(self):
        # A bus reset with every buffer held: u2ps takes them back, and the
        # stream after the enumeration gets every buffer again
        packets = list(range(START, START + 100)) + ["hold %d" % HELD_16_2]
        packets += list(range(START + 100, START + 100 + HELD_16_2)) + ["reset"]
        packets += ["hold %d" % HELD_16_2] + list(range(START + 300, START + 700))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["ovf"], s["lost"]) for s in stops],
                         [("reset", "0", "0"), ("idle", "0", "0")], out[-2000:])

    def test_reset(self):
        packets = list(range(START, START + 100)) + ["hold 5"] + list(range(START + 100, START + 105)) + ["reset"]
        packets += list(range(START + 300, START + 700))
        stops, _, out = self.run_tool(packets)
        self.assertEqual([(s["reason"], s["pkts"]) for s in stops], [("reset", "105"), ("idle", "400")], out[-2000:])
        self.assertIn(" usb reset", out)

    def reset_with_packets_held(self, ending, before=()):
        """10 packets read, then 50 while the main loop does not run (19
        held, 31 that find no buffer), then the ending, then a new stream of
        100 packets; with `before` ahead of the ending."""
        packets = list(range(START, START + 10)) + ["hold 50"] + list(range(START + 10, START + 60))
        packets += list(before) + ending + ["waitonly 0"] + list(range(START + 5000, START + 5100))
        return self.run_tool(packets)

    RESETS = (["reset"], ["unplug", "plug"], ["formats 16/2,24/3"])

    def test_reset_reads_the_held_packets(self):
        # A bus reset, a VBUS loss or a formats change ends the stream as
        # alt 0 does: the held packets are checked over the next passes,
        # since the glue lends their memory again only at the next open,
        # and the stream stops for the reset once they are read
        for ending in self.RESETS:
            with self.subTest(ending=ending):
                stops, results, out = self.reset_with_packets_held(ending)
                self.assertEqual([(s["reason"], s["pkts"], s["ovf"]) for s in stops],
                                 [("reset", "29", "31"), ("idle", "100", "0")], out[-2000:])
                self.check_all_read(stops[0])
                self.assertEqual([(r["verdict"], r["ovf"]) for r in results if r["stream"] == "1"], [("OVERRUN", "31")])
        # the stop line comes after the reset's; at a formats change, read
        # at once (stream_flush()), before the formats' line and the
        # disconnect
        lines = self.reset_with_packets_held(["reset"])[2].splitlines()
        at_reset = next(i for i, l in enumerate(lines) if l.endswith(" usb reset"))
        at_stop = next(i for i, l in enumerate(lines) if " stream stop " in l)
        self.assertLess(at_reset, at_stop)
        lines = self.reset_with_packets_held(["formats 16/2,24/3"])[2].splitlines()
        at_stop = next(i for i, l in enumerate(lines) if " stream stop " in l)
        at_formats = next(i for i, l in enumerate(lines) if l.endswith(" formats 16/2,24/3 changed=1"))
        self.assertEqual(lines[at_formats + 1].split(" ", 1)[1], "usb disconnected")
        self.assertLess(at_stop, at_formats)

    def test_reset_then_an_open_first(self):
        # The same, the main loop running only its USB task until the new
        # stream opens (a stream task too slow for the start's wait,
        # START_CHECK_MS): after a bus reset the open cuts the stream, as at
        # an alt change, and the 19 packets not read count as overflows with
        # the 31; a VBUS loss and a formats change read them at once
        # (stream_flush()), so nothing is left to cut
        for ending, cut in zip(self.RESETS, (True, False, False)):
            with self.subTest(ending=ending):
                stops, results, out = self.reset_with_packets_held(ending, ["waitonly 100000"])
                ovf = "50" if cut else "31"
                self.assertEqual([(s["reason"], s["pkts"], s["ovf"]) for s in stops],
                                 [("reset", "29", ovf), ("idle", "100", "0")], out[-2000:])
                self.check_all_read(stops[0], read=not cut)
                self.assertEqual([(r["verdict"], r["ovf"]) for r in results if r["stream"] == "1"], [("OVERRUN", ovf)])

    def test_pull_mid_file(self):
        # VBUS goes in the middle of a BPC1 file, 10 packets not read yet
        # (main.c's actions, vbus_act()): they are read at once, and the cut
        # file's result and the stream stop line come before "usb
        # disconnected" and "run start", so the result stays in the run
        # before (main.c)
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "pull.raw")
        k = START + len(pcm) // 24 // 2
        packets = list(range(START, k)) + ["hold 10"] + list(range(k, k + 10)) + ["pull", "plug"]
        packets += list(range(k + 3000, k + 3400))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(s["reason"], s["pkts"], s["ovf"]) for s in stops[:1]], [("reset", str(k + 10 - START), "0")],
                         out[-2000:])
        self.assertEqual([r["kind"] for r in results if r["stream"] == "1"], ["PCM"], out[-2000:])
        lines = out.splitlines()
        first = [i for i, l in enumerate(lines) if (" result " in l and fields(l)["stream"] == "1")
                 or " stream stop id=1 " in l]
        pull = [i for i, l in enumerate(lines) if l.endswith(" usb disconnected") or l.endswith(" run start")]
        self.assertEqual(len(first), 2, out[-2000:])
        self.assertEqual([lines[i].split(" ", 1)[1] for i in pull], ["usb disconnected", "run start"], out[-2000:])
        self.assertLess(max(first), min(pull), out[-2000:])

    def test_pull_with_a_backlog_over_a_pause(self):
        # A pull while 1900 packets of a stream and 50 of the next segment,
        # after a pause, wait: both are read at once, over many passes, and
        # stop before the pull's lines
        p = list(range(START, START + 100)) + ["hold 1960"] + list(range(START + 100, START + 1900))
        p += list(range(START + 2400, START + 2450)) + ["pull", "plug"] + list(range(START + 30000, START + 30400))
        stops, _, out = self.run_tool(p, big=True)
        self.assertEqual([(s["id"], s["reason"], s["pkts"], s["ovf"]) for s in stops],
                         [("1", "pause", "1900", "0"), ("2", "reset", "50", "0"), ("3", "idle", "400", "0")],
                         out[-3000:])
        for stop in stops:
            self.check_all_read(stop)
        lines = out.splitlines()
        at_stop = next(i for i, l in enumerate(lines) if " stream stop id=2 " in l)
        at_pull = next(i for i, l in enumerate(lines) if l.endswith(" usb disconnected"))
        self.assertEqual(lines[at_pull + 1].split(" ", 1)[1], "run start")
        self.assertLess(at_stop, at_pull)

    def test_cut_ends_each_segment(self):
        # A backlog over a pause cut by a new stream, the later segment with
        # a LATE hole of 3 and a NO_BUFFER hole of 2 in it: each segment
        # stops as its own stream, with its own counters
        p = list(range(START, START + 100)) + ["hold 1960"] + list(range(START + 100, START + 1900))
        p += list(range(START + 2400, START + 2450)) + ["late 3", "skip 3"] + list(range(START + 2453, START + 2500))
        p += ["nobuf 2", "skip 2"] + list(range(START + 2502, START + 2562))
        p += ["slow 64", "alt 0", "alt 1", "slow 0"] + list(range(START + 30000, START + 30400))
        stops, results, out = self.run_tool(p, big=True)
        self.assertEqual([(s["id"], s["reason"], s["pkts"], s["lost"], s["missed"], s["holes"], s["maxhole"], s["late"],
                           s["ovf"]) for s in stops],
                         [("1", "pause", "1900", "0", "0", "0", "0", "0", "776"),
                          ("2", "alt", "157", "3", "3", "1", "3", "3", "159"),
                          ("3", "idle", "400", "0", "0", "0", "0", "0", "0")], out[-3000:])
        self.assertEqual([(r["verdict"], r["ovf"]) for r in results if r["stream"] == "1"], [("OVERRUN", "776")])
        # the checker got none of the later segment's frames: no result, its
        # counts on its stop line only (12.6: no unit holds them)
        self.assertEqual([r for r in results if r["stream"] == "2"], [])
        self.check_all_read(stops[1], read=False)
        starts = [fields(l)["id"] for l in out.splitlines() if " stream start " in l]
        self.assertEqual(starts, ["1", "2", "3"])

    def test_cut_without_a_stream(self):
        # Packets of a stream that came while the main loop ran only its
        # USB task (a stream task too slow for the start's wait,
        # START_CHECK_MS), cut by the next stream's open: they are
        # a stream of their own, its packets overflows (19 held, 31 that
        # found no buffer)
        p = list(range(START, START + 100)) + ["alt 0", "idle 100", "waitonly 100000", "alt 1"]
        p += list(range(START + 1000, START + 1050)) + ["alt 2", "waitonly 0", "len 48"] + list(range(START + 5000, START + 5100))
        stops, results, out = self.run_tool(p)
        self.assertEqual([(s["id"], s["reason"], s["pkts"], s["ovf"]) for s in stops],
                         [("1", "alt", "100", "0"), ("2", "alt", "19", "50"), ("3", "idle", "100", "0")], out[-2000:])
        self.assertEqual([r for r in results if r["stream"] == "2"], [])  # no frame read: no result
        self.assertIn(" stream start id=2 rate=48000 alt=1 slot=2 res=16 ", out)
        self.check_all_read(stops[1], read=False)

    def test_segment_starts_lost_across_a_rate_change(self):
        # Every segment slot in use (7 pauses while the main loop is behind),
        # two more pauses and a rate change: the packets after a start the
        # interrupt dropped are no segment's, and count as overflows unread,
        # so the 96 kHz BPC1 file after the rate change, whose first
        # packets are among them, is OVERRUN, never checked at 48 kHz
        cfg = H.short_config(spec.KIND_PCM, 96000, 16)
        pcm = (H.pcm_words(cfg) >> 16).astype("<i2").tobytes()
        path = os.path.join(self.tmp.name, "lost_starts_96k.raw")
        with open(path, "wb") as f:
            f.write(bytes(24 * (100 + 1800 + 9 * 2)) + pcm)
        p = list(range(START, START + 100)) + ["hold 2040"] + list(range(START + 100, START + 1900))
        for i in range(9):
            p += list(range(START + 2500 + 600 * i, START + 2502 + 600 * i))
        p += ["rate 96000"] + list(range(START + 9000, START + 9000 + len(pcm) // 24 + 100))
        stops, results, out = self.run_tool(p, path, 1, big=True, usb_fault=1)
        self.assertNotIn("NOT_BIT_PERFECT", [r["verdict"] for r in results], out[-3000:])
        self.assertEqual([(r["verdict"], r["rate"]) for r in results if r["kind"] == "PCM"], [("OVERRUN", "96000")],
                         out[-3000:])
        # the 2 packets after each of the two pauses lost: overflows of the
        # last segment before them; those after the rate change, until the
        # main loop is back, of the segment after them, before the file's
        # frames received
        self.assertEqual([(s["reason"], s["rate"], s["ovf"]) for s in stops[-2:]],
                         [("rate", "48000.00", "4"), ("idle", "48000.00", "225")], out[-3000:])
        self.assertEqual([r["ovf"] for r in results if r["kind"] == "PCM"], ["225"])
        counters = [fields(l) for l in out.splitlines() if " usb counters " in l]
        self.assertEqual(counters[-1]["ev_lost"], "3", out[-2000:])

    # BPC1 files through the buffers

    def bpc1(self, packets, depth, alt, slot_dtype, shift, name):
        cfg = H.short_config(spec.KIND_PCM, 48000, depth)
        pcm = (H.pcm_words(cfg) >> shift).astype(slot_dtype).tobytes()
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(pcm)
        return pcm, path

    def bpc1_file(self, depth, alt, slot_dtype, shift, holds=()):
        """A short BPC1 file at 48 kHz in alt setting alt's slots, the main
        loop reading the packets in batches of each size in holds, in turn:
        bit-perfect."""
        pcm, path = self.bpc1(None, depth, alt, slot_dtype, shift, "short_%d.raw" % alt)
        n = len(pcm) // (6 * 2 * np.dtype(slot_dtype).itemsize)
        packets, p, i = [], START, 0
        while p < START + n + 40:
            if holds:
                # h packets held, then one after which the main loop reads
                h = holds[i % len(holds)]
                packets += ["hold %d" % h] + list(range(p, p + h + 1))
                p += h + 1
                i += 1
            else:
                packets.append(p)
                p += 1
        stops, results, out = self.run_tool(packets, path, alt)
        verdicts = [r["verdict"] for r in results]
        self.assertIn("BIT_PERFECT", verdicts, out[-2000:])
        self.assertEqual((len(stops), stops[0]["lost"], stops[0]["ovf"]), (1, "0", "0"))

    def test_bpc1_file_16_2(self):
        self.bpc1_file(16, 1, "<i2", 16)

    def test_bpc1_file_24_4(self):
        self.bpc1_file(24, 2, "<i4", 0)

    def test_bpc1_file_index_wrap(self):
        # Batches of every size up to every buffer held, so that the queue's and
        # u2ps's buffer indices wrap at every phase between write and read
        self.bpc1_file(16, 1, "<i2", 16, holds=(1, 3, 7, HELD_16_2 - 1, HELD_16_2, 2, 13))

    def odd_packets(self, alt, slot_bytes, sizes, holds=8):
        """A short BPC1 file in alt setting alt's slots (3 or 4 bytes, 24
        bits), in packets of the given sizes in turn, read in batches of
        holds packets: bit-perfect."""
        cfg = H.short_config(spec.KIND_PCM, 48000, 24)
        w = H.pcm_words(cfg).astype("<i4")
        pcm = w.view(np.uint8).reshape(-1, 4)[:, 4 - slot_bytes:4].tobytes()
        path = os.path.join(self.tmp.name, "short_odd_%d.raw" % alt)
        with open(path, "wb") as f:
            f.write(pcm)
        packets, p, left = [], START, len(pcm) + 40 * max(sizes)
        while left > 0:
            n = sizes[(p - START) % len(sizes)]
            if (p - START) % holds == 0:
                packets.append("hold %d" % (holds - 1))
            packets += ["len %d" % n, p]
            left -= n
            p += 1
        stops, results, out = self.run_tool(packets, path, alt)
        self.assertIn("BIT_PERFECT", [r["verdict"] for r in results], out[-2000:])
        self.assertEqual((len(stops), stops[0]["lost"], stops[0]["ovf"]), (1, "0", "0"))

    def test_odd_packets_24_3(self):
        # 3-byte slots in packets of 5 and 6 frames (30 and 36 bytes), as at
        # 44.1 kHz: lengths that are not a multiple of 4
        self.odd_packets(4, 3, (30, 36))

    def test_frames_across_packets_16_2(self):
        # Packets that end in the middle of a frame (23 and 25 bytes of
        # 4-byte frames): the stream is the bytes in order, as UAC2 requires
        # no host to send, and as the packets came
        cfg = H.short_config(spec.KIND_PCM, 48000, 16)
        pcm = (H.pcm_words(cfg) >> 16).astype("<i2").tobytes()
        path = os.path.join(self.tmp.name, "short_part.raw")
        with open(path, "wb") as f:
            f.write(pcm)
        packets, p, left = [], START, len(pcm) + 40 * 25
        while left > 0:
            n = 23 if (p - START) % 2 == 0 else 25
            packets += ["len %d" % n, p]
            left -= n
            p += 1
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertIn("BIT_PERFECT", [r["verdict"] for r in results], out[-2000:])

    def test_frames_across_packets_24_3_short(self):
        # 6-byte frames in packets of 7, 2 and 3 bytes: a packet shorter
        # than the rest of the frame before it
        self.odd_packets(4, 3, (7, 2, 3))

    def test_frames_across_packets_24_4(self):
        # 8-byte frames in packets of 45 and 51 bytes: frames that start off
        # a word boundary
        self.odd_packets(2, 4, (45, 51))

    def test_overflow_in_a_file(self):
        # Every buffer held in the middle of a file: the packets that found
        # none make the file OVERRUN, and its result line counts them, not
        # the line of the silence after it
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "short_ovf.raw")
        n = len(pcm) // 24
        k = START + n // 2
        packets = list(range(START, k)) + ["hold 30"] + list(range(k, START + n + 3000))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["ovf"], r["lost"]) for r in results],
                         [("OVERRUN", "11", "0"), ("UNKNOWN_SIGNAL", "0", "0")], out[-2000:])
        self.assertEqual((stops[0]["ovf"], stops[0]["lost"]), ("11", "0"))

    def test_overflow_before_a_files_first_frame(self):
        # An overflow right before the packet with a file's first frame,
        # after 500 packets of silence: within the file's extent, which
        # reaches one block before its block 0 (12.6), so the file is
        # OVERRUN and its line counts it
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "short_first.raw")
        with open(path, "wb") as f:
            f.write(bytes(24 * 500) + pcm)
        n = len(pcm) // 24
        packets = list(range(START, START + 500)) + ["nobuf 3"] + list(range(START + 503, START + 503 + n + 3000))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["ovf"]) for r in results], [("OVERRUN", "3"), ("UNKNOWN_SIGNAL", "0")],
                         out[-2000:])

    def test_lost_in_a_file(self):
        # LATE and CORRUPTED microframes in a file: lost, so the file is not
        # bit-perfect, and its result line counts them; one more in the
        # silence after it counts on that stretch's line
        pcm, path = self.bpc1(None, 16, 1, "<i2", 16, "short_lost.raw")
        n = len(pcm) // 24
        k = START + n // 2
        e = START + n + 2000
        packets = list(range(START, k)) + ["late 2", "skip 2"] + list(range(k + 2, k + 100))
        packets += ["crc 1", "skip 1"] + list(range(k + 101, e)) + ["late 1"] + list(range(e + 1, e + 1000))
        stops, results, out = self.run_tool(packets, path, 1)
        self.assertEqual([(r["verdict"], r["lost"], r["ovf"]) for r in results],
                         [("NOT_BIT_PERFECT", "3", "0"), ("UNKNOWN_SIGNAL", "1", "0")], out[-2000:])
        self.assertEqual((stops[0]["late"], stops[0]["missed"]), ("3", "4"))


if __name__ == "__main__":
    unittest.main()
