# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The status screen as drawn (firmware/app/screen.c) on a panel in memory
that follows firmware/include/panel.h, in the STM32F723E-DISCO's layout,
with the fonts tools/fontgen.py makes from Spleen: every glyph of the label
line (12x24), the file lines (16x32) and the version (8x16) equals its BDF
bitmap. Built with the
host tool firmware/app/host/screen_draw.c; needs a host C compiler and the
Spleen submodule; skipped without them."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
SPLEEN = os.path.join(FW, "lib", "spleen")
FONTGEN = os.path.join(FW, "tools", "fontgen.py")
CC = shutil.which("gcc") or shutil.which("cc")
FONTS = {"8x16": "spleen-8x16.bdf", "12x24": "spleen-12x24.bdf", "16x32": "spleen-16x32.bdf"}
SOURCES = ["app/host/screen_draw.c", "app/screen.c", "app/screen_model.c", "bpc1/bpc1_checker.c", "bpc1/bpc1.c"]


def load_fontgen():
    spec = importlib.util.spec_from_file_location("fontgen", FONTGEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(CC and os.path.exists(os.path.join(SPLEEN, FONTS["12x24"])), "no C compiler or no Spleen")
class ScreenDraw(unittest.TestCase):
    fw = FW

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        args = [f"{name}={os.path.join(SPLEEN, f)}" for name, f in FONTS.items()]
        subprocess.run([sys.executable, "-I", FONTGEN, "--out", d] + args, check=True)
        with open(os.path.join(d, "version.h"), "w") as f:
            f.write('#define FW_VERSION "1.1.0"\n')
        cls.exe = os.path.join(d, "screen_draw")
        cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", d]
        fw = cls.fw
        cmd += ["-I", os.path.join(fw, "include"), "-I", os.path.join(fw, "app"), "-I", os.path.join(fw, "bpc1")]
        cmd += ["-o", cls.exe, os.path.join(d, "fonts.c")] + [os.path.join(fw, s) for s in SOURCES]
        subprocess.run(cmd, check=True)
        fontgen = load_fontgen()
        cls.bdf = {name: fontgen.parse_bdf(os.path.join(SPLEEN, f)) for name, f in FONTS.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drawn(self, x, y, w, h, *args):
        out = subprocess.run([self.exe, *args], input=f"glyph {x} {y} {w} {h}\n", capture_output=True, text=True,
                             check=True).stdout.split()
        self.assertEqual(len(out), h)
        return out

    def expected(self, font, c):
        width, height, glyphs, _ = self.bdf[font]
        bits = 8 * ((width + 7) // 8)
        return ["".join("1" if row >> (bits - 1 - i) & 1 else "0" for i in range(width)) for row in glyphs[ord(c)]]

    def check_text(self, font, text, cols, y, *args):
        """text centred in cols columns at y (full-width text: as given)"""
        width, height = (int(v) for v in font.split("x"))
        pad = (cols - len(text)) // 2
        x0 = (240 - cols * width) // 2
        for i, c in enumerate(text):
            if c == " ":
                continue
            with self.subTest(font=font, char=c, args=args):
                self.assertEqual(self.drawn(x0 + (pad + i) * width, y, width, height, *args), self.expected(font, c))

    def test_splash(self):
        """BIT-PERFECT and CHECKER in 16x32, the address in 12x24, the version
        in 8x16, all centred; the squares red, green, blue from the left."""
        self.check_text("16x32", "BIT-PERFECT", 11, 40, "splash")
        self.check_text("16x32", "CHECKER", 7, 76, "splash")
        self.check_text("12x24", "bpc.metebalci.com", 17, 124, "splash")
        self.check_text("8x16", "1.1.0", 30, 220, "splash")
        q = "".join(f"pixel {40 + 64 * i + 16} {180}\n" for i in range(3))
        out = subprocess.run([self.exe, "splash"], input=q, capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, ["ff0000", "00ff00", "0000ff"])

    def test_label_12x24(self):
        """UNKNOWN, a column in from the left of 20 columns at y=78."""
        self.check_text("12x24", " UNKNOWN".ljust(20), 20, 78)

    def test_file_lines_16x32(self):
        """The last result's file, PCM over 24/48, centred in 15 columns."""
        self.check_text("16x32", "PCM", 15, 126)
        self.check_text("16x32", "24/48", 15, 164)

    def test_version_8x16(self):
        self.check_text("8x16", "1.1.0", 30, 220)


if __name__ == "__main__":
    unittest.main()
