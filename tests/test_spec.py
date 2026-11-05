# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Primitives and golden vectors, against both implementations.

The generator (generator/) and the checker's model (checker/model.py) are
independent implementations of docs/bpc1.md. These tests pin both to
published reference values and to the committed golden vectors.
"""

import json
import os
import unittest

import numpy as np

from checker import model
from generator import spec, vectors
from generator.signal import Config

VECTORS = os.path.join(os.path.dirname(__file__), "..", "vectors", "bpc1-v1-vectors.json")


class Primitives(unittest.TestCase):
    def test_crc16_check_value(self):
        # CRC-16/IBM-3740 (CCITT-FALSE) check value from the CRC catalogue
        self.assertEqual(spec.crc16(b"123456789"), 0x29B1)
        self.assertEqual(model.crc16(b"123456789"), 0x29B1)

    def test_splitmix64_reference(self):
        # first output of SplitMix64 seeded with 0, as published with the algorithm
        self.assertEqual(spec.splitmix64_first(0), 0xE220A8397B1DCDAF)
        self.assertEqual(model.splitmix64_first(0), 0xE220A8397B1DCDAF)

    def test_lb32_implementations_agree(self):
        rng = np.random.default_rng(7)
        keys = rng.integers(0, 2 ** 32, 50, dtype=np.uint64)
        ns = rng.integers(0, 2 ** 32, 1000, dtype=np.uint64).astype(np.uint32)
        for key in keys:
            a = spec.lb32_np(int(key), ns)
            b = model.lb32(int(key), ns)
            self.assertTrue(np.array_equal(a, b))
            self.assertEqual(int(a[0]), spec.lb32(int(key), int(ns[0])))

    def test_lb32_is_bijective_on_a_sample(self):
        n = np.arange(1 << 20, dtype=np.uint32)
        self.assertEqual(len(np.unique(spec.lb32_np(0x42504331, n))), 1 << 20)

    def test_geometry(self):
        for r in spec.PCM_RATES + spec.DSD_RATES:
            base, m = spec.family(r)
            self.assertEqual(spec.block_len(r), 4096 * m)
            self.assertIn(round(spec.block_len(r) / r * 1000, 2), (92.88, 85.33))
            end = spec.rhdr_start(r) + spec.RHDR_CHIPS * spec.chip_len(r)
            self.assertLess(end, spec.block_len(r))
        self.assertEqual(spec.blocks_for_seconds(2, 44100), 22)
        self.assertEqual(spec.blocks_for_seconds(2, 48000), 24)
        self.assertEqual(spec.blocks_for_seconds(10, 44100), 108)
        self.assertEqual(spec.blocks_for_seconds(0.5, 48000), 6)


class GoldenVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(VECTORS) as f:
            cls.text = f.read()
        cls.v = json.loads(cls.text)

    def test_generator_reproduces_file(self):
        self.assertEqual(vectors.dumps(vectors.build()), self.text)

    def test_primitives_in_model(self):
        p = self.v["primitives"]
        for e in p["lowbias32"]:
            self.assertEqual(int(model.lb32(int(e["key"], 16), [int(e["n"], 16)])[0]), int(e["result"], 16))
        for e in p["splitmix64_first"]:
            self.assertEqual(model.splitmix64_first(int(e["v"], 16)), int(e["result"], 16))
        for e in p["crc16_ibm3740"]:
            self.assertEqual(model.crc16(bytes.fromhex(e["data"])), int(e["crc"], 16))

    def _signal(self, c):
        f = model.Fields(1, c["kind"], c["depth_code"], c["rate_code"], 0, c["channels"],
                         0, int(c["seed"], 16), 0)
        return model.Signal(f, c["lead_blocks"], c["body_blocks"], c["total_blocks"])

    def test_model_reproduces_pcm_vectors(self):
        for c in self.v["pcm"]:
            sig = self._signal(c)
            self.assertEqual(sig.B, c["block_length"])
            self.assertEqual(sig.chip, c["chip_length"])
            self.assertEqual(["0x%08X" % k for k in sig.keys], c["channel_keys"])
            for k, b in c["blocks"].items():
                k = int(k)
                for ch in range(sig.nch):
                    self.assertEqual(["0x%04X" % w for w in sig.sync_words(ch, k)], b["sync_words"][ch])
                    if "rhdr_words" in b:
                        self.assertEqual(["0x%04X" % w for w in sig.rhdr_words(ch, k)], b["rhdr_words"][ch])
            for s in c["samples"]:
                a = s["first_frame"]
                w = sig.ref(a, a + len(s["values"])).astype(np.int64) >> (32 - sig.depth)
                self.assertEqual(w.tolist(), s["values"], (c["name"], s["what"]))

    def test_model_reproduces_dsd_vectors(self):
        for c in self.v["dsd"]:
            sig = self._signal(c)
            for s in c["dsd_bytes_msb_first"]:
                a = s["first_bit"] // 8
                n = len(s["hex"][0]) // 2
                got = sig.ref(a, a + n)
                for ch in range(sig.nch):
                    self.assertEqual(got[:, ch].tobytes().hex().upper(), s["hex"][ch],
                                     (c["name"], s["what"]))

    def test_generator_matches_model_everywhere_short(self):
        """Every unit of short files, every configuration: generator == model."""
        from generator.signal import dsd_blocks, pcm_blocks
        for depth in (16, 24, 32):
            for rate in spec.PCM_RATES:
                cfg = Config(spec.KIND_PCM, rate, depth, 1, 8, 1)
                sig = self._signal({"kind": 1, "depth_code": cfg.depth_code,
                                    "rate_code": cfg.rate_code, "channels": 2,
                                    "seed": "0x42504331",
                                    "lead_blocks": 1, "body_blocks": 8, "total_blocks": 10})
                g = pcm_blocks(cfg, 0, 10).astype(np.int64) << (32 - depth)
                m = sig.ref(0, 10 * cfg.block_len).astype(np.int64)
                self.assertTrue(np.array_equal(g.astype(np.int32), m.astype(np.int32)),
                                (rate, depth))
        for kind in (spec.KIND_DSD,):
            for rate in spec.DSD_RATES:
                cfg = Config(kind, rate, 1, 1, 8, 1)
                sig = self._signal({"kind": kind, "depth_code": 1,
                                    "rate_code": cfg.rate_code, "channels": 2,
                                    "seed": "0x42504331",
                                    "lead_blocks": 1, "body_blocks": 8, "total_blocks": 10})
                g = np.packbits(dsd_blocks(cfg, 0, 10), axis=1).T
                m = sig.ref(0, 10 * cfg.block_len // 8)
                self.assertTrue(np.array_equal(g, m), (kind, rate))


if __name__ == "__main__":
    unittest.main()
