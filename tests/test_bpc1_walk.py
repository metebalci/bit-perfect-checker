# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The frame walker of the BPC1 generator firmware (firmware/bpc1/bpc1_walk.c),
through its host tool, against the BPC1 file generator: the walker's frames
must be the data chunk that fileset.write_file or write_part writes, each
sample re-slotted to the slot size with zero low bytes. DSD files play as
DoP, the marker by the frame's index in the regular file. Needs a host C
compiler; skipped without one. Set BPC1_FULL_SET=1 to compare every file of
the number table as well, by sha256."""

import hashlib
import json
import os
import struct
import subprocess
import tempfile
import unittest

import numpy as np

from checker import readers
from generator import fileset, spec
from generator.signal import Config
from tests import test_bpc1_c

ROOT = test_bpc1_c.ROOT
VECTORS = test_bpc1_c.VECTORS
CC = test_bpc1_c.CC

SHORT = dict(lead_blocks=9, body_blocks=2, tail_blocks=1)   # block 8, an R-header block, in the lead-in
SLOTS = {16: (2, 3, 4), 24: (3, 4), 32: (4,), 1: (3, 4)}    # valid slot sizes per depth (1: DoP)
SPLITS = ("5,6,7", "96,97", "rand:1", "rand:2")
FAULTS = {"": "none", "altered": "altered", "incomplete": "incomplete", "gap": "gap"}


def data_chunk(path):
    """The bytes of a WAV file's data chunk."""
    with open(path, "rb") as f:
        d = f.read()
    pos = 12
    while pos + 8 <= len(d):
        cid, size = d[pos:pos + 4], struct.unpack_from("<I", d, pos + 4)[0]
        if cid == b"data":
            return d[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    raise ValueError(f"{path}: no data chunk")


def reslot(data, nbytes, slot):
    """Samples of nbytes little-endian bytes -> slot bytes, zero low bytes."""
    a = np.frombuffer(data, dtype=np.uint8).reshape(-1, nbytes)
    out = np.zeros((len(a), slot), dtype=np.uint8)
    out[:, slot - nbytes:] = a
    return out.tobytes()


def dop_bytes(dsd, first_frame):
    """DoP samples, 3 bytes little-endian, from DSD bytes (bytes, nch), the
    earliest bit as MSB: frame f, counted from first_frame, carries bytes 2f
    and 2f + 1 of each channel (docs/bpc1.md 9.5)."""
    hi, lo = dsd[0::2].astype(np.uint32), dsd[1::2].astype(np.uint32)
    f = np.arange(first_frame, first_frame + len(hi))
    marker = np.where(f % 2 == 0, 0x05, 0xFA).astype(np.uint32)
    v = (marker[:, None] << 16) | (hi << 8) | lo
    return np.ascontiguousarray(v.astype("<u4")).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


def reference(fspec, slot, out_dir):
    """The bytes a walker sends for fspec at slot size `slot`: the data chunk
    of its file, re-slotted. A DSF or DFF file as DoP."""
    path = fileset.write_file(fspec, out_dir)
    try:
        if fspec.format == "wav":
            nbytes = 3 if fspec.container == "dop" else fspec.cfg.depth // 8
            return reslot(data_chunk(path), nbytes, slot)
        with open(path, "rb") as f:
            data = f.read()
        dsd = (readers.read_dsf if fspec.format == "dsf" else readers.read_dff)(data).data
        return reslot(dop_bytes(dsd, fspec.units[0] // 16), 3, slot)
    finally:
        os.remove(path)


def short_spec(kind, rate, depth):
    """A short file: 9 lead-in blocks (block 8 an R-header block), 2 body
    blocks, 1 tail block; DSD as DoP."""
    cfg = Config(kind=kind, rate=rate, depth=depth, **SHORT)
    container = "dop" if cfg.dsd else "wav"
    return fileset.FileSpec(f"short_{kind}_{rate}_{depth}", container, cfg, (), "short")


def layout(fspec):
    """The walker tool's name for a file layout: kind:depth_code:rate_code:lead:body:tail."""
    c = fspec.cfg
    return f"{c.kind}:{c.depth_code}:{c.rate_code}:{c.lead_blocks}:{c.body_blocks}:{c.tail_blocks}"


def published():
    """Every file of the number table, by number, as fileset knows it: the
    regular set with the 32-bit WAVs, the code sweeps, the negative files and
    the gapless set, in every format each one ships in."""
    files = {}
    for f in (fileset.test_set(pcm32=True) + fileset.sweep_set() + fileset.negative_set()
              + fileset.gapless_set()):
        for fmt in (f.format,) + f.formats:
            files.setdefault(f.number(fmt), (f, fmt))
    return files


def table_line(f):
    """The walker tool's lookup line for a file of fileset: kind, depth code,
    rate code, lead-in, body and tail blocks, fault, part, cut, fault start,
    fault length, frame rate and frames sent."""
    c = f.cfg
    unit = 16 if c.dsd else 1                       # DSD bits per DoP frame
    cut = fileset.gapless_cut(c) // unit if f.part else 0
    at = length = 0
    if f.fault == "incomplete" or (f.fault == "altered" and c.dsd):
        at, length = fileset.fault_start(c), fileset.FAULT_FRAMES
    elif f.fault == "gap":
        length = fileset.gap_frames(c)
    total = c.total_units // unit
    frames = {0: total, 1: cut, 2: total - cut}[f.part]
    frames += length if f.fault == "gap" and f.part == 2 else 0
    frames -= length if f.fault == "incomplete" else 0
    return (f"{c.kind} {c.depth_code} {c.rate_code} {c.lead_blocks} {c.body_blocks} {c.tail_blocks} "
            f"{FAULTS[f.fault]} {f.part} {cut} {at} {length} {c.rate // unit} {frames}")


@unittest.skipUnless(CC, "no host C compiler")
class Walker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="bpc1-walk-")
        cls.exe = test_bpc1_c.build_tool(cls.tmp.name, ["bpc1.c", "bpc1_walk.c", "host/bpc1_walk_tool.c"],
                                         "bpc1_walk_tool")
        cls.files = os.path.join(cls.tmp.name, "files")
        os.makedirs(cls.files)
        cls.numbers = published()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tool(self, *args, stdin=None, check=True):
        return subprocess.run([self.exe, *args], input=stdin, capture_output=True, check=check)

    def fill(self, name, slot, split=None, head=None):
        """The walker's bytes for a number or layout; split: fill in pieces;
        head: only the first `head` frames."""
        args = ([split] if split else []) + ([f"head={head}"] if head else [])
        return self.tool("fill", name, str(slot), *args).stdout

    def info(self, name, slot):
        r = self.tool("info", name, str(slot), check=False)
        if r.returncode:
            return None
        return dict(kv.split("=") for kv in r.stdout.decode().split())

    def assertSame(self, got, want, what):
        """Equal bytes; on a difference, the first differing byte."""
        if got != want:
            n = min(len(got), len(want))
            first = next((i for i in range(n) if got[i] != want[i]), n)
            self.fail(f"{what}: {len(got)} bytes, want {len(want)}; first difference at byte {first}")

    def check_file(self, name, fspec, slots):
        """The walker's bytes for `name` at each slot equal the reference of
        fspec, and its frame count, frame size and rate agree."""
        nch = fspec.cfg.nch
        for slot in slots:
            want = reference(fspec, slot, self.files)
            got = self.fill(name, slot)
            self.assertSame(got, want, f"{name} slot {slot}")
            i = self.info(name, slot)
            self.assertEqual(int(i["frames"]) * nch * slot, len(want), f"{name} frames")
            self.assertEqual(int(i["frame_bytes"]), nch * slot)
            rate = fspec.cfg.rate // 16 if fspec.cfg.dsd else fspec.cfg.rate
            self.assertEqual(int(i["rate"]), rate)

    # The number table --------------------------------------------------------

    def test_number_table(self):
        """Every number 00000 to 99999: the files of fileset, in every format
        each ships in, with their layout, fault and part; any other number,
        such as 90100 (a FLAC of a negative file) or 00400, refused."""
        out = self.tool("lookup", stdin="".join(f"{n:05d}\n" for n in range(100000)).encode())
        got = out.stdout.decode().splitlines()
        self.assertEqual(len(got), 100000)
        want = ["-"] * 100000
        for number, (f, _) in self.numbers.items():
            want[int(number)] = table_line(f)
        bad = [(f"{n:05d}", g, w) for n, (g, w) in enumerate(zip(got, want)) if g != w]
        self.assertEqual(bad[:5], [])
        self.assertEqual(sum(1 for g in got if g != "-"), len(self.numbers))
        self.assertEqual(got[90100], "-")
        self.assertEqual(len(self.numbers), 102 + 10 + 81 + 38)   # 11.1, the 32-bit WAVs, 11.6, 11.7

    def test_refused(self):
        """Unknown numbers, depths wider than the slot, DoP in 2-byte slots
        and slot sizes outside 2 to 4."""
        for name, slot in (("90100", 3), ("00400", 2), ("00070", 4), ("99999", 4), ("0000", 2),
                           ("000000", 2), ("x", 2), ("00030", 2), ("00060", 3), ("01000", 2),
                           ("01400", 2), ("00000", 1), ("00000", 5), ("1:3:0:9:2:1", 2),
                           ("2:1:10:9:2:1", 2), ("1:1:0:9:2:1", 2), ("3:2:0:9:2:1", 2),
                           ("5:4:0:9:2:1", 4), ("1:2:13:9:2:1", 2)):
            with self.subTest(name=name, slot=slot):
                self.assertIsNone(self.info(name, slot))
                self.assertNotEqual(self.tool("fill", name, str(slot), check=False).returncode, 0)

    # Short files: every rate and slot --------------------------------------------

    def test_pcm_every_rate_depth_slot(self):
        for rate in spec.PCM_RATES:
            for depth in (16, 24, 32):
                with self.subTest(rate=rate, depth=depth):
                    f = short_spec(spec.KIND_PCM, rate, depth)
                    self.check_file(layout(f), f, SLOTS[depth])

    def test_sweeps(self):
        for rate in (44100, 768000):
            for depth in (16, 24):
                with self.subTest(rate=rate, depth=depth):
                    f = short_spec(spec.KIND_SWEEP, rate, depth)
                    self.check_file(layout(f), f, SLOTS[depth])

    def test_dop(self):
        for rate in spec.DSD_RATES:
            with self.subTest(rate=rate):
                f = short_spec(spec.KIND_DSD, rate, 1)
                self.check_file(layout(f), f, SLOTS[1])

    def test_fast_paths_against_the_units(self):
        """The walker's runs against bpc1_dsd_unit and bpc1_pcm_unit, frame
        by frame (the tool's ref command), over whole blocks: lead-in,
        R-header blocks 8 and 24, body, tail and the last block."""
        cases = [(spec.KIND_DSD, 1, r) for r in spec.DSD_RATES]
        cases += [(spec.KIND_PCM, 16, 44100), (spec.KIND_PCM, 24, 48000), (spec.KIND_PCM, 32, 768000),
                  (spec.KIND_SWEEP, 16, 88200), (spec.KIND_SWEEP, 24, 705600)]
        for kind, depth, rate in cases:
            cfg = Config(kind=kind, rate=rate, depth=depth, lead_blocks=10, body_blocks=15, tail_blocks=2)
            name = f"{kind}:{cfg.depth_code}:{cfg.rate_code}:10:15:2"
            for slot in SLOTS[depth][-1:] + SLOTS[depth][:1]:
                with self.subTest(kind=kind, rate=rate, slot=slot):
                    want = self.tool("ref", name, str(slot)).stdout
                    self.assertEqual(len(want), cfg.total_blocks * cfg.block_len // (16 if cfg.dsd else 1)
                                     * 2 * slot)
                    self.assertSame(self.fill(name, slot), want, name)

    # Fill in packet-sized pieces ----------------------------------------------------

    def test_splits(self):
        """Pieces of 5, 6, 7 frames, of 96 and 97, and of seeded random sizes
        give the bytes of one call, across every block boundary, a fault's
        edges, a gap and a part's start."""
        names = [(layout(short_spec(spec.KIND_PCM, r, 24)), 3) for r in spec.PCM_RATES]
        names += [(layout(short_spec(spec.KIND_PCM, 44100, 16)), 2),
                  (layout(short_spec(spec.KIND_PCM, 768000, 32)), 4),
                  (layout(short_spec(spec.KIND_SWEEP, 48000, 24)), 4)]
        names += [(layout(short_spec(spec.KIND_DSD, r, 1)), 3) for r in spec.DSD_RATES]
        names += [(n, 3) for n in ("90000", "90001", "90004", "91000", "91001", "20001", "21401")]
        names += [("90002", 2)]
        for name, slot in names:
            whole = self.fill(name, slot)
            self.assertGreater(len(whole), 0)
            for split in SPLITS:
                with self.subTest(name=name, split=split):
                    self.assertSame(self.fill(name, slot, split), whole, f"{name} split {split}")

    # Published files ------------------------------------------------------------

    def test_negatives(self):
        """All seven negative files, as write_file and write_part write them."""
        names = [n for n in self.numbers if n.startswith("9")]
        self.assertEqual(sorted(names), ["90000", "90001", "90002", "90003", "90004", "91000", "91001"])
        for name in sorted(names):
            with self.subTest(name=name):
                f, _ = self.numbers[name]
                self.check_file(name, f, SLOTS[f.cfg.depth][:1] + (4,))

    def test_gapless_parts(self):
        """Each part of the gapless set as write_part writes it (DSF and DFF
        parts as DoP), and the two parts of a pair one after the other give
        the regular file."""
        names = sorted(n for n in self.numbers if n.startswith("2"))
        self.assertEqual(len(names), 4 * 2 * 4 + 2 * 2)
        for name in names:
            f, fmt = self.numbers[name]
            if fmt not in ("wav", "dsf", "dff"):
                continue
            with self.subTest(name=name):
                self.check_file(name, f, SLOTS[f.cfg.depth][:1])
        for p1 in [n for n in names if f"{n[-1]}" in "02468"]:
            p2 = f"{int(p1) + 1:05d}"
            f, _ = self.numbers[p1]
            regular = {n: g for n, (g, _) in self.numbers.items() if not g.part and not g.fault
                       and g.cfg == f.cfg and n[2] == p1[2]}
            self.assertEqual(len(regular), 1, p1)
            slot = SLOTS[f.cfg.depth][-1]
            with self.subTest(pair=(p1, p2)):
                self.assertSame(self.fill(p1, slot) + self.fill(p2, slot),
                                self.fill(next(iter(regular)), slot), f"{p1}+{p2}")

    def test_spec_vectors(self):
        """docs/bpc1.md section 13: the first bytes of each PCM WAV's data
        chunk, the first DoP frames of DSD256, and the DSD bytes of each DSD
        vector at their positions, read from the walker's DoP frames."""
        with open(VECTORS) as f:
            vec = json.load(f)
        for v in vec["pcm"]:
            with self.subTest(v["file"]):
                want = bytes.fromhex(v["wav_data_first_bytes"])
                got = self.fill(v["file"][:5], v["depth"] // 8, head=64)
                self.assertEqual(got[:len(want)], want)
        for v in vec["dsd"]:
            with self.subTest(v["file"]):
                end = max(s["first_bit"] + 8 * len(s["hex"][0]) // 2 for s in v["dsd_bytes_msb_first"])
                got = self.fill(v["file"][:5], 3, head=end // 16 + 1)
                frames = np.frombuffer(got, dtype=np.uint8).reshape(-1, 2, 3)
                if "dop_first_frames" in v:
                    for i, pair in enumerate(v["dop_first_frames"]):
                        for c, hexval in enumerate(pair):
                            self.assertEqual(int.from_bytes(frames[i, c].tobytes(), "little"),
                                             int(hexval, 16), f"frame {i} channel {c}")
                for s in v["dsd_bytes_msb_first"]:
                    first, n = s["first_bit"] // 8, len(s["hex"][0]) // 2
                    for c in range(v["channels"]):
                        f0, f1 = first // 2, (first + n + 1) // 2
                        dsd = frames[f0:f1, c, [1, 0]].reshape(-1)        # DoP: byte 2f in bits 15:8
                        dsd = dsd[first - 2 * f0:first - 2 * f0 + n]
                        self.assertEqual(dsd.tobytes().hex().upper(), s["hex"][c].upper(), s["what"])

    @unittest.skipUnless(os.environ.get("BPC1_FULL_SET"), "set BPC1_FULL_SET=1 for every file")
    def test_full_set(self):
        """Every row of the number table, at its file's own slot size (DoP: 3
        bytes), by sha256 of the walker's bytes and of the data chunk."""
        rows = {}
        for name, (f, fmt) in sorted(self.numbers.items()):
            if fmt in ("flac", "aiff", "alac"):
                continue
            rows.setdefault(table_line(f), (name, f))
        self.assertEqual(len(rows), 70)
        for name, f in rows.values():
            slot = 3 if f.cfg.dsd else f.cfg.depth // 8
            with self.subTest(name=name):
                want = hashlib.sha256(reference(f, slot, self.files)).hexdigest()
                got = hashlib.sha256(self.fill(name, slot)).hexdigest()
                self.assertEqual(got, want, name)


if __name__ == "__main__":
    unittest.main()
