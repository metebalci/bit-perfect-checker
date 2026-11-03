"""The code-sweep set: kind 5, lbD, the sweep payload, its files, and verify.py
on sweep streams (spec 7.4 and 11.6)."""

import unittest

import numpy as np

from checker import model, readers, verify
from generator import fileset, signal, spec

from tests import helpers as H

KEYS = (0, 0x12345678, 0xDEADBEEF, 0xFFFFFFFF)


def sweep_cfg(rate=48000, depth=16, **kw):
    geo = dict(H.SHORT, **kw)
    return H.Config(kind=spec.KIND_SWEEP, rate=rate, depth=depth, **geo)


def sweep_units(cfg, x, c):
    """The sweep units of channel c of samples x (blocks 0..), in order."""
    B = cfg.block_len
    out = []
    for k in range(len(x) // B):
        if not spec.is_rhdr_block(k):
            out.append(x[k * B + spec.PAYLOAD_START:(k + 1) * B, c])
    return np.concatenate(out)


class LbD(unittest.TestCase):
    def test_bijective_16(self):
        s = np.arange(1 << 16, dtype=np.uint32)
        for key in KEYS:
            u = spec.lbD_np(16, key, s)
            self.assertTrue((np.bincount(u, minlength=1 << 16) == 1).all(), hex(key))

    def test_bijective_24(self):
        s = np.arange(1 << 24, dtype=np.uint32)
        for key in KEYS[:2]:
            u = spec.lbD_np(24, key, s)
            self.assertTrue((np.bincount(u, minlength=1 << 24) == 1).all(), hex(key))

    def test_implementations_agree(self):
        rng = np.random.default_rng(5)
        for D in (16, 24):
            s = rng.integers(0, 1 << D, 2000, dtype=np.uint32)
            for key in KEYS:
                a = spec.lbD_np(D, key, s)
                b = model.lbD(D, key, s)
                self.assertTrue(np.array_equal(a, b))
                self.assertEqual([spec.lbD(D, key, int(v)) for v in s[:50]], a[:50].tolist())

    def test_key_is_the_low_bits_of_the_channel_key(self):
        for D in (16, 24):
            for key in KEYS:
                self.assertEqual(spec.sweep_key(key, D), key & ((1 << D) - 1))
        s = np.arange(100, dtype=np.uint32)
        self.assertTrue(np.array_equal(spec.lbD_np(16, 0xABCD1234, s), spec.lbD_np(16, 0x1234, s)))


class SweepIndex(unittest.TestCase):
    def test_rhdr_blocks_before_k(self):
        for k in range(200):
            self.assertEqual((k + 7) // 16, sum(1 for j in range(k) if spec.is_rhdr_block(j)), k)

    def test_index_runs_on_across_rhdr_blocks(self):
        B = 4096
        prev = None
        for k in range(40):
            if spec.is_rhdr_block(k):
                continue
            first = spec.sweep_index(k, spec.PAYLOAD_START, B, 24)
            if prev is not None:
                self.assertEqual(first, prev + 1, k)
            prev = spec.sweep_index(k, B - 1, B, 24)


class Payload(unittest.TestCase):
    def test_every_code_once_16(self):
        cfg = sweep_cfg(48000, 16)
        x = signal.pcm_blocks(cfg, 0, cfg.total_blocks)
        for c in range(cfg.nch):
            u = sweep_units(cfg, x, c).astype(np.int64) & 0xFFFF
            self.assertGreater(len(u), (1 << 16) + 5000)
            for start in (0, 1, 4999):
                win = u[start:start + (1 << 16)]
                self.assertTrue((np.bincount(win, minlength=1 << 16) == 1).all(), (c, start))

    def test_every_code_once_24(self):
        f = fileset.sweep_file(705600, 24)
        cfg = f.cfg
        k1 = cfg.lead_blocks + cfg.body_blocks
        x = signal.pcm_blocks(cfg, cfg.lead_blocks, k1)
        B = cfg.block_len
        units = []
        for k in range(cfg.lead_blocks, k1):
            if not spec.is_rhdr_block(k):
                units.append(x[(k - cfg.lead_blocks) * B + 72:(k - cfg.lead_blocks + 1) * B])
        u = np.concatenate(units).astype(np.int64) & 0xFFFFFF
        self.assertGreaterEqual(len(u), 1 << 24)
        for c in range(cfg.nch):
            self.assertTrue((np.bincount(u[:1 << 24, c], minlength=1 << 24) == 1).all(), c)

    def test_structure_as_pcm(self):
        """X-syncs, bursts and R-header blocks as in a PCM file; payload codes."""
        cfg = sweep_cfg(44100, 24)
        pcm = cfg.with_(kind=spec.KIND_PCM)
        x, y = signal.pcm_blocks(cfg, 0, 10), signal.pcm_blocks(pcm, 0, 10)
        B = cfg.block_len
        # the R-header block: zeros and chips of the same level (the words differ in their kind)
        self.assertTrue(np.array_equal(np.abs(x[8 * B + 72:9 * B]), np.abs(y[8 * B + 72:9 * B])))
        for k in (0, 3, 9):
            self.assertTrue(np.array_equal(x[k * B + 8:k * B + 72], y[k * B + 8:k * B + 72]))
            self.assertEqual(x[k * B, 0] >> 8, cfg.sync_words(0, k)[0] - (1 << 16))
        k, i, c = 9, 1000, 1
        s = spec.sweep_index(k, i, B, 24)
        u = spec.lbD(24, spec.sweep_key(cfg.key(c), 24), s)
        self.assertEqual(x[k * B + i, c], u - (1 << 24) if u >> 23 else u)

    def test_model_matches_generator(self):
        for rate, depth in ((44100, 16), (48000, 24), (768000, 24)):
            cfg = sweep_cfg(rate, depth)
            f = model.Fields(1, spec.KIND_SWEEP, cfg.depth_code, cfg.rate_code, 0, 2, 0, cfg.seed, 0)
            self.assertTrue(f.valid())
            sig = model.Signal(f, cfg.lead_blocks, cfg.body_blocks, cfg.total_blocks)
            B = cfg.block_len
            for k in (0, 5, 8, cfg.total_blocks - 1):
                with self.subTest(rate=rate, depth=depth, k=k):
                    want = (signal.pcm_blocks(cfg, k, k + 1).astype(np.int64)
                            << (32 - depth)).astype(np.int32)
                    self.assertTrue(np.array_equal(sig.pcm_block(k), want))
                    self.assertEqual(sig.units_per_block, B)

    def test_validity(self):
        for depth_code, rate_code, ok in ((2, 0, True), (3, 9, True), (4, 0, False), (1, 0, False),
                                          (2, 10, False)):
            f = model.Fields(1, spec.KIND_SWEEP, depth_code, rate_code, 0, 2, 0, 1, 0)
            self.assertEqual(f.valid(), ok, (depth_code, rate_code))


class Files(unittest.TestCase):
    def test_the_set(self):
        files = fileset.sweep_set()
        main = [f for f in files if not f.fault]
        neg = [f for f in files if f.fault]
        self.assertEqual(len(main), 20)          # each as WAV, FLAC, AIFF and ALAC
        self.assertEqual(sorted(f.name for f in neg),
                         ["bpc1_pcm_044k1_16bit_sweep_altered", "bpc1_pcm_044k1_24bit_sweep_altered"])
        for f in files:
            self.assertTrue(f.name.endswith(("_sweep", "_sweep_altered")), f.name)
            self.assertEqual(f.cfg.kind, spec.KIND_SWEEP)
            self.assertEqual(f.formats, ("flac", "aiff", "alac"), f.name)
            self.assertIn("code sweep, FULL-SCALE NOISE", f.title)
            self.assertIn("full-scale code sweep", f.comment)
            self.assertIn("DO NOT PLAY ON SPEAKERS OR HEADPHONES", f.comment)
        for f in neg:
            self.assertEqual(f.relpath, "negative/" + f.filename)
            self.assertTrue(f.comment.endswith("A checker must report NOT_BIT_PERFECT."))
        self.assertFalse({f.name for f in files} & {f.name for f in fileset.test_set(pcm32=True)})

    def test_body_lengths(self):
        for f in fileset.sweep_set():
            c = f.cfg
            base = spec.family(c.rate)[0]
            self.assertEqual((c.lead_blocks, c.tail_blocks), spec_lead_tail(base))
            units = sum(c.block_len - 72 for k in range(c.lead_blocks, c.lead_blocks + c.body_blocks)
                        if not spec.is_rhdr_block(k))
            if c.depth == 16:
                self.assertEqual(c.body_blocks, 108 if base == 44100 else 118)
                self.assertGreaterEqual(units, 1 << 16)
            else:
                less = sum(c.block_len - 72 for k in range(c.lead_blocks, c.lead_blocks + c.body_blocks - 1)
                           if not spec.is_rhdr_block(k))
                self.assertTrue(less < (1 << 24) <= units, (f.name, units))

    def test_regular_set_unchanged(self):
        self.assertFalse([f for f in fileset.test_set(pcm32=True) if f.cfg.kind == spec.KIND_SWEEP])


def spec_lead_tail(base):
    return (22, 6) if base == 44100 else (24, 6)


class Verify(unittest.TestCase):
    def check(self, x, rate, slot=32):
        return verify.verify(H.pcm_stream(x, rate, slot))

    def words(self, cfg):
        return H.pcm_words(cfg)

    def test_sweep_file_passes(self):
        for rate, depth in ((44100, 16), (48000, 24), (768000, 16)):
            cfg = sweep_cfg(rate, depth)
            with self.subTest(rate=rate, depth=depth):
                r = self.check(self.words(cfg), rate)
                self.assertEqual((r["verdict"], r["kind"], r["depth"], r["bits"], r["dop"]),
                                 ("BIT_PERFECT", "SWEEP", depth, depth, 0))

    def test_single_code_fault(self):
        """One code changed everywhere: a regular file does not carry it, a
        sweep file does."""
        D = 16
        code = (1 << (D - 1)) - 2
        for kind, want in ((spec.KIND_PCM, "BIT_PERFECT"), (spec.KIND_SWEEP, "NOT_BIT_PERFECT")):
            cfg = sweep_cfg(48000, D).with_(kind=kind)
            s = signal.pcm_blocks(cfg, 0, cfg.total_blocks)
            body = slice(cfg.lead_blocks * cfg.block_len, (cfg.lead_blocks + cfg.body_blocks) * cfg.block_len)
            hit = np.nonzero(s[body, 0] == code)[0]
            s[body, 0][s[body, 0] == code] = code + 1
            x = (s.astype(np.int64) << (32 - D)).astype(np.int32)
            with self.subTest(kind=kind):
                r = self.check(x, 48000)
                self.assertEqual(r["verdict"], want)
                if want != "BIT_PERFECT":
                    self.assertEqual(r["diff_unit"], body.start + int(hit[0]))

    def test_negative_sweep_files(self):
        with H.TempDir() as d:
            f = [f for f in fileset.sweep_set() if f.fault and f.cfg.depth == 16][0]
            path = fileset.write_file(f, d)
            r = verify.verify(readers.read_file(path))
            first = fileset.sweep_fault_first(f)
            self.assertEqual((r["verdict"], r["kind"], r["diff_unit"]), ("NOT_BIT_PERFECT", "SWEEP", first))
            self.assertIsNotNone(first)

    def test_negative_sweep_24_in_memory(self):
        f = [f for f in fileset.sweep_set() if f.fault and f.cfg.depth == 24][0]
        cfg = f.cfg
        chunks = ((a, signal.pcm_blocks(cfg, a, b))
                  for a, b in fileset._chunk_ranges(cfg.total_blocks, cfg.block_len))
        s = np.concatenate(list(fileset._faulty(f, chunks, cfg.block_len)))
        x = (s.astype(np.int64) << 8).astype(np.int32)
        r = self.check(x, cfg.rate)
        self.assertEqual((r["verdict"], r["diff_unit"]), ("NOT_BIT_PERFECT", fileset.sweep_fault_first(f)))


class Packages(unittest.TestCase):
    def test_sweep_zips(self):
        import zipfile
        from generator import package as P
        self.assertEqual([p.filename for p in P.SWEEP_RELEASE[:2]],
                         ["bpc1-r1-sweep-wav.zip", "bpc1-r1-sweep-flac.zip"])
        self.assertEqual([p.filename for p in P.RELEASE[:2]], ["bpc1-r1-wav.zip", "bpc1-r1-flac.zip"])
        for pkg in P.SWEEP_RELEASE:
            with self.subTest(pkg.name):
                names = P.members(pkg)
                # every format at every rate
                self.assertEqual(len(names), 22)
                self.assertEqual(sum("705k6" in n or "768k" in n for n in names), 4)
                self.assertEqual(sum(n.startswith("negative/") for n in names), 2)
                self.assertEqual(pkg.method, zipfile.ZIP_STORED)
                self.assertEqual(pkg.warning_file,
                                 "!!! FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS OR HEADPHONES !!!.txt")
                text = P.readme(pkg).decode("ascii").replace("\r\n", "\n")
                self.assertTrue(text.startswith("!!! FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS"))
                self.assertIn("MIT", text)
                flat = " ".join(text.split())
                self.assertIn("Clipping and level indicators", text)
                self.assertIn("the sweep body covers every sample value", flat)
                self.assertIn("Turn off ReplayGain, Sound Check, volume normalization, limiters "
                              'and any "prevent clipping" option.', flat)
                neg = P.negative_readme(pkg).decode("ascii")
                self.assertEqual(neg.count("-> NOT_BIT_PERFECT"), 2)
                self.assertIn("A checker must report NOT_BIT_PERFECT.", neg)

    def test_write_with_the_sweep_warning(self):
        import zipfile
        from generator import package as P
        with H.TempDir() as d:
            path = P.write_zip(d + "/z.zip", "top", [], zipfile.ZIP_STORED, b"r", texts={},
                               warning_file=P.SWEEP_WARNING_FILE)
            names = zipfile.ZipFile(path).namelist()
            self.assertIn("top/" + P.SWEEP_WARNING_FILE, names)
            self.assertNotIn("top/" + P.WARNING_FILE, names)


class Diagnosis(unittest.TestCase):
    """The diagnosis describes sweep streams as it can; it must not fail."""

    def test_reports(self):
        from checker import check
        cfg = sweep_cfg(48000, 24)
        w = H.pcm_words(cfg)
        B = cfg.block_len
        for name, x in (("clean", w), ("a frame removed", np.delete(w, 10 * B + 5, axis=0)),
                        ("truncated to 16 bits", w & np.int32(-(1 << 16)))):
            with self.subTest(name):
                reps = check.check_stream(H.pcm_stream(x, 48000, 32))
                self.assertTrue(reps)
                self.assertEqual(reps[0]["verdict"], "BIT_PERFECT" if name == "clean" else "NOT_BIT_PERFECT")
                self.assertIsNotNone(reps[0]["diagnosis"])
                self.assertEqual(reps[0]["kind"], "SWEEP")


if __name__ == "__main__":
    unittest.main()
