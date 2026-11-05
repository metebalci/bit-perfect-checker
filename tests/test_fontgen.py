# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The display font generator (firmware/tools/fontgen.py) against Spleen's
BDF files in firmware/lib/spleen: every glyph of 0x20-0x7E, read back from
the generated C compiled on the host, equals the BDF bitmap; a re-run gives
byte-identical files. Needs the Spleen submodule and, for the glyph check, a
host C compiler; skipped without them. And on a small BDF made here: the
font's notice in fonts.c, and each malformed font stops the generator."""

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTGEN = os.path.join(ROOT, "firmware", "tools", "fontgen.py")
SPLEEN = os.path.join(ROOT, "firmware", "lib", "spleen")
FONTS = {"8x16": (8, 16), "16x32": (16, 32), "32x64": (32, 64)}
CC = shutil.which("gcc") or shutil.which("cc")

DUMP_C = r"""
#include <stdio.h>
#include "fonts.h"

static void dump(const char *name, const font_t *f) {
  printf("font %s %u %u %u %u %u\n", name, f->width, f->height, f->first, f->count, f->glyph_bytes);
  for (int c = 0; c < 256; c++) {
    const uint8_t *g = font_glyph(f, (char) c);
    printf("%d", c);
    for (unsigned i = 0; i < f->glyph_bytes; i++) {
      printf(" %02x", g[i]);
    }
    printf("\n");
  }
}

int main(void) {
  dump("8x16", &font_8x16);
  dump("16x32", &font_16x32);
  dump("32x64", &font_32x64);
  return 0;
}
"""


def bdf_glyphs(path):
    """The test's own reading of a BDF file: encoding -> bytes of its BITMAP
    rows, as written in the file."""
    glyphs = {}
    encoding = None
    in_bitmap = False
    rows = []
    with open(path, encoding="ascii") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ENCODING "):
                encoding = int(line.split()[1])
            elif line == "BITMAP":
                in_bitmap, rows = True, []
            elif line == "ENDCHAR":
                glyphs[encoding] = bytes.fromhex("".join(rows))
                in_bitmap = False
            elif in_bitmap:
                rows.append(line)
    return glyphs


def run_fontgen(out):
    args = [f"{name}={os.path.join(SPLEEN, 'spleen-' + name + '.bdf')}" for name in FONTS]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-I", FONTGEN, "--out", out] + args, check=True, env=env)


@unittest.skipUnless(os.path.exists(os.path.join(SPLEEN, "spleen-8x16.bdf")),
                     "Spleen submodule not checked out")
class FontGen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "a")
        run_fontgen(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rerun_is_byte_identical(self):
        again = os.path.join(self.tmp.name, "b")
        run_fontgen(again)
        for name in ("fonts.h", "fonts.c"):
            with open(os.path.join(self.out, name), "rb") as a, open(os.path.join(again, name), "rb") as b:
                self.assertEqual(a.read(), b.read(), name)

    def test_output_has_no_local_paths(self):
        for name in ("fonts.h", "fonts.c"):
            with open(os.path.join(self.out, name), encoding="ascii") as f:
                text = f.read()
            self.assertNotIn(ROOT, text, name)
            self.assertNotIn(self.tmp.name, text, name)

    @unittest.skipUnless(CC, "no host C compiler")
    def test_glyphs_match_bdf(self):
        dump_c = os.path.join(self.tmp.name, "dump.c")
        exe = os.path.join(self.tmp.name, "dump")
        with open(dump_c, "w") as f:
            f.write(DUMP_C)
        subprocess.run([CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Wcast-qual", "-Wsign-compare",
                        "-Werror", "-I", self.out, "-o", exe, dump_c,
                        os.path.join(self.out, "fonts.c")], check=True)
        out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout.splitlines()

        dumped = {}
        for line in out:
            words = line.split()
            if words[0] == "font":
                name = words[1]
                dumped[name] = {"header": tuple(int(w) for w in words[2:]), "glyphs": {}}
            else:
                dumped[name]["glyphs"][int(words[0])] = bytes(int(w, 16) for w in words[1:])

        self.assertEqual(sorted(dumped), sorted(FONTS))
        for name, (width, height) in FONTS.items():
            bdf = bdf_glyphs(os.path.join(SPLEEN, f"spleen-{name}.bdf"))
            per_glyph = (width + 7) // 8 * height
            self.assertEqual(dumped[name]["header"], (width, height, 0x20, 95, per_glyph), name)
            glyphs = dumped[name]["glyphs"]
            for c in range(256):
                with self.subTest(font=name, char=c):
                    expected = bdf[c] if 0x20 <= c <= 0x7E else bdf[ord("?")]
                    self.assertEqual(len(expected), per_glyph)
                    self.assertEqual(glyphs[c], expected)

    def test_flash_size(self):
        # 95 glyphs of 16, 64 and 256 bytes: the arrays' declared sizes
        with open(os.path.join(self.out, "fonts.c"), encoding="ascii") as f:
            text = f.read()
        self.assertIn("font_8x16_bits[1520]", text)
        self.assertIn("font_16x32_bits[6080]", text)
        self.assertIn("font_32x64_bits[24320]", text)


_spec = importlib.util.spec_from_file_location("fontgen", FONTGEN)
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)


def small_bdf(glyphs=range(0x20, 0x7F), bbx="8 2 0 0", rows=("FF", "81"), head="FONTBOUNDINGBOX 8 2 0 0\n"):
    """A BDF font of 8x2 glyphs, with a copyright comment"""
    out = ["STARTFONT 2.1\n", "COMMENT /*\n", "COMMENT * Copyright (c) 2026 Example Author\n",
           "COMMENT * SPDX-License-Identifier: BSD-2-Clause\n", "COMMENT */\n",
           'FAMILY_NAME "Small"\n', 'FONT_VERSION "1.0"\n', head, f"CHARS {len(glyphs)}\n"]
    for c in glyphs:
        out += [f"STARTCHAR c{c}\n", f"ENCODING {c}\n", f"BBX {bbx}\n", "BITMAP\n"]
        out += [r + "\n" for r in rows] + ["ENDCHAR\n"]
    return "".join(out + ["ENDFONT\n"])


class SmallFont(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def bdf(self, text, name="small.bdf"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_good(self):
        """Read as written; fonts.c names the font and carries its notice."""
        path = self.bdf(small_bdf())
        width, height, glyphs, info = F.parse_bdf(path)
        self.assertEqual((width, height, len(glyphs), glyphs[0x41]), (8, 2, 95, [0xFF, 0x81]))
        h, c = F.generate([("small", path)])
        self.assertIn("extern const font_t font_small;", h)
        self.assertIn("// font_small: small.bdf (Small 1.0)\n"
                      "//   Copyright (c) 2026 Example Author\n"
                      "//   SPDX-License-Identifier: BSD-2-Clause\n", c)

    def test_malformed(self):
        """Each one stops the generator with BdfError."""
        no_end = small_bdf().replace("ENDCHAR\nENDFONT\n", "")
        cases = {
            "a glyph of another box": small_bdf().replace("ENCODING 65\nBBX 8 2 0 0", "ENCODING 65\nBBX 8 2 0 -1"),
            "a glyph missing": small_bdf(glyphs=[c for c in range(0x20, 0x7F) if c != 0x41]),
            "rows missing": small_bdf(rows=("FF",)),
            "no bounding box": small_bdf(head=""),
            "no ENDCHAR": no_end[:no_end.rindex("BITMAP")],
            "no ENCODING": small_bdf().replace("ENCODING 65\n", ""),
            "no BBX": small_bdf().replace("ENCODING 65\nBBX 8 2 0 0\n", "ENCODING 65\n"),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(F.BdfError):
                F.parse_bdf(self.bdf(text))

    def test_main(self):
        """main(): 1 and no files on a malformed font; a usage error on a
        name that is no C identifier."""
        out = os.path.join(self.tmp.name, "out")
        bad = self.bdf(small_bdf(rows=("FF",)), "bad.bdf")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(F.main(["--out", out, f"bad={bad}"]), 1)
            self.assertIn("bad.bdf", err.getvalue())
            self.assertFalse(os.path.exists(out))
            with self.assertRaises(SystemExit):
                F.main(["--out", out, f"8-bit={self.bdf(small_bdf())}"])
        self.assertEqual(F.main(["--out", out, f"small={self.bdf(small_bdf())}"]), 0)
        self.assertEqual(sorted(os.listdir(out)), ["fonts.c", "fonts.h"])


if __name__ == "__main__":
    unittest.main()
