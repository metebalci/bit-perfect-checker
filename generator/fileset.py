"""The BPC1 v1 test-file set: which files exist, their names, tags and lengths."""

import hashlib
import os
from dataclasses import dataclass

import numpy as np

from . import containers, spec
from .signal import Config, dop_frames, dsd_blocks, pcm_blocks, standard_config

WARNING = "TEST - DO NOT PLAY ON SPEAKERS"   # in the tags; never in file names
ARTIST = "bit-perfect-checker"
ALBUM = "BPC1 v1 test signals"

BODY_SECONDS = 10.0         # the standard files' body, at every rate
PCM_DEPTHS = (16, 24)       # published PCM depths, at every PCM rate
EXTRA_DEPTH = 32            # 32-bit PCM: generated on request, not published

# Negative tests (section 11.5): deliberately faulty files in negative/.
NEGATIVE_DIR = "negative"
NEGATIVE_PCM = ((44100, 24), (48000, 24))
NEGATIVE_DSD = 2822400                    # DoP-WAV at 176.4 kHz
FAULT_BLOCK = 4                           # in the body: block lead + 4
FAULT_FRAME = 1024                        # first faulty frame in that block
FAULT_FRAMES = 1000
FAULTS = ("altered", "incomplete")


@dataclass(frozen=True)
class FileSpec:
    name: str          # file name without extension
    container: str     # "wav", "dsf", "dff", "dop"
    cfg: Config
    formats: tuple     # formats this WAV (PCM or DoP) also ships in: "flac", "aiff", "alac"
    description: str
    fault: str = ""    # negative test: "altered" or "incomplete"

    @property
    def ext(self):
        return {"wav": ".wav", "dop": ".wav", "dsf": ".dsf", "dff": ".dff"}[self.container]

    @property
    def filename(self):
        return self.name + self.ext

    @property
    def relpath(self):
        """Path in a generated set and in the release archives, "/"-separated."""
        return f"{NEGATIVE_DIR}/{self.filename}" if self.fault else self.filename

    @property
    def title(self):
        if self.fault:
            return (f"{WARNING} - BPC1 {self.description} - NEGATIVE TEST, "
                    f"DELIBERATELY {self.fault.upper()}")
        return f"{WARNING} - BPC1 {self.description}"

    @property
    def comment(self):
        if self.cfg.kind == spec.KIND_SWEEP:
            text = f"BPC1 v1 bit-perfect test signal, full-scale code sweep. {WARNING} OR HEADPHONES."
        else:
            text = ("BPC1 v1 bit-perfect test signal, low-level payload with "
                    f"full-scale bursts. {WARNING} OR HEADPHONES.")
        if self.fault:
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
                    f"PCM {rate_text(rate)} {depth}-bit code sweep, FULL-SCALE NOISE")


DSD_KINDS = {"dsf": spec.KIND_DSD_DSF, "dff": spec.KIND_DSD_DFF, "dop": spec.KIND_DSD_DOP}


def dsd_file(rate, container, seed=spec.DEFAULT_SEED):
    cfg = standard_config(DSD_KINDS[container], rate, 1, BODY_SECONDS, seed=seed)
    name = f"bpc1_{rate_token(rate)}_"
    desc = f"{rate_text(rate)} "
    if container == "dop":
        carrier = rate // 16
        name += f"dop-{rate_token(carrier)}_24bit"
        desc += f"DoP {rate_text(carrier)} 24-bit"
    else:
        name += container
        desc += container.upper()
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


def fault_text(fspec):
    """The fault, and where it is (body blocks counted from 0)."""
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
    return FileSpec(f"{base.name}_{fault}", base.container, base.cfg, base.formats,
                    base.description, fault)


def negative_set(seed=spec.DEFAULT_SEED):
    bases = [pcm_file(r, d, seed=seed) for r, d in NEGATIVE_PCM]
    bases.append(dsd_file(NEGATIVE_DSD, "dop", seed=seed))
    return [negative(b, f) for b in bases for f in FAULTS]


def sweep_set(seed=spec.DEFAULT_SEED):
    """The code-sweep set, generated on request: 16- and 24-bit at every PCM
    rate, and its negative files."""
    files = [sweep_file(rate, depth, seed=seed) for depth in PCM_DEPTHS for rate in spec.PCM_RATES]
    neg = [negative(sweep_file(44100, depth, seed=seed), "altered") for depth in PCM_DEPTHS]
    return files + neg


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
    return containers.riff_info([("INAM", fspec.title), ("IART", ARTIST),
                                 ("IPRD", ALBUM), ("ICMT", fspec.comment)])


def id3_tag(fspec):
    return containers.id3v23(fspec.title, ARTIST, ALBUM, fspec.comment)


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


def write_file(fspec, out_dir):
    """Write one file of the set and return its path."""
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
                                 fspec.title, ARTIST, id3_tag(fspec), chunks)
    return path


def flac_tags(fspec):
    return [("TITLE", fspec.title), ("ARTIST", ARTIST), ("ALBUM", ALBUM),
            ("COMMENT", fspec.comment)]


def write_flac(fspec, wav_path):
    flac_path = wav_path[:-4] + ".flac"
    rate = fspec.cfg.rate // 16 if fspec.container == "dop" else fspec.cfg.rate
    containers.encode_flac(wav_path, flac_path, flac_tags(fspec), rate)
    return flac_path


def write_aiff(fspec, wav_path):
    """The AIFF of a PCM or DoP WAV: the title in NAME, the comment in ANNO."""
    aiff_path = wav_path[:-4] + ".aiff"
    containers.write_aiff(wav_path, aiff_path, fspec.title, fspec.comment)
    return aiff_path


def write_alac(fspec, wav_path):
    """The ALAC (.m4a) of a PCM or DoP WAV, with the title and comment."""
    m4a_path = wav_path[:-4] + ".m4a"
    containers.encode_alac(wav_path, m4a_path, fspec.title, fspec.comment)
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
    if want_flac and "flac" in fspec.formats:
        paths.append(write_flac(fspec, wav))
    if want_aiff and "aiff" in fspec.formats:
        paths.append(write_aiff(fspec, wav))
    if want_alac and "alac" in fspec.formats:
        paths.append(write_alac(fspec, wav))
    return [(os.path.relpath(p, out_dir).replace(os.sep, "/"), os.path.getsize(p), sha256(p))
            for p in paths]
