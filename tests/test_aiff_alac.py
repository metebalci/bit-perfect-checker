"""AIFF and ALAC beside every PCM and DoP WAV, 705.6 and 768 kHz included:
the same samples, the 80-bit rate, the tags, determinism, and their release
archives."""

import os
import shutil
import struct
import unittest

import numpy as np

from generator import alac, containers, fileset, package as P, spec

from tests import helpers as H

ALAC = containers.alac_usable()


def short_files():
    out = []
    for rate, depth in ((44100, 16), (96000, 24), (384000, 24), (705600, 16), (705600, 24),
                        (768000, 16), (768000, 24)):
        out.append(H.short_file("wav", H.short_config(spec.KIND_PCM, rate, depth), True))
    for rate in (2822400, 11289600):
        out.append(H.short_file("dop", H.short_config(spec.KIND_DSD_DOP, rate, 1), True))
    sweep = H.short_config(spec.KIND_SWEEP, 48000, 24)
    out.append(H.short_file("wav", sweep, True))
    out.append(fileset.negative(H.short_file("wav", H.short_config(spec.KIND_PCM, 48000, 24), True),
                                "incomplete"))
    return out


def wav_samples(path):
    with open(path, "rb") as f:
        image = f.read()
    nch, rate, bits = containers.wav_format(image)
    raw = np.frombuffer(containers.wav_data(image), dtype=np.uint8).reshape(-1, bits // 8)
    le = np.zeros((len(raw), 4), dtype=np.uint8)
    le[:, 4 - bits // 8:] = raw
    return nch, rate, bits, (le.view("<i4").reshape(-1, nch) >> (32 - bits)).astype(np.int32)


class Extended80(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(containers.ieee_extended(44100).hex(), "400eac44000000000000")
        self.assertEqual(containers.ieee_extended(48000).hex(), "400ebb80000000000000")
        self.assertEqual(containers.ieee_extended(1).hex(), "3fff8000000000000000")

    def test_every_rate(self):
        for rate in spec.PCM_RATES:
            with self.subTest(rate=rate):
                b = containers.ieee_extended(rate)
                exp, mant = struct.unpack(">HQ", b)
                self.assertEqual(len(b), 10)
                self.assertEqual(mant >> 63, 1)                    # explicit integer bit
                self.assertEqual(mant >> (63 - (exp - 16383)), rate)
                self.assertEqual(mant & ((1 << (63 - (exp - 16383))) - 1), 0)
                self.assertEqual(containers.parse_ieee_extended(b), rate)


class Encodings(unittest.TestCase):
    """Write a short set with every encoding, twice; decode and compare."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.TempDir()
        cls.dirs = [cls.tmp.__enter__() + "/1", None]
        cls.dirs[1] = cls.dirs[0][:-2] + "/2"
        cls.files = short_files()
        cls.res = {}
        for d in cls.dirs:
            for f in cls.files:
                cls.res[d, f.name] = fileset.job((f, d, False, True, ALAC))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def path(self, d, f, ext):
        return os.path.join(d, *f.relpath[:-4].split("/")) + ext

    def alac(self, f):
        return ALAC and "alac" in f.formats

    def test_written(self):
        for f in self.files:
            names = [r[0] for r in self.res[self.dirs[0], f.name]]
            want = [f.relpath, f.relpath[:-4] + ".aiff"] + ([f.relpath[:-4] + ".m4a"] if self.alac(f) else [])
            self.assertEqual(names, want)
            # FLAC, AIFF and ALAC at every rate
            self.assertEqual(f.formats, ("flac", "aiff", "alac"))

    def test_aiff_samples_and_tags(self):
        d = self.dirs[0]
        for f in self.files:
            with self.subTest(f.name):
                nch, rate, bits, want = wav_samples(self.path(d, f, ".wav"))
                anch, arate, abits, chunks, got = containers.read_aiff(self.path(d, f, ".aiff"))
                self.assertEqual((anch, arate, abits), (nch, rate, bits))
                self.assertTrue(np.array_equal(got, want))
                self.assertEqual(chunks[b"NAME"].decode("ascii"), f.title)
                self.assertEqual(chunks[b"ANNO"].decode("ascii"), f.comment)
                with open(self.path(d, f, ".aiff"), "rb") as fh:
                    head = fh.read(12)
                self.assertEqual(struct.unpack(">I", head[4:8])[0] + 8,
                                 os.path.getsize(self.path(d, f, ".aiff")))

    @unittest.skipUnless(ALAC, "Apple's ALAC codec not usable")
    def test_alac_samples_and_tags(self):
        """Apple's decoder gives the WAV's samples; the MP4 holds the cookie
        of the WAV's format, the packet table and the two tags."""
        d = self.dirs[0]
        for f in filter(self.alac, self.files):
            with self.subTest(f.name):
                nch, rate, bits, _ = wav_samples(self.path(d, f, ".wav"))
                m4a = self.path(d, f, ".m4a")
                self.assertEqual(containers.decode_alac_pcm(m4a),
                                 containers.wav_pcm_data(self.path(d, f, ".wav")))
                track = containers.read_alac_track(m4a)
                cookie = alac.parse_cookie(track.cookie)
                self.assertEqual((cookie["sample_rate"], cookie["num_channels"], cookie["bit_depth"]),
                                 (rate, nch, bits))
                self.assertEqual(cookie["max_frame_bytes"], max(s for _, s in track.packets))
                self.assertEqual(track.tags, {b"\xa9nam": f.title, b"\xa9too": containers.ALAC_TOOL,
                                              b"\xa9cmt": f.comment})

    @unittest.skipUnless(ALAC and shutil.which("ffprobe"), "Apple's ALAC codec or ffprobe missing")
    def test_alac_container_with_ffprobe(self):
        """ffprobe, an independent MP4 parser, reads the stream and the tags
        (ffmpeg's ALAC decoder is not used: it mis-decodes some packets)."""
        import json, subprocess
        d = self.dirs[0]
        for f in filter(self.alac, self.files):
            with self.subTest(f.name):
                _, rate, bits, _ = wav_samples(self.path(d, f, ".wav"))
                m4a = self.path(d, f, ".m4a")
                probe = json.loads(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries",
                     "stream=codec_name,sample_rate,bits_per_raw_sample:format_tags",
                     "-of", "json", m4a], capture_output=True, text=True, check=True).stdout)
                st = probe["streams"][0]
                self.assertEqual((st["codec_name"], int(st["sample_rate"]), int(st["bits_per_raw_sample"])),
                                 ("alac", rate, bits))
                tags = probe["format"]["tags"]
                self.assertEqual((tags.pop("title"), tags.pop("comment")), (f.title, f.comment))
                # nothing else but what the MP4 muxer writes: the WAV's tags are not copied
                self.assertEqual(set(tags), {"major_brand", "minor_version", "compatible_brands",
                                             "encoder"})
                self.assertEqual(tags["encoder"], containers.ALAC_TOOL)

    def test_checker_reads_them(self):
        """The checker reads AIFF and ALAC to the same stream and verdict as the WAV."""
        from checker import readers, verify
        d = self.dirs[0]
        for f in self.files:
            exts = [".aiff"] + ([".m4a"] if self.alac(f) else [])
            wav = readers.read_file(self.path(d, f, ".wav"))
            want = [(r["verdict"], r["diff"], r["kind"], r["bits"], r["end"], r["rate"])
                    for r in verify.verify_stream(wav)]
            if f.fault:
                self.assertEqual(want[0][0], "NOT_BIT_PERFECT")
                self.assertIsNotNone(want[0][1])
            else:
                self.assertEqual(want[0][0], "BIT_PERFECT")
            for ext in exts:
                with self.subTest(f.name, ext=ext):
                    st = readers.read_file(self.path(d, f, ext))
                    self.assertEqual((st.rate, st.nch, st.slot_bits, st.container),
                                     (wav.rate, wav.nch, wav.slot_bits, {".aiff": "AIFF", ".m4a": "ALAC"}[ext]))
                    self.assertTrue(np.array_equal(st.data, wav.data))
                    got = [(r["verdict"], r["diff"], r["kind"], r["bits"], r["end"], r["rate"])
                           for r in verify.verify_stream(st)]
                    self.assertEqual(got, want)

    @unittest.skipUnless(ALAC, "Apple's ALAC codec not usable")
    def test_alac_needs_a_compiler(self):
        from unittest import mock
        from checker import readers
        f = self.files[0]
        with mock.patch("shutil.which", return_value=None), mock.patch.dict(os.environ, {"CXX": ""}):
            with self.assertRaisesRegex(RuntimeError, "C\\+\\+ compiler"):
                readers.read_file(self.path(self.dirs[0], f, ".m4a"))

    def test_deterministic(self):
        for f in self.files:
            with self.subTest(f.name):
                a = [(n, s, h) for n, s, h in self.res[self.dirs[0], f.name]]
                b = [(n, s, h) for n, s, h in self.res[self.dirs[1], f.name]]
                self.assertEqual(a, b)


@unittest.skipUnless(ALAC, "Apple's ALAC codec not usable")
class StepCase(unittest.TestCase):
    """The minimal case on which ffmpeg's ALAC encoder and Apple's decoder
    disagree (tests/test_alac.py): its .m4a reads back exactly in the checker,
    and would not if the generator encoded with ffmpeg."""

    def test_step_case_m4a(self):
        from checker import readers
        from tests.test_alac import step_case
        pcm = step_case()
        with H.TempDir() as d:
            wav, m4a = os.path.join(d, "step.wav"), os.path.join(d, "step.m4a")
            samples = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2).astype(np.int32)
            containers.write_wav(wav, 44100, 2, 16, 16, len(samples), b"", [samples])
            containers.encode_alac(wav, m4a, "step", "step case")
            st = readers.read_file(m4a)
            self.assertEqual((st.rate, st.nch, st.slot_bits, st.container), (44100, 2, 16, "ALAC"))
            self.assertTrue(np.array_equal(st.data, samples << 16))


READ_TRACK = """
import resource, sys
try:
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
except (ValueError, OSError):
    pass
from generator import containers
try:
    containers.read_alac_track(sys.argv[1])
except ValueError as e:
    print("ValueError:", e)
"""


@unittest.skipUnless(ALAC, "Apple's ALAC codec not usable")
class Mp4Reader(unittest.TestCase):
    """The MP4 reader that the checker uses (containers.read_alac_track) on
    damaged and unusual files: each gives a clear ValueError, or is read,
    never a hang, an unbounded allocation or another exception. The cases
    edit a file in place, so that every size and offset else stays valid."""

    COMMENT = "COVRCOVR"

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.TempDir()
        d = cls.tmp.__enter__()
        cls.wav, cls.m4a = os.path.join(d, "t.wav"), os.path.join(d, "t.m4a")
        rng = np.random.default_rng(5)
        cls.samples = rng.integers(-30000, 30000, size=(2 * 4096 + 100, 2)).astype(np.int32)
        containers.write_wav(cls.wav, 44100, 2, 16, 16, len(cls.samples), b"", [cls.samples])
        containers.encode_alac(cls.wav, cls.m4a, "reader test", cls.COMMENT)
        with open(cls.m4a, "rb") as f:
            cls.image = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def variant(self, image):
        path = os.path.join(os.path.dirname(self.m4a), "v.m4a")
        with open(path, "wb") as f:
            f.write(image)
        return path

    def replace(self, old, new):
        self.assertEqual(self.image.count(old), 1)
        self.assertEqual(len(old), len(new))
        return self.image.replace(old, new)

    def read_isolated(self, image):
        """read_alac_track in a child process with a time and a memory limit:
        what it printed (the ValueError's text) or the exception's last line."""
        import subprocess
        import sys
        top = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "-c", READ_TRACK, self.variant(image)], cwd=top,
                           capture_output=True, text=True, timeout=60)
        return (r.stdout + r.stderr).strip().split("\n")[-1]

    def test_cover_art(self):
        """A covr item (binary data, type 13: JPEG) is skipped; the text tags
        stay and the checker reads the samples."""
        from checker import readers
        image = self.replace(b"\xa9cmt", b"covr")
        old = struct.pack(">II", 1, 0) + self.COMMENT.encode()
        image = image.replace(old, struct.pack(">II", 13, 0) + b"\xff\xd8\xff\xe0\x00\x10JF")
        track = containers.read_alac_track(self.variant(image))
        self.assertEqual(track.tags, {b"\xa9nam": "reader test", b"\xa9too": containers.ALAC_TOOL})
        st = readers.read_file(self.variant(image))
        self.assertTrue(np.array_equal(st.data, self.samples << 16))

    def test_box_size_64_zero(self):
        """A top-level box with size 1 and a 64-bit size 0 ends the read with
        an error instead of looping."""
        out = self.read_isolated(self.image + struct.pack(">I4sQ", 1, b"free", 0))
        self.assertEqual(out, "ValueError: MP4: bad size of box b'free'")

    def test_box_beyond_the_file(self):
        out = self.read_isolated(self.image + struct.pack(">I4s", 100, b"free"))
        self.assertEqual(out, "ValueError: MP4: bad size of box b'free'")
        out = self.read_isolated(self.image + struct.pack(">I4sI", 1, b"free", 0))
        self.assertEqual(out, "ValueError: MP4: truncated box header")

    def test_constant_size_count(self):
        """A constant packet size with a count that the file cannot hold is
        refused before any table is built."""
        i = self.image.index(b"stsz")
        stsz = struct.pack(">4sIII", b"stsz", 0, 1, 0xFFFFFFFF)
        out = self.read_isolated(self.image[:i] + stsz + self.image[i + len(stsz):])
        self.assertEqual(out, "ValueError: MP4: stsz packet count exceeds the file")

    def test_tables_shorter_than_their_counts(self):
        for box, at in ((b"stsz", 12), (b"stco", 8), (b"stsc", 8)):
            with self.subTest(box.decode()):
                i = self.image.index(box) + at
                image = self.image[:i] + struct.pack(">I", 0x10000) + self.image[i + 4:]
                out = self.read_isolated(image)
                self.assertEqual(out, f"ValueError: MP4: {box.decode()} box shorter than its "
                                      "entry count")

    def test_missing_boxes(self):
        """A missing box gives a ValueError that names it, and the checker an
        error line with exit status 2."""
        import contextlib
        import io
        from checker import __main__ as cli
        for box in (b"moov", b"mdia", b"minf", b"stbl", b"stsd", b"stsz", b"stsc", b"stco"):
            with self.subTest(box.decode()):
                path = self.variant(self.replace(box, b"free"))
                with self.assertRaisesRegex(ValueError, f"MP4: no {box.decode()}"):
                    containers.read_alac_track(path)
                err = io.StringIO()
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main([path]), 2)
                self.assertIn(f"error: MP4: no {box.decode()}", err.getvalue())

    def test_truncated_file(self):
        """A file cut inside mdat: mdat is longer than the file, or with its
        size cut too, the last packets lie beyond the end."""
        with self.assertRaisesRegex(ValueError, "bad size of box b'mdat'"):
            containers.read_alac_track(self.variant(self.image[:-100]))
        i = self.image.index(b"mdat") - 4
        size = struct.unpack_from(">I", self.image, i)[0]
        image = self.image[:i] + struct.pack(">I", size - 100) + self.image[i + 4:-100]
        with self.assertRaisesRegex(ValueError, "packet beyond the end of the file"):
            containers.read_alac_track(self.variant(image))


class Packages(unittest.TestCase):
    def test_archives(self):
        names = [p.filename for p in P.RELEASE]
        self.assertEqual(names, ["bpc1-r1-wav.zip", "bpc1-r1-flac.zip", "bpc1-r1-aiff.zip",
                                 "bpc1-r1-alac.zip"])
        self.assertEqual([p.filename for p in P.SWEEP_RELEASE],
                         ["bpc1-r1-sweep-wav.zip", "bpc1-r1-sweep-flac.zip",
                          "bpc1-r1-sweep-aiff.zip", "bpc1-r1-sweep-alac.zip"])
        flac = set(P.members(P.FLAC))
        for pkg, ext in ((P.AIFF, ".aiff"), (P.ALAC, ".m4a"), (P.SWEEP_AIFF, ".aiff"),
                         (P.SWEEP_ALAC, ".m4a")):
            with self.subTest(pkg.name):
                import zipfile
                m = P.members(pkg)
                self.assertTrue(all(n.endswith(ext) for n in m))
                # ALAC: the FLAC files' names; AIFF: the WAV files' names but DSF and DFF
                base = (set(P.members(P.SWEEP_WAV if pkg.sweep else P.WAV)) if ext == ".aiff"
                        else {n[:-5] + ".wav" for n in P.members(P.SWEEP_FLAC if pkg.sweep else P.FLAC)})
                base = {n for n in base if n.endswith(".wav") and "_32bit" not in n}
                self.assertEqual({n[:-len(ext)] + ".wav" for n in m}, base)
                self.assertEqual(pkg.method, zipfile.ZIP_STORED)
                text = P.readme(pkg).decode("ascii")
                self.assertIn("Clipping and level indicators", text)
                self.assertIn(P.NEGATIVE.filename, " ".join(text.split()))
        self.assertEqual(len(flac), 23)
        self.assertEqual([len(P.members(p)) for p in P.RELEASE], [29, 23, 23, 23])
        self.assertEqual([len(P.members(p)) for p in P.SWEEP_RELEASE], [20, 20, 20, 20])
        neg = P.members(P.NEGATIVE)                     # 8 per format, in its folder
        for d, ext in (("aiff", ".aiff"), ("alac", ".m4a")):
            self.assertEqual(sum(n.startswith(d + "/") and n.endswith(ext) for n in neg), 8)


if __name__ == "__main__":
    unittest.main()
