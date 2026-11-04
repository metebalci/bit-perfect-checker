"""Prove bit-perfect: the results of a received stream.

The rules of spec 12.6, "Verdicts". A file
is recognised by its X-syncs (PCM: the top 16 bits of eight words; DoP: the
16 DSD bytes of eight frames whose markers alternate, decided per X-sync;
native DSD: 16 bytes). No inverted search. Per file, first that holds:

  BIT_PERFECT      the rate and channel count match, the body's limits are
                   shown, and every body unit arrived as expected, in one run
  NOT_BIT_PERFECT  otherwise

Every stretch of units outside every file's extent, silence included, is one
UNKNOWN_SIGNAL result; with the setting allow_silence, a stretch of silence
gives none. DEVICE_OVERRUN is for hardware checkers only.

A file passes only on what it shows: the lead-in X-sync before its body (or
a body from block 0), the tail X-sync after it (or its own LAST flag), and
every body unit equal to the expected one in every channel, X-syncs and
R-header units included. The R-header chips of r5 (block count) and r6 (its
CRC) are checked by the chip rule: each chip one of its two values, the two
chips of a bit opposite, r6 the CRC of r0-r5, and r5 the LAST block's index
+ 1, or the same in every body R-header. This module is the authority for
the hardware checker's verdict, end, diff and bits; the diagnosis
(check.py) only describes what changed.
"""

from dataclasses import dataclass

import numpy as np

from . import exact, model, readers

KIND_NAMES = {model.KIND_PCM: "PCM", model.KIND_DSF: "DSF", model.KIND_DFF: "DFF",
              model.KIND_DOP: "DOP", model.KIND_SWEEP: "SWEEP"}
R56 = range(13 + 5 * 32, 13 + 7 * 32)    # chips 173-236: r5 (block count) and r6 (CRC)
PCM_CHIP = 1 << 29                         # W of a +1 chip; -1 is its negation
DSD_CHIP = (0xEE, 0x11)                    # bytes of a +1 and a -1 chip
IDLE = (0x69, 0x96)                        # DSD idle bytes: silence
FLAGS = model.F_LEAD_IN | model.F_TAIL | model.F_LAST


@dataclass
class Sync:
    """An X-sync: position (stream units), block, fields per channel, DoP."""
    pos: int
    k: int
    fields: dict
    dop: bool
    U: int                  # block length in stream units

    @property
    def flags(self):
        f = 0
        for x in self.fields.values():
            f |= x.flags
        return f

    @property
    def key(self):
        f = next(iter(self.fields.values()))
        return (f.kind, f.depth_code, f.rate_code, f.nch, f.seed, self.dop)


def effective_bits(w):
    """Bits from the MSB down to the lowest bit set in any 32-bit word."""
    acc = int(np.bitwise_or.reduce(np.asarray(w).astype(np.int32).view(np.uint32).ravel())) \
        if np.size(w) else 0
    return 32 - ((acc & -acc).bit_length() - 1) if acc else 0


def _markers_ok(w):
    """Per DoP frame of w (frames, nch): the marker is 0x05 or 0xFA, the same
    in every channel."""
    m = (w.view(np.uint32) >> 24) & 0xFF
    return (m == m[:, :1]).all(axis=1) & ((m[:, 0] == 0x05) | (m[:, 0] == 0xFA)), m[:, 0]


def _dop_bytes(raw):
    w = raw.view(np.uint32)
    out = np.empty((2 * len(w), w.shape[1]), dtype=np.uint8)
    out[0::2] = (w >> 16) & 0xFF
    out[1::2] = (w >> 8) & 0xFF
    return out


def find_syncs(stream):
    """The X-syncs of a stream, in stream units (frames; native DSD: bytes),
    each with the channels whose X-sync has its reserved flag bits 0."""
    out = [Sync(a.pos, a.k, a.fields, False, exact.units_per_block(_f(a.fields)))
           for a in exact.find_anchors(stream)]
    if not stream.dsd and len(stream.data) >= 8:
        by = readers.Stream(True, stream.rate * 16, stream.nch, _dop_bytes(stream.data), "DoP")
        ok, m = _markers_ok(stream.data)
        alt = np.zeros(len(ok), dtype=bool)
        alt[:-1] = ok[:-1] & ok[1:] & (m[:-1] != m[1:])
        for a in exact.find_anchors(by):
            f = a.pos // 2
            if a.pos % 2 == 0 and alt[f:f + 7].all() and f + 7 < len(ok):
                out.append(Sync(f, a.k, a.fields, True, exact.units_per_block(_f(a.fields)) // 2))
    for s in out:
        # a channel whose X-sync has a reserved flag bit set is not used (3.4);
        # the others that agree with the first one left on identity and block
        s.fields = {c: f for c, f in s.fields.items() if f.flags & ~FLAGS == 0}
        if s.fields:
            f0 = _f(s.fields)
            s.fields = {c: f for c, f in s.fields.items() if f.same_file(f0) and f.block == f0.block}
            s.k = f0.block
            s.U = exact.units_per_block(f0) // (2 if s.dop else 1)
    out = [s for s in out if s.fields]
    return sorted(out, key=lambda s: s.pos)


def _f(fields):
    return next(iter(fields.values()))


def split(syncs, stream):
    """Files of a stream: (start, stop, end, syncs) in stream units. A file
    ends at its LAST block, at an X-sync of another identity (NEXT), and where
    the block index goes back or an X-sync not flagged TAIL follows a TAIL
    one (BACK). Its extent: from k x U units before its
    first X-sync (of block k) to (count - k) x U units after its last X-sync
    (of block k), count from its LAST X-sync or an R-header that passes
    section 6, else to the end of its last block with an X-sync; one block
    more at each end, never outside the stream or past the next file's first
    X-sync: the units before it are this file's as they arrived (12.6)."""
    n = len(stream.data)
    groups = []
    for s in syncs:
        g = groups[-1] if groups else None
        # a file ends at its LAST block, at another identity, where the block
        # index goes back, and at an X-sync not flagged TAIL after a TAIL one
        tail = g and any(x.flags & model.F_TAIL for x in g)
        if (g and not g[-1].flags & model.F_LAST and s.key == g[-1].key and s.k >= g[-1].k
                and not (tail and not s.flags & model.F_TAIL)):
            g.append(s)
        else:
            groups.append([s])
    files = []
    for i, g in enumerate(groups):
        a, z = g[0], g[-1]
        U = a.U
        start = a.pos - a.k * U - U
        count = z.k + 1 if z.flags & model.F_LAST else _count(stream, g)
        stop = z.pos + ((count - z.k) if count and count > z.k else 1) * U + U
        end = "LAST" if z.flags & model.F_LAST else "STOP"
        if i + 1 < len(groups):
            nxt = groups[i + 1][0]
            if end != "LAST":
                end = "NEXT" if nxt.key != z.key else "BACK"
            # the units up to the next file's first X-sync, as they arrived
            stop = min(stop, nxt.pos)
        files.append([max(0, start), min(stop, n), end, g])
    for i in range(1, len(files)):
        files[i][0] = max(files[i][0], files[i - 1][1])
    for f in files:
        f[1] = max(f[1], f[0])
    return [tuple(f) for f in files]


def _count(stream, g):
    """The block count r5 of an R-header of the file's X-syncs, read as 12.6
    says: every chip at its centre unit (PCM: the word's sign; DSD: byte 0xEE
    or 0x11), the Barker chips as generated and the two chips of each bit
    opposite, then section 6 and r0-r4 equal to h0-h4 of the channel's
    X-sync; or None."""
    data = _dop_bytes(stream.data) if g[0].dop else stream.data
    for s in g:
        if not model.Signal.is_rhdr(s.k):
            continue
        for c, f in s.fields.items():
            sig = model.Signal(f)
            sig.flag_override[s.k] = f.flags      # r0-r4 are h0-h4 of this X-sync
            dsd = model.is_dsd_kind(f.kind)
            p = s.pos * (2 if s.dop else 1)
            a, L = (p + sig.r_start // 8, sig.chip // 8) if dsd else (p + sig.r_start, sig.chip)
            idx = [a + j * L + L // 2 for j in range(model.N_CHIPS)]
            if idx[-1] >= len(data):
                continue
            v = data[idx, c]
            ch = (np.where(v == DSD_CHIP[0], 1, np.where(v == DSD_CHIP[1], -1, 0)) if dsd
                  else np.sign(v.astype(np.int64)))
            if tuple(ch[:13]) != model.BARKER13 or (ch[13:] == 0).any() or (ch[13::2] != -ch[14::2]).any():
                continue
            bits = ch[13::2] > 0
            words = [int("".join("1" if b else "0" for b in bits[16 * i:16 * i + 16]), 2)
                     for i in range(7)]
            if model.crc16_words(words[:6]) == words[6] and words[:5] == sig.words(f.channel, s.k):
                return words[5]
    return None


def verify_stream(stream, allow_silence=False):
    """The results of a received stream, in order (dicts): one per file, and
    one UNKNOWN_SIGNAL per stretch outside the files, except, with
    allow_silence, a stretch of silence."""
    n = len(stream.data)
    files = split(find_syncs(stream), stream)
    out, at = [], 0
    for start, stop, end, g in files:
        if start > at:
            out += _outside(stream, at, start, allow_silence)
        out.append(_verify_file(stream, start, stop, end, g))
        at = max(at, stop)
    if at < n:
        out += _outside(stream, at, n, allow_silence)
    for i, r in enumerate(out, 1):
        r["file"] = i
    return out


def _outside(stream, a, b, allow_silence):
    return [] if allow_silence and _silent(stream, a, b) else [_unknown(stream, a, b)]


def verify(stream, allow_silence=False):
    """The result of a stream that holds one file."""
    res = verify_stream(stream, allow_silence)
    if len(res) != 1:
        raise ValueError(f"the stream gives {len(res)} results: use verify_stream()")
    return res[0]


def _silent(stream, a, b):
    """Silence: PCM words 0; DoP frames with markers 0x05 or 0xFA, the same in
    every channel, DSD bytes 0x69 or 0x96 and zero bits below the 24; native
    DSD bytes 0x69 or 0x96."""
    x = stream.data[a:b]
    if stream.dsd:
        return bool(np.isin(x, IDLE).all())
    ok, _ = _markers_ok(x)
    w = x.view(np.uint32)
    idle = (np.isin((w >> 16) & 0xFF, IDLE) & np.isin((w >> 8) & 0xFF, IDLE)
            & ((w & 0xFF) == 0)).all(axis=1)
    return bool(((x == 0).all(axis=1) | (ok & idle)).all())


def _unknown(stream, a, b):
    """A stretch outside every file: UNKNOWN_SIGNAL, the file fields and bits 0."""
    return {"file": 0, "verdict": "UNKNOWN_SIGNAL", "kind": "NONE", "dop": 0, "seed": 0,
             "nch": 0, "file_rate": 0, "depth": 0, "rate": stream.rate, "bits": 0,
             "end": "STOP", "first": a, "units": b - a, "diff": None, "diff_unit": None,
             "diff_at": None, "why": "audio of no recognised file"}


def _verify_file(stream, start, stop, end, g):
    """One file: the stream units [start, stop) and its X-syncs g."""
    f = _f(g[0].fields)
    dop = g[0].dop
    rate = model.RATES[f.rate_code]
    out = {"file": 0, "kind": KIND_NAMES[f.kind], "dop": int(dop), "seed": f.seed,
           "nch": f.nch, "file_rate": rate, "depth": model.DEPTHS[f.depth_code],
           "rate": stream.rate, "bits": 0, "end": end, "first": start, "units": stop - start}
    # The units the file is compared in: DSD bytes for DoP (2 per frame)
    if dop:
        work = readers.Stream(True, stream.rate * 16, stream.nch, _dop_bytes(stream.data), "DoP")
        scale = 2
    else:
        work, scale = stream, 1
    u1 = stop * scale
    U = exact.units_per_block(f)
    by_k = {}
    for s in g:
        by_k.setdefault(s.k, s.pos * scale)
    flags = {}
    for s in g:
        flags[s.k] = flags.get(s.k, 0) | s.flags
    fails = []                      # (source unit, received unit, why)

    # the LEAD_IN X-syncs before the first one not flagged LEAD_IN
    lead = []
    for s in g:
        if not s.flags & model.F_LEAD_IN:
            break
        lead.append(s.k)
    L = max(lead) + 1 if lead else 0 if 0 in by_k else None
    tail = [k for k, fl in flags.items() if fl & model.F_TAIL]
    last = [k for k, fl in flags.items() if fl & model.F_LAST]
    E = min(tail) - 1 if tail else last[0] if last else None
    if L is None:
        # The body's start is not known: the first block whose X-sync arrived
        k = min(by_k)
        return dict(out, **_result([(k * U, by_k[k], "no lead-in X-sync before the body")], rate, f))
    if L in by_k:
        p = by_k[L]
    else:
        p = by_k[L - 1] + U
        fails.append((L * U, p, "the first body block's X-sync did not arrive"))
    if E is not None and E < L:
        fails.append((L * U, p, "the body's limits disagree"))
    rx_rate = stream.rate * 16 if dop else stream.rate
    if rx_rate != rate or stream.nch != f.nch:
        fails.append((L * U, p, "rate or channel count differs"))
        return dict(out, **_result(fails, rate, f))

    # Body: one run of units from the first body block's X-sync on
    n = (E + 1 - L) * U if E is not None and E >= L else max(0, u1 - p)
    sig = model.Signal(f, lead=L, body=(E + 1 - L) if E is not None and E >= L else None,
                       total=last[0] + 1 if last else 0)
    rx = work.data[p:min(p + n, u1)]
    eq = (rx == sig.ref(L * U, L * U + len(rx))).all(axis=1)
    body = []                       # (body unit, why)
    body += _chip_rule(sig, work.dsd, rx, eq, L, n, last[0] + 1 if last else None)
    bad = np.nonzero(~eq)[0]
    if len(bad):
        body.append((int(bad[0]), "a body unit differs"))
    if len(rx) < n:
        body.append((len(rx), "the body did not arrive in full"))
    if E is None:
        body.append((len(rx), "no tail X-sync or LAST block after the body"))
    if dop:
        body += _dop_frames(stream, p, p + len(rx))
    fails += [(L * U + i, p + i, why) for i, why in body]
    if dop:
        out["bits"] = effective_bits(stream.data[p // 2:(p + len(rx) + 1) // 2])
    else:
        out["bits"] = 1 if work.dsd else effective_bits(rx)
    return dict(out, **_result(fails, rate, f, scale))


def _result(fails, file_rate, f, scale=1):
    """The verdict and the first failure: diff in seconds from the file's
    start as generated (PCM: frame / rate; DSD: byte x 8 / DSD rate)."""
    if not fails:
        return {"verdict": "BIT_PERFECT", "diff": None, "diff_unit": None, "diff_at": None,
                "why": None}
    src, at, why = min(fails)
    # milliseconds, rounded half up, in integers: PCM frame / rate, DSD byte x 8 / rate
    num = src * 1000 * (8 if model.is_dsd_kind(f.kind) else 1)
    ms = (num + file_rate // 2) // file_rate
    return {"verdict": "NOT_BIT_PERFECT", "diff": ms / 1000, "diff_unit": src,
            "diff_at": at // scale, "why": why}


def _chips(sig, dsd, k0):
    """(first unit, length, +1 value, -1 value) of each r5 and r6 chip of the
    R-header block that starts at body unit k0."""
    if dsd:
        a, n = sig.r_start // 8, sig.chip // 8
        return [(k0 + a + j * n, n, DSD_CHIP[0], DSD_CHIP[1]) for j in R56]
    return [(k0 + sig.r_start + j * sig.chip, sig.chip, PCM_CHIP, -PCM_CHIP) for j in R56]


def _chip_rule(sig, dsd, rx, eq, L, n, count):
    """The chips of r5 and r6 in the body's R-header blocks, in order, as
    bpc1_checker.c reads them: a bit's first chip one of its two values, the
    same through the chip; its second chip the opposite value, unit by unit;
    r6 = crc16(r0..r5) with r0-r4 of the block; r5 = count (the LAST block's
    index + 1), or the same in every body R-header. Marks the chip units as
    compared in eq; returns the failures in body units, at most one per
    channel and R-header block."""
    U = sig.units_per_block
    fails, seen = [], None
    for k in range(L, L + -(-n // U)):
        if not model.Signal.is_rhdr(k):
            continue
        chips = [ch for ch in _chips(sig, dsd, (k - L) * U) if ch[0] < len(rx)]
        for a, m, _, _ in chips:
            eq[a:min(a + m, len(rx))] = True
        whole = len(chips) == len(R56) and chips[-1][0] + chips[-1][1] <= len(rx)
        for c in range(sig.nch):
            bits = []
            for j, (a, m, plus, minus) in enumerate(chips):
                seg = rx[a:a + m, c]       # a chip cut by the body's end: the units that arrived
                if j % 2 == 0:             # a bit's first chip: taken as received, if a value
                    if seg[0] not in (plus, minus):
                        fails.append((a, "an R-header chip is neither of its values"))
                        break
                    want, why = seg[0], "an R-header chip is neither of its values"
                    bits.append(1 if want == plus else 0)
                else:                      # its second chip: the opposite, unit by unit
                    want, why = (minus if bits[-1] else plus), "an R-header bit's chips are not opposite"
                off = np.nonzero(seg != want)[0]
                if len(off):
                    fails.append((a + int(off[0]), why))
                    break
            else:
                if not whole:
                    continue       # the CRC and count need every chip; the cut fails anyway
                r5 = int("".join(map(str, bits[0:16])), 2)
                r6 = int("".join(map(str, bits[16:32])), 2)
                if model.crc16_words(sig.words(c, k) + [r5]) != r6:
                    fails.append((chips[0][0], "an R-header CRC fails"))
                elif count is not None and r5 != count & 0xFFFF:
                    fails.append((chips[0][0], "an R-header block count is not the LAST block's"))
                elif count is None and seen is not None and r5 != seen:
                    fails.append((chips[0][0], "the R-headers' block counts disagree"))
                seen = r5 if seen is None else seen
    return fails


def _dop_frames(stream, u0, u1):
    """DoP body frames: the marker 0x05/0xFA alternating in every frame,
    either phase first, the same in all channels; zero bits below the 24 in
    wider slots. Returns the first failure, in DSD units from u0."""
    f0, f1 = u0 // 2, (u1 + 1) // 2
    w = stream.data[f0:f1]
    ok, m = _markers_ok(w)
    ok[1:] &= m[1:] != m[:-1]
    if stream.slot_bits > 24:
        ok &= ((w.view(np.uint32) & 0xFF) == 0).all(axis=1)
    bad = np.nonzero(~ok)[0]
    return [(2 * (f0 + int(bad[0])) - u0, "a DoP frame's marker or padding differs")] if len(bad) else []
