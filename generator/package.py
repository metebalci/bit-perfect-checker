"""Release archives: deterministic zip files built from a generated set.

    python3 -m generator package [--out out] [--dist dist]
    python3 -m generator package --sweep [--out out-sweep] [--dist dist]
    python3 -m generator package --gapless [--out out-gapless] [--dist dist]
    python3 -m generator package --negative [--out out] [--out-sweep out-sweep] [--dist dist]

bpc1-r1-wav.zip holds every PCM WAV and every DSD file; bpc1-r1-flac.zip,
bpc1-r1-aiff.zip and bpc1-r1-alac.zip every FLAC, AIFF and ALAC (.m4a)
file. With --sweep, from a set that `python3 -m generator --sweep` wrote:
bpc1-r1-sweep-wav.zip, -sweep-flac.zip, -sweep-aiff.zip and
-sweep-alac.zip, all stored. These eight hold only files that should pass.
With --negative, from the regular set (--out) and the code-sweep set
(--out-sweep): bpc1-r1-negative.zip, stored, with every negative test file
of both sets in one folder per format (wav/, flac/, aiff/, alac/). With
--gapless, from the set that `python3 -m generator --gapless` wrote:
bpc1-r1-gapless.zip, stored, with one folder per format. The names carry
the file-set release, RELEASE_NAME (bpc1-r1), which also starts each
archive's top-level folder (bpc1-r1-wav/ and so on). The dist folder's
SHA256SUMS lists every zip in it, rewritten from the zips there after every
run, a failed one too. The 32-bit PCM WAVs that `python3 -m generator
--32bit` adds are not in the release. Each archive has one top-level folder
with an empty file whose name is the do-not-play warning (the negative
archive: both warnings), a README.txt and a SHA256SUMS, then the audio
files.

Determinism: entries sorted by name, one fixed timestamp, Unix permissions
0644 (files) and 0755 (the folder), no comments or extra fields. The WAV
archive is deflated (level 9), so its bytes depend on the zlib version as
FLAC files depend on the flac version (ALAC files on the pinned ALAC codec,
measured with GCC 15.2: README.md, ALAC); the other archives are
stored.

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
    files: str         # "wav": WAV and DSD files; "flac", "aiff", "alac": those of the WAVs;
                       # "gapless": every file of the gapless set; "negative": the
                       # negative test files of the regular and the sweep set
    title: str
    contents: str      # README paragraph about what this archive holds
    sweep: bool = False                    # the code-sweep set
    warning_file: str | tuple = WARNING_FILE  # the empty warning file, or several
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

GAPLESS = Package(
    f"{RELEASE_NAME}-gapless", zipfile.ZIP_STORED, "gapless", "BPC1 v1 test files: gapless pairs",
    """This archive holds gapless pairs: regular test files cut in two at 7.5 s,
inside the body that a checker judges.

  wav/ aiff/ flac/ alac/  PCM 44.1 and 48 kHz, 16 and 24 bits (4 pairs each)
  dsf/ dff/               DSD64 (1 pair each)

Play them only after the regular files pass in the same player and format:
they assume that samples arrive unchanged, and test only that the player
joins two tracks without a gap. Turn gapless playback on and crossfade off,
and play part1, then part2 (a whole folder plays them in order). Each pair
gives one BIT_PERFECT, with lost=0 on the result line, when the join was
exact; one NOT_BIT_PERFECT with diff 7.500 or a little less when the player
changed the join; two NOT_BIT_PERFECT (diff 7.500, then 7.523 or 7.509) when
it closed the stream between the parts. Checked one by one, each part is
NOT_BIT_PERFECT; that is expected.""")

NEGATIVE = Package(
    f"{RELEASE_NAME}-negative", zipfile.ZIP_STORED, "negative", "BPC1 v1 negative test files",
    """  wav/ flac/ aiff/ alac/  the same 8 files in each format (32 files)""",
    warning_file=(SWEEP_WARNING_FILE, WARNING_FILE), warning_text=SWEEP_WARNING_TEXT)

RELEASE = (WAV, FLAC, AIFF, ALAC)          # the published archives
SWEEP_RELEASE = (SWEEP_WAV, SWEEP_FLAC, SWEEP_AIFF, SWEEP_ALAC)
EXT = fileset.EXT                          # beside each WAV


def archives_line(pkg):
    """The README lines that name the set's four archives and the negative one."""
    names = [p.filename for p in (SWEEP_RELEASE if pkg.sweep else RELEASE)]
    return textwrap.fill("The set comes in four archives, one per format: " + ", ".join(names[:-1])
                         + " and " + names[-1] + ". The negative tests, deliberately faulty "
                         "files that must not check as bit-perfect, are in "
                         + NEGATIVE.filename + ".", 76, break_on_hyphens=False)


def file_set(pkg):
    if pkg.files == "gapless":
        return fileset.gapless_set()
    return fileset.sweep_set() if pkg.sweep else fileset.test_set()


def negative_files():
    """(name in the negative archive, from the sweep set, path in its set) of
    every negative test file of the regular and the code-sweep set: one
    folder per format."""
    out = []
    for sweep, files in ((False, fileset.test_set()), (True, fileset.sweep_set())):
        for f in files:
            if f.fault:
                out.append((f"wav/{f.filename}", sweep, f.relpath))
                out += [(f"{x}/{f.name}{EXT[x]}", sweep, f.relpath_as(x)) for x in f.formats]
    return sorted(out)


def members(pkg):
    """Paths ("/"-separated) in the package: those of the generated set, or,
    for the negative archive, those in its folders."""
    if pkg.files == "negative":
        return [n for n, _, _ in negative_files()]
    out = []
    for f in file_set(pkg):
        if f.fault:
            continue                       # in the negative archive
        if pkg.files in ("wav", "gapless"):
            out.append(f.relpath)
        out += [f.relpath_as(x) for x in f.formats if pkg.files in (x, "gapless")]
    return sorted(out)


def negative_rows(files):
    """README lines for negative test files: each one's fault and verdict."""
    lines = []
    for f in files:
        what, where = fileset.fault_text(f)
        lines += [f"  {f.name}", f"      {what}"]
        lines += [f"      {where}"] if where else []
        lines += [f"      -> {fileset.expected(f)}"]
    return "\n".join(lines)


def clipping(pkg):
    """The README section about clipping and level indicators."""
    peaks = ("the walking-bit bursts in every block reach it, and the sweep body covers "
             "every sample value up to it" if pkg.sweep else
             "the walking-bit bursts in every block reach it, and the body of the sweep "
             "files covers every sample value up to it" if pkg.files == "negative" else
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
    if pkg.files == "gapless":
        return gapless_readme(pkg)
    if pkg.files == "negative":
        return negative_readme(pkg)
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

SHA256SUMS lists the SHA-256 of every file.


How to use them
---------------

1. With a Bit-Perfect Checker device (a USB audio receiver): select it as
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
Bit-Perfect Checker device and read its verdict, or check a file or a raw
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


def negative_readme(pkg):
    """README.txt of the negative archive: what each deliberately faulty
    file is and the verdict it must give."""
    with open(LICENSE, encoding="ascii") as f:
        licence = f.read().strip()
    regular = [f for f in fileset.test_set() if f.fault]
    sweep = [f for f in fileset.sweep_set() if f.fault]
    text = f"""!!! {pkg.warning_text} !!!

{pkg.title}
{"=" * len(pkg.title)}

WARNING: {pkg.warning_text}.
WARNING: {WARNING_TEXT}.
These files are not music. The regular ones contain a full-scale burst every
85 to 93 ms, and parts of them are loud square waves and noise; the body of
the sweep ones is noise at full scale. Played on speakers or headphones they
are loud, and they can damage equipment and hearing at once. Play them only
into a bit-perfect checker.


What they are
-------------

Test signals in the BPC1 format of the Bit-Perfect Checker project, each
deliberately faulty: each one must NOT check as bit-perfect. Each is a copy
of a file of the regular set or of the code-sweep set with one fault in its
body, as sections 11.5 and 11.6 of docs/bpc1.md describe; the lead-in and
tail are unchanged. Its name is that of the file it copies with _altered or
_incomplete added.

{pkg.contents}

The fault in each (body blocks counted from 0), and the verdict it must
give, in every format:

From the regular set ({RELEASE_NAME}-wav.zip and the other format archives):

{negative_rows(regular)}

From the code-sweep set ({RELEASE_NAME}-sweep-wav.zip and the others):

{negative_rows(sweep)}

A checker must report NOT_BIT_PERFECT for each: a file here must not check
as BIT_PERFECT.

SHA256SUMS lists the SHA-256 of every file, with its folder.


When to use them
----------------

They test the checker, not the player. Play or check them to see that a
Bit-Perfect Checker device, or the software checker, does reject altered and
incomplete files: after setting a device up, after changing its firmware or
a checker, or to see what a failure looks like. A BIT_PERFECT result for any
of them means that the checker is wrong. Whether a player is bit-perfect is
what the files of the other archives test.


How to use them
---------------

Use them as the regular files ({RELEASE_NAME}-wav.zip): play a file into a
Bit-Perfect Checker device and read its verdict, or check a file with the
software checker of the bit-perfect-checker repository:

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


def gapless_readme(pkg):
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
NOT_BIT_PERFECT.

{pkg.contents}

SHA256SUMS lists the SHA-256 of every file, with its folder.

The format and this set (section 11.7) are specified in docs/bpc1.md of the
bit-perfect-checker source repository.


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
    """Write `path` with folder `top`: the warning file (`warning_file`, a
    name or a tuple of names), README.txt, SHA256SUMS and `files`, entries
    sorted by name. `files` lists source
    paths, stored under their base name, or (name, source path) pairs, where
    name may be in a subfolder ("wav/x.wav"). `texts` maps further
    names to bytes (e.g. "notes/README.txt"). A zip of `limit` bytes or
    more (default ASSET_LIMIT) is removed and raises RuntimeError; `path`
    is then left as it was."""
    limit = ASSET_LIMIT if limit is None else limit
    digests = digests or {}
    names = dict(f if isinstance(f, tuple) else (os.path.basename(f), f) for f in files)
    sums = "".join(f"{digests.get(n) or sha256_file(p)}  {n}\n" for n, p in sorted(names.items()))
    warnings = (warning_file,) if isinstance(warning_file, str) else warning_file
    entries = {w: b"" for w in warnings}
    entries.update({"README.txt": readme_bytes, "SHA256SUMS": sums.encode("ascii")})
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


def sources(pkg, out_dir, sweep_dir=None):
    """(name in the package, generated set, path in that set) of every file
    of the package; the negative archive takes the sweep set's files from
    `sweep_dir`."""
    if pkg.files != "negative":
        return [(n, out_dir, n) for n in members(pkg)]
    if sweep_dir is None:
        raise RuntimeError(f"{pkg.filename} needs the code-sweep set too (--out-sweep)")
    return [(n, sweep_dir if sweep else out_dir, rel) for n, sweep, rel in negative_files()]


def build(pkg, out_dir, dist_dir, limit=None, sweep_dir=None):
    """Check the package's files against the SHA256SUMS of their set and
    write the zip."""
    rows = sources(pkg, out_dir, sweep_dir)
    sums = {}
    for d in dict.fromkeys(d for _, d, _ in rows):
        gen = "python3 -m generator" + (" --sweep" if d == sweep_dir else "")
        if not os.path.isdir(d):
            raise RuntimeError(f"{d}: folder missing: write the set with `{gen} --out {d}` first")
        sums[d] = read_sums(d)
        if sums[d] is None:
            raise RuntimeError(f"{d}/SHA256SUMS missing: run the full generator first ({gen})")
    missing = [os.path.join(d, rel) for _, d, rel in rows
               if rel not in sums[d] or not os.path.isfile(os.path.join(d, *rel.split("/")))]
    if missing:
        hint = {"flac": " (FLAC needs flac >= 1.4.0 when generating)",
                "alac": " (ALAC needs a C++ compiler when generating)"}.get(pkg.files, "")
        raise RuntimeError(f"{pkg.filename}: {len(missing)} files missing{hint}, "
                           f"e.g. {missing[0]}")
    files, digests = [], {}
    for n, d, rel in rows:
        p = os.path.join(d, *rel.split("/"))
        if sha256_file(p) != sums[d][rel]:
            raise RuntimeError(f"{p} does not match {d}/SHA256SUMS")
        files.append((n, p))
        digests[n] = sums[d][rel]
    os.makedirs(dist_dir, exist_ok=True)
    return write_zip(os.path.join(dist_dir, pkg.filename), pkg.name, files, pkg.method,
                     readme(pkg), digests, None, pkg.warning_file, limit)


def _job(args):
    pkg, out_dir, dist_dir, limit, sweep_dir = args
    try:
        path = build(pkg, out_dir, dist_dir, limit, sweep_dir)
        return pkg.filename, os.path.getsize(path), sha256_file(path), None
    except (OSError, RuntimeError, ValueError) as e:
        return pkg.filename, 0, None, str(e)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m generator package",
                                 description="Build the release zip files from a generated set.")
    ap.add_argument("--out", help="generated set (default: out; with --gapless: out-gapless)")
    ap.add_argument("--dist", default="dist", help="output directory (default: dist)")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--sweep", action="store_true",
                       help="build the code-sweep zips from a set written with --sweep")
    which.add_argument("--gapless", action="store_true",
                       help="build the gapless zip from a set written with --gapless")
    which.add_argument("--negative", action="store_true",
                       help="build the negative zip from the regular set (--out) and the "
                            "code-sweep set (--out-sweep)")
    ap.add_argument("--out-sweep", help="with --negative: the code-sweep set (default: out-sweep)")
    a = ap.parse_args(argv)
    if a.out_sweep and not a.negative:
        ap.error("--out-sweep goes with --negative")
    a.out = a.out or ("out-gapless" if a.gapless else "out")
    sweep_dir = (a.out_sweep or "out-sweep") if a.negative else None
    pkgs = (SWEEP_RELEASE if a.sweep else (GAPLESS,) if a.gapless else (NEGATIVE,) if a.negative
            else RELEASE)
    with ProcessPoolExecutor(max_workers=len(pkgs)) as pool:
        # the limit goes with each job: a worker imports this module afresh
        res = list(pool.map(_job, [(p, a.out, a.dist, ASSET_LIMIT, sweep_dir) for p in pkgs]))
    ok = [r for r in res if r[3] is None]
    for name, size, _, err in res:
        print(f"{name}: error: {err}" if err else f"{size:>13,d}  {name}",
              file=sys.stderr if err else sys.stdout)
    if os.path.isdir(a.dist):
        # SHA256SUMS lists every zip in dist, of every set, from the
        # files there after every run: a failed zip leaves no stale line
        digests = {name: digest for name, _, digest, _ in ok}
        zips = sorted(n for n in os.listdir(a.dist)
                      if n.endswith(".zip") and os.path.isfile(os.path.join(a.dist, n)))
        with open(os.path.join(a.dist, "SHA256SUMS"), "w") as f:
            for name in zips:
                f.write(f"{digests.get(name) or sha256_file(os.path.join(a.dist, name))}  {name}\n")
    return 0 if len(ok) == len(res) else 1
