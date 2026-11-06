# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The checker's descriptors at each speed (firmware/app/usb_descriptors.c)
on the host. At High-Speed the configuration is the audio function with its
streaming interface; at Full-Speed the device enumerates as USB 2.0 (5.3.1.1,
9.1.1.3) requires, with a configuration that holds only the AudioControl
interface, so it offers nothing to play. The device qualifier and the
other-speed configuration describe the speed the device is not running at
(USB 2.0 9.6.2, 9.6.4). Built with the host tool
firmware/app/host/usb_desc_tool.c; needs a host C compiler (gcc or cc);
skipped without one."""

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
FIXTURES = os.path.join(ROOT, "tests", "uac2")
CC = shutil.which("gcc") or shutil.which("cc")

DEVICE, CONFIGURATION, OTHER_SPEED_CONFIGURATION = 0x01, 0x02, 0x07

# bcdDevice, raised with every change to the descriptors
BCD_DEVICE = 0x0103

# The device descriptor at both speeds: USB 2.0, the IAD class codes (UAC2
# 4.2), a 64-byte control endpoint, 16C0:27E0, one configuration
DEVICE_DESC = bytes([0x12, 0x01, 0x00, 0x02, 0xEF, 0x02, 0x01, 0x40, 0xC0, 0x16, 0xE0, 0x27,
                     BCD_DEVICE & 0xFF, BCD_DEVICE >> 8, 0x01, 0x02, 0x03, 0x01])

# The device qualifier at both speeds: the other speed's class codes (UAC2
# 4.3: as the device descriptor's), control endpoint size and number of
# configurations (USB 2.0 Table 9-9)
QUALIFIER = bytes([0x0A, 0x06, 0x00, 0x02, 0xEF, 0x02, 0x01, 0x40, 0x01, 0x00])

# The Full-Speed configuration: one interface (USB 2.0 9.6.3: one or more),
# the audio function's IAD over it (UAC2 4.6), the AudioControl interface
# without endpoints and the class-specific header of an I/O box with no
# entities: no streaming interface (UAC2 3.16.2: zero or more), so no USB
# streaming terminal for one to link to.
FS_CONFIG = bytes.fromhex(
    "09 02 23 00 01 01 00 80 32"
    "08 0b 00 01 01 00 20 00"
    "09 04 00 00 00 01 01 20 00"
    "09 24 01 00 02 08 09 00 00")


def fixture(name):
    """A configuration descriptor from tests/uac2/, as bytes."""
    with open(os.path.join(FIXTURES, name)) as f:
        return bytes.fromhex(" ".join(l for l in f.read().splitlines() if l.strip() and not l.startswith("#")))


def high_speed_config(name):
    """The High-Speed configuration as the fixture holds it, read from a
    checker before the output terminal became a digital audio interface
    (0x0602, USB Audio 2.0 Terminal Types, Table 2-6) instead of a speaker
    (0x0301)."""
    cfg = bytearray(fixture(name))
    ot = cfg.find(bytes([0x0C, 0x24, 0x03]))
    assert cfg[ot + 4:ot + 6] == bytes([0x01, 0x03])
    cfg[ot + 4:ot + 6] = bytes([0x02, 0x06])
    return bytes(cfg)


def retyped(cfg, desc_type):
    return bytes([cfg[0], desc_type]) + cfg[2:]


@unittest.skipUnless(CC, "no host C compiler")
class UsbDescriptors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = os.path.join(cls.tmp.name, "usb_desc_tool")
        cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-DCFG_TUSB_MCU=OPT_MCU_STM32U5"]
        # TinyUSB's own tusb.h and the firmware's tusb_config.h; the stub
        # directory only for family.h
        for d in ["lib/tinyusb/src", "app", "app/host/stub"]:
            cmd += ["-I", os.path.join(FW, d)]
        cmd += ["-o", cls.exe] + [os.path.join(FW, s) for s in ["app/host/usb_desc_tool.c", "app/usb_descriptors.c"]]
        subprocess.run(cmd, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def descriptors(self, formats=None):
        """{(speed, name): bytes or None}"""
        out = subprocess.run([self.exe] + ([formats] if formats else []), capture_output=True, text=True,
                             check=True).stdout
        res = {}
        for line in out.splitlines():
            speed, name, rest = line.split(" ", 2)
            res[(speed, name)] = None if rest == "none" else bytes.fromhex(rest)
        return res

    def test_device_and_qualifier(self):
        """The same device descriptor and device qualifier at both speeds."""
        d = self.descriptors()
        for speed in ("high", "full"):
            with self.subTest(speed=speed):
                self.assertEqual(d[(speed, "device")], DEVICE_DESC)
                self.assertEqual(d[(speed, "qualifier")], QUALIFIER)

    def test_high_speed_unchanged(self):
        """At High-Speed the configuration is the one the checker has always
        had for each format list."""
        cases = {None: "checker-default.txt", "16/2,32/4": "checker-16-2_32-4.txt", "24/3": "checker-24-3.txt",
                 "24/3,16/2": "checker-24-3_16-2.txt"}
        for formats, name in cases.items():
            with self.subTest(formats=formats):
                self.assertEqual(self.descriptors(formats)[("high", "configuration")], high_speed_config(name))

    def test_full_speed_offers_no_audio(self):
        """At Full-Speed, whatever the formats, only the AudioControl
        interface."""
        for formats in (None, "24/3", "16/2,32/4"):
            with self.subTest(formats=formats):
                self.assertEqual(self.descriptors(formats)[("full", "configuration")], FS_CONFIG)

    def test_other_speed(self):
        """The other-speed configuration is the other speed's configuration,
        retyped: Full-Speed's at High-Speed, High-Speed's (with the formats
        offered) at Full-Speed."""
        for formats, name in ((None, "checker-default.txt"), ("24/3", "checker-24-3.txt")):
            with self.subTest(formats=formats):
                d = self.descriptors(formats)
                self.assertEqual(d[("high", "other_speed")], retyped(FS_CONFIG, OTHER_SPEED_CONFIGURATION))
                self.assertEqual(d[("full", "other_speed")],
                                 retyped(high_speed_config(name), OTHER_SPEED_CONFIGURATION))
                # and the configuration itself stays a configuration
                for speed in ("high", "full"):
                    self.assertEqual(d[(speed, "configuration")][1], CONFIGURATION)


if __name__ == "__main__":
    unittest.main()
