"""Container writers for BPC1 files: WAV, DSF, DFF, DoP-in-WAV, FLAC, AIFF,
ALAC (MP4).

Every writer is deterministic: no timestamps, no host or tool data that
varies between runs. FLAC files additionally depend on the flac version;
ALAC files are made with Apple's reference encoder, pinned as a submodule
(generator/alac.py).
"""

import os
import shutil
import struct
import subprocess
import tempfile

import numpy as np

from . import alac

KSDATAFORMAT_SUBTYPE_PCM = bytes.fromhex("0100000000001000800000aa00389b71")
CHANNEL_MASKS = {1: 0x4, 2: 0x3}


# Tags ----------------------------------------------------------------------

def _pad_even(b):
    return b + b"\x00" if len(b) % 2 else b


def riff_info(tags):
    """RIFF LIST/INFO chunk. tags: list of (4-char id, ASCII text)."""
    body = b"INFO"
    for cid, text in tags:
        data = text.encode("ascii") + b"\x00"
        body += cid.encode("ascii") + struct.pack("<I", len(data)) + _pad_even(data)
    return b"LIST" + struct.pack("<I", len(body)) + body


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3v23(title, artist, album, comment):
    """Minimal ID3v2.3 tag with ISO-8859-1 (here: ASCII) text frames."""
    def frame(fid, data):
        return fid.encode("ascii") + struct.pack(">I", len(data)) + b"\x00\x00" + data
    frames = (frame("TIT2", b"\x00" + title.encode("ascii"))
              + frame("TPE1", b"\x00" + artist.encode("ascii"))
              + frame("TALB", b"\x00" + album.encode("ascii"))
              + frame("COMM", b"\x00eng\x00" + comment.encode("ascii")))
    return b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames


# WAV -----------------------------------------------------------------------

def wav_header(rate, nch, container_bits, valid_bits, nframes, info):
    block_align = nch * container_bits // 8
    extensible = container_bits > 16 or valid_bits != container_bits or nch > 2
    if extensible:
        mask = CHANNEL_MASKS.get(nch, (1 << nch) - 1)
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, nch, rate, rate * block_align,
                          block_align, container_bits, 22, valid_bits, mask)
        fmt += KSDATAFORMAT_SUBTYPE_PCM
    else:
        fmt = struct.pack("<HHIIHH", 1, nch, rate, rate * block_align,
                          block_align, container_bits)
    data_len = nframes * block_align
    chunks = (b"fmt " + struct.pack("<I", len(fmt)) + fmt + info
              + b"data" + struct.pack("<I", data_len))
    riff_len = 4 + len(chunks) + data_len + (data_len & 1)
    if riff_len > 0xFFFFFFFF:
        raise ValueError("file too large for RIFF")
    return b"RIFF" + struct.pack("<I", riff_len) + b"WAVE" + chunks, data_len


def pcm_bytes(samples, container_bits):
    """int32 array (frames, nch) of container-depth values -> LE bytes."""
    if container_bits == 16:
        return samples.astype("<i2").tobytes()
    if container_bits == 32:
        return samples.astype("<i4").tobytes()
    if container_bits == 24:
        b = np.ascontiguousarray(samples, dtype="<i4").view(np.uint8).reshape(-1, 4)
        return b[:, :3].tobytes()
    raise ValueError(container_bits)


def write_wav(path, rate, nch, container_bits, valid_bits, nframes, info, chunks):
    """Write a WAV file; `chunks` yields int32 arrays (frames, nch)."""
    header, data_len = wav_header(rate, nch, container_bits, valid_bits,
                                  nframes, info)
    written = 0
    with open(path, "wb") as f:
        f.write(header)
        for samples in chunks:
            data = pcm_bytes(samples, container_bits)
            written += len(data)
            f.write(data)
        if data_len & 1:
            f.write(b"\x00")
    if written != data_len:
        raise RuntimeError(f"{path}: wrote {written} of {data_len} data bytes")


# DSF (Sony DSD Stream File 1.01): little-endian, LSB-first bytes --------------

DSF_BLOCK = 4096
DSF_CHANNEL_TYPES = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 7}


def write_dsf(path, rate, nch, nbits, tag, chunks):
    """`chunks` yields uint8 bit arrays (nch, bits); bits a multiple of 32768."""
    if nbits % (8 * DSF_BLOCK):
        raise ValueError("DSF writer needs whole 4096-byte blocks")
    data_len = nch * nbits // 8
    data_off = 28 + 52
    meta_off = data_off + 12 + data_len
    total = meta_off + len(tag)
    with open(path, "wb") as f:
        f.write(b"DSD " + struct.pack("<QQQ", 28, total, meta_off))
        f.write(b"fmt " + struct.pack("<QIIIIIIQII", 52, 1, 0,
                                      DSF_CHANNEL_TYPES[nch], nch, rate, 1,
                                      nbits, DSF_BLOCK, 0))
        f.write(b"data" + struct.pack("<Q", 12 + data_len))
        written = 0
        for bits in chunks:
            packed = np.packbits(bits, axis=1, bitorder="little")  # (nch, bytes)
            nblk = packed.shape[1] // DSF_BLOCK
            arr = packed.reshape(nch, nblk, DSF_BLOCK).transpose(1, 0, 2)
            data = arr.tobytes()
            written += len(data)
            f.write(data)
        f.write(tag)
    if written != data_len:
        raise RuntimeError(f"{path}: wrote {written} of {data_len} data bytes")


# DFF (Philips DSDIFF 1.5): big-endian, MSB-first, byte-interleaved -----------

def _dff_chunk(cid, body):
    return cid + struct.pack(">Q", len(body)) + _pad_even(body)


def _dff_text(cid, text):
    t = text.encode("ascii")
    return _dff_chunk(cid, struct.pack(">I", len(t)) + t)


def dff_channel_ids(nch):
    if nch == 2:
        return [b"SLFT", b"SRGT"]
    if nch == 1:
        return [b"C   "]
    return [b"C%03d" % i for i in range(nch)]


def write_dff(path, rate, nch, nbits, title, artist, tag, chunks):
    data_len = nch * nbits // 8
    prop = b"SND "
    prop += _dff_chunk(b"FS  ", struct.pack(">I", rate))
    prop += _dff_chunk(b"CHNL", struct.pack(">H", nch) + b"".join(dff_channel_ids(nch)))
    prop += _dff_chunk(b"CMPR", b"DSD " + bytes([14]) + b"not compressed" + b"\x00")
    if nch == 2:
        prop += _dff_chunk(b"LSCO", struct.pack(">H", 0))
    head = _dff_chunk(b"FVER", struct.pack(">I", 0x01050000)) + _dff_chunk(b"PROP", prop)
    diin = _dff_chunk(b"DIIN", _dff_text(b"DIAR", artist) + _dff_text(b"DITI", title))
    trailer = (b"\x00" if data_len & 1 else b"") + diin + _dff_chunk(b"ID3 ", tag)
    frm_len = 4 + len(head) + 12 + data_len + len(trailer)
    with open(path, "wb") as f:
        f.write(b"FRM8" + struct.pack(">Q", frm_len) + b"DSD ")
        f.write(head)
        f.write(b"DSD " + struct.pack(">Q", data_len))
        written = 0
        for bits in chunks:
            packed = np.packbits(bits, axis=1, bitorder="big")      # (nch, bytes)
            data = packed.T.tobytes()                               # interleave
            written += len(data)
            f.write(data)
        f.write(trailer)
    if written != data_len:
        raise RuntimeError(f"{path}: wrote {written} of {data_len} data bytes")


# FLAC via the reference command-line tool -------------------------------------

FLAC_MIN_VERSION = (1, 4, 0)


def flac_version():
    """Return the flac tool's version tuple, or None if it is not usable."""
    exe = shutil.which("flac")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             check=True).stdout.split()
        return tuple(int(x) for x in out[1].split(".")[:3])
    except (OSError, subprocess.CalledProcessError, IndexError, ValueError):
        return None


def flac_usable():
    v = flac_version()
    return v is not None and v >= FLAC_MIN_VERSION


FLAC_SUBSET_MAX_RATE = 655350


def encode_flac(wav_path, flac_path, tags, rate):
    """Encode a WAV with `flac -8`, then decode it and compare the PCM.

    Rates above 655 350 Hz are outside the FLAC subset; flac needs --lax
    for them (PCM at 705.6 and 768 kHz, and DoP of DSD256 at 705.6 kHz)."""
    cmd = ["flac", "--silent", "--force", "--no-preserve-modtime", "-8",
           "--verify", "-o", flac_path]
    if rate > FLAC_SUBSET_MAX_RATE:
        cmd.append("--lax")
    for k, v in tags:
        cmd += ["-T", f"{k}={v}"]
    cmd.append(wav_path)
    subprocess.run(cmd, check=True, capture_output=True,
                   env=dict(os.environ, LC_ALL="C.UTF-8"))
    if decode_flac_pcm(flac_path) != wav_pcm_data(wav_path):
        raise RuntimeError(f"{flac_path}: decoded PCM differs from {wav_path}")


def decode_flac_wav(flac_path):
    """Decode a FLAC file with `flac -d` and return the WAV bytes."""
    return subprocess.run(["flac", "-d", "-c", "--silent", flac_path],
                          check=True, capture_output=True).stdout


def wav_chunks(data):
    """Yield (id, payload) of the RIFF chunks in a WAV file image."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        yield cid, data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)


def wav_data(image):
    for cid, payload in wav_chunks(image):
        if cid == b"data":
            return payload
    raise ValueError("no data chunk")


def wav_pcm_data(path):
    with open(path, "rb") as f:
        return wav_data(f.read())


def decode_flac_pcm(flac_path):
    return wav_data(decode_flac_wav(flac_path))


# WAV image -> format ----------------------------------------------------------

def wav_format(image):
    """(nch, rate, container bits) of a WAV file image."""
    for cid, payload in wav_chunks(image):
        if cid == b"fmt ":
            _, nch, rate, _, _, bits = struct.unpack_from("<HHIIHH", payload)
            return nch, rate, bits
    raise ValueError("no fmt chunk")


# AIFF: big-endian PCM, COMM with the 80-bit extended sample rate ---------------

def ieee_extended(rate):
    """An integer rate as the 80-bit IEEE 754 extended float of AIFF's COMM
    chunk: sign 0, 15-bit exponent (bias 16383), 64-bit mantissa with an
    explicit leading 1."""
    if rate <= 0:
        raise ValueError(rate)
    e = rate.bit_length() - 1
    return struct.pack(">HQ", 16383 + e, rate << (63 - e))


def parse_ieee_extended(b):
    exp, mant = struct.unpack(">HQ", b)
    return mant / (1 << 63) * 2.0 ** ((exp & 0x7FFF) - 16383)


def _aiff_chunk(cid, body):
    return cid + struct.pack(">I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")


def write_aiff(wav_path, aiff_path, name, annotation):
    """Write an AIFF with the samples of a PCM WAV (16 or 24 bits): FORM AIFF
    with COMM, NAME (the title), ANNO (the comment) and SSND (offset and
    block size 0), the samples big-endian, chunks padded to even length."""
    with open(wav_path, "rb") as f:
        image = f.read()
    nch, rate, bits = wav_format(image)
    data = wav_data(image)
    width = bits // 8
    frames = len(data) // (nch * width)
    be = np.frombuffer(data, dtype=np.uint8).reshape(-1, width)[:, ::-1].tobytes()
    comm = struct.pack(">hIh", nch, frames, bits) + ieee_extended(rate)
    head = (_aiff_chunk(b"COMM", comm) + _aiff_chunk(b"NAME", name.encode("ascii"))
            + _aiff_chunk(b"ANNO", annotation.encode("ascii")))
    ssnd = b"SSND" + struct.pack(">III", 8 + len(be), 0, 0)
    size = 4 + len(head) + len(ssnd) + len(be) + (len(be) & 1)
    if size > 0xFFFFFFFF:
        raise ValueError("file too large for AIFF")
    with open(aiff_path, "wb") as f:
        f.write(b"FORM" + struct.pack(">I", size) + b"AIFF" + head + ssnd)
        f.write(be)
        if len(be) & 1:
            f.write(b"\x00")


def read_aiff(path):
    """(nch, rate, bits, chunks, samples) of an AIFF: samples int32 (frames,
    nch) at the file depth, chunks {id: payload} without SSND."""
    with open(path, "rb") as f:
        image = f.read()
    if image[:4] != b"FORM" or image[8:12] != b"AIFF":
        raise ValueError("not an AIFF file")
    pos, chunks, ssnd = 12, {}, None
    while pos + 8 <= len(image):
        cid, size = image[pos:pos + 4], struct.unpack_from(">I", image, pos + 4)[0]
        body = image[pos + 8:pos + 8 + size]
        if cid == b"SSND":
            offset = struct.unpack_from(">I", body)[0]
            ssnd = body[8 + offset:]
        else:
            chunks[cid] = body
        pos += 8 + size + (size & 1)
    nch, frames, bits = struct.unpack_from(">hIh", chunks[b"COMM"])
    rate = parse_ieee_extended(chunks[b"COMM"][8:18])
    width = bits // 8
    raw = np.frombuffer(ssnd[:frames * nch * width], dtype=np.uint8).reshape(-1, width)
    le = np.zeros((len(raw), 4), dtype=np.uint8)
    le[:, 4 - width:] = raw[:, ::-1]
    samples = (le.view("<i4").reshape(frames, nch) >> (32 - bits)).astype(np.int32)
    return nch, rate, bits, chunks, samples


# ALAC in MP4, with Apple's reference encoder ------------------------------------

ALAC_TOOL = "Apple ALAC encoder, macosforge/alac " + alac.CODEC_COMMIT[:7]   # the \xa9too tag
ALAC_PACKETS_PER_CHUNK = 32


def alac_usable():
    return alac.usable()


def wav_data_span(path):
    """(nch, rate, container bits, offset, size) of a WAV's data chunk, from
    its chunk headers."""
    fmt = None
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAVE file")
        pos = 12
        while True:
            f.seek(pos)
            h = f.read(8)
            if len(h) < 8:
                raise ValueError("no data chunk")
            cid, size = h[:4], struct.unpack("<I", h[4:])[0]
            if cid == b"fmt ":
                fmt = struct.unpack("<HHIIHH", f.read(16))
            elif cid == b"data":
                if fmt is None:
                    raise ValueError("data before fmt")
                return fmt[1], fmt[2], fmt[5], pos + 8, size
            pos += 8 + size + (size & 1)


def _box(kind, body):
    return struct.pack(">I", 8 + len(body)) + kind + body


def _full_box(kind, body, version=0, flags=0):
    return _box(kind, struct.pack(">I", (version << 24) | flags) + body)


UNITY_MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def mp4_alac_moov(rate, nch, bits, cookie, sizes, frames, data_offset, tags):
    """The moov box of an ALAC track whose packets lie back to back from
    data_offset: no times (creation and modification 0), the track's time
    scale the sample rate, chunks of ALAC_PACKETS_PER_CHUNK packets, and the
    tags [(4-byte key, text)] in udta/meta/ilst."""
    n = len(sizes)
    mvhd = _full_box(b"mvhd", struct.pack(">IIII", 0, 0, rate, frames) + struct.pack(">IH", 0x10000, 0x100)
                     + bytes(10) + UNITY_MATRIX + bytes(24) + struct.pack(">I", 2))
    tkhd = _full_box(b"tkhd", struct.pack(">IIIII", 0, 0, 1, 0, frames) + bytes(8)
                     + struct.pack(">hhhH", 0, 1, 0x100, 0) + UNITY_MATRIX + struct.pack(">II", 0, 0),
                     flags=3)
    mdhd = _full_box(b"mdhd", struct.pack(">IIIIHH", 0, 0, rate, frames, 0x55C4, 0))  # "und"
    hdlr = _full_box(b"hdlr", struct.pack(">I", 0) + b"soun" + bytes(12) + b"SoundHandler\0")
    smhd = _full_box(b"smhd", struct.pack(">hH", 0, 0))
    dinf = _box(b"dinf", _full_box(b"dref", struct.pack(">I", 1) + _full_box(b"url ", b"", flags=1)))
    entry = _box(b"alac", bytes(6) + struct.pack(">HHHIHHHHI", 1, 0, 0, 0, nch, bits, 0, 0,
                                                    rate << 16 if rate <= 0xFFFF else 0)
                 + _full_box(b"alac", cookie))
    stsd = _full_box(b"stsd", struct.pack(">I", 1) + entry)
    last = frames - (n - 1) * alac.FRAMES_PER_PACKET if n else 0
    runs = ([(n - 1, alac.FRAMES_PER_PACKET), (1, last)] if n and last != alac.FRAMES_PER_PACKET
            else [(n, alac.FRAMES_PER_PACKET)] if n else [])
    runs = [r for r in runs if r[0]]
    stts = _full_box(b"stts", struct.pack(">I", len(runs)) + b"".join(struct.pack(">II", *r) for r in runs))
    per = ALAC_PACKETS_PER_CHUNK
    nchunks = -(-n // per)
    chunks = [(1, per)] if n >= per else []
    if n % per:
        chunks.append((nchunks, n % per))
    stsc = _full_box(b"stsc", struct.pack(">I", len(chunks))
                     + b"".join(struct.pack(">III", first, count, 1) for first, count in chunks))
    stsz = _full_box(b"stsz", struct.pack(">II", 0, n) + struct.pack(f">{n}I", *sizes))
    offsets, pos = [], data_offset
    for k in range(n):
        if k % per == 0:
            offsets.append(pos)
        pos += sizes[k]
    if pos > 0xFFFFFFFF:
        raise ValueError("file too large for 32-bit chunk offsets")
    stco = _full_box(b"stco", struct.pack(">I", nchunks) + struct.pack(f">{nchunks}I", *offsets))
    stbl = _box(b"stbl", stsd + stts + stsc + stsz + stco)
    mdia = _box(b"mdia", mdhd + hdlr + _box(b"minf", smhd + dinf + stbl))
    ilst = _box(b"ilst", b"".join(_box(key, _box(b"data", struct.pack(">II", 1, 0) + text.encode("utf-8")))
                                  for key, text in tags))
    meta = _full_box(b"meta", _full_box(b"hdlr", struct.pack(">I", 0) + b"mdirappl" + bytes(9)) + ilst)
    return _box(b"moov", mvhd + _box(b"trak", tkhd + mdia) + _box(b"udta", meta))


def encode_alac(wav_path, m4a_path, title, comment):
    """Encode a 16- or 24-bit WAV as ALAC in MP4 with Apple's reference
    encoder (generator/alac.py), then decode it with Apple's decoder and
    compare the PCM with the WAV's.

    The MP4 holds ftyp, moov and mdat, in this order; moov has one track with
    the packets and the encoder's magic cookie, and the tags \xa9nam (the
    title), \xa9too (ALAC_TOOL) and \xa9cmt (the comment)."""
    nch, rate, bits, offset, size = wav_data_span(wav_path)
    packets_path = m4a_path + ".alac.tmp"
    try:
        cookie, sizes = alac.encode(wav_path, offset, size, rate, nch, bits, packets_path)
        frames = size // (nch * bits // 8)
        ftyp = _box(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
        tags = [(b"\xa9nam", title), (b"\xa9too", ALAC_TOOL), (b"\xa9cmt", comment)]
        moov_len = len(mp4_alac_moov(rate, nch, bits, cookie, sizes, frames, 0, tags))
        moov = mp4_alac_moov(rate, nch, bits, cookie, sizes, frames, len(ftyp) + moov_len + 8, tags)
        data_len = sum(sizes)
        if 8 + data_len > 0xFFFFFFFF:
            raise ValueError("file too large for a 32-bit mdat")
        with open(m4a_path, "wb") as f, open(packets_path, "rb") as packets:
            f.write(ftyp + moov + struct.pack(">I", 8 + data_len) + b"mdat")
            shutil.copyfileobj(packets, f, 1 << 20)
    finally:
        if os.path.exists(packets_path):
            os.remove(packets_path)
    if decode_alac_pcm(m4a_path) != wav_pcm_data(wav_path):
        raise RuntimeError(f"{m4a_path}: decoded PCM differs from {wav_path}")


class AlacTrack:
    """The ALAC track of an MP4 file: its magic cookie, its packets [(offset,
    size)] and its ilst tags {key: text}."""

    def __init__(self, cookie, packets, tags):
        self.cookie, self.packets, self.tags = cookie, packets, tags


def _boxes(b, start, end):
    """Yield (type, body start, body end) of the boxes in b[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", b, pos)
        head = 8
        if size == 1:
            size, head = struct.unpack_from(">Q", b, pos + 8)[0], 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            raise ValueError("bad MP4 box size")
        yield kind, pos + head, pos + size
        pos += size


def read_alac_track(m4a_path):
    """The first ALAC track of an MP4 file (generator side; the checker has its
    own reader). Reads every box but mdat."""
    with open(m4a_path, "rb") as f:
        top, end = {}, os.fstat(f.fileno()).st_size
        pos = 0
        while pos + 8 <= end:
            f.seek(pos)
            size, kind = struct.unpack(">I4s", f.read(8))
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            elif size == 0:
                size = end - pos
            if kind == b"moov":
                f.seek(pos)
                top[kind] = f.read(size)
            pos += size
    moov = top[b"moov"]
    find = lambda s, e, kind: next((bs, be) for k, bs, be in _boxes(moov, s, e) if k == kind)
    tags = {}
    for kind, s, e in _boxes(moov, 8, len(moov)):
        if kind == b"udta":
            ms, me = find(s, e, b"meta")
            ls, le = find(ms + 4, me, b"ilst")
            for key, ks, ke in _boxes(moov, ls, le):
                ds, de = find(ks, ke, b"data")
                tags[key] = moov[ds + 8:de].decode("utf-8")
    for kind, s, e in _boxes(moov, 8, len(moov)):
        if kind != b"trak":
            continue
        st = find(*find(*find(s, e, b"mdia"), b"minf"), b"stbl")
        boxes = {k: moov[bs:be] for k, bs, be in _boxes(moov, *st)}
        stsd = boxes[b"stsd"]
        if stsd[12:16] != b"alac":
            continue
        entry_end = 8 + struct.unpack_from(">I", stsd, 8)[0]
        cookie = next(stsd[bs + 4:be] for k, bs, be in _boxes(stsd, 8 + 36, entry_end) if k == b"alac")
        count = struct.unpack_from(">I", boxes[b"stsz"], 8)[0]
        fixed = struct.unpack_from(">I", boxes[b"stsz"], 4)[0]
        sizes = [fixed] * count if fixed else list(struct.unpack_from(f">{count}I", boxes[b"stsz"], 12))
        if b"co64" in boxes:
            nc = struct.unpack_from(">I", boxes[b"co64"], 4)[0]
            offsets = struct.unpack_from(f">{nc}Q", boxes[b"co64"], 8)
        else:
            nc = struct.unpack_from(">I", boxes[b"stco"], 4)[0]
            offsets = struct.unpack_from(f">{nc}I", boxes[b"stco"], 8)
        ne = struct.unpack_from(">I", boxes[b"stsc"], 4)[0]
        stsc = [struct.unpack_from(">III", boxes[b"stsc"], 8 + 12 * i) for i in range(ne)]
        packets, k = [], 0
        for c, off in enumerate(offsets, 1):
            per = [count_ for first, count_, _ in stsc if first <= c][-1]
            for _ in range(per):
                if k == len(sizes):
                    break
                packets.append((off, sizes[k]))
                off += sizes[k]
                k += 1
        return AlacTrack(cookie, packets, tags)
    raise ValueError(f"{m4a_path}: no ALAC track")


def decode_alac_pcm(m4a_path):
    """The PCM of an ALAC file, decoded by Apple's decoder, as little-endian
    bytes (16 bits in 2 bytes, 24 in 3)."""
    track = read_alac_track(m4a_path)
    with tempfile.TemporaryDirectory(prefix="bpc1-alac-") as d:
        out = os.path.join(d, "out.pcm")
        alac.decode(m4a_path, track.packets, track.cookie, out)
        with open(out, "rb") as f:
            return f.read()
