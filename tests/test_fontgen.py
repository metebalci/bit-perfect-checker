"""The display font generator (firmware/tools/fontgen.py) against Spleen's
BDF files in firmware/lib/spleen: every glyph of 0x20-0x7E, read back from
the generated C compiled on the host, equals the BDF bitmap; a re-run gives
byte-identical files. Needs the Spleen submodule and, for the glyph check, a
host C compiler; skipped without them."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTGEN = os.path.join(ROOT, "firmware", "tools", "fontgen.py")
SPLEEN = os.path.join(ROOT, "firmware", "lib", "spleen")
FONTS = {"8x16": (8, 16), "16x32": (16, 32)}
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
        # 95 glyphs of 16 and of 64 bytes: the arrays' declared sizes
        with open(os.path.join(self.out, "fonts.c"), encoding="ascii") as f:
            text = f.read()
        self.assertIn("font_8x16_bits[1520]", text)
        self.assertIn("font_16x32_bits[6080]", text)


if __name__ == "__main__":
    unittest.main()
