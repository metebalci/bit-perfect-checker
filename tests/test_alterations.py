"""Simulated playback-chain alterations: each must be detected and named."""

import unittest

import numpy as np

from checker import check, readers
from generator import spec

from tests import helpers as H
from tests import simulate as sim


def body_pos(cfg, block=12, offset=1500):
    return block * cfg.block_len + offset


class PcmAlterations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = H.short_config(spec.KIND_PCM, 44100, 24)
        cls.w = H.pcm_words(cls.cfg)
        cls.p = body_pos(cls.cfg)

    def check(self, w, rate=44100, slot=24):
        return H.check_words(w, rate, slot)

    def test_bit_perfect(self):
        self.assertEqual(self.check(self.w)["verdict"], "BIT_PERFECT")

    def test_truncation_24_to_16(self):
        rep = self.check(sim.truncate(self.w, 16), slot=16)
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(rep["diagnosis"]["reasons"][0]["code"], "TRUNCATED")
        self.assertEqual(rep["diagnosis"]["reasons"][0]["bits"], 16)
        self.assertEqual(rep["diagnosis"]["lock"], "exact")

    def test_rounding_24_to_16(self):
        rep = self.check(sim.quantize(self.w / 2.0 ** 31, 16), slot=16)
        self.assertEqual(H.codes(rep)[0], "ROUNDED")

    def test_tpdf_dither_24_to_16(self):
        rep = self.check(sim.tpdf_dither(self.w, 16), slot=16)
        self.assertEqual(rep["verdict"], "UNKNOWN_SIGNAL")
        r = rep["diagnosis"]["reasons"][0]
        self.assertEqual((r["code"], r["bits"]), ("DITHERED", 16))
        self.assertAlmostEqual(r["rms_lsb"], 0.5, delta=0.05)   # TPDF + rounding

    def test_gain_minus_6_02(self):
        rep = self.check(sim.gain(self.w, -6.02, 24))
        r = rep["diagnosis"]["reasons"][0]
        self.assertEqual(r["code"], "GAIN")
        self.assertAlmostEqual(r["gain_db"][0], -6.02, delta=0.005)
        self.assertEqual(rep["diagnosis"]["lock"], "robust")

    def test_gain_minus_0_5(self):
        rep = self.check(sim.gain(self.w, -0.5, 24))
        r = rep["diagnosis"]["reasons"][0]
        self.assertEqual(r["code"], "GAIN")
        self.assertAlmostEqual(r["gain_db"][0], -0.50, delta=0.005)
        self.assertAlmostEqual(rep["diagnosis"]["robust"]["level_db"], -0.50, delta=0.02)

    def test_channel_swap(self):
        rep = self.check(sim.swap(self.w))
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.codes(rep), ["CHANNEL_MAP"])
        self.assertEqual(rep["diagnosis"]["reasons"][0]["text"], "channels swapped")

    def test_polarity(self):
        rep = self.check(sim.invert(self.w, 24))
        self.assertEqual(rep["verdict"], "UNKNOWN_SIGNAL")
        self.assertEqual(H.codes(rep), ["POLARITY"])
        self.assertEqual(rep["diagnosis"]["lock"], "exact")

    def test_swap_and_polarity_in_spec_order(self):
        rep = self.check(sim.swap(sim.invert(self.w, 24)))
        self.assertEqual((rep["verdict"], rep["diagnosis"]["lock"]), ("UNKNOWN_SIGNAL", "exact"))
        self.assertEqual(H.codes(rep), ["CHANNEL_MAP", "POLARITY"])
        self.assertEqual(rep["diagnosis"]["verdict_text"], "ALTERED: channels swapped")

    def test_inverted_then_truncated(self):
        # The X-sync words of -W truncated read as ~w where the bits below
        # them were not zero, else as -w: the inverted search takes either.
        for slot in (16, 24, 32):
            with self.subTest(slot=slot):
                rep = self.check(sim.truncate(sim.invert(self.w, 24), 16), slot=slot)
                self.assertEqual(H.codes(rep), ["POLARITY", "TRUNCATED"])
                self.assertEqual(rep["diagnosis"]["reasons"][1]["bits"], 16)
                self.assertEqual(rep["diagnosis"]["reasons"][1]["text"], "truncated to 16 bits")
                self.assertEqual(rep["diagnosis"]["lock"], "exact")
                self.assertEqual((rep["diagnosis"]["coverage"]["end"], rep["diagnosis"]["coverage"]["missing"]), ("LAST", "none"))

    def test_truncated_then_inverted(self):
        # The full-scale bursts reach -2^31, which the 24-bit inversion clamps
        # to the 24-bit maximum: those samples use 24 bits, the others 16.
        for inv_bits in (24, 16):
            with self.subTest(inv_bits=inv_bits):
                rep = self.check(sim.invert(sim.truncate(self.w, 16), inv_bits), slot=inv_bits)
                self.assertEqual(H.codes(rep), ["POLARITY", "TRUNCATED"])
                self.assertEqual(rep["diagnosis"]["reasons"][1]["bits"], 16)
                self.assertEqual(rep["diagnosis"]["reasons"][1]["text"],
                                 "truncated to 16 bits before the polarity inversion")
                self.assertEqual(rep["diagnosis"]["verdict_text"], "ALTERED: polarity inverted")

    def test_rounded_then_inverted(self):
        x = sim.quantize(self.w / 2.0 ** 31, 16)
        for inv_bits in (24, 16):
            with self.subTest(inv_bits=inv_bits):
                rep = self.check(sim.invert(x, inv_bits), slot=inv_bits)
                self.assertEqual(H.codes(rep), ["POLARITY", "ROUNDED"])
                self.assertEqual(rep["diagnosis"]["reasons"][1]["bits"], 16)
                self.assertEqual(rep["diagnosis"]["reasons"][1]["text"],
                                 "rounded to 16 bits before the polarity inversion")

    def test_swapped_truncated_then_inverted(self):
        rep = self.check(sim.swap(sim.invert(sim.truncate(self.w, 16), 24)))
        self.assertEqual(H.codes(rep), ["CHANNEL_MAP", "POLARITY", "TRUNCATED"])
        self.assertEqual(rep["diagnosis"]["reasons"][2]["bits"], 16)

    def test_one_dropped_frame(self):
        rep = self.check(sim.drop(self.w, self.p))
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.counts(rep), {"dropped": 1})
        self.assertEqual((rep["diagnosis"]["coverage"]["end"], rep["diagnosis"]["coverage"]["missing"]), ("LAST", "middle"))
        e = [e for e in rep["diagnosis"]["events"] if e["section"] == "body"]
        self.assertEqual((e[0]["position"], e[0]["block"]), (self.p, 12))

    def test_one_repeated_frame(self):
        rep = self.check(sim.repeat(self.w, self.p))
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.counts(rep), {"repeated": 1})

    def test_ten_zeroed_frames(self):
        rep = self.check(sim.zero(self.w, self.p, 10))
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT", "zero-filled frames are not ALTERED")
        self.assertEqual(H.counts(rep), {"zero_filled": 10})
        cov = rep["diagnosis"]["coverage"]
        self.assertEqual((cov["altered"], cov["units"] - cov["equal"]), (0, 10))

    def test_inserted_zero_frames(self):
        w = np.insert(self.w, self.p, np.zeros((7, 2), np.int32), axis=0)
        rep = self.check(w)
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT", "inserted frames are not ALTERED")
        self.assertEqual(H.counts(rep), {"zero_inserted": 7})

    def test_dropped_whole_packet(self):
        rep = self.check(sim.drop(self.w, self.p, 6))
        self.assertEqual(H.counts(rep), {"dropped": 6})

    def test_gap_in_lead_in_not_counted(self):
        rep = self.check(sim.drop(self.w, 1000))
        self.assertEqual(rep["verdict"], "BIT_PERFECT")
        self.assertTrue(any("lead-in" in n for n in rep["diagnosis"]["notes"]))
        self.assertEqual(rep["diagnosis"]["counts"]["side_events"], 1)

    def test_resampling_96_to_48(self):
        c = H.short_config(spec.KIND_PCM, 96000, 24)
        rep = self.check(sim.resample(H.pcm_words(c), 96000, 48000), rate=48000)
        self.assertEqual((rep["verdict"], H.codes(rep)), ("UNKNOWN_SIGNAL", ["RESAMPLED"]))
        self.assertEqual(rep["diagnosis"]["robust"]["source_rate"], 96000)
        self.assertAlmostEqual(rep["diagnosis"]["robust"]["rate_ratio"], 0.5, places=4)
        r = rep["diagnosis"]["reasons"][0]
        self.assertEqual((r["file_rate"], r["rate"]), (96000, 48000))
        self.assertEqual(rep["diagnosis"]["verdict_text"], "ALTERED: resampled: 96 kHz -> 48 kHz")

    def test_resampling_44_1_to_48(self):
        rep = self.check(sim.resample(self.w, 44100, 48000), rate=48000)
        self.assertEqual((rep["verdict"], H.codes(rep)), ("UNKNOWN_SIGNAL", ["RESAMPLED"]))
        self.assertEqual(rep["diagnosis"]["robust"]["source_rate"], 44100)
        self.assertAlmostEqual(rep["diagnosis"]["robust"]["rate_ratio"], 48000 / 44100, places=4)

    def test_resampling_with_gain(self):
        c = H.short_config(spec.KIND_PCM, 96000, 24)
        x = sim.resample(H.pcm_words(c), 96000, 48000)
        rep = self.check(sim.gain(x, -3.0, 24), rate=48000)
        self.assertEqual(rep["verdict"], "UNKNOWN_SIGNAL")
        self.assertEqual(H.codes(rep), ["RESAMPLED", "GAIN"])
        self.assertAlmostEqual(rep["diagnosis"]["reasons"][1]["gain_db"][0], -3.0, delta=0.1)

    def test_resampled_swap_and_polarity_in_spec_order(self):
        c = H.short_config(spec.KIND_PCM, 96000, 24)
        x = sim.resample(H.pcm_words(c), 96000, 48000)
        rep = self.check(sim.swap(sim.invert(x, 24)), rate=48000)
        self.assertEqual((rep["verdict"], rep["diagnosis"]["lock"]), ("UNKNOWN_SIGNAL", "robust"))
        self.assertEqual(H.codes(rep), ["RESAMPLED", "CHANNEL_MAP", "POLARITY"])

    def test_gain_with_clipping(self):
        rep = self.check(sim.gain(self.w, 1.0, 24))
        r = rep["diagnosis"]["reasons"][0]
        self.assertEqual(r["code"], "GAIN")
        self.assertAlmostEqual(r["gain_db"][0], 1.0, delta=0.005)
        self.assertGreater(r["clipped"], 0)

    def test_gain_and_polarity_and_swap(self):
        rep = self.check(sim.swap(sim.invert(sim.gain(self.w, -40.0, 24), 24)))
        self.assertEqual(H.codes(rep), ["CHANNEL_MAP", "POLARITY", "GAIN"])
        self.assertAlmostEqual(rep["diagnosis"]["reasons"][2]["gain_db"][0], -40.0, delta=0.01)

    def test_downmix(self):
        w = self.w.astype(np.int64)
        mono = ((w[:, :1] + w[:, 1:]) // 2).astype(np.int32)
        rep = self.check(np.repeat(mono, 2, axis=1))
        self.assertEqual(rep["verdict"], "UNKNOWN_SIGNAL")
        self.assertEqual(H.codes(rep), ["DOWNMIX"])

    def test_one_channel_on_both(self):
        rep = self.check(np.repeat(self.w[:, :1], 2, axis=1))
        self.assertEqual(H.codes(rep), ["CHANNEL_MAP"])
        self.assertEqual(rep["diagnosis"]["reasons"][0]["order"], [0, 0])

    def test_resampling_768_to_44_1(self):
        c = H.short_config(spec.KIND_PCM, 768000, 24)
        rep = self.check(sim.resample(H.pcm_words(c), 768000, 44100), rate=44100)
        self.assertEqual(rep["diagnosis"]["verdict_text"], "ALTERED: resampled: 768 kHz -> 44.1 kHz")
        self.assertEqual(H.codes(rep), ["RESAMPLED"])

    def test_rate_mismatch(self):
        rep = self.check(self.w, rate=48000)
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.codes(rep), ["RATE_MISMATCH"])

    def test_noise_below_depth(self):
        c = H.short_config(spec.KIND_PCM, 48000, 16)
        rep = self.check(sim.tpdf_dither(H.pcm_words(c), 24), rate=48000)
        self.assertEqual(H.codes(rep)[0], "LOW_BIT_NOISE")

    def test_bit_errors(self):
        w = self.w.copy()
        w[self.p, 0] ^= 1 << 12
        rep = self.check(w)
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.codes(rep), ["BIT_ERRORS"])

    def test_not_a_test_signal(self):
        rng = np.random.default_rng(3)
        w = (rng.integers(-2 ** 23, 2 ** 23, (200000, 2)) << 8).astype(np.int32)
        self.assertEqual(self.check(w)["verdict"], "UNKNOWN_SIGNAL")
        self.assertEqual(self.check(np.zeros((50000, 2), np.int32))["verdict"], "UNKNOWN_SIGNAL")


class QuantizedWithDrops(unittest.TestCase):
    """Frames dropped from a rounded or truncated stream: the gaps are located
    and the quantisation is still named."""

    def dropped(self, w, cfg, n, blocks=(4, 7, 10)):
        """w with n frames dropped at three body positions, later ones first."""
        for k in sorted(blocks, reverse=True):
            w = sim.drop(w, body_pos(cfg, k, 700), n)
        return w

    def check_named(self, rate, code, x, n, bits=20, blocks=(4, 7, 10), verdict="NOT_BIT_PERFECT"):
        cfg = H.short_config(spec.KIND_PCM, rate, 24)
        w = H.pcm_words(cfg)
        # dither or rounding to 18 bits loses X-syncs: the file, then unknown signal
        rep, *rest = check.check_stream(H.pcm_stream(self.dropped(x(w), cfg, n, blocks), rate, 24))
        self.assertTrue(all(r["verdict"] == "UNKNOWN_SIGNAL" for r in rest))
        codes = code if isinstance(code, list) else [code]
        self.assertEqual((rep["verdict"], H.codes(rep)), (verdict, codes))
        self.assertEqual(rep["diagnosis"]["reasons"][-1]["bits"], bits)
        self.assertEqual(H.counts(rep), {"dropped": 3 * n})
        self.assertEqual(rep["diagnosis"]["coverage"]["missing"], "middle")
        return rep

    def test_rounded_44k1(self):
        self.check_named(44100, "ROUNDED", lambda w: sim.quantize(w / 2.0 ** 31, 20), 6)

    def test_truncated_44k1(self):
        self.check_named(44100, "TRUNCATED", lambda w: sim.truncate(w, 20), 6)

    def test_rounded_768k_packets(self):
        self.check_named(768000, "ROUNDED", lambda w: sim.quantize(w / 2.0 ** 31, 20), 96)

    def test_truncated_768k_packets(self):
        self.check_named(768000, "TRUNCATED", lambda w: sim.truncate(w, 20), 96)

    def test_dithered(self):
        # Dither below 16 bits carries into the X-sync words of some blocks;
        # at 18 bits and 44.1 kHz none survives before block 5, so the gaps
        # are put where the anchors around them remain.
        for rate, n in ((44100, 6), (768000, 96)):
            for bits in (18, 20, 22):
                with self.subTest(rate=rate, bits=bits):
                    self.check_named(rate, "DITHERED", lambda w: sim.tpdf_dither(w, bits), n, bits,
                                     blocks=(7, 10, 13))

    def test_rounded_then_inverted(self):
        # The inversion clamps -2^31 to the 24-bit maximum, which sets bits
        # below 16: k for the gap search leaves that word out.
        self.check_named(44100, ["POLARITY", "ROUNDED"],
                         lambda w: sim.invert(sim.quantize(w / 2.0 ** 31, 16), 24), 6, 16,
                         verdict="UNKNOWN_SIGNAL")


class LongGaps(unittest.TestCase):
    """Gaps longer than the +-S search are located by the long search, so the
    units after them are not compared out of place."""

    def test_stop_after_a_long_drop_48k(self):
        cfg = H.short_config(spec.KIND_PCM, 48000, 24)
        B = cfg.block_len
        x = sim.drop(H.pcm_words(cfg), 6 * B + 1500, 100)[:6 * B + 3500]
        rep = H.check_words(x, 48000, 24)
        self.assertEqual((rep["verdict"], H.counts(rep)), ("NOT_BIT_PERFECT", {"dropped": 100}))
        self.assertEqual((rep["diagnosis"]["coverage"]["end"], rep["diagnosis"]["coverage"]["missing"]), ("STOP", "middle,end"))

    def test_stop_after_a_long_drop_768k(self):
        cfg = H.short_config(spec.KIND_PCM, 768000, 24)
        B = cfg.block_len
        x = sim.drop(H.pcm_words(cfg), 6 * B + 3000, 3000)[:6 * B + 30000]
        rep = H.check_words(x, 768000, 24)
        self.assertEqual((rep["verdict"], H.counts(rep)), ("NOT_BIT_PERFECT", {"dropped": 3000}))
        self.assertEqual(rep["diagnosis"]["coverage"]["missing"], "middle,end")

    def test_two_drops_in_one_span_768k(self):
        cfg = H.short_config(spec.KIND_PCM, 768000, 24)
        B = cfg.block_len
        w = H.pcm_words(cfg)
        x = sim.drop(sim.drop(w, 6 * B + 20000, 3), 6 * B + 3000, 6000)
        rep = H.check_words(x, 768000, 24)
        self.assertEqual((rep["verdict"], H.counts(rep)), ("NOT_BIT_PERFECT", {"dropped": 6003}))
        self.assertEqual((rep["diagnosis"]["coverage"]["end"], rep["diagnosis"]["coverage"]["missing"]), ("LAST", "middle"))
        drops = [(e["position"], e["count"]) for e in rep["diagnosis"]["events"] if e["type"] == "dropped"]
        self.assertEqual(drops, [(6 * B + 3000, 6000), (6 * B + 20000 - 6000, 3)])


class Coverage(unittest.TestCase):
    """How a file ended, what of its body is missing, and streams of several files."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = H.short_config(spec.KIND_PCM, 44100, 24)
        cls.w = H.pcm_words(cls.cfg)
        cls.p = body_pos(cls.cfg)
        cls.w16 = H.pcm_words(H.short_config(spec.KIND_PCM, 44100, 16))

    def reports(self, w):
        return check.check_stream(H.pcm_stream(w, 44100, 24))

    def test_cut_off(self):
        rep = H.check_words(self.w[:self.p], 44100, 24)
        cov = rep["diagnosis"]["coverage"]
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual((cov["end"], cov["missing"]), ("STOP", "end"))
        self.assertEqual((cov["body_blocks"], cov["total"]), (11, self.cfg.total_blocks))
        self.assertEqual(cov["body"], 0, "no tail block seen: the body length is unknown")
        self.assertEqual(H.counts(rep), {})

    def test_cut_in_the_tail(self):
        """Only the body counts: a stream that stops in the tail misses nothing."""
        rep = H.check_words(self.w[:-self.cfg.block_len - 100], 44100, 24)
        self.assertEqual((rep["verdict"], rep["diagnosis"]["coverage"]["end"], rep["diagnosis"]["coverage"]["missing"]),
                         ("BIT_PERFECT", "STOP", "none"))
        self.assertEqual(rep["diagnosis"]["coverage"]["body"], self.cfg.body_blocks)

    def test_started_in_the_lead_in(self):
        rep = H.check_words(self.w[777:], 44100, 24)
        self.assertEqual((rep["verdict"], rep["diagnosis"]["coverage"]["missing"]), ("BIT_PERFECT", "none"))

    def test_cut_in_the_last_body_block(self):
        cut = (self.cfg.lead_blocks + self.cfg.body_blocks) * self.cfg.block_len - 100
        rep = H.check_words(self.w[:cut], 44100, 24)
        self.assertEqual((rep["verdict"], rep["diagnosis"]["coverage"]["end"], rep["diagnosis"]["coverage"]["missing"]),
                         ("NOT_BIT_PERFECT", "STOP", "end"))

    def test_dropped_run(self):
        rep = H.check_words(sim.drop(self.w, self.p, 1000), 44100, 24)
        cov = rep["diagnosis"]["coverage"]
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual((cov["end"], cov["missing"]), ("LAST", "middle"))
        self.assertEqual(H.counts(rep), {"dropped": 1000})
        self.assertEqual(cov["units"], self.cfg.body_blocks * self.cfg.block_len - 1000)
        self.assertEqual(cov["units"], cov["equal"])

    def test_started_in_the_body(self):
        rep = H.check_words(self.w[3 * self.cfg.block_len + 777:], 44100, 24)
        self.assertEqual((rep["verdict"], rep["diagnosis"]["coverage"]["missing"]), ("NOT_BIT_PERFECT", "start"))
        self.assertEqual(rep["diagnosis"]["coverage"]["lead"], 0, "no lead-in block seen: unknown")

    def test_altered_before_incomplete(self):
        w = sim.drop(self.w, self.p, 1000)
        w[self.p + 3000, 0] ^= 1 << 12
        rep = H.check_words(w, 44100, 24)
        self.assertEqual((rep["verdict"], H.codes(rep)), ("NOT_BIT_PERFECT", ["BIT_ERRORS"]))
        self.assertEqual(rep["diagnosis"]["coverage"]["missing"], "middle")

    def test_two_files_joined(self):
        reps = self.reports(np.concatenate([self.w, self.w16]))
        self.assertEqual([(r["file"], r["verdict"], r["diagnosis"]["signal"]["depth"], r["diagnosis"]["coverage"]["end"])
                          for r in reps], [(1, "BIT_PERFECT", 24, "LAST"),
                                           (2, "BIT_PERFECT", 16, "LAST")])
        self.assertEqual([r["received"] for r in reps],
                         [{"first": 0, "units": len(self.w)},
                          {"first": len(self.w), "units": len(self.w16)}])
        self.assertRaises(ValueError, check.check, H.pcm_stream(np.concatenate([self.w, self.w]),
                                                                44100, 24))

    def test_joined_with_gaps_and_silence(self):
        z = np.zeros((3000, 2), np.int32)
        gap = sim.drop(self.w, self.p)
        reps = self.reports(np.concatenate([z, self.w16, gap, self.w16, z]))
        self.assertEqual([(r["verdict"], r["diagnosis"]["signal"]["depth"], r["diagnosis"]["coverage"]["missing"])
                          for r in reps], [("BIT_PERFECT", 16, "none"),
                                           ("NOT_BIT_PERFECT", 24, "middle"),
                                           ("BIT_PERFECT", 16, "none")])
        self.assertEqual(reps[1]["diagnosis"]["events"][0]["position"], 3000 + len(self.w16) + self.p)

    def test_identity_change_before_last(self):
        """A cut-off file followed by another: NEXT, and the next file is whole."""
        reps = self.reports(np.concatenate([self.w[:self.p], self.w16]))
        self.assertEqual([(r["verdict"], r["diagnosis"]["coverage"]["end"], r["diagnosis"]["coverage"]["missing"])
                          for r in reps], [("NOT_BIT_PERFECT", "NEXT", "end"),
                                           ("BIT_PERFECT", "LAST", "none")])
        self.assertEqual(reps[1]["received"]["first"], self.p)

    def test_block_index_goes_back(self):
        """The same file restarted before its end (a seek back): BACK."""
        reps = self.reports(np.concatenate([self.w[:self.p], self.w]))
        self.assertEqual([(r["verdict"], r["diagnosis"]["coverage"]["end"]) for r in reps],
                         [("NOT_BIT_PERFECT", "BACK"), ("BIT_PERFECT", "LAST")])

    def test_json_lines(self):
        import contextlib, io, json, os
        from checker.__main__ import main
        with H.TempDir() as d:
            path = os.path.join(d, "cap.raw")
            np.concatenate([self.w, sim.drop(self.w16, self.p)]).astype("<i4").tofile(path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--json", "--raw", "s32le", "--rate", "44100", path])
        lines = out.getvalue().splitlines()
        reps = [json.loads(x) for x in lines]
        self.assertEqual(code, 1)
        self.assertEqual([(r["path"], r["file"], r["verdict"]) for r in reps],
                         [(path, 1, "BIT_PERFECT"), (path, 2, "NOT_BIT_PERFECT")])
        self.assertEqual(reps[1]["diagnosis"]["counts"]["dropped"], 1)


class Float32Path(unittest.TestCase):
    def test_float32_roundtrip_32bit(self):
        c = H.short_config(spec.KIND_PCM, 96000, 32)
        rep = H.check_words(sim.float32_roundtrip(H.pcm_words(c)), 96000, 32)
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.codes(rep), ["FLOAT32"])

    def test_float32_is_exact_for_24bit(self):
        c = H.short_config(spec.KIND_PCM, 96000, 24)
        rep = H.check_words(sim.float32_roundtrip(H.pcm_words(c)), 96000, 32)
        self.assertEqual(rep["verdict"], "BIT_PERFECT")


class DsdAlterations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = H.short_config(spec.KIND_DSD, 2822400, 1)
        cls.bits = H.dsd_bits(cls.cfg)

    def test_dsd_to_pcm_decimation(self):
        for factor, rate in ((32, 88200), (16, 176400)):
            with self.subTest(rate=rate):
                pcm = sim.dsd_to_pcm(self.bits, factor)
                rep = H.check_words(pcm, rate, 24)
                self.assertEqual((rep["verdict"], H.codes(rep)), ("UNKNOWN_SIGNAL", ["DSD_TO_PCM", "LEVEL"]))
                self.assertEqual(rep["diagnosis"]["signal"]["kind"], "DSD")
                self.assertEqual(rep["diagnosis"]["robust"]["source_rate"], 2822400)
                self.assertIn(f"PCM {rate / 1000:g} kHz", rep["diagnosis"]["verdict_text"])
                self.assertAlmostEqual(rep["diagnosis"]["robust"]["level_db"], -6.0, delta=0.3)

    def test_dsd256_to_pcm_352k8(self):
        cfg = H.short_config(spec.KIND_DSD, 11289600, 1)
        rep = H.check_words(sim.dsd_to_pcm(H.dsd_bits(cfg), 32), 352800, 24)
        self.assertEqual(rep["diagnosis"]["verdict_text"],
                         "ALTERED: DSD converted to PCM: DSD256 -> PCM 352.8 kHz")

    def _dop_stream(self, bits):
        from generator.signal import dop_frames
        f = dop_frames(bits, 0).astype(np.int64) << 8
        return readers.Stream(False, 176400, 2, f.astype(np.int32), "test", 24, 24)

    def test_dop_dropped_frame(self):
        cfg = H.short_config(spec.KIND_DSD, 2822400, 1)
        st = self._dop_stream(H.dsd_bits(cfg))
        p = 12 * cfg.block_len // 16 + 3000
        st.data = np.delete(st.data, p, axis=0)
        rep = check.check(st)
        self.assertEqual(rep["diagnosis"]["stream"]["transport"], "DoP")
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.counts(rep), {"dropped": 2})     # 2 bytes = 16 DSD bits

    def test_dsd_bit_errors(self):
        b = self.bits.copy()
        b[0, 12 * self.cfg.block_len + 5000] ^= 1
        by = np.ascontiguousarray(np.packbits(b, axis=1).T)
        rep = check.check(readers.Stream(True, 2822400, 2, by, "test"))
        self.assertEqual(rep["verdict"], "NOT_BIT_PERFECT")
        self.assertEqual(H.codes(rep), ["BIT_ERRORS"])


if __name__ == "__main__":
    unittest.main()
