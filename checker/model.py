# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Expected-signal model of BPC1 v1, written from docs/bpc1.md.

This is deliberately independent of the generator package: the round-trip
tests compare two implementations of the same document. The logic is the one
a C port would use: integer hashing per frame, block by block. numpy only
evaluates the per-frame formulas over a block at a time.
"""

from dataclasses import dataclass

import numpy as np

MAGIC0, MAGIC1 = 0xB1C0, 0x5EED
VERSION = 1

KIND_PCM, KIND_DSD, KIND_SWEEP = 1, 2, 5       # 0, 3, 4 and 6 to 15 are reserved
KIND_TEXT = {1: "PCM", 2: "DSD", 5: "SWEEP"}


def is_dsd_kind(kind):
    """The DSD kind; PCM and the PCM code sweep (kind 5) are PCM."""
    return kind == KIND_DSD

DEPTHS = {1: 1, 2: 16, 3: 24, 4: 32}
RATES = (44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000,
         705600, 768000, 2822400, 5644800, 11289600)

F_LEAD_IN, F_TAIL, F_LAST = 0x01, 0x02, 0x04

BARKER13 = (1, 1, 1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1)
N_CHIPS = 13 + 7 * 32


def lb32(key, n):
    x = np.asarray(n, dtype=np.uint32) ^ np.uint32(key)
    x = x ^ (x >> np.uint32(16))
    x = x * np.uint32(0x7FEB352D)
    x = x ^ (x >> np.uint32(15))
    x = x * np.uint32(0x846CA68B)
    return x ^ (x >> np.uint32(16))


def lbD(D, key, s):
    """lowbias-style bijection on D bits (the code sweep, D = 16 or 24)."""
    m = np.uint32((1 << D) - 1)
    h = np.uint32(D // 2)
    x = (np.asarray(s, dtype=np.uint32) ^ np.uint32(key & 0xFFFFFFFF)) & m
    x = x ^ (x >> h)
    x = (x * np.uint32(0x7FEB352D & int(m))) & m
    x = x ^ (x >> (h - np.uint32(1)))
    x = (x * np.uint32(0x846CA68B & int(m))) & m
    return x ^ (x >> h)


def splitmix64_first(v):
    m = (1 << 64) - 1
    z = (v + 0x9E3779B97F4A7C15) & m
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & m
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & m
    return z ^ (z >> 31)


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc16_words(words):
    return crc16(b"".join(int(w).to_bytes(2, "big") for w in words))


def rate_family(rate):
    """(base, M) for a BPC1 rate code's rate."""
    base = 44100 if rate % 44100 == 0 else 48000
    return base, rate // base


@dataclass
class Fields:
    """The decoded words w2..w6 of an X-sync or robust header."""
    version: int
    kind: int
    depth_code: int
    rate_code: int
    channel: int
    nch: int
    flags: int
    seed: int
    block: int

    @classmethod
    def parse(cls, w2, w3, w4, w5, w6):
        return cls(w2 >> 12, (w2 >> 8) & 15, (w2 >> 4) & 15, w2 & 15,
                   w3 >> 12, (w3 >> 8) & 15, w3 & 0xFF, (w4 << 16) | w5, w6)

    def valid(self):
        return (self.version == VERSION and self.kind in KIND_TEXT
                and self.depth_code in DEPTHS and self.rate_code < len(RATES)
                and 1 <= self.nch <= 15 and self.channel < self.nch
                and (not is_dsd_kind(self.kind)) == (self.depth_code != 1)
                and (not is_dsd_kind(self.kind)) == (self.rate_code < 10)
                and (self.kind != KIND_SWEEP or self.depth_code in (2, 3)))

    def same_file(self, o):
        return (self.kind, self.depth_code, self.rate_code, self.nch,
                self.seed) == (o.kind, o.depth_code, o.rate_code, o.nch, o.seed)


def parse_sync(words):
    """words: 8 ints (top 16 bits of 8 frames) -> Fields or None."""
    if words[0] != MAGIC0 or words[1] != MAGIC1:
        return None
    if crc16_words(words[:7]) != words[7]:
        return None
    f = Fields.parse(*words[2:7])
    return f if f.valid() else None


def parse_rhdr(words):
    """words: 7 ints r0..r6 -> (Fields, total_blocks) or None."""
    if crc16_words(words[:6]) != words[6]:
        return None
    f = Fields.parse(*words[:5])
    return (f, words[5]) if f.valid() else None


class Signal:
    """The expected BPC1 signal of one file, as seen by a checker."""

    def __init__(self, f, lead=None, body=None, total=None):
        self.kind = f.kind
        self.depth_code = f.depth_code
        self.depth = DEPTHS[f.depth_code]
        self.rate_code = f.rate_code
        self.rate = RATES[f.rate_code]
        self.nch = f.nch
        self.seed = f.seed
        self.dsd = is_dsd_kind(self.kind)
        base, m = rate_family(self.rate)
        self.base = base
        self.B = 4096 * m                       # frames or DSD bits
        self.chip = (11 if base == 44100 else 12) * m
        self.r_start = 512 * m
        self.lead, self.body, self.total = lead, body, total
        self.keys = []
        for c in range(self.nch):
            v = (self.seed << 32) | (self.kind << 24) | (self.rate_code << 16) \
                | (self.depth_code << 8) | c
            self.keys.append(splitmix64_first(v) & 0xFFFFFFFF)
        self.flag_override = {}

    # Block structure ------------------------------------------------------

    def flags(self, k):
        if k in self.flag_override:
            return self.flag_override[k]
        f = 0
        if self.lead is not None and k < self.lead:
            f |= F_LEAD_IN
        if self.lead is not None and self.body is not None and k >= self.lead + self.body:
            f |= F_TAIL
        if self.total is not None and k == self.total - 1:
            f |= F_LAST
        return f

    def section(self, k):
        """'lead-in', 'body' or 'tail' for block k."""
        f = self.flags(k)
        return "lead-in" if f & F_LEAD_IN else "tail" if f & F_TAIL else "body"

    @staticmethod
    def is_rhdr(k):
        return k % 16 == 8

    def words(self, c, k):
        """w2..w6 for channel c, block k."""
        return [(VERSION << 12) | (self.kind << 8) | (self.depth_code << 4) | self.rate_code,
                (c << 12) | (self.nch << 8) | self.flags(k),
                self.seed >> 16, self.seed & 0xFFFF, k & 0xFFFF]

    def sync_words(self, c, k):
        w = [MAGIC0, MAGIC1] + self.words(c, k)
        return w + [crc16_words(w)]

    def rhdr_words(self, c, k):
        r = self.words(c, k) + [self.total & 0xFFFF]
        return r + [crc16_words(r)]

    def chips(self, c, k):
        out = list(BARKER13)
        for w in self.rhdr_words(c, k):
            for i in range(15, -1, -1):
                b = 1 if (w >> i) & 1 else -1
                out += [b, -b]
        return out

    # PCM: expected 32-bit left-justified words W ---------------------------

    def walk(self, c):
        D = self.depth
        mask = (1 << D) - 1
        alt = 0x55555555 & mask
        vals = []
        for j in range(64):
            i = j if c % 2 == 0 else 63 - j
            u = (1 << i) if i < D else mask ^ (1 << (i - D)) if i < 2 * D \
                else (alt if i % 2 == 0 else mask ^ alt)
            vals.append((u << (32 - D)) & 0xFFFFFFFF)
        return np.array(vals, dtype=np.uint32).view(np.int32)

    def sweep(self, c, k):
        """W of the code-sweep payload, frames 72..B-1 of block k (no robust
        header): code lbD(D, low D bits of the channel key, s) at sweep index
        s = ((k - floor((k + 7) / 16)) (B - 72) + i - 72) mod 2^D."""
        D, B = self.depth, self.B
        s0 = (k - (k + 7) // 16) * (B - 72)
        s = ((np.arange(B - 72, dtype=np.int64) + s0) % (1 << D)).astype(np.uint32)
        u = lbD(D, self.keys[c] & ((1 << D) - 1), s)
        return (u << np.uint32(32 - D)).view(np.int32)

    def pcm_block(self, k):
        """Expected W of block k, int32 array (B, nch)."""
        D = self.depth
        A = {16: 10, 24: 16, 32: 24}[D]
        B = self.B
        n = np.arange(k * B, (k + 1) * B, dtype=np.int64).astype(np.uint32)
        out = np.empty((B, self.nch), dtype=np.int32)
        low_mask = np.uint32((0xFFFFFFFF << (32 - D)) & 0xFFFFFFFF)
        for c in range(self.nch):
            h = lb32(self.keys[c], n)
            w = (h.view(np.int32) >> np.int32(32 - A)) << np.int32(32 - D)
            sync = np.array(self.sync_words(c, k), dtype=np.uint32)
            w[:8] = (((sync << np.uint32(16)) | (h[:8] >> np.uint32(16))) & low_mask).view(np.int32)
            w[8:72] = self.walk(c)
            if self.is_rhdr(k) and self.total is not None:
                w[72:] = 0
                chips = np.array(self.chips(c, k), dtype=np.int32)
                w[self.r_start:self.r_start + N_CHIPS * self.chip] = np.repeat(chips, self.chip) << 29
            elif self.is_rhdr(k):
                w[72:] = 0     # chips unknown until the total block count is known
            elif self.kind == KIND_SWEEP:
                w[72:] = self.sweep(c, k)
            out[:, c] = w
        return out

    # DSD: expected bytes, MSB = earliest bit -----------------------------------

    def dsd_block(self, k):
        """Expected bytes of block k, uint8 array (B/8, nch)."""
        B = self.B
        out = np.empty((B // 8, self.nch), dtype=np.uint8)
        for c in range(self.nch):
            q = np.arange(k * B // 64, (k + 1) * B // 64, dtype=np.uint32)
            h = lb32(self.keys[c], q)
            # each hash bit b becomes the bit pair (b, not b): spread the
            # 32 bits of h over 64 bits, MSB first
            bits = np.unpackbits(h.astype(">u4").view(np.uint8))
            pairs = np.empty(2 * len(bits), dtype=np.uint8)
            pairs[0::2] = bits
            pairs[1::2] = 1 - bits
            by = np.packbits(pairs)
            sync = b"".join(w.to_bytes(2, "big") for w in self.sync_words(c, k))
            by[:16] = np.frombuffer(sync, dtype=np.uint8)
            if self.is_rhdr(k):
                by[16:] = 0x69
                if self.total is not None:
                    a = self.r_start // 8
                    L8 = self.chip // 8
                    for i, s in enumerate(self.chips(c, k)):
                        by[a + i * L8:a + (i + 1) * L8] = 0xEE if s > 0 else 0x11
            out[:, c] = by
        return out

    def block(self, k):
        return self.dsd_block(k) if self.dsd else self.pcm_block(k)

    @property
    def units_per_block(self):
        return self.B // 8 if self.dsd else self.B

    def ref(self, u0, u1):
        """Expected units u0..u1-1 (frames, or bytes per channel for DSD)."""
        U = self.units_per_block
        dtype = np.uint8 if self.dsd else np.int32
        out = np.zeros((u1 - u0, self.nch), dtype=dtype)
        k = max(0, u0) // U
        while k * U < u1:
            blk = self.block(k)
            a, b = max(u0, k * U), min(u1, (k + 1) * U)
            out[a - u0:b - u0] = blk[a - k * U:b - k * U]
            k += 1
        return out
