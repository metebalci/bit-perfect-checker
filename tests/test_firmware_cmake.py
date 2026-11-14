# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The firmware's configure (firmware/CMakeLists.txt) and u2ps's build
constants: the board and its family decide them, so a -D of one, which
would change nothing, stops the configure, and the cache holds the value in
use. Needs CMake, Ninja, the GNU Arm toolchain and the submodules; skipped
without them."""

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
CMAKE = shutil.which("cmake")
NINJA = "/usr/bin/ninja" if os.path.exists("/usr/bin/ninja") else shutil.which("ninja")
ARM = shutil.which("arm-none-eabi-gcc")
SUBMODULES = all(os.path.exists(os.path.join(FW, "lib", d, f)) for d, f in (
    ("u2ps", "CMakeLists.txt"), ("cmsis_device_u5", "Include"), ("stm32u5xx_hal_driver", "Inc"), ("CMSIS_5", "CMSIS")))
BOARD = "nucleo_u5a5zj_q"


@unittest.skipUnless(CMAKE and NINJA and ARM and SUBMODULES, "no CMake, Ninja, GNU Arm toolchain or submodules")
class U2psConstants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.build = os.path.join(self.tmp.name, "build")

    def tearDown(self):
        self.tmp.cleanup()

    def configure(self, *args):
        return subprocess.run([CMAKE, "-S", FW, "-B", self.build, "-G", "Ninja", "-DBOARD=" + BOARD,
                               "-DCMAKE_MAKE_PROGRAM=" + NINJA, *args], capture_output=True, text=True)

    def cache(self, name):
        with open(os.path.join(self.build, "CMakeCache.txt")) as f:
            for line in f:
                if line.startswith(name + ":"):
                    return line.strip().split(":", 1)[1]
        return None

    def test_value_in_use(self):
        r = self.configure()
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        # the family's 2702 buffers (families/stm32u5/family.cmake), not
        # u2ps's default of 16
        self.assertEqual(self.cache("U2PS_ISO_OUT_COUNT_MAX"), "INTERNAL=2702")
        self.assertEqual(self.cache("U2PS_CHIP"), "INTERNAL=stm32u5a5")
        self.assertEqual(self.configure().returncode, 0)

    def test_d_refused(self):
        for first in (True, False):
            with self.subTest(first_configure=first):
                self.build = os.path.join(self.tmp.name, "build-%s" % first)
                if not first:
                    self.assertEqual(self.configure().returncode, 0)
                r = self.configure("-DU2PS_ISO_OUT_COUNT_MAX=500")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("U2PS_ISO_OUT_COUNT_MAX is this project's (2702", r.stderr)
                # it stays refused until it leaves the cache
                self.assertNotEqual(self.configure().returncode, 0)
                self.assertEqual(self.configure("-UU2PS_ISO_OUT_COUNT_MAX").returncode, 0)
                self.assertEqual(self.cache("U2PS_ISO_OUT_COUNT_MAX"), "INTERNAL=2702")

    def test_d_of_another_refused(self):
        r = self.configure("-DU2PS_VBUS=bus")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("U2PS_VBUS is this project's (app", r.stderr)


if __name__ == "__main__":
    unittest.main()
