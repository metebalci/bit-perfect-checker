# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The status screen as drawn (firmware/app/screen.c) on a panel in memory
that follows firmware/include/panel.h, with the fonts tools/fontgen.py makes
from Spleen: every glyph of the splash, the label line, the file lines and
the version equals its BDF bitmap (Spleen's 12x24 doubled for the DK2's
24x48), in each board's layout as its display.c uses it
(boards/<board>/screen_layout.h): the STM32F723E-DISCO's (240x240) and the
STM32U5G9J-DK2's (800x480: the F723's screen at twice the size on the left
480x480, its band, frame and hourglass included, the right 320x480 black;
and moved to x = 160, to test the area's offset). Built with the host tool
firmware/app/host/screen_draw.c; needs a host C compiler and the Spleen
submodule; skipped without them."""

import importlib.util
import os
import re
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


def layout_flags(board, w, h):
    """The host tool's build flags for a board's panel size and layout header"""
    return [f"-DDRAW_W={w}u", f"-DDRAW_H={h}u", '-DDRAW_LAYOUT="screen_layout.h"',
            "-I", os.path.join(FW, "boards", board)]


class ScreenDrawBase:
    """A board's screen drawn by the host tool: subclasses give the panel,
    the layout's area, the fonts and the tool's build flags."""
    fw = FW
    panel = (240, 240)
    area = (0, 240)  # x, width
    fonts = FONTS
    cflags = layout_flags("stm32f723e_disco", 240, 240)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        args = [f"{name}={os.path.join(SPLEEN, f)}" for name, f in cls.fonts.items()]
        subprocess.run([sys.executable, "-I", FONTGEN, "--out", d] + args, check=True)
        with open(os.path.join(d, "version.h"), "w") as f:
            f.write('#define FW_VERSION "1.1.0"\n')
        cls.exe = os.path.join(d, "screen_draw")
        cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", d] + cls.cflags
        fw = cls.fw
        cmd += ["-I", os.path.join(fw, "include"), "-I", os.path.join(fw, "app"), "-I", os.path.join(fw, "bpc1")]
        cmd += ["-o", cls.exe, os.path.join(d, "fonts.c")] + [os.path.join(fw, s) for s in SOURCES]
        subprocess.run(cmd, check=True)
        fontgen = load_fontgen()
        cls.bdf = {name: fontgen.parse_bdf(os.path.join(SPLEEN, f)) for name, f in cls.fonts.items()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drawn(self, x, y, w, h, *args):
        out = subprocess.run([self.exe, *args], input=f"glyph {x} {y} {w} {h}\n", capture_output=True, text=True,
                             check=True).stdout.split()
        self.assertEqual(len(out), h)
        return out

    def pixels(self, points, *args):
        q = "".join(f"pixel {x} {y}\n" for x, y in points)
        return subprocess.run([self.exe, *args], input=q, capture_output=True, text=True, check=True).stdout.split()

    doubled = {}  # a font name drawn as another font at twice its size

    def expected(self, font, c):
        if font in self.doubled:
            return [r for row in self.expected(self.doubled[font], c) for r in ["".join(b + b for b in row)] * 2]
        width, height, glyphs, _ = self.bdf[font]
        bits = 8 * ((width + 7) // 8)
        return ["".join("1" if row >> (bits - 1 - i) & 1 else "0" for i in range(width)) for row in glyphs[ord(c)]]

    # The tall band at Full-Speed: its lines, its height, the frame's width
    # and the band's font; the version under it
    full_speed_lines = ["NEEDS", "HIGH-SPEED", "USB PORT"]
    tall = (208, 10, "16x32")
    version = ("8x16", 220)

    def check_full_speed(self):
        """Set up at Full-Speed: NEEDS, HIGH-SPEED and USB PORT in the band's
        font, each line centred in the area, the three centred in a tall band
        (4 px between lines) with a red frame; inside the frame nothing but
        their glyphs, so not the run's label or the file lines that it
        covers; black from the band down to the version, which stays."""
        h, f, font = self.tall
        fw, fh = (int(v) for v in font.split("x"))
        ax, aw = self.area
        lines = self.full_speed_lines
        top = (h - len(lines) * fh - (len(lines) - 1) * 4) // 2
        lit = 0
        for n, line in enumerate(lines):
            self.assertLessEqual(len(line) * fw, aw - 2 * f, f"{line} does not fit")
            x0 = ax + (aw - len(line) * fw) // 2
            for i, c in enumerate(line):
                if c == " ":
                    continue
                with self.subTest(line=line, char=c):
                    want = self.expected(font, c)
                    lit += "".join(want).count("1")
                    self.assertEqual(self.drawn(x0 + i * fw, top + n * (fh + 4), fw, fh, "full-speed"), want)
        inside = self.drawn(ax + f, f, aw - 2 * f, h - 2 * f, "full-speed")
        self.assertEqual("".join(inside).count("1"), lit)
        frame = [(ax, 0), (ax + f - 1, h // 2), (ax + aw - 1, h - 1), (ax + aw // 2, h - f), (ax + aw - f, 1)]
        self.assertEqual(self.pixels(frame, "full-speed"), ["ff0000"] * len(frame))
        vfont, vy = self.version
        self.assertEqual(set("".join(self.drawn(ax, h, aw, vy - h, "full-speed"))), {"0"})
        self.check_text(vfont, "1.1.0", 30, vy, "full-speed")

    def check_full_speed_back(self):
        """From Full-Speed back to High-Speed, also after High-Speed before it:
        every pixel as if Full-Speed had never been drawn (the tall band
        cleared, the texts it covered drawn again)."""
        q = "hash\n"
        hashes = [subprocess.run([self.exe, *a], input=q, capture_output=True, text=True, check=True).stdout
                  for a in ([], ["full-speed-back"], ["full-speed-between"], ["full-speed"])]
        self.assertEqual(hashes[1], hashes[0])
        self.assertEqual(hashes[2], hashes[0])
        self.assertNotEqual(hashes[3], hashes[0])

    def check_text(self, font, text, cols, y, *args):
        """text centred in cols columns centred in the area at y (full-width
        text: as given)"""
        width, height = (int(v) for v in font.split("x"))
        pad = (cols - len(text)) // 2
        x0 = self.area[0] + (self.area[1] - cols * width) // 2
        for i, c in enumerate(text):
            if c == " ":
                continue
            with self.subTest(font=font, char=c, args=args):
                self.assertEqual(self.drawn(x0 + (pad + i) * width, y, width, height, *args), self.expected(font, c))


@unittest.skipUnless(CC and os.path.exists(os.path.join(SPLEEN, FONTS["12x24"])), "no C compiler or no Spleen")
class ScreenDraw(ScreenDrawBase, unittest.TestCase):
    """The STM32F723E-DISCO's layout (boards/stm32f723e_disco/screen_layout.h),
    240x240."""

    def test_splash(self):
        """BIT-PERFECT and CHECKER in 16x32, the address in 12x24, the version
        in 8x16, all centred; the squares red, green, blue from the left."""
        self.check_text("16x32", "BIT-PERFECT", 11, 40, "splash")
        self.check_text("16x32", "CHECKER", 7, 76, "splash")
        self.check_text("12x24", "bpc.metebalci.com", 17, 124, "splash")
        self.check_text("8x16", "1.1.0", 30, 220, "splash")
        out = self.pixels([(40 + 64 * i + 16, 180) for i in range(3)], "splash")
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

    def test_file_lines_old_run(self):
        """The last result from the run before: no file lines (the version
        stays)."""
        self.assertEqual(set("".join(self.drawn(0, 126, 240, 70, "old-run"))), {"0"})
        self.check_text("8x16", "1.1.0", 30, 220, "old-run")

    def test_full_speed(self):
        self.check_full_speed()

    def test_full_speed_back(self):
        self.check_full_speed_back()


DK2_FONTS = {"12x24": "spleen-12x24.bdf", "16x32": "spleen-16x32.bdf", "32x64": "spleen-32x64.bdf"}


def hourglass_1x():
    """The hourglass bitmap in app/screen.c, as rows of 16 characters"""
    with open(os.path.join(FW, "app", "screen.c"), encoding="ascii") as f:
        text = f.read()
    body = re.search(r"static const uint8_t hourglass\[[^]]*\] = \{(.*?)\};", text, re.S).group(1)
    data = [int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
    return [format(data[2 * r] << 8 | data[2 * r + 1], "016b") for r in range(len(data) // 2)]


@unittest.skipUnless(CC and os.path.exists(os.path.join(SPLEEN, DK2_FONTS["32x64"])), "no C compiler or no Spleen")
class ScreenDrawDK2(ScreenDrawBase, unittest.TestCase):
    """The STM32U5G9J-DK2's layout (boards/stm32u5g9j_dk2/screen_layout.h):
    the F723's at twice the size on the left 480x480 of 800x480; the labels
    and the address in Spleen's 12x24 doubled (24x48, which Spleen lacks)."""
    panel = (800, 480)
    area = (0, 480)
    fonts = DK2_FONTS
    doubled = {"24x48": "12x24"}
    cflags = layout_flags("stm32u5g9j_dk2", 800, 480)
    tall = (416, 20, "32x64")
    version = ("16x32", 440)

    def outside_black(self, *args):
        """Every pixel left and right of the area is black."""
        x, w = self.area
        if x > 0:
            self.assertEqual(set("".join(self.drawn(0, 0, x, 480, *args))), {"0"})
        if x + w < 800:
            self.assertEqual(set("".join(self.drawn(x + w, 0, 800 - x - w, 480, *args))), {"0"})

    def test_splash(self):
        """BIT-PERFECT and CHECKER in 32x64, the address in 24x48, the
        version in 16x32, all centred in the area; the squares, 64 px, red,
        green, blue from the left; nothing outside the area."""
        self.check_text("32x64", "BIT-PERFECT", 11, 80, "splash")
        self.check_text("32x64", "CHECKER", 7, 148, "splash")
        self.check_text("24x48", "bpc.metebalci.com", 17, 248, "splash")
        self.check_text("16x32", "1.1.0", 30, 440, "splash")
        out = self.pixels([(self.area[0] + 80 + 128 * i + 32, 360) for i in range(3)], "splash")
        self.assertEqual(out, ["ff0000", "00ff00", "0000ff"])
        self.outside_black("splash")

    def test_label_24x48(self):
        """UNKNOWN, a column in from the left of 20 columns of 24x48 at y=156."""
        self.check_text("24x48", " UNKNOWN".ljust(20), 20, 156)

    def test_file_lines_32x64(self):
        """The last result's file, PCM over 24/48, in 15 columns of 32x64."""
        self.check_text("32x64", "PCM", 15, 252)
        self.check_text("32x64", "24/48", 15, 328)

    def test_version_16x32(self):
        self.check_text("16x32", "1.1.0", 30, 440)

    def test_file_lines_old_run(self):
        """The last result from the run before: no file lines (the version
        stays)."""
        self.assertEqual(set("".join(self.drawn(self.area[0], 252, 480, 140, "old-run"))), {"0"})
        self.check_text("16x32", "1.1.0", 30, 440, "old-run")

    def test_band(self):
        """WAITING in 32x64 with the hourglass (lit) at twice its size right
        of it, the group centred in the area and the band's 136 lines; a frame
        of 20 px, red after the run's UNKNOWN_SIGNAL, black inside."""
        ax = self.area[0]
        word, gap = "WAITING", 1
        group = (len(word) + gap) * 32 + 32
        x0, y = ax + (480 - group) // 2, (136 - 64) // 2
        for i, c in enumerate(word):
            with self.subTest(char=c):
                self.assertEqual(self.drawn(x0 + 32 * i, y, 32, 64), self.expected("32x64", c))
        glass = self.drawn(x0 + group - 32, (136 - 64) // 2, 32, 64)
        doubled = [r for row in hourglass_1x() for r in ["".join(b + b for b in row)] * 2]
        self.assertEqual(glass, doubled)  # lit at the tool's time
        frame = [(0, 0), (19, 68), (460, 68), (479, 135)]
        inside = [(240, 20), (21, 21), (458, 114), (240, 115)]
        out = self.pixels([(ax + x, y) for x, y in frame + inside])
        self.assertEqual(out, ["ff0000"] * 4 + ["000000"] * 4)

    def test_band_not_connected(self):
        """NOT CONNECTED in 32x64 on one line, centred in the area (13
        columns fill the 416 px inside the frame and its margins), with no
        hourglass."""
        ax = self.area[0]
        word = "NOT CONNECTED"
        x0, y = ax + (480 - 32 * len(word)) // 2, (136 - 64) // 2
        for i, c in enumerate(word):
            if c == " ":
                continue
            with self.subTest(char=c):
                self.assertEqual(self.drawn(x0 + 32 * i, y, 32, 64, "not-connected"), self.expected("32x64", c))
        self.outside_black("not-connected")

    def test_outside_black(self):
        """Nothing outside the area (the right 320 columns of the DK2's)."""
        self.outside_black()

    def test_full_speed(self):
        self.check_full_speed()
        self.outside_black("full-speed")

    def test_full_speed_back(self):
        self.check_full_speed_back()


class ScreenDrawDK2Moved(ScreenDrawDK2):
    """The DK2's layout with its area moved to x = 160 (a test layout): the
    same checks, so that a broken area offset shows."""
    area = (160, 480)
    cflags = ScreenDrawDK2.cflags + ["-DDRAW_AREA_X=160u"]


if __name__ == "__main__":
    unittest.main()
