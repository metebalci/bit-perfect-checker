"""Round trip: generate, write the container, read it back, check it.

Every container and configuration of the format, 32-bit PCM included, as
short files with the full block structure; a few files at standard length;
FLAC files decoded by `flac -d` and compared with the WAV byte for byte; raw
captures and wider slots.
"""

import os
import unittest

import numpy as np

from checker import check, readers
from generator import containers, fileset, spec

from tests import helpers as H

HAVE_FLAC = containers.flac_usable()


class PcmRoundTrip(unittest.TestCase):
    def test_every_pcm_configuration(self):
        with H.TempDir() as d:
            for depth in (16, 24, 32):
                for rate in spec.PCM_RATES:
                    with self.subTest(rate=rate, depth=depth):
                        cfg = H.short_config(spec.KIND_PCM, rate, depth)
                        fsp = H.short_file("wav", cfg)
                        rep, = H.write_and_check(fsp, d)
                        self.assertEqual(rep["verdict"], "BIT_PERFECT", rep.get("verdict_text"))
                        self.assertEqual(rep["diagnosis"]["lock"], "exact")
                        sig = rep["diagnosis"]["signal"]
                        self.assertEqual((sig["kind"], sig["rate"], sig["depth"]), ("PCM", rate, depth))
                        self.assertEqual(sig["blocks"], {"lead_in": 2, "body": 24, "total": 28})
                        self.assertEqual(rep["diagnosis"]["sync"], {"x_syncs": 28, "r_headers": 4})
                        self.assertEqual(rep["diagnosis"]["depth"]["used_bits"], depth)
                        self.assertEqual(rep["diagnosis"]["stream"]["slot_bits"], depth)
                        os.remove(os.path.join(d, fsp.filename))

    @unittest.skipUnless(HAVE_FLAC, "flac >= 1.4.0 not installed")
    def test_every_flac_configuration(self):
        with H.TempDir() as d:
            for depth in (16, 24):
                for rate in spec.PCM_RATES:
                    cfg = H.short_config(spec.KIND_PCM, rate, depth)
                    self.assertIn("flac", fileset.formats("wav", cfg))
                    with self.subTest(rate=rate, depth=depth):
                        fsp = H.short_file("wav", cfg, encoded=True)
                        wav = fileset.write_file(fsp, d)
                        flac = fileset.write_flac(fsp, wav)   # verifies decode == WAV
                        self.assertEqual(containers.decode_flac_pcm(flac),
                                         containers.wav_pcm_data(wav))
                        rep = check.check(readers.read_file(flac))
                        self.assertEqual(rep["verdict"], "BIT_PERFECT")
                        self.assertEqual(rep["diagnosis"]["stream"]["container"].split()[0], "FLAC")

    def test_flac_rule(self):
        """FLAC, AIFF and ALAC at every rate for 16- and 24-bit PCM and for DoP;
        nothing for 32-bit, DSF and DFF."""
        files = fileset.test_set(pcm32=True) + fileset.sweep_set()
        all3 = ("flac", "aiff", "alac")
        for f in files:
            if f.fault:
                want = ()                          # the negative files: WAV only
            elif f.container == "wav":
                want = () if f.cfg.depth > 24 else all3
            else:
                want = all3 if f.container == "dop" else ()
            self.assertEqual(f.formats, want, f.name)
        self.assertEqual(sum("flac" in f.formats for f in fileset.test_set()), 23)
        self.assertEqual(sum("alac" in f.formats for f in fileset.test_set()), 23)
        self.assertEqual(sum("aiff" in f.formats for f in fileset.test_set()), 23)

    @unittest.skipUnless(HAVE_FLAC, "flac >= 1.4.0 not installed")
    def test_dop_flac_every_rate(self):
        """DoP-FLAC up to DSD256 (705.6 kHz, outside the FLAC subset)."""
        with H.TempDir() as d:
            for rate in spec.DSD_RATES:
                cfg = H.short_config(spec.KIND_DSD, rate, 1)
                self.assertIn("flac", fileset.formats("dop", cfg))
                with self.subTest(rate=rate):
                    fsp = H.short_file("dop", cfg, encoded=True)
                    wav = fileset.write_file(fsp, d)
                    flac = fileset.write_flac(fsp, wav)   # verifies decode == WAV
                    self.assertEqual(containers.decode_flac_pcm(flac),
                                     containers.wav_pcm_data(wav))
                    rep = check.check(readers.read_file(flac))
                    self.assertEqual(rep["verdict"], "BIT_PERFECT")
                    self.assertEqual(rep["diagnosis"]["stream"]["transport"], "DoP")
                    self.assertEqual(rep["diagnosis"]["stream"]["rate"], rate // 16)
                    self.assertEqual((rep["diagnosis"]["signal"]["kind"], rep["diagnosis"]["signal"]["rate"]), ("DSD", rate))


class DsdRoundTrip(unittest.TestCase):
    def test_every_dsd_configuration(self):
        with H.TempDir() as d:
            for rate in spec.DSD_RATES:
                for cont in ("dsf", "dff", "dop"):
                    with self.subTest(rate=rate, container=cont):
                        cfg = H.short_config(spec.KIND_DSD, rate, 1)
                        fsp = H.short_file(cont, cfg)
                        rep, = H.write_and_check(fsp, d)
                        self.assertEqual(rep["verdict"], "BIT_PERFECT", rep.get("verdict_text"))
                        self.assertEqual(rep["diagnosis"]["stream"]["transport"],
                                         "DoP" if cont == "dop" else "DSD")
                        self.assertEqual(rep["diagnosis"]["signal"]["kind"], "DSD")
                        self.assertEqual(rep["diagnosis"]["signal"]["rate"], rate)
                        self.assertEqual(rep["diagnosis"]["sync"]["r_headers"], 4)
                        os.remove(os.path.join(d, fsp.filename))


class StandardLength(unittest.TestCase):
    def test_standard_files(self):
        cases = [fileset.pcm_file(44100, 16), fileset.pcm_file(768000, 32),
                 fileset.dsd_file(2822400, "dsf"), fileset.dsd_file(2822400, "dop")]
        with H.TempDir() as d:
            for f in cases:
                with self.subTest(f.name):
                    rep, = H.write_and_check(f, d)
                    self.assertEqual(rep["verdict"], "BIT_PERFECT")
                    b = rep["diagnosis"]["signal"]["blocks"]
                    self.assertEqual((b["lead_in"], b["body"], b["total"]),
                                     (f.cfg.lead_blocks, f.cfg.body_blocks, f.cfg.total_blocks))
                    cov = rep["diagnosis"]["coverage"]
                    self.assertEqual((cov["end"], cov["missing"], cov["body_blocks"]),
                                     ("LAST", "none", f.cfg.body_blocks))
                    self.assertEqual((cov["lead"], cov["body"], cov["total"]),
                                     (f.cfg.lead_blocks, f.cfg.body_blocks, f.cfg.total_blocks))
                    self.assertEqual(cov["units"], cov["equal"])
                    self.assertEqual((cov["altered"], cov["uncompared"]), (0, 0))

    def test_32bit_on_request(self):
        default = fileset.test_set()
        extra = [f for f in fileset.test_set(pcm32=True) if f not in default]
        self.assertFalse(any(f.cfg.depth == 32 for f in default))
        self.assertEqual(sorted(f.cfg.rate for f in extra), sorted(spec.PCM_RATES))
        self.assertTrue(all(f.cfg.depth == 32 and f.container == "wav" and not f.formats
                            for f in extra))

    def test_set_lengths(self):
        for f in fileset.test_set():
            c = f.cfg
            base, _ = spec.family(c.rate)
            lead, tail = (22, 6) if base == 44100 else (24, 6)
            self.assertEqual((c.lead_blocks, c.tail_blocks), (lead, tail), f.name)
            secs = c.body_blocks * 4096 / base
            want = 10
            self.assertTrue(want <= secs < want + 4096 / base, f.name)
            self.assertNotIn("TEST", f.filename)
            self.assertTrue(f.title().startswith(f"TEST - DO NOT PLAY ON SPEAKERS - {f.number()} BPC1 "))
            self.assertIn("TEST - DO NOT PLAY ON SPEAKERS OR HEADPHONES.", f.comment)


    def test_standard_body_blocks(self):
        """A 10 s body at every rate: 108 blocks in the 44.1 kHz family and
        DSD, 118 in the 48 kHz family."""
        files = fileset.test_set(pcm32=True)
        self.assertEqual(len(files), 3 * len(spec.PCM_RATES) + 3 * len(spec.DSD_RATES) + 4)
        for f in files:
            with self.subTest(f.name):
                want = 108 if spec.family(f.cfg.rate)[0] == 44100 else 118
                self.assertEqual(f.cfg.body_blocks, want)


class SlotsAndCaptures(unittest.TestCase):
    def setUp(self):
        self.c24 = H.short_config(spec.KIND_PCM, 96000, 24)
        self.w24 = H.pcm_words(self.c24)

    def test_16_in_24_slot(self):
        c = H.short_config(spec.KIND_PCM, 48000, 16)
        rep = H.check_words(H.pcm_words(c), 48000, 24)
        self.assertEqual(rep["verdict"], "BIT_PERFECT")
        self.assertIn("16-bit content in a 24-bit slot", rep["diagnosis"]["notes"])
        self.assertEqual(rep["diagnosis"]["depth"]["used_bits"], 16)

    def test_raw_s32le_24_in_32(self):
        with H.TempDir() as d:
            p = os.path.join(d, "cap.raw")
            self.w24.astype("<i4").tofile(p)
            rep = check.check(readers.read_file(p, raw="s32le", rate=96000, nch=2))
            self.assertEqual(rep["verdict"], "BIT_PERFECT")
            self.assertIn("24-bit content in a 32-bit slot", rep["diagnosis"]["notes"])

    def test_raw_s24le_low_justified(self):
        with H.TempDir() as d:
            p = os.path.join(d, "cap.raw")
            (self.w24.astype(np.int64) >> 8).astype("<i4").tofile(p)
            rep = check.check(readers.read_file(p, raw="s24le", rate=96000, nch=2))
            self.assertEqual(rep["verdict"], "BIT_PERFECT")

    def test_raw_native_dsd_u32_be(self):
        cfg = H.short_config(spec.KIND_DSD, 2822400, 1)
        bits = H.dsd_bits(cfg)                       # (nch, nbits)
        by = np.packbits(bits, axis=1)               # (nch, bytes), MSB first
        words = by.reshape(2, -1, 4).transpose(1, 0, 2)   # (words, nch, 4) big-endian
        with H.TempDir() as d:
            p = os.path.join(d, "cap.raw")
            words.tofile(p)
            rep = check.check(readers.read_file(p, raw="dsd_u32_be", rate=88200, nch=2))
            self.assertEqual(rep["verdict"], "BIT_PERFECT")
            self.assertEqual(rep["diagnosis"]["stream"]["transport"], "DSD")

    def test_float_wav(self):
        """A 24-bit file in a float32 WAV is exact (24-bit mantissa)."""
        f = (self.w24.astype(np.float64) / 2 ** 31).astype("<f4")
        st = readers.Stream(False, 96000, 2, readers.pcm_to_w(f.tobytes(), 32, True, 2),
                            "float", 32, 32, is_float=True)
        rep = check.check(st)
        self.assertEqual(rep["verdict"], "BIT_PERFECT")


if __name__ == "__main__":
    unittest.main()
