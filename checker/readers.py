"""Readers: WAV, FLAC, AIFF, ALAC (.m4a), DSF, DFF and raw captures -> a Stream.

A PCM stream holds 32-bit left-justified words W (int32, shape frames x nch):
a D-bit sample s is W = s * 2**(32-D). A DSD stream holds bytes per channel
(uint8, shape bytes x nch), the earliest DSD bit in the MSB.
"""

import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np

BITREV = np.array([int(f"{i:08b}"[::-1], 2) for i in range(256)], dtype=np.uint8)


@dataclass
class Stream:
    dsd: bool
    rate: int               # PCM frame rate, or DSD bit rate per channel
    nch: int
    data: np.ndarray        # PCM: int32 W (frames, nch); DSD: uint8 (bytes, nch)
    container: str
    slot_bits: int = 0      # PCM container bits per sample
    valid_bits: int = 0     # declared valid bits
    is_float: bool = False
    dop: bool = False       # DSD stream unpacked from DoP
    notes: list = field(default_factory=list)


# WAV ----------------------------------------------------------------------

def _chunks_le(data, pos):
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)


def pcm_to_w(raw, bits, is_float, nch):
    """Little-endian sample bytes -> int32 W (frames, nch)."""
    if is_float:
        f = np.frombuffer(raw, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
        w = np.clip(np.rint(f * 2.0 ** 31), -2.0 ** 31, 2.0 ** 31 - 1).astype(np.int32)
    elif bits == 16:
        w = np.frombuffer(raw, dtype="<i2").astype(np.int32) << 16
    elif bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.uint32)
        w = ((b[:, 2] << 24) | (b[:, 1] << 16) | (b[:, 0] << 8)).view(np.int32)
    elif bits == 32:
        w = np.frombuffer(raw, dtype="<i4").astype(np.int32)
    elif bits == 8:
        w = (np.frombuffer(raw, dtype=np.uint8).astype(np.int32) - 128) << 24
    else:
        raise ValueError(f"unsupported sample size {bits}")
    n = len(w) // nch
    return np.ascontiguousarray(w[:n * nch].reshape(n, nch))


def read_wav_bytes(data, container="WAV"):
    if data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    fmt = pcm = None
    for cid, off, size in _chunks_le(data, 12):
        if cid == b"fmt ":
            fmt = data[off:off + size]
        elif cid == b"data":
            pcm = data[off:off + size] if off + size <= len(data) else data[off:]
    if fmt is None or pcm is None:
        raise ValueError("WAV without fmt or data chunk")
    tag, nch, rate, _, align, bits = struct.unpack_from("<HHIIHH", fmt)
    valid = bits
    if tag == 0xFFFE:
        valid = struct.unpack_from("<H", fmt, 18)[0]
        sub = fmt[24:40]
        tag = struct.unpack_from("<H", sub)[0]
        container += " (EXTENSIBLE)"
    if tag not in (1, 3):
        raise ValueError(f"unsupported WAV format tag {tag:#x}")
    slot = 8 * align // nch
    w = pcm_to_w(pcm, slot, tag == 3, nch)
    return Stream(False, rate, nch, w, container, slot_bits=slot,
                  valid_bits=valid, is_float=(tag == 3))


def read_flac(path):
    if not shutil.which("flac"):
        raise RuntimeError("reading FLAC needs the flac command-line tool (>= 1.4.0)")
    wav = subprocess.run(["flac", "-d", "-c", "--silent", path], check=True,
                         capture_output=True).stdout
    return read_wav_bytes(wav, container="FLAC")


def read_aiff(data):
    """AIFF (uncompressed, big-endian PCM): COMM and SSND."""
    if data[:4] != b"FORM" or data[8:12] != b"AIFF":
        raise ValueError("not an AIFF file")
    pos, comm, ssnd = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from(">I", data, pos + 4)[0]
        if cid == b"COMM":
            comm = data[pos + 8:pos + 8 + size]
        elif cid == b"SSND":
            offset = struct.unpack_from(">I", data, pos + 8)[0]
            ssnd = data[pos + 16 + offset:pos + 8 + size]
        pos += 8 + size + (size & 1)
    if comm is None or ssnd is None:
        raise ValueError("AIFF without COMM or SSND chunk")
    nch, frames, bits = struct.unpack_from(">hIh", comm)
    exp, mant = struct.unpack_from(">HQ", comm, 8)
    e = (exp & 0x7FFF) - 16383
    rate = mant >> (63 - e) if 0 <= e <= 63 else 0
    slot = -(-bits // 8) * 8
    width = slot // 8
    raw = np.frombuffer(ssnd[:frames * nch * width], dtype=np.uint8).reshape(-1, width)
    if slot == 8:                                              # AIFF: signed 8-bit
        w = np.ascontiguousarray((raw[:, 0].view(np.int8).astype(np.int32) << 24)
                                 [:frames * nch].reshape(-1, nch))
    else:
        le = np.ascontiguousarray(raw[:, ::-1]).tobytes()      # to little-endian samples
        w = pcm_to_w(le, slot, False, nch)
    return Stream(False, rate, nch, w, "AIFF", slot_bits=slot, valid_bits=bits)


def read_alac(path):
    """ALAC in MP4 (.m4a), decoded by Apple's reference decoder (the helper of
    generator/alac.py). Not ffmpeg's: it decodes some packets of Apple's
    encoder wrongly (a Rice-history clamp that differs from Apple's)."""
    from generator import alac, containers
    why = alac.missing()
    if why:
        raise RuntimeError(f"reading ALAC (.m4a): {why}")
    track = containers.read_alac_track(path)
    c = alac.parse_cookie(track.cookie)
    bits, nch, rate = c["bit_depth"], c["num_channels"], c["sample_rate"]
    if bits not in (16, 20, 24, 32):
        raise ValueError(f"{path}: ALAC with {bits} bits")
    slot = {16: 16, 20: 24, 24: 24, 32: 32}[bits]
    with tempfile.TemporaryDirectory(prefix="bpc1-alac-") as d:
        out = os.path.join(d, "out.pcm")
        alac.decode(path, track.packets, track.cookie, out)
        with open(out, "rb") as f:
            raw = f.read()
    w = pcm_to_w(raw, slot, False, nch)
    return Stream(False, rate, nch, w, "ALAC", slot_bits=slot, valid_bits=bits)


# DSF ----------------------------------------------------------------------

def read_dsf(data):
    if data[:4] != b"DSD ":
        raise ValueError("not a DSF file")
    pos = struct.unpack_from("<Q", data, 4)[0]
    fmt = None
    while pos + 12 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<Q", data, pos + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<IIIIIIQI", data, pos + 12)
        elif cid == b"data":
            body = np.frombuffer(data, dtype=np.uint8, count=size - 12, offset=pos + 12)
            break
        pos += size
    else:
        raise ValueError("DSF without data chunk")
    _, _, _, nch, rate, bps, count, bsize = fmt
    nblk = len(body) // (bsize * nch)
    arr = body[:nblk * bsize * nch].reshape(nblk, nch, bsize).transpose(1, 0, 2)
    arr = arr.reshape(nch, nblk * bsize)[:, :(count + 7) // 8]
    if bps == 1:
        arr = BITREV[arr]
    return Stream(True, rate, nch, np.ascontiguousarray(arr.T), "DSF")


# DFF ----------------------------------------------------------------------

def read_dff(data):
    if data[:4] != b"FRM8" or data[12:16] != b"DSD ":
        raise ValueError("not a DSDIFF file")
    pos, end = 16, 12 + struct.unpack_from(">Q", data, 4)[0]
    rate = nch = None
    while pos + 12 <= min(end, len(data)):
        cid = data[pos:pos + 4]
        size = struct.unpack_from(">Q", data, pos + 4)[0]
        body = pos + 12
        if cid == b"PROP":
            p = body + 4
            while p < body + size:
                sid = data[p:p + 4]
                ssize = struct.unpack_from(">Q", data, p + 4)[0]
                if sid == b"FS  ":
                    rate = struct.unpack_from(">I", data, p + 12)[0]
                elif sid == b"CHNL":
                    nch = struct.unpack_from(">H", data, p + 12)[0]
                elif sid == b"CMPR" and data[p + 12:p + 16] != b"DSD ":
                    raise ValueError("compressed (DST) DSDIFF is not supported")
                p += 12 + ssize + (ssize & 1)
        elif cid == b"DSD ":
            raw = np.frombuffer(data, dtype=np.uint8, count=size, offset=body)
            n = len(raw) // nch
            return Stream(True, rate, nch, raw[:n * nch].reshape(n, nch), "DFF")
        pos = body + size + (size & 1)
    raise ValueError("DSDIFF without DSD sound data chunk")


# Raw captures ----------------------------------------------------------------

RAW_FORMATS = ("s16le", "s24_3le", "s24le", "s32le", "f32le",
               "dsd_u8", "dsd_u16_be", "dsd_u32_be", "dsd_u32_le")


def read_raw(data, fmt, rate, nch):
    """`rate` is the frame rate of the raw format (ALSA convention)."""
    if fmt == "s16le":
        return Stream(False, rate, nch, pcm_to_w(data, 16, False, nch), "raw s16le", 16, 16)
    if fmt == "s24_3le":
        return Stream(False, rate, nch, pcm_to_w(data, 24, False, nch), "raw s24_3le", 24, 24)
    if fmt == "s24le":      # 24 valid bits in the low bytes of 32-bit words
        w = pcm_to_w(data, 32, False, nch) << 8
        return Stream(False, rate, nch, w, "raw s24le", 32, 24)
    if fmt == "s32le":
        return Stream(False, rate, nch, pcm_to_w(data, 32, False, nch), "raw s32le", 32, 32)
    if fmt == "f32le":
        return Stream(False, rate, nch, pcm_to_w(data, 32, True, nch), "raw f32le",
                      32, 24, is_float=True)
    if fmt.startswith("dsd_"):
        width = {"dsd_u8": 1, "dsd_u16_be": 2, "dsd_u32_be": 4, "dsd_u32_le": 4}[fmt]
        b = np.frombuffer(data, dtype=np.uint8)
        n = len(b) // (width * nch)
        b = b[:n * width * nch].reshape(n, nch, width)
        if fmt == "dsd_u32_le":
            b = b[:, :, ::-1]
        out = b.transpose(0, 2, 1).reshape(n * width, nch)
        return Stream(True, rate * 8 * width, nch, np.ascontiguousarray(out), f"raw {fmt}")
    raise ValueError(f"unknown raw format {fmt}; use one of {', '.join(RAW_FORMATS)}")


# DoP ----------------------------------------------------------------------

def dop_markers(w):
    """Fraction of frames carrying the alternating DoP markers on all channels."""
    top = ((w >> 24) & 0xFF).astype(np.int32)
    same = (top == top[:, :1]).all(axis=1)
    m = top[:, 0]
    good = same & (((m == 0x05) & (np.roll(m, -1) == 0xFA)) |
                   ((m == 0xFA) & (np.roll(m, -1) == 0x05)))
    return good


def detect_dop(s):
    if s.dsd or s.slot_bits < 24 or len(s.data) < 32:
        return False
    return dop_markers(s.data).mean() > 0.5


def unpack_dop(s):
    """PCM stream carrying DoP -> DSD stream (16 DSD bits per frame)."""
    good = dop_markers(s.data)
    w = s.data.view(np.uint32)
    b1 = ((w >> 16) & 0xFF).astype(np.uint8)
    b2 = ((w >> 8) & 0xFF).astype(np.uint8)
    out = np.empty((2 * len(w), s.nch), dtype=np.uint8)
    out[0::2] = b1
    out[1::2] = b2
    d = Stream(True, s.rate * 16, s.nch, out, s.container + ", DoP",
               slot_bits=s.slot_bits, valid_bits=s.valid_bits, dop=True)
    bad = int((~good).sum())
    if bad:
        d.notes.append(f"{bad} frames without valid DoP markers")
    return d


def read_file(path, raw=None, rate=None, nch=None):
    if raw:
        with open(path, "rb") as f:
            return read_raw(f.read(), raw, rate, nch)
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:4] == b"fLaC":
        return read_flac(path)
    if head[4:8] == b"ftyp":
        return read_alac(path)
    with open(path, "rb") as f:
        data = f.read()
    if head[:4] == b"FORM" and head[8:12] == b"AIFF":
        return read_aiff(data)
    if head[:4] in (b"RIFF", b"RF64"):
        return read_wav_bytes(data)
    if head[:4] == b"DSD ":
        return read_dsf(data)
    if head[:4] == b"FRM8":
        return read_dff(data)
    raise ValueError(f"{path}: unknown file type (use --raw for raw captures)")
