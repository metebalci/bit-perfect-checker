# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The BPC1 v1 test-file set: which files exist, their names, tags and lengths."""

import hashlib
import itertools
import os
import struct
from dataclasses import dataclass, replace

import numpy as np

from . import containers, spec
from .signal import Config, dop_frames, dsd_blocks, pcm_blocks, standard_config

WARNING = "TEST - DO NOT PLAY ON SPEAKERS"   # in the tags; never in file names
ARTIST = "bit-perfect-checker"
ALBUM = "BPC1 v1 test signals"

BODY_SECONDS = 10.0         # the standard files' body, at every rate
PCM_DEPTHS = (16, 24)       # published PCM depths, at every PCM rate
EXTRA_DEPTH = 32            # 32-bit PCM: generated on request, not published

# Negative tests (section 11.5): deliberately faulty files in negative/,
# WAV only, one per fault.
NEGATIVE_DIR = "negative"
NEGATIVE_PCM = (44100, 24)
NEGATIVE_DSD = 2822400                    # DoP-WAV at 176.4 kHz
NEGATIVE_SWEEP = (44100, 16)
NEGATIVE_GAPLESS = (44100, 16)
FAULT_BLOCK = 4                           # in the body: block lead + 4
FAULT_FRAME = 1024                        # first faulty frame in that block
FAULT_FRAMES = 1000
FAULTS = ("altered", "incomplete")
GAP = "gap"                               # a gapless pair with silence before part 2
GAP_MS = 10

# The gapless set (section 11.7): regular files cut in two inside the body.
GAPLESS_PCM = ((44100, 16), (44100, 24), (48000, 16), (48000, 24))
GAPLESS_DSD = (2822400, ("dsf", "dff"))
CUT_MS = 7500                              # the cut: one unit after 7.5 s
PARTS = 2

EXT = {"flac": ".flac", "aiff": ".aiff", "alac": ".m4a"}     # beside each PCM or DoP WAV
FILE_EXT = {"wav": ".wav", "dsf": ".dsf", "dff": ".dff", **EXT}
FORMAT_NAMES = {"wav": "WAV", "flac": "FLAC", "aiff": "AIFF", "alac": "ALAC", "dsf": "DSF",
                "dff": "DFF"}

# File numbers (section 11.2): five digits TOFPP before every file name.
TYPE_DIGIT = {"regular": 0, "sweep": 1, "gapless": 2, "negative": 9}
FORMAT_DIGIT = {"wav": 0, "flac": 1, "aiff": 2, "alac": 3, "dsf": 4, "dff": 5}
DEPTH_BASE = {16: 0, 24: 30, 32: 60, 1: 0}
# The negative files take fixed positions in their 9OF group, in this order.
NEGATIVE_POSITIONS = {
    "bpc1_pcm_044k1_24bit_altered": 0, "bpc1_pcm_044k1_24bit_incomplete": 1,
    "bpc1_pcm_044k1_16bit_sweep_altered": 2,
    "bpc1_pcm_044k1_16bit_gapless_part1_gap": 3, "bpc1_pcm_044k1_16bit_gapless_part2_gap": 4,
    "bpc1_dsd064_dop-176k4_24bit_altered": 0, "bpc1_dsd064_dop-176k4_24bit_incomplete": 1,
}


@dataclass(frozen=True)
class FileSpec:
    name: str          # file name without number and extension
    container: str     # "wav", "dsf", "dff", "dop"
    cfg: Config
    formats: tuple     # formats this WAV (PCM or DoP) also ships in: "flac", "aiff", "alac"
    description: str
    fault: str = ""    # negative test: "altered", "incomplete" or "gap"
    part: int = 0      # gapless set: part 1 or 2 of a regular file; 0 for a whole file

    @property
    def ext(self):
        return FILE_EXT[self.format]

    @property
    def format(self):
        """The format of the file itself: "wav" (PCM or DoP), "dsf" or "dff";
        its FLAC, AIFF and ALAC files are in `formats`."""
        return "wav" if self.container == "dop" else self.container

    def number(self, fmt=None):
        """The five-digit number TOFPP of this file as format fmt (default
        its own), section 11.2."""
        fmt = fmt or self.format
        cfg = self.cfg
        if self.fault:
            pp = NEGATIVE_POSITIONS[self.name]
            kind = "negative"
        else:
            rates = spec.DSD_RATES if cfg.dsd else spec.PCM_RATES
            r, b = rates.index(cfg.rate), DEPTH_BASE[cfg.depth]
            pp = b + 2 * r + self.part - 1 if self.part else b + r
            kind = ("gapless" if self.part else "sweep" if cfg.kind == spec.KIND_SWEEP
                    else "regular")
        if not 0 <= pp < 100:
            raise ValueError(f"{self.name}: position {pp} out of range")
        return f"{TYPE_DIGIT[kind]}{int(cfg.dsd)}{FORMAT_DIGIT[fmt]}{pp:02d}"

    @property
    def filename(self):
        return self.filename_as(self.format)

    def filename_as(self, fmt):
        """The file name of this file as format fmt ("wav", "dsf", "dff",
        "flac", "aiff", "alac"): its number, a hyphen, its name."""
        return f"{self.number(fmt)}-{self.name}{FILE_EXT[fmt]}"

    @property
    def relpath(self):
        """Path in a generated set, "/"-separated: a negative file in
        negative/, a gapless part in the folder of its format."""
        return self.relpath_as(self.format)

    def relpath_as(self, fmt):
        """The path of this file as format fmt, e.g. its FLAC, AIFF or ALAC
        file ("flac", "aiff", "alac")."""
        if self.fault:
            return f"{NEGATIVE_DIR}/{self.filename_as(fmt)}"
        if self.part:
            return f"{fmt}/{self.filename_as(fmt)}"
        return self.filename_as(fmt)

    @property
    def units(self):
        """The units of the regular file that this file holds, [u0, u1):
        frames (PCM) or DSD bits per channel (DSD)."""
        if not self.part:
            return 0, self.cfg.total_units
        cut = gapless_cut(self.cfg)
        return (0, cut) if self.part == 1 else (cut, self.cfg.total_units)

    def album(self, fmt):
        """The album tag of this file as format fmt ("wav", "flac", "aiff",
        "alac", "dsf", "dff")."""
        if not self.part:
            return ALBUM
        album = f"BPC1 gapless {self.description} {FORMAT_NAMES[fmt]}"
        return album + ", GAP INSERTED" if self.fault == GAP else album

    def title(self, fmt=None):
        """The title tag of this file as format fmt (default its own): the
        warning, then its number and format, from which its file name
        follows (11.3)."""
        fmt = fmt or self.format
        text = f"{WARNING} - {self.number(fmt)} BPC1 {self.description} {FORMAT_NAMES[fmt]}"
        if self.cfg.kind == spec.KIND_SWEEP:
            text += ", FULL-SCALE NOISE"
        if self.part:
            text += f" gapless, part {self.part} of {PARTS}"
        if self.fault == GAP:
            text += " - NEGATIVE TEST, GAP INSERTED"
        elif self.fault:
            text += f" - NEGATIVE TEST, DELIBERATELY {self.fault.upper()}"
        return text

    @property
    def comment(self):
        if self.part:
            text = (f"BPC1 v1 bit-perfect test signal, gapless pair, part {self.part} of {PARTS}: "
                    f"one test file cut at {CUT_MS / 1000:.3f} s. Play both parts gaplessly; a "
                    f"checker gives one result for the pair. {WARNING} OR HEADPHONES.")
        elif self.cfg.kind == spec.KIND_SWEEP:
            text = f"BPC1 v1 bit-perfect test signal, full-scale code sweep. {WARNING} OR HEADPHONES."
        else:
            text = ("BPC1 v1 bit-perfect test signal, low-level payload with "
                    f"full-scale bursts. {WARNING} OR HEADPHONES.")
        if self.fault == GAP:
            what, _ = fault_text(self)
            text += (f" NEGATIVE TEST, gap inserted: {what}. Played gaplessly, the pair must give "
                     f"{expected(self)}.")
        elif self.fault:
            what, where = fault_text(self)
            if where:
                what += f", {where} (body blocks counted from 0)"
            text += (f" NEGATIVE TEST, deliberately {self.fault}: {what}. "
                     f"A checker must report {expected(self)}.")
        return text


def rate_token(rate):
    """44100 -> '044k1', 48000 -> '048k', 2822400 -> 'dsd064'."""
    if spec.is_dsd_rate(rate):
        return "dsd%03d" % (rate // 44100)
    k, rem = divmod(rate, 1000)
    return "%03dk%s" % (k, str(rem // 100) if rem else "")


def rate_text(rate):
    if spec.is_dsd_rate(rate):
        return "DSD%d" % (rate // 44100)
    return ("%g kHz" % (rate / 1000))


def formats(container, cfg):
    """The formats a PCM or DoP WAV of 16 or 24 bits also ships in: FLAC, AIFF
    and ALAC at every rate, 705.6 and 768 kHz and DoP of DSD256 included.
    FLAC above 655 350 Hz is outside the FLAC subset (encoded with --lax);
    Apple's own ALAC decoders refuse ALAC above 384 kHz. None for 32-bit
    PCM, DSF and DFF."""
    if container == "dop" or (container == "wav" and cfg.depth <= 24):
        return ("flac", "aiff", "alac")
    return ()


def pcm_file(rate, depth, seed=spec.DEFAULT_SEED):
    cfg = standard_config(spec.KIND_PCM, rate, depth, BODY_SECONDS, seed=seed)
    name = f"bpc1_pcm_{rate_token(rate)}_{depth}bit"
    desc = f"PCM {rate_text(rate)} {depth}-bit"
    return FileSpec(name, "wav", cfg, formats("wav", cfg), desc)


def sweep_file(rate, depth, seed=spec.DEFAULT_SEED):
    """A code-sweep file: 2 s lead-in, 0.5 s tail, and a body of 10 s (16-bit)
    or of at least 2^24 sweep units per channel (24-bit)."""
    lead = spec.blocks_for_seconds(2.0, rate)
    cfg = Config(kind=spec.KIND_SWEEP, rate=rate, depth=depth, lead_blocks=lead,
                 body_blocks=spec.sweep_body_blocks(depth, rate, lead),
                 tail_blocks=spec.blocks_for_seconds(0.5, rate), seed=seed)
    return FileSpec(f"bpc1_pcm_{rate_token(rate)}_{depth}bit_sweep", "wav", cfg,
                    formats("wav", cfg),
                    f"PCM {rate_text(rate)} {depth}-bit code sweep")


def dsd_file(rate, container, seed=spec.DEFAULT_SEED):
    """A DSD file: the DSF, DFF and DoP files of a rate carry the same DSD
    bits (one kind, 3.1)."""
    cfg = standard_config(spec.KIND_DSD, rate, 1, BODY_SECONDS, seed=seed)
    name, desc = f"bpc1_{rate_token(rate)}", rate_text(rate)
    if container == "dop":
        carrier = rate // 16
        name += f"_dop-{rate_token(carrier)}_24bit"
        desc += f" DoP {rate_text(carrier)} 24-bit"
    return FileSpec(name, container, cfg, formats(container, cfg), desc)


def fault_start(cfg):
    """First faulty frame (DoP frame for DoP) of a negative test file."""
    frames = cfg.block_len // 16 if cfg.dsd else cfg.block_len
    return (cfg.lead_blocks + FAULT_BLOCK) * frames + FAULT_FRAME


def sweep_fault_code(depth):
    """The code a negative sweep file changes (to the next one): 2^(D-1) - 2."""
    return (1 << (depth - 1)) - 2


def sweep_fault_first(fspec):
    """The first frame a negative sweep file changes: the first body frame
    whose channel 0 sample is the fault code, or None."""
    cfg = fspec.cfg
    code = sweep_fault_code(cfg.depth)
    for a, b in _chunk_ranges(cfg.lead_blocks + cfg.body_blocks, cfg.block_len):
        a = max(a, cfg.lead_blocks)
        if a >= b:
            continue
        hit = np.nonzero(pcm_blocks(cfg, a, b)[:, 0] == code)[0]
        if len(hit):
            return a * cfg.block_len + int(hit[0])
    return None


def gap_frames(cfg):
    """The zero frames a gapless negative pair has before part 2: GAP_MS."""
    return cfg.rate * GAP_MS // 1000


def fault_text(fspec):
    """The fault, and where it is (body blocks counted from 0)."""
    if fspec.fault == GAP:
        return (f"{gap_frames(fspec.cfg)} zero frames ({GAP_MS} ms of silence) before the first "
                f"frame of part 2", "")
    if fspec.cfg.kind == spec.KIND_SWEEP:
        c = sweep_fault_code(fspec.cfg.depth)
        return f"every body sample of channel 0 with code {c} changed to {c + 1}", ""
    where = f"from frame {FAULT_FRAME} of body block {FAULT_BLOCK}"
    if fspec.fault == "incomplete":
        return f"{FAULT_FRAMES} {'DoP frames' if fspec.cfg.dsd else 'frames'} cut out", where
    if fspec.cfg.dsd:
        return f"the DSD bits of {FAULT_FRAMES} DoP frames inverted, markers kept", where
    return "the low 8 bits of every body sample cleared", ""


def expected(fspec):
    """The verdict a checker must report for a negative test file."""
    return "NOT_BIT_PERFECT"


def negative(base, fault):
    """A negative copy of a file, as a WAV only."""
    return replace(base, name=f"{base.name}_{fault}", formats=(), fault=fault)


def negative_set(seed=spec.DEFAULT_SEED):
    """The regular set's negative files: PCM and DoP, each altered and
    incomplete."""
    bases = [pcm_file(*NEGATIVE_PCM, seed=seed), dsd_file(NEGATIVE_DSD, "dop", seed=seed)]
    return [negative(b, f) for b in bases for f in FAULTS]


def sweep_set(seed=spec.DEFAULT_SEED):
    """The code-sweep set, generated on request: 16- and 24-bit at every PCM
    rate, and its negative file."""
    files = [sweep_file(rate, depth, seed=seed) for depth in PCM_DEPTHS for rate in spec.PCM_RATES]
    return files + [negative(sweep_file(*NEGATIVE_SWEEP, seed=seed), "altered")]


def gapless_cut(cfg):
    """The first unit of part 2: one unit after CUT_MS, the unit a frame
    (PCM) or a DoP frame of 16 DSD bits (DSD), so that part 1 of a DSD pair
    holds an odd number of DoP frames."""
    unit = 16 if cfg.dsd else 1
    return (cfg.rate * CUT_MS // 1000 // unit + 1) * unit


def gapless_set(seed=spec.DEFAULT_SEED):
    """The gapless set: parts 1 and 2 of each regular file of the pairs, with
    the regular file's identity. A PCM part is a WAV, also written as FLAC,
    AIFF and ALAC; a DSD part a DSF or DFF file. Then its negative pair, a
    WAV pair with GAP_MS of zero frames before part 2."""
    bases = [pcm_file(r, d, seed=seed) for r, d in GAPLESS_PCM]
    bases += [dsd_file(GAPLESS_DSD[0], c, seed=seed) for c in GAPLESS_DSD[1]]
    files = [replace(b, name=f"{b.name}_gapless_part{p}", part=p)
             for b in bases for p in range(1, PARTS + 1)]
    gap = pcm_file(*NEGATIVE_GAPLESS, seed=seed)
    return files + [negative(replace(gap, name=f"{gap.name}_gapless_part{p}", part=p), GAP)
                    for p in range(1, PARTS + 1)]


def test_set(seed=spec.DEFAULT_SEED, pcm32=False):
    """The published set; with pcm32=True the 32-bit PCM WAVs are added."""
    files = []
    depths = PCM_DEPTHS + ((EXTRA_DEPTH,) if pcm32 else ())
    for depth in depths:
        for rate in spec.PCM_RATES:
            files.append(pcm_file(rate, depth, seed=seed))
    for rate in spec.DSD_RATES:
        for container in ("dsf", "dff", "dop"):
            files.append(dsd_file(rate, container, seed=seed))
    return files + negative_set(seed)


# Writing -------------------------------------------------------------------

def _chunk_ranges(total_blocks, block_len, target=1 << 20):
    step = max(1, target // block_len)
    for k in range(0, total_blocks, step):
        yield k, min(total_blocks, k + step)


def info_tags(fspec):
    tags = [("INAM", fspec.title("wav")), ("IART", ARTIST), ("IPRD", fspec.album("wav")),
            ("ICMT", fspec.comment)]
    return containers.riff_info(tags + ([("ITRK", str(fspec.part))] if fspec.part else []))


def id3_tag(fspec, fmt=None):
    """The ID3v2.3 tag of a DSF or DFF file, or of a gapless part's AIFF."""
    track = f"{fspec.part}/{PARTS}" if fspec.part else None
    fmt = fmt or fspec.format
    return containers.id3v23(fspec.title(fmt), ARTIST, fspec.album(fmt),
                             fspec.comment, track)


def _faulty(fspec, chunks, frames_per_block):
    """Apply a negative test's fault to the chunks of a PCM or DoP WAV."""
    cfg = fspec.cfg
    body = (cfg.lead_blocks * frames_per_block,
            (cfg.lead_blocks + cfg.body_blocks) * frames_per_block)
    f0 = fault_start(cfg)
    for k0, x in chunks:
        n0 = k0 * frames_per_block
        a, b = max(f0, n0) - n0, min(f0 + FAULT_FRAMES, n0 + len(x)) - n0
        if cfg.kind == spec.KIND_SWEEP:
            a, b = max(body[0], n0) - n0, min(body[1], n0 + len(x)) - n0
            if a < b:
                x = x.copy()
                seg = x[a:b, 0]
                seg[seg == sweep_fault_code(cfg.depth)] += 1
        elif fspec.fault == "incomplete":
            if a < b:
                x = np.delete(x, np.s_[a:b], axis=0)
        elif cfg.dsd:
            if a < b:
                x = x.copy()
                x[a:b] ^= 0xFFFF            # the 16 DSD bits; the marker stays
        else:
            a, b = max(body[0], n0) - n0, min(body[1], n0 + len(x)) - n0
            if a < b:
                x = x.copy()
                x[a:b] &= ~0xFF
        yield x


def _part_chunks(cfg, u0, u1, blocks, units_per_block, axis):
    """Units u0..u1-1 of a file in chunks: blocks(cfg, k0, k1) gives blocks
    k0..k1-1, units_per_block of them each along `axis`."""
    k0, k1 = u0 // units_per_block, -(-u1 // units_per_block)
    for a, b in _chunk_ranges(k1 - k0, units_per_block):
        a, b = a + k0, b + k0
        n0 = a * units_per_block
        lo, hi = max(u0, n0) - n0, min(u1, b * units_per_block) - n0
        x = blocks(cfg, a, b)
        yield x[lo:hi] if axis == 0 else x[:, lo:hi]


def write_part(fspec, out_dir):
    """Write one part of the gapless set (a WAV, DSF or DFF) and return its
    path."""
    cfg = fspec.cfg
    path = os.path.join(out_dir, *fspec.relpath.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    u0, u1 = fspec.units
    if fspec.container == "wav":
        chunks = _part_chunks(cfg, u0, u1, pcm_blocks, cfg.block_len, 0)
        gap = gap_frames(cfg) if fspec.fault == GAP and fspec.part == 2 else 0
        if gap:
            chunks = itertools.chain([np.zeros((gap, cfg.nch), np.int32)], chunks)
        containers.write_wav(path, cfg.rate, cfg.nch, cfg.depth, cfg.depth, u1 - u0 + gap,
                             info_tags(fspec), chunks)
    elif fspec.container == "dsf":
        containers.write_dsf(path, cfg.rate, cfg.nch, u1 - u0, id3_tag(fspec),
                             _part_chunks(cfg, u0, u1, dsd_blocks, cfg.block_len, 1))
    elif fspec.container == "dff":
        containers.write_dff(path, cfg.rate, cfg.nch, u1 - u0, fspec.title(), ARTIST, id3_tag(fspec),
                             _part_chunks(cfg, u0, u1, dsd_blocks, cfg.block_len, 1))
    else:
        raise ValueError(f"no gapless part in {fspec.container}")
    return path


def write_file(fspec, out_dir):
    """Write one file of the set and return its path."""
    if fspec.part:
        return write_part(fspec, out_dir)
    cfg = fspec.cfg
    path = os.path.join(out_dir, *fspec.relpath.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cut = FAULT_FRAMES if fspec.fault == "incomplete" else 0
    if fspec.container == "wav":
        chunks = ((a, pcm_blocks(cfg, a, b))
                  for a, b in _chunk_ranges(cfg.total_blocks, cfg.block_len))
        chunks = _faulty(fspec, chunks, cfg.block_len) if fspec.fault else (x for _, x in chunks)
        containers.write_wav(path, cfg.rate, cfg.nch, cfg.depth, cfg.depth,
                             cfg.total_units - cut, info_tags(fspec), chunks)
    elif fspec.container == "dop":
        def dop_chunks():
            for a, b in _chunk_ranges(cfg.total_blocks, cfg.block_len // 16):
                bits = dsd_blocks(cfg, a, b)
                yield a, dop_frames(bits, a * cfg.block_len // 16).astype(np.int32)
        chunks = dop_chunks()
        chunks = _faulty(fspec, chunks, cfg.block_len // 16) if fspec.fault else (x for _, x in chunks)
        containers.write_wav(path, cfg.rate // 16, cfg.nch, 24, 24,
                             cfg.total_units // 16 - cut, info_tags(fspec), chunks)
    else:
        chunks = (dsd_blocks(cfg, a, b)
                  for a, b in _chunk_ranges(cfg.total_blocks, cfg.block_len // 8))
        if fspec.container == "dsf":
            containers.write_dsf(path, cfg.rate, cfg.nch, cfg.total_units,
                                 id3_tag(fspec), chunks)
        else:
            containers.write_dff(path, cfg.rate, cfg.nch, cfg.total_units,
                                 fspec.title(), ARTIST, id3_tag(fspec), chunks)
    return path


def flac_tags(fspec):
    tags = [("TITLE", fspec.title("flac")), ("ARTIST", ARTIST), ("ALBUM", fspec.album("flac")),
            ("COMMENT", fspec.comment)]
    if fspec.part:
        tags += [("TRACKNUMBER", str(fspec.part)), ("TRACKTOTAL", str(PARTS))]
    return tags


def _target(fspec, wav_path, path, fmt):
    """The path to write: `path`, or the file's name as format fmt in the
    WAV's folder."""
    path = path or os.path.join(os.path.dirname(wav_path), fspec.filename_as(fmt))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return path


def write_flac(fspec, wav_path, path=None):
    flac_path = _target(fspec, wav_path, path, "flac")
    rate = fspec.cfg.rate // 16 if fspec.container == "dop" else fspec.cfg.rate
    containers.encode_flac(wav_path, flac_path, flac_tags(fspec), rate)
    return flac_path


def write_aiff(fspec, wav_path, path=None):
    """The AIFF of a PCM or DoP WAV: the title in NAME, the comment in ANNO;
    a gapless part also has an ID3 chunk."""
    aiff_path = _target(fspec, wav_path, path, "aiff")
    containers.write_aiff(wav_path, aiff_path, fspec.title("aiff"), fspec.comment,
                          id3_tag(fspec, "aiff") if fspec.part else None)
    return aiff_path


def alac_tags(fspec):
    """The MP4 tags of a gapless part's ALAC beyond the title, tool and
    comment: artist, album, track number (data type 0) and gapless (pgap,
    type 21)."""
    if not fspec.part:
        return []
    return [(b"\xa9ART", ARTIST), (b"\xa9alb", fspec.album("alac")),
            (b"trkn", (0, struct.pack(">HHHH", 0, fspec.part, PARTS, 0))), (b"pgap", (21, b"\x01"))]


def write_alac(fspec, wav_path, path=None):
    """The ALAC (.m4a) of a PCM or DoP WAV, with the title and comment."""
    m4a_path = _target(fspec, wav_path, path, "alac")
    containers.encode_alac(wav_path, m4a_path, fspec.title("alac"), fspec.comment, alac_tags(fspec))
    return m4a_path


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def job(args):
    """Worker: write one file, and its FLAC, AIFF and ALAC as wanted and as
    its formats allow; return [(name, size, sha256)]."""
    fspec, out_dir, want_flac = args[:3]
    want_aiff, want_alac = args[3:5] if len(args) > 3 else (False, False)
    wav = write_file(fspec, out_dir)
    paths = [wav]
    for fmt, want, write in (("flac", want_flac, write_flac), ("aiff", want_aiff, write_aiff),
                             ("alac", want_alac, write_alac)):
        if want and fmt in fspec.formats:
            paths.append(write(fspec, wav, os.path.join(out_dir, *fspec.relpath_as(fmt).split("/"))))
    return [(os.path.relpath(p, out_dir).replace(os.sep, "/"), os.path.getsize(p), sha256(p))
            for p in paths]
