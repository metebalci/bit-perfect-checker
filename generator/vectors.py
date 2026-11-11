# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Golden test vectors for ports of BPC1 v1 (vectors/bpc1-v1-vectors.json).

    python3 -m generator.vectors [--check]

The file lists primitive results (lowbias32, SplitMix64, CRC-16), and for a
few configurations the block geometry, channel keys, X-sync and robust-header
words, and exact samples or DSD bytes at chosen positions, including the
first bytes of the container's audio data, and for checkers the gap
search's masked comparison and an inverted X-sync.
"""

import argparse
import json
import os
import sys

import numpy as np

from . import containers, fileset, spec
from .signal import dop_frames, dsd_blocks, pcm_blocks

PATH = os.path.join(os.path.dirname(__file__), "..", "vectors", "bpc1-v1-vectors.json")


def hx(v, width=4):
    return "0x%0*X" % (width, v)


def primitives():
    lb = [(0, 0), (0, 1), (0x12345678, 0), (0x12345678, 1),
          (0xDEADBEEF, 0xFFFFFFFF), (0x9E3779B9, 123456789)]
    sm = [0, 1, 0x4250433101010300]
    keys = [(spec.DEFAULT_SEED, spec.KIND_PCM, 0, 2, 0), (spec.DEFAULT_SEED, spec.KIND_PCM, 0, 2, 1),
            (spec.DEFAULT_SEED, spec.KIND_DSD, 10, 1, 0)]
    crc = [b"123456789", spec.words_to_bytes([0xB1C0, 0x5EED, 0x1032, 0x0201, 0x4250, 0x4331, 0x0000])]
    lbd = [(16, 0, 0), (16, 0, 1), (16, 0x8788, 0), (16, 0x8788, 12345), (16, 0xFFFF, 0xFFFF),
           (24, 0, 0), (24, 0, 1), (24, 0xE5BFCF, 0), (24, 0xE5BFCF, 1234567), (24, 0xFFFFFF, 0xFFFFFF)]
    return {
        "lowbias32": [{"key": hx(k, 8), "n": hx(n, 8), "result": hx(spec.lb32(k, n), 8)} for k, n in lb],
        "splitmix64_first": [{"v": hx(v, 16), "result": hx(spec.splitmix64_first(v), 16)} for v in sm],
        "channel_key": [{"seed": hx(s, 8), "kind": k, "rate_code": r, "depth_code": d, "channel": c,
                         "key": hx(spec.channel_key(s, k, r, d, c), 8)} for s, k, r, d, c in keys],
        "crc16_ibm3740": [{"data": d.hex().upper(), "crc": hx(spec.crc16(d))} for d in crc],
        "lbD": [{"D": D, "key": hx(k, 6), "s": hx(n, 6), "result": hx(spec.lbD(D, k, n), 6)}
                for D, k, n in lbd],
        "sweep_channel_key": [{"seed": hx(spec.DEFAULT_SEED, 8), "kind": spec.KIND_SWEEP,
                               "rate_code": r, "depth_code": spec.DEPTH_CODES[D], "channel": c,
                               "key": hx(spec.channel_key(spec.DEFAULT_SEED, spec.KIND_SWEEP, r,
                                                          spec.DEPTH_CODES[D], c), 8),
                               "sweep_key": hx(spec.sweep_key(spec.channel_key(
                                   spec.DEFAULT_SEED, spec.KIND_SWEEP, r, spec.DEPTH_CODES[D], c), D), 6)}
                              for r, D in ((0, 16), (1, 24)) for c in (0, 1)],
    }


def config_geometry(fsp):
    cfg = fsp.cfg
    return {
        "name": fsp.name,
        "file": fsp.filename,
        "container": fsp.container,
        "kind": cfg.kind, "rate": cfg.rate, "rate_code": cfg.rate_code,
        "depth": cfg.depth, "depth_code": cfg.depth_code, "channels": cfg.nch,
        "seed": hx(cfg.seed, 8),
        "lead_blocks": cfg.lead_blocks, "body_blocks": cfg.body_blocks,
        "tail_blocks": cfg.tail_blocks, "total_blocks": cfg.total_blocks,
        "block_length": cfg.block_len, "chip_length": spec.chip_len(cfg.rate),
        "rhdr_start": spec.rhdr_start(cfg.rate),
        "channel_keys": [hx(cfg.key(c), 8) for c in range(cfg.nch)],
    } | ({"sweep_keys": [hx(spec.sweep_key(cfg.key(c), cfg.depth), 6) for c in range(cfg.nch)]}
         if cfg.kind == spec.KIND_SWEEP else {})


def words(cfg, blocks):
    out = {}
    for k in blocks:
        out[str(k)] = {"flags": hx(cfg.flags(k), 2),
                       "sync_words": [[hx(w) for w in cfg.sync_words(c, k)] for c in range(cfg.nch)]}
        if spec.is_rhdr_block(k):
            out[str(k)]["rhdr_words"] = [[hx(w) for w in cfg.rhdr_words(c, k)] for c in range(cfg.nch)]
            out[str(k)]["rhdr_first_chips"] = spec.rhdr_chips(cfg.rhdr_words(0, k))[:45]
    return out


def pcm_vectors(fsp):
    cfg = fsp.cfg
    B = cfg.block_len
    r0 = spec.rhdr_start(cfg.rate)
    v = config_geometry(fsp)
    last = cfg.total_blocks - 1
    v["blocks"] = words(cfg, [0, 8, 24, last])
    ranges = [(0, 80, "block 0: X-sync, walk, payload"),
              (8 * B + 68, 8 * B + 76, "block 8: walk end, guard"),
              (8 * B + r0 - 2, 8 * B + r0 + 3 * spec.chip_len(cfg.rate) + 2,
               "block 8: start of the R-header chips"),
              (30 * B + 1000, 30 * B + 1016, "block 30: payload"),
              (last * B + B - 8, last * B + B, "last frames of the file")]
    if cfg.kind == spec.KIND_SWEEP:
        ranges.insert(4, (30 * B + 64, 30 * B + 88, "block 30: walk end, first sweep units"))
    v["samples"] = []
    for a, b, what in ranges:
        k0, k1 = a // B, (b - 1) // B + 1
        s = pcm_blocks(cfg, k0, k1)[a - k0 * B:b - k0 * B]
        v["samples"].append({"what": what, "first_frame": a,
                             "values": [[int(x) for x in row] for row in s]})
    hdr, _ = containers.wav_header(cfg.rate, cfg.nch, cfg.depth, cfg.depth,
                                   cfg.total_units, fileset.info_tags(fsp))
    v["wav_data_offset"] = len(hdr)
    v["wav_data_first_bytes"] = containers.pcm_bytes(pcm_blocks(cfg, 0, 1)[:8], cfg.depth).hex().upper()
    return v


def dsd_vectors(fsp):
    cfg = fsp.cfg
    B = cfg.block_len
    r0 = spec.rhdr_start(cfg.rate)
    L = spec.chip_len(cfg.rate)
    v = config_geometry(fsp)
    last = cfg.total_blocks - 1
    v["blocks"] = words(cfg, [0, 8, last])
    ranges = [(0, 256, "block 0: X-sync and payload"),
              (8 * B + 128, 8 * B + 256, "block 8: guard after the X-sync"),
              (8 * B + r0 - 64, 8 * B + r0 + 2 * L, "block 8: start of the R-header chips"),
              (30 * B + 4096, 30 * B + 4224, "block 30: payload")]
    v["dsd_bytes_msb_first"] = []
    for a, b, what in ranges:
        k0, k1 = a // B, (b - 1) // B + 1
        bits = dsd_blocks(cfg, k0, k1)[:, a - k0 * B:b - k0 * B]
        v["dsd_bytes_msb_first"].append({
            "what": what, "first_bit": a,
            "hex": [np.packbits(bits[c]).tobytes().hex().upper() for c in range(cfg.nch)]})
    first = dsd_blocks(cfg, 0, 1)[:, :512]
    if fsp.container == "dsf":
        v["dsf_data_first_bytes"] = np.packbits(first[0, :128], bitorder="little").tobytes().hex().upper()
        v["dsf_note"] = "channel 0's first 16 bytes; channel 1's block follows 4096 bytes later"
    elif fsp.container == "dff":
        v["dff_data_first_bytes"] = np.packbits(first[:, :128], axis=1).T.tobytes().hex().upper()
    else:
        f = dop_frames(first[:, :128], 0)
        v["dop_first_frames"] = [[hx(int(x), 6) for x in row] for row in f]
    return v


def _signed(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def gap_match(k, received, expected):
    """The gap search's masked comparison of one unit (12.2): with the bits
    below k cleared, as signed integers, within 2 steps of k bits on every
    channel."""
    m = (0xFFFFFFFF << (32 - k)) & 0xFFFFFFFF
    return all(abs(_signed(a & m) - _signed(b & m)) <= 2 << (32 - k)
               for a, b in zip(received, expected))


def gap_rows():
    rows = [(16, [0x12340000], [0x1234ABCD], "truncated"),
            (16, [0x12350000], [0x1234ABCD], "rounded up"),
            (16, [0x12360000], [0x12340000], "2 steps above"),
            (16, [0x12370000], [0x12340000], "3 steps above"),
            (16, [0x12320000], [0x12340000], "2 steps below"),
            (20, [0xFFFFF000], [0x00001000], "2 steps, across zero"),
            (20, [0x80000000], [0x7FFFF000], "full scale against full scale: no wrap-around"),
            (16, [0x7FFFFF00], [0x7FFFFF00], "the 24-bit clamp word of an inversion"),
            (16, [0x12340000, 0x56780000], [0x12340000, 0x567B0000], "one channel 3 steps off")]
    return [{"k": k, "received": [hx(x, 8) for x in rx], "expected": [hx(x, 8) for x in ref],
             "match": gap_match(k, rx, ref), "what": what} for k, rx, ref, what in rows]


def inverted_xsync():
    """The first X-sync of the 48 kHz 24-bit file, channel 0, that after an
    inversion at 24 bits and a truncation to 16 bits reads as neither -W nor
    ~W in every word: each word is read as either."""
    cfg = fileset.pcm_file(48000, 24).cfg
    top = (1 << 31) - (1 << 8)
    for k in range(cfg.total_blocks):
        w = cfg.sync_words(0, k)
        W = [(int(x) << 8) & 0xFFFFFFFF for x in pcm_blocks(cfg, k, k + 1)[:8, 0]]
        R = [min(-_signed(x), top) & 0xFFFF0000 for x in W]
        neg = [((-_signed(r)) & 0xFFFFFFFF) >> 16 for r in R]
        com = [(~r & 0xFFFFFFFF) >> 16 for r in R]
        if neg != list(w) and com != list(w) and all(w[i] in (neg[i], com[i]) for i in range(8)):
            return {"file": fileset.pcm_file(48000, 24).name, "block": k, "channel": 0,
                    "sync_words": [hx(x) for x in w],
                    "words": [hx(x, 8) for x in W],
                    "received": [hx(x, 8) for x in R],
                    "read_as": ["-W" if neg[i] == w[i] else "~W" for i in range(8)]}
    raise ValueError("no such X-sync")


def checking():
    return {"gap_match": gap_rows(), "inverted_xsync": inverted_xsync()}


# Verdict cases (spec 13): streams built from short files, with one change,
# and the results a checker must give. Positions are frames of the stream
# as it is at that step; words are 32-bit, left-justified.
VERDICT_FILES = {
    "pcm48_24": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=2, body_blocks=24,
                     tail_blocks=2),
    "pcm48_16": dict(kind=spec.KIND_PCM, rate=48000, depth=16, lead_blocks=2, body_blocks=24,
                     tail_blocks=2),
    "pcm48_24_lead12": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=12,
                            body_blocks=14, tail_blocks=2),
    "dop64": dict(kind=spec.KIND_DSD, rate=2822400, depth=1, lead_blocks=2, body_blocks=8,
                  tail_blocks=2),
    "pcm176_24": dict(kind=spec.KIND_PCM, rate=176400, depth=24, lead_blocks=2, body_blocks=8,
                      tail_blocks=2),
    "pcm768_24": dict(kind=spec.KIND_PCM, rate=768000, depth=24, lead_blocks=2, body_blocks=8,
                      tail_blocks=2),
    "sweep48_16": dict(kind=spec.KIND_SWEEP, rate=48000, depth=16, lead_blocks=2, body_blocks=24,
                       tail_blocks=2),
    "pcm48_24_long": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=2, body_blocks=30,
                          tail_blocks=2),
    "pcm48_24_lead24": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=24, body_blocks=4,
                            tail_blocks=2),
    "dop64_long": dict(kind=spec.KIND_DSD, rate=2822400, depth=1, lead_blocks=2, body_blocks=20,
                       tail_blocks=2),
    "pcm48_24_10_1_10": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=10, body_blocks=1,
                             tail_blocks=10),
    "pcm48_24_lead10": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=10, body_blocks=4,
                            tail_blocks=2),
    "pcm48_24_10_16_2": dict(kind=spec.KIND_PCM, rate=48000, depth=24, lead_blocks=10, body_blocks=16,
                             tail_blocks=2),
}
OPS = {
    "xor": "XOR value into the words of frames [frame, frame + count) (count 1), in channels "
           "(all)",
    "and": "AND value into the words of those frames and channels",
    "remove": "remove frames [frame, frame + count)",
    "insert": "insert count frames of value before frame",
    "copy": "insert a copy of frames [from, from + count) before frame",
    "replace": "in channels (all), overwrite frames [frame, frame + count) with frames "
               "[from, from + count)",
    "start": "the stream starts at frame", "stop": "the stream stops before frame",
    "swap_channels": "exchange channels 0 and 1",
    "negate": "W = min(-W, 2^31 - 2^(32-bits)) in every word",
    "round": "W = min((W + 2^(31-bits)) & ~(2^(32-bits) - 1), (2^31 - 1) & ~(2^(32-bits) - 1)) in "
             "the words of frames [frame, frame + count) (all)",
    "halve": "W = W >> 1 (arithmetic) in the words of frames [frame, frame + count)",
    "recode": "in channels (all), the words equal to from in frames [frame, frame + count) "
              "become to",
}
PARTS = {
    "file": "the file's words, as generated (frames: only frames [first, stop) of it)",
    "zeros": "n frames of zero words",
    "dop_idle": "n DoP frames of idle bytes 0x69 0x69, markers 0x05 then 0xFA alternating",
    "noise": "n frames, channel c of frame i: lowbias32(key, i x nch + c) with the low 8 bits "
             "cleared",
}


def _diff(unit, per_s):
    """Seconds, 3 decimals, rounded half up: unit / per_s."""
    q = (unit * 1000 + per_s // 2) // per_s
    return "%d.%03d" % divmod(q, 1000)


# How a case's files end, where not LAST (an UNKNOWN_SIGNAL stretch: STOP)
ENDS = {
    "an r5 chip unit, the stream stops in the tail before LAST": ["STOP"],
    "the stream stops in the body": ["STOP"],
    "the stream stops at the body's end": ["STOP"],
    "an r5 chip unit changed, the stream stops 50 chips later": ["STOP"],
    "an r6 bit's two chips inverted in channel 0, the stream stops in the tail before LAST": ["STOP"],
    "an r6 bit's two chips inverted in channel 0, the stream stops right after chip 236": ["STOP"],
    "block 8's R-header spells count 29, the stream stops in the tail before LAST": ["STOP"],
    "block 24's R-header spells count 29, the stream stops in the tail before LAST": ["STOP"],
    "block 8's R-header spells count 29, a payload bit of block 10, the stream stops in the tail "
    "before LAST": ["STOP"],
    "the stream stops right after the first tail X-sync": ["STOP"],
    "a block that is not tail after the first tail block (a splice of two lengths)": ["BACK", "LAST"],
    "rounded to 16 bits": ["STOP"],
    "a file and a file rounded to 16 bits": ["LAST", "STOP"],
    # the LAST block's X-sync is changed too: the file ends with the stream
    "lead-in and tail zeroed outside their limit X-syncs": ["STOP"],
    "a fade: the first lead-in block and the last tail block halved": ["STOP"],
    "a 24-block lead-in, the stream stops right after the first tail X-sync": ["STOP"],
    "a DoP file cut in its body, then a PCM file at the same frame rate from its block 1":
        ["NEXT", "LAST"],
    "a DoP file cut 7 frames into a body block's X-sync, then a PCM file from its block 1":
        ["NEXT", "LAST"],
    "a DoP file cut 7 frames into a body block's X-sync, then a DoP file from its block 1":
        ["BACK", "LAST"],
    "the LAST X-sync broken, the lead-in R-header's count 18 (a valid R-header)": ["STOP"],
    "the LAST X-sync broken, the lead-in R-header's count 18 and a reserved flag bit set": ["STOP"],
    "the X-syncs of tail blocks 12 to 20 erased, the lead-in R-header's first Barker chip erased":
        ["STOP"],
    "the X-syncs of tail blocks 12 to 20 erased, the lead-in R-header intact": ["STOP"],
    "the LAST X-sync erased, the lead-in R-header's count 40, the body R-header's 28, "
    "5 blocks of zeros after": ["STOP"],
    "the LAST X-sync erased, the lead-in R-header's count 20, the body R-header's 28, "
    "a block of zeros after": ["STOP"],
    "the LAST X-sync erased, both R-headers' count 28, a block of zeros after": ["STOP"],
}


def _result(r, name):
    """{verdict, end, diff} of one expected result."""
    v, d = r
    i = _result.index.get(name, 0)
    _result.index[name] = i + 1
    end = "STOP" if v == "UNKNOWN_SIGNAL" else ENDS.get(name, ["LAST"] * 9)[i]
    return {"verdict": v, "end": end, "diff": d or "-"}


_result.index = {}


def _first_code():
    """The first body frame of the sweep48_16 file whose channel 0 sample is
    32766 (2^15 - 2), the code the negative sweep file changes."""
    from .signal import Config
    cfg = Config(**VERDICT_FILES["sweep48_16"])
    x = pcm_blocks(cfg, cfg.lead_blocks, cfg.lead_blocks + cfg.body_blocks)[:, 0]
    return cfg.lead_blocks * cfg.block_len + int(np.nonzero(x == 32766)[0][0])


def _rhdr_bits(k, delta):
    """xor changes that invert the R-header bits of block k (48 kHz) given as
    {word: mask} in r0..r5, and the bits of r6 that keep its CRC valid: both
    chips of a bit negated (W of a chip is 2^29 or -2^29), in every channel."""
    B, chip, r0 = spec.block_len(48000), spec.chip_len(48000), spec.rhdr_start(48000)
    d = [delta.get(i, 0) for i in range(6)]
    d.append(spec.crc16(spec.words_to_bytes(d)) ^ spec.crc16(spec.words_to_bytes([0] * 6)))
    out = []
    for i, m in enumerate(d):
        for b in range(16):
            if m >> (15 - b) & 1:
                out.append({"op": "xor", "frame": k * B + r0 + (13 + 2 * (16 * i + b)) * chip,
                            "count": 2 * chip, "value": "0xC0000000"})
    return out


def _xsync_flags(k, channel, mask):
    """xor changes that set the flag bits mask in one channel's X-sync of
    block k (48 kHz, h1 is word 3) and keep its CRC (word 7) valid."""
    B = spec.block_len(48000)
    d = [0, 0, 0, mask, 0, 0, 0]
    crc = spec.crc16(spec.words_to_bytes(d)) ^ spec.crc16(spec.words_to_bytes([0] * 7))
    return [{"op": "xor", "frame": k * B + 3, "channels": [channel], "value": hx(mask << 16, 8)},
            {"op": "xor", "frame": k * B + 7, "channels": [channel], "value": hx(crc << 16, 8)}]


def _dop_kind(kind, blocks):
    """xor changes that give every X-sync of a DSD64 DoP file of `blocks`
    blocks the kind `kind` instead of the DSD kind and keep their CRCs
    valid: DoP frame i of a block carries X-sync word i (h0 is word 2, the
    CRC word 7)."""
    F = spec.block_len(2822400) // 16
    d = [0, 0, (kind ^ spec.KIND_DSD) << 8, 0, 0, 0, 0]
    crc = spec.crc16(spec.words_to_bytes(d)) ^ spec.crc16(spec.words_to_bytes([0] * 7))
    return [{"op": "xor", "frame": k * F + i, "value": hx(v << 8, 8)}
            for k in range(blocks) for i, v in ((2, d[2]), (7, crc))]


def verdict_cases():
    B, F = spec.block_len(48000), spec.block_len(2822400) // 16    # frames per block
    R = 48000
    DOP = 176400                     # DoP frame rate: a DoP frame is 2 DSD bytes
    chip, r0 = spec.chip_len(48000), spec.rhdr_start(48000)
    r5 = lambda k, j=0, t=0: k * B + r0 + (13 + 5 * 32 + j) * chip + t
    # both chips of r5/r6 bit b (0 to 31) of block k inverted: a valid pair of the other value
    pair = lambda k, b, ch: {"op": "xor", "frame": r5(k, 2 * b), "count": 2 * chip, "channels": ch,
                             "value": "0xC0000000"}
    no_last = {"op": "stop", "frame": 26 * B + 100}   # pcm48_24: after the first tail X-sync
    dchip = spec.chip_len(2822400) // 16              # DoP frames per chip
    d173 = 8 * F + spec.rhdr_start(2822400) // 16 + (13 + 5 * 32) * dchip
    dpair = {"op": "xor", "frame": d173 + 62 * dchip, "count": 2 * dchip, "channels": [0],
             "value": "0x00FFFF00"}                   # r6 bit 15 of block 8, its DSD bytes inverted
    bp, nbp, unk = "BIT_PERFECT", "NOT_BIT_PERFECT", "UNKNOWN_SIGNAL"
    one = [{"file": "pcm48_24"}]
    long = [{"file": "pcm48_24_long"}]
    d1 = [{"file": "dop64"}]
    cases = [
        ("clean", one, [], [(bp, None)]),
        ("a body X-sync's flags word broken in both channels", one,
         [{"op": "xor", "frame": 10 * B + 3, "value": "0x00100000"}],
         [(nbp, _diff(10 * B + 3, R))]),
        ("a bit below the X-sync word in one channel", one,
         [{"op": "xor", "frame": 10 * B + 5, "channels": [1], "value": "0x00000100"}],
         [(nbp, _diff(10 * B + 5, R))]),
        ("the lowest bit of a payload word", one,
         [{"op": "xor", "frame": 7 * B + 3000, "channels": [0], "value": "0x00000100"}],
         [(nbp, _diff(7 * B + 3000, R))]),
        ("an r5 chip unit, block count known", one,
         [{"op": "xor", "frame": r5(8, 3, 4), "channels": [0], "value": "0x00000100"}],
         [(nbp, _diff(r5(8, 3, 4), R))]),
        ("an r5 chip unit, the stream starts after block 8", [{"file": "pcm48_24_lead12"}],
         [{"op": "xor", "frame": r5(24, 3, 4), "channels": [0], "value": "0x00000100"},
          {"op": "start", "frame": 9 * B}],
         [(nbp, _diff(r5(24, 3, 4), R))]),
        ("an r5 chip unit, the stream stops in the tail before LAST", one,
         [{"op": "xor", "frame": r5(8, 3, 4), "channels": [0], "value": "0x00000100"},
          {"op": "stop", "frame": 26 * B + 1000}],
         [(nbp, _diff(r5(8, 3, 4), R))]),
        ("the last body frame", one,
         [{"op": "xor", "frame": 26 * B - 1, "channels": [1], "value": "0x00000100"}],
         [(nbp, _diff(26 * B - 1, R))]),
        ("1 frame removed in the body", one, [{"op": "remove", "frame": 10 * B + 1234, "count": 1}],
         [(nbp, _diff(10 * B + 1234, R))]),
        ("6 frames removed in the body", one, [{"op": "remove", "frame": 10 * B + 1234, "count": 6}],
         [(nbp, _diff(10 * B + 1234, R))]),
        ("6 zero frames inserted in the body", one,
         [{"op": "insert", "frame": 10 * B + 1234, "count": 6, "value": "0x00000000"}],
         [(nbp, _diff(10 * B + 1234, R))]),
        ("6 frames repeated in the body", one,
         [{"op": "copy", "frame": 10 * B + 1234, "from": 10 * B + 1228, "count": 6}],
         [(nbp, _diff(10 * B + 1234, R))]),
        ("the stream starts in the body", one, [{"op": "start", "frame": 5 * B + 10}],
         [(nbp, _diff(6 * B, R))]),
        ("the stream stops in the body", one, [{"op": "stop", "frame": 12 * B}],
         [(nbp, _diff(12 * B, R))]),
        ("the stream stops at the body's end", one, [{"op": "stop", "frame": 26 * B}],
         [(nbp, _diff(26 * B, R))]),
        ("received at 44.1 kHz", [{"file": "pcm48_24", "rate": 44100}], [],
         [(nbp, _diff(2 * B, R))]),
        ("channels exchanged", one, [{"op": "swap_channels"}], [(nbp, _diff(2 * B, R))]),
        ("inverted", one, [{"op": "negate", "bits": 24}], [(unk, None)]),
        # Rounding to 16 bits breaks most X-syncs (a carry into their word);
        # here those of block 8 (channel 1) and 20 (channel 0) survive: the
        # body's start is not shown, diff at block 8.
        ("rounded to 16 bits", one, [{"op": "round", "bits": 16}], [(nbp, _diff(8 * B, R))]),
        ("rounded to 16 bits, the stream starts at block 9", one,
         [{"op": "round", "bits": 16}, {"op": "start", "frame": 9 * B},
          {"op": "stop", "frame": 11 * B - 9 * B}], [(unk, None)]),
        ("muted to zeros", one, [{"op": "and", "frame": 0, "count": 28 * B,
                                  "value": "0x00000000"}], [(unk, None)]),
        ("zeros only", [{"zeros": 50000}], [], [(unk, None)]),
        ("a file and a file rounded to 16 bits", [{"file": "pcm48_24"}, {"file": "pcm48_24"}],
         [{"op": "round", "bits": 16, "frame": 28 * B, "count": 28 * B}],
         [(bp, None), (nbp, _diff(8 * B, R))]),
        ("a file and a file rounded to 16 bits, the stream stops at its block 8",
         [{"file": "pcm48_24"}, {"file": "pcm48_24"}],
         [{"op": "round", "bits": 16, "frame": 28 * B, "count": 28 * B},
          {"op": "stop", "frame": 28 * B + 8 * B}], [(bp, None), (unk, None)]),
        ("a track that is not a test file between two files",
         [{"file": "pcm48_24"}, {"noise": 5 * B, "key": "0x0000ABCD"}, {"file": "pcm48_16"}], [],
         [(bp, None), (unk, None), (bp, None)]),
        ("3 blocks of zeros between two files",
         [{"file": "pcm48_24"}, {"zeros": 3 * B}, {"file": "pcm48_16"}], [],
         [(bp, None), (unk, None), (bp, None)]),
        ("zeros shorter than a block before and after a file",
         [{"zeros": 3000}, {"file": "pcm48_24"}, {"zeros": 3000}], [], [(bp, None)]),
        ("two gapless files", [{"file": "pcm48_24"}, {"file": "pcm48_16"}], [],
         [(bp, None), (bp, None)]),
        ("the same file twice", [{"file": "pcm48_24"}, {"file": "pcm48_24"}], [],
         [(bp, None), (bp, None)]),
        ("lead-in and tail zeroed outside their limit X-syncs", one,
         [{"op": "and", "frame": 100, "count": B - 100, "value": "0x00000000"},
          {"op": "and", "frame": 26 * B + 100, "count": 2 * B - 100, "value": "0x00000000"}],
         [(bp, None)]),
        ("a fade: the first lead-in block and the last tail block halved", one,
         [{"op": "halve", "frame": 0, "count": B}, {"op": "halve", "frame": 27 * B, "count": B}],
         [(bp, None)]),
        ("300 frames removed in the lead-in before the first intact X-sync", one,
         [{"op": "xor", "frame": 3, "value": "0x00100000"},
          {"op": "remove", "frame": 500, "count": 300}], [(bp, None)]),
        ("700 frames repeated in the tail after the LAST X-sync", one,
         [{"op": "copy", "frame": 27 * B + 500, "from": 27 * B + 100, "count": 700}], [(bp, None)]),
        ("the last lead-in X-sync broken in both channels", one,
         [{"op": "xor", "frame": B + 3, "value": "0x00100000"}], [(nbp, _diff(B, R))]),
        ("the first tail X-sync broken in both channels", one,
         [{"op": "xor", "frame": 26 * B + 3, "value": "0x00100000"}], [(nbp, _diff(26 * B + 3, R))]),
        # DoP
        ("DoP, clean", d1, [], [(bp, None)]),
        ("DoP, the other marker phase", d1,
         [{"op": "xor", "frame": 0, "count": 12 * F, "value": "0xFF000000"}], [(bp, None)]),
        ("DoP, a DSD bit of the second byte of a frame", d1,
         [{"op": "xor", "frame": 5 * F + 77, "channels": [0], "value": "0x00001000"}],
         [(nbp, _diff(2 * (5 * F + 77) + 1, 2 * DOP))]),
        ("DoP, a padding bit set", [{"file": "dop64", "slot": 32}],
         [{"op": "xor", "frame": 5 * F + 77, "channels": [1], "value": "0x00000001"}],
         [(nbp, _diff(5 * F + 77, DOP))]),
        ("DoP, a marker zeroed", d1,
         [{"op": "and", "frame": 5 * F + 77, "value": "0x00FFFFFF"}], [(nbp, _diff(5 * F + 77, DOP))]),
        ("DoP, two markers exchanged", d1,
         [{"op": "xor", "frame": 5 * F + 77, "count": 2, "value": "0xFF000000"}],
         [(nbp, _diff(5 * F + 77, DOP))]),
        ("DoP, a phase jump", d1,
         [{"op": "xor", "frame": 5 * F + 77, "count": 7 * F - 77, "value": "0xFF000000"}],
         [(nbp, _diff(5 * F + 77, DOP))]),
        ("DoP without markers", d1, [{"op": "and", "frame": 0, "count": 12 * F, "value": "0x00FFFFFF"}],
         [(unk, None)]),
        # Kinds 3 and 4 are reserved (3.1): a DoP file whose X-syncs all carry
        # one, with valid CRCs, is not a test signal
        ("DoP, every X-sync of reserved kind 3", d1, _dop_kind(3, 12), [(unk, None)]),
        ("DoP, every X-sync of reserved kind 4", d1, _dop_kind(4, 12), [(unk, None)]),
        ("DoP idle between two DoP files", [{"file": "dop64"}, {"dop_idle": 3 * F}, {"file": "dop64"}],
         [], [(bp, None), (unk, None), (bp, None)]),
        ("diff half-way between two milliseconds rounds up", one,
         [{"op": "xor", "frame": 61944, "channels": [0], "value": "0x00100000"}],
         [(nbp, _diff(61944, R))]),
        ("channel 1's first body X-sync replaced by its last lead-in X-sync", one,
         [{"op": "replace", "frame": 2 * B, "from": B, "count": 8, "channels": [1]}],
         [(nbp, _diff(2 * B, R))]),
        ("channel 1's last body X-sync replaced by its first tail X-sync", one,
         [{"op": "replace", "frame": 25 * B, "from": 26 * B, "count": 8, "channels": [1]}],
         [(nbp, _diff(25 * B + 3, R))]),
        ("a 2-block lead-in, then the same file with a 12-block lead-in from block 6",
         [{"file": "pcm48_24", "frames": [0, 6 * B]}, {"file": "pcm48_24_lead12", "frames": [6 * B, 28 * B]}],
         [], [(nbp, _diff(6 * B + 3, R))]),
        ("the same, and a payload bit of block 3",
         [{"file": "pcm48_24", "frames": [0, 6 * B]}, {"file": "pcm48_24_lead12", "frames": [6 * B, 28 * B]}],
         [{"op": "xor", "frame": 3 * B + 1000, "channels": [0], "value": "0x00000100"}],
         [(nbp, _diff(3 * B + 1000, R))]),
        ("a block that is not tail after the first tail block (a splice of two lengths)",
         [{"file": "pcm48_24", "frames": [0, 27 * B]}, {"file": "pcm48_24_long", "frames": [27 * B, 34 * B]}],
         [], [(bp, None), (nbp, _diff(27 * B, R))]),
        ("an r5 chip unit changed, the stream stops 50 chips later", one,
         [{"op": "xor", "frame": r5(8, 0, 30), "channels": [0], "value": "0x00000100"},
          {"op": "stop", "frame": r5(8) + 50 * chip}], [(nbp, _diff(r5(8, 0, 30), R))]),
        # The R-header rule reads each bit as it comes: bit 1's second chip
        # (r5 chip 3) at its first chip's value, then r5 chip 27 zeroed: the
        # pair fails first, at its second chip
        ("an r5 bit's chips not opposite, then a later r5 chip zeroed", one,
         [{"op": "xor", "frame": r5(8, 3), "count": chip, "channels": [0], "value": "0xC0000000"},
          {"op": "and", "frame": r5(8, 27), "count": chip, "channels": [0], "value": "0x00000000"}],
         [(nbp, _diff(r5(8, 3), R))]),
        # The R-header rule's words: with chips 173 to 236 whole and without a
        # chip difference in every channel, a CRC or count that fails is a
        # difference at the block's chip 173, also where the count is known
        # only later or not at all; a chip difference in them comes first, and
        # the words are not judged
        ("an r6 bit's two chips inverted in channel 0: the CRC fails", one, [pair(8, 31, [0])],
         [(nbp, _diff(r5(8), R))]),
        ("an r6 bit's two chips inverted in channel 0, the stream stops in the tail before LAST", one,
         [pair(8, 31, [0]), no_last], [(nbp, _diff(r5(8), R))]),
        ("an r6 bit's two chips inverted in channel 0, the stream stops right after chip 236", one,
         [pair(8, 31, [0]), {"op": "stop", "frame": r5(8, 64)}], [(nbp, _diff(r5(8), R))]),
        ("block 8's R-header spells count 29 with a valid CRC", one, _rhdr_bits(8, {5: 28 ^ 29}),
         [(nbp, _diff(r5(8), R))]),
        ("block 8's R-header spells count 29, the stream stops in the tail before LAST", one,
         _rhdr_bits(8, {5: 28 ^ 29}) + [no_last], [(nbp, _diff(r5(24), R))]),
        ("block 24's R-header spells count 29 with a valid CRC", one, _rhdr_bits(24, {5: 28 ^ 29}),
         [(nbp, _diff(r5(24), R))]),
        ("block 24's R-header spells count 29, the stream stops in the tail before LAST", one,
         _rhdr_bits(24, {5: 28 ^ 29}) + [no_last], [(nbp, _diff(r5(24), R))]),
        ("block 8's CRC fails (an r6 bit's chips inverted in channel 1), block 24 spells count 29", one,
         [pair(8, 20, [1])] + _rhdr_bits(24, {5: 28 ^ 29}), [(nbp, _diff(r5(8), R))]),
        ("an r5 bit's two chips inverted in channel 0, chip 223 zeroed in channel 0", long,
         [pair(8, 1, [0]), {"op": "and", "frame": r5(8, 50), "count": chip, "channels": [0],
                            "value": "0x00000000"}], [(nbp, _diff(r5(8, 50), R))]),
        ("chip 183 zeroed in channel 0, an r5 bit's two chips inverted in channel 1", long,
         [{"op": "and", "frame": r5(8, 10), "count": chip, "channels": [0], "value": "0x00000000"},
          pair(8, 1, [1])], [(nbp, _diff(r5(8, 10), R))]),
        ("an r5 bit's two chips inverted in channel 0, chip 192's first unit changed in channel 1", long,
         [pair(8, 13, [0]), {"op": "xor", "frame": r5(8, 19), "channels": [1], "value": "0x00000100"}],
         [(nbp, _diff(r5(8, 19), R))]),
        ("block 8's R-header spells count 29, a unit of chip 230 changed in channel 0", one,
         _rhdr_bits(8, {5: 28 ^ 29}) + [{"op": "xor", "frame": r5(8, 57, 3), "channels": [0],
                                         "value": "0x00000100"}], [(nbp, _diff(r5(8, 57, 3), R))]),
        ("block 8's R-header spells count 29, a payload bit of block 10", one,
         _rhdr_bits(8, {5: 28 ^ 29}) + [{"op": "xor", "frame": 10 * B + 3000, "channels": [0],
                                         "value": "0x00000100"}], [(nbp, _diff(r5(8), R))]),
        ("block 8's R-header spells count 29, a payload bit of block 10, the stream stops in the tail "
         "before LAST", one,
         _rhdr_bits(8, {5: 28 ^ 29}) + [{"op": "xor", "frame": 10 * B + 3000, "channels": [0],
                                         "value": "0x00000100"}, no_last],
         [(nbp, _diff(10 * B + 3000, R))]),
        ("DoP, an r6 bit's two chips inverted in channel 0, a marker zeroed in chip 193", d1,
         [dpair, {"op": "and", "frame": d173 + 20 * dchip + 3, "value": "0x00FFFFFF"}],
         [(nbp, _diff(d173 + 20 * dchip + 3, DOP))]),
        ("DoP, an r6 bit's two chips inverted in channel 0, a padding bit set in chip 193",
         [{"file": "dop64", "slot": 32}],
         [dpair, {"op": "xor", "frame": d173 + 20 * dchip + 3, "channels": [1], "value": "0x00000001"}],
         [(nbp, _diff(d173 + 20 * dchip + 3, DOP))]),
        ("the stream starts at the last lead-in block", one, [{"op": "start", "frame": B}],
         [(bp, None)]),
        ("the stream stops right after the first tail X-sync", one,
         [{"op": "stop", "frame": 26 * B + 8}], [(bp, None)]),
        ("the stream stops inside the LAST block, after its X-sync", one,
         [{"op": "stop", "frame": 27 * B + 100}], [(bp, None)]),
        ("96 frames removed in the body at 768 kHz (one USB packet)", [{"file": "pcm768_24"}],
         [{"op": "remove", "frame": 5 * 65536 + 1234, "count": 96}],
         [(nbp, _diff(5 * 65536 + 1234, 768000))]),
        ("16-bit content in a 24-bit slot", [{"file": "pcm48_16", "slot": 24}], [], [(bp, None)]),
        ("DoP, a marker that differs between channels", d1,
         [{"op": "xor", "frame": 5 * F + 77, "channels": [1], "value": "0xFF000000"}],
         [(nbp, _diff(5 * F + 77, DOP))]),
        ("a code-sweep file", [{"file": "sweep48_16"}], [], [(bp, None)]),
        ("a code-sweep file, every body sample of code 32766 in channel 0 changed to 32767",
         [{"file": "sweep48_16"}],
         [{"op": "recode", "frame": 2 * B, "count": 24 * B, "channels": [0],
           "from": "0x7FFE0000", "to": "0x7FFF0000"}],
         [(nbp, _diff(_first_code(), R))]),
        ("the same change in a regular 16-bit file, which does not carry that code",
         [{"file": "pcm48_16"}],
         [{"op": "recode", "frame": 2 * B, "count": 24 * B, "channels": [0],
           "from": "0x7FFE0000", "to": "0x7FFF0000"}], [(bp, None)]),
        ("a PCM file and a DoP file at the same frame rate, gapless",
         [{"file": "pcm176_24"}, {"file": "dop64"}], [], [(bp, None), (bp, None)]),
        # The first body block, 24, is an R-header block (the standard 48 kHz lead-in)
        ("a 24-block lead-in: the first body block is an R-header block", [{"file": "pcm48_24_lead24"}],
         [], [(bp, None)]),
        ("a 24-block lead-in, an r5 chip unit of the first body block", [{"file": "pcm48_24_lead24"}],
         [{"op": "xor", "frame": r5(24, 3, 4), "channels": [0], "value": "0x00000100"}],
         [(nbp, _diff(r5(24, 3, 4), R))]),
        ("a 24-block lead-in, the stream stops right after the first tail X-sync",
         [{"file": "pcm48_24_lead24"}], [{"op": "stop", "frame": 28 * B + 8}], [(bp, None)]),
        # A file cut by the next one: its diff is the first unit after the cut,
        # the frames of a cut X-sync that arrived included
        ("a DoP file cut in its body, then a PCM file at the same frame rate from its block 1",
         [{"file": "dop64", "frames": [0, 6 * F + 1000]}, {"file": "pcm176_24", "frames": [F, 12 * F]}],
         [], [(nbp, _diff(2 * (6 * F + 1000), 2 * DOP)), (bp, None)]),
        ("a DoP file cut 7 frames into a body block's X-sync, then a PCM file from its block 1",
         [{"file": "dop64_long", "frames": [0, 21 * F + 7]}, {"file": "pcm176_24", "frames": [F, 12 * F]}],
         [], [(nbp, _diff(2 * (21 * F + 7), 2 * DOP)), (bp, None)]),
        ("a DoP file cut 7 frames into a body block's X-sync, then a DoP file from its block 1",
         [{"file": "dop64_long", "frames": [0, 21 * F + 7]}, {"file": "dop64", "frames": [F, 12 * F]}],
         [], [(nbp, _diff(2 * (21 * F + 7), 2 * DOP)), (bp, None)]),
        # The extent's end from the count of an R-header, the only one, in the
        # lead-in (not compared): 18 blocks instead of 16 cover the zeros after
        # the file; with a reserved flag bit set the R-header is not used.
        ("the LAST X-sync broken, the lead-in R-header's count 18 (a valid R-header)",
         [{"file": "pcm48_24_lead10"}, {"zeros": 3 * B}],
         [{"op": "xor", "frame": 15 * B + 3, "value": "0x00100000"}] + _rhdr_bits(8, {5: 0x0002}),
         [(bp, None)]),
        ("the LAST X-sync broken, the lead-in R-header's count 18 and a reserved flag bit set",
         [{"file": "pcm48_24_lead10"}, {"zeros": 3 * B}],
         [{"op": "xor", "frame": 15 * B + 3, "value": "0x00100000"}]
         + _rhdr_bits(8, {1: 0x0008, 5: 0x0002}), [(bp, None), (unk, None)]),
        # Several R-headers give a count: the first sets the extent and the
    # file's declared size (12.6). The file has 28 blocks, its LAST X-sync
    # (block 27) is erased, the last X-sync is block 26's. The lead-in
    # R-header (block 8, not compared) is changed to spell 40 with a valid
    # CRC: the extent covers the zeros, and the body R-header's 28 differs
    # from the declared size, a difference at its first r5 unit. Spelling
    # 20, no greater than 26, it disagrees with the X-syncs that arrived: a
    # difference at its own first r5 unit; it gives no extent, which ends a
    # block after block 26, and the body R-header's 28 does not replace it.
    ("the LAST X-sync erased, the lead-in R-header's count 40, the body R-header's 28, "
     "5 blocks of zeros after", [{"file": "pcm48_24_10_16_2"}, {"zeros": 5 * B}],
     [{"op": "and", "frame": 27 * B, "count": 8, "value": "0x00000000"}]
     + _rhdr_bits(8, {5: 28 ^ 40}), [(nbp, _diff(r5(24), R))]),
    ("the LAST X-sync erased, the lead-in R-header's count 20, the body R-header's 28, "
     "a block of zeros after", [{"file": "pcm48_24_10_16_2"}, {"zeros": B}],
     [{"op": "and", "frame": 27 * B, "count": 8, "value": "0x00000000"}]
     + _rhdr_bits(8, {5: 28 ^ 20}), [(nbp, _diff(r5(8), R)), (unk, None)]),
    ("the LAST X-sync erased, both R-headers' count 28, a block of zeros after",
     [{"file": "pcm48_24_10_16_2"}, {"zeros": B}],
     [{"op": "and", "frame": 27 * B, "count": 8, "value": "0x00000000"}], [(bp, None)]),
    # A reserved flag bit in one channel's X-sync (a valid CRC): that
        # channel's X-sync is not used, the other channel's is (12.6 b)
        ("a reserved flag bit in channel 0's last lead-in X-sync", one, _xsync_flags(1, 0, 0x0008),
         [(bp, None)]),
        ("a reserved flag bit in channel 1's last lead-in X-sync", one, _xsync_flags(1, 1, 0x0008),
         [(bp, None)]),
        ("a reserved flag bit in both channels' last lead-in X-syncs", one,
         _xsync_flags(1, 0, 0x0008) + _xsync_flags(1, 1, 0x0008), [(nbp, _diff(B, R))]),
        ("a reserved flag bit in channel 0's X-sync of body block 5", one, _xsync_flags(5, 0, 0x0008),
         [(nbp, _diff(5 * B + 3, R))]),
        # The count of an R-header is read at every chip's centre (12.6): with
        # a Barker chip erased there is none, and the extent ends a block after
        # block 11, the last block with an X-sync
        ("the X-syncs of tail blocks 12 to 20 erased, the lead-in R-header's first Barker chip erased",
         [{"file": "pcm48_24_10_1_10"}],
         [{"op": "and", "frame": 8 * B + r0, "count": chip, "value": "0x00000000"}]
         + [{"op": "and", "frame": k * B, "count": 8, "value": "0x00000000"} for k in range(12, 21)],
         [(bp, None), (unk, None)]),
        ("the X-syncs of tail blocks 12 to 20 erased, the lead-in R-header intact",
         [{"file": "pcm48_24_10_1_10"}],
         [{"op": "and", "frame": k * B, "count": 8, "value": "0x00000000"} for k in range(12, 21)],
         [(bp, None)]),
    ]
    # allow_silence true: silence outside every file gives no result, anything else still does
    pad = 3 * B
    quiet = [
        ("silence allowed: zeros before, between and after two files",
         [{"zeros": pad}, {"file": "pcm48_24"}, {"zeros": pad}, {"file": "pcm48_16"}, {"zeros": pad}],
         [], [(bp, None), (bp, None)]),
        ("silence allowed: zeros only", [{"zeros": 50000}], [], []),
        ("silence allowed: DoP idle before, between and after two DoP files",
         [{"dop_idle": 3 * F}, {"file": "dop64"}, {"dop_idle": 3 * F}, {"file": "dop64"},
          {"dop_idle": 3 * F}], [], [(bp, None), (bp, None)]),
        ("silence allowed: a track that is not a test file between two files",
         [{"file": "pcm48_24"}, {"noise": 5 * B, "key": "0x0000ABCD"}, {"file": "pcm48_16"}], [],
         [(bp, None), (unk, None), (bp, None)]),
        ("silence allowed: a near-silent word in the zeros between two files",
         [{"file": "pcm48_24"}, {"zeros": pad}, {"file": "pcm48_16"}],
         [{"op": "xor", "frame": 28 * B + pad // 2, "channels": [1], "value": "0x00000100"}],
         [(bp, None), (unk, None), (bp, None)]),
        ("silence allowed: DoP idle with a padding bit set", [{"dop_idle": 3 * F, "slot": 32}],
         [{"op": "xor", "frame": F, "channels": [0], "value": "0x00000001"}], [(unk, None)]),
        ("silence allowed: a DoP file whose X-syncs all carry reserved kind 3", d1, _dop_kind(3, 12),
         [(unk, None)]),
        ("silence allowed: a DoP file whose X-syncs all carry reserved kind 4", d1, _dop_kind(4, 12),
         [(unk, None)]),
        ("silence allowed: a file muted to zeros", one,
         [{"op": "and", "frame": 0, "count": 28 * B, "value": "0x00000000"}], []),
    ]
    strict = [
        ("zeros before, between and after two files",
         [{"zeros": pad}, {"file": "pcm48_24"}, {"zeros": pad}, {"file": "pcm48_16"}, {"zeros": pad}],
         [], [(unk, None), (bp, None), (unk, None), (bp, None), (unk, None)]),
        ("DoP idle before and after a DoP file", [{"dop_idle": 3 * F}, {"file": "dop64"},
                                                    {"dop_idle": 3 * F}], [],
         [(unk, None), (bp, None), (unk, None)]),
    ]
    out = []
    for allow, group in ((False, cases + strict), (True, quiet)):
        for name, stream, change, results in group:
            out.append({"case": name, "allow_silence": allow, "stream": stream, "change": change,
                        "results": [_result(r, name) for r in results]})
    return out


def verdicts():
    files = {k: dict(v, kind=v["kind"], seed=hx(spec.DEFAULT_SEED, 8), channels=2)
             for k, v in VERDICT_FILES.items()}
    return {"about": "Streams built from the files below in order (a part may give the "
                     "received frame rate, default the file's or DoP frame rate, and the slot "
                     "bits, default 24 for DoP and 32 for PCM), then each change in order, checked "
                     "with the case's allow_silence (default false). "
                     "Results are those of a conforming checker, in stream order; diff in "
                     "seconds from the file's start as generated, or -.",
            "files": files, "parts": PARTS, "ops": OPS, "cases": verdict_cases()}


def build():
    files = [fileset.pcm_file(44100, 16), fileset.pcm_file(48000, 24),
             fileset.pcm_file(96000, 32), fileset.pcm_file(768000, 24),
             fileset.sweep_file(44100, 16), fileset.sweep_file(48000, 24)]
    dsd = [fileset.dsd_file(2822400, "dsf"), fileset.dsd_file(5644800, "dff"),
           fileset.dsd_file(11289600, "dop")]
    return {
        "format": "BPC1", "version": 1,
        "about": "Golden vectors for docs/bpc1.md. Samples are signed integers "
                 "at the file depth; words, keys and bytes are hexadecimal.",
        "primitives": primitives(),
        "pcm": [pcm_vectors(f) for f in files],
        "dsd": [dsd_vectors(f) for f in dsd],
        "checking": checking(),
        "verdicts": verdicts(),
    }


def dumps(obj):
    """JSON with one line per list of scalars, so the file stays readable."""
    def enc(o, ind):
        pad = " " * ind
        if isinstance(o, dict) and all(not isinstance(v, (dict, list)) for v in o.values()) \
                and len(json.dumps(o)) <= 100:
            return pad + json.dumps(o)
        if isinstance(o, dict):
            items = [f'{pad} {json.dumps(k)}: {enc(v, ind + 1).lstrip()}' for k, v in o.items()]
            return pad + "{\n" + ",\n".join(items) + "\n" + pad + "}"
        if isinstance(o, list):
            if all(not isinstance(x, (dict, list)) for x in o):
                return pad + json.dumps(o)
            return pad + "[\n" + ",\n".join(enc(x, ind + 1) for x in o) + "\n" + pad + "]"
        return pad + json.dumps(o)
    return enc(obj, 0) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m generator.vectors")
    ap.add_argument("--check", action="store_true", help="compare with the committed file")
    ap.add_argument("--out", default=PATH)
    a = ap.parse_args(argv)
    text = dumps(build())
    if a.check:
        with open(a.out) as f:
            same = f.read() == text
        print("vectors match" if same else "vectors DIFFER")
        return 0 if same else 1
    with open(a.out, "w") as f:
        f.write(text)
    print(f"wrote {os.path.normpath(a.out)} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
