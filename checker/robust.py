"""Robust layer: find and decode the robust headers (R-headers).

The decoder works on a real-valued signal x (full scale = 1.0): PCM samples,
or for a DSD stream the bit density of each 16-bit DoP word. It needs no exact
samples, so it survives gain, dither, truncation, polarity and resampling.

1. Matched filter for the Barker-13 sync at a chip length L (in received
   samples), normalised by the signal energy under the window. Box sums come
   from a running sum, so the cost is 13 operations per sample and L.
2. At each peak above 0.7, integrate the middle half of each of the 237 chips,
   decode Manchester bits by comparing the two chips of each bit, check the
   CRC-16.
"""

from dataclasses import dataclass

import numpy as np

from . import model

THRESHOLD = 0.7
GAIN_DB_MIN = 0.1        # R-header level changes below this are not reported
ERASURE = 0.5            # a chip pair differing by less than this x 2 x level is erased
POP16 = np.array([bin(i).count("1") for i in range(1 << 16)], dtype=np.int8)


@dataclass
class RHeader:
    channel: int          # received channel
    pos: float            # received sample index of the Barker start
    chip: float           # chip length in received samples
    polarity: int         # +1, or -1 if inverted
    level: float          # mean chip amplitude, full scale = 1.0
    fields: model.Fields
    total: int
    mixed: tuple = ()     # source channels, if the header is a mix of several

    @property
    def src_rate(self):
        return model.RATES[self.fields.rate_code]


def signal_of(stream):
    """(x, rate): float signal (samples, nch) the decoder runs on."""
    if stream.dsd:
        b = stream.data
        n = len(b) // 2
        w = (b[0:2 * n:2].astype(np.int32) << 8) | b[1:2 * n:2]
        x = (2.0 * POP16[w] - 16.0) / 16.0
        return x, stream.rate // 16
    return stream.data.astype(np.float64) / 2.0 ** 31, stream.rate


def barker_rho(x, L):
    """Normalised Barker-13 correlation at every start index."""
    e = np.rint(np.arange(14) * L).astype(np.int64)
    n = len(x) - e[13]
    if n <= 0:
        return np.zeros(0)
    C = np.concatenate(([0.0], np.cumsum(x)))
    C2 = np.concatenate(([0.0], np.cumsum(x * x)))
    t = np.arange(n)
    corr = np.zeros(n)
    for j, s in enumerate(model.BARKER13):
        corr += s * (C[t + e[j + 1]] - C[t + e[j]])
    energy = C2[t + e[13]] - C2[t]
    # Running sums lose precision where the signal is nearly silent; below
    # -120 dBFS RMS the window counts as silence (rho = 0).
    floor = e[13] * 1e-12
    rho = corr / np.sqrt(np.maximum(energy, floor) * e[13])
    rho[energy < floor] = 0.0
    return rho


def _peaks(rho, L):
    idx = np.nonzero(np.abs(rho) > THRESHOLD)[0]
    if len(idx) == 0:
        return []
    groups = np.split(idx, np.nonzero(np.diff(idx) > 13 * L)[0] + 1)
    return [int(g[np.argmax(np.abs(rho[g]))]) for g in groups]


def _words(bits):
    return [int("".join(map(str, bits[16 * k:16 * k + 16])), 2) for k in range(7)]


def decode_at(C, t0, L, pol):
    """Decode the chips at start t0 with chip length L; returns
    (Fields, total, level, mixed) or None.

    A bit whose two chips are nearly equal is an erasure. Erasures only in the
    channel index and the CRC mean the received channel is a mix of several
    source channels (a downmix): every channel index whose CRC agrees with the
    bits that were received is returned in `mixed`."""
    i = np.arange(model.N_CHIPS)
    a = np.rint(t0 + (i + 0.25) * L).astype(np.int64)
    b = np.maximum(np.rint(t0 + (i + 0.75) * L).astype(np.int64), a + 1)
    if a[0] < 0 or b[-1] >= len(C):
        return None
    v = pol * (C[b] - C[a]) / (b - a)
    if (np.sign(v[:13]) != np.array(model.BARKER13)).any():
        return None
    lvl = float(np.mean(np.abs(v[:13])))
    d = v[13:].reshape(-1, 2)
    diff = d[:, 0] - d[:, 1]
    bits = (diff > 0).astype(int)
    erased = np.abs(diff) < ERASURE * 2 * lvl
    mixed = ()
    if not erased.any():
        r = model.parse_rhdr(_words(bits))
    else:
        # bit index of the channel nibble (r1 bits 15..12) and of r6
        allowed = np.zeros(112, dtype=bool)
        allowed[16:20] = True
        allowed[96:112] = True
        if (erased & ~allowed).any():
            return None
        r = None
        cands = []
        for c in range(16):
            bb = bits.copy()
            bb[16:20] = [(c >> s) & 1 for s in (3, 2, 1, 0)]
            if (erased[16:20] == False).all() and (bb[16:20] != bits[16:20]).any():
                continue
            w = _words(bb)
            crc = model.crc16_words(w[:6])
            want = np.array([(crc >> s) & 1 for s in range(15, -1, -1)])
            ok = erased[96:112] | (want == bits[96:112])
            if ok.all():
                bb[96:112] = want
                res = model.parse_rhdr(_words(bb))
                if res and res[0].channel < res[0].nch:
                    cands.append((c, res))
        if len(cands) < 2:
            return None
        mixed = tuple(c for c, _ in cands)
        r = cands[0][1]
    if r is None:
        return None
    sb = np.where(erased, 0, np.where(bits > 0, 1, -1))
    chips = np.concatenate((np.array(model.BARKER13), np.stack([sb, -sb], 1).ravel()))
    used = chips != 0
    level = float(np.mean((v * chips)[used]))
    return r[0], r[1], level, mixed


def chip_candidates(rate, native_too=False):
    """Chip lengths in received samples: time-preserving (resampled or native)
    for both rate families, and optionally the native lengths of every rate
    (content played at the wrong rate)."""
    c = [11 * rate / 44100, 12 * rate / 48000]
    if native_too:
        for r in model.RATES[:10]:
            base, m = model.rate_family(r)
            c.append((11 if base == 44100 else 12) * m)
    out = []
    for L in c:
        if L >= 4 and all(abs(L - o) > 1e-9 for o in out):
            out.append(L)
    return out


def expected_chip(src_rate, rx_rate):
    """Chip length in received samples if the time base is preserved."""
    base, _ = model.rate_family(src_rate)
    return (11 if base == 44100 else 12) * rx_rate / base


def _scan_with(x, rate, cands):
    found = []
    chunk = 1 << 22
    for L0 in cands:
        span = int(model.N_CHIPS * L0 * 1.05) + 64
        for c in range(x.shape[1]):
            for s0 in range(0, len(x), chunk):
                seg = x[s0:s0 + chunk + span, c]
                last = s0 + chunk >= len(x)
                rho = barker_rho(seg, L0)
                C = np.concatenate(([0.0], np.cumsum(seg)))
                for p in _peaks(rho, L0):
                    if p >= chunk and not last:
                        continue        # decoded with the next chunk
                    pol = 1 if rho[p] > 0 else -1
                    for dt in (0, -1, 1):
                        r = decode_at(C, p + dt, L0, pol)
                        if r:
                            found.append(RHeader(c, float(s0 + p + dt), L0, pol,
                                                 r[2], r[0], r[1], r[3]))
                            break
    # The same header may decode at both family chip lengths; keep the one
    # whose length fits its own source rate (its peak position is exact).
    found.sort(key=lambda h: (h.channel, h.pos))
    out = []
    for h in found:
        dup = next((o for o in out if o.channel == h.channel
                    and abs(o.pos - h.pos) < 13 * h.chip), None)
        if dup is None:
            out.append(h)
            continue
        want = expected_chip(h.src_rate, rate)
        if abs(h.chip - want) < abs(dup.chip - want):
            out[out.index(dup)] = h
    return out


def scan(stream, max_seconds=None):
    """Find and decode R-headers in every channel. Returns a list of RHeader.

    Chip lengths tried: the two time-preserving ones first (native playback or
    resampling), then the native lengths of every rate (content played at the
    wrong rate without resampling)."""
    x, rate = signal_of(stream)
    if max_seconds is not None:
        x = x[:int(max_seconds * rate)]
    found = _scan_with(x, rate, chip_candidates(rate))
    if not found:
        more = [L for L in chip_candidates(rate, native_too=True)
                if L not in chip_candidates(rate)]
        found = _scan_with(x, rate, more)
    return found


def rate_ratio(headers):
    """Received samples per source unit (frame or DSD bit), from the spacing
    of R-headers in the same channel; None with fewer than two."""
    best = None
    for c in {h.channel for h in headers}:
        hs = [h for h in headers if h.channel == c]
        for a, b in zip(hs, hs[1:]):
            dk = b.fields.block - a.fields.block
            if dk <= 0:
                continue
            base, m = model.rate_family(a.src_rate)
            src_units = dk * 4096 * m
            r = (b.pos - a.pos) / src_units
            if best is None or dk > best[1]:
                best = (r, dk)
    return best[0] if best else None
