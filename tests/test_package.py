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
# the files of bpc1-r1-negative.zip, in each of its folders (sections 11.5 and 11.6)
NEGATIVE_NAMES = ("bpc1_dsd064_dop-176k4_24bit_altered", "bpc1_dsd064_dop-176k4_24bit_incomplete",
                  "bpc1_pcm_044k1_16bit_sweep_altered", "bpc1_pcm_044k1_24bit_altered",
                  "bpc1_pcm_044k1_24bit_incomplete", "bpc1_pcm_044k1_24bit_sweep_altered",
                  "bpc1_pcm_048k_24bit_altered", "bpc1_pcm_048k_24bit_incomplete")
NEGATIVE_FOLDERS = {"wav": ".wav", "flac": ".flac", "aiff": ".aiff", "alac": ".m4a"}


class Members(unittest.TestCase):
    def test_release_members(self):
        files = fileset.test_set()
        wav, flac = set(P.members(P.WAV)), set(P.members(P.FLAC))
        self.assertEqual(wav, {f.relpath for f in files if not f.fault})
        self.assertEqual(flac, {f.relpath[:-4] + ".flac" for f in files
                                if "flac" in f.formats and not f.fault})
        self.assertEqual((len(wav), len(flac)), (29, 23))   # FLAC: 20 PCM, 3 DoP
        # FLAC and ALAC at every rate: 705.6 and 768 kHz PCM, DSD256 DoP
        alac = set(P.members(P.ALAC))
        for t in ("pcm_705k6_16bit", "pcm_705k6_24bit", "pcm_768k_16bit", "pcm_768k_24bit",
                  "dsd256_dop-705k6_24bit"):
            self.assertIn(f"bpc1_{t}.flac", flac)
            self.assertIn(f"bpc1_{t}.m4a", alac)
        self.assertEqual(len(alac), 23)
        self.assertEqual(len(P.members(P.AIFF)), 23)        # AIFF: 20 PCM, 3 DoP
        self.assertFalse([n for n in wav | flac if "soak" in n])
        self.assertFalse(any(n.endswith(".flac") for n in wav))
        self.assertTrue(all(n.endswith(".flac") for n in flac))
        self.assertEqual(sum("_dop-" in n for n in flac), 3)
        self.assertFalse(any("_32bit" in n or "TEST" in n for n in wav | flac))
        self.assertEqual(P.RELEASE, (P.WAV, P.FLAC, P.AIFF, P.ALAC))

    def test_no_negative_in_format_zips(self):
        """The format and sweep zips hold only files that should pass."""
        for pkg in P.RELEASE + P.SWEEP_RELEASE:
            with self.subTest(pkg.name):
                names = P.members(pkg)
                self.assertTrue(names)
                self.assertFalse([n for n in names if "/" in n or "negative" in n
                                  or "_altered" in n or "_incomplete" in n])
                self.assertEqual(len(names), 20 if pkg.sweep else 29 if pkg.files == "wav" else 23)

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
                self.assertNotIn("negative/", text)
                self.assertIn("loop one file gaplessly", text.replace("\n", " "))
                self.assertIn("Every repetition gets its own result.", text.replace("\n", " "))
                self.assertNotIn("soak", text.lower())
                self.assertIn("BPC1 format of the Bit-Perfect Checker project", " ".join(text.split()))
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
        for pkg in P.RELEASE + P.SWEEP_RELEASE:          # the negatives: in their own zip
            text = " ".join(P.readme(pkg).decode("ascii").split())
            self.assertNotIn("negative/", text, pkg.name)
            self.assertNotIn("(6 files", text, pkg.name)
            self.assertNotIn("(2 files", text, pkg.name)
            self.assertIn("bpc1-r1-negative.zip", text, pkg.name)

    def test_negative_readme(self):
        text = P.readme(P.NEGATIVE).decode("ascii")
        self.assertNotIn("\n", text.replace("\r\n", ""))
        text = text.replace("\r\n", "\n")
        flat = " ".join(text.split())
        self.assertTrue(text.startswith("!!! FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS OR "
                                        "HEADPHONES !!!"))
        self.assertIn("TEST SIGNALS - DO NOT PLAY ON SPEAKERS OR HEADPHONES", flat)
        self.assertIn("deliberately faulty", flat)
        for name in NEGATIVE_NAMES:
            self.assertIn(f"  {name}\n", text)
        self.assertEqual(text.count("-> NOT_BIT_PERFECT"), 8)
        self.assertIn("must not check as BIT_PERFECT", flat)
        self.assertIn("wav/ flac/ aiff/ alac/", text)
        self.assertIn("sections 11.5 and 11.6 of docs/bpc1.md", flat)
        self.assertIn("reject altered and incomplete files", flat)
        self.assertIn("python3 -m checker FILE", text)
        self.assertIn("Clipping and level indicators", text)
        self.assertIn("Turn off ReplayGain, Sound Check, volume normalization, limiters "
                      'and any "prevent clipping" option.', flat)
        with open(LICENSE) as f:
            self.assertIn(f.read().strip(), text)
        self.assertNotIn("ALTERED", text.replace("_altered", ""))
        self.assertNotIn("INCOMPLETE", text.replace("_incomplete", ""))
        self.assertNotIn("soak", text.lower())
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

    def test_cli_sums_after_failed_run(self):
        """A run in which one zip fails still rewrites SHA256SUMS from the zips in dist."""
        out = os.path.join(self.dir, "out-changed")
        shutil.copytree(self.out, out)
        code, _ = self._package("dist-changed")
        self.assertEqual(code, 0)
        dist = os.path.join(self.dir, "dist-changed")
        sizes = {p.filename: os.path.getsize(os.path.join(dist, p.filename)) for p in P.RELEASE}
        limit = max(sizes.values())
        over = {n for n, s in sizes.items() if s >= limit}
        # change a file of a zip that is still written, keeping its size
        pkg = next(p for p in P.RELEASE if p.filename not in over and p.files != "wav")
        name = next(n for n in P.members(pkg) if n.endswith(P.EXT[pkg.files]))
        data = name.upper().encode("ascii")
        with open(os.path.join(out, *name.split("/")), "wb") as f:
            f.write(data)
        sums = P.read_sums(out)
        sums[name] = hashlib.sha256(data).hexdigest()
        with open(os.path.join(out, "SHA256SUMS"), "w") as f:
            f.writelines(f"{d}  {n}\n" for n, d in sorted(sums.items()))
        before = P.sha256_file(os.path.join(dist, pkg.filename))
        for n in over:                                      # gone from dist: not listed
            os.remove(os.path.join(dist, n))
        err = StringIO()
        with mock_limit(limit), redirect_stdout(StringIO()), redirect_stderr(err):
            code = cli.main(["package", "--out", out, "--dist", dist])
        self.assertNotEqual(code, 0)
        self.assertTrue(all(n + ": error:" in err.getvalue() for n in over), err.getvalue())
        self.assertNotEqual(P.sha256_file(os.path.join(dist, pkg.filename)), before)
        zips = sorted(n for n in os.listdir(dist) if n.endswith(".zip"))
        self.assertEqual(P.read_sums(dist), {n: P.sha256_file(os.path.join(dist, n))
                                             for n in zips})


class NegativeArchive(unittest.TestCase):
    """bpc1-r1-negative.zip: every negative test file of the regular and the
    sweep set, one folder per format, built from both generated sets."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = H.TempDir()
        cls.dir = cls.tmp.__enter__()
        # stand-ins for out/ and out-sweep/: every file of each set, a few bytes each
        cls.out, cls.sweep = os.path.join(cls.dir, "out"), os.path.join(cls.dir, "out-sweep")
        for top, files in ((cls.out, fileset.test_set()), (cls.sweep, fileset.sweep_set())):
            os.makedirs(os.path.join(top, fileset.NEGATIVE_DIR))
            names = sorted(n for f in files for n in [f.relpath] + [f.relpath_as(x) for x in f.formats])
            with open(os.path.join(top, "SHA256SUMS"), "w") as sums:
                for n in names:
                    with open(os.path.join(top, *n.split("/")), "wb") as f:
                        f.write(n.encode("ascii"))
                    sums.write(f"{hashlib.sha256(n.encode('ascii')).hexdigest()}  {n}\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def _package(self, dist, *args, limit=None):
        out, err = StringIO(), StringIO()
        with mock_limit(limit or P.ASSET_LIMIT), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["package", "--negative", "--dist", os.path.join(self.dir, dist)]
                            + list(args or ["--out", self.out, "--out-sweep", self.sweep]))
        return code, out.getvalue(), err.getvalue()

    def test_package(self):
        self.assertEqual(P.NEGATIVE.filename, "bpc1-r1-negative.zip")
        self.assertEqual(P.NEGATIVE.method, zipfile.ZIP_STORED)
        self.assertNotIn(P.NEGATIVE, P.RELEASE + P.SWEEP_RELEASE)
        want = sorted(f"{d}/{n}{e}" for d, e in NEGATIVE_FOLDERS.items() for n in NEGATIVE_NAMES)
        self.assertEqual(P.members(P.NEGATIVE), want)
        self.assertEqual(len(want), 32)

    def test_zip(self):
        code, out, err = self._package("dist")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("bpc1-r1-negative.zip", out)
        path = os.path.join(self.dir, "dist", "bpc1-r1-negative.zip")
        top = "bpc1-r1-negative/"
        files = sorted(f"{d}/{n}{e}" for d, e in NEGATIVE_FOLDERS.items() for n in NEGATIVE_NAMES)
        with zipfile.ZipFile(path) as zf:
            self.assertIsNone(zf.testzip())
            infos = zf.infolist()
            got = [i.filename for i in infos]
            self.assertEqual(got, sorted(got))
            self.assertEqual(got[:6], [top, top + P.SWEEP_WARNING_FILE, top + P.WARNING_FILE,
                                       top + "README.txt", top + "SHA256SUMS", top + "aiff/"])
            self.assertEqual(set(got), {top, top + P.SWEEP_WARNING_FILE, top + P.WARNING_FILE,
                                        top + "README.txt", top + "SHA256SUMS"}
                             | {top + d + "/" for d in NEGATIVE_FOLDERS}
                             | {top + n for n in files})
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in infos))
            for i in infos:
                self.assertEqual(i.date_time, P.ZIP_DATE)
                self.assertEqual(i.external_attr >> 16, 0o40755 if i.is_dir() else 0o100644)
            self.assertEqual(zf.read(top + P.WARNING_FILE), b"")
            self.assertEqual(zf.read(top + P.SWEEP_WARNING_FILE), b"")
            self.assertEqual(zf.read(top + "README.txt"), P.readme(P.NEGATIVE))
            sums = zf.read(top + "SHA256SUMS").decode("ascii").splitlines()
            self.assertEqual([line.split("  ", 1)[1] for line in sums], files)
            for line in sums:
                digest, name = line.split("  ", 1)
                self.assertEqual(hashlib.sha256(zf.read(top + name)).hexdigest(), digest, name)
            # each file is the negative file of its set, under its folder
            for name in files:
                src = (self.sweep if "_sweep_" in name else self.out)
                base = name.split("/", 1)[1]
                with open(os.path.join(src, fileset.NEGATIVE_DIR, base), "rb") as f:
                    self.assertEqual(zf.read(top + name), f.read(), name)
        self.assertEqual(P.read_sums(os.path.join(self.dir, "dist")),
                         {"bpc1-r1-negative.zip": P.sha256_file(path)})

    def test_rerun_identical(self):
        for d in ("dist-a", "dist-b"):
            self.assertEqual(self._package(d)[0], 0)
        a, b = (os.path.join(self.dir, d) for d in ("dist-a", "dist-b"))
        self.assertEqual(sorted(os.listdir(a)), ["SHA256SUMS", "bpc1-r1-negative.zip"])
        for n in os.listdir(a):
            with open(os.path.join(a, n), "rb") as fa, open(os.path.join(b, n), "rb") as fb:
                self.assertEqual(fa.read(), fb.read(), n)

    def test_missing_folder(self):
        """A missing input folder, or a file missing in one, fails clearly and writes no zip."""
        nowhere = os.path.join(self.dir, "nowhere")
        for i, (args, what) in enumerate((
                (["--out", self.out, "--out-sweep", nowhere], nowhere),
                (["--out", nowhere, "--out-sweep", self.sweep], nowhere),
                (["--out", self.out, "--out-sweep", self.out], "sweep_altered"))):
            with self.subTest(args=args):
                code, _, err = self._package(f"dist-missing{i}", *args)
                self.assertNotEqual(code, 0)
                self.assertIn("bpc1-r1-negative.zip: error:", err)
                self.assertIn(what, err)
                self.assertFalse(os.path.exists(os.path.join(self.dir, f"dist-missing{i}",
                                                             "bpc1-r1-negative.zip")))
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.main(["package", "--out-sweep", self.sweep, "--dist", os.path.join(self.dir, "x")])

    def test_limit(self):
        code, _, _ = self._package("dist-size")
        size = os.path.getsize(os.path.join(self.dir, "dist-size", "bpc1-r1-negative.zip"))
        code, _, err = self._package("dist-limit", limit=size)
        self.assertNotEqual(code, 0)
        self.assertIn(f"{size:,d}", err)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "dist-limit", "bpc1-r1-negative.zip")))


def mock_limit(limit):
    return mock.patch.object(P, "ASSET_LIMIT", limit)


if __name__ == "__main__":
    unittest.main()
