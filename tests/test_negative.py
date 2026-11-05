# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The negative test files: each deliberately faulty file is NOT_BIT_PERFECT,
and the diagnosis names its fault."""

import os
import unittest

from checker import check, readers
from generator import fileset

from tests import helpers as H

# What the reference checker must give each file: the verdict, and from the
# diagnosis the reason, its bits and the body counts that are not zero.
WANT = {
    "bpc1_pcm_044k1_24bit_altered": ("NOT_BIT_PERFECT", "TRUNCATED", 16, {}),
    "bpc1_dsd064_dop-176k4_24bit_altered": ("NOT_BIT_PERFECT", "BIT_ERRORS", None, {}),
    "bpc1_pcm_044k1_24bit_incomplete": ("NOT_BIT_PERFECT", None, None, {"dropped": 1000}),
    "bpc1_dsd064_dop-176k4_24bit_incomplete": ("NOT_BIT_PERFECT", None, None, {"dropped": 2000}),
}


def result(rep):
    d = rep["diagnosis"]
    r = d["reasons"][0] if d["reasons"] else {}
    return rep["verdict"], r.get("code"), r.get("bits"), H.counts(rep)


class NegativeFiles(unittest.TestCase):
    def test_the_set(self):
        neg = [f for f in fileset.test_set() if f.fault]
        self.assertEqual({f.name for f in neg}, set(WANT))
        self.assertEqual(neg, fileset.negative_set())
        for f in neg:
            self.assertEqual(f.relpath, "negative/" + f.filename)
            self.assertTrue(f.filename.endswith(".wav"))
            self.assertEqual(f.formats, ())                # WAV only
            self.assertTrue(f.title().endswith(" WAV - NEGATIVE TEST, DELIBERATELY " + f.fault.upper()))
            self.assertIn("NEGATIVE TEST, deliberately " + f.fault, f.comment)
            self.assertTrue(f.comment.endswith("A checker must report NOT_BIT_PERFECT."))
            self.assertIn("DO NOT PLAY ON SPEAKERS OR HEADPHONES", f.comment)
            # the fault is in the body, away from X-syncs and R-header blocks
            k = f.cfg.lead_blocks + fileset.FAULT_BLOCK
            self.assertLess(k, f.cfg.lead_blocks + f.cfg.body_blocks)
            self.assertNotEqual(k % 16, 8)

    def test_verdicts(self):
        with H.TempDir() as d:
            for f in fileset.negative_set():
                with self.subTest(f.name):
                    path = fileset.write_file(f, d)
                    self.assertEqual(path, os.path.join(d, "negative", f.filename))
                    rep = check.check(readers.read_file(path))
                    self.assertEqual(result(rep), WANT[f.name], rep["verdict_text"])
                    # the first body unit that differs, in seconds into the file
                    self.assertEqual(rep["diff_unit"], fileset.fault_start(f.cfg) * (2 if f.cfg.dsd else 1)
                                     if f.fault == "incomplete" or f.cfg.dsd else
                                     f.cfg.lead_blocks * f.cfg.block_len)
                    dg = rep["diagnosis"]
                    cov = dg["coverage"]
                    self.assertEqual((cov["end"], cov["body_blocks"]), ("LAST", f.cfg.body_blocks))
                    self.assertEqual(cov["missing"],
                                     "middle" if f.fault == "incomplete" else "none")
                    self.assertEqual(dg["stream"]["transport"], "DoP" if f.cfg.dsd else "PCM")
                    self.assertFalse([n for n in dg["notes"] if "DoP markers" in n],
                                     "the DoP markers are kept")
                    if f.cfg.dsd and f.fault == "altered":
                        self.assertEqual(cov["altered"], 2 * fileset.FAULT_FRAMES)
                    if f.fault == "incomplete":
                        self.assertEqual(dg["events"][0]["block"],
                                         f.cfg.lead_blocks + fileset.FAULT_BLOCK)
                    os.remove(path)

    def test_every_negative_file(self):
        """The seven negative files of the three sets, WAV only, with the
        fixed numbers of 11.2."""
        neg = [f for f in fileset.test_set() + fileset.sweep_set() + fileset.gapless_set() if f.fault]
        self.assertEqual(sorted(f.filename for f in neg),
                         ["90000-bpc1_pcm_044k1_24bit_altered.wav", "90001-bpc1_pcm_044k1_24bit_incomplete.wav",
                          "90002-bpc1_pcm_044k1_16bit_sweep_altered.wav",
                          "90003-bpc1_pcm_044k1_16bit_gapless_part1_gap.wav",
                          "90004-bpc1_pcm_044k1_16bit_gapless_part2_gap.wav",
                          "91000-bpc1_dsd064_dop-176k4_24bit_altered.wav",
                          "91001-bpc1_dsd064_dop-176k4_24bit_incomplete.wav"])
        self.assertEqual({f.formats for f in neg}, {()})


if __name__ == "__main__":
    unittest.main()
