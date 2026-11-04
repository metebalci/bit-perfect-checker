"""Release archives: deterministic zip files built from a generated set.

    python3 -m generator package [--out out] [--dist dist]
    python3 -m generator package --sweep [--out out-sweep] [--dist dist]

bpc1-r1-wav.zip holds every PCM WAV and every DSD file; bpc1-r1-flac.zip,
bpc1-r1-aiff.zip and bpc1-r1-alac.zip every FLAC, AIFF and ALAC (.m4a)
file. With --sweep, from a set that `python3 -m generator --sweep` wrote:
bpc1-r1-sweep-wav.zip, -sweep-flac.zip, -sweep-aiff.zip and
-sweep-alac.zip, all stored. The names carry the file-set release,
RELEASE_NAME (bpc1-r1), which also starts each archive's top-level folder
(bpc1-r1-wav/ and so on). The dist folder's SHA256SUMS lists every zip built there. The
32-bit PCM WAVs that `python3 -m generator --32bit` adds are not in the
release. Each archive has
one top-level folder with an empty file whose name is the do-not-play
warning, a README.txt and a SHA256SUMS, then the audio files, and a folder
negative/ with the deliberately faulty files and a README.txt about them.

Determinism: entries sorted by name, one fixed timestamp, Unix permissions
0644 (files) and 0755 (the folder), no comments or extra fields. The WAV
archive is deflated (level 9), so its bytes depend on the zlib version as
FLAC files depend on the flac version (ALAC files depend only on the pinned
ALAC codec); the other archives are stored.

GitHub takes release files under 2 GiB only: a zip of ASSET_LIMIT bytes or
more is an error and is not written, and the dist folder keeps any earlier
zip of that name.
"""

import argparse
import hashlib
import os
import shutil
import sys
import textwrap
import zipfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from . import fileset

ZIP_DATE = (2026, 11, 2, 0, 0, 0)          # BPC1 v1 date, for every entry
RELEASE_NAME = "bpc1-r1"                   # the file-set release: zip and folder names
LEVEL = 9
WARNING_FILE = "!!! TEST SIGNALS - DO NOT PLAY ON SPEAKERS OR HEADPHONES !!!.txt"
WARNING_TEXT = "TEST SIGNALS - DO NOT PLAY ON SPEAKERS OR HEADPHONES"
LICENSE = os.path.join(os.path.dirname(__file__), "..", "LICENSE")
FORBIDDEN = set('<>:"/\\|?*')
ASSET_LIMIT = 2 ** 31                      # GitHub: each release file must be under 2 GiB


@dataclass(frozen=True)
class Package:
    name: str          # zip file name without .zip, also the top-level folder
    method: int        # zipfile.ZIP_DEFLATED or ZIP_STORED
    files: str         # "wav": WAV and DSD files; "flac", "aiff", "alac": those of the WAVs
    title: str
    contents: str      # README paragraph about what this archive holds
    sweep: bool = False                    # the code-sweep set
    warning_file: str = WARNING_FILE
    warning_text: str = WARNING_TEXT

    @property
    def filename(self):
        return self.name + ".zip"


WAV = Package(
    f"{RELEASE_NAME}-wav", zipfile.ZIP_DEFLATED, "wav", "BPC1 v1 test files: WAV and DSD",
    f"""This archive holds:

  PCM WAV  16 and 24 bits at 44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384,
           705.6 and 768 kHz (20 files). Each has a 2 s lead-in, a 10 s
           body and a 0.5 s tail.
  DSD      DSD64, DSD128 and DSD256, each as DSF, DFF and DoP in a 24-bit
           WAV (9 files), 10 s body.
  Negative in the folder negative/: deliberately faulty files that must not
           check as bit-perfect (6 files, see negative/README.txt).

The PCM files and the DoP files also come as FLAC, AIFF and ALAC, in
{RELEASE_NAME}-flac.zip, {RELEASE_NAME}-aiff.zip and {RELEASE_NAME}-alac.zip.
File names give the format: bpc1_pcm_096k_24bit.wav is PCM at 96 kHz, 24 bits
(044k1 is 44.1 kHz); bpc1_dsd064_dsf.dsf is DSD64 in a DSF file;
bpc1_dsd064_dop-176k4_24bit.wav is DSD64 as DoP in a 176.4 kHz 24-bit WAV.

For long runs, to find rare faults, play the files one after another
continuously, for hours if needed, or loop one file gaplessly so that it
arrives as one continuous stream. Every repetition gets its own result.""")

FLAC = Package(
    f"{RELEASE_NAME}-flac", zipfile.ZIP_STORED, "flac", "BPC1 v1 test files: FLAC",
    f"""This archive holds the test files as FLAC:

  PCM FLAC  16 and 24 bits at 44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384,
            705.6 and 768 kHz (20 files). Each has a 2 s lead-in, a 10 s
            body and a 0.5 s tail.
  DoP FLAC  DSD64, DSD128 and DSD256 as DoP in 24-bit FLAC at 176.4, 352.8
            and 705.6 kHz (3 files), 10 s body.
  Negative  in the folder negative/: deliberately faulty files that must
            not check as bit-perfect (6 files, see negative/README.txt).

Each FLAC file decodes to exactly the samples of the WAV file of the same
name. The PCM files at 705.6 and 768 kHz and the DSD256 DoP file, at
705.6 kHz, are above the rates of the FLAC subset: they need a FLAC decoder
based on libFLAC 1.4.0 or later and may not play in older players. The WAV
files of the same name always work. DSD as DSF and DFF is only in the WAV
archive, {RELEASE_NAME}-wav.zip.

For long runs, to find rare faults, play the files one after another
continuously, for hours if needed, or loop one file gaplessly so that it
arrives as one continuous stream. Every repetition gets its own result.""")

SWEEP_WARNING_FILE = "!!! FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS OR HEADPHONES !!!.txt"
SWEEP_WARNING_TEXT = "FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS OR HEADPHONES"
SWEEP_CONTENTS = """This archive holds the code-sweep files{fmt}:

  Sweep    16 and 24 bits at 44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384,
           705.6 and 768 kHz (20 files). Each has a 2 s lead-in and a 0.5 s
           tail like the regular files. Their payload is a full-scale code
           sweep: each channel's body carries every 16-bit or 24-bit sample
           code, in a scrambled order. The 16-bit body lasts 10 s; the
           24-bit body holds all 2^24 codes in each channel, which takes
           6.9 minutes at 44.1 kHz and 23.5 s at 768 kHz.
  Negative in the folder negative/: deliberately faulty files that must not
           check as bit-perfect (2 files, see negative/README.txt).

They are checked like the regular files: BIT_PERFECT only if every sample
arrived unchanged. Where the regular files test the paths of a player and
a device with a low-level payload, the sweep tests that every code passes."""


def _sweep_contents(fmt, note=""):
    """README paragraph of a sweep archive; note: a paragraph about its format."""
    return SWEEP_CONTENTS.format(fmt=fmt) + ("\n\n" + textwrap.fill(note, 76) if note else "")


SWEEP_WAV = Package(f"{RELEASE_NAME}-sweep-wav", zipfile.ZIP_STORED, "wav",
                    "BPC1 v1 code-sweep files: WAV", _sweep_contents(" as WAV"),
                    True, SWEEP_WARNING_FILE, SWEEP_WARNING_TEXT)
SWEEP_FLAC = Package(f"{RELEASE_NAME}-sweep-flac", zipfile.ZIP_STORED, "flac",
                     "BPC1 v1 code-sweep files: FLAC",
                     _sweep_contents(" as FLAC", "Each FLAC file decodes to exactly the samples "
                                     "of the WAV file of the same name. The files at 705.6 and "
                                     "768 kHz need a FLAC decoder based on libFLAC 1.4.0 or "
                                     "later."),
                     True, SWEEP_WARNING_FILE, SWEEP_WARNING_TEXT)

LONG_RUNS = """For long runs, to find rare faults, play the files one after another
continuously, for hours if needed, or loop one file gaplessly so that it
arrives as one continuous stream. Every repetition gets its own result."""
AIFF_NOTE = ("The samples are big-endian PCM; the title is in the NAME chunk, the "
             "comment in the ANNO chunk.")
ALAC_NOTE = ("The files are Apple Lossless (ALAC) in MP4, encoded with Apple's reference ALAC "
             "encoder; the title and the comment are MP4 tags. Apple's own ALAC decoders "
             "refuse ALAC above 384 kHz (the one in macOS does, and so presumably does iOS's "
             "built-in playback), so {above} are for players with their own ALAC decoder; the "
             "WAV and AIFF files of the same name hold the same samples. Players and tools that "
             "decode ALAC with ffmpeg (8.0.1 and its current development version) decode some "
             "packets of some of these files wrongly, an ffmpeg bug (ffmpeg issue 25727): such "
             "a player fails those files while the WAV and AIFF files pass.")
ABOVE = "the PCM files at 705.6 and 768 kHz and the DSD256 DoP file, at 705.6 kHz,"
SWEEP_ABOVE = "the files at 705.6 and 768 kHz"


def _contents(fmt, ext, note):
    """README paragraph of the AIFF and ALAC archives."""
    return f"""This archive holds the test files as {fmt} ({ext}):

  PCM       16 and 24 bits at 44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384,
            705.6 and 768 kHz (20 files). Each has a 2 s lead-in, a 10 s
            body and a 0.5 s tail.
  DoP       DSD64, DSD128 and DSD256 as DoP in 24-bit {fmt} at 176.4, 352.8
            and 705.6 kHz (3 files), 10 s body.
  Negative  in the folder negative/: deliberately faulty files that must
            not check as bit-perfect (6 files, see negative/README.txt).

{textwrap.fill(f"Each {fmt} file holds exactly the samples of the WAV file of the same "
               f"name. {note} DSD as DSF and DFF is only in the WAV archive, "
               f"{RELEASE_NAME}-wav.zip.", 76)}

{LONG_RUNS}"""


AIFF = Package(f"{RELEASE_NAME}-aiff", zipfile.ZIP_STORED, "aiff", "BPC1 v1 test files: AIFF",
               _contents("AIFF", ".aiff", AIFF_NOTE))
ALAC = Package(f"{RELEASE_NAME}-alac", zipfile.ZIP_STORED, "alac", "BPC1 v1 test files: ALAC",
               _contents("ALAC", ".m4a", ALAC_NOTE.format(above=ABOVE)))
SWEEP_AIFF = Package(f"{RELEASE_NAME}-sweep-aiff", zipfile.ZIP_STORED, "aiff",
                     "BPC1 v1 code-sweep files: AIFF",
                     _sweep_contents(" as AIFF (.aiff)", "Each AIFF file holds exactly the "
                                     "samples of the WAV file of the same name. " + AIFF_NOTE),
                     True, SWEEP_WARNING_FILE, SWEEP_WARNING_TEXT)
SWEEP_ALAC = Package(f"{RELEASE_NAME}-sweep-alac", zipfile.ZIP_STORED, "alac",
                     "BPC1 v1 code-sweep files: ALAC",
                     _sweep_contents(" as ALAC in MP4 (.m4a)", "Each ALAC file decodes to "
                                     "exactly the samples of the WAV file of the same name. "
                                     + ALAC_NOTE.format(above=SWEEP_ABOVE)),
                     True, SWEEP_WARNING_FILE, SWEEP_WARNING_TEXT)

RELEASE = (WAV, FLAC, AIFF, ALAC)          # the published archives
SWEEP_RELEASE = (SWEEP_WAV, SWEEP_FLAC, SWEEP_AIFF, SWEEP_ALAC)
EXT = {"flac": ".flac", "aiff": ".aiff", "alac": ".m4a"}     # beside each WAV


def archives_line(pkg):
    """The README line that names the set's four archives."""
    names = [p.filename for p in (SWEEP_RELEASE if pkg.sweep else RELEASE)]
    return textwrap.fill("The set comes in four archives, one per format: " + ", ".join(names[:-1])
                         + " and " + names[-1] + ".", 76)


def file_set(pkg):
    return fileset.sweep_set() if pkg.sweep else fileset.test_set()


def members(pkg):
    """Paths ("/"-separated) of the generated set that go into the package."""
    out = []
    for f in file_set(pkg):
        if pkg.files == "wav":
            out.append(f.relpath)
        if pkg.files in f.formats:
            out.append(f.relpath[:-len(f.ext)] + EXT[pkg.files])
    return sorted(out)


def negative_readme(pkg):
    """negative/README.txt: what each deliberately faulty file must give."""
    ext = ".wav" if pkg.files == "wav" else EXT[pkg.files]
    lines = []
    for f in [f for f in file_set(pkg) if f.fault]:
        what, where = fileset.fault_text(f)
        lines += [f"  {f.name}{ext}", f"      {what}"]
        lines += [f"      {where}"] if where else []
        lines += [f"      -> {fileset.expected(f)}"]
    rows = "\n".join(lines)
    text = f"""!!! {pkg.warning_text} !!!

Negative tests: deliberately faulty files
=========================================

The files in this folder are deliberately faulty: each one must NOT check as
bit-perfect. They test the checker itself. Each is the file of the same name
without _altered or _incomplete, with one fault in its body, as section 11.5
of docs/bpc1.md describes; the lead-in and tail are unchanged.
Body blocks are counted from 0. The fault in each, and the verdict it must
give:

{rows}

A checker must report NOT_BIT_PERFECT.
"""
    return text.replace("\n", "\r\n").encode("ascii")


def clipping(pkg):
    """The README section about clipping and level indicators."""
    peaks = ("the walking-bit bursts in every block reach it, and the sweep body covers "
             "every sample value up to it" if pkg.sweep else
             "the walking-bit bursts in every block reach it")
    paras = [f"The files reach full scale: {peaks}. A player or tool that measures levels "
             "may report clipping, or peaks at 0 dBFS. That is expected, and no problem "
             "when the player is transparent: it passes the samples unchanged, and the "
             "checker judges only what arrives.",
             'Turn off ReplayGain, Sound Check, volume normalization, limiters and any '
             '"prevent clipping" option. They change the samples, and the file then fails.']
    return ("Clipping and level indicators\n-----------------------------\n\n"
            + "\n\n".join(textwrap.fill(t.replace("0 dBFS", "0\0dBFS"), 76).replace("\0", " ")
                          for t in paras))


def readme(pkg):
    if pkg.sweep:
        return sweep_readme(pkg)
    with open(LICENSE, encoding="ascii") as f:
        licence = f.read().strip()
    text = f"""!!! {WARNING_TEXT} !!!

{pkg.title}
{"=" * len(pkg.title)}

WARNING: {WARNING_TEXT}.
These files are not music. Every 85 to 93 ms they contain a full-scale burst,
and parts of them are loud square waves and noise. Played on speakers or
headphones they are loud and unpleasant, and they can damage equipment and
hearing. Play them only into a bit-perfect checker.


What they are
-------------

Test signals in the BPC1 format of the Bit-Perfect Checker project. A
checker regenerates every expected sample from the signal itself, and tells
whether the playback chain delivered the file bit-perfect: BIT_PERFECT or
NOT_BIT_PERFECT. The software checker also describes what changed, as an
informative diagnosis: truncation, dither, volume, resampling, channel
order, polarity, dropped, repeated or zero-filled frames, DSD-to-PCM
conversion.

{pkg.contents}

{archives_line(pkg)}

The folder negative/ holds deliberately faulty files: each must NOT check
as bit-perfect (one altered, one incomplete each of PCM 44.1 kHz and 48 kHz
24-bit and DSD64 as DoP at 176.4 kHz). negative/README.txt says what each
must give.

SHA256SUMS lists the SHA-256 of every file.


How to use them
---------------

1. With a bit-perfect-checker device (a USB audio receiver): select it as
   the audio output, play a file in your player and read the device's
   verdict.
   Use the player's exclusive or bit-perfect mode, 100 % volume and no
   effects. The first 2 s of every file are a lead-in that is not judged.

2. With the software checker in the bit-perfect-checker source repository
   (Python 3 and numpy), run from the repository folder:

     python3 -m checker FILE...

   to check files, for example after a conversion, or

     python3 -m checker --raw s32le --rate 96000 --channels 2 capture.raw

   to check a raw recording of what a player sent.

The format is specified in docs/bpc1.md of that repository.


{clipping(pkg)}


Licence
-------

The test files, like the generator that writes them, are under the MIT
licence:

{licence}
"""
    return text.replace("\n", "\r\n").encode("ascii")


def sweep_readme(pkg):
    with open(LICENSE, encoding="ascii") as f:
        licence = f.read().strip()
    text = f"""!!! {pkg.warning_text} !!!

{pkg.title}
{"=" * len(pkg.title)}

WARNING: {pkg.warning_text}.
These files are not music. Their body is noise at full scale, every sample
code from the most negative to the most positive. Played on speakers or
headphones they are as loud as a file can be, and they can damage
equipment and hearing at once. Play them only into a bit-perfect checker.


What they are
-------------

Test signals in the BPC1 format of the Bit-Perfect Checker project. A
checker regenerates every expected sample from the signal itself, and tells
whether the playback chain delivered the file bit-perfect: BIT_PERFECT or
NOT_BIT_PERFECT.

{pkg.contents}

{archives_line(pkg)}

SHA256SUMS lists the SHA-256 of every file.


How to use them
---------------

Use them as the regular files ({RELEASE_NAME}-wav.zip): play a file into a
bit-perfect-checker device and read its verdict, or check a file or a raw
recording with the software checker of the bit-perfect-checker repository:

  python3 -m checker FILE...

The format is specified in docs/bpc1.md of that repository.


{clipping(pkg)}


Licence
-------

The test files, like the generator that writes them, are under the MIT
licence:

{licence}
"""
    return text.replace("\n", "\r\n").encode("ascii")


def valid_name(name):
    """Portable file name: ASCII, none of <>:"/\\|?*, no trailing space or dot."""
    return (name.isascii() and not FORBIDDEN & set(name) and name == name.strip()
            and not name.endswith(".") and all(32 <= ord(c) < 127 for c in name))


def _info(name, method, is_dir=False):
    zi = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    zi.create_system = 3
    if is_dir:
        zi.external_attr = (0o40755 << 16) | 0x10
        zi.compress_type = zipfile.ZIP_STORED
    else:
        zi.external_attr = 0o100644 << 16
        zi.compress_type = method
    return zi


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_zip(path, top, files, method, readme_bytes, digests=None, texts=None,
              warning_file=WARNING_FILE, limit=None):
    """Write `path` with folder `top`: the warning file, README.txt,
    SHA256SUMS and `files`, entries sorted by name. `files` lists source
    paths, stored under their base name, or (name, source path) pairs, where
    name may be in a subfolder ("negative/x.wav"). `texts` maps further
    names to bytes (e.g. "negative/README.txt"). A zip of `limit` bytes or
    more (default ASSET_LIMIT) is removed and raises RuntimeError; `path`
    is then left as it was."""
    limit = ASSET_LIMIT if limit is None else limit
    digests = digests or {}
    names = dict(f if isinstance(f, tuple) else (os.path.basename(f), f) for f in files)
    sums = "".join(f"{digests.get(n) or sha256_file(p)}  {n}\n" for n, p in sorted(names.items()))
    entries = {warning_file: b"", "README.txt": readme_bytes,
               "SHA256SUMS": sums.encode("ascii")}
    entries.update(texts or {})
    entries.update(names)
    for n in list(entries):
        if not all(valid_name(part) for part in n.split("/")):
            raise ValueError(f"not a portable file name: {n!r}")
        for i in range(n.count("/")):
            entries["/".join(n.split("/")[:i + 1]) + "/"] = None     # its folder
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", compression=method, compresslevel=LEVEL) as zf:
        zf.writestr(_info(top + "/", method, is_dir=True), b"")
        for n in sorted(entries):
            src = entries[n]
            if src is None:
                zf.writestr(_info(f"{top}/{n}", method, is_dir=True), b"")
            elif isinstance(src, bytes):
                zi = _info(f"{top}/{n}", method if src else zipfile.ZIP_STORED)
                zf.writestr(zi, src, compresslevel=LEVEL)
            else:
                zi = _info(f"{top}/{n}", method)
                zi.file_size = os.path.getsize(src)
                with open(src, "rb") as s, zf.open(zi, "w") as d:
                    shutil.copyfileobj(s, d, 1 << 20)
    size = os.path.getsize(tmp)
    if size >= limit:
        os.remove(tmp)
        raise RuntimeError(f"{os.path.basename(path)}: {size:,d} bytes, not under the limit of "
                           f"{limit:,d} bytes for a GitHub release file; not written")
    os.replace(tmp, path)
    return path


def read_sums(out_dir):
    path = os.path.join(out_dir, "SHA256SUMS")
    if not os.path.isfile(path):
        return None
    sums = {}
    with open(path) as f:
        for line in f:
            d, n = line.rstrip("\n").split("  ", 1)
            sums[n] = d
    return sums


def build(pkg, out_dir, dist_dir, limit=None):
    """Check the package's files against out/SHA256SUMS and write the zip."""
    sums = read_sums(out_dir)
    if sums is None:
        raise RuntimeError(f"{out_dir}/SHA256SUMS missing: run the full generator first")
    names = members(pkg)
    missing = [n for n in names if n not in sums or not os.path.isfile(os.path.join(out_dir, n))]
    if missing:
        hint = {"flac": " (FLAC needs flac >= 1.4.0 when generating)",
                "alac": " (ALAC needs a C++ compiler when generating)"}.get(pkg.files, "")
        raise RuntimeError(f"{pkg.filename}: {len(missing)} files missing in {out_dir}{hint}, "
                           f"e.g. {missing[0]}")
    paths = [os.path.join(out_dir, *n.split("/")) for n in names]
    for n, p in zip(names, paths):
        if sha256_file(p) != sums[n]:
            raise RuntimeError(f"{p} does not match {out_dir}/SHA256SUMS")
    os.makedirs(dist_dir, exist_ok=True)
    return write_zip(os.path.join(dist_dir, pkg.filename), pkg.name, list(zip(names, paths)),
                     pkg.method, readme(pkg), {n: sums[n] for n in names},
                     {f"{fileset.NEGATIVE_DIR}/README.txt": negative_readme(pkg)},
                     pkg.warning_file, limit)


def _job(args):
    pkg, out_dir, dist_dir, limit = args
    try:
        path = build(pkg, out_dir, dist_dir, limit)
        return pkg.filename, os.path.getsize(path), sha256_file(path), None
    except (OSError, RuntimeError, ValueError) as e:
        return pkg.filename, 0, None, str(e)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m generator package",
                                 description="Build the release zip files from a generated set.")
    ap.add_argument("--out", default="out", help="generated set (default: out)")
    ap.add_argument("--dist", default="dist", help="output directory (default: dist)")
    ap.add_argument("--sweep", action="store_true",
                    help="build the code-sweep zips from a set written with --sweep")
    a = ap.parse_args(argv)
    pkgs = SWEEP_RELEASE if a.sweep else RELEASE
    with ProcessPoolExecutor(max_workers=len(pkgs)) as pool:
        # the limit goes with each job: a worker imports this module afresh
        res = list(pool.map(_job, [(p, a.out, a.dist, ASSET_LIMIT) for p in pkgs]))
    ok = [r for r in res if r[3] is None]
    for name, size, _, err in res:
        print(f"{name}: error: {err}" if err else f"{size:>13,d}  {name}",
              file=sys.stderr if err else sys.stdout)
    if ok and len(ok) == len(res):
        # SHA256SUMS lists every zip built in dist: the regular and the sweep ones
        sums = read_sums(a.dist) or {}
        sums = {n: d for n, d in sums.items() if os.path.isfile(os.path.join(a.dist, n))}
        sums.update((name, digest) for name, _, digest, _ in ok)
        with open(os.path.join(a.dist, "SHA256SUMS"), "w") as f:
            for name in sorted(sums):
                f.write(f"{sums[name]}  {name}\n")
    return 0 if len(ok) == len(res) else 1
