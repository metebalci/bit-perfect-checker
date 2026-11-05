# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The status screen's slices (firmware/app/screen.c): each call of
screen_task() draws for at most SCREEN_DRAW_US, on a panel whose drawing
time is a model (time per window, per call and per pixel), in the
STM32U5G9J-DK2's layout (800x480, the layout's area the left 480): from the
clear of the whole panel after the splash to the full status. Built with
the host tool firmware/app/host/screen_slice.c; needs a host C compiler and
the Spleen submodule; skipped without them."""

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
SOURCES = ["app/host/screen_slice.c", "app/screen.c", "app/screen_model.c", "bpc1/bpc1_checker.c", "bpc1/bpc1.c"]
DK2_FONTS = {"12x24": "spleen-12x24.bdf", "16x32": "spleen-16x32.bdf", "32x64": "spleen-32x64.bdf"}


@unittest.skipUnless(CC and os.path.exists(os.path.join(SPLEEN, DK2_FONTS["32x64"])), "no C compiler or no Spleen")
class ScreenSliceDK2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        args = [f"{name}={os.path.join(SPLEEN, f)}" for name, f in DK2_FONTS.items()]
        subprocess.run([sys.executable, "-I", FONTGEN, "--out", d] + args, check=True)
        with open(os.path.join(d, "version.h"), "w") as f:
            f.write('#define FW_VERSION "1.1.0"\n')
        exe = os.path.join(d, "screen_slice")
        cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", d, "-DDRAW_W=800u", "-DDRAW_H=480u",
               '-DDRAW_LAYOUT="screen_layout.h"', "-I", os.path.join(FW, "boards", "stm32u5g9j_dk2")]
        cmd += ["-I", os.path.join(FW, "include"), "-I", os.path.join(FW, "app"), "-I", os.path.join(FW, "bpc1")]
        cmd += ["-o", exe, os.path.join(d, "fonts.c")] + [os.path.join(FW, s) for s in SOURCES]
        subprocess.run(cmd, check=True)
        out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split("\n")
        cls.budget = int(out[0].split()[1])
        cls.slices = [tuple(int(v) for v in line.split()[1:]) for line in out[1:] if line.startswith("slice ")]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_whole_panel_is_cleared(self):
        # The clear's pixels and at least the band's
        self.assertGreater(sum(px for _, px in self.slices), 800 * 480 + 480 * 136)

    def test_every_slice_within_the_budget(self):
        over = [(i, c) for i, (c, _) in enumerate(self.slices) if c > self.budget]
        self.assertEqual(over, [], f"budget {self.budget} cycles")


if __name__ == "__main__":
    unittest.main()
