# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The firmware release zip (firmware/tools/package.py), its parts that need
no toolchain: the zip's bytes and contents from given images, README.txt,
NOTICES.txt from a small source tree, and the checks that stop a release;
and the files a build read, from a small Ninja build (skipped without Ninja
and a C compiler). The firmware's build itself (clone, CMake) is not run
here."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "firmware_package", os.path.join(ROOT, "firmware", "tools", "package.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

VERSION = "1.2.0"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FW = "v1.2.0-0123456"
DATE = (2026, 11, 5, 12, 34, 56)
IMAGES = {b: bytes([i]) * (1000 + i) + b"image " + b.encode() for i, b in enumerate(P.BOARDS)}
NOTICES = b"notices\r\n"
USB = (0x16C0, 0x27E0, "bpc.metebalci.com:")
U2PS = ("0.1.0", "https://github.com/metebalci/uac2-playback-stack", "b" * 40)
TOP = f"bit-perfect-checker-firmware-{VERSION}"
BSD3_BODY = """All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""


def build_archive(images=IMAGES, version=VERSION, date=DATE, notices=NOTICES, usb=USB, paths=()):
    return P.archive(version, FW, COMMIT, date, images, notices, usb, U2PS, paths=paths)


class Archive(unittest.TestCase):
    def test_name_and_listing(self):
        name, data = build_archive()
        self.assertEqual(name, TOP + ".zip")
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertEqual(names, [TOP + "/", f"{TOP}/NOTICES.txt", f"{TOP}/README.txt",
                                 f"{TOP}/SHA256SUMS",
                                 f"{TOP}/bpc-nucleo_u5a5zj_q-{VERSION}.bin",
                                 f"{TOP}/bpc-stm32f723e_disco-{VERSION}.bin",
                                 f"{TOP}/bpc-stm32u5g9j_dk2-{VERSION}.bin"])

    def test_contents(self):
        z = zipfile.ZipFile(io.BytesIO(build_archive()[1]))
        for board, image in IMAGES.items():
            self.assertEqual(z.read(f"{TOP}/bpc-{board}-{VERSION}.bin"), image)
        self.assertEqual(z.read(f"{TOP}/NOTICES.txt"), NOTICES)
        sums = z.read(f"{TOP}/SHA256SUMS").decode("ascii")
        self.assertEqual(sums, "".join(
            f"{hashlib.sha256(IMAGES[b]).hexdigest()}  bpc-{b}-{VERSION}.bin\n"
            for b in sorted(IMAGES)))
        self.assertEqual(z.read(f"{TOP}/README.txt"), P.readme(VERSION, FW, COMMIT, USB, U2PS))

    def test_entries_fixed(self):
        """Every entry: the given date, stored, Unix permissions, no extras."""
        for zi in zipfile.ZipFile(io.BytesIO(build_archive()[1])).infolist():
            with self.subTest(zi.filename):
                self.assertEqual(zi.date_time, (2026, 11, 5, 12, 34, 56))
                self.assertEqual(zi.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(zi.create_system, 3)
                mode = 0o40755 if zi.filename.endswith("/") else 0o100644
                self.assertEqual(zi.external_attr >> 16, mode)
                self.assertEqual((zi.extra, zi.comment), (b"", b""))

    def test_deterministic(self):
        """The same inputs give the same bytes, in any order of the images."""
        a = build_archive()[1]
        b = build_archive(dict(reversed(list(IMAGES.items()))))[1]
        self.assertEqual(a, b)
        self.assertNotEqual(a, build_archive(date=(2026, 11, 6, 0, 0, 0))[1])
        changed = dict(IMAGES, nucleo_u5a5zj_q=IMAGES["nucleo_u5a5zj_q"] + b"\0")
        self.assertNotEqual(a, build_archive(changed)[1])

    def test_every_board(self):
        images = dict(IMAGES)
        del images["stm32u5g9j_dk2"]
        with self.assertRaises(P.PackageError):
            build_archive(images)

    def test_no_paths(self):
        """A local path in an image, in NOTICES.txt or in README.txt stops it."""
        paths = ["/work/x", "/src/repo"]
        build_archive(paths=paths)
        cases = {"image": dict(images=dict(IMAGES, stm32f723e_disco=b"\0/src/repo/firmware/app/main.c\0")),
                 "NOTICES.txt": dict(notices=b"notices of /work/x/bit-perfect-checker/firmware\r\n"),
                 "README.txt": dict(usb=(0x1209, 0xB9C1, "/src/repo:"))}
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(P.PackageError):
                build_archive(paths=paths, **kwargs)


class Readme(unittest.TestCase):
    def test_text(self):
        data = P.readme("1.2.0-0123456", FW, COMMIT, USB, U2PS)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        text = data.decode("ascii")
        for b in P.BOARDS:
            self.assertIn(f"bpc-{b}-1.2.0-0123456.bin", text)
        self.assertIn(f"Built from commit {COMMIT} of\r\n", text)
        self.assertIn("The firmware reports its version as v1.2.0-0123456.", text)
        for word in ("NUCLEO-U5A5ZJ-Q", "STM32F723E-DISCO", "STM32U5G9J-DK2", "0x08000000",
                     "sn=<serial number>", "-w bpc-<board>-1.2.0-0123456.bin 0x08000000",
                     "STM32_Programmer_CLI -l st-link", "NOD_U5A5ZJ", "DIS_F723IE",
                     "16C0:27E0", "bpc.metebalci.com:", COMMIT,
                     "https://github.com/metebalci/bit-perfect-checker",
                     "https://bpc.metebalci.com", "NOTICES.txt", "SHA256SUMS"):
            self.assertIn(word, text)
        self.assertIn("STM32U5G9J-DK2's ST-LINK shows no drive", text)
        self.assertIn("16C0:27E0, Objective Development's shared VID/PID pair", text)
        self.assertIn("u2ps 0.1.0, the USB Audio Class 2.0 playback stack, at its\r\n"
                      "release tag v0.1.0 (commit " + "b" * 40 + ") of\r\n"
                      "https://github.com/metebalci/uac2-playback-stack.", text)
        self.assertNotIn("TinyUSB", text)

    def test_usb_id(self):
        """The USB ID and the serial number prefix the builds were made with."""
        text = P.readme(VERSION, FW, COMMIT, (0x1209, 0xB9C1, "example.org:"), U2PS).decode("ascii")
        self.assertIn("The device uses 1209:B9C1. Its serial number starts with example.org:", text)
        self.assertNotIn("16C0", text)
        self.assertNotIn("Objective Development", text)
        self.assertNotIn("bpc.metebalci.com:", text)


CACHE = """# This is the CMakeCache file.
//USB vendor ID
USB_VID:STRING=0x16C0
//USB product ID
USB_PID:STRING=0x27E0
//Serial number prefix
USB_SERIAL_PREFIX:STRING=bpc.metebalci.com:
USB_VID_EXTRA:STRING=0x1234
"""


class UsbOptions(unittest.TestCase):
    def test_from_cache(self):
        self.assertEqual(P.usb_options(CACHE), USB)
        other = CACHE.replace("0x16C0", "0x1209").replace("0x27E0", "0xb9c1").replace(
            "bpc.metebalci.com:", "example.org:")
        self.assertEqual(P.usb_options(other), (0x1209, 0xB9C1, "example.org:"))

    def test_bad_cache(self):
        for bad in (CACHE.replace("USB_PID:", "USB_PIDX:"), CACHE.replace("0x16C0", "0x10000"),
                    CACHE.replace("0x27E0", "27E0h"), "", CACHE.replace("USB_SERIAL_PREFIX:", "X:")):
            with self.subTest(bad), self.assertRaises(P.PackageError):
                P.usb_options(bad)

    def test_one_for_all_boards(self):
        same = {b: USB for b in P.BOARDS}
        self.assertEqual(P.check_usb(same), USB)
        for other in ((0x1209, 0x27E0, USB[2]), (0x16C0, 0x27E1, USB[2]), (0x16C0, 0x27E0, "x:")):
            with self.subTest(other), self.assertRaises(P.PackageError):
                P.check_usb(dict(same, stm32f723e_disco=other))

    def test_build_folders(self):
        """From each build folder's CMakeCache.txt; one board built with
        another prefix stops the script."""
        with tempfile.TemporaryDirectory() as d:
            dirs = {b: os.path.join(d, b) for b in P.BOARDS}
            for board, folder in dirs.items():
                os.makedirs(folder)
                with open(os.path.join(folder, "CMakeCache.txt"), "w") as f:
                    f.write(CACHE.replace("0x16C0", "0x1209"))
            self.assertEqual(P.build_usb(dirs), (0x1209, 0x27E0, "bpc.metebalci.com:"))
            with open(os.path.join(dirs["stm32u5g9j_dk2"], "CMakeCache.txt"), "w") as f:
                f.write(CACHE.replace("0x16C0", "0x1209").replace("bpc.metebalci.com:", "x:"))
            with self.assertRaises(P.PackageError):
                P.build_usb(dirs)


class U2ps(unittest.TestCase):
    """The USB stack the images use: u2ps, the submodule firmware/lib/u2ps,
    named in README.txt by its release."""

    def test_works(self):
        self.assertEqual(P.WORKS["firmware/lib/u2ps"], ("u2ps (the USB device stack)", "MIT", "LICENSE"))
        self.assertFalse([f for f in P.WORKS if "tinyusb" in f])

    def test_version(self):
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "firmware", "lib", "u2ps")
            os.makedirs(folder)
            cmake = os.path.join(folder, "CMakeLists.txt")
            with open(cmake, "w") as f:
                f.write("cmake_minimum_required(VERSION 3.22)\nproject(u2ps VERSION 0.1.0 LANGUAGES C)\n")
            self.assertEqual(P.u2ps_version(d), "0.1.0")
            with open(cmake, "w") as f:
                f.write("project(u2ps LANGUAGES C)\n")
            with self.assertRaises(P.PackageError):
                P.u2ps_version(d)

    def test_tag(self):
        """The release tag v<version> names the pinned commit; a clone
        without the tag, or a tag on another commit, stops the release."""
        pinned = "488631ec70e22b28c23750c66d0325489f09b0cd"
        P.check_u2ps_tag("0.1.0", pinned, pinned)
        for tagged, why in ((None, "no tag v0.1.0"), ("0" * 40, "not the pinned commit")):
            with self.assertRaisesRegex(P.PackageError, why):
                P.check_u2ps_tag("0.1.0", tagged, pinned)

    def test_tag_commit(self):
        """tag_commit() resolves a tag in a clone, annotated or not, to its
        commit; None without the tag."""
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
            for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "a"], ["tag", "v0.1.0"],
                         ["commit", "-q", "--allow-empty", "-m", "b"], ["tag", "-a", "-m", "t", "v0.2.0"]):
                subprocess.run(["git", *args], cwd=d, env=env, check=True, capture_output=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, check=True, capture_output=True,
                                  text=True).stdout.strip()
            first = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=d, check=True, capture_output=True,
                                   text=True).stdout.strip()
            self.assertEqual(P.tag_commit("v0.1.0", d), first)
            self.assertEqual(P.tag_commit("v0.2.0", d), head)
            self.assertIsNone(P.tag_commit("v0.3.0", d))


class Checks(unittest.TestCase):
    def test_version(self):
        """v<VERSION>-<hash> of the commit built, the hash at any of git's
        lengths; -dirty, -unknown, the old forms, another commit's hash and
        a mix stop the release."""
        ok = {b: FW for b in P.BOARDS}
        self.assertEqual(P.check_version(ok, COMMIT), FW)
        longer = "v1.2.0-0123456789ab"
        self.assertEqual(P.check_version({b: longer for b in P.BOARDS}, COMMIT), longer)
        for bad in ("v1.2.0-0123456-dirty", "v1.2.0-unknown", "v1.2.0-dirty", "1.2.0", "1.2.0-0123456",
                    "v1.2.0-277b70c", "v1.2.0-01234", "v1.2-0123456", f"v1.2.0-{COMMIT}0"):
            with self.subTest(bad), self.assertRaises(P.PackageError):
                P.check_version({b: bad for b in P.BOARDS}, COMMIT)
        with self.assertRaises(P.PackageError):
            P.check_version(dict(ok, stm32u5g9j_dk2="v1.2.0-0123456789"), COMMIT)

    def test_release_version(self):
        """The names' version: <VERSION> at the release tag, else
        <VERSION>-<hash>; a pre-release tag or another release's tag is no
        release tag."""
        self.assertEqual(P.release_version(FW, ["firmware-v1.2.0"]), "1.2.0")
        self.assertEqual(P.release_version(FW, ["firmware-v1.1.0", "firmware-v1.2.0"]), "1.2.0")
        for tags in ([], ["firmware-v1.2.0-rc1"], ["firmware-v1.1.0"], ["bpc1-r1"]):
            with self.subTest(tags):
                self.assertEqual(P.release_version(FW, tags), "1.2.0-0123456")
        self.assertEqual(P.release_version("v1.2.0-0123456789ab", []), "1.2.0-0123456789ab")

    def test_no_paths(self):
        P.check_no_paths(IMAGES, ["/work/x", "/src/repo"])
        with self.assertRaises(P.PackageError):
            P.check_no_paths(dict(IMAGES, stm32f723e_disco=b"\0/src/repo/firmware/app/main.c\0"),
                             ["/work/x", "/src/repo"])

    def test_warnings(self):
        self.assertEqual(P.build_warnings("[1/2] Building C object a.c.obj\n[2/2] Linking\n"), [])
        out = "[1/2] Building\n/x/a.c:3:5: warning: unused variable 'y' [-Wunused-variable]\n"
        self.assertEqual(len(P.build_warnings(out)), 1)
        self.assertEqual(len(P.build_warnings("CMake Warning at CMakeLists.txt:9:\n")), 1)

    def test_work_of(self):
        self.assertEqual(P.work_of("firmware/app/main.c"), "")
        self.assertEqual(P.work_of("firmware/lib/u2ps/device/u2ps_dev.c"), "firmware/lib/u2ps")
        for rel in ("firmware/lib/tinyusb/src/tusb.c", "firmware/lib/patches/x.patch"):
            with self.assertRaises(P.PackageError):
                P.work_of(rel)
        with self.assertRaises(P.PackageError):
            P.work_of("firmware/lib/newlib/memcpy.c")
        with self.assertRaises(P.PackageError):
            P.work_of("firmware/lib/u2ps2/x.c")


class Copyright(unittest.TestCase):
    def test_lines(self):
        text = """/*
 * Copyright (c) 2019 Ha Thach (tinyusb.org)
 *
 * The above copyright notice and this permission notice shall be included
 */
// Copyright (c) 2026 Mete Balci
/** <h2><center>&copy; Copyright (c) 2019 STMicroelectronics.
  ******************************************************************************
  * Copyright (c) 2021 STMicroelectronics. */
 * Copyright (c) 2009-2021 Arm Limited. All rights reserved.
COMMENT  * Copyright (c) 2018-2026, Frederic Cambus
COPYRIGHT "Copyright (c) 2018-2026, Frederic Cambus"
# Copyright (c) 2026 Mete Balci
"""
        self.assertEqual(P.copyright_lines(text), [
            "Copyright (c) 2019 Ha Thach (tinyusb.org)", "Copyright (c) 2026 Mete Balci",
            "Copyright (c) 2019 STMicroelectronics.", "Copyright (c) 2021 STMicroelectronics.",
            "Copyright (c) 2009-2021 Arm Limited. All rights reserved.",
            "Copyright (c) 2018-2026, Frederic Cambus"])

    def test_embedded_notice(self):
        body = "\n".join((" * " + l).rstrip() for l in BSD3_BODY.splitlines())
        text = f"// SPDX-License-Identifier: MIT AND BSD-3-Clause\n/*\n * Taken from ST.\n *\n" \
               f" * Copyright (c) 2016 STMicroelectronics.\n{body}\n */\nint x;\n"
        self.assertEqual(P.embedded_notice(text),
                         "Copyright (c) 2016 STMicroelectronics.\n" + BSD3_BODY + "\n")
        self.assertIsNone(P.embedded_notice("// Copyright (c) 2026 Mete Balci\n"))
        self.assertIsNone(P.embedded_notice(" * Copyright (c) 2016 STMicroelectronics.\n * no end\n"))

    def test_tinyusb_notice(self):
        body = "\n".join((" * " + l).rstrip() for l in TINYUSB_NOTICE.splitlines())
        text = f"// SPDX-License-Identifier: MIT\n// Copyright (c) 2026 Mete Balci\n" \
               f"// Copyright (c) 2023 Ha Thach (tinyusb.org)\n/*\n * Adapted from TinyUSB.\n *\n{body}\n */\n"
        self.assertEqual(P.tinyusb_notice(text), TINYUSB_NOTICE + "\n")
        self.assertIsNone(P.tinyusb_notice("// Copyright (c) 2023 Ha Thach (tinyusb.org)\n"))
        self.assertIsNone(P.tinyusb_notice(" * The MIT License (MIT)\n *\n * Copyright (c) 2023, Ha Thach (tinyusb.org)\n"))


TINYUSB_NOTICE = """The MIT License (MIT)

Copyright (c) 2023, Ha Thach (tinyusb.org)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE."""


class Notices(unittest.TestCase):
    """collect() and notices() on a small tree laid out like the repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.realpath(self.tmp.name)
        self.files = {
            "LICENSE": "MIT License\n\nCopyright (c) 2026 Mete Balci\n\nPermission ...\n",
            "firmware/app/main.c": "// SPDX-License-Identifier: MIT\n// Copyright (c) 2026 Mete Balci\n",
            "firmware/boards/stm32f723e_disco/panel_st7789h2.c":
                "// SPDX-License-Identifier: MIT AND BSD-3-Clause\n// Copyright (c) 2026 Mete Balci\n"
                "/*\n * Copyright (c) 2016 STMicroelectronics.\n"
                + "\n".join((" * " + l).rstrip() for l in BSD3_BODY.splitlines()) + "\n */\n",
            "firmware/boards/nucleo_u5a5zj_q/board.c":
                "// SPDX-License-Identifier: MIT\n// Copyright (c) 2026 Mete Balci\n"
                "// Copyright (c) 2023 Ha Thach (tinyusb.org)\n/*\n"
                + "\n".join((" * " + l).rstrip() for l in TINYUSB_NOTICE.splitlines()) + "\n */\n",
            "firmware/lib/u2ps/LICENSE": "MIT License\n\nCopyright (c) 2026 Mete Balci\n",
            "firmware/lib/u2ps/device/u2ps_dev.c": "/* SPDX-License-Identifier: MIT\n * Copyright (c) 2026 Mete Balci\n */\n",
            "firmware/lib/u2ps/port/regs.h":
                " * Copyright (c) 2025, Someone Else\n"
                "/** <h2><center>&copy; Copyright (c) 2019 STMicroelectronics.\n"
                "  * licensed by ST under BSD 3-Clause license\n",
            "firmware/lib/spleen/LICENSE": "Copyright (c) 2018-2026, Frederic Cambus\nAll rights reserved.\n",
            "firmware/lib/spleen/spleen-16x32.bdf":
                "STARTFONT 2.1\nCOPYRIGHT \"Copyright (c) 2018-2026, Frederic Cambus\"\n",
            "firmware/lib/stm32u5xx_hal_driver/LICENSE.md": "Copyright 2021 STMicroelectronics.\n",
            "firmware/lib/stm32u5xx_hal_driver/Src/hal.c": " * Copyright (c) 2021 STMicroelectronics.\n",
        }
        for rel, text in self.files.items():
            path = os.path.join(self.src, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        self.subs = {f: (f"https://example.org/{f}", "a" * 40) for f in P.WORKS}

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, rel):
        return os.path.join(self.src, *rel.split("/"))

    def used(self):
        common = ["firmware/app/main.c", "firmware/lib/u2ps/device/u2ps_dev.c",
                  "firmware/lib/u2ps/port/regs.h"]
        return {
            "nucleo_u5a5zj_q": [self.path(r) for r in common + ["firmware/lib/stm32u5xx_hal_driver/Src/hal.c",
                                                                 "firmware/boards/nucleo_u5a5zj_q/board.c"]]
                               + ["/usr/include/newlib/string.h"],
            "stm32f723e_disco": [self.path(r) for r in common + [
                "firmware/boards/stm32f723e_disco/panel_st7789h2.c",
                "firmware/lib/spleen/spleen-16x32.bdf"]],
        }

    def test_sections(self):
        sections = P.collect(self.src, self.used(), self.subs, COMMIT)
        self.assertEqual([s["title"] for s in sections], [
            "Bit-Perfect Checker firmware",
            "STMicroelectronics panel code in this project's board files",
            "TinyUSB code in this project's board files",
            "u2ps (the USB device stack)", "STMicroelectronics STM32U5 HAL", "Spleen (the screens' fonts)"])
        own, st, tu, u2ps, hal, spleen = sections
        self.assertEqual(own["copyrights"], ["Copyright (c) 2026 Mete Balci"])
        self.assertEqual((tu["boards"], tu["licence"], tu["source"]),
                         (["nucleo_u5a5zj_q"], "MIT", "firmware/boards/nucleo_u5a5zj_q/board.c"))
        self.assertEqual(tu["copyrights"], ["Copyright (c) 2023, Ha Thach (tinyusb.org)"])
        self.assertEqual(tu["texts"], [("notice in firmware/boards/nucleo_u5a5zj_q/board.c", TINYUSB_NOTICE + "\n")])
        self.assertEqual(own["boards"], ["nucleo_u5a5zj_q", "stm32f723e_disco"])
        self.assertEqual(own["texts"], [("LICENSE", self.files["LICENSE"])])
        self.assertEqual(st["boards"], ["stm32f723e_disco"])
        self.assertEqual(st["copyrights"], ["Copyright (c) 2016 STMicroelectronics."])
        self.assertEqual(st["texts"][0][1],
                         "Copyright (c) 2016 STMicroelectronics.\n" + BSD3_BODY + "\n")
        self.assertEqual(u2ps["copyrights"], ["Copyright (c) 2019 STMicroelectronics.",
                                              "Copyright (c) 2025, Someone Else",
                                              "Copyright (c) 2026 Mete Balci"])
        self.assertEqual(len(u2ps["notes"]), 1)
        self.assertIn("port/regs.h", u2ps["notes"][0])
        self.assertEqual(u2ps["source"], f"https://example.org/firmware/lib/u2ps, commit {'a' * 40}")
        self.assertEqual(u2ps["texts"][0][1], self.files["firmware/lib/u2ps/LICENSE"])
        self.assertEqual(hal["boards"], ["nucleo_u5a5zj_q"])
        self.assertEqual(spleen["copyrights"], ["Copyright (c) 2018-2026, Frederic Cambus"])

    def test_licence_texts_verbatim(self):
        """Each licence file appears whole in NOTICES.txt, CRLF line ends."""
        data = P.notices(VERSION, P.collect(self.src, self.used(), self.subs, COMMIT))
        text = data.decode("ascii")
        self.assertNotIn("\n", text.replace("\r\n", ""))
        for rel in ("LICENSE", "firmware/lib/u2ps/LICENSE", "firmware/lib/spleen/LICENSE",
                    "firmware/lib/stm32u5xx_hal_driver/LICENSE.md"):
            self.assertIn(self.files[rel].replace("\n", "\r\n"), text)
        self.assertNotIn("CMSIS", text)               # no file of it was used

    def test_tinyusb_line_without_notice(self):
        with open(self.path("firmware/boards/nucleo_u5a5zj_q/board.c"), "w") as f:
            f.write("// SPDX-License-Identifier: MIT\n// Copyright (c) 2023 Ha Thach (tinyusb.org)\n")
        with self.assertRaises(P.PackageError):
            P.collect(self.src, self.used(), self.subs, COMMIT)

    def test_unknown_work(self):
        used = self.used()
        os.makedirs(self.path("firmware/lib/other"))
        with open(self.path("firmware/lib/other/x.c"), "w") as f:
            f.write("int x;\n")
        used["nucleo_u5a5zj_q"].append(self.path("firmware/lib/other/x.c"))
        with self.assertRaises(P.PackageError):
            P.collect(self.src, used, self.subs, COMMIT)

    def test_build_folder(self):
        """A file the build wrote is fine without copyright lines (version.h)
        and stops the script with them: it must have been traced to its
        inputs."""
        os.makedirs(self.path("firmware/build/nucleo_u5a5zj_q"))
        with open(self.path("firmware/build/nucleo_u5a5zj_q/version.h"), "w") as f:
            f.write('#define FW_VERSION "v1.2.0-0123456"\n')
        with open(self.path("firmware/build/nucleo_u5a5zj_q/fonts.c"), "w") as f:
            f.write("// COPYRIGHT \"Copyright (c) 2018-2026, Frederic Cambus\"\n")
        used = self.used()
        used["nucleo_u5a5zj_q"].append(self.path("firmware/build/nucleo_u5a5zj_q/version.h"))
        P.collect(self.src, used, self.subs, COMMIT)
        used["nucleo_u5a5zj_q"].append(self.path("firmware/build/nucleo_u5a5zj_q/fonts.c"))
        with self.assertRaises(P.PackageError):
            P.collect(self.src, used, self.subs, COMMIT)

    def test_bsd3_without_notice(self):
        with open(self.path("firmware/app/main.c"), "w") as f:
            f.write("// SPDX-License-Identifier: MIT AND BSD-3-Clause\n// Copyright (c) 2026 Mete Balci\n")
        with self.assertRaises(P.PackageError):
            P.collect(self.src, self.used(), self.subs, COMMIT)


def ar(members):
    """A GNU ar archive of `members` ({name: bytes}), long names in "//"."""
    longnames, heads = b"", []
    for name in members:
        if len(name) > 15:
            heads.append(f"/{len(longnames)}")
            longnames += name.encode() + b"/\n"
        else:
            heads.append(name + "/")

    def entry(name, body):
        head = f"{name:<16}{0:<12}{0:<6}{0:<6}{644:<8}{len(body):<10}`\n".encode()
        return head + body + (b"\n" if len(body) & 1 else b"")
    out = b"!<arch>\n" + entry("/", b"\0\0\0\0")
    if longnames:
        out += entry("//", longnames)
    for head, body in zip(heads, members.values()):
        out += entry(head, body)
    return out


NEWLIB_A = "/opt/tc/lib/thumb/v8-m.main+fp/hard/libg_nano.a"
NEWLIB_F7 = "/opt/tc/lib/thumb/v7e-m+fp/hard/libg_nano.a"
LIBGCC_A = "/opt/tc/gcc/thumb/v8-m.main+fp/hard/libgcc.a"
MAP = f"""Archive member included to satisfy reference by file (symbol)

libtinyusb.a(tusb.c.obj)      CMakeFiles/x.dir/app/main.c.obj (tusb_rhport_init)
{LIBGCC_A}(_aeabi_uldivmod.o)
                              CMakeFiles/x.dir/app/load.c.obj (__aeabi_uldivmod)
{NEWLIB_A}(libc_a-memcpy.o)
                              CMakeFiles/x.dir/app/log.c.obj (memcpy)
{NEWLIB_A}(libc_a-memcpy.o)
                              CMakeFiles/x.dir/app/stream.c.obj (memcpy)
{NEWLIB_A}(libc_a-strlen-stub.o)
                              CMakeFiles/x.dir/app/log.c.obj (strlen)

Discarded input sections

 .text          0x0 0x0 {NEWLIB_A}(libc_a-strchr.o)
{NEWLIB_A}(libc_a-strchr.o)
"""
ARCHIVES = {
    NEWLIB_A: ar({"libc_a-memcpy.o": b"\x7fELF..../build/newlib/libc/machine/arm/memcpy-armv7m.S\0..",
                  "libc_a-strlen-stub.o": b"\x7fELF..newlib/libc/machine/arm/strlen-stub.c\0",
                  "libc_a-strchr.o": b"\x7fELF..newlib/libc/string/strchr.c\0"}),
    NEWLIB_F7: ar({"libc_a-memcpy.o": b"\x7fELF..newlib/libc/machine/arm/memcpy-armv7m.S\0",
                   "libc_a-strlen.o": b"\x7fELF.. no name"}),
}


class Toolchain(unittest.TestCase):
    def test_linked_members(self):
        self.assertEqual(P.linked_members(MAP), [
            ("libtinyusb.a", "tusb.c.obj"), (LIBGCC_A, "_aeabi_uldivmod.o"),
            (NEWLIB_A, "libc_a-memcpy.o"), (NEWLIB_A, "libc_a-strlen-stub.o")])
        self.assertEqual(P.linked_members("Memory Configuration\n"), [])

    def test_linked_members_spaces(self):
        """An archive path with spaces, its reference on the same line or the next."""
        spaced = "/opt/arm tool chain/lib/thumb/v8-m.main+fp/hard/libg_nano.a"
        text = MAP.replace(NEWLIB_A, spaced)
        self.assertEqual(P.linked_members(text), [
            ("libtinyusb.a", "tusb.c.obj"), (LIBGCC_A, "_aeabi_uldivmod.o"),
            (spaced, "libc_a-memcpy.o"), (spaced, "libc_a-strlen-stub.o")])
        short = f"Archive member included to satisfy reference by file (symbol)\n\n" \
                f"/a b/libc.a(libc_a-memset.o)  CMakeFiles/x.dir/app/log.c.obj (memset)\n\nDiscarded\n"
        self.assertEqual(P.linked_members(short), [("/a b/libc.a", "libc_a-memset.o")])

    def test_linked_members_unmatched(self):
        """A line in the members' part that names no member stops the script."""
        for bad in ("/opt/tc/lib/libg_nano.a[libc_a-memcpy.o]", "libc_a-memcpy.o",
                    "/opt/tc/lib/libg_nano(libc_a-memcpy.o)"):
            text = MAP.replace(f"{NEWLIB_A}(libc_a-strlen-stub.o)", bad)
            with self.subTest(bad), self.assertRaises(P.PackageError):
                P.linked_members(text)

    def test_ar_members(self):
        members = {"short.o": b"abc", "a-much-longer-member-name.o": b"defg", "x.o": b""}
        self.assertEqual(P.ar_members(ar(members)), members)
        with self.assertRaises(P.PackageError):
            P.ar_members(b"\x7fELF")

    def test_routines(self):
        linked = {"nucleo_u5a5zj_q": P.linked_members(MAP),
                  "stm32f723e_disco": [(NEWLIB_F7, "libc_a-memcpy.o"), (NEWLIB_F7, "libc_a-strlen.o")],
                  "stm32u5g9j_dk2": P.linked_members(MAP)}
        routines = P.toolchain_routines(linked, ARCHIVES.__getitem__)
        self.assertEqual(routines, {
            "memcpy": {"newlib/libc/machine/arm/memcpy-armv7m.S":
                       ["nucleo_u5a5zj_q", "stm32f723e_disco", "stm32u5g9j_dk2"]},
            "strlen-stub": {"newlib/libc/machine/arm/strlen-stub.c":
                            ["nucleo_u5a5zj_q", "stm32u5g9j_dk2"]},
            "strlen": {"libc_a-strlen.o (no source named)": ["stm32f723e_disco"]}})

    def test_unknown_toolchain_library(self):
        linked = {"nucleo_u5a5zj_q": [("/opt/tc/lib/libnosys.a", "sbrk.o")]}
        with self.assertRaises(P.PackageError):
            P.toolchain_routines(linked, ARCHIVES.__getitem__)

    def test_section(self):
        licences = "The newlib subdirectory is a collection of software from several sources.\n" \
                   "(1) University of California, Berkeley\n"
        routines = {"memcpy": {"newlib/libc/machine/arm/memcpy-armv7m.S": ["stm32u5g9j_dk2"]}}
        section = P.newlib_section(routines, "4.6.0-1", licences)
        self.assertEqual(section["boards"], ["stm32u5g9j_dk2"])
        text = P.notices(VERSION, [section]).decode("ascii").replace("\r\n", "\n")
        self.assertIn("libnewlib-arm-none-eabi 4.6.0-1", text)
        self.assertIn("  memcpy: newlib/libc/machine/arm/memcpy-armv7m.S (stm32u5g9j_dk2)", text)
        self.assertIn(licences, text)


ARM_SRC = """/*
 * Copyright (c) 2013 ARM Ltd
 * All rights reserved.
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice.
 *
 * THIS SOFTWARE IS PROVIDED BY ARM LTD ``AS IS''
 * EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/* This memcpy routine is optimised for Cortex-M3/M4 cores.  */
"""
LINARO_SRC = """/* Copyright (c) 2010-2011,2013 Linaro Limited
   All rights reserved.

      * Redistributions of source code must retain the above copyright
\tnotice.

   OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

   Assumes:
   ARMv6T2 or ARMv7E-M, AArch32
 */

/* Copyright (c) 2015 ARM Ltd.
   All rights reserved.
   SUCH DAMAGE.  */
"""
PERMISSIVE_SRC = """/*
 * Copyright (C) 2004 CodeSourcery, LLC
 *
 * Permission to use, copy, modify, and distribute this file.
 */

/* Handle ELF .{pre_init,init,fini}_array sections.  */
"""
PLAIN_SRC = """/*
FUNCTION
\t<<memset>>---set an area of memory
*/
"""
COPYING = """The newlib subdirectory is a collection of software from several sources.

Each file may have its own copyright/license that is embedded in the source
file.  Unless otherwise noted in the body of the source file(s), the following copyright
notices will apply to the contents of the newlib subdirectory:

(1) Red Hat Incorporated

Copyright (c) 1994-2009  Red Hat, Inc. All rights reserved.

This copyrighted material is made available to anyone wishing to use.

(2) University of California, Berkeley

Copyright (c) 1990 The Regents of the University of California.
"""


class NewlibNotices(unittest.TestCase):
    def test_source_notices(self):
        self.assertEqual(P.source_notices(ARM_SRC), [
            "Copyright (c) 2013 ARM Ltd\nAll rights reserved.\n\n1. Redistributions of source "
            "code must retain the above copyright\n   notice.\n\nTHIS SOFTWARE IS PROVIDED BY "
            "ARM LTD ``AS IS''\nEVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.\n"])
        self.assertEqual(P.source_notices(LINARO_SRC), [
            "Copyright (c) 2010-2011,2013 Linaro Limited\nAll rights reserved.\n\n"
            "   * Redistributions of source code must retain the above copyright\n     notice.\n\n"
            "OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.\n",
            "Copyright (c) 2015 ARM Ltd.\nAll rights reserved.\nSUCH DAMAGE.\n"])
        self.assertEqual(P.source_notices(PERMISSIVE_SRC), [
            "Copyright (C) 2004 CodeSourcery, LLC\n\n"
            "Permission to use, copy, modify, and distribute this file.\n"])
        self.assertEqual(P.source_notices(PLAIN_SRC), [])

    def test_copying_default(self):
        text = P.copying_default(COPYING)
        self.assertTrue(text.startswith("The newlib subdirectory is a collection"))
        self.assertIn("(1) Red Hat Incorporated\n\nCopyright (c) 1994-2009  Red Hat", text)
        self.assertTrue(text.endswith("anyone wishing to use.\n"))
        self.assertNotIn("Berkeley", text)
        with self.assertRaises(P.PackageError):
            P.copying_default("(2) University of California, Berkeley\n")

    def test_tag_and_package(self):
        """--newlib-tag's release must begin the package's version."""
        for tag, version in (("newlib-4.6.0", "4.6.0.20260123-1"), ("newlib-4.6.0", "4.6.0-1"),
                             ("newlib-4.6.0", "4.6.0"), ("newlib-4.5.0", "1:4.5.0+dfsg-2")):
            with self.subTest(tag=tag, version=version):
                P.check_newlib_tag(tag, version)
        for tag, version in (("newlib-4.5.0", "4.6.0.20260123-1"), ("newlib-4.6", "4.6.0-1"),
                             ("newlib-4.6.0", "4.6.01-1"), ("newlib-4.6.0", "14.6.0-1"),
                             ("4.6.0", "4.6.0-1"), ("newlib-4.6.0x", "4.6.0x-1"),
                             ("newlib-4.6.0", "4.5.0.20240101-1")):
            with self.subTest(tag=tag, version=version), self.assertRaises(P.PackageError):
                P.check_newlib_tag(tag, version)

    def test_sha256(self):
        """The download's files against its sha256 list: each listed file
        matches, and every file read is listed."""
        with tempfile.TemporaryDirectory() as top:
            d = os.path.join(top, "newlib")
            with open(os.path.join(top, "outside.c"), "w") as f:
                f.write(PLAIN_SRC)
            files = {"COPYING.NEWLIB": COPYING, "newlib/libc/string/memset.c": PLAIN_SRC,
                     "newlib/libc/machine/arm/memcpy-armv7m.S": ARM_SRC}
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
                with open(os.path.join(d, rel), "w") as f:
                    f.write(text)
            sums = "".join(f"{hashlib.sha256(t.encode()).hexdigest()}  ./{r}\n" for r, t in files.items())
            P.check_sha256(d, sums, list(files))
            P.check_sha256(d, sums.replace("  ./", " *"), list(files))
            with open(os.path.join(d, "newlib/libc/string/memset.c"), "a") as f:
                f.write("/* changed */\n")
            with self.assertRaises(P.PackageError):
                P.check_sha256(d, sums, list(files))            # a file differs
            ok = sums.replace(hashlib.sha256(PLAIN_SRC.encode()).hexdigest(), hashlib.sha256(
                (PLAIN_SRC + "/* changed */\n").encode()).hexdigest())
            P.check_sha256(d, ok, list(files))
            with self.assertRaises(P.PackageError):
                P.check_sha256(d, ok, list(files) + ["newlib/libc/string/strchr.c"])  # not listed
            outside = hashlib.sha256(PLAIN_SRC.encode()).hexdigest()
            for bad in (ok + "0" * 64 + "  ./newlib/libc/string/strchr.c\n",          # missing
                        ok + outside + "  ../outside.c\n", ok + outside + f"  {top}/outside.c\n",
                        ok + "not a sum line\n"):
                with self.subTest(bad.splitlines()[-1]), self.assertRaises(P.PackageError):
                    P.check_sha256(d, bad, list(files))

    def test_upstream(self):
        """newlib's sources from the folder: the tag checked against the
        package version, the files against the sha256 list when one is given,
        a missing source stops the script."""
        routines = {"memcpy": {"newlib/libc/machine/arm/memcpy-armv7m.S": ["nucleo_u5a5zj_q"]},
                    "strlen": {"libc_a-strlen.o (no source named)": ["stm32f723e_disco"]}}
        files = {"COPYING.NEWLIB": COPYING, "newlib/libc/machine/arm/memcpy-armv7m.S": ARM_SRC}
        with tempfile.TemporaryDirectory() as d:
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
                with open(os.path.join(d, rel), "w") as f:
                    f.write(text)
            sums = "".join(f"{hashlib.sha256(t.encode()).hexdigest()}  ./{r}\n" for r, t in files.items())
            expected = ("newlib-4.6.0", {"newlib/libc/machine/arm/memcpy-armv7m.S": ARM_SRC}, COPYING)
            self.assertEqual(P.newlib_upstream(routines, "4.6.0.20260123-1", "newlib-4.6.0", d), expected)
            self.assertEqual(P.newlib_upstream(routines, "4.6.0.20260123-1", "newlib-4.6.0", d, sums),
                             expected)
            with self.assertRaises(P.PackageError):
                P.newlib_upstream(routines, "4.5.0.20250101-1", "newlib-4.6.0", d, sums)
            with self.assertRaises(P.PackageError):
                P.newlib_upstream(routines, "4.6.0.20260123-1", "newlib-4.6.0", d, sums.splitlines()[0])
            with open(os.path.join(d, "COPYING.NEWLIB"), "a") as f:
                f.write("changed\n")
            P.newlib_upstream(routines, "4.6.0.20260123-1", "newlib-4.6.0", d)
            with self.assertRaises(P.PackageError):
                P.newlib_upstream(routines, "4.6.0.20260123-1", "newlib-4.6.0", d, sums)
            os.remove(os.path.join(d, "newlib/libc/machine/arm/memcpy-armv7m.S"))
            with self.assertRaises(P.PackageError):
                P.newlib_upstream(routines, "4.6.0.20260123-1", "newlib-4.6.0", d)

    def test_section_with_sources(self):
        routines = {
            "memcpy": {"newlib/libc/machine/arm/memcpy-armv7m.S": ["nucleo_u5a5zj_q"]},
            "memset": {"newlib/libc/string/memset.c": ["nucleo_u5a5zj_q"]},
            "strchr": {"newlib/libc/string/strchr.c": ["stm32f723e_disco"]},
            "strlen": {"newlib/libc/machine/arm/strlen-armv7.S": ["stm32f723e_disco"]}}
        files = {"newlib/libc/machine/arm/memcpy-armv7m.S": ARM_SRC,
                 "newlib/libc/string/memset.c": PLAIN_SRC, "newlib/libc/string/strchr.c": PLAIN_SRC,
                 "newlib/libc/machine/arm/strlen-armv7.S": LINARO_SRC}
        section = P.newlib_section(routines, "4.6.0-1", "DEBIAN LIST\n",
                                   ("newlib-4.6.0", files, COPYING))
        self.assertIn("tag newlib-4.6.0", section["source"])
        notes = "\n".join(section["notes"])
        self.assertIn("  memcpy: newlib/libc/machine/arm/memcpy-armv7m.S (nucleo_u5a5zj_q)\n"
                      "      covered by its own notice: Copyright (c) 2013 ARM Ltd", notes)
        self.assertIn("  memset: newlib/libc/string/memset.c (nucleo_u5a5zj_q)\n"
                      "      no notice in the file: COPYING.NEWLIB (1) covers it", notes)
        self.assertIn("      covered by its own notice: Copyright (c) 2010-2011,2013 Linaro Limited\n"
                      "      covered by its own notice: Copyright (c) 2015 ARM Ltd.", notes)
        headings = [h for h, _ in section["texts"]]
        self.assertEqual(headings, [
            "notice in newlib/libc/machine/arm/memcpy-armv7m.S, newlib tag newlib-4.6.0",
            "COPYING.NEWLIB (1), the default notice, newlib tag newlib-4.6.0",
            "notice in newlib/libc/machine/arm/strlen-armv7.S, newlib tag newlib-4.6.0",
            "notice in newlib/libc/machine/arm/strlen-armv7.S, newlib tag newlib-4.6.0",
            "newlib's licence list, the copyright file of libnewlib-arm-none-eabi 4.6.0-1"])
        self.assertEqual(section["texts"][-1][1], "DEBIAN LIST\n")
        text = P.notices(VERSION, [section]).decode("ascii")
        self.assertIn("Copyright (c) 2015 ARM Ltd.\r\nAll rights reserved.", text)


NINJA = shutil.which("ninja")
CC = shutil.which("gcc") or shutil.which("cc")
TOY_NINJA = """rule gen
  command = sed -e 's|^|// |' $in > $out
rule version
  command = printf '#define FW_VERSION "v1.2.0-0123456"\\n' > $out
rule cc
  command = {cc} -nostdinc -MD -MF $out.d -I../../include -I. -c $in -o $out
  depfile = $out.d
  deps = gcc
build version.h: version
build fonts.c: gen ../../lib/spleen/spleen-8x16.bdf
build fonts.o: cc fonts.c
build main.o: cc ../../app/main.c || version.h
"""


@unittest.skipUnless(NINJA and CC, "no ninja or no C compiler")
class ToyBuild(unittest.TestCase):
    """used_files() and collect() on a small Ninja build in a tree laid out
    like the clone: a source that includes a header by a path relative to
    the build folder, a source the build makes from a font, and a header the
    build writes from nothing (as version.h)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.realpath(self.tmp.name)
        self.build = os.path.join(self.src, "firmware", "build", "nucleo_u5a5zj_q")
        files = {
            "LICENSE": "MIT License\n\nCopyright (c) 2026 Mete Balci\n",
            "firmware/app/main.c": '// Copyright (c) 2026 Mete Balci\n#include "toy.h"\n#include "version.h"\n'
                                   "int main(void) { return TOY; }\n",
            "firmware/include/toy.h": "// Copyright (c) 2026 Mete Balci\n#define TOY 0\n",
            "firmware/lib/spleen/LICENSE": "Copyright (c) 2018-2026, Frederic Cambus\n",
            "firmware/lib/spleen/spleen-8x16.bdf":
                'STARTFONT 2.1\nCOPYRIGHT "Copyright (c) 2018-2026, Frederic Cambus"\nENDFONT\n',
            "firmware/build/nucleo_u5a5zj_q/build.ninja": TOY_NINJA.format(cc=CC),
            "firmware/build/nucleo_u5a5zj_q/compile_commands.json": json.dumps([
                {"directory": self.build, "file": "../../app/main.c", "command": "cc main.c"},
                {"directory": self.build, "file": "fonts.c", "command": "cc fonts.c"}]),
        }
        for rel, text in files.items():
            path = os.path.join(self.src, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        r = subprocess.run([NINJA, "-C", self.build], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, rel):
        return os.path.join(self.src, *rel.split("/"))

    def test_used_files(self):
        """The sources and headers by their real paths (the header's
        dependency is relative to the build folder), the font in place of the
        source made from it, the written header itself."""
        self.assertEqual(P.used_files(self.build, NINJA), sorted([
            self.path("firmware/app/main.c"), self.path("firmware/include/toy.h"),
            self.path("firmware/lib/spleen/spleen-8x16.bdf"),
            self.path("firmware/build/nucleo_u5a5zj_q/version.h")]))

    def test_notices(self):
        """The font's copyright line under Spleen, none under this project."""
        subs = {f: (f"https://example.org/{f}", "a" * 40) for f in P.WORKS}
        used = {"nucleo_u5a5zj_q": P.used_files(self.build, NINJA)}
        sections = P.collect(self.src, used, subs, COMMIT)
        self.assertEqual([(s["title"], s["copyrights"]) for s in sections], [
            ("Bit-Perfect Checker firmware", ["Copyright (c) 2026 Mete Balci"]),
            ("Spleen (the screens' fonts)", ["Copyright (c) 2018-2026, Frederic Cambus"])])


if __name__ == "__main__":
    unittest.main()
