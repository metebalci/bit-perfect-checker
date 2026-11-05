# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""One DSD kind (docs/bpc1.md, 3.1): the DSF, DFF and DoP files of a DSD
rate carry the same DSD bits, as the WAV, FLAC, AIFF and ALAC files of a
PCM signal carry the same samples; kinds 1, 2 and 5 are the only ones a
checker accepts.

T1 writes short DSF, DFF and DoP files (WAV, FLAC, AIFF, ALAC) with the
set's own file specs and compares the bits each decodes to. Set
BPC1_FULL_SET=1 to compare the published DSD files too. T2 checks the
header-word rule over every h0 (the C engine's agreement is in
tests/test_bpc1_c.py)."""

import os
import subprocess
import sys
import unittest
from dataclasses import replace

import numpy as np

from checker import model, readers
from generator import containers, fileset, spec

from tests import helpers as H
from tests.test_repeatability import ROOT

H1 = 0x0201            # channel 0 of 2, flags LEAD_IN


def short(f):
    """A file spec of the set with the short structure of tests/helpers.py."""
    return replace(f, cfg=replace(f.cfg, **H.SHORT))


def dsd_bits(path):
    """The DSD bytes (bytes, nch), earliest bit in the MSB, of a DSF, DFF or
    DoP file, and its DSD rate."""
    s = readers.read_file(path)
    if not s.dsd:
        s = readers.unpack_dop(s)
    return s.data, s.rate


def written(out, files):
    """Paths of every file of `files` and its FLAC, AIFF and ALAC as the
    tools allow."""
    flac, alac = containers.flac_usable(), containers.alac_usable()
    paths = []
    for f in files:
        paths += [os.path.join(out, *rel.split("/"))
                  for rel, _, _ in fileset.job((f, out, flac, True, alac))]
    return paths


class SameBits(unittest.TestCase):
    """T1: the DSF, DFF and DoP files of one rate decode to the same bits."""

    def test_short_files(self):
        for rate in spec.DSD_RATES:
            files = [short(fileset.dsd_file(rate, c)) for c in ("dsf", "dff", "dop")]
            self.assertEqual({f.cfg.kind for f in files}, {spec.KIND_DSD})
            with self.subTest(rate=rate), H.TempDir() as d:
                paths = written(d, files)
                self.assertGreaterEqual(len(paths), 4)
                want = H.dsd_bits(files[0].cfg)
                want = np.ascontiguousarray(np.packbits(want, axis=1).T)
                for p in paths:
                    bits, got_rate = dsd_bits(p)
                    self.assertEqual(got_rate, rate, p)
                    self.assertTrue(np.array_equal(bits, want), os.path.basename(p))

    @unittest.skipUnless(os.environ.get("BPC1_FULL_SET"), "set BPC1_FULL_SET=1 for the published files")
    def test_published_files(self):
        with H.TempDir() as d:
            subprocess.run([sys.executable, "-m", "generator", "--out", d, "--only", "dsd", "--jobs", "12"],
                           cwd=ROOT, check=True, capture_output=True)
            by_rate = {}
            for f in fileset.test_set():
                if f.cfg.dsd and not f.fault:
                    rels = [f.relpath] + [f.relpath_as(x) for x in f.formats]
                    by_rate.setdefault(f.cfg.rate, []).extend(rels)
            for rate, rels in by_rate.items():
                with self.subTest(rate=rate):
                    self.assertEqual(len(rels), 6)
                    first, _ = dsd_bits(os.path.join(d, rels[0]))
                    for rel in rels[1:]:
                        bits, got_rate = dsd_bits(os.path.join(d, rel))
                        self.assertEqual(got_rate, rate, rel)
                        self.assertTrue(np.array_equal(bits, first), rel)


class Kinds(unittest.TestCase):
    """T2: the header-word rule accepts kinds 1, 2 and 5 only."""

    def test_h0_table(self):
        accepted = {}
        for h0 in range(1 << 16):
            f = model.Fields.parse(h0, H1, 0x4250, 0x4331, 0)
            if f.valid():
                accepted.setdefault(f.kind, set()).add((f.depth_code, f.rate_code))
        self.assertEqual(sorted(accepted), [model.KIND_PCM, model.KIND_DSD, model.KIND_SWEEP])
        self.assertEqual(accepted[model.KIND_DSD], {(1, r) for r in (10, 11, 12)})
        self.assertEqual(accepted[model.KIND_PCM], {(d, r) for d in (2, 3, 4) for r in range(10)})
        self.assertEqual(accepted[model.KIND_SWEEP], {(d, r) for d in (2, 3) for r in range(10)})

    def test_generator_kinds(self):
        self.assertEqual((spec.KIND_PCM, spec.KIND_DSD, spec.KIND_SWEEP), (1, 2, 5))
        for f in fileset.test_set() + fileset.sweep_set() + fileset.gapless_set():
            self.assertEqual(f.cfg.kind, spec.KIND_DSD if f.cfg.dsd else
                             spec.KIND_SWEEP if "sweep" in f.name else spec.KIND_PCM, f.name)


if __name__ == "__main__":
    unittest.main()
