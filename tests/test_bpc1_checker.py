# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The firmware's checker engine (firmware/bpc1/bpc1_checker.c) through its
host tool firmware/bpc1/host/bpc1_check.c, on generated raw s32le streams:
the verdict cases of the vectors file and random changes against the
reference checker/verify.py (verdict, end, diff and bits; for random faults
in the R-headers' r5 and r6 chips, diff to the source unit);
no false fail (clean files of every kind, depth and rate, gapless runs,
starts and stops within the lead-in and tail), no false pass (single-bit
changes in every class of body unit, DoP marker and padding faults, drops,
inserts, repeats, late starts, early stops, channel order, rate), the gap
sweep and review cases of an earlier engine, unknown signal outside the
files, allowed silence, overruns and chunking. Needs a host C
compiler; skipped without one."""

import json
import os
import random
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from checker import readers, verify
from generator import spec
from generator.signal import Config, dop_frames, dsd_blocks, pcm_blocks

from tests import simulate as sim
from tests.test_bpc1_c import BPC1, CC, ROOT, VECTORS
from tests.test_verify import build_case

# Short files with every section: block 8 (an R-header) in the body.
SHORT = dict(lead_blocks=2, body_blocks=8, tail_blocks=1)
# Two R-headers in the body (blocks 8 and 24), and two tail blocks.
LONG = dict(lead_blocks=2, body_blocks=24, tail_blocks=2)
CHIP_R5 = 13 + 5 * 32
FUZZ_TRIALS = 1500
MULTI_TRIALS = 300
SWEEP_TRIALS = 200
RHDR_TRIALS = 150
R5_TRIALS = 300

_streams = {}


def words(kind, rate, depth, layout=None):
    """A whole file as 32-bit left-justified words (frames, 2): PCM (and the
    PCM code sweep), or DoP."""
    layout = layout or SHORT
    key = (kind, rate, depth, tuple(sorted(layout.items())))
    if key not in _streams:
        cfg = Config(kind=kind, rate=rate, depth=depth, **layout)
        if not spec.is_dsd_kind(kind):  # PCM and the PCM code sweep
            s = pcm_blocks(cfg, 0, cfg.total_blocks).astype(np.int64)
            w = (s << (32 - depth)).astype(np.int32)
        else:
            f = dop_frames(dsd_blocks(cfg, 0, cfg.total_blocks), 0).astype(np.int64) << 8
            w = f.astype(np.uint32).view(np.int32)
        w.setflags(write=False)
        _streams[key] = (cfg, w)
    return _streams[key]


def sweep_words(rate, depth, layout=None):
    """A PCM code-sweep file (kind SWEEP) as words, from the generator."""
    return words(spec.KIND_SWEEP, rate, depth, layout)


def frames_per_block(cfg):
    return cfg.block_len // 16 if cfg.dsd else cfg.block_len


def units_per_block(cfg):
    return cfg.block_len // 8 if cfg.dsd else cfg.block_len


def flip(w, frame, ch, bit):
    x = w.copy()
    x[frame, ch] ^= np.int32(-(1 << 31) if bit == 31 else 1 << bit)
    return x


def zeros(n):
    return np.zeros((n, 2), np.int32)


def dop_idle(n, first=0x05):
    """DoP silence: alternating markers, DSD idle bytes 0x69."""
    x = np.full((n, 2), (first << 24) | (0x6969 << 8), np.uint32)
    x[1::2] ^= np.uint32(0xFF000000)
    return x.view(np.int32)


def chip_frame(cfg, k, q, offset=0):
    """The received frame of chip q of R-header block k (PCM)."""
    m = cfg.block_len // 4096
    L = (11 if cfg.rate % 44100 == 0 else 12) * m
    return k * cfg.block_len + 512 * m + q * L + offset


def with_count(w, cfg, k, count, channels=(0, 1)):
    """PCM words w with the R-header of block k spelling the block count
    `count` and a CRC that passes: both chips of each r5 bit that changes,
    and of the r6 bits that keep the CRC, negated."""
    d = [0] * 5 + [(count ^ cfg.total_blocks) & 0xFFFF]
    d.append(spec.crc16(spec.words_to_bytes(d)) ^ spec.crc16(spec.words_to_bytes([0] * 6)))
    x = w.copy()
    for i, m in enumerate(d):
        for b in range(16):
            if m >> (15 - b) & 1:
                a = chip_frame(cfg, k, 13 + 2 * (16 * i + b))
                for c in channels:
                    x[a:a + 2 * (chip_frame(cfg, k, 1) - chip_frame(cfg, k, 0)), c] *= -1
    return x


def with_count_dop(w, cfg, k, count, channels=(0, 1)):
    """DoP words w (24-bit DSD bytes in bits 23 to 8) with the R-header of
    block k spelling the block count `count` and a CRC that passes: the DSD
    bytes of both chips of each bit that changes inverted (0xEE <-> 0x11)."""
    d = [0] * 5 + [(count ^ cfg.total_blocks) & 0xFFFF]
    d.append(spec.crc16(spec.words_to_bytes(d)) ^ spec.crc16(spec.words_to_bytes([0] * 6)))
    x = w.view(np.uint32).copy()
    for i, m in enumerate(d):
        for b in range(16):
            if m >> (15 - b) & 1:
                a = chip_frame(cfg, k, 13 + 2 * (16 * i + b)) // 16
                n = 2 * (chip_frame(cfg, k, 1) - chip_frame(cfg, k, 0)) // 16
                for c in channels:
                    x[a:a + n, c] ^= np.uint32(0x00FFFF00)
    return x.view(np.int32)


def sweep_stream(cfg, w, size, how, p):
    """A stream with one gap at frame p: size frames dropped, or inserted as
    zeros (DoP: the markers kept), DoP silence, repeats of the frame before, or
    noise (the review patch's sweep)."""
    rng = np.random.default_rng(size * 7 + p)
    if how == "drop":
        return np.delete(w, np.s_[p:p + size], axis=0)
    if how == "repeat":
        return np.insert(w, p, np.repeat(w[p - 1:p], size, axis=0), axis=0)
    mk = (w[p:p + size].view(np.uint32) >> 24 & 0xFF).astype(np.uint32) << 24
    if how == "zero":
        ins = mk.view(np.int32) if cfg.dsd else zeros(size)
    elif how == "silence":
        ins = (mk | (0x6969 << 8)).view(np.int32)
    elif cfg.dsd:  # noise
        ins = (mk | (rng.integers(0, 1 << 16, size).astype(np.uint32)[:, None] << 8)).view(np.int32)
    else:
        ins = (rng.integers(-(1 << 15), 1 << 15, (size, 2)).astype(np.int64) << (32 - cfg.depth)).astype(np.int32)
    return np.insert(w, p, ins, axis=0)


def fuzz_case(rng):
    """A random stream: one of four short files with one or two random changes
    (bits flipped, X-sync words broken, drops, inserts, repeats, zeros, a late
    start, an early stop, a DoP marker) in its lead-in, body, tail or at their
    edges; a random slot (DoP), chunking and silence setting. Returns the
    stream, its rate, slot, chunk and allow_silence, and the changes."""
    layouts = ((spec.KIND_PCM, 48000, 24, LONG), (spec.KIND_PCM, 44100, 16, SHORT),
               (spec.KIND_PCM, 96000, 32, dict(lead_blocks=3, body_blocks=9, tail_blocks=2)),
               (spec.KIND_DSD, spec.DSD_RATES[0], 1, dict(lead_blocks=2, body_blocks=8, tail_blocks=2)))
    kind, rate, depth, layout = rng.choice(layouts)
    cfg, w = words(kind, rate, depth, layout)
    x, ops = changed(rng, cfg, w, rng.randint(1, 2))
    slot = 24 if cfg.dsd and rng.random() < 0.5 else 32
    hr = rate // 16 if cfg.dsd else rate
    return x, hr, slot, rng.choice([1, 7, 255, 256, 333, 4096]), rng.random() < 0.3, ops


def changed(rng, cfg, w, n_ops):
    """The file w with n_ops random changes (fuzz_case), and the changes."""
    F = frames_per_block(cfg)
    L, E = cfg.lead_blocks, cfg.lead_blocks + cfg.body_blocks - 1
    x = w.astype(np.int64)
    ops = []
    for _ in range(n_ops):
        op = rng.choice(["flip", "drop", "ins0", "rep", "zero", "stop", "start", "mk", "flipsync"])
        region = rng.choice(["lead", "body", "tail", "edge"])
        if region == "lead":
            pos = rng.randrange(0, L * F)
        elif region == "body":
            pos = rng.randrange(L * F, (E + 1) * F)
        elif region == "tail":
            pos = rng.randrange((E + 1) * F, len(w))
        else:
            pos = rng.choice([L * F, (E + 1) * F, (L - 1) * F, (E + 1) * F + 8]) + rng.randint(-10, 10)
        if pos >= len(x) - 1:
            continue
        pos = max(1, pos)
        n = rng.choice([1, 2, 3, 6, 50, 700, F + 3])
        if op == "flip":
            x[pos, rng.randint(0, 1)] ^= 1 << rng.choice([0, 8, 12, 16, 20, 24, 29, 31])
        elif op == "flipsync":
            pos = pos // F * F + rng.randint(0, 7)
            for c in rng.choice([[0], [1], [0, 1]]):
                x[pos, c] ^= 1 << 20
        elif op == "drop":
            x = np.delete(x, np.s_[pos:pos + n], axis=0)
        elif op == "ins0":
            x = np.insert(x, pos, np.zeros((n, 2), np.int64), axis=0)
        elif op == "rep":
            x = np.insert(x, pos, x[max(0, pos - n):pos], axis=0)
        elif op == "zero":
            x[pos:pos + n] = 0
        elif op == "stop":
            x = x[:pos]
        elif op == "start":
            x = x[pos:]
        elif op == "mk":
            x[pos] ^= 0xFF000000
        ops.append((op, region, pos, n))
        if len(x) < 10:
            break
    return (x & 0xFFFFFFFF).astype(np.uint32).view(np.int32), ops


# Files of one frame rate, for streams of several files: PCM, code sweeps
# (kind SWEEP) and DoP; LEAD8: the first body block, 8, is an R-header block.
LEAD8 = dict(lead_blocks=8, body_blocks=3, tail_blocks=1)
FAMILIES = ((48000, ((spec.KIND_PCM, 48000, 24, LONG), (spec.KIND_PCM, 48000, 16, SHORT),
                     (spec.KIND_PCM, 48000, 24, LEAD8), (spec.KIND_SWEEP, 48000, 16, SHORT),
                     (spec.KIND_SWEEP, 48000, 24, SHORT))),
            (176400, ((spec.KIND_PCM, 176400, 24, SHORT), (spec.KIND_SWEEP, 176400, 16, SHORT),
                      (spec.KIND_DSD, spec.DSD_RATES[0], 1, dict(lead_blocks=2, body_blocks=8, tail_blocks=2)))))


def multi_case(rng):
    """A random stream of 2 or 3 files of one frame rate: the same file
    twice, PCM next to DoP, code sweeps, each file changed (fuzz_case) or
    not, or cut in its body (often within an X-sync), gapless or with a
    short gap of zeros (DoP idle after a DoP file); a random slot, chunking
    and silence setting."""
    rate, pool = rng.choice(FAMILIES)
    parts, ops, prev = [], [], None
    for i in range(rng.randint(2, 3)):
        f = prev if prev and rng.random() < 0.3 else rng.choice(pool)
        cfg, w = words(*f)
        if i and rng.random() < 0.5:
            n = rng.choice([1, 3, 100, 3000, frames_per_block(cfg) + 3, 3 * frames_per_block(cfg)])
            parts.append(dop_idle(n) if prev[0] == spec.KIND_DSD else zeros(n))
            ops.append(("gap", n))
        x, o = changed(rng, cfg, w, 1) if rng.random() < 0.4 else (w, [])
        if rng.random() < 0.25:  # cut in the body, often within an X-sync, before the next file
            F = frames_per_block(cfg)
            cut = rng.randrange(cfg.lead_blocks, cfg.lead_blocks + cfg.body_blocks) * F + rng.choice(
                [rng.randint(1, 7), rng.randrange(F)])
            x = x[:cut]
            o = o + [("cut", cut)]
        parts.append(x)
        ops.append((f[0], f[2], o))
        prev = f
    return (np.concatenate(parts), rate, rng.choice([24, 32]), rng.choice([1, 7, 333, 4096]),
            rng.random() < 0.3, ops)


def sweep_case(rng):
    """A random code-sweep file (kind SWEEP, 16 or 24 bits; LONG at 16 bits:
    the sweep index wraps at 2^16) with one or two changes (fuzz_case)."""
    rate, depth, layout = rng.choice(((44100, 16, SHORT), (44100, 16, LONG), (48000, 24, LONG),
                                      (96000, 24, SHORT), (48000, 16, LEAD8)))
    cfg, w = sweep_words(rate, depth, layout)
    x, ops = changed(rng, cfg, w, rng.randint(1, 2))
    return x, rate, 32, rng.choice([1, 333, 4096]), rng.random() < 0.3, ops


def rhdr_case(rng, dop):
    """A file with the layout LONG (body R-headers 8 and 24), PCM at 48 kHz or
    DoP, with 1 to 3 random faults in the r5 and r6 chips of its body
    R-headers: a chip inverted or zeroed from one of its units on, one unit's
    low bit changed, or both chips of a bit inverted (a valid pair of the
    other value, which only the CRC or the count shows); DoP also a marker
    zeroed or a padding bit set. The stream stops after the LAST block, in
    the tail before it (the count not known) or right after a block's chip
    236. Returns the stream, its rate and slot, and the changes."""
    if dop:
        cfg, w = words(spec.KIND_DSD, spec.DSD_RATES[0], 1, LONG)
        inv, zero, d = 0x00FFFF00, 0xFF0000FF, 16  # the DSD bytes; frames: 16 DSD bits
    else:
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        inv, zero, d = 0xC0000000, 0, 1
    at = lambda k, q, t=0: chip_frame(cfg, k, q) // d + t
    L = at(8, 1) - at(8, 0)
    x = w.view(np.uint32).copy()
    ops = []
    for _ in range(rng.randint(1, 3)):
        k, q, ch = rng.choice((8, 24)), CHIP_R5 + rng.randrange(64), rng.choice(([0], [1], [0, 1]))
        t = rng.randrange(L) if rng.random() < 0.4 else 0
        kind = rng.choice(("invert", "zero", "low", "pair") + (("marker", "padding") if dop else ()))
        a, b = at(k, q, t), at(k, q + 1)
        if kind == "invert":
            x[a:b, ch] ^= np.uint32(inv)
        elif kind == "zero":
            x[a:b, ch] &= np.uint32(zero)
        elif kind == "low":
            x[a, ch] ^= np.uint32(0x100)
        elif kind == "pair":
            q = CHIP_R5 + ((q - CHIP_R5) & ~1)
            x[at(k, q):at(k, q + 2), ch] ^= np.uint32(inv)
        elif kind == "marker":
            x[a, ch] &= np.uint32(0x00FFFFFF)
        else:
            x[a, ch] ^= np.uint32(1)
        ops.append((kind, k, q, t, ch))
    end = rng.random()
    if end < 0.2:
        x = x[:26 * frames_per_block(cfg) + 100]
        ops.append(("stop in the tail", None))
    elif end < 0.3:
        k = rng.choice((8, 24))
        x = x[:at(k, CHIP_R5 + 64)]
        ops.append(("stop after chip 236", k))
    return x.view(np.int32), cfg.rate // d, 32, ops


def r5_case(rng):
    """A file whose R-headers, in the lead-in, the body or the tail, spell
    random block counts with CRCs that pass, in one channel or both: PCM at
    44.1 to 192 kHz in 16, 24 or 32 bits, or DoP at DSD64 or DSD128 in
    24- or 32-bit slots; the LAST X-sync mostly present, sometimes erased;
    sometimes a payload bit changed; the stream whole, stopped in the tail
    or the LAST block, or followed by silence or by the file again. Returns
    the stream, its rate, slot and a chunk size, and the changes."""
    layout = rng.choice((SHORT, LONG, dict(lead_blocks=10, body_blocks=4, tail_blocks=2),
                         dict(lead_blocks=10, body_blocks=16, tail_blocks=2),
                         dict(lead_blocks=10, body_blocks=1, tail_blocks=10),
                         dict(lead_blocks=2, body_blocks=4, tail_blocks=10),
                         dict(lead_blocks=2, body_blocks=4, tail_blocks=2)))
    dop = rng.random() < 0.35
    if dop:
        rate = rng.choice(spec.DSD_RATES[:2])
        cfg, w = words(spec.KIND_DSD, rate, 1, layout)
        rate, slot, d = rate // 16, rng.choice((24, 32)), 16
        quiet, recount = dop_idle, with_count_dop
    else:
        rate, depth = rng.choice((44100, 48000, 88200, 96000, 176400, 192000)), rng.choice((16, 24, 32))
        cfg, w = words(spec.KIND_PCM, rate, depth, layout)
        slot, d = 32, 1
        quiet, recount = zeros, with_count
    B, T = frames_per_block(cfg), cfg.total_blocks
    x, ops = w, []
    for k in range(8, T, 16):
        if rng.random() < 0.6:
            n = rng.choice((T - 1, T + 1, T + 2, k, k + 1, k + 2, rng.randrange(1, 64), 0, 1, max(k - 1, 0),
                            max(T - 3, 0), rng.randrange(0, max(T, 1))))
            ch = rng.choice(((0, 1), (0, 1), (0,), (1,)))
            x = recount(x, cfg, k, n, ch)
            ops.append(("count", k, n, ch))
    if rng.random() < 0.3:
        x = x.copy()
        x[(T - 1) * B:(T - 1) * B + 8 * (2 if dop else 1)] = quiet(8 * (2 if dop else 1))
        ops.append(("LAST erased",))
    if rng.random() < 0.2:
        f = rng.randrange(cfg.lead_blocks * B, (cfg.lead_blocks + cfg.body_blocks) * B)
        x = flip(x, f, rng.randrange(2), 8)
        ops.append(("flip", f))
    end = rng.random()
    if end < 0.1 and T > 8:
        n = chip_frame(cfg, 8, CHIP_R5 + 64) // d + rng.randrange(1, 251)
        x = x[:n]
        ops.append(("stop after the first R-header's chip 236", n))
    elif end < 0.2:
        n = (T - 1) * B + rng.choice((8 * (2 if dop else 1), 100, B // 2))
        x = x[:n]
        ops.append(("stop in the LAST block", n))
    elif end < 0.33:
        n = (cfg.lead_blocks + cfg.body_blocks) * B + rng.choice((8 * (2 if dop else 1), 100))
        x = x[:n]
        ops.append(("stop in the tail", n))
    elif end < 0.5:
        n = rng.choice((B // 2, B, 3 * B, 20 * B))
        x = np.concatenate([x, quiet(n)])
        ops.append(("silence after", n))
    elif end < 0.7:
        x = np.concatenate([x, w])
        ops.append(("the file again",))
    return x, rate, slot, rng.choice((1, 333, 4096)), ops


def py_verify(w, rate, slot=32, silence=False):
    """checker/verify.py on the stream as the device receives it in the slot."""
    x = (w.view(np.uint32) & np.uint32((0xFFFFFFFF << (32 - slot)) & 0xFFFFFFFF)).view(np.int32)
    st = readers.Stream(False, rate, 2, np.ascontiguousarray(x), "test", slot_bits=slot, valid_bits=slot)
    return verify.verify_stream(st, silence)


def py_results(w, rate, slot=32, silence=False):
    return [(r["verdict"], r["end"], "-" if r["diff"] is None else "%.3f" % r["diff"], str(r["bits"]))
            for r in py_verify(w, rate, slot, silence)]


def c_key(res):
    return [(r["verdict"], r["end"], r["diff"], r["bits"]) for r in res]


def unit_key(res):
    """Verdict, end and, for NOT_BIT_PERFECT, the source unit of the first
    difference: of the engine's results or of checker/verify.py's."""
    return [(r["verdict"], r["end"], str(r["diff_unit"]) if r["verdict"] == "NOT_BIT_PERFECT" else "-")
            for r in res]


def build(directory, name):
    exe = os.path.join(directory, name)
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-D_POSIX_C_SOURCE=199309L", "-I", BPC1, "-o", exe]
    cmd += [os.path.join(BPC1, s) for s in ("bpc1.c", "bpc1_checker.c", "host/bpc1_check.c")]
    subprocess.run(cmd, check=True)
    return exe


def parse(out):
    """The results (check and diag lines joined) and the stream line."""
    res, stream = [], None
    for line in out.splitlines():
        kv = dict(x.split("=", 1) for x in line.split()[1:])
        if line.startswith("check "):
            res.append(kv)
        elif line.startswith("diag "):
            res[-1].update({k: v for k, v in kv.items() if k != "file"})
        elif line.startswith("stream "):
            stream = kv
    return res, stream


@unittest.skipUnless(CC, "no host C compiler")
class Checker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="bpc1-checker-")
        cls.exe = build(cls.tmp.name, "bpc1_check")
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_c(self, w, rate, chunk=4096, slot=32, silence=False, overruns=()):
        """Results and the stream line of the engine on stream w."""
        type(self).n += 1
        raw = os.path.join(self.tmp.name, f"s{self.n}_{threading_id()}.raw")
        np.ascontiguousarray(w, dtype="<i4").tofile(raw)
        args = [self.exe] + (["-s"] if silence else [])
        for o in overruns:
            args += ["-o", str(o)]
        out = subprocess.run(args + [str(rate), str(slot), str(chunk), raw], capture_output=True, text=True,
                             check=True).stdout
        os.remove(raw)
        return parse(out)

    def results(self, w, rate, **kw):
        return self.run_c(w, rate, **kw)[0]

    def one(self, w, rate, **kw):
        res = self.results(w, rate, **kw)
        self.assertEqual(len(res), 1, res)
        return res[0]

    def assert_pass(self, w, rate, **kw):
        res, st = self.run_c(w, rate, **kw)
        self.assertEqual([r["verdict"] for r in res], ["BIT_PERFECT"], res)
        r = res[0]
        self.assertEqual((r["diff"], r["why"]), ("-", "none"), r)
        return r, st

    def assert_fail(self, w, rate, unit=None, why=None, **kw):
        """The stream's one file is NOT_BIT_PERFECT, its first difference at
        source unit `unit` (if given), found by check `why` (if given)."""
        res = [r for r in self.results(w, rate, **kw) if r["verdict"] != "UNKNOWN_SIGNAL"]
        self.assertEqual([r["verdict"] for r in res], ["NOT_BIT_PERFECT"], res)
        r = res[0]
        if unit is not None:
            self.assertEqual(int(r["diff_unit"]), unit, r)
        if why is not None:
            self.assertEqual(r["why"], why, r)
        return r

    # No false fail ---------------------------------------------------------

    def test_clean_pcm(self):
        for rate in spec.PCM_RATES:
            for depth in (16, 24, 32):
                with self.subTest(rate=rate, depth=depth):
                    cfg, w = words(spec.KIND_PCM, rate, depth)
                    r, st = self.assert_pass(w, rate)
                    self.assertEqual((r["kind"], r["dop"], r["end"], r["depth"], r["bits"], r["file_rate"], r["rate"]),
                                     ("PCM", "0", "LAST", str(depth), str(depth), str(rate), str(rate)))
                    self.assertEqual((r["body_k"], int(r["compared"])), ("2", 8 * cfg.block_len))
                    self.assertEqual((r["seed"], r["nch"]), ("0x42504331", "2"))
                    self.assertEqual(st, {"outside": "0", "silent": "0", "results": "1"})

    def test_clean_dop(self):
        for rate in spec.DSD_RATES:
            with self.subTest(rate=rate):
                cfg, w = words(spec.KIND_DSD, rate, 1)
                r, _ = self.assert_pass(w, rate // 16)
                self.assertEqual((r["kind"], r["dop"], r["end"], r["depth"], r["bits"], r["file_rate"]),
                                 ("DSD", "1", "LAST", "1", "24", str(rate)))
                self.assertEqual(int(r["compared"]), 8 * units_per_block(cfg))
        with self.subTest("the other marker phase first"):
            _, w = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
            self.assert_pass((w.view(np.uint32) ^ np.uint32(0xFF000000)).view(np.int32), spec.DSD_RATES[0] // 16)

    def test_slots(self):
        """A file in a slot wider than its depth is the same word W."""
        _, w16 = words(spec.KIND_PCM, 48000, 16)
        _, w24 = words(spec.KIND_PCM, 48000, 24)
        for w, slot in ((w16, 16), (w16, 24), (w24, 24)):
            with self.subTest(slot=slot):
                r, _ = self.assert_pass(w, 48000, slot=slot)
                self.assertEqual(r["slot"], str(slot))
        _, wd = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        self.assert_pass(wd, spec.DSD_RATES[0] // 16, slot=24)
        with self.subTest("a 3-byte slot carries no bits below 24"):
            r, _ = self.assert_pass(flip(wd, 6 * frames_per_block(words(spec.KIND_DSD, spec.DSD_RATES[0], 1)[0])
                                         + 7, 0, 3), spec.DSD_RATES[0] // 16, slot=24)
            self.assertEqual(r["bits"], "24")

    def test_gapless(self):
        """Same-rate files joined into one stream, each reported on its own."""
        _, a = words(spec.KIND_PCM, 48000, 16)
        _, b = words(spec.KIND_PCM, 48000, 24)
        _, c = words(spec.KIND_PCM, 48000, 32)
        res, st = self.run_c(np.concatenate([a, b, c, a, a]), 48000)
        self.assertEqual([(r["verdict"], r["depth"], r["end"]) for r in res],
                         [("BIT_PERFECT", "16", "LAST"), ("BIT_PERFECT", "24", "LAST"), ("BIT_PERFECT", "32", "LAST"),
                          ("BIT_PERFECT", "16", "LAST"), ("BIT_PERFECT", "16", "LAST")])
        self.assertEqual(st["outside"], "0")
        self.assertEqual([int(r["first_frame"]) for r in res], [0, len(a), len(a) + len(b),
                                                               len(a) + len(b) + len(c), 2 * len(a) + len(b) + len(c)])
        _, d = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        _, p = words(spec.KIND_PCM, spec.DSD_RATES[0] // 16, 24)
        res = self.results(np.concatenate([p, d, d, p]), spec.DSD_RATES[0] // 16)
        self.assertEqual([(r["verdict"], r["kind"]) for r in res],
                         [("BIT_PERFECT", "PCM"), ("BIT_PERFECT", "DSD"), ("BIT_PERFECT", "DSD"), ("BIT_PERFECT", "PCM")])

    def test_starts_and_stops(self):
        """The stream may start before the last lead-in X-sync and stop after
        the first tail X-sync, or in the LAST block."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        for name, x, end in (("from the first frame", w, "LAST"),
                             ("from the middle of block 0", w[777:], "LAST"),
                             ("from the last frame before the last lead-in X-sync", w[B - 1:], "LAST"),
                             ("from the last lead-in X-sync", w[B:], "LAST"),
                             ("to just past the first tail X-sync", w[: 26 * B + 8], "STOP"),
                             ("to the middle of the first tail block", w[: 26 * B + 3000], "STOP"),
                             ("to the middle of the LAST block", w[: 27 * B + 3000], "LAST")):
            with self.subTest(name):
                r, _ = self.assert_pass(x, 48000)
                self.assertEqual(r["end"], end)

    def test_lead_in_and_tail_not_compared(self):
        """Changes in the lead-in and tail, outside their limit X-syncs, are
        not compared; nor are fades there unknown signal."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=4, body_blocks=8, tail_blocks=2))
        B = cfg.block_len
        rng = np.random.default_rng(1)
        fade = np.linspace(0.0, 1.0, 3 * B)[:, None]
        cases = {
            "noise in the lead-in payload": np.concatenate(
                [(rng.integers(-2 ** 20, 2 ** 20, (B, 2)) << 8).astype(np.int32), w[B:]]),
            "a fade-in over blocks 0 to 2": np.concatenate([(w[:3 * B] * fade).astype(np.int32), w[3 * B:]]),
            "100 dropped in the last lead-in block": sim.drop(w, 3 * B + 500, 100),
            "100 inserted in the lead-in": np.insert(w, 1000, zeros(100), axis=0),
            "a lead-in X-sync before the last one broken": flip(flip(w, B + 2, 0, 20), B + 2, 1, 20),
            "the last lead-in X-sync broken in one channel": flip(w, 3 * B + 2, 1, 20),
            "the first tail X-sync broken in one channel": flip(w, 12 * B + 2, 0, 20),
            "a fade-out after the first tail X-sync": np.concatenate(
                [w[:12 * B + 8], (w[12 * B + 8:] * np.linspace(1.0, 0.0, 2 * B - 8)[:, None]).astype(np.int32)]),
            "100 dropped in the tail": sim.drop(w, 12 * B + 500, 100),
        }
        for name, x in cases.items():
            with self.subTest(name):
                self.assert_pass(x, 48000)

    def test_after_the_body(self):
        """After the body: X-syncs of the file's earlier blocks do not decide
        where its tail starts; one that is not TAIL after its first TAIL
        X-sync starts the next file (end BACK)."""
        cfg, w = words(spec.KIND_PCM, 44100, 16)
        B = cfg.block_len
        with self.subTest("4099 frames repeated inside the tail X-sync: the tail X-sync after them"):
            x = np.insert(w, 10 * B + 2, w[10 * B + 2 - 4099:10 * B + 2], axis=0)
            self.assert_pass(x, 44100)
        with self.subTest("the last body block repeated after the body"):
            self.assert_pass(np.insert(w, 10 * B, w[9 * B:10 * B], axis=0), 44100)
        with self.subTest("a longer file of the same identity spliced in after the first tail block"):
            c1, a = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))
            _, b = words(spec.KIND_PCM, 48000, 24, LONG)
            B = c1.block_len
            res, st = self.run_c(np.concatenate([a[:11 * B], b[11 * B:]]), 48000)
            self.assertEqual([(r["verdict"], r["end"], r["why"]) for r in res],
                             [("BIT_PERFECT", "BACK", "none"), ("NOT_BIT_PERFECT", "LAST", "start")])
            self.assertEqual((res[1]["diff_unit"], st["outside"]), (str(11 * B), "0"))

    def test_r_headers(self):
        """The chips of r5 and r6 settle without a LAST block, and with one."""
        cfg, w = words(spec.KIND_PCM, 44100, 24, LONG)
        B = cfg.block_len
        self.assert_pass(w, 44100)
        r, _ = self.assert_pass(w[: 26 * B + 100], 44100)  # no LAST block: the two R-headers agree
        self.assertEqual(r["end"], "STOP")
        _, d = words(spec.KIND_DSD, spec.DSD_RATES[0], 1, LONG)
        self.assert_pass(d[: 26 * (len(d) // 28) + 100], spec.DSD_RATES[0] // 16)

    def test_chunking(self):
        """Results do not depend on how the frames are split across calls."""
        cfg, w = words(spec.KIND_PCM, 44100, 24)
        _, d = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        B, Bd = cfg.block_len, len(d) // 11
        streams = [("clean", w, 44100), ("a drop", sim.drop(w, 6 * B + 1500), 44100),
                   ("an R-header chip", flip(w, chip_frame(cfg, 8, 200, 3), 1, 29), 44100),
                   ("unknown signal around", np.concatenate([zeros(9000), w, zeros(9000), w]), 44100),
                   ("DoP, a frame dropped", np.delete(d, 6 * Bd + 3000, axis=0), spec.DSD_RATES[0] // 16)]
        for name, x, rate in streams:
            with self.subTest(name):
                outs = {str(self.run_c(x, rate, chunk=c)) for c in (1, 7, 333, 4096)}
                self.assertEqual(len(outs), 1, outs)

    # No false pass ---------------------------------------------------------

    def test_single_bit_flips(self):
        """A bit flipped in each class of body unit: the first difference is
        that unit. Source units: frames from the file's start."""
        cfg, w = words(spec.KIND_PCM, 48000, 24)
        B = cfg.block_len
        cases = [
            ("X-sync word 0, one channel", flip(w, 6 * B, 0, 20), 6 * B),
            ("X-sync word 3, all channels", flip(flip(w, 6 * B + 3, 0, 20), 6 * B + 3, 1, 20), 6 * B + 3),
            ("X-sync payload bits", flip(w, 6 * B + 5, 1, 8), 6 * B + 5),
            ("X-sync CRC word", flip(w, 6 * B + 7, 1, 16), 6 * B + 7),
            ("walking-bit burst", flip(w, 6 * B + 30, 1, 9), 6 * B + 30),
            ("payload, lowest bit", flip(w, 6 * B + 1500, 0, 8), 6 * B + 1500),
            ("payload, sign bit", flip(w, 6 * B + 1500, 1, 31), 6 * B + 1500),
            ("R-header zeros before the chips", flip(w, 8 * B + 100, 1, 8), 8 * B + 100),
            ("R-header Barker chip", flip(w, chip_frame(cfg, 8, 3, 5), 0, 29), chip_frame(cfg, 8, 3, 5)),
            ("R-header r0 chip", flip(w, chip_frame(cfg, 8, 20, 3), 0, 29), chip_frame(cfg, 8, 20, 3)),
            ("R-header r5 chip, inside", flip(w, chip_frame(cfg, 8, 180, 3), 0, 29), chip_frame(cfg, 8, 180, 3)),
            ("R-header r5 chip, its first unit", flip(w, chip_frame(cfg, 8, 180), 0, 30), chip_frame(cfg, 8, 180)),
            ("R-header r6 chip, second of a pair", flip(w, chip_frame(cfg, 8, 230, 5), 1, 29),
             chip_frame(cfg, 8, 230, 5)),
            ("R-header zeros after the chips", flip(w, 8 * B + 4000, 0, 8), 8 * B + 4000),
            ("the first body unit", flip(w, 2 * B, 0, 8), 2 * B),
            ("the last body unit", flip(w, 10 * B - 1, 1, 8), 10 * B - 1),
        ]
        for name, x, unit in cases:
            with self.subTest(name):
                self.assert_fail(x, 48000, unit)
        with self.subTest("an r5 chip unit that is neither value"):
            self.assert_fail(flip(w, chip_frame(cfg, 8, 175), 0, 30), 48000, chip_frame(cfg, 8, 175), "chip")
        with self.subTest("the two chips of a bit not opposite"):
            x = w.copy()
            a, b = chip_frame(cfg, 8, 180), chip_frame(cfg, 8, 181)
            x[b:b + (b - a)] = x[a:b]
            self.assert_fail(x, 48000)
        with self.subTest("a bit's second chip, its first unit at the first chip's value"):
            b = chip_frame(cfg, 8, CHIP_R5 + 9)
            x = w.copy()
            x[b, 0] = -x[b, 0]
            self.assert_fail(x, 48000, b)

    def test_r_header_count(self):
        """r5 and r6 spelled correctly for another count (a valid CRC): with
        the count known from the LAST block, and with it unknown."""
        cfg, w = words(spec.KIND_PCM, 48000, 24)
        _, other = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=9, tail_blocks=1))
        B = cfg.block_len
        x = w.copy()
        x[8 * B:9 * B] = other[8 * B:9 * B]
        self.assert_fail(x, 48000, chip_frame(cfg, 8, CHIP_R5), "count")
        cl, wl = words(spec.KIND_PCM, 48000, 24, LONG)
        _, ol = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=25, tail_blocks=2))
        for k in (8, 24):
            x = wl.copy()
            x[k * B:(k + 1) * B] = ol[k * B:(k + 1) * B]
            with self.subTest(f"block {k}, LAST known"):
                self.assert_fail(x, 48000, chip_frame(cl, k, CHIP_R5), "count")
            with self.subTest(f"block {k}, no LAST: the R-headers disagree"):
                self.assert_fail(x[: 26 * B + 100], 48000, chip_frame(cl, 24, CHIP_R5), "count")
        with self.subTest("a CRC that fails"):
            x = wl.copy()
            a, b = chip_frame(cl, 8, CHIP_R5 + 62), chip_frame(cl, 8, CHIP_R5 + 64)
            x[a:b] = -x[a:b]  # the last bit of r6, both chips inverted
            self.assert_fail(x[: 26 * B + 100], 48000, chip_frame(cl, 8, CHIP_R5), "crc")

    def test_dop_faults(self):
        cfg, w = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        F = frames_per_block(cfg)
        f = 6 * F + 3001
        s = 2 * f  # source unit: the frame's first DSD byte
        flipped_marker = w.copy()
        flipped_marker[f] = (w[f].view(np.uint32) ^ np.uint32(0xFF000000)).view(np.int32)
        cases = [
            ("a marker changed in one channel", flip(w, f, 0, 24), s, "marker"),
            ("a marker the same twice", flipped_marker, s, "marker"),
            ("padding below the 24 bits", flip(w, f, 1, 0), s, "marker"),
            ("a DSD bit of the first byte", flip(w, f, 0, 20), s, "unit"),
            ("a DSD bit of the second byte", flip(w, f, 1, 12), s + 1, "unit"),
            ("1000 frames of DSD bits inverted", (w.view(np.uint32) ^ np.where(
                (np.arange(len(w)) >= f) & (np.arange(len(w)) < f + 1000), np.uint32(0xFFFF00), np.uint32(0))[:, None]
            ).view(np.int32), s, "unit"),
            ("the X-sync's first DSD byte", flip(w, 6 * F, 0, 16), 6 * 2 * F, "unit"),
            ("a frame dropped", np.delete(w, f, axis=0), s, None),
            ("two frames dropped", np.delete(w, np.s_[f:f + 2], axis=0), s, None),
            ("a frame repeated", np.insert(w, f, w[f - 1], axis=0), None, None),
        ]
        for name, x, unit, why in cases:
            with self.subTest(name):
                self.assert_fail(x, spec.DSD_RATES[0] // 16, unit, why)
        with self.subTest("an R-header chip"):
            m = 64
            q = 180
            frame = 8 * F + (512 * m // 8 + q * 11 * m // 8 + 10) // 2
            self.assert_fail(flip(w, frame, 0, 12), spec.DSD_RATES[0] // 16, 2 * frame + 1)

    def test_gaps_and_order(self):
        cfg, w = words(spec.KIND_PCM, 48000, 24)
        B = cfg.block_len
        p = 6 * B + 1500
        cases = [
            ("one dropped", sim.drop(w, p), p, None),
            ("a packet dropped", sim.drop(w, p, 6), p, None),
            ("one repeated", sim.repeat(w, p), p, None),
            ("seven zeros inserted", np.insert(w, p, zeros(7), axis=0), p, None),
            ("ten zeroed", sim.zero(w, p, 10), p, None),
            ("a whole block dropped", np.delete(w, np.s_[6 * B:7 * B], axis=0), 6 * B, "unit"),
            ("a block repeated", np.insert(w, 7 * B, w[6 * B:7 * B], axis=0), 7 * B, "unit"),
            ("3 dropped in the R-header's zeros", sim.drop(w, 8 * B + 200, 3), None, None),
            ("3 dropped in the R-header chips", sim.drop(w, chip_frame(cfg, 8, 100), 3), None, None),
            ("a late start into the body", w[3 * B + 777:], 4 * B, "start"),  # the first block with an X-sync
            ("a start in the last lead-in block, after its X-sync", w[B + 100:], 2 * B, "start"),
            ("a start at the first body block", w[2 * B:], 2 * B, "start"),
            ("a stop in the body", w[: 6 * B + 100], 6 * B + 100, "left"),
            ("a stop in the last body block", w[: 10 * B - 1], 10 * B - 1, "left"),
            ("a stop just before the tail X-sync", w[: 10 * B], 10 * B, "left"),
            ("a stop inside the tail X-sync", w[: 10 * B + 5], 10 * B + 3, "end"),
            ("the tail X-sync broken in every channel", flip(flip(w, 10 * B + 3, 0, 20), 10 * B + 3, 1, 20),
             10 * B + 3, "end"),
            ("the last lead-in X-sync broken in every channel", flip(flip(w, B + 2, 0, 20), B + 2, 1, 20),
             B, "first"),  # the body taken to start after the last LEAD_IN X-sync seen
            ("the first body X-sync broken in every channel", flip(flip(w, 2 * B + 2, 0, 20), 2 * B + 2, 1, 20),
             2 * B, "first"),
            ("channels swapped", sim.swap(w), 2 * B, None),
            ("channel 0 on both", np.repeat(w[:, :1], 2, axis=1), 2 * B, None),
            ("truncated to 16 bits", sim.truncate(w, 16), 2 * B, None),
        ]
        for name, x, unit, why in cases:
            with self.subTest(name):
                self.assert_fail(x, 48000, unit, why)
        with self.subTest("cut short by the next file"):
            _, b = words(spec.KIND_PCM, 48000, 16)
            res = self.results(np.concatenate([w[: 7 * B + 50], b]), 48000)
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("NOT_BIT_PERFECT", "NEXT"),
                                                                       ("BIT_PERFECT", "LAST")])
        with self.subTest("the block index goes back"):
            res = self.results(np.concatenate([w[: 6 * B], w[4 * B:]]), 48000)
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("NOT_BIT_PERFECT", "BACK"),
                                                                       ("NOT_BIT_PERFECT", "LAST")])

    def test_rate_and_channels(self):
        cfg, w = words(spec.KIND_PCM, 48000, 24)
        r = self.assert_fail(w, 44100, 2 * cfg.block_len, "rate")
        self.assertEqual((r["bits"], r["diff"]), ("0", "0.171"))
        _, d = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        self.assert_fail(d, spec.DSD_RATES[1] // 16, None, "rate")
        mono = Config(kind=spec.KIND_PCM, rate=48000, depth=24, nch=1, **SHORT)
        m = (pcm_blocks(mono, 0, mono.total_blocks).astype(np.int64) << 8).astype(np.int32)
        r = self.assert_fail(np.repeat(m, 2, axis=1), 48000, 2 * cfg.block_len, "rate")
        self.assertEqual(r["nch"], "1")

    def test_review_sweep(self):
        """The gap sweep of an earlier engine's review (250 cases): a gap of
        every kind, size and place in the body is never BIT_PERFECT."""
        cases = []
        for rate, depth in ((48000, 24), (768000, 24)):
            s = 2 * -(-rate // 8000) + 1
            for size in (s + 1, 500, 4100, 6000):
                for how in ("drop", "zero", "repeat", "noise"):
                    for blk, off in ((6, 100), (6, 1500), (8, 300), (8, 2000), (7, 4000)):
                        cases.append((spec.KIND_PCM, rate, depth, size, how, blk, off))
        for rate in (spec.DSD_RATES[0], spec.DSD_RATES[2]):
            s = 2 * -(-(rate // 8) // 8000) + 1
            for size in (s // 2 + 1, 1000, 3000):
                for how in ("drop", "zero", "silence", "repeat", "noise"):
                    for blk, off in ((6, 1500), (8, 1000), (7, 4000)):
                        cases.append((spec.KIND_DSD, rate, 1, size, how, blk, off))
        self.assertEqual(len(cases), 250)
        for kind, rate, depth, *_ in cases:
            words(kind, rate, depth)  # generated before the threads

        def one(case):
            kind, rate, depth, size, how, blk, off = case
            cfg, w = words(kind, rate, depth)
            x = sweep_stream(cfg, w, size, how, blk * frames_per_block(cfg) + off)
            res = self.results(x, rate // 16 if cfg.dsd else rate)
            return case, [r["verdict"] for r in res if r["verdict"] != "UNKNOWN_SIGNAL"]

        with ThreadPoolExecutor(max_workers=min(12, os.cpu_count() or 1)) as pool:
            got = list(pool.map(one, cases))
        bad = [(case, v) for case, v in got if not v or "BIT_PERFECT" in v]
        self.assertEqual(bad, [], f"{len(bad)} of {len(cases)} not NOT_BIT_PERFECT")

    def test_review_cases(self):
        """An earlier engine's review cases: every one is NOT_BIT_PERFECT."""
        cfg, w = words(spec.KIND_PCM, 768000, 24)
        B = cfg.block_len
        p = 6 * B + 3000
        cases = []
        x = sim.drop(w, p, 6000)
        x[p + 20000, 0] ^= 1 << 12
        cases.append(("768k: 6000 dropped, a bit error 20000 frames later", x, 768000))
        x = sim.drop(w, p, 3000)
        x[p + 2000, 0] ^= 1 << 12
        cases.append(("768k: 3000 dropped, a bit error 2000 frames later", x, 768000))
        cases.append(("768k: 5000 zero frames inserted", np.insert(w, p, zeros(5000), axis=0), 768000))
        cd, wd = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        pd = 6 * frames_per_block(cd) + 3000
        mk = (wd[pd:pd + 3000].view(np.uint32) >> 24) & 0xFF
        sil = ((mk.astype(np.uint32) << 24) | (0x6969 << 8)).view(np.int32)
        cases.append(("DoP64: 3000 silence frames inserted", np.insert(wd, pd, sil, axis=0), spec.DSD_RATES[0] // 16))
        c4, w4 = words(spec.KIND_PCM, 48000, 24)
        B4 = c4.block_len
        cases.append(("48k: 500 frames repeated in the first R-header block",
                      np.insert(w4, 8 * B4 + 2000, w4[8 * B4 + 1500: 8 * B4 + 2000], axis=0), 48000))
        rng = np.random.default_rng(1)
        x = w4.copy()
        for k in (0, 1):
            seg = slice(k * B4 + 72, (k + 1) * B4)
            x[seg] = (x[seg].astype(np.int64) + (rng.integers(-1, 2, x[seg].shape) << 8)).astype(np.int32)
        cases.append(("48k: noise in the lead-in, 100 dropped in body block 2", sim.drop(x, 2 * B4 + 1500, 100), 48000))
        cases.append(("48k: the first body block's X-sync broken", flip(w4, 2 * B4 + 2, 0, 20), 48000))
        cases.append(("48k: 100 dropped, then the stream stops in that span",
                      sim.drop(w4, 6 * B4 + 1500, 100)[: 6 * B4 + 3500], 48000))
        cases.append(("768k: 6000 dropped, then 3 more in the same span",
                      sim.drop(sim.drop(w, 6 * B + 20000, 3), 6 * B + 3000, 6000), 768000))
        for name, x, rate in cases:
            with self.subTest(name):
                self.assert_fail(x, rate)
        with self.subTest("a lead-in X-sync broken and 300 dropped in the lead-in: the body intact"):
            c5, w5 = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=4, body_blocks=8, tail_blocks=1))
            x = flip(w5, c5.block_len + 2, 0, 20)
            self.assert_pass(sim.drop(x, c5.block_len - 50, 300), 48000)

    def test_sweep(self):
        """PCM code-sweep files (kind SWEEP): clean at D = 16 and 24, and a
        single code changed (the negative sweep files' fault)."""
        for rate, depth in ((44100, 16), (48000, 24), (96000, 16), (768000, 24)):
            with self.subTest(rate=rate, depth=depth):
                cfg, w = sweep_words(rate, depth)
                r, st = self.assert_pass(w, rate)
                self.assertEqual((r["kind"], r["depth"], r["bits"], r["end"], r["dop"]),
                                 ("SWEEP", str(depth), str(depth), "LAST", "0"))
                self.assertEqual(int(r["compared"]), 8 * cfg.block_len)
                B = cfg.block_len
                for frame in (2 * B + 72, 6 * B + 1500, 10 * B - 1):
                    self.assert_fail(flip(w, frame, 1, 32 - depth), rate, frame, "unit")
        with self.subTest("the code 2^(D-1) - 2 of channel 0 becomes 2^(D-1) - 1 in the body"):
            for depth in (16, 24):
                cfg, w = sweep_words(44100, depth, LONG)
                B = cfg.block_len
                code = np.int64((1 << (depth - 1)) - 2) << (32 - depth)
                body = slice(2 * B, 26 * B)
                hits = np.nonzero(w[body, 0].astype(np.int64) == code)[0]
                x = w.copy()
                x[2 * B + hits, 0] += np.int32(1 << (32 - depth))
                if len(hits):
                    self.assert_fail(x, 44100, 2 * B + int(hits[0]), "unit")
                else:
                    self.assert_pass(x, 44100)  # the body has no such code: nothing changed
        with self.subTest("a PCM file is not a sweep file, nor the other way round"):
            _, p = words(spec.KIND_PCM, 48000, 24)
            _, sw = sweep_words(48000, 24)
            res = self.results(np.concatenate([p, sw, p]), 48000)
            self.assertEqual([(r["verdict"], r["kind"]) for r in res],
                             [("BIT_PERFECT", "PCM"), ("BIT_PERFECT", "SWEEP"), ("BIT_PERFECT", "PCM")])
        with self.subTest("a sweep X-sync of depth 32 is not valid"):
            cfg, w32 = words(spec.KIND_PCM, 48000, 32)
            x = w32.view(np.uint32).copy()
            for k in range(cfg.total_blocks):  # each X-sync's kind made SWEEP, its CRC valid
                for c in range(2):
                    f = k * cfg.block_len
                    wd = [int(v) >> 16 for v in x[f:f + 8, c]]
                    wd[2] = (wd[2] & 0xF0FF) | (spec.KIND_SWEEP << 8)
                    wd[7] = spec.crc16(b"".join(v.to_bytes(2, "big") for v in wd[:7]))
                    x[f:f + 8, c] = [(v << 16) | (int(o) & 0xFFFF) for v, o in zip(wd, x[f:f + 8, c])]
            res, st = self.run_c(x.view(np.int32), 48000)
            self.assertEqual([r["verdict"] for r in res], ["UNKNOWN_SIGNAL"])
            x16 = x.copy()  # the same at depth code 2 (16 bits) is a sweep file's X-sync
            for k in range(cfg.total_blocks):
                for c in range(2):
                    f = k * cfg.block_len
                    wd = [int(v) >> 16 for v in x16[f:f + 8, c]]
                    wd[2] = (wd[2] & 0xFF0F) | (2 << 4)
                    wd[7] = spec.crc16(b"".join(v.to_bytes(2, "big") for v in wd[:7]))
                    x16[f:f + 8, c] = [(v << 16) | (int(o) & 0xFFFF) for v, o in zip(wd, x16[f:f + 8, c])]
            res, st = self.run_c(x16.view(np.int32), 48000)
            self.assertEqual([(r["verdict"], r["kind"], r["depth"]) for r in res], [("NOT_BIT_PERFECT", "SWEEP", "16")])

    def test_sweep_against_verify(self):
        """Sweep streams, clean and changed: the same results as
        checker/verify.py."""
        rng = random.Random(5)
        cases = []
        for rate, depth in ((44100, 16), (48000, 24), (192000, 24), (768000, 16)):
            cfg, w = sweep_words(rate, depth)
            B = cfg.block_len
            cases.append((f"{rate}/{depth} clean", w, rate))
            for _ in range(6):
                f = rng.randrange(0, len(w) - 1)
                op = rng.choice(["flip", "drop", "rep", "zero", "stop", "start"])
                if op == "flip":
                    x = flip(w, f, rng.randint(0, 1), rng.choice([32 - depth, 31, 20]))
                elif op == "drop":
                    x = sim.drop(w, f, rng.choice([1, 6, 500]))
                elif op == "rep":
                    x = sim.repeat(w, f)
                elif op == "zero":
                    x = sim.zero(w, f, 3)
                elif op == "stop":
                    x = w[:f]
                else:
                    x = w[f:]
                cases.append((f"{rate}/{depth} {op} at {f}", x, rate))
            code = np.int64((1 << (depth - 1)) - 2) << (32 - depth)
            x = w.copy()
            hit = x[:, 0].astype(np.int64) == code
            hit[: 2 * B] = hit[10 * B:] = False
            x[hit, 0] += np.int32(1 << (32 - depth))
            cases.append((f"{rate}/{depth} the code 2^(D-1)-2 of channel 0 changed in the body", x, rate))
        for name, x, rate in cases:
            with self.subTest(name):
                self.assertEqual(c_key(self.results(x, rate)), py_results(x, rate))

    # Against the reference -------------------------------------------------

    def test_verdict_vectors(self):
        """Every verdict case of the vectors file: the fields its results give
        (verdict, diff, end), and the end and bits checker/verify.py gives."""
        with open(VECTORS) as f:
            v = json.load(f)["verdicts"]
        self.assertGreater(len(v["cases"]), 50)
        for case in v["cases"]:
            with self.subTest(case["case"]):
                st = build_case(v, case)
                sil = case.get("allow_silence", False)
                res = self.results(st.data, st.rate, slot=st.slot_bits, silence=sil)
                want = case["results"]  # verdict and diff, and every other field a case gives (end)
                self.assertEqual([{k: r[k] for k in w} for r, w in zip(res, want)] if len(res) == len(want) else res,
                                 want)
                self.assertEqual(c_key(res), py_results(st.data, st.rate, st.slot_bits, sil))

    def test_units_against_verify(self):
        """The engine and checker/verify.py split a stream alike (12.6 Files
        in a stream): every result ends at the same unit, so a file's dur
        (from its first X-sync, the same in both) agrees, and every stretch
        of unknown signal starts at the same unit."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        _, a = words(spec.KIND_PCM, 48000, 24)
        _, v = words(spec.KIND_PCM, 48000, 16, LONG)
        lost0 = w.copy()
        lost0[:8] = 0  # block 0's X-sync lost
        v0 = v.copy()
        v0[:8] = 0
        rate = spec.DSD_RATES[0]
        cp, p176 = words(spec.KIND_PCM, rate // 16, 24)
        c3, d3 = words(spec.KIND_DSD, rate, 1)
        d0 = d3.copy()
        d0[:16] = dop_idle(16)  # block 0's X-sync lost
        F = frames_per_block(c3)
        for name, x, r in (("LAST, then unknown signal", np.concatenate([a, zeros(3 * B)]), 48000),
                           ("LAST, then the next file", np.concatenate([a, a]), 48000),
                           ("NEXT, the next file's block 0 X-sync lost", np.concatenate([w[: 10 * B], v0]), 48000),
                           ("BACK, the next file's block 0 X-sync lost", np.concatenate([w[: 10 * B + 2000], lost0]), 48000),
                           ("BACK, cut inside a block", np.concatenate([w[: 10 * B + 2000], w]), 48000),
                           ("cut, then unknown signal", np.concatenate([w[: 10 * B], zeros(3 * B)]), 48000),
                           ("PCM, then DoP, cut inside a block", np.concatenate([p176[: 10 * cp.block_len + 2000], d3]), rate // 16),
                           ("DoP, then DoP, its block 0 X-sync lost", np.concatenate([d3[: 10 * F], d0]), rate // 16),
                           # another transport: the boundary is at the next file's first X-sync seen
                           ("PCM, then DoP, its block 0 X-sync lost", np.concatenate([p176[: 10 * cp.block_len], d0]),
                            rate // 16)):
            with self.subTest(name):
                c = self.results(x, r)
                py = py_verify(x, r)
                self.assertEqual([(q["verdict"], q["end"], int(q["first_frame"]) + int(q["frames"])) for q in c],
                                 [(q["verdict"], q["end"], q["first"] + q["units"]) for q in py])
                self.assertEqual([int(q["first_frame"]) for q in c if q["verdict"] == "UNKNOWN_SIGNAL"],
                                 [q["first"] for q in py if q["verdict"] == "UNKNOWN_SIGNAL"])

    def test_diagnosis_splits_alike(self):
        """The diagnosis (checker/check.py, by checker/exact.py split_files)
        ends a file that another file follows where checker/verify.py does:
        at its LAST block's end, or a cut file at the next file's first
        X-sync (12.6 Files in a stream)."""
        from checker import check
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        _, a = words(spec.KIND_PCM, 48000, 24)
        _, v = words(spec.KIND_PCM, 48000, 16, LONG)
        v0 = v.copy()
        v0[:8] = 0
        lost0 = w.copy()
        lost0[:8] = 0
        rate = spec.DSD_RATES[0]
        c3, d3 = words(spec.KIND_DSD, rate, 1)
        d0 = d3.copy()
        d0[:16] = dop_idle(16)
        F = frames_per_block(c3)
        for name, x, r in (("LAST, then the next file", np.concatenate([a, a]), 48000),
                           ("NEXT, the next file's block 0 X-sync lost", np.concatenate([w[:10 * B], v0]), 48000),
                           ("BACK, the next file's block 0 X-sync lost", np.concatenate([w[:10 * B + 2000], lost0]), 48000),
                           ("BACK, cut inside a block", np.concatenate([w[:10 * B + 2000], w]), 48000),
                           ("DoP, then DoP, its block 0 X-sync lost", np.concatenate([d3[:10 * F], d0]), rate // 16)):
            with self.subTest(name):
                x32 = np.ascontiguousarray(x)
                st = readers.Stream(False, r, 2, x32, "test", slot_bits=32, valid_bits=32)
                py = [q for q in verify.verify_stream(st) if q["verdict"] != "UNKNOWN_SIGNAL"]
                dg = check.diagnose_stream(st)
                scale = 2 if py[0]["dop"] else 1
                self.assertEqual([d["received"]["first"] + d["received"]["units"] for d in dg[:-1]],
                                 [scale * (q["first"] + q["units"]) for q in py[:-1]])

    def test_fuzz_against_verify(self):
        """Random changes (fixed seed): the same results as checker/verify.py."""
        rng = random.Random(1)
        cases = [fuzz_case(rng) for _ in range(FUZZ_TRIALS)]

        def one(case):
            x, rate, slot, chunk, sil, ops = case
            return ops, c_key(self.results(x, rate, slot=slot, chunk=chunk, silence=sil))

        with ThreadPoolExecutor(max_workers=min(12, os.cpu_count() or 1)) as pool:
            got = list(pool.map(one, cases))
        bad = []
        for (x, rate, slot, chunk, sil, _), (ops, c) in zip(cases, got):
            p = py_results(x, rate, slot, sil)
            if c != p:
                bad.append((ops, rate, slot, sil, c, p))
        self.assertEqual(bad, [], f"{len(bad)} of {len(cases)} differ")

    def fuzz(self, cases):
        """C against checker/verify.py on (stream, rate, slot, chunk,
        allow_silence, changes) cases: the cases that differ."""
        def one(case):
            x, rate, slot, chunk, sil, ops = case
            return c_key(self.results(x, rate, slot=slot, chunk=chunk, silence=sil))

        with ThreadPoolExecutor(max_workers=min(12, os.cpu_count() or 1)) as pool:
            got = list(pool.map(one, cases))
        return [(ops, rate, slot, sil, c, p) for (x, rate, slot, chunk, sil, ops), c in zip(cases, got)
                if c != (p := py_results(x, rate, slot, sil))]

    def test_fuzz_streams_against_verify(self):
        """Streams of 2 or 3 files, changed or not, gapless or with short gaps
        (fixed seed): the same results as checker/verify.py."""
        rng = random.Random(2)
        cases = [multi_case(rng) for _ in range(MULTI_TRIALS)]
        bad = self.fuzz(cases)
        self.assertEqual(bad, [], f"{len(bad)} of {len(cases)} differ")

    def test_fuzz_sweep_against_verify(self):
        """Code-sweep files with random changes (fixed seed): the same results
        as checker/verify.py."""
        rng = random.Random(3)
        cases = [sweep_case(rng) for _ in range(SWEEP_TRIALS)]
        bad = self.fuzz(cases)
        self.assertEqual(bad, [], f"{len(bad)} of {len(cases)} differ")

    def test_rhdr_faults_against_verify(self):
        """Random faults in the r5 and r6 chips of body R-headers (fixed seed),
        PCM and DoP: the same verdict, end and first difference, to the
        source unit, as checker/verify.py (12.6, the R-header rule)."""
        for dop in (False, True):
            with self.subTest("DoP" if dop else "PCM"):
                rng = random.Random(4 + dop)
                cases = [rhdr_case(rng, dop) for _ in range(RHDR_TRIALS)]
                with ThreadPoolExecutor(max_workers=min(12, os.cpu_count() or 1)) as pool:
                    got = list(pool.map(lambda c: unit_key(self.results(c[0], c[1], slot=c[2])), cases))
                bad = [(ops, c, p) for (x, rate, slot, ops), c in zip(cases, got)
                       if c != (p := unit_key(py_verify(x, rate, slot)))]
                self.assertEqual(bad, [], f"{len(bad)} of {len(cases)} differ")

    def test_review_differences(self):
        """Streams on which the two checkers differed in a review, with what
        12.6 gives: both give it now."""
        cfg, a = words(spec.KIND_PCM, 48000, 24, LONG)
        _, lead12 = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=12, body_blocks=14, tail_blocks=2))
        B = cfg.block_len
        cases = []
        # A splice of two files of one identity with different lead-ins: the
        # X-sync of block 6 flagged LEAD_IN is not a body block's
        cases.append(("L is not taken from later LEAD_IN X-syncs", np.concatenate([a[:6 * B], lead12[6 * B:]]),
                      [("NOT_BIT_PERFECT", "LAST", "0.512", "24")]))
        # X-syncs whose channels carry different flags: received channel c
        # against source channel c
        for name, dst, src in (("the first body block's X-sync in channel 1 the last lead-in one's", 2 * B, B),
                               ("the last body block's X-sync in channel 1 the first tail one's", 25 * B, 26 * B)):
            y = a.copy()
            y[dst:dst + 8, 1] = a[src:src + 8, 1]
            cases.append((name, y, [("NOT_BIT_PERFECT", "LAST", "%.3f" % ((dst + 3) / 48000), "24")]))
        # The stream stops in the r6 chips after an r5 chip unit that is
        # neither value: the diff is that unit
        q0 = chip_frame(cfg, 8, CHIP_R5)
        y = a.copy()
        y[q0 + 30, 0] ^= np.int32(1 << 8)
        cases.append(("an r5 chip unit, then a stop in the r6 chips", y[:q0 + 50 * 12],
                      [("NOT_BIT_PERFECT", "STOP", "0.737", "24")]))
        # A pair not opposite (chip r5+3 at the first chip's value), then an r5
        # chip zeroed (r5+27): the pair is found first, at its second chip
        q3, q27 = chip_frame(cfg, 8, CHIP_R5 + 3), chip_frame(cfg, 8, CHIP_R5 + 27)
        y = a.copy()
        y[q3:q3 + 12, 0] = -y[q3:q3 + 12, 0]
        y[q27:q27 + 12, 0] = 0
        cases.append(("a pair not opposite, then a chip zeroed", y, [("NOT_BIT_PERFECT", "LAST", "0.737", "24")]))
        # diff at an exact half millisecond: 61944 / 48000 = 1.2905 s
        cases.append(("diff at an exact half millisecond", flip(a, 61944, 0, 8),
                      [("NOT_BIT_PERFECT", "LAST", "1.291", "24")]))
        for name, x, want in cases:
            with self.subTest(name):
                self.assertEqual(c_key(self.results(x, 48000)), want)
                self.assertEqual(py_results(x, 48000), want)

    # Unknown signal, silence, overruns -------------------------------------

    def test_unknown_signal(self):
        cfg, a = words(spec.KIND_PCM, 44100, 24)
        _, b = words(spec.KIND_PCM, 44100, 16)
        rng = np.random.default_rng(3)
        noise = (rng.integers(-2 ** 23, 2 ** 23, (200000, 2)) << 8).astype(np.int32)
        for name, x in (("noise", noise), ("zeros", zeros(50000)), ("inverted", sim.invert(a, 24)),
                        ("DoP without markers", (words(spec.KIND_DSD, spec.DSD_RATES[0], 1)[1].view(np.uint32)
                                                 & np.uint32(0x00FFFFFF)).view(np.int32))):
            with self.subTest(name):
                res, st = self.run_c(x, 44100)
                self.assertEqual([(r["verdict"], r["kind"], r["end"], r["diff"], r["seed"], r["nch"], r["file_rate"],
                                   r["depth"]) for r in res],
                                 [("UNKNOWN_SIGNAL", "NONE", "STOP", "-", "0x00000000", "0", "0", "0")])
                self.assertEqual((int(st["outside"]), int(res[0]["frames"])), (len(x), len(x)))
        with self.subTest("a bit-perfect file, then the same rounded to 16 bits"):
            res = self.results(np.concatenate([a, sim.quantize(a / 2.0 ** 31, 16)]), 44100)
            self.assertEqual([r["verdict"] for r in res], ["BIT_PERFECT", "UNKNOWN_SIGNAL"])
        with self.subTest("a stretch between files, before and after"):
            res, st = self.run_c(np.concatenate([noise[:30000], a, zeros(30000), b, noise[:30000]]), 44100)
            self.assertEqual([r["verdict"] for r in res], ["UNKNOWN_SIGNAL", "BIT_PERFECT", "UNKNOWN_SIGNAL",
                                                           "BIT_PERFECT", "UNKNOWN_SIGNAL"])
            margin = cfg.block_len  # one block at each end of a file's extent
            self.assertEqual([int(r["frames"]) for r in res if r["verdict"] == "UNKNOWN_SIGNAL"],
                             [30000 - margin, 30000 - 2 * margin, 30000 - margin])
            self.assertEqual(int(st["outside"]), 90000 - 4 * margin)
        with self.subTest("within a block of a file: its extent"):
            res, st = self.run_c(np.concatenate([zeros(4000), a, zeros(4000), b, zeros(4000)]), 44100)
            self.assertEqual(([r["verdict"] for r in res], st["outside"]), (["BIT_PERFECT", "BIT_PERFECT"], "0"))
        with self.subTest("the extent's end from an R-header's count, with the LAST X-sync broken"):
            cl, wl = words(spec.KIND_PCM, 44100, 24, LONG)
            B = cl.block_len
            x = np.concatenate([flip(flip(wl, 27 * B + 2, 0, 20), 27 * B + 2, 1, 20), zeros(B + 10)])
            res, st = self.run_c(x, 44100)
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("BIT_PERFECT", "STOP"), ("UNKNOWN_SIGNAL", "STOP")])
            self.assertEqual(st["outside"], "10")

    def test_allow_silence(self):
        _, a = words(spec.KIND_PCM, 48000, 24)
        _, b = words(spec.KIND_PCM, 48000, 16)
        _, d = words(spec.KIND_DSD, spec.DSD_RATES[0], 1)
        z = zeros(30000)
        rng = np.random.default_rng(2)
        quiet = (rng.integers(-2, 3, (30000, 2)) << 8).astype(np.int32)  # not silence
        dr = spec.DSD_RATES[0] // 16
        cases = [  # stream, rate, results strict, results allowed
            ("zeros at the start", np.concatenate([z, a]), 48000, ["UNKNOWN_SIGNAL", "BIT_PERFECT"], ["BIT_PERFECT"]),
            ("zeros between files", np.concatenate([a, z, b]), 48000,
             ["BIT_PERFECT", "UNKNOWN_SIGNAL", "BIT_PERFECT"], ["BIT_PERFECT", "BIT_PERFECT"]),
            ("zeros at the end", np.concatenate([a, z]), 48000, ["BIT_PERFECT", "UNKNOWN_SIGNAL"], ["BIT_PERFECT"]),
            ("zeros only", z, 48000, ["UNKNOWN_SIGNAL"], []),
            ("near-silence at the start", np.concatenate([quiet, a]), 48000,
             ["UNKNOWN_SIGNAL", "BIT_PERFECT"], ["UNKNOWN_SIGNAL", "BIT_PERFECT"]),
            ("zeros, then near-silence, at the end", np.concatenate([a, z, quiet]), 48000,
             ["BIT_PERFECT", "UNKNOWN_SIGNAL"], ["BIT_PERFECT", "UNKNOWN_SIGNAL"]),
            ("DoP idle between DoP files", np.concatenate([d, dop_idle(60000), d]), dr,
             ["BIT_PERFECT", "UNKNOWN_SIGNAL", "BIT_PERFECT"], ["BIT_PERFECT", "BIT_PERFECT"]),
            ("DoP idle with the 0x96 byte at the end", np.concatenate(
                [d, (dop_idle(60000).view(np.uint32) ^ np.uint32(0x00FFFF00)).view(np.int32)]), dr,
             ["BIT_PERFECT", "UNKNOWN_SIGNAL"], ["BIT_PERFECT"]),
            ("DoP idle with a bad marker", np.concatenate([d, flip(dop_idle(60000), 50000, 0, 25)]), dr,
             ["BIT_PERFECT", "UNKNOWN_SIGNAL"], ["BIT_PERFECT", "UNKNOWN_SIGNAL"]),
        ]
        for name, x, rate, strict, allowed in cases:
            with self.subTest(name):
                res, st = self.run_c(x, rate)
                self.assertEqual([r["verdict"] for r in res], strict)
                self.assertEqual(st["silent"], "0")
                res, st2 = self.run_c(x, rate, silence=True)
                self.assertEqual([r["verdict"] for r in res], allowed)
                self.assertEqual(st2["outside"], st["outside"])
                if len(allowed) < len(strict):
                    self.assertEqual(st2["silent"], st["outside"])
        with self.subTest("zeros inside the body are a difference under either setting"):
            cfg, _ = words(spec.KIND_PCM, 48000, 24)
            self.assert_fail(sim.zero(a, 6 * cfg.block_len + 1500, 10), 48000, 6 * cfg.block_len + 1500, silence=True)

    def test_overrun(self):
        """An overrun inside a file makes it not judged, unless the file's
        first difference came before it; in a stretch outside every file, an
        overrun makes that stretch not judged."""
        _, a = words(spec.KIND_PCM, 48000, 24)
        res = self.results(np.concatenate([a, a]), 48000, overruns=(len(a) + 20000,))
        self.assertEqual([(r["verdict"], r["diff"]) for r in res], [("BIT_PERFECT", "-"), ("OVERRUN", "-")])
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        x = flip(w, 5 * B + 100, 0, 8)
        for name, over, want in (("a difference, then an overrun", 20 * B, ("NOT_BIT_PERFECT", "0.429")),
                                 ("an overrun, then a difference", 3 * B, ("OVERRUN", "-")),
                                 ("an overrun at the differing frame", 5 * B + 100, ("OVERRUN", "-")),
                                 ("an overrun in the lead-in, no difference", B // 2, ("OVERRUN", "-")),
                                 ("an overrun in the body, no difference", 20 * B, ("OVERRUN", "-"))):
            with self.subTest(name):
                r = self.one(w if "no difference" in name else x, 48000, overruns=(over,))
                self.assertEqual((r["verdict"], r["diff"]), want)
        with self.subTest("a difference at the body's start, then an overrun"):
            res = self.results(np.concatenate([sim.truncate(a, 16), a]), 48000, overruns=(20000,))
            self.assertEqual([(r["verdict"], r["diff"]) for r in res], [("NOT_BIT_PERFECT", "0.171"), ("BIT_PERFECT", "-")])
        with self.subTest("two overruns, the first before the difference"):
            r = self.one(x, 48000, overruns=(3 * B, 20 * B))
            self.assertEqual((r["verdict"], r["diff"]), ("OVERRUN", "-"))
        with self.subTest("an overrun that drops whole r5 bit pairs"):
            for k, n in ((8, 24), (8, 48), (24, 24), (24, 48)):
                d = chip_frame(cfg, k, CHIP_R5 + 4)  # the chips after it shift by one or two bits
                r = self.one(np.delete(w, np.s_[d:d + n], axis=0), 48000, overruns=(d,))
                self.assertEqual((r["verdict"], r["diff"]), ("OVERRUN", "-"), (k, n))
        with self.subTest("a body left after an overrun"):
            r = self.one(w[: 10 * B], 48000, overruns=(8 * B,))
            self.assertEqual((r["verdict"], r["diff"]), ("OVERRUN", "-"))
        # The stream stops where the overrun is (a device that told it at
        # the end): the first unit that did not arrive is at the overrun,
        # not before it
        for cut in (10 * B, 10 * B + 100, 10 * B + 1):
            with self.subTest("an overrun where the received frames end", cut=cut):
                r = self.one(w[:cut], 48000, overruns=(cut,))
                self.assertEqual((r["verdict"], r["diff"]), ("OVERRUN", "-"))
        with self.subTest("a difference, then an overrun where the received frames end"):
            r = self.one(x[: 10 * B], 48000, overruns=(10 * B,))
            self.assertEqual((r["verdict"], r["diff"]), ("NOT_BIT_PERFECT", "0.429"))
        res = self.results(np.concatenate([a, zeros(40000), a]), 48000, overruns=(len(a) + 20000,))
        self.assertEqual([r["verdict"] for r in res], ["BIT_PERFECT", "OVERRUN", "BIT_PERFECT"])
        res = self.results(np.concatenate([a, zeros(40000)]), 48000, overruns=(len(a) + 30000,), silence=True)
        self.assertEqual([r["verdict"] for r in res], ["BIT_PERFECT", "OVERRUN"])


    def test_overrun_at_the_stream_end(self):
        """An overrun where the stream ends (a device that tells it at its
        stop) stands before a unit that never came: it is the result's that
        holds the last unit received. That is the open file's in every
        state, OVERRUN where it would have been NOT_BIT_PERFECT and where
        it would have passed. Outside its extent it is the stretch's after
        it; after the file's LAST block, with no stretch after it, no
        result's."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len  # LONG: blocks 0-1 lead-in, 2-25 body, 26-27 tail, 27 LAST
        for name, n, without in (("in the lead-in", B + 100, ("NOT_BIT_PERFECT", "0.171", "STOP")),
                                 ("the body done, the tail's X-sync not in", 26 * B, ("NOT_BIT_PERFECT", "2.219", "STOP")),
                                 ("after the tail's X-sync, a passed body", 26 * B + 2000, ("BIT_PERFECT", "-", "STOP")),
                                 ("inside the LAST block", 27 * B + 2000, ("BIT_PERFECT", "-", "LAST"))):
            with self.subTest(name):
                r = self.one(w[:n], 48000)
                self.assertEqual((r["verdict"], r["diff"], r["end"]), without)
                r = self.one(w[:n], 48000, overruns=(n,))
                self.assertEqual((r["verdict"], r["diff"], r["end"]), ("OVERRUN", "-", without[2]))
        with self.subTest("at the LAST block's end: no result's"):
            res = self.results(w, 48000, overruns=(len(w),))
            self.assertEqual([(r["verdict"], r["kind"], r["end"], r["first_frame"], r["frames"]) for r in res],
                             [("BIT_PERFECT", "PCM", "LAST", "0", str(len(w)))])
        with self.subTest("DoP, in the body"):
            rate = spec.DSD_RATES[0]
            c2, d = words(spec.KIND_DSD, rate, 1, LONG)
            n = 10 * frames_per_block(c2) + 37
            self.assertEqual(self.one(d[:n], rate // 16)["verdict"], "NOT_BIT_PERFECT")
            r = self.one(d[:n], rate // 16, overruns=(n,))
            self.assertEqual((r["verdict"], r["diff"]), ("OVERRUN", "-"))
        _, a = words(spec.KIND_PCM, 48000, 24)
        with self.subTest("outside the extent: the trailing stretch's"):
            x = np.concatenate([a, zeros(3 * B)])
            res = self.results(x, 48000, overruns=(len(x),))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("BIT_PERFECT", "LAST"), ("OVERRUN", "STOP")])
        with self.subTest("where the extent ends too: the file's, no stretch after it"):
            cfg3, a3 = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))
            x = a3.copy()
            x[11 * B:11 * B + 8] = 0  # the LAST X-sync: the extent ends a block after block 11
            y = np.concatenate([x, zeros(B)])  # the stream ends there, at 13 blocks
            self.assertEqual([(r["verdict"], r["end"]) for r in self.results(y, 48000)], [("BIT_PERFECT", "STOP")])
            res = self.results(y, 48000, overruns=(13 * B,))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("OVERRUN", "STOP")])

    def test_overrun_boundaries(self):
        """An overrun belongs to the result that holds the units it stands
        before (12.6 verdict 2). A file cut without completing its LAST
        block holds the units up to the next file's first X-sync, and an
        overrun there; after a complete file or a stretch, an overrun at
        the boundary is the later result's."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        _, v = words(spec.KIND_PCM, 48000, 16, LONG)  # another file
        v = v.copy()
        v[:8] = 0  # its block 0 X-sync: its first X-sync seen is block 1's
        nxt = np.concatenate([w[: 10 * B], v])  # NEXT: file 2 from 10 B, its first X-sync seen at 11 B
        # the first file is cut at 11 B, the second's first X-sync seen
        for name, over, want in (("NEXT, before the second file's start", 10 * B - 100, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                                 ("NEXT, at the second file's start", 10 * B, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                                 # the first file's first difference, at 10 B, came before it
                                 ("NEXT, between the second file's start and the cut", 10 * B + 100,
                                  [("NOT_BIT_PERFECT", "0.853"), ("BIT_PERFECT", "-")]),
                                 ("NEXT, at the cut", 11 * B, [("NOT_BIT_PERFECT", "0.853"), ("BIT_PERFECT", "-")]),
                                 ("NEXT, after the cut", 11 * B + 1,
                                  [("NOT_BIT_PERFECT", "0.853"), ("OVERRUN", "-")])):
            with self.subTest(name):
                res = self.results(nxt, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["diff"]) for r in res], want)
                # the first file's frames end at the second's first X-sync seen
                self.assertEqual((res[0]["end"], res[0]["frames"]), ("NEXT", str(11 * B)))
        u = w.copy()
        u[:8] = 0
        back = np.concatenate([w[: 10 * B], u])  # BACK: the same file again
        for name, over, want in (("BACK, before the second file's start", 10 * B - 1, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                                 ("BACK, at the second file's start", 10 * B, [("OVERRUN", "-"), ("BIT_PERFECT", "-")])):
            with self.subTest(name):
                res = self.results(back, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["end"], r["diff"]) for r in res][0][1], "BACK")
                self.assertEqual([(r["verdict"], r["diff"]) for r in res], want)
        _, a = words(spec.KIND_PCM, 48000, 24)  # SHORT: blocks 0-10, LAST at 10, its end 11 B, extent 12 B
        n = len(a)
        tail = np.concatenate([a, zeros(3 * B)])
        for name, over, want in (("LAST, then unknown signal: before the end", n - 1, [("OVERRUN", "LAST"), ("UNKNOWN_SIGNAL", "STOP")]),
                                 ("LAST, then unknown signal: at the end", n, [("BIT_PERFECT", "LAST"), ("OVERRUN", "STOP")]),
                                 ("LAST, then unknown signal: in the margin", n + 100, [("BIT_PERFECT", "LAST"), ("OVERRUN", "STOP")])):
            with self.subTest(name):
                res = self.results(tail, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["end"]) for r in res], want)
        margin = np.concatenate([a, zeros(B // 2)])  # the stream ends in the margin block
        for name, over in (("the stream's end in the margin", n + B // 2), ("in the margin, the stream ending there", n + 10)):
            with self.subTest(name):
                res = self.results(margin, 48000, overruns=(over,))
                # no result's: the device shows it on its stream stop line only
                self.assertEqual([(r["verdict"], r["kind"], r["end"], r["first_frame"], r["frames"]) for r in res],
                                 [("BIT_PERFECT", "PCM", "LAST", "0", str(n))])
        lead = np.concatenate([zeros(2 * B), a])  # the file's extent starts a block before its first frame, at B
        for name, over, want in (("unknown signal, then a file: before its extent", B - 1, [("OVERRUN", "STOP"), ("BIT_PERFECT", "LAST")]),
                                 ("unknown signal, then a file: at its extent's start", B, [("UNKNOWN_SIGNAL", "STOP"), ("OVERRUN", "LAST")]),
                                 ("the stream's first frame", 0, [("OVERRUN", "STOP"), ("BIT_PERFECT", "LAST")])):
            with self.subTest(name):
                res = self.results(lead, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["end"]) for r in res], want)
        with self.subTest("the stream's first frame, a file there"):
            self.assertEqual(self.one(a, 48000, overruns=(0,))["verdict"], "OVERRUN")
        rate = spec.DSD_RATES[0]
        c2, d = words(spec.KIND_DSD, rate, 1)
        F = frames_per_block(c2)
        nd = len(d)
        dtail = np.concatenate([d, dop_idle(3 * F)])
        for name, over, want in (("DoP: LAST, then idle: before the end", nd - 1, [("OVERRUN", "LAST"), ("UNKNOWN_SIGNAL", "STOP")]),
                                 ("DoP: LAST, then idle: at the end", nd, [("BIT_PERFECT", "LAST"), ("OVERRUN", "STOP")])):
            with self.subTest(name):
                res = self.results(dtail, rate // 16, overruns=(over,))
                self.assertEqual([(r["verdict"], r["end"]) for r in res], want)
        # The next file's first X-sync inside the earlier file's latest
        # block: the earlier file is cut there and holds an overrun at that
        # X-sync; one after it is the next file's
        cut = 10 * B + 2000
        _, w2 = words(spec.KIND_PCM, 48000, 24, LONG)
        _, v2 = words(spec.KIND_PCM, 48000, 16, LONG)
        for name, x, want in (("BACK, cut inside a block", np.concatenate([w2[:cut], w2]), [("NOT_BIT_PERFECT", "BACK"), ("OVERRUN", "LAST")]),
                              ("NEXT, cut inside a block", np.concatenate([w2[:cut], v2]), [("NOT_BIT_PERFECT", "NEXT"), ("OVERRUN", "LAST")]),
                              ("the LAST block cut by the next file", np.concatenate([a[:cut], a]), [("BIT_PERFECT", "LAST"), ("OVERRUN", "LAST")])):
            for over, first, second in ((cut - 1, "OVERRUN", "BIT_PERFECT"), (cut, "OVERRUN", "BIT_PERFECT"),
                                        (cut + 1, want[0][0], want[1][0])):
                with self.subTest(name, over=over - cut):
                    res = self.results(x, 48000, overruns=(over,))
                    self.assertEqual([(r["verdict"], r["end"]) for r in res],
                                     [(first, want[0][1]), (second, "LAST")])
        rate = spec.DSD_RATES[0]
        cp, p176 = words(spec.KIND_PCM, rate // 16, 24)  # PCM at the DoP frame rate
        c3, d3 = words(spec.KIND_DSD, rate, 1)
        F3 = frames_per_block(c3)
        for name, x, at in (("PCM, then DoP, cut inside a block", np.concatenate([p176[: 10 * cp.block_len + 2000], d3]),
                             10 * cp.block_len + 2000),
                            ("DoP, then DoP, cut inside a block", np.concatenate([d3[: 10 * F3 + 500], d3]), 10 * F3 + 500)):
            for over, want in ((at - 1, ["OVERRUN", "BIT_PERFECT"]), (at, ["OVERRUN", "BIT_PERFECT"]),
                               (at + 1, ["BIT_PERFECT", "OVERRUN"])):
                with self.subTest(name, over=over - at):
                    res = self.results(x, rate // 16, overruns=(over,))
                    self.assertEqual([r["verdict"] for r in res], want)
        with self.subTest("5 overruns in a file's last frames and 1 in the next, one more than the queue holds"):
            two = np.concatenate([a, a])
            res = self.results(two, 48000, overruns=tuple(range(n - 15, n - 10)) + (n + 1,))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("OVERRUN", "LAST"), ("OVERRUN", "LAST")])

    def test_overrun_before_the_next_file(self):
        """An overrun before a frame of a file's units is that file's; one
        at the frame where a file's LAST block ends stands before the next
        file's first frame, so it is the next file's (12.6 verdict 2)."""
        cfg, a = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))
        B, n = cfg.block_len, len(a)
        x = np.concatenate([a, a])
        y = flip(x, n + 5 * B + 100, 0, 8)  # a body bit of the second file
        cut = np.concatenate([a[:10 * B + 2000], a])  # the first file cut in its tail
        for name, w, over, want in (
                ("in the first file's last block", x, n - B // 2, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                ("at its last frame", x, n - 1, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                ("before the second file's first frame", x, n, [("BIT_PERFECT", "-"), ("OVERRUN", "-")]),
                ("after the second file's first frame", x, n + 1, [("BIT_PERFECT", "-"), ("OVERRUN", "-")]),
                ("in the first file's last block, a difference in the second", y, n - B // 2,
                 [("OVERRUN", "-"), ("NOT_BIT_PERFECT", "0.429")]),
                ("the first file cut in its tail, an overrun before the cut", cut, 10 * B + 2000 - B // 4,
                 [("OVERRUN", "-"), ("BIT_PERFECT", "-")])):
            with self.subTest(name):
                res = self.results(w, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["diff"]) for r in res], want)

    def test_overrun_beyond_the_extent(self):
        """An overrun is assigned by the files' extents as they end (12.6): one
        after a file's extent is the stretch's after it, one inside the
        extent the file's, also where a later X-sync extended the extent."""
        cfg, a = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))
        B = cfg.block_len
        x = a.copy()
        x[11 * B:11 * B + 8] = 0  # the LAST X-sync: the extent ends a block after block 11 (count 12)
        y = np.concatenate([x, zeros(4 * B)])  # silence through block 15
        for name, over, sil, want in (
                ("at block 14, after the extent", 14 * B + 100, False,
                 [("BIT_PERFECT", "STOP"), ("OVERRUN", "STOP")]),
                ("at block 14, silence allowed", 14 * B + 100, True,
                 [("BIT_PERFECT", "STOP"), ("OVERRUN", "STOP")]),
                ("at the extent's last frame", 13 * B - 1, False,
                 [("OVERRUN", "STOP"), ("UNKNOWN_SIGNAL", "STOP")]),
                # without its LAST X-sync the file is cut: it holds its
                # units to its extent's end, and an overrun there
                ("at the extent's end", 13 * B, False,
                 [("OVERRUN", "STOP"), ("UNKNOWN_SIGNAL", "STOP")]),
                ("a frame after the extent's end", 13 * B + 1, False,
                 [("BIT_PERFECT", "STOP"), ("OVERRUN", "STOP")])):
            with self.subTest(name):
                res = self.results(y, 48000, overruns=(over,), silence=sil)
                self.assertEqual([(r["verdict"], r["end"]) for r in res], want)
        cfg, b = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=10, body_blocks=4, tail_blocks=2))
        z = b.copy()
        for k in range(3, 9):
            z[k * B:k * B + 8] = 0  # lead-in X-syncs 3 to 8: the extent grows at block 9's
        with self.subTest("in the lead-in, where block 9's X-sync extends the extent"):
            res = self.results(z, 48000, overruns=(6 * B + 100,))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("OVERRUN", "LAST")])

    def test_frames_end_at_the_extent(self):
        """A file's received frames (dur) end with its extent, where the
        unknown signal after it starts: they are not counted twice."""
        cfg, a = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))
        B = cfg.block_len
        x = a.copy()
        x[11 * B:11 * B + 8] = 0  # the LAST X-sync, in both channels
        res, st = self.run_c(np.concatenate([x, zeros(3 * B)]), 48000)
        self.assertEqual([(r["verdict"], r["end"]) for r in res], [("BIT_PERFECT", "STOP"), ("UNKNOWN_SIGNAL", "STOP")])
        # the extent: to the end of block 11 (count 12 from the R-header of block 8), and one block more
        self.assertEqual((res[0]["first_frame"], res[0]["frames"], res[0]["dur"]), ("0", str(13 * B), "1.109"))
        self.assertEqual((res[1]["first_frame"], res[1]["frames"], st["outside"]), (str(13 * B), str(2 * B), str(2 * B)))

    # One span per file, and the declared size ------------------------------

    def assert_units(self, x, rate, want, **kw):
        """The engine's results (verdict, end, source unit of the first
        difference or None) are want; without overruns checker/verify.py's
        are too, and both end every result at the same unit."""
        res = self.results(x, rate, **kw)
        self.assertEqual([(r["verdict"], r["end"], int(r["diff_unit"]) if r["verdict"] == "NOT_BIT_PERFECT" else None)
                          for r in res], want)
        if not kw.get("overruns"):
            py = py_verify(x, rate, kw.get("slot", 32))
            self.assertEqual(unit_key(res), unit_key(py))
            self.assertEqual([int(q["first_frame"]) + int(q["frames"]) for q in res], [q["first"] + q["units"] for q in py])
        return res

    def test_cut_file_holds_to_the_next_x_sync(self):
        """A file that ends without completing its LAST block is cut: it
        holds its units up to the next file's first X-sync, at most to its
        extent's end, and an overrun there; one after it is the next
        result's (12.6 Files in a stream, verdict 2). A file cut in its tail
        passes."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        _, v = words(spec.KIND_PCM, 48000, 16, LONG)
        for name, x, end in (("NEXT, at a block's edge", np.concatenate([w[:10 * B], v]), "NEXT"),
                             ("BACK, at a block's edge", np.concatenate([w[:10 * B], w]), "BACK")):
            for over, want in ((10 * B - 1, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                               (10 * B, [("OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                               (10 * B + 1, [("NOT_BIT_PERFECT", "0.853"), ("OVERRUN", "-")])):
                with self.subTest(name, over=over - 10 * B):
                    res = self.results(x, 48000, overruns=(over,))
                    self.assertEqual([(r["verdict"], r["diff"]) for r in res], want)
                    self.assertEqual((res[0]["end"], res[0]["frames"]), (end, str(10 * B)))
            with self.subTest(name, over=None):
                # the cut file's difference: the first unit that did not arrive, at the cut
                self.assert_units(x, 48000, [("NOT_BIT_PERFECT", end, 10 * B), ("BIT_PERFECT", "LAST", None)])
        # Cut in its tail after the tail X-syncs of blocks 10 and 11, then
        # silence: the extent ends at 14 B (count 13 from block 8's R-header)
        _, t = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=3))
        x = np.concatenate([t[:11 * B + 100], zeros(5 * B)])
        for over, want in ((14 * B - 1, ["OVERRUN", "UNKNOWN_SIGNAL"]), (14 * B, ["OVERRUN", "UNKNOWN_SIGNAL"]),
                           (14 * B + 1, ["BIT_PERFECT", "OVERRUN"])):
            with self.subTest("cut in the tail, then a stretch", over=over - 14 * B):
                res = self.results(x, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["end"]) for r in res], [(want[0], "STOP"), (want[1], "STOP")])
                self.assertEqual((res[0]["frames"], res[1]["first_frame"]), (str(14 * B), str(14 * B)))
        with self.subTest("cut in the tail, then a stretch, no overrun"):
            self.assert_units(x, 48000, [("BIT_PERFECT", "STOP", None), ("UNKNOWN_SIGNAL", "STOP", None)])
        # Without its LAST X-sync a file is cut even where its R-header gives
        # its size: it holds the stream's end inside its extent
        _, a4 = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))  # LAST 11
        x = a4.copy()
        x[11 * B:11 * B + 8] = 0
        for name, y, over, want in (
                ("LAST erased, an overrun at the stream's end in the margin", np.concatenate([x, zeros(B // 2)]),
                 12 * B + B // 2, [("OVERRUN", "STOP")]),
                ("LAST erased, an overrun in the margin", np.concatenate([x, zeros(B // 2)]), 12 * B + 10,
                 [("OVERRUN", "STOP")]),
                ("LAST intact, an overrun at the stream's end in the margin", np.concatenate([a4, zeros(B // 2)]),
                 12 * B + B // 2, [("BIT_PERFECT", "LAST")]),
                ("LAST intact, an overrun in the margin", np.concatenate([a4, zeros(B // 2)]), 12 * B + 10,
                 [("BIT_PERFECT", "LAST")])):
            with self.subTest(name):
                res = self.results(y, 48000, overruns=(over,))
                self.assertEqual([(r["verdict"], r["end"]) for r in res], want)

    def test_declared_size(self):
        """A file's declared size is the count of its first R-header (12.6
        Extent), else its LAST block's index + 1. A count that disagrees with
        the file is a difference at that R-header's first r5 unit, also in
        the lead-in: an intact LAST X-sync whose index + 1 is not the count,
        and a count no greater than the block index of an X-sync of the file
        that arrived; so is a body R-header whose count is not the declared
        size (the R-header rule's Words step). An earlier difference comes
        first. Such a file is not complete: it is cut, and holds an overrun
        at the next file's first X-sync."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=10, body_blocks=4, tail_blocks=2))  # LAST 15
        B = cfg.block_len
        r8 = chip_frame(cfg, 8, CHIP_R5)  # block 8, an R-header in the lead-in
        no_last = w.copy()
        no_last[15 * B:15 * B + 8] = 0
        barker = with_count(w, cfg, 8, 17)
        barker[chip_frame(cfg, 8, 0):chip_frame(cfg, 8, 1)] = 0  # its first Barker chip: no count
        for name, x, want in (
                ("the lead-in R-header's count one more than the LAST block's", with_count(w, cfg, 8, 17),
                 [("NOT_BIT_PERFECT", "LAST", r8)]),
                ("the lead-in R-header's count one less", with_count(w, cfg, 8, 15), [("NOT_BIT_PERFECT", "LAST", r8)]),
                ("the lead-in R-header's count the LAST block's", w, [("BIT_PERFECT", "LAST", None)]),
                ("a payload bit of body block 11, the lead-in R-header's count 17",
                 flip(with_count(w, cfg, 8, 17), 11 * B + 100, 0, 8), [("NOT_BIT_PERFECT", "LAST", r8)]),
                ("a payload bit of body block 11", flip(w, 11 * B + 100, 0, 8), [("NOT_BIT_PERFECT", "LAST", 11 * B + 100)]),
                ("the lead-in R-header unreadable, its count 17", barker, [("BIT_PERFECT", "LAST", None)]),
                ("the LAST X-sync erased, the lead-in R-header's count 17", with_count(no_last, cfg, 8, 17),
                 [("BIT_PERFECT", "STOP", None)]),
                ("the LAST X-sync erased, the lead-in R-header's count 15", with_count(no_last, cfg, 8, 15),
                 [("BIT_PERFECT", "STOP", None)]),
                # block 14's X-sync arrived: a count of 14 is too small
                ("the LAST X-sync erased, the lead-in R-header's count 14", with_count(no_last, cfg, 8, 14),
                 [("NOT_BIT_PERFECT", "STOP", r8)]),
                ("the LAST X-sync erased, the lead-in R-header's count 3", with_count(no_last, cfg, 8, 3),
                 [("NOT_BIT_PERFECT", "STOP", r8)]),
                ("the stream stopped in the tail after block 14's X-sync, the lead-in R-header's count 14",
                 with_count(w, cfg, 8, 14)[:14 * B + 100], [("NOT_BIT_PERFECT", "STOP", r8)]),
                ("the same, its count 15", with_count(w, cfg, 8, 15)[:14 * B + 100], [("BIT_PERFECT", "STOP", None)]),
                # the stream ends right after the centre of the lead-in
                # R-header's last chip: read, its count 3 is too small
                ("the stream stopped right after the centre of block 8's chip 236, its count 3",
                 with_count(w, cfg, 8, 3)[:chip_frame(cfg, 8, CHIP_R5 + 63) + (chip_frame(cfg, 8, 1) - chip_frame(cfg, 8, 0)) // 2 + 1],
                 [("NOT_BIT_PERFECT", "STOP", r8)])):
            with self.subTest(name):
                self.assert_units(x, 48000, want)
        # Block 8 in the lead-in, block 24 in the body (vectors: 28 blocks,
        # the lead-in R-header changed)
        c2, f = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=10, body_blocks=16, tail_blocks=2))
        r24 = chip_frame(c2, 24, CHIP_R5)
        nl = f.copy()
        nl[27 * B:27 * B + 8] = 0
        for name, x, want in (
                ("the LAST X-sync erased, the lead-in count 40: the body R-header's 28 differs",
                 np.concatenate([with_count(nl, c2, 8, 40), zeros(5 * B)]), [("NOT_BIT_PERFECT", "STOP", r24)]),
                # too small: X-syncs up to block 26 arrived; it gives no
                # extent, so the zeros after block 27 are unknown signal
                ("the LAST X-sync erased, the lead-in count 20",
                 np.concatenate([with_count(nl, c2, 8, 20), zeros(B)]),
                 [("NOT_BIT_PERFECT", "STOP", r8), ("UNKNOWN_SIGNAL", "STOP", None)]),
                ("the LAST X-sync intact, the lead-in count 40", with_count(f, c2, 8, 40),
                 [("NOT_BIT_PERFECT", "LAST", r8)]),
                ("the LAST X-sync intact, the body R-header's count 29", with_count(f, c2, 24, 29),
                 [("NOT_BIT_PERFECT", "LAST", r24)])):
            with self.subTest(name):
                self.assert_units(x, 48000, want)
        # A body R-header's count too small: a difference at its chip 173,
        # unless an earlier body unit differs
        ca, a = words(spec.KIND_PCM, 48000, 24, LONG)  # body 2-25, R-headers 8 and 24
        r8a = chip_frame(ca, 8, CHIP_R5)
        for name, x, want in (
                ("the body R-header of block 8 spells 5", with_count(a, ca, 8, 5), [("NOT_BIT_PERFECT", "LAST", r8a)]),
                ("and a payload bit of block 3", flip(with_count(a, ca, 8, 5), 3 * B + 100, 0, 8),
                 [("NOT_BIT_PERFECT", "LAST", 3 * B + 100)]),
                ("block 8 spells 5, the stream stops in the tail before LAST", with_count(a, ca, 8, 5)[:26 * B + 100],
                 [("NOT_BIT_PERFECT", "STOP", r8a)])):
            with self.subTest(name):
                self.assert_units(x, 48000, want)
        # The stream ends soon after the first R-header's chip 236: its count
        # is known when the file ends
        e236 = chip_frame(ca, 8, CHIP_R5 + 64)
        for t in (1, 20, 100, 250, 2000):
            with self.subTest("block 8 spells 5, the stream stops after its chip 236", frames=t):
                self.assert_units(with_count(a, ca, 8, 5)[:e236 + t], 48000, [("NOT_BIT_PERFECT", "STOP", r8a)])
        # A file entered in its body: the body's start is not known, the
        # first R-header's words are not judged, and the difference is at
        # the first block whose X-sync arrived, before any overrun (12.6
        # verdict 2)
        for name, x in (("as generated", a), ("block 8 spells 5", with_count(a, ca, 8, 5)),
                        ("block 8 spells 40", with_count(a, ca, 8, 40))):
            with self.subTest("the stream starts at block 5", count=name):
                self.assert_units(x[5 * B:], 48000, [("NOT_BIT_PERFECT", "LAST", 5 * B)])
                res = self.results(x[5 * B:], 48000, overruns=(100,))
                self.assertEqual([(r["verdict"], r["diff_unit"]) for r in res], [("NOT_BIT_PERFECT", str(5 * B))])
        # Fewer than 9 blocks: no R-header; the LAST block gives the size
        c3, s = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=4, tail_blocks=2))  # LAST 7
        s0 = s.copy()
        s0[7 * B:7 * B + 8] = 0
        with self.subTest("8 blocks"):
            self.assert_units(s, 48000, [("BIT_PERFECT", "LAST", None)])
            self.assertEqual(self.results(s, 48000, overruns=(8 * B,))[0]["verdict"], "BIT_PERFECT")
        with self.subTest("8 blocks, the LAST X-sync erased"):
            res = self.assert_units(s0, 48000, [("BIT_PERFECT", "STOP", None)])
            self.assertEqual(res[0]["frames"], str(8 * B))
            self.assertEqual(self.results(s0, 48000, overruns=(8 * B,))[0]["verdict"], "OVERRUN")
        # A body R-header's count one more than the LAST block's: the file
        # is not complete, so an overrun at the next file's first X-sync is
        # its own
        ca, a = words(spec.KIND_PCM, 48000, 24)  # SHORT: 11 blocks, block 8 in the body
        x = np.concatenate([with_count(a, ca, 8, 12), a])
        r8a = chip_frame(ca, 8, CHIP_R5)
        with self.subTest("a body R-header's count 12, then the file again"):
            self.assert_units(x, 48000, [("NOT_BIT_PERFECT", "LAST", r8a), ("BIT_PERFECT", "LAST", None)])
            res = self.results(x, 48000, overruns=(len(a),))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("NOT_BIT_PERFECT", "LAST"), ("BIT_PERFECT", "LAST")])
        with self.subTest("the file, then the file again"):
            res = self.results(np.concatenate([a, a]), 48000, overruns=(len(a),))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("BIT_PERFECT", "LAST"), ("OVERRUN", "LAST")])

    def test_declared_size_cut_and_overrun(self):
        """A file whose first R-header's count is not its LAST block's index +
        1 is cut, also where that count gives no extent (no greater than the
        index of its last X-sync): it holds an overrun at the next file's
        first X-sync. In the lead-in that count is a difference; in the
        tail, where no word is checked, none (12.6, the R-header rule)."""
        cfg, w = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=10, body_blocks=4, tail_blocks=2))  # LAST 15
        B, n = cfg.block_len, len(w)
        r8 = chip_frame(cfg, 8, CHIP_R5)
        x = np.concatenate([with_count(w, cfg, 8, 15), w])  # count 15: no extent, not the LAST block's 16
        with self.subTest("lead-in count 15, LAST 15, then the file again"):
            self.assert_units(x, 48000, [("NOT_BIT_PERFECT", "LAST", r8), ("BIT_PERFECT", "LAST", None)])
            res = self.results(x, 48000, overruns=(n,))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("NOT_BIT_PERFECT", "LAST"), ("BIT_PERFECT", "LAST")])
        ct, t = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=4, tail_blocks=10))  # block 8 in the tail
        y = with_count(t, ct, 8, 17)
        with self.subTest("a tail R-header's count 17, LAST 15: no difference, but cut"):
            self.assert_units(y, 48000, [("BIT_PERFECT", "LAST", None)])
            self.assert_units(np.concatenate([y, t]), 48000, [("BIT_PERFECT", "LAST", None), ("BIT_PERFECT", "LAST", None)])
            res = self.results(np.concatenate([y, t]), 48000, overruns=(len(t),))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("OVERRUN", "LAST"), ("BIT_PERFECT", "LAST")])
        with self.subTest("the same file with its own count: complete"):
            res = self.results(np.concatenate([t, t]), 48000, overruns=(len(t),))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("BIT_PERFECT", "LAST"), ("OVERRUN", "LAST")])

    def test_declared_size_dop(self):
        """DoP: the LAST block against the lead-in R-header's count, the body
        R-header against the declared size, and a cut file holding an
        overrun at its extent's end."""
        rate = spec.DSD_RATES[0]
        cfg, d = words(spec.KIND_DSD, rate, 1, dict(lead_blocks=10, body_blocks=4, tail_blocks=2))  # LAST 15
        F = frames_per_block(cfg)
        r8 = chip_frame(cfg, 8, CHIP_R5) // 8  # DSD bytes
        for name, x, want in (("lead-in count 17, LAST 15", with_count_dop(d, cfg, 8, 17), [("NOT_BIT_PERFECT", "LAST", r8)]),
                              ("lead-in count 15, LAST 15", with_count_dop(d, cfg, 8, 15), [("NOT_BIT_PERFECT", "LAST", r8)]),
                              ("lead-in count 16, LAST 15", with_count_dop(d, cfg, 8, 16), [("BIT_PERFECT", "LAST", None)]),
                              ("lead-in count 12, the stream stopped in block 14", with_count_dop(d, cfg, 8, 12)[:14 * F + 100],
                               [("NOT_BIT_PERFECT", "STOP", r8)])):
            with self.subTest(name):
                self.assert_units(x, rate // 16, want, slot=24)
        c2, f = words(spec.KIND_DSD, rate, 1, dict(lead_blocks=10, body_blocks=16, tail_blocks=2))  # LAST 27
        nl = f.copy()
        nl[27 * F:27 * F + 16] = dop_idle(16)
        r24 = chip_frame(c2, 24, CHIP_R5) // 8
        with self.subTest("LAST erased, the lead-in count 40: the body R-header's 28 differs"):
            self.assert_units(np.concatenate([with_count_dop(nl, c2, 8, 40), dop_idle(5 * F)]), rate // 16,
                              [("NOT_BIT_PERFECT", "STOP", r24)], slot=24)
        with self.subTest("LAST intact, the body R-header's count 29"):
            self.assert_units(with_count_dop(f, c2, 24, 29), rate // 16, [("NOT_BIT_PERFECT", "LAST", r24)], slot=24)
        _, t = words(spec.KIND_DSD, rate, 1, dict(lead_blocks=2, body_blocks=8, tail_blocks=3))  # count 13 from block 8
        x = np.concatenate([t[:11 * F + 50], dop_idle(5 * F)])  # cut in the tail; the extent ends at 14 F
        with self.subTest("cut in the tail, then DoP idle"):
            self.assert_units(x, rate // 16, [("BIT_PERFECT", "STOP", None), ("UNKNOWN_SIGNAL", "STOP", None)], slot=24)
        for over, want in ((14 * F - 1, ["OVERRUN", "UNKNOWN_SIGNAL"]), (14 * F, ["OVERRUN", "UNKNOWN_SIGNAL"]),
                           (14 * F + 1, ["BIT_PERFECT", "OVERRUN"])):
            with self.subTest("cut in the tail, then DoP idle", over=over - 14 * F):
                res = self.results(x, rate // 16, slot=24, overruns=(over,))
                self.assertEqual([r["verdict"] for r in res], want)
                self.assertEqual(res[0]["frames"], str(14 * F))

    def test_r5_fuzz_against_verify(self):
        """R-headers that spell random block counts with CRCs that pass, in
        the lead-in, the body or the tail, in one channel or both, PCM at
        several rates and depths and DoP, the LAST X-sync mostly present
        (fixed seed): the same results as checker/verify.py, to the source
        unit of the first difference and the unit each result ends at."""
        rng = random.Random(6)
        cases = [r5_case(rng) for _ in range(R5_TRIALS)]

        def one(case):
            x, rate, slot, chunk, ops = case
            res = self.results(x, rate, slot=slot, chunk=chunk)
            return c_key(res), unit_key(res), [int(q["first_frame"]) + int(q["frames"]) for q in res]

        with ThreadPoolExecutor(max_workers=min(12, os.cpu_count() or 1)) as pool:
            got = list(pool.map(one, cases))
        bad = []
        for (x, rate, slot, chunk, ops), c in zip(cases, got):
            py = py_verify(x, rate, slot)
            p = (py_results(x, rate, slot), unit_key(py), [q["first"] + q["units"] for q in py])
            if c != p:
                bad.append((ops, c, p))
        self.assertEqual(bad, [], f"{len(bad)} of {len(cases)} differ")


def threading_id():
    import threading
    return threading.get_ident()


if __name__ == "__main__":
    unittest.main()
