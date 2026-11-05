# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The STM32U5G9J-DK2's colour look-up table
(firmware/boards/stm32u5g9j_dk2/clut_rgb332.h), compiled on the host for
each panel green (default 208, in the header and in board.cmake): RGB332 indices expand to 8-bit channels; black, white, red
and blue stay exact; the pure greens (red and blue 0) scale so that full
green is PANEL_GREEN_MAX; every other entry is the plain expansion; and
0xRRGGBB truncated to an index (the panel driver's rgb332()) maps the screen's
colours to the right entries. Needs a host C compiler; skipped without one."""

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOARD = os.path.join(ROOT, "firmware", "boards", "stm32u5g9j_dk2")
CC = shutil.which("gcc") or shutil.which("cc")

DUMP_C = r"""
#include <stdint.h>
#include <stdio.h>
#include "clut_rgb332.h"

int main(void) {
  for (uint32_t i = 0; i < 256u; i++) {
    printf("%06lx\n", (unsigned long) clut_rgb332(i));
  }
  return 0;
}
"""


def expand3(v):
    return (v << 5) | (v << 2) | (v >> 1)


def plain(i):
    return expand3(i >> 5) << 16 | expand3((i >> 2) & 7) << 8 | (i & 3) * 0x55


def index(rgb):
    """The panel driver's rgb332(): R[7:5] G[7:5] B[7:6]"""
    return (rgb >> 16) & 0xE0 | (rgb >> 11) & 0x1C | (rgb >> 6) & 0x03


@unittest.skipUnless(CC, "no host C compiler")
class Clut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.tmp.name, "dump.c"), "w") as f:
            f.write(DUMP_C)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def table(self, green=None):
        exe = os.path.join(self.tmp.name, f"dump{green}")
        flags = [] if green is None else [f"-DPANEL_GREEN_MAX={green}u"]
        subprocess.run([CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", BOARD] + flags
                       + ["-o", exe, os.path.join(self.tmp.name, "dump.c")], check=True)
        out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(len(out), 256)
        return [int(v, 16) for v in out]

    def test_255_is_plain_expansion(self):
        """PANEL_GREEN_MAX 255 is no correction: the plain table."""
        self.assertEqual(self.table(255), [plain(i) for i in range(256)])

    def test_default_is_208(self):
        """Without PANEL_GREEN_MAX the header's default, 208, as board.cmake's
        cache default: the table of green 208."""
        self.assertEqual(self.table(), self.table(208))
        self.assertEqual(self.table()[index(0x00FF00)], 208 << 8)
        with open(os.path.join(BOARD, "board.cmake"), encoding="ascii") as f:
            self.assertRegex(f.read(), r"set\(PANEL_GREEN_MAX 208 CACHE STRING")

    def test_green_scaled_rest_exact(self):
        for green in (208, 192, 176):
            t = self.table(green)
            with self.subTest(green=green):
                for name, rgb in (("black", 0x000000), ("white", 0xFFFFFF), ("red", 0xFF0000), ("blue", 0x0000FF)):
                    self.assertEqual(t[index(rgb)], rgb, name)
                self.assertEqual(t[index(0x00FF00)], green << 8)
                for i in range(256):
                    r, g, b = i >> 5, (i >> 2) & 7, i & 3
                    if r == 0 and b == 0:
                        self.assertEqual(t[i], (expand3(g) * green + 127) // 255 << 8, i)
                    else:
                        self.assertEqual(t[i], plain(i), i)


if __name__ == "__main__":
    unittest.main()
