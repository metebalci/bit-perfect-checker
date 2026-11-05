# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The firmware release zip: every board built from a clean clone of one
commit, with a README, the licence notices and checksums.

    python3 -I firmware/tools/package.py --work <dir> [--rev <commit or tag>]
                                         [--dist <dir>] [--ninja <path>]

clones this repository at --rev (default HEAD) into <dir>, which must be
empty or missing, with the firmware's submodules at their pinned commits
(from this checkout's submodules when they hold the commit, else from their
upstream URLs), builds each board in BOARDS with the build's defaults, and
writes <dist>/bit-perfect-checker-firmware-<version>.zip (default dist:
firmware/build/release) and <dist>/SHA256SUMS, which lists every zip there.
The version is what the device reports, from the build's version.h:
1.1.0 at the tag firmware-v1.1.0, 1.1.0-<hash> at another commit. The
script stops on a build warning, a version that is -dirty or -unknown, and
an image that holds a path of this machine.

The archive has one top-level folder, named like the zip, with
bpc-<board>-<version>.bin per board (the flash image for 0x08000000),
README.txt, NOTICES.txt and SHA256SUMS (the .bin files). NOTICES.txt holds,
for this project and every work that code or data in the images comes from,
the copyright lines found in the files the build read and the work's licence
text as its pinned submodule ships it. The files the build read are its
sources and the headers they include (from Ninja's dependency log), with a
generated file replaced by its inputs. The toolchain's C library (newlib)
gets a section of its own: the routines the linker took from it (from each
board's map file, with the source file each object names) and the licence
list its package ships, by default Debian's copyright file of
libnewlib-arm-none-eabi, which must own the archives linked
(--newlib-licence and --newlib-version name another), and, from newlib's
own sources at the release that matches the toolchain (--newlib-source, a
folder with COPYING.NEWLIB and each routine's source file at its path in the
newlib repository; --newlib-tag names the release), the notice each source
file carries, or COPYING.NEWLIB's default notice for a file without one.
libgcc's routines get none.

Determinism, as in generator/package.py: entries sorted by name, the
commit's date for every entry, Unix permissions 0644 (files) and 0755
(folders), no comments or extra fields; the archive is stored, so its bytes
do not depend on the zlib version. A re-run from the same commit with the
same toolchain gives the same zip.
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_URL = "https://github.com/metebalci/bit-perfect-checker"
WEBSITE = "https://bpc.metebalci.com"
NAME = "bit-perfect-checker-firmware"
FLASH_ADDRESS = 0x08000000
USB_ID = "16C0:27E0"
SERIAL_PREFIX = "bpc.metebalci.com:"
VERSION_RE = re.compile(r'^#define FW_VERSION "([^"]+)"$', re.M)

# Board folder in firmware/boards/: (board name, the ST-LINK's USB drive or None)
BOARDS = {
    "nucleo_u5a5zj_q": ("NUCLEO-U5A5ZJ-Q", "NOD_U5A5ZJ"),
    "stm32f723e_disco": ("STM32F723E-DISCO", "DIS_F723IE"),
    "stm32u5g9j_dk2": ("STM32U5G9J-DK2", None),
}

# The works whose files a build may use, by folder: (title, licence, licence
# file in that folder). A file in firmware/lib/ outside these folders stops
# the script, so that a new submodule cannot enter the images unnoticed.
WORKS = {
    "firmware/lib/tinyusb": ("TinyUSB", "MIT", "LICENSE"),
    "firmware/lib/CMSIS_5": ("Arm CMSIS 5 (CMSIS-Core)", "Apache-2.0", "LICENSE.txt"),
    "firmware/lib/cmsis_device_u5": ("STMicroelectronics CMSIS device files for the STM32U5",
                                     "Apache-2.0", "LICENSE.md"),
    "firmware/lib/cmsis_device_f7": ("STMicroelectronics CMSIS device files for the STM32F7",
                                     "Apache-2.0", "LICENSE.md"),
    "firmware/lib/stm32u5xx_hal_driver": ("STMicroelectronics STM32U5 HAL", "BSD-3-Clause",
                                          "LICENSE.md"),
    "firmware/lib/stm32f7xx_hal_driver": ("STMicroelectronics STM32F7 HAL", "BSD-3-Clause",
                                          "LICENSE.md"),
    "firmware/lib/spleen": ("Spleen (the screens' fonts)", "BSD-2-Clause", "LICENSE"),
}
NEWLIB_PACKAGE = "libnewlib-arm-none-eabi"
NEWLIB_REPO = "https://sourceware.org/git/newlib-cygwin.git"
NEWLIB_ARCHIVE_RE = re.compile(r"^lib[cgm](_nano)?\.a$")
LIBGCC = "libgcc.a"
SOURCE_RE = re.compile(rb"newlib/[A-Za-z0-9_./+-]+\.[cS]")
MEMBER_RE = re.compile(r"^(\S+\.a)\(([^)]+)\)")
LIB = "firmware/lib/"
PATCHES = "firmware/lib/patches/"            # this project's own files

# Environment variables a CMake build reads at its first configure
BUILD_ENV_DROP = ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "ASMFLAGS", "ASM_FLAGS",
                  "CC", "CXX", "ASM", "MAKEFLAGS", "NINJA_STATUS")

BSD3_RE = re.compile(r"BSD[- ]3-Clause")
COPYRIGHT_RE = re.compile(r"Copyright\s+(\(c\)|\(C\)|&copy;|©)?\s*\d{4}")
NOTICE_START_RE = re.compile(r"Copyright \(c\) \d{4}(-\d{4})? STMicroelectronics\.")
NOTICE_END = "POSSIBILITY OF SUCH DAMAGE."


class PackageError(Exception):
    pass


# --- pure parts ----------------------------------------------------------------

def copyright_lines(text):
    """The copyright lines of a source, BDF or licence text, without comment
    leaders, HTML or BDF quoting, in order of appearance."""
    out = []
    for line in text.splitlines():
        if not COPYRIGHT_RE.search(line):
            continue
        line = re.sub(r"<[^>]*>", "", line).replace("&copy;", "")
        line = line.strip()
        if line.startswith("COPYRIGHT "):                  # a BDF property
            line = line[len("COPYRIGHT "):].strip().strip('"')
        elif line.startswith("COMMENT "):                  # a BDF comment
            line = line[len("COMMENT "):].strip()
        line = re.sub(r"^(//+|/\*+|\*+|#+|;+|@+|--)\s*", "", line)
        line = re.sub(r"\s*\*+/$", "", line).strip()
        if line not in out:
            out.append(line)
    return out


def embedded_notice(text):
    """The STMicroelectronics BSD-3-Clause notice held in a comment of one of
    this project's files, from its copyright line to the end of the
    disclaimer, without the comment leaders; None when there is none."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if NOTICE_START_RE.search(line):
            for j in range(i, len(lines)):
                if NOTICE_END in lines[j]:
                    block = [re.sub(r"^\s*\*( |$)", "", l).rstrip() for l in lines[i:j + 1]]
                    return "\n".join(block) + "\n"
            return None
    return None


def spdx(text):
    m = re.search(r"SPDX-License-Identifier:\s*(.+)", text)
    return m.group(1).strip() if m else None


def work_of(rel):
    """The WORKS folder of a repository-relative path, "" for this project."""
    if rel.startswith(LIB) and not rel.startswith(PATCHES):
        for folder in WORKS:
            if rel == folder or rel.startswith(folder + "/"):
                return folder
        raise PackageError(f"{rel}: in {LIB} but in none of the known works; add it to WORKS")
    return ""


def check_version(versions):
    """The one version of all boards' builds; a -dirty, -unknown or mixed
    version stops the release."""
    found = set(versions.values())
    if len(found) != 1:
        raise PackageError(f"the boards report different versions: {versions}")
    version = found.pop()
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9a-f]{7,})?", version):
        raise PackageError(f"version {version}: not a release or a clean commit's pre-release")
    return version


def check_no_paths(images, paths):
    """No image holds one of `paths` (this machine's folders)."""
    for name, data in sorted(images.items()):
        for p in paths:
            if p and p.encode() in data:
                raise PackageError(f"{name} holds the local path {p}")


def build_warnings(output):
    """The lines of a configure or build output that report a warning."""
    return [l for l in output.splitlines() if "warning" in l.lower()]


def bin_name(board, version):
    return f"bpc-{board}-{version}.bin"


def readme(version, commit, boards=BOARDS):
    width = max(len(bin_name(b, version)) for b in boards) + 2
    rows = "\n".join(f"{bin_name(b, version):<{width}}{name}" for b, (name, _) in boards.items())
    rows += f"\n{'NOTICES.txt':<{width}}licences and copyright notices of the code in the images"
    rows += f"\n{'SHA256SUMS':<{width}}the SHA-256 of every .bin file"
    drives = [f"{drive} on the {name}" for name, drive in boards.values() if drive]
    no_drive = [name for name, drive in boards.values() if not drive]
    drive_text = ("Without STM32CubeProgrammer, copy the board's .bin file to its ST-LINK's\n"
                  f"USB drive: {', '.join(drives)}.")
    if no_drive:
        drive_text += (f"\nThe {' and the '.join(no_drive)}'s ST-LINK shows no drive; flash it "
                       "with\nSTM32CubeProgrammer.")
    title = f"Bit-Perfect Checker firmware {version}"
    text = f"""{title}
{"=" * len(title)}

Firmware for the Bit-Perfect Checker: a USB Audio Class 2 device that
receives the BPC1 test signal and reports whether it arrived bit-perfect.
Built from commit {commit} of
{REPO_URL}


Files
-----

{rows}

Each .bin file is the board's flash image, for address 0x{FLASH_ADDRESS:08X}.


Flashing
--------

Each board has two USB ports: the ST-LINK's, which flashes it and carries
the serial log, and the user port, which goes to the host under test.
Connect the ST-LINK's port. With ST's STM32CubeProgrammer, list the
connected ST-LINKs with their serial numbers:

  STM32_Programmer_CLI -l st-link

and flash the board's file to the ST-LINK with that serial number:

  STM32_Programmer_CLI -c port=SWD sn=<serial number> mode=UR -w {bin_name("<board>", version)} 0x{FLASH_ADDRESS:08X} -v -rst

{drive_text}


USB ID
------

The device uses 16C0:27E0, Objective Development's shared VID/PID pair for
audio class devices. Devices that share the pair tell themselves apart by
their serial number: this firmware's starts with {SERIAL_PREFIX} followed
by the chip's unique ID as 24 hex digits.


More
----

Website: {WEBSITE}
Source, building, the serial log and its commands: {REPO_URL}
(firmware/README.md)

The firmware is under the MIT licence. NOTICES.txt holds the licences and
copyright notices of everything in the images.
"""
    return text.replace("\n", "\r\n").encode("ascii")


def notices(version, sections):
    """NOTICES.txt from `sections`, each a dict with title, source, boards,
    licence, copyrights (lines), texts ((heading, text) pairs) and
    optionally notes (lines)."""
    title = f"Notices for Bit-Perfect Checker firmware {version}"
    out = [title, "=" * len(title), "",
           "The firmware images (.bin files) in this archive hold code and data from",
           "the works below. For each, the copyright lines found in the files the",
           "build read, then its licence text as the work ships it.", ""]
    for n, s in enumerate(sections, 1):
        head = f"{n}. {s['title']}"
        out += ["", head, "-" * len(head), "",
                f"Source:   {s['source']}",
                f"Images:   {', '.join(s['boards'])}",
                f"Licence:  {s['licence']}", ""]
        if s.get("notes"):
            out += s["notes"] + [""]
        if s["copyrights"]:
            out += s["copyrights"] + [""]
        for heading, text in s["texts"]:
            out += [f"--- {heading} ---", "", text.rstrip("\n"), ""]
    text = "\n".join(out).rstrip("\n") + "\n"
    try:
        return text.replace("\n", "\r\n").encode("ascii")
    except UnicodeEncodeError as e:
        raise PackageError(f"NOTICES.txt: not ASCII: {e}") from e


def linked_members(map_text):
    """(archive, member) of every archive member a GNU ld map file says the
    linker included, in the map's order."""
    out, inside = [], False
    for line in map_text.splitlines():
        if line.startswith("Archive member included"):
            inside = True
        elif inside:
            m = MEMBER_RE.match(line)
            if m and (m.group(1), m.group(2)) not in out:
                out.append((m.group(1), m.group(2)))
            elif line[:1].isalpha():
                break                    # the next part of the map
    return out


def ar_members(data):
    """{name: bytes} of a Unix ar archive (GNU long names included)."""
    if not data.startswith(b"!<arch>\n"):
        raise PackageError("not an ar archive")
    out, names, i = {}, b"", 8
    while i + 60 <= len(data):
        head = data[i:i + 60]
        name, size = head[:16].decode("ascii").rstrip(), int(head[48:58])
        body = data[i + 60:i + 60 + size]
        i += 60 + size + (size & 1)
        if name == "//":
            names = body
        elif name.startswith("/") and name[1:].isdigit():
            start = int(name[1:])
            out[names[start:names.index(b"/\n", start)].decode("ascii")] = body
        elif name not in ("/", "/SYM64/"):
            out[name.rstrip("/")] = body
    return out


def object_source(data):
    """The newlib source file an object's debug information names, or None."""
    m = SOURCE_RE.search(data)
    return m.group(0).decode("ascii") if m else None


def toolchain_routines(linked, read_archive=None):
    """{routine: {source: [boards]}} of the newlib routines in `linked`
    ({board: [(archive, member)]}); libgcc's are left out, and an archive
    of the toolchain that is neither stops the script."""
    read_archive = read_archive or (lambda p: open(p, "rb").read())
    cache, out = {}, {}
    for board in BOARDS:
        for archive_path, member in linked.get(board, ()):
            if not os.path.isabs(archive_path):
                continue                 # this project's own libraries
            base = os.path.basename(archive_path)
            if base == LIBGCC:
                continue
            if not NEWLIB_ARCHIVE_RE.match(base):
                raise PackageError(f"{archive_path}: a toolchain library NOTICES.txt does not know")
            if archive_path not in cache:
                cache[archive_path] = ar_members(read_archive(archive_path))
            source = object_source(cache[archive_path][member]) or f"{member} (no source named)"
            routine = re.sub(r"^lib[a-z]_a-|\.o$", "", member)
            boards = out.setdefault(routine, {}).setdefault(source, [])
            if board not in boards:
                boards.append(board)
    return out


def source_notices(text):
    """The copyright notices in a source file's comments: each from its
    copyright line to the end of its comment, or of its disclaimer (a line
    ending in "SUCH DAMAGE."), without comment leaders."""
    out = []
    for m in re.finditer(r"/\*(.*?)\*/", text, re.S):
        lines = m.group(1).expandtabs(8).split("\n")
        start = next((i for i, l in enumerate(lines) if COPYRIGHT_RE.search(l)), None)
        if start is None:
            continue
        lines = lines[start:]
        end = next((i for i, l in enumerate(lines) if l.rstrip().endswith("SUCH DAMAGE.")), None)
        if end is not None:
            lines = lines[:end + 1]
        first = lines[0][COPYRIGHT_RE.search(lines[0]).start():]
        rest = lines[1:]
        if all(re.match(r"^\s*\*( |$)", l) for l in rest if l.strip()):
            rest = [re.sub(r"^\s*\*( |$)", "", l) for l in rest]
        else:
            rest = textwrap.dedent("\n".join(rest)).split("\n")
        block = "\n".join([first] + [l.rstrip() for l in rest]).strip("\n")
        out.append(block + "\n")
    return out


def copying_default(copying):
    """COPYING.NEWLIB's preamble and its notice (1), which covers a newlib
    file that names no other, as the file has them."""
    m = re.search(r"^\(2\) ", copying, re.M)
    head = copying[:m.start()] if m else ""
    if "\n(1) " not in head or not COPYRIGHT_RE.search(head):
        raise PackageError("COPYING.NEWLIB: no default notice (1)")
    return head.rstrip() + "\n"


def newlib_section(routines, version, text, upstream=None):
    """The NOTICES.txt section of newlib from toolchain_routines(), the
    licence list of newlib's package `version` and, if given, `upstream`:
    (release tag, {source path: text}, COPYING.NEWLIB) of newlib itself."""
    boards = [b for b in BOARDS if any(b in bs for src in routines.values() for bs in src.values())]
    notes = ["The images hold these routines of newlib-nano, each built from the source"
             "\nfile its object names:"]
    texts, unknown = [], False
    for routine in sorted(routines):
        for source, bs in sorted(routines[routine].items()):
            notes.append(f"  {routine}: {source} ({', '.join(bs)})")
            if upstream is None or source not in upstream[1]:
                unknown = True
                continue
            tag, files, copying = upstream
            found = source_notices(files[source])
            if found:
                for notice in found:
                    notes.append(f"      covered by its own notice: {notice.splitlines()[0]}")
                    heading = f"notice in {source}, newlib tag {tag}"
                    if (heading, notice) not in texts:
                        texts.append((heading, notice))
            else:
                notes.append("      no notice in the file: COPYING.NEWLIB (1) covers it")
                default = (f"COPYING.NEWLIB (1), the default notice, newlib tag {tag}",
                           copying_default(copying))
                if default not in texts:
                    texts.append(default)
    if unknown:
        notes.append("\nFor a routine without a notice named here, the whole list that newlib's"
                     "\npackage ships, below, holds the licence that covers it.")
    else:
        notes.append(f"\nThe notices below come from newlib's sources at the tag {upstream[0]};"
                     "\nthe whole list that newlib's package ships follows them.")
    texts.append((f"newlib's licence list, the copyright file of {NEWLIB_PACKAGE} {version}", text))
    source = f"{NEWLIB_PACKAGE} {version}"
    if upstream is not None:
        source += f"; notices from {NEWLIB_REPO}, tag {upstream[0]}"
    return {"title": "newlib, the toolchain's C library", "source": source,
            "boards": boards, "licence": "several, listed below", "notes": notes,
            "copyrights": [], "texts": texts}


def _info(name, date_time, is_dir=False):
    zi = zipfile.ZipInfo(name, date_time=date_time)
    zi.create_system = 3
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = ((0o40755 << 16) | 0x10) if is_dir else (0o100644 << 16)
    return zi


def zip_bytes(top, entries, date_time):
    """A stored zip of `entries` (name -> bytes, names may hold folders) in
    the folder `top`, entries sorted by name, every one dated `date_time`."""
    names = dict(entries)
    for n in list(names):
        parts = n.split("/")
        for i in range(1, len(parts)):
            names["/".join(parts[:i]) + "/"] = None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(_info(top + "/", date_time, is_dir=True), b"")
        for n in sorted(names):
            if names[n] is None:
                zf.writestr(_info(f"{top}/{n}", date_time, is_dir=True), b"")
            else:
                zf.writestr(_info(f"{top}/{n}", date_time), names[n])
    return buf.getvalue()


def sha256sums(files):
    return "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in sorted(files.items()))


def archive(version, commit, date_time, images, notices_bytes, boards=BOARDS):
    """(zip name, zip bytes) from `images` (board -> .bin bytes)."""
    if set(images) != set(boards):
        raise PackageError(f"images for {sorted(images)}, boards {sorted(boards)}")
    bins = {bin_name(b, version): images[b] for b in boards}
    entries = dict(bins)
    entries["README.txt"] = readme(version, commit, boards)
    entries["NOTICES.txt"] = notices_bytes
    entries["SHA256SUMS"] = sha256sums(bins).encode("ascii")
    top = f"{NAME}-{version}"
    return top + ".zip", zip_bytes(top, entries, date_time)


# --- clone, build, collect -----------------------------------------------------

def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        raise PackageError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def submodules(src):
    """(name, path, url) of the firmware's submodules in .gitmodules of `src`."""
    out = git("config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$", cwd=src)
    rows = []
    for line in out.splitlines():
        key, path = line.split(" ", 1)
        name = key[len("submodule."):-len(".path")]
        if path.startswith(LIB):
            url = git("config", "-f", ".gitmodules", f"submodule.{name}.url", cwd=src)
            rows.append((name, path, url))
    return rows


def clone(repo, rev, dest):
    """Clone `repo` at `rev` into `dest` with the firmware's submodules;
    (commit, {path: (url, commit)})."""
    commit = git("rev-parse", "--verify", f"{rev}^{{commit}}", cwd=repo)
    git("clone", "--quiet", "--no-checkout", "--no-hardlinks", repo, dest, cwd=repo)
    git("-c", "advice.detachedHead=false", "switch", "--quiet", "--detach", commit, cwd=dest)
    subs = {}
    for name, path, url in submodules(dest):
        pinned = git("rev-parse", f"HEAD:{path}", cwd=dest)
        local = os.path.join(repo, path)
        source = url
        if os.path.exists(os.path.join(local, ".git")):
            r = subprocess.run(["git", "cat-file", "-e", f"{pinned}^{{commit}}"], cwd=local,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                source = local
        git("config", f"submodule.{name}.url", source, cwd=dest)
        subs[path] = (url, pinned)
    git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "--quiet",
        "--no-recommend-shallow", "--", *subs, cwd=dest)
    for path, (_, pinned) in subs.items():
        if git("rev-parse", "HEAD", cwd=os.path.join(dest, path)) != pinned:
            raise PackageError(f"{path}: not at its pinned commit {pinned}")
    return commit, subs


def build_env():
    env = {k: v for k, v in os.environ.items()
           if k not in BUILD_ENV_DROP and not k.startswith("CMAKE_")}
    env["LC_ALL"] = "C"
    return env


def build(src, board, ninja):
    """Configure and build `board` in src/firmware/build/<board>; the build
    folder. A compiler or linker warning stops the script."""
    out = os.path.join(src, "firmware", "build", board)
    configure = ["cmake", "-S", os.path.join(src, "firmware"), "-B", out, "-G", "Ninja",
                 f"-DBOARD={board}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
    if ninja:
        configure.append(f"-DCMAKE_MAKE_PROGRAM={ninja}")
    env = build_env()
    for cmd in (configure, ["cmake", "--build", out]):
        r = subprocess.run(cmd, cwd=src, env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            raise PackageError(f"{board}: {' '.join(cmd[:2])} failed:\n{r.stdout[-4000:]}")
        warnings = build_warnings(r.stdout)
        if warnings:
            raise PackageError(f"{board}: build warnings:\n" + "\n".join(warnings[:20]))
    return out


def ninja_tool(ninja, build_dir, *args):
    r = subprocess.run([ninja or "ninja", "-C", build_dir, "-t", *args], text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        raise PackageError(f"ninja -t {args[0]}: {r.stderr.strip()}")
    return r.stdout


def used_files(build_dir, ninja):
    """Every file the build read: the compiled sources and the headers they
    include, a generated file (in the build folder) replaced by the inputs of
    the command that wrote it, if Ninja knows them."""
    with open(os.path.join(build_dir, "compile_commands.json")) as f:
        files = {os.path.realpath(os.path.join(e["directory"], e["file"])) for e in json.load(f)}
    for line in ninja_tool(ninja, build_dir, "deps").splitlines():
        if line.startswith((" ", "\t")) and line.strip():
            files.add(os.path.realpath(line.strip()))
    bdir = os.path.realpath(build_dir) + os.sep
    seen, used, todo = set(), set(), sorted(files)
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        if not p.startswith(bdir):
            used.add(p)
            continue
        out = ninja_tool(ninja, build_dir, "query", os.path.relpath(p, build_dir))
        inputs, section = [], None
        for line in out.splitlines():
            s = line.strip()
            if line.startswith("  ") and not line.startswith("    "):
                section = s.split(":")[0]
            elif section == "input" and line.startswith("    ") and not s.startswith("|"):
                inputs.append(os.path.realpath(os.path.join(build_dir, s)))
        if inputs:
            todo.extend(inputs)
        else:
            used.add(p)                  # written at configure time (version.h)
    return sorted(used)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def collect(src, used, subs, commit):
    """NOTICES.txt sections for the clone `src` at `commit`, from the files
    each board's build read ({board: absolute paths}) and the submodules
    ({path: (url, pinned commit)})."""
    src = os.path.realpath(src)
    per_work = {}                        # work folder -> {file: set of boards}
    for board, files in used.items():
        for p in files:
            if not p.startswith(src + os.sep):
                continue                 # the toolchain's files
            rel = os.path.relpath(p, src).replace(os.sep, "/")
            per_work.setdefault(work_of(rel), {}).setdefault(rel, set()).add(board)
    order = list(BOARDS)

    def boards_of(files):
        found = set().union(*files.values())
        return [b for b in order if b in found]

    def lines_of(files):
        out = []
        for rel in sorted(files):
            for line in copyright_lines(read_text(os.path.join(src, rel))):
                if line not in out:
                    out.append(line)
        return sorted(out)

    own = per_work.pop("", {})
    st = {rel: b for rel, b in own.items() if "BSD-3-Clause" in (spdx(read_text(
        os.path.join(src, rel))) or "")}
    texts = []
    for rel in sorted(st):
        notice = embedded_notice(read_text(os.path.join(src, rel)))
        if notice is None:
            raise PackageError(f"{rel}: SPDX names BSD-3-Clause, but no "
                               "STMicroelectronics notice found in it")
        texts.append((f"notice in {rel}", notice))
    st_lines = sorted({line for _, t in texts for line in copyright_lines(t)})
    sections = [{
        "title": "Bit-Perfect Checker firmware",
        "source": f"{REPO_URL}, commit {commit}",
        "boards": boards_of(own), "licence": "MIT",
        "copyrights": [l for l in lines_of(own) if l not in st_lines],
        "texts": [("LICENSE", read_text(os.path.join(src, "LICENSE")))]}]
    if st:
        sections.append({
            "title": "STMicroelectronics panel code in this project's board files",
            "source": ", ".join(sorted(st)), "boards": boards_of(st),
            "licence": "BSD-3-Clause", "copyrights": st_lines, "texts": texts})
    for folder, (title, licence, licence_file) in WORKS.items():
        files = per_work.pop(folder, None)
        if not files:
            continue
        url, pinned = subs[folder]
        notes = []
        if licence != "BSD-3-Clause":
            # a file under another licence than its work's, as TinyUSB's
            # dwc2_type.h with an STMicroelectronics BSD-3-Clause notice
            for rel in sorted(files):
                text = read_text(os.path.join(src, rel))
                if BSD3_RE.search(text):
                    notes.append(f"Also under BSD-3-Clause (its text is in the sections under "
                                 f"that licence): {rel[len(folder) + 1:]} "
                                 f"({'; '.join(copyright_lines(text))})")
        sections.append({
            "title": title, "source": f"{url}, commit {pinned}",
            "boards": boards_of(files), "licence": licence, "notes": notes,
            "copyrights": lines_of(files),
            "texts": [(f"{licence_file} of {title}",
                       read_text(os.path.join(src, folder, licence_file)))]})
    if any(s.get("notes") for s in sections) and not any(
            s["licence"] == "BSD-3-Clause" for s in sections):
        raise PackageError("a file is under BSD-3-Clause, but no section holds its text")
    return sections


def debian_newlib(archives):
    """(version, licence list) of the Debian package that owns `archives`."""
    try:
        version = subprocess.run(["dpkg-query", "-W", "-f=${Version}", NEWLIB_PACKAGE],
                                 capture_output=True, text=True, check=True).stdout.strip()
        for path in sorted(archives):
            owner = subprocess.run(["dpkg-query", "-S", os.path.realpath(path)],
                                   capture_output=True, text=True, check=True).stdout
            if not owner.startswith(NEWLIB_PACKAGE + ":"):
                raise PackageError(f"{path}: not from {NEWLIB_PACKAGE} ({owner.strip()})")
    except (OSError, subprocess.CalledProcessError) as e:
        raise PackageError(f"newlib's package not found ({e}); name its licence list and "
                           "version with --newlib-licence and --newlib-version") from e
    return version, read_text(f"/usr/share/doc/{NEWLIB_PACKAGE}/copyright")


def commit_date(src, commit):
    """The commit's date in UTC, as a zip entry's date and time."""
    t = time.gmtime(int(git("show", "-s", "--format=%ct", commit, cwd=src)))
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -I firmware/tools/package.py",
                                 description="Build the firmware release zip from a clean clone.")
    ap.add_argument("--rev", default="HEAD", help="commit or tag to build (default: HEAD)")
    ap.add_argument("--work", required=True, help="scratch folder, empty or missing")
    ap.add_argument("--dist", default=os.path.join(ROOT, "firmware", "build", "release"),
                    help="output folder (default: firmware/build/release)")
    ap.add_argument("--ninja", help="the ninja to build with (default: ninja on PATH)")
    ap.add_argument("--repo", default=ROOT, help="repository to clone (default: this one)")
    ap.add_argument("--newlib-licence", help="newlib's licence list (default: Debian's "
                    f"copyright file of {NEWLIB_PACKAGE})")
    ap.add_argument("--newlib-version", help="the newlib package or release that list is from")
    ap.add_argument("--newlib-source", help="folder with newlib's COPYING.NEWLIB and the linked "
                    "routines' sources at their repository paths, at the toolchain's release")
    ap.add_argument("--newlib-tag", help="newlib's release tag of --newlib-source")
    a = ap.parse_args(argv)
    try:
        if bool(a.newlib_licence) != bool(a.newlib_version):
            raise PackageError("--newlib-licence and --newlib-version go together")
        if not a.newlib_source or not a.newlib_tag:
            raise PackageError("--newlib-source and --newlib-tag are needed: the notices of the "
                               "newlib routines in the images come from newlib's sources")
        work = os.path.abspath(a.work)
        if os.path.exists(work) and os.listdir(work):
            raise PackageError(f"{work}: not empty")
        os.makedirs(work, exist_ok=True)
        src = os.path.join(work, "bit-perfect-checker")
        commit, subs = clone(os.path.abspath(a.repo), a.rev, src)
        used, images, versions, linked = {}, {}, {}, {}
        for board in BOARDS:
            bdir = build(src, board, a.ninja)
            with open(os.path.join(bdir, "version.h")) as f:
                m = VERSION_RE.search(f.read())
            if not m:
                raise PackageError(f"{bdir}/version.h: no FW_VERSION")
            versions[board] = m.group(1)
            with open(os.path.join(bdir, f"bpc-{board}.bin"), "rb") as f:
                images[board] = f.read()
            used[board] = used_files(bdir, a.ninja)
            with open(os.path.join(bdir, f"bpc-{board}.map")) as f:
                linked[board] = linked_members(f.read())
            print(f"{board}: {len(images[board]):,d} bytes, fw={versions[board]}")
        version = check_version(versions)
        check_no_paths(images, sorted({work, os.path.realpath(work), os.path.abspath(a.repo),
                                       os.path.realpath(a.repo), os.path.expanduser("~")}))
        sections = collect(src, used, subs, commit)
        routines = toolchain_routines(linked)
        if routines:
            if a.newlib_licence:
                newlib = (a.newlib_version, read_text(a.newlib_licence))
            else:
                newlib = debian_newlib({p for ms in linked.values() for p, _ in ms
                                        if NEWLIB_ARCHIVE_RE.match(os.path.basename(p))})
            files = {}
            for source in sorted({src for r in routines.values() for src in r if src.startswith("newlib/")}):
                path = os.path.join(a.newlib_source, *source.split("/"))
                if not os.path.isfile(path):
                    raise PackageError(f"{path}: missing in --newlib-source")
                files[source] = read_text(path)
            copying = read_text(os.path.join(a.newlib_source, "COPYING.NEWLIB"))
            sections.append(newlib_section(routines, *newlib, (a.newlib_tag, files, copying)))
        name, data = archive(version, commit, commit_date(src, commit), images, notices(version, sections))
        os.makedirs(a.dist, exist_ok=True)
        path = os.path.join(a.dist, name)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        zips = sorted(n for n in os.listdir(a.dist)
                      if n.endswith(".zip") and os.path.isfile(os.path.join(a.dist, n)))
        with open(os.path.join(a.dist, "SHA256SUMS"), "w") as f:
            for n in zips:
                with open(os.path.join(a.dist, n), "rb") as z:
                    f.write(f"{hashlib.sha256(z.read()).hexdigest()}  {n}\n")
        print(f"{len(data):,d}  {path}")
        print(f"sha256 {hashlib.sha256(data).hexdigest()}")
    except (PackageError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
