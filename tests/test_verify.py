# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""verify.py, the four verdicts: no false pass and no false fail.

No false pass: every kind of change inside a file's body, of its limits, of
its rate or channels, or of the DoP framing gives NOT_BIT_PERFECT (or
UNKNOWN_SIGNAL where no X-sync is left), with diff at the first changed unit. No
false fail: every kind, depth and slot, gapless runs, starts in the lead-in,
stops in the tail and changes there give BIT_PERFECT.
"""

import unittest

import numpy as np

from checker import readers, verify
from generator import containers, fileset, spec
from generator.signal import dop_frames

from tests import helpers as H
from tests import simulate as sim

LEAD, BODY = H.SHORT["lead_blocks"], H.SHORT["body_blocks"]
FIRST, LAST = LEAD, LEAD + BODY - 1          # body blocks
TAIL = LEAD + BODY                            # first tail block
RHDR = 8                                      # an R-header block in the body
R56 = 13 + 5 * 32                             # the first r5 chip


def pcm(rate=48000, depth=24, seed=spec.DEFAULT_SEED):
    cfg = H.Config(kind=spec.KIND_PCM, rate=rate, depth=depth, seed=seed, **H.SHORT)
    return cfg, H.pcm_words(cfg)


def check(x, rate=48000, slot=32):
    return verify.verify(H.pcm_stream(x, rate, slot))


def flip(x, frame, bit, channels=(0,)):
    y = x.copy()
    for c in channels:
        y[frame, c] ^= np.int32(np.uint32(1 << bit).view(np.int32))
    return y


def chip_unit(cfg, k, chip, t=0):
    return k * cfg.block_len + spec.rhdr_start(cfg.rate) + chip * spec.chip_len(cfg.rate) + t


def dop(cfg, slot=24, rate=None):
    f = dop_frames(H.dsd_bits(cfg), 0).astype(np.int64) << 8
    return readers.Stream(False, rate or cfg.rate // 16, cfg.nch, f.astype(np.uint32).view(np.int32),
                          "test", slot, slot)


def native(cfg, by=None):
    by = np.ascontiguousarray(np.packbits(H.dsd_bits(cfg), axis=1).T) if by is None else by
    return readers.Stream(True, cfg.rate, cfg.nch, by, "test")


class Pcm(unittest.TestCase):
    """Single changes in the body of a 48 kHz 24-bit file in 32-bit slots."""

    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.w = pcm()
        cls.B = cls.cfg.block_len

    def fails_at(self, x, unit, rate=48000, slot=32, verdict="NOT_BIT_PERFECT"):
        r = check(x, rate, slot)
        self.assertEqual(r["verdict"], verdict, r)
        if unit is not None:
            self.assertEqual(r["diff_unit"], unit, r)
            self.assertEqual(r["diff"], round(unit / self.cfg.rate, 3))
        return r

    def test_clean(self):
        r = check(self.w)
        self.assertEqual((r["verdict"], r["kind"], r["dop"], r["seed"], r["nch"], r["file_rate"],
                          r["depth"], r["rate"], r["bits"], r["end"], r["diff"]),
                         ("BIT_PERFECT", "PCM", 0, spec.DEFAULT_SEED, 2, 48000, 24, 48000, 24,
                          "LAST", None))

    def test_xsync_words(self):
        k = 5
        for i in range(8):
            for bit in (8, 15, 16, 31):          # below and in the 16-bit X-sync word
                for ch in ((0,), (1,), (0, 1)):
                    with self.subTest(word=i, bit=bit, channels=ch):
                        self.fails_at(flip(self.w, k * self.B + i, bit, ch), k * self.B + i)

    def test_first_body_xsync_in_all_channels(self):
        u = FIRST * self.B
        for i in range(8):
            with self.subTest(word=i):
                r = self.fails_at(flip(self.w, u + i, 20, (0, 1)), None)
                self.assertLessEqual(r["diff_unit"], u + i)

    def test_every_bit_of_a_payload_word(self):
        u = 7 * self.B + 3000
        for bit in range(32):
            with self.subTest(bit=bit):
                self.fails_at(flip(self.w, u, bit), u)

    def test_unit_classes(self):
        r0 = spec.rhdr_start(self.cfg.rate)
        cases = {"burst (walk)": 6 * self.B + 8, "burst end": 6 * self.B + 71,
                 "payload": 9 * self.B + 100, "low bit": 9 * self.B + 200,
                 "R-header zero stretch": RHDR * self.B + 100,
                 "R-header Barker chip": RHDR * self.B + r0 + 3,
                 "R-header r0 chip": chip_unit(self.cfg, RHDR, 13 + 5),
                 "R-header r5 chip": chip_unit(self.cfg, RHDR, R56 + 3, 2),
                 "R-header r6 chip": chip_unit(self.cfg, RHDR, R56 + 40),
                 "after the chips": (RHDR + 1) * self.B - 1,
                 "first body unit": FIRST * self.B, "last body unit": (LAST + 1) * self.B - 1}
        for name, u in cases.items():
            bit = 8 if name == "low bit" else 20
            with self.subTest(name):
                self.fails_at(flip(self.w, u, bit), u)

    def stop_in_tail(self, x):
        """The stream stops in the tail, after its X-sync: no LAST block, so the
        block count is unknown."""
        return x[:TAIL * self.B + 1000]

    def test_stop_in_tail_passes(self):
        r = check(self.stop_in_tail(self.w))
        self.assertEqual((r["verdict"], r["end"]), ("BIT_PERFECT", "STOP"))

    def test_rhdr_count_unknown(self):
        x = self.stop_in_tail(self.w)
        V = 1 << 29
        u = chip_unit(self.cfg, RHDR, R56 + 4, 5)
        with self.subTest("a unit neither of the chip's values"):
            self.fails_at(flip(x, u, 3), u)
        with self.subTest("one unit of a chip at the other value"):
            y = x.copy()
            y[u, 0] = -y[u, 0]
            self.fails_at(y, u)
        L = spec.chip_len(self.cfg.rate)
        a = chip_unit(self.cfg, RHDR, R56 + 4)
        with self.subTest("a bit of r5 changed (both chips): CRC"):
            y = x.copy()
            y[a:a + 2 * L, 0] = -y[a:a + 2 * L, 0]
            self.fails_at(y, chip_unit(self.cfg, RHDR, R56))
        with self.subTest("a chip pair that does not spell a bit"):
            y = x.copy()
            y[a:a + L, 0] = -y[a:a + L, 0]
            self.fails_at(y, a + L)
        with self.subTest("r5 and its CRC changed consistently: counts disagree"):
            y = x.copy()
            for c in range(2):
                words = self.cfg.rhdr_words(c, RHDR)
                r5 = words[5] ^ 1
                r6 = spec.crc16(spec.words_to_bytes(list(words[:5]) + [r5]))
                for wi, word in ((5, r5), (6, r6)):
                    for i, b in enumerate(range(15, -1, -1)):
                        s = 1 if (word >> b) & 1 else -1
                        for j, v in enumerate((s, -s)):
                            u0 = chip_unit(self.cfg, RHDR, 13 + wi * 32 + 2 * i + j)
                            y[u0:u0 + L, c] = v * V
            r = check(y)
            self.assertEqual(r["verdict"], "NOT_BIT_PERFECT")
            self.assertEqual(r["why"], "the R-headers' block counts disagree")

    def test_rhdr_second_chip(self):
        """Each bit as it is read (12.6, R-header rule): the second chip is
        compared with the opposite of the first, unit by unit, and a pair
        that is not opposite is found before a later chip that is neither
        value. bpc1_checker.c does the same (tests/test_bpc1_checker.py)."""
        L = spec.chip_len(self.cfg.rate)
        a3, a27 = chip_unit(self.cfg, RHDR, R56 + 3), chip_unit(self.cfg, RHDR, R56 + 27)
        for name, x in (("count unknown", self.stop_in_tail(self.w)), ("count known", self.w)):
            with self.subTest(name + ": pair 1 not opposite, chip 27 zeroed"):
                y = x.copy()
                y[a3:a3 + L, 0] = -y[a3:a3 + L, 0]
                y[a27:a27 + L, 0] = 0
                r = self.fails_at(y, a3)
                self.assertEqual(r["why"], "an R-header bit's chips are not opposite")
            with self.subTest(name + ": the second chip's first unit at the first chip's value"):
                y = x.copy()
                y[a3, 0] = -y[a3, 0]
                self.fails_at(y, a3)
            with self.subTest(name + ": the second chip neither value from its unit 5"):
                y = x.copy()
                y[a3 + 5:a3 + L, 1] = 0
                self.fails_at(y, a3 + 5)

    def set_r5(self, x, k, r5):
        """Write r5 and its CRC r6 into the chips of R-header block k."""
        L, V = spec.chip_len(self.cfg.rate), 1 << 29
        for c in range(2):
            words = self.cfg.rhdr_words(c, k)
            r6 = spec.crc16(spec.words_to_bytes(list(words[:5]) + [r5]))
            for wi, word in ((5, r5), (6, r6)):
                for i, b in enumerate(range(15, -1, -1)):
                    s = 1 if (word >> b) & 1 else -1
                    for j, v in enumerate((s, -s)):
                        u0 = chip_unit(self.cfg, k, 13 + wi * 32 + 2 * i + j)
                        x[u0:u0 + L, c] = v * V

    def test_rhdr_count_not_the_last_blocks(self):
        # r5 and r6 changed consistently in every body R-header: they agree with
        # each other, so only the LAST block's index tells
        y = self.w.copy()
        for k in (8, 24):
            self.set_r5(y, k, self.cfg.total_blocks + 1)
        r = check(y)
        self.assertEqual((r["verdict"], r["why"], r["diff_unit"]),
                         ("NOT_BIT_PERFECT", "an R-header block count is not the LAST block's",
                          chip_unit(self.cfg, 8, R56)))
        self.assertEqual(check(self.stop_in_tail(y))["verdict"], "BIT_PERFECT",
                         "without LAST, agreeing R-headers pass")

    def test_stop_in_the_last_block_without_tail(self):
        cfg = H.Config(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=2, body_blocks=24,
                       tail_blocks=0)
        w = H.pcm_words(cfg)
        self.assertEqual(check(w)["verdict"], "BIT_PERFECT")
        u = 25 * self.B + 100           # in the LAST block, after its X-sync
        r = check(w[:u])
        self.assertEqual((r["verdict"], r["diff_unit"], r["why"]),
                         ("NOT_BIT_PERFECT", u, "the body did not arrive in full"))

    def test_rhdr_count_known(self):
        u = chip_unit(self.cfg, RHDR, R56 + 4, 5)
        y = self.w.copy()
        y[u, 0] = -y[u, 0]
        self.fails_at(y, u)

    def test_channels_disagree_on_the_block(self):
        # one channel's X-sync replaced by that of a limit block: the flags of
        # a channel that disagrees with the first on the block do not count
        for name, at, src in (("first body X-sync <- last lead-in X-sync", FIRST, LEAD - 1),
                              ("last body X-sync <- first tail X-sync", LAST, TAIL)):
            with self.subTest(name):
                y = self.w.copy()
                y[at * self.B:at * self.B + 8, 1] = self.w[src * self.B:src * self.B + 8, 1]
                r = check(y)
                self.assertEqual(r["verdict"], "NOT_BIT_PERFECT", r)

    def test_lead_from_the_first_lead_in_run(self):
        # a 2-block lead-in, then from block 6 the same file with a 12-block
        # lead-in: the later LEAD_IN X-syncs are compared as body units
        _, other = pcm()
        cfg12 = H.Config(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=12, body_blocks=14,
                         tail_blocks=2)
        x = np.concatenate([self.w[:6 * self.B], H.pcm_words(cfg12)[6 * self.B:]])
        y = flip(x, 3 * self.B + 1000, 8)
        r = check(y)
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", 3 * self.B + 1000), r)
        self.assertEqual((check(x)["verdict"], check(x)["diff_unit"]),
                         ("NOT_BIT_PERFECT", 6 * self.B + 3))

    def test_diff_rounds_half_up(self):
        r = check(flip(self.w, 61944, 20))           # 61944 / 48000 = 1.2905 s
        self.assertEqual((r["diff_unit"], "%.3f" % r["diff"]), (61944, "1.291"))

    def test_cut_inside_the_chips(self):
        # a chip unit changed, the stream stops 50 chips later: the chips that
        # arrived are checked; only the CRC and count need them all
        a = chip_unit(self.cfg, RHDR, R56)
        y = flip(self.w, a + 30, 8)[:a + 50 * spec.chip_len(self.cfg.rate)]
        r = check(y)
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", a + 30), r)
        L = spec.chip_len(self.cfg.rate)
        y = self.w.copy()
        y[a + 4 * L:a + 5 * L, 0] = -y[a + 4 * L:a + 5 * L, 0]   # a pair not opposite
        r = check(y[:a + 50 * L])
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", a + 5 * L), r)

    def test_non_tail_after_tail_ends_the_file(self):
        long = H.Config(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=2, body_blocks=30,
                        tail_blocks=2)
        x = np.concatenate([self.w[:27 * self.B], H.pcm_words(long)[27 * self.B:]])
        rs = verify.verify_stream(H.pcm_stream(x, 48000, 32))
        self.assertEqual([(r["verdict"], r["end"], r["diff_unit"]) for r in rs],
                         [("BIT_PERFECT", "BACK", None), ("NOT_BIT_PERFECT", "LAST", 27 * self.B)])

    def test_gaps(self):
        p = 10 * self.B + 1234
        cases = {"one frame dropped": sim.drop(self.w, p),
                 "a packet dropped": sim.drop(self.w, p, 6),
                 "a frame repeated": sim.repeat(self.w, p),
                 "zeros inserted": np.insert(self.w, p, np.zeros((6, 2), np.int32), axis=0),
                 "zero-filled": sim.zero(self.w, p, 10),
                 "a block dropped": sim.drop(self.w, 10 * self.B, self.B)}
        for name, x in cases.items():
            with self.subTest(name):
                r = check(x)
                self.assertEqual(r["verdict"], "NOT_BIT_PERFECT")
                self.assertLessEqual(r["diff_unit"], p + 1)
                self.assertGreaterEqual(r["diff_unit"], 10 * self.B)

    def test_last_body_unit_dropped(self):
        self.fails_at(sim.drop(self.w, (LAST + 1) * self.B - 1), (LAST + 1) * self.B - 1)

    def test_late_start(self):
        for name, x in (("into the body", self.w[4 * self.B + 10:]),
                        ("at the first body block", self.w[FIRST * self.B:]),
                        ("after the last lead-in X-sync", self.w[(LEAD - 1) * self.B + 8:])):
            with self.subTest(name):
                r = check(x)
                self.assertEqual((r["verdict"], r["why"]),
                                 ("NOT_BIT_PERFECT", "no lead-in X-sync before the body"))

    def test_early_stop(self):
        end = TAIL * self.B
        for name, n in (("one unit short", end - 1), ("at the body's end", end),
                        ("in the body", 12 * self.B)):
            with self.subTest(name):
                self.fails_at(self.w[:n], min(n, end))
        # Without the tail X-sync the body's end is not known: the units that
        # arrived are compared as body, so the tail X-sync's flags word differs
        with self.subTest("in the tail X-sync"):
            self.fails_at(self.w[:end + 4], end + 3)

    def test_limit_xsyncs_broken(self):
        for name, k in (("last lead-in", LEAD - 1), ("first tail", TAIL)):
            with self.subTest(name):
                r = check(flip(self.w, k * self.B + 3, 20, (0, 1)))
                self.assertEqual(r["verdict"], "NOT_BIT_PERFECT", r)
        # one channel intact is enough
        self.assertEqual(check(flip(self.w, (LEAD - 1) * self.B + 3, 20, (1,)))["verdict"],
                         "BIT_PERFECT")

    def test_rate_and_channels(self):
        self.fails_at(self.w, FIRST * self.B, rate=44100)
        self.fails_at(sim.swap(self.w), FIRST * self.B)
        self.fails_at(np.repeat(self.w[:, :1], 2, axis=1), FIRST * self.B)
        r = verify.verify(H.pcm_stream(np.ascontiguousarray(self.w[:, :1]), 48000, 32))
        self.assertEqual(r["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(check(sim.invert(self.w, 24))["verdict"], "UNKNOWN_SIGNAL")

    def test_slots(self):
        cfg, w16 = pcm(48000, 16)
        self.assertEqual(check(w16, slot=16)["verdict"], "BIT_PERFECT")
        u = 9 * self.B + 5
        r = check(flip(w16, u, 8), slot=24)     # a bit below the file's 16 in a 24-bit slot
        self.assertEqual((r["verdict"], r["diff_unit"], r["bits"]), ("NOT_BIT_PERFECT", u, 24))

    def test_another_seed_spliced(self):
        _, other = pcm(seed=0x12345678)
        x = self.w.copy()
        x[10 * self.B:11 * self.B] = other[10 * self.B:11 * self.B]
        rs = verify.verify_stream(H.pcm_stream(x, 48000, 32))
        self.assertEqual([r["verdict"] for r in rs], ["NOT_BIT_PERFECT"] * 3)
        self.assertEqual([r["end"] for r in rs], ["NEXT", "NEXT", "LAST"])

    def test_replay_back(self):
        p = 12 * self.B + 500
        rs = verify.verify_stream(H.pcm_stream(np.concatenate([self.w[:p], self.w]), 48000, 32))
        self.assertEqual([(r["verdict"], r["end"]) for r in rs],
                         [("NOT_BIT_PERFECT", "BACK"), ("BIT_PERFECT", "LAST")])

    def test_gapless_second_changed(self):
        _, w16 = pcm(48000, 16)
        u = 9 * self.B + 77
        rs = verify.verify_stream(H.pcm_stream(np.concatenate([self.w, flip(w16, u, 20)]), 48000, 32))
        self.assertEqual([(r["verdict"], r["diff_unit"]) for r in rs],
                         [("BIT_PERFECT", None), ("NOT_BIT_PERFECT", u)])


class Dsd(unittest.TestCase):
    """DoP and native DSD."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = H.Config(kind=spec.KIND_DSD, rate=2822400, depth=1, **H.SHORT)
        cls.st = dop(cls.cfg)
        cls.F = cls.cfg.block_len // 16          # DoP frames per block

    def with_data(self, data, slot=24):
        return readers.Stream(False, self.st.rate, 2, data, "test", slot, slot)

    def fails_at(self, st, unit):
        r = verify.verify(st)
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", unit), r)

    def test_clean(self):
        for slot in (24, 32):
            with self.subTest(slot=slot):
                r = verify.verify(self.with_data(self.st.data, slot))
                self.assertEqual((r["verdict"], r["kind"], r["dop"], r["file_rate"], r["depth"],
                                  r["rate"], r["bits"]),
                                 ("BIT_PERFECT", "DSD", 1, 2822400, 1, 176400, 24))

    def test_dsd_bits_and_markers(self):
        f = 10 * self.F + 321
        d = self.st.data
        x = d.copy(); x[f, 0] ^= 1 << 20             # the frame's first DSD byte
        self.fails_at(self.with_data(x), 2 * f)
        x = d.copy(); x[f, 0] ^= 1 << 12             # its second
        self.fails_at(self.with_data(x), 2 * f + 1)
        x = d.copy(); x[f] &= 0x00FFFFFF
        self.fails_at(self.with_data(x), 2 * f)
        x = d.copy(); x[f, 1] = x[f, 1] & 0x00FFFFFF | (x[f + 1, 1] & ~0x00FFFFFF)
        self.fails_at(self.with_data(x), 2 * f)        # markers differ between channels
        x = d.copy(); m = x[f:f + 2] & ~0x00FFFFFF
        x[f:f + 2] = (x[f:f + 2] & 0x00FFFFFF) | m[::-1]
        self.fails_at(self.with_data(x), 2 * f)        # two markers swapped
        x = d.copy(); x[f:] ^= np.int32(np.uint32(0xFF000000).view(np.int32))
        self.fails_at(self.with_data(x), 2 * f)        # a phase jump: 05 FA 05 FA FA 05 ...

    def test_padding(self):
        f = 11 * self.F + 5
        x = self.st.data.copy(); x[f, 0] |= 1
        self.fails_at(self.with_data(x, 32), 2 * f)

    def test_no_markers_no_signal(self):
        x = self.st.data & 0x00FFFFFF
        r = verify.verify(self.with_data(x))
        self.assertEqual((r["verdict"], r["kind"], r["seed"], r["file_rate"], r["depth"], r["bits"],
                          r["end"], r["diff"]), ("UNKNOWN_SIGNAL", "NONE", 0, 0, 0, 0, "STOP", None))

    def test_dop_rate_mismatch(self):
        st = readers.Stream(False, 88200, 2, self.st.data, "test", 24, 24)
        self.assertEqual(verify.verify(st)["verdict"], "NOT_BIT_PERFECT")

    def test_dsd_as_dop_and_native(self):
        cfg = H.Config(kind=spec.KIND_DSD, rate=2822400, depth=1, **H.SHORT)
        r = verify.verify(dop(cfg))
        self.assertEqual((r["verdict"], r["dop"]), ("BIT_PERFECT", 1))
        st = native(cfg)
        r = verify.verify(st)
        self.assertEqual((r["verdict"], r["kind"], r["dop"], r["bits"]), ("BIT_PERFECT", "DSD", 0, 1))
        u = 10 * cfg.block_len // 8 + 77
        by = st.data.copy(); by[u, 1] ^= 0x10
        r = verify.verify(native(cfg, by))
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", u))
        self.assertEqual(r["diff"], round(u * 8 / 2822400, 3))

    def test_rhdr_count_unknown(self):
        cfg = H.Config(kind=spec.KIND_DSD, rate=2822400, depth=1, **H.SHORT)
        by = native(cfg).data[:TAIL * cfg.block_len // 8 + 100]
        self.assertEqual(verify.verify(native(cfg, by))["verdict"], "BIT_PERFECT")
        a = RHDR * cfg.block_len // 8 + spec.rhdr_start(cfg.rate) // 8 \
            + (R56 + 2) * spec.chip_len(cfg.rate) // 8 + 1
        x = by.copy(); x[a, 0] ^= 0xFF                   # 0xEE <-> 0x11 in one byte
        r = verify.verify(native(cfg, x))
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", a))


def build_case(v, case):
    """The stream of a verdict case of the vectors file."""
    parts, rate, slot = [], None, None
    for part in case["stream"]:
        if "file" in part:
            f = dict(v["files"][part["file"]])
            cfg = H.Config(kind=f["kind"], rate=f["rate"], depth=f["depth"], seed=int(f["seed"], 16),
                           lead_blocks=f["lead_blocks"], body_blocks=f["body_blocks"],
                           tail_blocks=f["tail_blocks"])
            if cfg.dsd:
                w = (dop_frames(H.dsd_bits(cfg), 0).astype(np.int64) << 8).astype(np.uint32)
                r, sl = cfg.rate // 16, 24
            else:
                w = H.pcm_words(cfg).view(np.uint32)
                r, sl = cfg.rate, 32
            a, b = part.get("frames", (0, len(w)))
            parts.append(w[a:b])
            rate, slot = rate or part.get("rate", r), slot or part.get("slot", sl)
            continue
        slot = slot or part.get("slot")
        if "zeros" in part:
            parts.append(np.zeros((part["zeros"], 2), np.uint32))
        elif "dop_idle" in part:
            m = np.where(np.arange(part["dop_idle"]) % 2 == 0, 0x05, 0xFA).astype(np.uint32)
            parts.append(np.repeat(((m << 24) | 0x696900)[:, None], 2, axis=1))
        else:
            i = np.arange(part["noise"] * 2, dtype=np.uint32)
            parts.append((spec.lb32_np(int(part["key"], 16), i) & 0xFFFFFF00).reshape(-1, 2))
    rate, slot = rate or 48000, slot or 32
    x = np.concatenate(parts).astype(np.int64)
    for c in case["change"]:
        op, a = c["op"], c.get("frame", 0)
        n = c.get("count", 1 if op in ("xor", "and") else len(x))
        ch = c.get("channels", [0, 1])
        if op in ("xor", "and"):
            v32 = int(c["value"], 16)
            for k in ch:
                seg = x[a:a + n, k] & 0xFFFFFFFF
                x[a:a + n, k] = (seg ^ v32) if op == "xor" else (seg & v32)
        elif op == "remove":
            x = np.delete(x, np.s_[a:a + c["count"]], axis=0)
        elif op == "insert":
            x = np.insert(x, a, np.full((c["count"], 2), int(c["value"], 16)), axis=0)
        elif op == "copy":
            x = np.insert(x, a, x[c["from"]:c["from"] + c["count"]], axis=0)
        elif op == "recode":
            for k in ch:
                seg = x[a:a + n, k] & 0xFFFFFFFF
                x[a:a + n, k] = np.where(seg == int(c["from"], 16), int(c["to"], 16), seg)
        elif op == "replace":
            for k in ch:
                x[a:a + c["count"], k] = x[c["from"]:c["from"] + c["count"], k]
        elif op == "start":
            x = x[a:]
        elif op == "stop":
            x = x[:a]
        elif op == "swap_channels":
            x = x[:, ::-1]
        else:
            s = x[a:a + n] & 0xFFFFFFFF
            s = np.where(s >= 1 << 31, s - (1 << 32), s)
            b = c.get("bits", 0)
            if op == "negate":
                s = np.minimum(-s, (1 << 31) - (1 << (32 - b)))
            elif op == "round":
                q = 1 << (32 - b)
                s = np.minimum((s + q // 2) & ~(q - 1), ((1 << 31) - 1) & ~(q - 1))
            elif op == "halve":
                s = s >> 1
            x[a:a + n] = s
    w = (np.ascontiguousarray(x) & 0xFFFFFFFF).astype(np.uint32).view(np.int32)
    return readers.Stream(False, rate, 2, w, "test", slot, slot)


class VerdictCases(unittest.TestCase):
    """The verdict cases of the vectors file, which the C engine's tests read too."""

    def test_cases(self):
        import json, os
        with open(os.path.join(os.path.dirname(__file__), "..", "vectors",
                               "bpc1-v1-vectors.json")) as f:
            v = json.load(f)["verdicts"]
        for case in v["cases"]:
            with self.subTest(case["case"]):
                rs = verify.verify_stream(build_case(v, case), case.get("allow_silence", False))
                got = [{"verdict": r["verdict"], "end": r["end"],
                        "diff": "-" if r["diff"] is None else "%.3f" % r["diff"]} for r in rs]
                self.assertEqual(got, case["results"], [r.get("why") for r in rs])


class NoFalseFail(unittest.TestCase):
    def test_every_depth_and_slot(self):
        for rate in (44100, 48000, 96000, 192000, 768000):
            for depth in (16, 24, 32):
                _, w = pcm(rate, depth)
                for slot in (16, 24, 32):
                    if slot < depth:
                        continue
                    with self.subTest(rate=rate, depth=depth, slot=slot):
                        r = check(w, rate, slot)
                        self.assertEqual((r["verdict"], r["bits"], r["depth"]),
                                         ("BIT_PERFECT", depth, depth), r)

    def test_gapless_runs(self):
        _, a = pcm(48000, 24)
        _, b = pcm(48000, 16)
        rs = verify.verify_stream(H.pcm_stream(np.concatenate([a, b, a, a]), 48000, 32))
        self.assertEqual([(r["verdict"], r["depth"], r["end"]) for r in rs],
                         [("BIT_PERFECT", 24, "LAST"), ("BIT_PERFECT", 16, "LAST"),
                          ("BIT_PERFECT", 24, "LAST"), ("BIT_PERFECT", 24, "LAST")])

    def test_lead_in_and_tail(self):
        cfg, w = pcm()
        B = cfg.block_len
        fade = w.copy()
        fade[:LEAD * B] = (fade[:LEAD * B].astype(np.float64)
                           * np.linspace(0, 1, LEAD * B)[:, None]).astype(np.int32)
        fade[(LEAD - 1) * B:(LEAD - 1) * B + 8] = w[(LEAD - 1) * B:(LEAD - 1) * B + 8]
        zeros = w.copy()
        zeros[(LEAD - 1) * B + 8:LEAD * B] = 0
        zeros[TAIL * B + 8:] = 0
        cases = {"starts in the lead-in": w[1000:],
                 "starts at the last lead-in X-sync": w[(LEAD - 1) * B:],
                 "stops in the tail": w[:TAIL * B + 8],
                 "stops in the tail, later": w[:(TAIL + 1) * B + 300],
                 "a fade-in over the lead-in": fade,
                 "zeros in the lead-in and tail": zeros,
                 "frames cut in the lead-in": sim.drop(w, 500, 300),
                 "frames cut in the tail": sim.drop(w, TAIL * B + 200, 300),
                 "zeros within a block before and after": np.concatenate(
                     [np.zeros((3000, 2), np.int32), w, np.zeros((3000, 2), np.int32)]),
                 "frames repeated in the tail": np.insert(w, (TAIL + 1) * B + 500,
                                                          w[(TAIL + 1) * B + 100:(TAIL + 1) * B + 800],
                                                          axis=0)}
        for name, x in cases.items():
            with self.subTest(name):
                r = check(x)
                self.assertEqual(r["verdict"], "BIT_PERFECT", r)

    def test_written_files(self):
        with H.TempDir() as d:
            for container, kind, rate, depth in (("wav", spec.KIND_PCM, 44100, 16),
                                                 ("wav", spec.KIND_PCM, 96000, 32),
                                                 ("dsf", spec.KIND_DSD, 2822400, 1),
                                                 ("dff", spec.KIND_DSD, 2822400, 1),
                                                 ("dop", spec.KIND_DSD, 2822400, 1)):
                cfg = H.Config(kind=kind, rate=rate, depth=depth, **H.SHORT)
                f = H.short_file(container, cfg)
                path = fileset.write_file(f, d)
                paths = [path]
                if containers.flac_usable() and container != "dsf" and container != "dff":
                    paths.append(fileset.write_flac(f, path))
                for p in paths:
                    with self.subTest(p.rsplit("/", 1)[-1]):
                        self.assertEqual(verify.verify(readers.read_file(p))["verdict"],
                                         "BIT_PERFECT")


if __name__ == "__main__":
    unittest.main()
