"""BPC1 v1: constants and primitive functions.

This module mirrors the normative definitions in docs/signal-format.md. The
section numbers in the comments refer to that document. Everything here is
plain integer arithmetic; numpy is used only to evaluate it on arrays.
"""

import math

import numpy as np

VERSION = 1

# 4. Codes ---------------------------------------------------------------

MAGIC = (0xB1C0, 0x5EED)

KIND_PCM = 1
KIND_DSD_DSF = 2
KIND_DSD_DFF = 3
KIND_DSD_DOP = 4
KIND_SWEEP = 5            # PCM code sweep: PCM in every respect but its payload
KIND_NAMES = {KIND_PCM: "PCM", KIND_DSD_DSF: "DSD from DSF",
              KIND_DSD_DFF: "DSD from DFF", KIND_DSD_DOP: "DSD from DoP (WAV or FLAC)",
              KIND_SWEEP: "PCM code sweep"}
DSD_KINDS = (KIND_DSD_DSF, KIND_DSD_DFF, KIND_DSD_DOP)


def is_dsd_kind(kind):
    """DSD kinds; PCM and the PCM code sweep are not."""
    return kind in DSD_KINDS

DEPTH_CODES = {1: 1, 16: 2, 24: 3, 32: 4}
DEPTH_FROM_CODE = {v: k for k, v in DEPTH_CODES.items()}

PCM_RATES = (44100, 48000, 88200, 96000, 176400, 192000,
             352800, 384000, 705600, 768000)
DSD_RATES = (2822400, 5644800, 11289600)
RATES = PCM_RATES + DSD_RATES
RATE_CODES = {r: i for i, r in enumerate(RATES)}

FLAG_LEAD_IN = 0x01
FLAG_TAIL = 0x02
FLAG_LAST = 0x04

# 5. Block structure ------------------------------------------------------

BLOCK_UNITS = 4096        # block length in units of M frames (or DSD bits)
SYNC_FRAMES = 8           # PCM X-sync length in frames
SYNC_BITS = 128           # DSD X-sync length in DSD bits
WALK_FRAMES = 64          # PCM walking-bit burst length
PAYLOAD_START = SYNC_FRAMES + WALK_FRAMES
RHDR_PERIOD = 16          # every 16th block carries the robust header ...
RHDR_PHASE = 8            # ... namely the blocks with k % 16 == 8
RHDR_START_UNITS = 512    # chip span starts at 512*M within the block
BARKER13 = (1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1)
RHDR_WORDS = 7
RHDR_CHIPS = len(BARKER13) + 2 * 16 * RHDR_WORDS   # 237

DSD_GUARD_BYTE = 0x69     # DSD idle pattern, MSB = earliest bit
DSD_CHIP_POS = 0b1110     # chip +1: four-bit pattern, MSB = earliest bit
DSD_CHIP_NEG = 0b0001     # chip -1

# 7.4 Payload: active bits A for depth D (the low-level payload)
ACTIVE_BITS = {16: 10, 24: 16, 32: 24}

DEFAULT_SEED = 0x42504331  # "BPC1"


def family(rate):
    """Return (base, M) with rate = base * M, base 44100 or 48000."""
    for base in (44100, 48000):
        if rate % base == 0:
            m = rate // base
            if m & (m - 1) == 0:
                return base, m
    raise ValueError(f"rate {rate} is not a BPC1 rate")


def is_dsd_rate(rate):
    return rate in DSD_RATES


def block_len(rate):
    """Block length in frames (PCM) or DSD bits per channel (DSD)."""
    return BLOCK_UNITS * family(rate)[1]


def chip_len(rate):
    """Robust-header chip length in frames (PCM) or DSD bits (DSD)."""
    base, m = family(rate)
    return (11 if base == 44100 else 12) * m


def rhdr_start(rate):
    return RHDR_START_UNITS * family(rate)[1]


def is_rhdr_block(k):
    return k % RHDR_PERIOD == RHDR_PHASE


def blocks_for_seconds(seconds, rate):
    """Smallest whole number of blocks lasting at least `seconds`."""
    base, _ = family(rate)
    return math.ceil(seconds * base / BLOCK_UNITS - 1e-9)


# 3. Generator primitives -------------------------------------------------

M32 = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF


def lb32(key, n):
    """lowbias32 of (n XOR key), scalar."""
    x = (n ^ key) & M32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & M32
    x ^= x >> 15
    x = (x * 0x846CA68B) & M32
    x ^= x >> 16
    return x


def lb32_np(key, n):
    """lowbias32 of (n XOR key) on a numpy uint32 array."""
    x = np.asarray(n, dtype=np.uint32) ^ np.uint32(key)
    x ^= x >> np.uint32(16)
    x *= np.uint32(0x7FEB352D)
    x ^= x >> np.uint32(15)
    x *= np.uint32(0x846CA68B)
    x ^= x >> np.uint32(16)
    return x


def splitmix64_first(v):
    """First output of SplitMix64 seeded with v."""
    z = (v + 0x9E3779B97F4A7C15) & M64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


def lbD(D, key, s):
    """lowbias-style mix on D bits (D = 16 or 24), a bijection of s, scalar."""
    m = (1 << D) - 1
    h = D // 2
    x = (s ^ key) & m
    x ^= x >> h
    x = (x * (0x7FEB352D & m)) & m
    x ^= x >> (h - 1)
    x = (x * (0x846CA68B & m)) & m
    x ^= x >> h
    return x


def lbD_np(D, key, s):
    """lbD on a numpy array (uint32 arithmetic, then the low D bits)."""
    m = np.uint32((1 << D) - 1)
    h = np.uint32(D // 2)
    x = (np.asarray(s, dtype=np.uint32) ^ np.uint32(key & M32)) & m
    x ^= x >> h
    x = (x * np.uint32(0x7FEB352D & int(m))) & m
    x ^= x >> (h - np.uint32(1))
    x = (x * np.uint32(0x846CA68B & int(m))) & m
    x ^= x >> h
    return x


def sweep_key(channel_key, D):
    """The lbD key of a channel: the low D bits of its channel key (4.2)."""
    return channel_key & ((1 << D) - 1)


def sweep_index(k, i, block_len, D):
    """Sweep index of frame i (>= 72) of block k, a block without the robust
    header: the sweep units of the blocks before it, counted without the
    R-header blocks (floor((k + 7) / 16) of them before block k), mod 2^D."""
    return ((k - (k + 7) // RHDR_PERIOD) * (block_len - PAYLOAD_START) + i - PAYLOAD_START) \
        % (1 << D)


def sweep_body_blocks(depth, rate, lead_blocks):
    """Body blocks of a sweep file: the standard 10 s at 16 bits; at 24 bits
    the fewest that hold at least 2^24 sweep units per channel."""
    if depth == 16:
        return blocks_for_seconds(10.0, rate)
    per_block = block_len(rate) - PAYLOAD_START
    n = units = 0
    while units < 1 << depth:
        if not is_rhdr_block(lead_blocks + n):
            units += per_block
        n += 1
    return n


def channel_key(seed, kind, rate_code, depth_code, channel):
    v = ((seed & M32) << 32) | (kind << 24) | (rate_code << 16) \
        | (depth_code << 8) | channel
    return splitmix64_first(v) & M32


def crc16(data):
    """CRC-16/IBM-3740 (poly 0x1021, init 0xFFFF, no reflection, xorout 0)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def words_to_bytes(words):
    return b"".join(w.to_bytes(2, "big") for w in words)


# 5.2 X-sync and 7 robust header --------------------------------------------

def header_words(kind, depth_code, rate_code, channel, nch, flags, seed, k):
    """Words w2..w6 shared by the X-sync and the robust header."""
    return [
        (VERSION << 12) | (kind << 8) | (depth_code << 4) | rate_code,
        (channel << 12) | (nch << 8) | flags,
        (seed >> 16) & 0xFFFF,
        seed & 0xFFFF,
        k & 0xFFFF,
    ]


def sync_words(kind, depth_code, rate_code, channel, nch, flags, seed, k):
    """The eight 16-bit X-sync words w0..w7."""
    w = list(MAGIC) + header_words(kind, depth_code, rate_code, channel,
                                   nch, flags, seed, k)
    w.append(crc16(words_to_bytes(w)))
    return w


def rhdr_words(kind, depth_code, rate_code, channel, nch, flags, seed, k,
               total_blocks):
    """The seven 16-bit robust-header words r0..r6."""
    r = header_words(kind, depth_code, rate_code, channel, nch, flags, seed, k)
    r.append(total_blocks & 0xFFFF)
    r.append(crc16(words_to_bytes(r)))
    return r


def rhdr_chips(words):
    """Barker-13 followed by the Manchester-coded words, as +1/-1 chips."""
    chips = list(BARKER13)
    for w in words:
        for i in range(15, -1, -1):
            s = 1 if (w >> i) & 1 else -1
            chips += [s, -s]
    assert len(chips) == RHDR_CHIPS
    return chips


def block_flags(k, lead_blocks, body_blocks, total_blocks):
    f = 0
    if k < lead_blocks:
        f |= FLAG_LEAD_IN
    elif k >= lead_blocks + body_blocks:
        f |= FLAG_TAIL
    if k == total_blocks - 1:
        f |= FLAG_LAST
    return f
