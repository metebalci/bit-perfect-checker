# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The checker's descriptors on the host, as u2ps builds them from the
checker's description (firmware/app/usb_formats.c), read with GET_DESCRIPTOR
requests through u2ps's PC test kit (firmware/app/host/usb_desc_tool.c).
They must be byte for byte those of firmware 1.1.1
(tests/uac2/checker-1.1.1-descriptors.txt): the device, the device
qualifier, the configuration and the other-speed configuration at both
speeds, and strings 0 to 3, for several format lists. bcdDevice stays
0x0104 only while they are.

At High-Speed the configuration is the audio function with its streaming
interface; at Full-Speed the device enumerates as USB 2.0 (5.3.1.1, 9.1.1.3)
requires, with a configuration that holds only the AudioControl interface,
so it offers nothing to play. The device qualifier and the other-speed
configuration describe the speed the device is not running at (USB 2.0
9.6.2, 9.6.4). Needs CMake and a host C compiler; skipped without them."""

import os
import subprocess
import tempfile
import unittest

from tests import host_tools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "uac2", "checker-1.1.1-descriptors.txt")

CONFIGURATION, OTHER_SPEED_CONFIGURATION = 0x02, 0x07
NAMES = ("device", "qualifier", "configuration", "other_speed", "string0", "string1", "string2", "string3")
ALL = "16/2,24/4,32/4,24/3"


def fixture():
    """{formats: {(speed, name): bytes}} from the 1.1.1 capture."""
    res = {}
    with open(FIXTURE) as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            formats, speed, name, rest = line.split(" ", 3)
            res.setdefault(formats, {})[(speed, name)] = bytes.fromhex(rest)
    return res


def iso_data_ep(cfg):
    """Offsets of the class-specific AS isochronous audio data endpoint
    descriptors (UAC2 4.10.1.2: CS_ENDPOINT 0x25, EP_GENERAL 0x01)."""
    offsets, i = [], 0
    while i < len(cfg):
        if cfg[i + 1:i + 3] == bytes([0x25, 0x01]):
            offsets.append(i)
        i += cfg[i]
    return offsets


@unittest.skipUnless(host_tools.AVAILABLE, "no CMake or host C compiler")
class UsbDescriptors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = host_tools.build(cls.tmp.name, ["usb_desc_tool"])["usb_desc_tool"]
        cls.expected = fixture()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def descriptors(self, formats):
        """{(speed, name): bytes}, as the tool read them from u2ps"""
        r = subprocess.run([self.exe, formats], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        res = {}
        for line in r.stdout.splitlines():
            if line.startswith(("high ", "full ")):  # not the kit's own lines
                speed, name, rest = line.split(" ", 2)
                res[(speed, name)] = bytes.fromhex(rest)
        return res

    def test_identical_to_1_1_1(self):
        """Every descriptor, at both speeds, for every format list of the
        capture."""
        self.assertGreaterEqual(len(self.expected), 9)
        for formats, want in self.expected.items():
            got = self.descriptors(formats)
            for speed in ("high", "full"):
                for name in NAMES:
                    with self.subTest(formats=formats, speed=speed, name=name):
                        self.assertEqual(got.get((speed, name)), want[(speed, name)])

    def test_bcd_device(self):
        """bcdDevice 0x0104, as 1.1.1."""
        d = self.descriptors(ALL)[("high", "device")]
        self.assertEqual(d[12:14], bytes([0x04, 0x01]))

    def test_no_lock_delay(self):
        """Every streaming alt setting's data endpoint has bLockDelayUnits
        and wLockDelay zero: the endpoint is asynchronous, and UAC2 4.10.1.2
        requires both to be zero for asynchronous endpoints. Also in the
        other-speed configuration at Full-Speed."""
        for formats, alts in ((ALL, 4), ("24/3", 1), ("16/2,32/4", 2), ("24/3,16/2", 2)):
            with self.subTest(formats=formats):
                d = self.descriptors(formats)
                for cfg in (d[("high", "configuration")], d[("full", "other_speed")]):
                    eps = iso_data_ep(cfg)
                    self.assertEqual(len(eps), alts)
                    for ep in eps:
                        self.assertEqual(cfg[ep], 8)
                        self.assertEqual(cfg[ep + 5:ep + 8], bytes(3))

    def test_other_speed(self):
        """The other-speed configuration is the other speed's configuration,
        retyped: Full-Speed's at High-Speed, High-Speed's at Full-Speed."""
        for formats in (ALL, "24/3"):
            with self.subTest(formats=formats):
                d = self.descriptors(formats)
                for speed, other in (("high", "full"), ("full", "high")):
                    cfg = d[(other, "configuration")]
                    self.assertEqual(d[(speed, "other_speed")], bytes([cfg[0], OTHER_SPEED_CONFIGURATION]) + cfg[2:])
                    self.assertEqual(d[(speed, "configuration")][1], CONFIGURATION)


if __name__ == "__main__":
    unittest.main()
