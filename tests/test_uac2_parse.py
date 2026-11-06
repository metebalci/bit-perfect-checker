# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The BPC1 generator's UAC2 descriptor parser (firmware/bpc1-generator/
uac2_host.c) on the host: the checker's own configuration descriptors for
four format lists (tests/uac2/, read from a checker through Linux sysfs),
the same descriptors made malformed or unplayable, which must be refused
with the right reason, the clock's RANGE reply, and the choice of the alt
setting for a file. Built with the host tool
firmware/bpc1-generator/host/uac2_host_tool.c, with the address and
undefined-behaviour sanitizers where the compiler has them; needs a host C
compiler (gcc or cc); skipped without one."""

import os
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(ROOT, "firmware", "bpc1-generator")
FIXTURES = os.path.join(ROOT, "tests", "uac2")
CC = shutil.which("gcc") or shutil.which("cc")
SOURCES = ["host/uac2_host_tool.c", "uac2_host.c"]

RATES = [44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000]

# The checker's format lists (firmware/README.md, Formats) and their fixtures
LISTS = {
    "16/2,24/4,32/4,24/3": "checker-default.txt",
    "24/3": "checker-24-3.txt",
    "16/2,32/4": "checker-16-2_32-4.txt",
    "24/3,16/2": "checker-24-3_16-2.txt",
}

# wMaxPacketSize per slot: 97 frames (768 kHz, rounded up, plus one) of two
# channels (firmware/app/usb_descriptors.h, UAC2_EP_OUT_SZ)
OUT_SIZE = {2: 388, 3: 582, 4: 776}

CS_INTERFACE = 0x24
ENDPOINT = 0x05
INTERFACE = 0x04


def build(directory, gen):
    exe = os.path.join(directory, "uac2_host_tool")
    base = [CC, "-std=c99", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-I", gen]
    srcs = [os.path.join(gen, s) for s in SOURCES]
    san = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    if subprocess.run(base + san + ["-o", exe] + srcs, capture_output=True).returncode != 0:
        subprocess.run(base + ["-o", exe] + srcs, check=True)
    return exe


def load(name):
    """A fixture as a list of descriptors (bytearrays)."""
    descs = []
    with open(os.path.join(FIXTURES, name)) as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                descs.append(bytearray.fromhex(line))
    return descs


def join(descs, fix_total=True):
    """The descriptors as one configuration descriptor, wTotalLength fixed."""
    b = bytearray().join(descs)
    if fix_total:
        struct.pack_into("<H", b, 2, len(b))
    return bytes(b)


def find(descs, dtype, subtype=None, nth=0):
    """Index of the nth descriptor of a type (and CS subtype)."""
    hits = [i for i, d in enumerate(descs)
            if d[1] == dtype and (subtype is None or d[2] == subtype)]
    return hits[nth]


def data_eps(descs):
    """Indexes of the isochronous OUT data endpoints."""
    return [i for i, d in enumerate(descs) if d[1] == ENDPOINT and not d[2] & 0x80]


def range_reply(subranges):
    b = struct.pack("<H", len(subranges))
    for lo, hi, res in subranges:
        b += struct.pack("<III", lo, hi, res)
    return b


@unittest.skipUnless(CC, "no host C compiler")
class Uac2Parse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build(cls.tmp.name, GEN)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tool(self, data, *args):
        r = subprocess.run([self.exe, *args], input=data.hex(), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return r.stdout.splitlines()

    def parse(self, data):
        return self.tool(data, "parse")

    def refused(self, data, reason):
        self.assertEqual(self.parse(data), [f"refused {reason}"])

    # The checker's descriptors

    def test_checker_format_lists(self):
        for formats, name in LISTS.items():
            with self.subTest(formats=formats):
                out = self.parse(join(load(name), fix_total=False))
                self.assertEqual(out[0], f"ok ac=0 as=1 clock=4 clock_set=1 terminal=1 formats={formats}")
                self.assertEqual(len(out), 1 + len(formats.split(",")))
                for n, (fmt, line) in enumerate(zip(formats.split(","), out[1:]), start=1):
                    res, slot = (int(x) for x in fmt.split("/"))
                    self.assertEqual(line, f"alt {n} slot={slot} res={res} sync=async out=0x01 "
                                           f"out_size={OUT_SIZE[slot]} fb=0x81 fb_size=4 fb_interval=4")

    def test_fixtures_hold_no_strings(self):
        for name in LISTS.values():
            descs = load(name)
            self.assertEqual(descs[0][1], 0x02)
            self.assertNotIn(0x03, [d[1] for d in descs])  # string descriptors
            self.assertEqual(struct.unpack_from("<H", descs[0], 2)[0], len(join(descs, False)))

    def test_other_interfaces_before_and_after(self):
        # A vendor interface before the audio function and after it is skipped
        descs = load(LISTS["16/2,32/4"])
        vendor = [bytearray.fromhex("09 04 02 00 01 ff 00 00 00"), bytearray.fromhex("07 05 82 02 00 02 00")]
        descs = descs[:1] + vendor + descs[1:] + vendor
        descs[0][4] = 3
        self.assertEqual(self.parse(join(descs))[0],
                         "ok ac=0 as=1 clock=4 clock_set=1 terminal=1 formats=16/2,32/4")

    def test_clock_selector_with_one_input(self):
        # Terminal -> selector 9 -> clock source 4: the requests go to the source
        descs = load(LISTS["24/3"])
        it = find(descs, CS_INTERFACE, 0x02)
        descs[it][7] = 9
        selector = bytearray.fromhex("08 24 0b 09 01 04 03 00")
        descs.insert(it, selector)
        hdr = find(descs, CS_INTERFACE, 0x01)
        struct.pack_into("<H", descs[hdr], 6, struct.unpack_from("<H", descs[hdr], 6)[0] + len(selector))
        self.assertTrue(self.parse(join(descs))[0].startswith("ok ac=0 as=1 clock=4 "))
        # Two inputs: which one is used is the device's choice, so it is refused
        descs[it] = bytearray.fromhex("09 24 0b 09 02 04 05 03 00")
        self.refused(join(descs), "clock")

    def test_clock_read_only(self):
        descs = load(LISTS["24/3"])
        cs = find(descs, CS_INTERFACE, 0x0A)
        descs[cs][5] = 0x05  # frequency read-only, validity read-only
        self.assertIn("clock_set=0", self.parse(join(descs))[0])

    def test_adaptive_without_feedback(self):
        # An adaptive endpoint needs no feedback endpoint
        descs = load(LISTS["24/3"])
        ep = data_eps(descs)[0]
        descs[ep][3] = 0x09  # isochronous, adaptive
        fb = find(descs, ENDPOINT, nth=1)
        del descs[fb]
        descs[find(descs, INTERFACE, nth=2)][4] = 1  # bNumEndpoints
        out = self.parse(join(descs))
        self.assertEqual(out[1], "alt 1 slot=3 res=24 sync=adaptive out=0x01 out_size=582 fb=0x00 fb_size=0 "
                                 "fb_interval=0")

    # Malformed descriptors

    def test_length_below_two(self):
        for n in (0, 1):
            for at in (1, 5, 9, -1):
                with self.subTest(bLength=n, at=at):
                    descs = load(LISTS["16/2,24/4,32/4,24/3"])
                    descs[at][0] = n
                    # wTotalLength stays: the parser stops at the bad descriptor
                    self.refused(join(descs, False), "length")
        # A one-byte descriptor between two good ones, wTotalLength counting it
        for at in (1, 5, 9):
            with self.subTest(one_byte_at=at):
                descs = load(LISTS["16/2,24/4,32/4,24/3"])
                descs.insert(at, bytearray([1]))
                self.refused(join(descs), "length")

    def test_total_length_cut(self):
        data = join(load(LISTS["16/2,24/4,32/4,24/3"]), False)
        for n in (len(data) - 1, len(data) - 7, 100, 10):
            with self.subTest(len=n):
                self.refused(data[:n], "cut")
        # A descriptor that reaches past wTotalLength
        descs = load(LISTS["24/3"])
        data = bytearray(join(descs))
        data[-7] = 9  # the last endpoint's bLength
        self.refused(bytes(data), "cut")
        # Too short for a configuration descriptor at all
        for n in range(0, 9):
            with self.subTest(len=n):
                self.refused(data[:n], "cut")

    def test_not_a_configuration(self):
        data = bytearray(join(load(LISTS["24/3"])))
        data[1] = 0x01
        self.refused(bytes(data), "not-config")

    def test_short_for_its_type(self):
        cases = [(INTERFACE, None, 0), (CS_INTERFACE, 0x01, 0), (CS_INTERFACE, 0x0A, 0),
                 (CS_INTERFACE, 0x02, 0), (CS_INTERFACE, 0x01, 1), (CS_INTERFACE, 0x02, 1), (ENDPOINT, None, 0)]
        for dtype, sub, nth in cases:
            with self.subTest(type=dtype, subtype=sub, nth=nth):
                descs = load(LISTS["24/3"])
                i = find(descs, dtype, sub, nth)
                descs[i] = descs[i][:3] if sub is not None else descs[i][:2]
                descs[i][0] = len(descs[i])
                self.refused(join(descs), "length")

    def test_unknown_class(self):
        descs = load(LISTS["16/2,24/4,32/4,24/3"])
        for d in descs:
            if d[1] == INTERFACE:
                d[5] = 0xFF
        self.refused(join(descs), "not-audio")

    def test_uac1(self):
        descs = load(LISTS["24/3"])
        for d in descs:
            if d[1] == INTERFACE:
                d[7] = 0x00  # bInterfaceProtocol: UAC1
        self.refused(join(descs), "uac1")
        descs = load(LISTS["24/3"])
        struct.pack_into("<H", descs[find(descs, CS_INTERFACE, 0x01)], 3, 0x0100)  # bcdADC 1.00
        self.refused(join(descs), "version")

    def test_implicit_feedback(self):
        # Asynchronous OUT without a feedback endpoint
        descs = load(LISTS["24/3"])
        del descs[find(descs, ENDPOINT, nth=1)]
        descs[find(descs, INTERFACE, nth=2)][4] = 1
        self.refused(join(descs), "implicit-feedback")
        # The second endpoint is an implicit feedback data endpoint (usage 2)
        descs = load(LISTS["24/3"])
        descs[find(descs, ENDPOINT, nth=1)][3] = 0x25
        self.refused(join(descs), "implicit-feedback")
        # In one alt of four
        descs = load(LISTS["16/2,24/4,32/4,24/3"])
        del descs[find(descs, ENDPOINT, nth=5)]
        descs[find(descs, INTERFACE, nth=4)][4] = 1
        self.refused(join(descs), "implicit-feedback")

    def test_high_bandwidth(self):
        for extra in (1, 2):
            descs = load(LISTS["16/2,24/4,32/4,24/3"])
            ep = descs[data_eps(descs)[2]]
            struct.pack_into("<H", ep, 4, 776 | extra << 11)
            self.refused(join(descs), "high-bandwidth")

    def test_interval(self):
        for interval in (0, 2, 4):
            with self.subTest(bInterval=interval):
                descs = load(LISTS["16/2,24/4,32/4,24/3"])
                descs[data_eps(descs)[1]][6] = interval
                self.refused(join(descs), "interval")

    def test_channels(self):
        for n in (1, 6, 8):
            with self.subTest(channels=n):
                descs = load(LISTS["16/2,24/4,32/4,24/3"])
                descs[find(descs, CS_INTERFACE, 0x01, nth=2)][10] = n  # AS_GENERAL of alt 2
                self.refused(join(descs), "channels")

    def test_format(self):
        cases = {
            "type": lambda d: d.__setitem__(find(d, CS_INTERFACE, 0x02, nth=1), bytearray.fromhex("06 24 02 02 03 18")),
            "as-type": lambda d: d[find(d, CS_INTERFACE, 0x01, nth=1)].__setitem__(5, 0x02),
            "not-pcm": lambda d: struct.pack_into("<I", d[find(d, CS_INTERFACE, 0x01, nth=1)], 6, 1 << 31),
            "slot": lambda d: d[find(d, CS_INTERFACE, 0x02, nth=1)].__setitem__(4, 5),
            "res": lambda d: d[find(d, CS_INTERFACE, 0x02, nth=1)].__setitem__(5, 25),
        }
        for what, change in cases.items():
            with self.subTest(what):
                descs = load(LISTS["24/3"])
                change(descs)
                self.refused(join(descs), "format")
        # An alt without its format descriptor
        descs = load(LISTS["24/3"])
        del descs[find(descs, CS_INTERFACE, 0x02, nth=1)]
        self.refused(join(descs), "format")

    def test_endpoints(self):
        cases = {
            "in": lambda d: d[data_eps(d)[0]].__setitem__(2, 0x82),
            "bulk": lambda d: d[data_eps(d)[0]].__setitem__(3, 0x02),
            "fb-out": lambda d: d[find(d, ENDPOINT, nth=1)].__setitem__(2, 0x02),
            "fb-size": lambda d: struct.pack_into("<H", d[find(d, ENDPOINT, nth=1)], 4, 3),
        }
        for what, change in cases.items():
            with self.subTest(what):
                descs = load(LISTS["24/3"])
                change(descs)
                self.refused(join(descs), "endpoint")

    def test_no_terminal_no_streaming(self):
        descs = load(LISTS["24/3"])
        struct.pack_into("<H", descs[find(descs, CS_INTERFACE, 0x02)], 4, 0x0301)  # speaker, not USB streaming
        self.refused(join(descs), "no-terminal")
        descs = load(LISTS["24/3"])
        descs[find(descs, CS_INTERFACE, 0x01, nth=1)][3] = 3  # bTerminalLink: the output terminal
        self.refused(join(descs), "no-streaming")
        descs = load(LISTS["24/3"])
        del descs[find(descs, INTERFACE, nth=1)]  # no alt 0
        self.refused(join(descs), "no-streaming")
        descs = load(LISTS["24/3"])
        descs = descs[:find(descs, INTERFACE, nth=2)]  # alt 0 only: nothing links to the terminal
        self.refused(join(descs), "no-streaming")
        descs = load(LISTS["24/3"])
        general = descs[find(descs, CS_INTERFACE, 0x01, nth=1)]
        descs = descs[:find(descs, INTERFACE, nth=2)] + [general]  # linked, but alt 0 only
        self.refused(join(descs), "no-alt")
        descs = load(LISTS["24/3"])
        cs = find(descs, CS_INTERFACE, 0x0A)
        descs[cs][3] = 7  # the terminal's clock 4 no longer exists
        self.refused(join(descs), "clock")

    def test_alts(self):
        descs = load(LISTS["16/2,32/4"])
        descs[find(descs, INTERFACE, nth=3)][3] = 1  # alt 2 numbered 1 again
        self.refused(join(descs), "alt-repeated")
        descs = load(LISTS["24/3"])
        alt = descs[find(descs, INTERFACE, nth=2):]
        for n in range(2, 10):
            more = [bytearray(d) for d in alt]
            more[0][3] = n
            descs += more
        self.refused(join(descs), "too-many-alts")
        descs = descs[:-len(alt)]  # 8 alts: the most
        self.assertTrue(self.parse(join(descs))[0].endswith("formats=" + ",".join(["24/3"] * 8)))

    def test_alts_out_of_order(self):
        # Listed as alt 2, then alt 1: reported in alt setting order
        descs = load(LISTS["16/2,32/4"])
        a1, a2 = find(descs, INTERFACE, nth=2), find(descs, INTERFACE, nth=3)
        descs = descs[:a1] + descs[a2:] + descs[a1:a2]
        out = self.parse(join(descs))
        self.assertTrue(out[0].endswith("formats=16/2,32/4"))
        self.assertTrue(out[1].startswith("alt 1 slot=2 "))

    def test_truncation_and_noise(self):
        # Any prefix and any single-byte change: an answer, never a crash or a
        # read past the end (the sanitizers would show one)
        data = join(load(LISTS["16/2,24/4,32/4,24/3"]), False)
        inputs = [data[:n] for n in range(len(data))]
        rnd = random.Random(1)
        for _ in range(300):
            b = bytearray(data)
            b[rnd.randrange(len(b))] = rnd.randrange(256)
            inputs.append(bytes(b))
        for d in inputs:
            out = self.parse(d)
            self.assertTrue(out and (out[0].startswith("ok ") or out[0].startswith("refused ")), (d.hex(), out))
            if d != data and len(d) < len(data):
                self.assertTrue(out[0].startswith("refused "), d.hex())

    # RANGE

    def test_range_checker(self):
        # The checker's reply: the ten rates as single values (firmware/app/uac2.c)
        self.assertEqual(self.tool(range_reply([(r, r, 0) for r in RATES]), "range"),
                         ["rates " + ",".join(map(str, RATES))])

    def test_range_continuous(self):
        self.assertEqual(self.tool(range_reply([(8000, 768000, 100)]), "range"),
                         ["rates " + ",".join(map(str, RATES))])
        self.assertEqual(self.tool(range_reply([(44100, 96000, 0)]), "range"), ["rates 44100"])
        self.assertEqual(self.tool(range_reply([(32000, 96000, 16000)]), "range"), ["rates 48000,96000"])
        self.assertEqual(self.tool(range_reply([(32000, 32000, 0), (22050, 22050, 0)]), "range"), ["rates"])

    def test_range_short(self):
        full = range_reply([(r, r, 0) for r in RATES])
        self.assertEqual(self.tool(full[:2 + 12 * 3 + 5], "range"), ["rates 44100,48000,88200"])
        for n in (0, 1, 2, 13):
            self.assertEqual(self.tool(full[:n], "range"), ["refused"])
        self.assertEqual(self.tool(range_reply([]), "range"), ["refused"])
        # wNumSubRanges smaller than the reply: only that many
        b = bytearray(full)
        struct.pack_into("<H", b, 0, 2)
        self.assertEqual(self.tool(bytes(b), "range"), ["rates 44100,48000"])

    # The alt setting for a file

    def choose(self, formats, rate, depth, dop=False):
        data = join(load(LISTS[formats]), False)
        return self.tool(data, "choose", str(rate), str(depth), "1" if dop else "0")[0]

    def test_choose(self):
        cases = [
            ("16/2,24/4,32/4,24/3", 48000, 16, False, "alt 1"),
            ("16/2,24/4,32/4,24/3", 48000, 24, False, "alt 4"),
            ("16/2,24/4,32/4,24/3", 768000, 32, False, "alt 3"),
            ("16/2,24/4,32/4,24/3", 176400, 24, True, "alt 4"),
            ("24/3", 44100, 16, False, "alt 1"),
            ("24/3", 768000, 32, False, "none"),
            ("16/2,32/4", 96000, 24, False, "alt 2"),
            ("16/2,32/4", 705600, 24, True, "alt 2"),
            ("24/3,16/2", 768000, 16, False, "alt 2"),
            ("24/3,16/2", 768000, 24, False, "alt 1"),
            ("24/3,16/2", 352800, 24, True, "alt 1"),
        ]
        for formats, rate, depth, dop, want in cases:
            with self.subTest(formats=formats, rate=rate, depth=depth, dop=dop):
                self.assertEqual(self.choose(formats, rate, depth, dop), want)

    def test_choose_dop_slot(self):
        # DoP needs 3 or 4 bytes: a 16/2-only device cannot take it
        descs = load(LISTS["16/2,32/4"])
        descs = descs[:find(descs, INTERFACE, nth=3)]
        data = join(descs)
        self.assertEqual(self.tool(data, "choose", "88200", "16", "0"), ["alt 1"])
        self.assertEqual(self.tool(data, "choose", "88200", "24", "1"), ["none"])

    def test_choose_packet_size(self):
        # A 24/3 alt whose wMaxPacketSize holds 49 frames (384 kHz) but not 97
        descs = load(LISTS["24/3"])
        struct.pack_into("<H", descs[data_eps(descs)[0]], 4, 49 * 6)
        data = join(descs)
        self.assertEqual(self.tool(data, "choose", "384000", "24", "0"), ["alt 1"])
        self.assertEqual(self.tool(data, "choose", "705600", "24", "0"), ["none"])
        struct.pack_into("<H", descs[data_eps(descs)[0]], 4, 49 * 6 - 1)
        self.assertEqual(self.tool(join(descs), "choose", "384000", "24", "0"), ["none"])
        # 44.1 kHz: 5.5125 frames, so 7 at most
        struct.pack_into("<H", descs[data_eps(descs)[0]], 4, 7 * 6)
        self.assertEqual(self.tool(join(descs), "choose", "44100", "24", "0"), ["alt 1"])
        struct.pack_into("<H", descs[data_eps(descs)[0]], 4, 7 * 6 - 1)
        self.assertEqual(self.tool(join(descs), "choose", "44100", "24", "0"), ["none"])


if __name__ == "__main__":
    unittest.main()
