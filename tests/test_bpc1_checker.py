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
files, allowed silence, device overruns and chunking. Needs a host C
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

    def test_device_overrun(self):
        """An overrun inside a file makes it not judged, unless the file's
        first difference came before it; in a stretch outside every file, an
        overrun makes that stretch not judged."""
        _, a = words(spec.KIND_PCM, 48000, 24)
        res = self.results(np.concatenate([a, a]), 48000, overruns=(len(a) + 20000,))
        self.assertEqual([(r["verdict"], r["diff"]) for r in res], [("BIT_PERFECT", "-"), ("DEVICE_OVERRUN", "-")])
        cfg, w = words(spec.KIND_PCM, 48000, 24, LONG)
        B = cfg.block_len
        x = flip(w, 5 * B + 100, 0, 8)
        for name, over, want in (("a difference, then an overrun", 20 * B, ("NOT_BIT_PERFECT", "0.429")),
                                 ("an overrun, then a difference", 3 * B, ("DEVICE_OVERRUN", "-")),
                                 ("an overrun at the differing frame", 5 * B + 100, ("DEVICE_OVERRUN", "-")),
                                 ("an overrun in the lead-in, no difference", B // 2, ("DEVICE_OVERRUN", "-")),
                                 ("an overrun in the body, no difference", 20 * B, ("DEVICE_OVERRUN", "-"))):
            with self.subTest(name):
                r = self.one(w if "no difference" in name else x, 48000, overruns=(over,))
                self.assertEqual((r["verdict"], r["diff"]), want)
        with self.subTest("a difference at the body's start, then an overrun"):
            res = self.results(np.concatenate([sim.truncate(a, 16), a]), 48000, overruns=(20000,))
            self.assertEqual([(r["verdict"], r["diff"]) for r in res], [("NOT_BIT_PERFECT", "0.171"), ("BIT_PERFECT", "-")])
        with self.subTest("two overruns, the first before the difference"):
            r = self.one(x, 48000, overruns=(3 * B, 20 * B))
            self.assertEqual((r["verdict"], r["diff"]), ("DEVICE_OVERRUN", "-"))
        with self.subTest("an overrun that drops whole r5 bit pairs"):
            for k, n in ((8, 24), (8, 48), (24, 24), (24, 48)):
                d = chip_frame(cfg, k, CHIP_R5 + 4)  # the chips after it shift by one or two bits
                r = self.one(np.delete(w, np.s_[d:d + n], axis=0), 48000, overruns=(d,))
                self.assertEqual((r["verdict"], r["diff"]), ("DEVICE_OVERRUN", "-"), (k, n))
        with self.subTest("a body left after an overrun"):
            r = self.one(w[: 10 * B], 48000, overruns=(8 * B,))
            self.assertEqual((r["verdict"], r["diff"]), ("DEVICE_OVERRUN", "-"))
        res = self.results(np.concatenate([a, zeros(40000), a]), 48000, overruns=(len(a) + 20000,))
        self.assertEqual([r["verdict"] for r in res], ["BIT_PERFECT", "DEVICE_OVERRUN", "BIT_PERFECT"])
        res = self.results(np.concatenate([a, zeros(40000)]), 48000, overruns=(len(a) + 30000,), silence=True)
        self.assertEqual([r["verdict"] for r in res], ["BIT_PERFECT", "DEVICE_OVERRUN"])


    def test_overrun_before_the_next_file(self):
        """An overrun in a file's units, up to the frame where they end, is
        that file's: the gapless file after it is judged (12.6 verdict 2: its
        units start where the earlier file's end)."""
        cfg, a = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=2, body_blocks=8, tail_blocks=2))
        B, n = cfg.block_len, len(a)
        x = np.concatenate([a, a])
        y = flip(x, n + 5 * B + 100, 0, 8)  # a body bit of the second file
        cut = np.concatenate([a[:10 * B + 2000], a])  # the first file cut in its tail
        for name, w, over, want in (
                ("in the first file's last block", x, n - B // 2, [("DEVICE_OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                ("at its last frame", x, n - 1, [("DEVICE_OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                ("before the second file's first frame", x, n, [("DEVICE_OVERRUN", "-"), ("BIT_PERFECT", "-")]),
                ("after the second file's first frame", x, n + 1, [("BIT_PERFECT", "-"), ("DEVICE_OVERRUN", "-")]),
                ("in the first file's last block, a difference in the second", y, n - B // 2,
                 [("DEVICE_OVERRUN", "-"), ("NOT_BIT_PERFECT", "0.429")]),
                ("the first file cut in its tail, an overrun before the cut", cut, 10 * B + 2000 - B // 4,
                 [("DEVICE_OVERRUN", "-"), ("BIT_PERFECT", "-")])):
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
                 [("BIT_PERFECT", "STOP"), ("DEVICE_OVERRUN", "STOP")]),
                ("at block 14, silence allowed", 14 * B + 100, True,
                 [("BIT_PERFECT", "STOP"), ("DEVICE_OVERRUN", "STOP")]),
                ("at the extent's last frame", 13 * B - 1, False,
                 [("DEVICE_OVERRUN", "STOP"), ("UNKNOWN_SIGNAL", "STOP")]),
                ("at the extent's end", 13 * B, False,
                 [("BIT_PERFECT", "STOP"), ("DEVICE_OVERRUN", "STOP")])):
            with self.subTest(name):
                res = self.results(y, 48000, overruns=(over,), silence=sil)
                self.assertEqual([(r["verdict"], r["end"]) for r in res], want)
        cfg, b = words(spec.KIND_PCM, 48000, 24, dict(lead_blocks=10, body_blocks=4, tail_blocks=2))
        z = b.copy()
        for k in range(3, 9):
            z[k * B:k * B + 8] = 0  # lead-in X-syncs 3 to 8: the extent grows at block 9's
        with self.subTest("in the lead-in, where block 9's X-sync extends the extent"):
            res = self.results(z, 48000, overruns=(6 * B + 100,))
            self.assertEqual([(r["verdict"], r["end"]) for r in res], [("DEVICE_OVERRUN", "LAST")])

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


def threading_id():
    import threading
    return threading.get_ident()


if __name__ == "__main__":
    unittest.main()
