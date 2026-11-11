# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Exact layer: X-sync lock, frame-exact comparison and gap analysis.

Units are PCM frames, or bytes per channel (8 DSD bits) for DSD.

Every valid X-sync is an anchor: it ties a received unit position to a block
start. Between two anchors the received units are compared with the expected
signal. At a mismatch that does not end within a few units (an altered run),
the checker tries a short list of frame offsets (dropped or inserted units)
around the mismatch, the "+-2 packet search", and takes the first that makes
the following units match again.
"""

import itertools
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from . import model

VERIFY = 16          # units that must match to accept a re-alignment
RUN_WINDOW = 64      # an altered run must end within this many units
LONG_UNITS = 4096    # the long search's reach, at most a block


@dataclass
class Anchor:
    pos: int
    k: int
    fields: dict      # rx channel -> model.Fields


@dataclass
class Event:
    kind: str         # dropped, inserted, repeated, zero_inserted, silence_inserted,
                      # zero_filled, altered, unlocated, discontinuity
    count: int
    pos: int          # received unit index
    block: int        # source block index
    section: str      # lead-in, body, tail


@dataclass
class ExactResult:
    sig: model.Signal
    anchors: list
    chmap: list               # rx channel -> source channel
    events: list = field(default_factory=list)
    regions: list = field(default_factory=list)   # (rx_start, src_start, length)
    body_units: int = 0        # received body units compared
    body_equal: int = 0        # of those, exactly equal on every channel
    body_blocks: set = field(default_factory=set)
    polarity: int = 1          # -1: compared against the inverted reference

    def ref(self, u0, u1, inverted=True):
        r = self.sig.ref(u0, u1)[:, self.chmap]
        if inverted and self.polarity < 0:
            r = invert_ref(r, self.sig.depth)
        return r


def invert_ref(r, depth):
    """Expected samples after a polarity inversion at the file depth."""
    top = (1 << 31) - (1 << (32 - depth))
    return np.minimum(-r.astype(np.int64), top).astype(np.int32)


def _sync_words_pcm(w, i, c):
    return [int(x) for x in ((w[i:i + 8, c].view(np.uint32) >> 16) & 0xFFFF)]


def _inverted_sync(neg, com):
    """X-sync fields from the top 16 bits of -W (neg) and ~W (com) of eight
    words, each word read as either: after an inversion and a truncation the
    top bits of -W are ~w unless the bits below them were zero."""
    opts = [(int(a),) if a == b else (int(a), int(b)) for a, b in zip(neg, com)]
    for words in itertools.product(*opts):
        if words[0] != model.MAGIC0 or words[1] != model.MAGIC1:
            continue
        f = model.parse_sync(list(words))
        if f is not None:
            return f
    return None


def find_anchors(stream, inverted=False):
    """The anchors of a stream. inverted (PCM): search the X-syncs of an
    inverted stream, each word read as -W or ~W."""
    data = stream.data
    found = {}
    for c in range(stream.nch):
        if inverted:
            x = data[:, c].astype(np.int64)
            neg, com = ((-x) & 0xFFFFFFFF) >> 16, ((~x) & 0xFFFFFFFF) >> 16
            if len(x) < 8:
                continue
            m0 = (neg == model.MAGIC0) | (com == model.MAGIC0)
            m1 = (neg == model.MAGIC1) | (com == model.MAGIC1)
            for i in np.nonzero(m0[:-7] & m1[1:-6])[0]:
                i = int(i)
                f = _inverted_sync(neg[i:i + 8], com[i:i + 8])
                if f is not None:
                    found.setdefault(i, {})[c] = f
            continue
        if stream.dsd:
            b = data[:, c]
            if len(b) < 16:
                continue
            cand = np.nonzero((b[:-15] == 0xB1) & (b[1:-14] == 0xC0) &
                              (b[2:-13] == 0x5E) & (b[3:-12] == 0xED))[0]
        else:
            top = (data[:, c].view(np.uint32) >> 16)
            if len(top) < 8:
                continue
            cand = np.nonzero((top[:-7] == model.MAGIC0) & (top[1:-6] == model.MAGIC1))[0]
        for i in cand:
            i = int(i)
            if stream.dsd:
                raw = data[i:i + 16, c].tobytes()
                words = [int.from_bytes(raw[2 * j:2 * j + 2], "big") for j in range(8)]
            else:
                words = _sync_words_pcm(data, i, c)
            f = model.parse_sync(words)
            if f is not None:
                found.setdefault(i, {})[c] = f
    return [Anchor(p, next(iter(fl.values())).block, fl) for p, fl in sorted(found.items())]


def file_identity(anchors):
    """Most common file identity among the anchors, and the anchors that agree."""
    key = lambda f: (f.kind, f.depth_code, f.rate_code, f.nch, f.seed)
    votes = Counter(key(f) for a in anchors for f in a.fields.values())
    best = votes.most_common(1)[0][0]
    ref = next(f for a in anchors for f in a.fields.values() if key(f) == best)
    keep = []
    for a in anchors:
        fl = {c: f for c, f in a.fields.items() if key(f) == best}
        if fl and len({f.block for f in fl.values()}) == 1:
            keep.append(Anchor(a.pos, a.k, fl))
    return ref, keep


def _key(a):
    f = next(iter(a.fields.values()))
    return (f.kind, f.depth_code, f.rate_code, f.nch, f.seed)


def _last(a):
    return any(f.flags & model.F_LAST for f in a.fields.values())


def units_per_block(f):
    """B in units (frames, or bytes per channel for DSD) for a file's fields."""
    _, m = model.rate_family(model.RATES[f.rate_code])
    return 4096 * m // 8 if model.is_dsd_kind(f.kind) else 4096 * m


def split_files(anchors, n):
    """Cut a received stream of n units into files (section 12.6): at the end
    of a block flagged LAST, at an identity change and where the block index
    goes back. Returns (start, stop, end, anchors) per file, end being LAST,
    NEXT, BACK or STOP. A file ends at the end of its LAST block, or at the
    next file's first anchor if that comes first; without a LAST block, at
    the next file's first anchor (12.6 Files in a stream: a cut file). The
    diagnosis does not read R-header counts, so a LAST block whose count
    differs from the R-header's ends its file here, unlike verify.py."""
    files = []
    start, cur = 0, [anchors[0]]
    for a in anchors[1:]:
        p = cur[-1]
        if _last(p):
            stop, end = min(p.pos + units_per_block(next(iter(p.fields.values()))), a.pos), "LAST"
        elif _key(a) != _key(p) or a.k < p.k:
            end = "NEXT" if _key(a) != _key(p) else "BACK"
            stop = a.pos
        else:
            cur.append(a)
            continue
        files.append((start, stop, end, cur))
        start, cur = stop, [a]
    p = cur[-1]
    if _last(p):
        files.append((start, min(p.pos + units_per_block(next(iter(p.fields.values()))), n),
                      "LAST", cur))
    else:
        files.append((start, n, "STOP", cur))
    return files


def build_signal(ref_fields, anchors, total=None):
    lead_k = [a.k for a in anchors if any(f.flags & model.F_LEAD_IN for f in a.fields.values())]
    tail_k = [a.k for a in anchors if any(f.flags & model.F_TAIL for f in a.fields.values())]
    last_k = [a.k for a in anchors if any(f.flags & model.F_LAST for f in a.fields.values())]
    lead = max(lead_k) + 1 if lead_k else 0
    if total is None and last_k:
        total = last_k[0] + 1
    body = (min(tail_k) - lead) if tail_k else (total - lead if total else None)
    sig = model.Signal(ref_fields, lead, body, total)
    for a in anchors:
        sig.flag_override[a.k] = next(iter(a.fields.values())).flags
    return sig


def channel_map(anchors, nch):
    m = []
    for c in range(nch):
        votes = Counter(a.fields[c].channel for a in anchors if c in a.fields)
        m.append(votes.most_common(1)[0][0] if votes else c)
    return m


def search_window(stream):
    """+-2 USB High-Speed packets (125 us) of units, at least 8."""
    units_per_s = stream.rate // 8 if stream.dsd else stream.rate
    return max(8, 2 * -(-units_per_s // 8000) + 1)


def gap_match(rx, ref, k):
    """Per-unit match of the gap search (12.2): equal words, or with k (the
    bits a stream uses, below the file depth) the received and expected words
    with the bits below k cleared, as signed integers, within 2 steps of k
    bits on every channel (truncated, rounded or dithered)."""
    if k is None:
        return (rx == ref).all(axis=1)
    m = np.int64(-(1 << (32 - k)))      # the top k bits, as a signed mask
    d = (rx.astype(np.int64) & m) - (ref.astype(np.int64) & m)
    return (np.abs(d) <= 2 << (32 - k)).all(axis=1)


class _Span:
    """Comparison of one stretch of received units against the reference."""

    def __init__(self, ctx, rx0, rx_len, src0, src_len):
        self.ctx, self.rx0, self.rx_len, self.src0, self.src_len = ctx, rx0, rx_len, src0, src_len
        need = abs(src_len - rx_len) if src_len is not None else 0
        self.margin = ctx.S + VERIFY + RUN_WINDOW + need + ctx.L + 8
        hi = src0 + (src_len if src_len is not None else rx_len) + self.margin
        self.lo = src0 - self.margin
        self.ref = ctx.ref_cols(self.lo, hi)
        self.rx = ctx.rx[rx0:rx0 + rx_len]

    def eq(self, j0, j1, off):
        """Per-unit equality of rx[j0:j1] with source units j0+off.. (masked)."""
        s0 = j0 + off + self.src0 - self.lo
        s1 = s0 + (j1 - j0)
        if j0 >= j1 or s0 < 0 or s1 > len(self.ref):
            return np.zeros(max(0, j1 - j0), dtype=bool)
        return self.same(self.rx[j0:j1], self.ref[s0:s1])

    def same(self, a, b):
        """Per-unit match of received units a with expected units b."""
        return gap_match(a, b, self.ctx.k)

    def matches(self, j, off):
        n = min(VERIFY, self.rx_len - j)
        return n > 0 and bool(self.eq(j, j + n, off).all())

    def long_candidates(self, j, off, lo, hi):
        """Offsets d with lo <= |d| <= hi, nearest first and dropped before
        inserted, at which the unit at j (dropped) or the expected unit at j
        (inserted) matches: the candidates of the long search."""
        s0 = j + off + self.src0 - self.lo
        if s0 < 0:
            return []
        n = min(hi, len(self.ref) - 1 - s0)
        drop = set((lo + np.nonzero(self.same(self.rx[j:j + 1], self.ref[s0 + lo:s0 + n + 1]))[0]).tolist())
        n = min(hi, self.rx_len - 1 - j)
        ins = set((lo + np.nonzero(self.same(self.rx[j + lo:j + n + 1], self.ref[s0:s0 + 1]))[0]).tolist())
        return [d for m in sorted(drop | ins) for d in (m, -m)
                if (d > 0 and m in drop) or (d < 0 and m in ins)]


class _Ctx:
    def __init__(self, stream, res, k):
        self.rx = stream.data
        self.res, self.sig, self.chmap, self.k = res, res.sig, res.chmap, k
        self.S = search_window(stream)
        self.U = res.sig.units_per_block
        self.L = min(self.U, LONG_UNITS)
        self.long_ok = True     # False after a failed long search, until a span compares clean

    def ref_cols(self, u0, u1):
        return self.res.ref(u0, u1)


def _classify_inserted(ctx, j_abs, n):
    seg = ctx.rx[j_abs:j_abs + n]
    if ctx.sig.dsd:
        if (seg == 0x69).all() or (seg == 0x96).all():
            return "silence_inserted"
    if (seg == 0).all():
        return "zero_inserted"
    if j_abs >= n and np.array_equal(seg, ctx.rx[j_abs - n:j_abs]):
        return "repeated"
    if j_abs >= 1 and (seg == ctx.rx[j_abs - 1]).all():
        return "repeated"
    return "inserted"


def _analyze_span(ctx, res, rx0, rx_len, src0, src_len):
    """Compare a span; src_len None means open-ended (after the last anchor)."""
    sp = _Span(ctx, rx0, rx_len, src0, src_len)
    n_events = len(res.events)
    target = None if src_len is None else src_len - rx_len
    off = 0
    j = 0
    seg_start, seg_off = 0, 0

    def close(upto):
        if upto > seg_start:
            res.regions.append((rx0 + seg_start, src0 + seg_start + seg_off, upto - seg_start))

    def event(kind, count, jj, o):
        src = src0 + jj + o
        k = src // ctx.U
        res.events.append(Event(kind, count, rx0 + jj, k, ctx.sig.section(k)))

    def shift(d):
        """Accept a re-alignment by d units at j; return True if it fits."""
        nonlocal j, off, seg_start, seg_off
        if d > 0 and sp.matches(j, off + d):
            close(j)
            event("dropped", d, j, off)
            off += d
        elif d < 0 and j - d <= rx_len and sp.matches(j - d, off + d):
            close(j)
            event(_classify_inserted(ctx, rx0 + j, -d), -d, j, off)
            j -= d
            off += d
        else:
            return False
        seg_start, seg_off = j, off
        return True

    while j < rx_len:
        eq = sp.eq(j, rx_len, off)
        bad = np.nonzero(~eq)[0]
        if len(bad) == 0:
            break
        j += int(bad[0])
        # 1. The anchors say units are missing or extra in this span: try that.
        if target is not None and target != off and shift(target - off):
            continue
        # 2. A run of altered (or zeroed) units that ends at the same offset.
        win = eq[j - (rx_len - len(eq)):][:RUN_WINDOW + VERIFY]
        if len(win) >= VERIFY:
            good = np.convolve(win.astype(np.int32), np.ones(VERIFY, dtype=np.int32), "valid")
            ends = np.nonzero(good == VERIFY)[0]
        else:
            falses = np.nonzero(~win)[0]
            ends = [int(falses[-1]) + 1] if win[-1] else []
        if len(ends):
            r = j + int(ends[0])
            run = ctx.rx[rx0 + j:rx0 + r]
            event("zero_filled" if (run == 0).all() else "altered", r - j, j, off)
            j = r
            continue
        # 3. The +-2 packet search for dropped or inserted units.
        if any(shift(d) for s_ in range(1, ctx.S + 1) for d in (s_, -s_)):
            continue
        # 4. The long search, up to a block or LONG_UNITS units (or the net
        # offset the anchors imply, plus S), once per file until a span
        # compares clean.
        if ctx.long_ok:
            reach = max(ctx.L, abs(target - off) + ctx.S if target is not None else 0)
            if any(shift(d) for d in sp.long_candidates(j, off, ctx.S + 1, reach)):
                continue
            ctx.long_ok = False
        # 5. No re-alignment found: the rest of the span is altered.
        event("altered", int((~eq[j - (rx_len - len(eq)):]).sum()), j, off)
        break
    if not res.events[n_events:]:
        ctx.long_ok = True
    close(rx_len)
    if target is not None and off != target:
        event("unlocated", target - off, rx_len - 1, off)


def analyze(stream, total=None, k=None, anchors=None, polarity=1):
    """k: the bits the stream uses, below the file depth, for the gap search."""
    """Run the exact layer. Returns ExactResult, or None without a sync."""
    if anchors is None:
        anchors = find_anchors(stream)
    if not anchors:
        return None
    ref_fields, anchors = file_identity(anchors)
    sig = build_signal(ref_fields, anchors, total)
    if ref_fields.nch != stream.nch:
        return None
    chmap = channel_map(anchors, stream.nch)
    res = ExactResult(sig, anchors, chmap, polarity=polarity)
    ctx = _Ctx(stream, res, k)
    U = ctx.U
    N = len(stream.data)
    first = anchors[0]
    # Units before the first anchor are aligned backwards from it.
    pre = min(first.pos, first.k * U)
    if pre:
        _analyze_span(ctx, res, first.pos - pre, pre, first.k * U - pre, pre)
    for a, b in zip(anchors, anchors[1:]):
        if b.k <= a.k:
            k = a.k
            res.events.append(Event("discontinuity", 0, b.pos, k, sig.section(k)))
            _analyze_span(ctx, res, a.pos, b.pos - a.pos, a.k * U, None)
            continue
        _analyze_span(ctx, res, a.pos, b.pos - a.pos, a.k * U, (b.k - a.k) * U)
    last = anchors[-1]
    if N > last.pos:
        end = N - last.pos
        if sig.total is not None:
            end = min(end, max(0, (sig.total - last.k) * U))
        if end:
            _analyze_span(ctx, res, last.pos, end, last.k * U, None)
    # Body statistics over the aligned regions.
    for rx_s, src_s, n in res.regions:
        for k in range(src_s // U, (src_s + n - 1) // U + 1):
            if sig.section(k) != "body":
                continue
            a = max(src_s, k * U)
            b = min(src_s + n, (k + 1) * U)
            if a >= b:
                continue
            rx = stream.data[rx_s + a - src_s:rx_s + b - src_s]
            ref = res.ref(a, b)
            res.body_units += b - a
            res.body_equal += int((rx == ref).all(axis=1).sum())
            res.body_blocks.add(k)
    return res


def aligned_pairs(stream, res, body_only=True, inverted=True):
    """(rx, ref, pos): received and expected units over the aligned regions,
    and the received index of each. Units whose expected value the checker
    cannot know are left out: the X-sync of a block without an anchor (its
    flags are inferred), and R-header blocks while the block count is unknown.
    With inverted=False, ref is the expected before res's polarity inversion."""
    rxs, refs, poss = [], [], []
    sig = res.sig
    U = sig.units_per_block
    sync = 16 if sig.dsd else 8
    known = {a.k for a in res.anchors}
    for rx_s, src_s, n in res.regions:
        k0, k1 = src_s // U, (src_s + n - 1) // U
        for k in range(k0, k1 + 1):
            if body_only and sig.section(k) != "body":
                continue
            a = max(src_s, k * U)
            b = min(src_s + n, (k + 1) * U)
            if a >= b:
                continue
            keep = np.ones(b - a, dtype=bool)
            off = np.arange(a, b) - k * U
            if k not in known:
                keep &= off >= sync
            if sig.is_rhdr(k) and sig.total is None:
                keep &= off < sync
            pos = np.arange(rx_s + a - src_s, rx_s + b - src_s)
            rxs.append(stream.data[pos[keep]])
            refs.append(res.ref(a, b, inverted)[keep])
            poss.append(pos[keep])
    if not rxs:
        return None, None, None
    return np.concatenate(rxs), np.concatenate(refs), np.concatenate(poss)


def decode_rhdr_exact(stream, sig, anchors, chmap):
    """Read the robust header of an exactly received R-header block by the
    sign of each chip; returns the total block count, or None."""
    U = sig.units_per_block
    for a in anchors:
        if not sig.is_rhdr(a.k):
            continue
        for c in a.fields:
            if sig.dsd:
                L = sig.chip // 8
                p0 = a.pos + sig.r_start // 8 + L // 2
            else:
                L = sig.chip
                p0 = a.pos + sig.r_start + L // 2
            idx = p0 + L * np.arange(model.N_CHIPS)
            if idx[-1] >= len(stream.data):
                continue
            v = stream.data[idx, c]
            if sig.dsd:
                chips = np.where(v == 0xEE, 1, np.where(v == 0x11, -1, 0))
            else:
                chips = np.sign(v.astype(np.int64))
            if (chips[:13] != np.array(model.BARKER13)).any():
                continue
            d = chips[13:].reshape(-1, 2)
            if (d[:, 0] != -d[:, 1]).any() or (d == 0).any():
                continue
            bits = (d[:, 0] > 0).astype(int)
            words = [int("".join(map(str, bits[16 * i:16 * i + 16])), 2) for i in range(7)]
            r = model.parse_rhdr(words)
            if r is not None:
                return r[1]
    return None
