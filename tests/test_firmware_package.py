# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The firmware release zip (firmware/tools/package.py), its parts that need
no toolchain: the zip's bytes and contents from given images, README.txt,
NOTICES.txt from a small source tree, and the checks that stop a release.
The build itself (clone, CMake, Ninja) is not run here."""

import hashlib
import importlib.util
import io
import os
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "firmware_package", os.path.join(ROOT, "firmware", "tools", "package.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

VERSION = "1.1.0"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
DATE = (2026, 11, 5, 12, 34, 56)
IMAGES = {b: bytes([i]) * (1000 + i) + b"image " + b.encode() for i, b in enumerate(P.BOARDS)}
NOTICES = b"notices\r\n"
TOP = f"bit-perfect-checker-firmware-{VERSION}"
BSD3_BODY = """All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""


def build_archive(images=IMAGES, version=VERSION, date=DATE, notices=NOTICES):
    return P.archive(version, COMMIT, date, images, notices)


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
        self.assertEqual(z.read(f"{TOP}/README.txt"), P.readme(VERSION, COMMIT))

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


class Readme(unittest.TestCase):
    def test_text(self):
        data = P.readme("1.1.0-abc1234", COMMIT)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        text = data.decode("ascii")
        for b in P.BOARDS:
            self.assertIn(f"bpc-{b}-1.1.0-abc1234.bin", text)
        for word in ("NUCLEO-U5A5ZJ-Q", "STM32F723E-DISCO", "STM32U5G9J-DK2", "0x08000000",
                     "sn=<serial number>", "-w bpc-<board>-1.1.0-abc1234.bin 0x08000000",
                     "STM32_Programmer_CLI -l st-link", "NOD_U5A5ZJ", "DIS_F723IE",
                     "16C0:27E0", "bpc.metebalci.com:", COMMIT,
                     "https://github.com/metebalci/bit-perfect-checker",
                     "https://bpc.metebalci.com", "NOTICES.txt", "SHA256SUMS"):
            self.assertIn(word, text)
        self.assertIn("STM32U5G9J-DK2's ST-LINK shows no drive", text)


class Checks(unittest.TestCase):
    def test_version(self):
        ok = {b: "1.1.0" for b in P.BOARDS}
        self.assertEqual(P.check_version(ok), "1.1.0")
        self.assertEqual(P.check_version({b: "1.1.0-277b70c" for b in P.BOARDS}), "1.1.0-277b70c")
        for bad in ("1.1.0-dirty", "1.1.0-277b70c-dirty", "1.1.0-unknown"):
            with self.subTest(bad), self.assertRaises(P.PackageError):
                P.check_version({b: bad for b in P.BOARDS})
        with self.assertRaises(P.PackageError):
            P.check_version(dict(ok, stm32u5g9j_dk2="1.1.0-277b70c"))

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
        self.assertEqual(P.work_of("firmware/lib/patches/tinyusb-audio-out-zlp.patch"), "")
        self.assertEqual(P.work_of("firmware/lib/tinyusb/src/tusb.c"), "firmware/lib/tinyusb")
        with self.assertRaises(P.PackageError):
            P.work_of("firmware/lib/newlib/memcpy.c")
        with self.assertRaises(P.PackageError):
            P.work_of("firmware/lib/tinyusb2/x.c")


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
            "firmware/lib/tinyusb/LICENSE": "The MIT License (MIT)\n\nCopyright (c) 2012-2026, hathach\n",
            "firmware/lib/tinyusb/src/tusb.c": "/*\n * Copyright (c) 2019 Ha Thach (tinyusb.org)\n */\n",
            "firmware/lib/tinyusb/src/dwc2_type.h":
                " * Copyright (c) 2024, hathach (tinyusb.org)\n"
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
        common = ["firmware/app/main.c", "firmware/lib/tinyusb/src/tusb.c",
                  "firmware/lib/tinyusb/src/dwc2_type.h"]
        return {
            "nucleo_u5a5zj_q": [self.path(r) for r in common + ["firmware/lib/stm32u5xx_hal_driver/Src/hal.c"]]
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
            "TinyUSB", "STMicroelectronics STM32U5 HAL", "Spleen (the screens' fonts)"])
        own, st, tusb, hal, spleen = sections
        self.assertEqual(own["copyrights"], ["Copyright (c) 2026 Mete Balci"])
        self.assertEqual(own["boards"], ["nucleo_u5a5zj_q", "stm32f723e_disco"])
        self.assertEqual(own["texts"], [("LICENSE", self.files["LICENSE"])])
        self.assertEqual(st["boards"], ["stm32f723e_disco"])
        self.assertEqual(st["copyrights"], ["Copyright (c) 2016 STMicroelectronics."])
        self.assertEqual(st["texts"][0][1],
                         "Copyright (c) 2016 STMicroelectronics.\n" + BSD3_BODY + "\n")
        self.assertEqual(tusb["copyrights"], ["Copyright (c) 2019 Ha Thach (tinyusb.org)",
                                              "Copyright (c) 2019 STMicroelectronics.",
                                              "Copyright (c) 2024, hathach (tinyusb.org)"])
        self.assertEqual(len(tusb["notes"]), 1)
        self.assertIn("src/dwc2_type.h", tusb["notes"][0])
        self.assertEqual(tusb["source"], f"https://example.org/firmware/lib/tinyusb, commit {'a' * 40}")
        self.assertEqual(tusb["texts"][0][1], self.files["firmware/lib/tinyusb/LICENSE"])
        self.assertEqual(hal["boards"], ["nucleo_u5a5zj_q"])
        self.assertEqual(spleen["copyrights"], ["Copyright (c) 2018-2026, Frederic Cambus"])

    def test_licence_texts_verbatim(self):
        """Each licence file appears whole in NOTICES.txt, CRLF line ends."""
        data = P.notices(VERSION, P.collect(self.src, self.used(), self.subs, COMMIT))
        text = data.decode("ascii")
        self.assertNotIn("\n", text.replace("\r\n", ""))
        for rel in ("LICENSE", "firmware/lib/tinyusb/LICENSE", "firmware/lib/spleen/LICENSE",
                    "firmware/lib/stm32u5xx_hal_driver/LICENSE.md"):
            self.assertIn(self.files[rel].replace("\n", "\r\n"), text)
        self.assertNotIn("CMSIS", text)               # no file of it was used

    def test_unknown_work(self):
        used = self.used()
        os.makedirs(self.path("firmware/lib/other"))
        with open(self.path("firmware/lib/other/x.c"), "w") as f:
            f.write("int x;\n")
        used["nucleo_u5a5zj_q"].append(self.path("firmware/lib/other/x.c"))
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


if __name__ == "__main__":
    unittest.main()
