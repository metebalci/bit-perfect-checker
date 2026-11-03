"""Release archives: members, README, warning file, determinism, contents."""

import hashlib
import os
import shutil
import zipfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

from generator import __main__ as cli, fileset, package as P, spec

from tests import helpers as H

LICENSE = os.path.join(os.path.dirname(__file__), "..", "LICENSE")


class Members(unittest.TestCase):
    def test_release_members(self):
        files = fileset.test_set()
        wav, flac = set(P.members(P.WAV)), set(P.members(P.FLAC))
        self.assertEqual(wav, {f.relpath for f in files})
        self.assertEqual(flac, {f.relpath[:-4] + ".flac" for f in files if "flac" in f.formats})
        self.assertEqual((len(wav), len(flac)), (35, 29))   # FLAC: 20 PCM, 3 DoP, 6 negative
        # FLAC and ALAC at every rate: 705.6 and 768 kHz PCM, DSD256 DoP
        alac = set(P.members(P.ALAC))
        for t in ("pcm_705k6_16bit", "pcm_705k6_24bit", "pcm_768k_16bit", "pcm_768k_24bit",
                  "dsd256_dop-705k6_24bit"):
            self.assertIn(f"bpc1_{t}.flac", flac)
            self.assertIn(f"bpc1_{t}.m4a", alac)
        self.assertEqual(len(alac), 29)
        self.assertEqual(len(P.members(P.AIFF)), 29)        # AIFF: 20 PCM, 3 DoP, 6 negative
        self.assertFalse([n for n in wav | flac if "soak" in n])
        for names in (wav, flac):
            neg = sorted(n for n in names if "/" in n)
            self.assertEqual(len(neg), 6)
            self.assertTrue(all(n.startswith("negative/bpc1_") for n in neg), neg)
            self.assertEqual(sum(n.endswith(("_altered.wav", "_altered.flac")) for n in neg), 3)
        self.assertFalse(any(n.endswith(".flac") for n in wav))
        self.assertTrue(all(n.endswith(".flac") for n in flac))
        self.assertEqual(sum("_dop-" in n for n in flac), 3 + 2)   # 2 negative DoP-FLACs
        self.assertFalse(any("_32bit" in n or "TEST" in n for n in wav | flac))
        self.assertEqual(P.RELEASE, (P.WAV, P.FLAC, P.AIFF, P.ALAC))

    def test_methods(self):
        self.assertEqual(P.WAV.method, zipfile.ZIP_DEFLATED)
        self.assertEqual(P.FLAC.method, zipfile.ZIP_STORED)


class ReleaseName(unittest.TestCase):
    def test_one_place(self):
        self.assertEqual(P.RELEASE_NAME, "bpc1-r1")
        for pkg in P.RELEASE + P.SWEEP_RELEASE:
            self.assertTrue(pkg.name.startswith(P.RELEASE_NAME + "-"), pkg.name)
            self.assertEqual(pkg.filename, pkg.name + ".zip")
            self.assertNotIn("bpc1-v1", P.readme(pkg).decode("ascii"))


class Tags(unittest.TestCase):
    def test_project_name(self):
        from generator import fileset
        self.assertEqual(fileset.ARTIST, "bit-perfect-checker")
        f = fileset.test_set()[0]
        self.assertIn(b"bit-perfect-checker", fileset.info_tags(f))
        self.assertIn(b"bit-perfect-checker", fileset.id3_tag(f))
        self.assertIn(("ARTIST", "bit-perfect-checker"), fileset.flac_tags(f))


class Readme(unittest.TestCase):
    def test_text(self):
        with open(LICENSE) as f:
            licence = f.read().strip()
        for pkg in (P.WAV, P.FLAC):
            with self.subTest(pkg.name):
                b = P.readme(pkg)
                text = b.decode("ascii")                 # plain ASCII
                self.assertNotIn("\n", text.replace("\r\n", ""))
                text = text.replace("\r\n", "\n")
                self.assertTrue(text.startswith("!!! TEST SIGNALS - DO NOT PLAY ON SPEAKERS "
                                                "OR HEADPHONES !!!"))
                self.assertIn("WARNING: TEST SIGNALS - DO NOT PLAY ON SPEAKERS OR HEADPHONES.", text)
                self.assertIn("python3 -m checker FILE", text)
                self.assertIn(licence, text)
                self.assertIn("MIT", text)
                self.assertIn("negative/README.txt", text)
                self.assertIn("loop one file gaplessly", text.replace("\n", " "))
                self.assertIn("Every repetition gets its own result.", text.replace("\n", " "))
                self.assertNotIn("soak", text.lower())
                self.assertIn("BPC1 format of the bit-perfect-checker project", " ".join(text.split()))
                self.assertNotRegex(text, r"bit-perfect-check(?!er)")
                flat = " ".join(text.split())
                self.assertIn("Clipping and level indicators", text)
                self.assertIn("may report clipping, or peaks at 0 dBFS", flat)
                self.assertIn("the checker judges only what arrives", flat)
                self.assertIn("Turn off ReplayGain, Sound Check, volume normalization, limiters "
                              'and any "prevent clipping" option.', flat)
        flac = P.readme(P.FLAC).decode("ascii").replace("\r\n", "\n")
        flat = " ".join(flac.split())
        self.assertIn("DSD64, DSD128 and DSD256 as DoP in 24-bit FLAC", flat)
        self.assertIn("The PCM files at 705.6 and 768 kHz and the DSD256 DoP file, at 705.6 kHz, "
                      "are above the rates of the FLAC subset: they need a FLAC decoder based on "
                      "libFLAC 1.4.0 or later", flat)
        sweep = " ".join(P.readme(P.SWEEP_FLAC).decode("ascii").split())
        self.assertIn("The files at 705.6 and 768 kHz need a FLAC decoder based on libFLAC "
                      "1.4.0 or later", sweep)
        for pkg in P.RELEASE + P.SWEEP_RELEASE:               # every rate in every format
            text = " ".join(P.readme(pkg).decode("ascii").split())
            self.assertIn("705.6 and 768 kHz (20 files)", text, pkg.name)
            self.assertNotIn("(16 files)", text, pkg.name)
            self.assertNotIn("come as WAV and AIFF only", text, pkg.name)
            self.assertEqual("libFLAC" in text, pkg.files == "flac", pkg.name)
        for pkg in (P.ALAC, P.SWEEP_ALAC):
            text = " ".join(P.readme(pkg).decode("ascii").split())
            self.assertIn("refuse ALAC above 384 kHz", text, pkg.name)
            self.assertIn("for players with their own ALAC decoder", text, pkg.name)
            self.assertIn("the WAV and AIFF files of the same name hold the same samples", text,
                          pkg.name)
        self.assertIn("DSD64, DSD128 and DSD256 as DoP in 24-bit ALAC",
                      " ".join(P.readme(P.ALAC).decode("ascii").split()))
        self.assertIn("bpc1-r1-wav.zip", flac)

    def test_negative_readme(self):
        for pkg, ext in ((P.WAV, ".wav"), (P.FLAC, ".flac")):
            with self.subTest(pkg.name):
                b = P.negative_readme(pkg)
                text = b.decode("ascii")
                self.assertNotIn("\n", text.replace("\r\n", ""))
                text = text.replace("\r\n", "\n")
                self.assertTrue(text.startswith("!!! TEST SIGNALS - DO NOT PLAY ON SPEAKERS"))
                self.assertIn("deliberately faulty", text)
                for f in fileset.negative_set():
                    self.assertIn(f"  {f.name}{ext}\n", text)
                self.assertEqual(text.count("-> NOT_BIT_PERFECT"), 6)
                self.assertIn("A checker must report NOT_BIT_PERFECT.", text)
                self.assertNotIn("ALTERED", text.replace("_altered", ""))
                self.assertNotIn("INCOMPLETE", text.replace("_incomplete", ""))
                self.assertLessEqual(max(len(x) for x in text.splitlines()), 79)

    def test_warning_file_name(self):
        n = P.WARNING_FILE
        self.assertTrue(P.valid_name(n))
        self.assertTrue(n.isascii())
        self.assertFalse(set('<>:"/\\|?*') & set(n))
        self.assertIn("DO NOT PLAY ON SPEAKERS OR HEADPHONES", n)
        for bad in ("a:b", "a?", 'a"b', "a*", "a<b", "a|b", "a.", "a ", "–"):
            self.assertFalse(P.valid_name(bad), bad)


class ZipFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = H.TempDir()
        cls.dir = cls.tmp.__enter__()
        src = os.path.join(cls.dir, "src")
        os.makedirs(src)
        specs = [H.short_file("wav", H.short_config(spec.KIND_PCM, 48000, 24)),
                 H.short_file("dsf", H.short_config(spec.KIND_DSD_DSF, 2822400, 1)),
                 H.short_file("dop", H.short_config(spec.KIND_DSD_DOP, 2822400, 1))]
        cls.sources = sorted(fileset.write_file(f, src) for f in specs)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def _zip(self, sources, name, method):
        path = os.path.join(self.dir, name)
        return P.write_zip(path, "bpc1-r1-test", sources, method, P.readme(P.WAV))

    def test_deterministic(self):
        for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
            with self.subTest(method=method):
                a = self._zip(self.sources, f"a{method}.zip", method)
                # same files elsewhere, with other times and permissions
                other = os.path.join(self.dir, f"copy{method}")
                os.makedirs(other, exist_ok=True)
                copies = []
                for s in self.sources:
                    c = os.path.join(other, os.path.basename(s))
                    shutil.copyfile(s, c)
                    os.utime(c, (1e9, 1e9))
                    os.chmod(c, 0o600)
                    copies.append(c)
                b = self._zip(list(reversed(copies)), f"b{method}.zip", method)
                with open(a, "rb") as fa, open(b, "rb") as fb:
                    self.assertEqual(fa.read(), fb.read())

    def test_contents(self):
        path = self._zip(self.sources, "c.zip", zipfile.ZIP_DEFLATED)
        with zipfile.ZipFile(path) as zf:
            self.assertIsNone(zf.testzip())
            infos = zf.infolist()
            names = [i.filename for i in infos]
            self.assertEqual(names, sorted(names))
            self.assertEqual(names[0], "bpc1-r1-test/")
            self.assertEqual(names[1], "bpc1-r1-test/" + P.WARNING_FILE)
            want = {"bpc1-r1-test/" + n for n in
                    [P.WARNING_FILE, "README.txt", "SHA256SUMS"] + [os.path.basename(s) for s in self.sources]}
            self.assertEqual(set(names[1:]), want)
            for i in infos:
                self.assertEqual(i.date_time, P.ZIP_DATE)
                self.assertEqual(i.create_system, 3)
                self.assertEqual(i.comment, b"")
                self.assertEqual(i.extra, b"")
                mode = i.external_attr >> 16
                self.assertEqual(mode, 0o40755 if i.is_dir() else 0o100644, i.filename)
            self.assertEqual(zf.read(names[1]), b"")
            self.assertEqual(zf.read("bpc1-r1-test/README.txt"), P.readme(P.WAV))
            sums = zf.read("bpc1-r1-test/SHA256SUMS").decode("ascii").splitlines()
            self.assertEqual(len(sums), len(self.sources))
            for s, line in zip(self.sources, sums):
                with open(s, "rb") as f:
                    data = f.read()
                n = os.path.basename(s)
                self.assertEqual(line, f"{hashlib.sha256(data).hexdigest()}  {n}")
                self.assertEqual(zf.read("bpc1-r1-test/" + n), data)
                self.assertEqual(zf.getinfo("bpc1-r1-test/" + n).compress_type, zipfile.ZIP_DEFLATED)

    def test_subfolder(self):
        """Files in a subfolder: a folder entry, sorted, in SHA256SUMS with the folder."""
        files = [(os.path.basename(self.sources[0]), self.sources[0]),
                 ("negative/" + os.path.basename(self.sources[1]), self.sources[1])]
        path = P.write_zip(os.path.join(self.dir, "s.zip"), "top", files, zipfile.ZIP_DEFLATED,
                           P.readme(P.WAV), texts={"negative/README.txt": b"x\r\n"})
        with zipfile.ZipFile(path) as zf:
            names = [i.filename for i in zf.infolist()]
            self.assertEqual(names, sorted(names))
            self.assertIn("top/negative/", names)
            self.assertLess(names.index("top/negative/"), names.index("top/negative/README.txt"))
            self.assertTrue(zf.getinfo("top/negative/").is_dir())
            self.assertEqual(zf.getinfo("top/negative/").external_attr >> 16, 0o40755)
            sums = zf.read("top/SHA256SUMS").decode("ascii")
            self.assertIn("  negative/" + os.path.basename(self.sources[1]) + "\n", sums)
            self.assertNotIn("README", sums)
            self.assertEqual(zf.read("top/negative/README.txt"), b"x\r\n")
        with self.assertRaises(ValueError):
            P.write_zip(os.path.join(self.dir, "bad.zip"), "top", [("neg:x/a.wav", self.sources[0])],
                        zipfile.ZIP_STORED, P.readme(P.WAV))

    def test_build_refuses_incomplete_set(self):
        out = os.path.join(self.dir, "incomplete")
        os.makedirs(out, exist_ok=True)
        with self.assertRaises(RuntimeError):
            P.build(P.WAV, out, os.path.join(self.dir, "dist"))      # no SHA256SUMS
        with open(os.path.join(out, "SHA256SUMS"), "w") as f:
            for n in P.members(P.WAV):
                f.write("0" * 64 + "  " + n + "\n")
        with self.assertRaisesRegex(RuntimeError, "missing"):
            P.build(P.WAV, out, os.path.join(self.dir, "dist"))


class AssetLimit(unittest.TestCase):
    """A zip of 2 GiB or more cannot be a GitHub release asset: packaging refuses it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.TempDir()
        cls.dir = cls.tmp.__enter__()
        # a stand-in for out/: every member of the four release zips, a few bytes each
        cls.out = os.path.join(cls.dir, "out")
        os.makedirs(os.path.join(cls.out, fileset.NEGATIVE_DIR))
        with open(os.path.join(cls.out, "SHA256SUMS"), "w") as sums:
            for n in sorted({n for pkg in P.RELEASE for n in P.members(pkg)}):
                with open(os.path.join(cls.out, *n.split("/")), "wb") as f:
                    f.write(n.encode("ascii"))
                sums.write(f"{hashlib.sha256(n.encode('ascii')).hexdigest()}  {n}\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def _package(self, dist, limit=None):
        """Run `python3 -m generator package`, with the limit lowered to `limit`."""
        err = StringIO()
        with mock_limit(limit or P.ASSET_LIMIT), redirect_stdout(StringIO()), \
                redirect_stderr(err):
            code = cli.main(["package", "--out", self.out, "--dist", os.path.join(self.dir, dist)])
        return code, err.getvalue()

    def test_github_limit(self):
        self.assertEqual(P.ASSET_LIMIT, 2 ** 31)                 # "under 2 GiB" per file

    def test_write_zip_boundary(self):
        path = os.path.join(self.dir, "b.zip")
        size = os.path.getsize(P.write_zip(path, "top", [], zipfile.ZIP_STORED, b"r"))
        os.remove(path)
        self.assertEqual(P.write_zip(path, "top", [], zipfile.ZIP_STORED, b"r", limit=size + 1), path)
        self.assertEqual(os.path.getsize(path), size)            # one byte under: written
        os.remove(path)
        with self.assertRaises(RuntimeError) as cm:
            P.write_zip(path, "top", [], zipfile.ZIP_STORED, b"r", limit=size)
        self.assertIn("b.zip", str(cm.exception))
        self.assertIn(f"{size:,d}", str(cm.exception))
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(path + ".part"))
        with mock_limit(size), self.assertRaises(RuntimeError):  # the module constant applies
            P.write_zip(path, "top", [], zipfile.ZIP_STORED, b"r")
        self.assertFalse(os.path.exists(path))

    def test_cli_refuses_oversized_zip(self):
        code, err = self._package("dist-ok")
        self.assertEqual((code, err), (0, ""))
        dist = os.path.join(self.dir, "dist-ok")
        sizes = {p.filename: os.path.getsize(os.path.join(dist, p.filename)) for p in P.RELEASE}
        limit = sizes[P.FLAC.filename]
        over = {n for n, s in sizes.items() if s >= limit}
        self.assertTrue(over and over != set(sizes), sizes)

        code, err = self._package("dist-over", limit)
        self.assertNotEqual(code, 0)
        dist = os.path.join(self.dir, "dist-over")
        sums = P.read_sums(dist) or {}
        for n in sizes:
            with self.subTest(n):
                if n in over:
                    line = [x for x in err.splitlines() if x.startswith(n + ": error:")]
                    self.assertEqual(len(line), 1, err)
                    self.assertIn(f"{sizes[n]:,d}", line[0])
                    self.assertIn(f"{limit:,d}", line[0])
                    self.assertFalse(os.path.exists(os.path.join(dist, n)))
                    self.assertFalse(os.path.exists(os.path.join(dist, n + ".part")))
                    self.assertNotIn(n, sums)
                else:
                    self.assertNotIn(n, err)
                    self.assertEqual(os.path.getsize(os.path.join(dist, n)), sizes[n])

    def test_cli_keeps_earlier_sums_true(self):
        """A refused zip leaves dist as it was: SHA256SUMS lists only true digests."""
        code, _ = self._package("dist-again")
        self.assertEqual(code, 0)
        dist = os.path.join(self.dir, "dist-again")
        before = P.read_sums(dist)
        code, _ = self._package("dist-again", min(os.path.getsize(os.path.join(dist, p.filename))
                                                  for p in P.RELEASE))
        self.assertNotEqual(code, 0)
        self.assertEqual(P.read_sums(dist), before)
        for n, d in before.items():
            self.assertEqual(P.sha256_file(os.path.join(dist, n)), d, n)


def mock_limit(limit):
    return mock.patch.object(P, "ASSET_LIMIT", limit)


if __name__ == "__main__":
    unittest.main()
