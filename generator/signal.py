# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""BPC1 v1 signal synthesis: PCM samples and DSD bits, block by block.

The generator writes files in chunks of whole blocks, so memory stays small
whatever a file's length.
"""

from dataclasses import dataclass, replace

import numpy as np

from . import spec


@dataclass(frozen=True)
class Config:
    kind: int                 # spec.KIND_*
    rate: int                 # PCM sample rate or DSD bit rate, Hz
    depth: int                # 16, 24, 32 for PCM; 1 for DSD
    lead_blocks: int
    body_blocks: int
    tail_blocks: int
    nch: int = 2
    seed: int = spec.DEFAULT_SEED

    @property
    def dsd(self):
        return spec.is_dsd_kind(self.kind)

    @property
    def total_blocks(self):
        return self.lead_blocks + self.body_blocks + self.tail_blocks

    @property
    def block_len(self):
        return spec.block_len(self.rate)

    @property
    def total_units(self):
        """Frames (PCM) or DSD bits per channel (DSD) in the whole file."""
        return self.total_blocks * self.block_len

    @property
    def rate_code(self):
        return spec.RATE_CODES[self.rate]

    @property
    def depth_code(self):
        return spec.DEPTH_CODES[self.depth]

    def key(self, channel):
        return spec.channel_key(self.seed, self.kind, self.rate_code,
                                self.depth_code, channel)

    def flags(self, k):
        return spec.block_flags(k, self.lead_blocks, self.body_blocks,
                                self.total_blocks)

    def sync_words(self, channel, k):
        return spec.sync_words(self.kind, self.depth_code, self.rate_code,
                               channel, self.nch, self.flags(k), self.seed, k)

    def rhdr_words(self, channel, k):
        return spec.rhdr_words(self.kind, self.depth_code, self.rate_code,
                               channel, self.nch, self.flags(k), self.seed, k,
                               self.total_blocks)

    def with_(self, **kw):
        return replace(self, **kw)


def standard_config(kind, rate, depth, body_seconds, seed=spec.DEFAULT_SEED,
                    nch=2):
    """A file with the standard 2 s lead-in and 0.5 s tail (section 8)."""
    return Config(kind=kind, rate=rate, depth=depth,
                  lead_blocks=spec.blocks_for_seconds(2.0, rate),
                  body_blocks=spec.blocks_for_seconds(body_seconds, rate),
                  tail_blocks=spec.blocks_for_seconds(0.5, rate),
                  nch=nch, seed=seed)


# PCM ---------------------------------------------------------------------

def _to_signed(u, bits):
    return u - (1 << bits) if u >> (bits - 1) else u


def walk_values(depth, channel):
    """The 64 walking-bit burst samples (D-bit signed values)."""
    mask = (1 << depth) - 1
    alt55 = int("01" * 16, 2) & mask
    out = []
    for j in range(spec.WALK_FRAMES):
        jj = j if channel % 2 == 0 else spec.WALK_FRAMES - 1 - j
        if jj < depth:
            u = 1 << jj
        elif jj < 2 * depth:
            u = mask ^ (1 << (jj - depth))
        else:
            u = alt55 if jj % 2 == 0 else mask ^ alt55
        out.append(_to_signed(u, depth))
    return out


def pcm_blocks(cfg, k0, k1):
    """Samples of blocks k0..k1-1 as int32 array (frames, nch), D-bit units."""
    B = cfg.block_len
    D = cfg.depth
    A = spec.ACTIVE_BITS[D]
    n = (np.arange(k0 * B, k1 * B, dtype=np.int64) & spec.M32).astype(np.uint32)
    out = np.empty((len(n), cfg.nch), dtype=np.int32)
    L = spec.chip_len(cfg.rate)
    r0 = spec.rhdr_start(cfg.rate)
    level = 1 << (D - 3)
    for c in range(cfg.nch):
        h = spec.lb32_np(cfg.key(c), n)
        col = h.view(np.int32) >> np.int32(32 - A)
        walk = np.array(walk_values(D, c), dtype=np.int32)
        for k in range(k0, k1):
            b = (k - k0) * B
            words = np.array(cfg.sync_words(c, k), dtype=np.uint32)
            w = (words << np.uint32(16)) | (h[b:b + spec.SYNC_FRAMES] >> np.uint32(16))
            col[b:b + spec.SYNC_FRAMES] = w.view(np.int32) >> np.int32(32 - D)
            col[b + spec.SYNC_FRAMES:b + spec.PAYLOAD_START] = walk
            if spec.is_rhdr_block(k):
                col[b + spec.PAYLOAD_START:b + B] = 0
                chips = np.array(spec.rhdr_chips(cfg.rhdr_words(c, k)),
                                 dtype=np.int32)
                col[b + r0:b + r0 + len(chips) * L] = np.repeat(chips, L) * level
            elif cfg.kind == spec.KIND_SWEEP:
                col[b + spec.PAYLOAD_START:b + B] = sweep_codes(cfg, c, k)
        out[:, c] = col
    return out


def sweep_codes(cfg, c, k):
    """The sweep payload of channel c, block k (no robust header): frames
    72..B-1 carry the D-bit codes lbD(D, key, s) as signed samples."""
    D, B = cfg.depth, cfg.block_len
    s0 = spec.sweep_index(k, spec.PAYLOAD_START, B, D)
    s = (np.arange(s0, s0 + B - spec.PAYLOAD_START, dtype=np.int64) % (1 << D)).astype(np.uint32)
    u = spec.lbD_np(D, spec.sweep_key(cfg.key(c), D), s).astype(np.int32)
    return np.where(u >> (D - 1), u - (1 << D), u)


# DSD ---------------------------------------------------------------------

def _words_to_bits(words_u32):
    """uint32 array -> bits, MSB of each word first."""
    return np.unpackbits(words_u32.astype(">u4").view(np.uint8))


def _pattern_bits(pattern, period, start, count):
    """Periodic bit pattern (MSB = earliest) indexed by absolute bit index."""
    idx = np.arange(start, start + count, dtype=np.int64) % period
    return ((pattern >> (period - 1 - idx)) & 1).astype(np.uint8)


def dsd_blocks(cfg, k0, k1):
    """DSD bits of blocks k0..k1-1 as uint8 array (nch, bits), values 0/1."""
    B = cfg.block_len
    L = spec.chip_len(cfg.rate)
    r0 = spec.rhdr_start(cfg.rate)
    m0, m1 = k0 * B, k1 * B
    out = np.empty((cfg.nch, m1 - m0), dtype=np.uint8)
    for c in range(cfg.nch):
        key = cfg.key(c)
        q = np.arange(m0 >> 6, m1 >> 6, dtype=np.uint32)
        b = _words_to_bits(spec.lb32_np(key, q))
        bits = np.empty(2 * len(b), dtype=np.uint8)
        bits[0::2] = b
        bits[1::2] = 1 - b
        for k in range(k0, k1):
            o = (k - k0) * B
            sync = spec.words_to_bytes(cfg.sync_words(c, k))
            bits[o:o + spec.SYNC_BITS] = np.unpackbits(
                np.frombuffer(sync, dtype=np.uint8))
            if spec.is_rhdr_block(k):
                a = k * B + spec.SYNC_BITS
                bits[o + spec.SYNC_BITS:o + B] = _pattern_bits(
                    spec.DSD_GUARD_BYTE, 8, a, B - spec.SYNC_BITS)
                chips = spec.rhdr_chips(cfg.rhdr_words(c, k))
                pos = _pattern_bits(spec.DSD_CHIP_POS, 4, 0, L)
                for i, s in enumerate(chips):
                    p = o + r0 + i * L
                    bits[p:p + L] = pos if s > 0 else 1 - pos
        out[c] = bits
    return out


def dop_frames(bits, first_frame):
    """DoP samples (24-bit, as uint32 values) from DSD bits (nch, 16*F)."""
    nch, nbits = bits.shape
    F = nbits // 16
    packed = np.packbits(bits, axis=1, bitorder="big")       # (nch, 2F)
    words = (packed[:, 0::2].astype(np.uint32) << 8) | packed[:, 1::2]
    f = np.arange(first_frame, first_frame + F, dtype=np.int64)
    marker = np.where(f % 2 == 0, 0x05, 0xFA).astype(np.uint32)
    return ((marker[None, :] << 16) | words).T                # (F, nch)
