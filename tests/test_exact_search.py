# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The gap search's masked comparison and the inverted X-sync search (12.2),
against the golden vectors and on whole streams."""

import json
import os
import unittest

import numpy as np

from checker import exact, readers
from generator import spec

from tests import helpers as H
from tests import simulate as sim

VECTORS = os.path.join(os.path.dirname(__file__), "..", "vectors", "bpc1-v1-vectors.json")


def words(*xs):
    """One unit of 32-bit words, given as unsigned values."""
    return np.array([[np.uint32(x).view(np.int32) for x in xs]], dtype=np.int32)


class GapMatch(unittest.TestCase):
    def test_exact_without_k(self):
        self.assertTrue(exact.gap_match(words(5, 7), words(5, 7), None)[0])
        self.assertFalse(exact.gap_match(words(5, 7), words(5, 8), None)[0])

    def test_two_steps_either_way(self):
        q = 1 << 16
        ref = 0x12340000 + 0xABCD
        for steps, ok in ((0, True), (1, True), (2, True), (3, False), (-1, True), (-2, True), (-3, False)):
            with self.subTest(steps=steps):
                rx = (0x12340000 + steps * q) & 0xFFFFFFFF
                self.assertEqual(bool(exact.gap_match(words(rx), words(ref), 16)[0]), ok)

    def test_signed_without_wrap(self):
        # In 32-bit arithmetic 0x80000000 - 0x7FFFF000 wraps to one step.
        self.assertFalse(exact.gap_match(words(0x80000000), words(0x7FFFF000), 20)[0])

    def test_every_channel(self):
        self.assertFalse(exact.gap_match(words(0x12340000, 0x12370000),
                                         words(0x12340000, 0x12340000), 16)[0])


class Vectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(VECTORS) as f:
            cls.v = json.load(f)["checking"]

    def test_gap_match_rows(self):
        for row in self.v["gap_match"]:
            with self.subTest(row=row):
                got = exact.gap_match(words(*[int(x, 16) for x in row["received"]]),
                                      words(*[int(x, 16) for x in row["expected"]]), row["k"])[0]
                self.assertEqual(bool(got), row["match"])

    def test_inverted_xsync(self):
        x = self.v["inverted_xsync"]
        rx = np.array([[np.uint32(int(w, 16)).view(np.int32)] for w in x["received"]], dtype=np.int32)
        st = readers.Stream(False, 48000, 1, rx, "raw", 32, 32)
        self.assertEqual(exact.find_anchors(st), [])
        a, = exact.find_anchors(st, inverted=True)
        self.assertEqual((a.pos, a.k), (0, x["block"]))
        self.assertEqual(a.fields[0].block, x["block"])


class InvertedSearch(unittest.TestCase):
    """Every X-sync of an inverted stream is found, none in other streams."""

    def anchors(self, x, inverted):
        return exact.find_anchors(H.pcm_stream(x, 48000, 32), inverted=inverted)

    def test_inverted_streams(self):
        cfg = H.short_config(spec.KIND_PCM, 48000, 24)
        w = H.pcm_words(cfg)
        for name, x in (("inverted", sim.invert(w, 24)),
                        ("inverted, truncated to 16", sim.truncate(sim.invert(w, 24), 16)),
                        ("inverted, truncated to 20", sim.truncate(sim.invert(w, 24), 20))):
            with self.subTest(name=name):
                a = self.anchors(x, True)
                self.assertEqual([b.k for b in a], list(range(cfg.total_blocks)))
                self.assertTrue(all(len(b.fields) == 2 for b in a))

    def test_no_false_lock(self):
        for depth in (16, 24, 32):
            cfg = H.short_config(spec.KIND_PCM, 48000, depth)
            w = H.pcm_words(cfg)
            for name, x in (("clean", w), ("truncated to 16", sim.truncate(w, 16)),
                            ("dithered to 16", sim.tpdf_dither(w, 16))):
                with self.subTest(depth=depth, name=name):
                    self.assertEqual(self.anchors(x, True), [])
