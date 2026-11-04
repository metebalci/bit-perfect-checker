"""Repeatability: a second generation gives byte-identical files.

The default test regenerates one file of every container (and FLAC) twice
through the command line. Set BPC1_FULL_SET=1 to regenerate the whole set,
with the 32-bit PCM files, twice, package both and compare every byte of
the files and the zips.
"""

import filecmp
import os
import subprocess
import sys
import unittest
import zipfile

from generator import containers

from tests import helpers as H

ROOT = os.path.join(os.path.dirname(__file__), "..")
SUBSET = ["044k1_16bit", "096k_32bit", "768k_24bit", "dsd064_dsf",
          "dsd128_dff", "dsd256_dop", "352k8_24bit", "044k1_24bit_altered",
          "dop-176k4_24bit_incomplete"]


def tree(top):
    """Every file under top, as "/"-separated relative paths."""
    return sorted(os.path.relpath(os.path.join(d, n), top).replace(os.sep, "/")
                  for d, _, names in os.walk(top) for n in names)


def generate(out, args):
    subprocess.run([sys.executable, "-m", "generator", "--out", out, "--jobs", "4"] + args,
                   cwd=ROOT, check=True, capture_output=True)


class Repeatability(unittest.TestCase):
    def _twice(self, args):
        with H.TempDir() as a, H.TempDir() as b:
            generate(a, args)
            generate(b, args)
            names = tree(a)
            self.assertEqual(names, tree(b))
            self.assertTrue(names)
            for n in names:
                self.assertTrue(filecmp.cmp(os.path.join(a, n), os.path.join(b, n), shallow=False), n)
            return names

    def test_subset_twice(self):
        args = ["--32bit"] + sum((["--only", s] for s in SUBSET), [])
        names = self._twice(args)
        exts = {os.path.splitext(n)[1] for n in names}
        self.assertTrue({".wav", ".dsf", ".dff"} <= exts)
        self.assertIn("negative/bpc1_pcm_044k1_24bit_altered.wav", names)
        self.assertIn(".aiff", exts)
        if containers.flac_usable():
            self.assertIn(".flac", exts)
        if containers.alac_usable():
            self.assertIn(".m4a", exts)
        # FLAC, AIFF and ALAC at every rate
        for base in ("bpc1_pcm_768k_24bit", "bpc1_dsd256_dop-705k6_24bit"):
            self.assertIn(base + ".aiff", names)
            if containers.flac_usable():
                self.assertIn(base + ".flac", names)
            if containers.alac_usable():
                self.assertIn(base + ".m4a", names)

    @unittest.skipUnless(os.environ.get("BPC1_FULL_SET"), "set BPC1_FULL_SET=1 for the full set")
    def test_full_set_twice(self):
        """Generate everything twice, package both, compare every byte."""
        with H.TempDir() as a, H.TempDir() as b:
            for d in (a, b):
                generate(os.path.join(d, "out"), ["--32bit"])
                subprocess.run([sys.executable, "-m", "generator", "package",
                                "--out", os.path.join(d, "out"), "--dist", os.path.join(d, "dist")],
                               cwd=ROOT, check=True, capture_output=True)
            for sub in ("out", "dist"):
                names = tree(os.path.join(a, sub))
                self.assertEqual(names, tree(os.path.join(b, sub)))
                self.assertIn("SHA256SUMS", names)
                for n in names:
                    self.assertTrue(filecmp.cmp(os.path.join(a, sub, n), os.path.join(b, sub, n),
                                                shallow=False), n)
            self.assertEqual(sorted(os.listdir(os.path.join(a, "dist"))),
                             ["SHA256SUMS", "bpc1-r1-aiff.zip", "bpc1-r1-alac.zip",
                              "bpc1-r1-flac.zip", "bpc1-r1-wav.zip"])
            for name, ext, count in (("bpc1-r1-wav", ".wav", 23), ("bpc1-r1-flac", ".flac", 23),
                                     ("bpc1-r1-aiff", ".aiff", 23), ("bpc1-r1-alac", ".m4a", 23)):
                with zipfile.ZipFile(os.path.join(a, "dist", name + ".zip")) as zf:
                    neg = [n for n in zf.namelist() if "negative" in n or "_altered" in n
                           or "_incomplete" in n]
                    audio = [n for n in zf.namelist() if n.endswith(ext)]
                    self.assertIsNone(zf.testzip())
                self.assertEqual(neg, [])               # in bpc1-r1-negative.zip
                self.assertEqual(len(audio), count, name)   # the WAV zip: and 3 DSF, 3 DFF
                self.assertEqual(sum("705k6" in n or "768k" in n for n in audio), 5, name)


if __name__ == "__main__":
    unittest.main()
