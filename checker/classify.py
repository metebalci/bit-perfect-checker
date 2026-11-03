"""Name the alteration of aligned PCM samples.

Input: received and expected 32-bit left-justified words over the body,
already in source-channel order. Hypotheses are tested in a fixed order; exact
ones (a deterministic function of the expected samples) before statistical
ones. The first that holds is the reason given.
"""

import numpy as np

GAIN_DB_MIN = 0.005       # smaller gain deviations count as unity
STAT_FRACTION = 0.999     # share of samples a statistical hypothesis must fit

I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1


def effective_bits(w):
    """Bits from the MSB down to the lowest bit set in any word (0 if silent)."""
    acc = int(np.bitwise_or.reduce(np.asarray(w).astype(np.int32).view(np.uint32).ravel())) if np.size(w) else 0
    if acc == 0:
        return 0
    low = (acc & -acc).bit_length() - 1
    return 32 - low


def float32_path(ref):
    """int32 -> float32 -> int32 with round-to-nearest-even and clamping."""
    f = ref.astype(np.float32).astype(np.float64)
    return np.clip(f, I32_MIN, I32_MAX).astype(np.int64)


def negate(ref, depth=32):
    """Polarity inversion of D-bit samples, clamped at the D-bit maximum."""
    return np.minimum(-ref.astype(np.int64), I32_MAX - (1 << (32 - depth)) + 1)


def finding(code, text, **kw):
    d = {"code": code, "text": text}
    d.update(kw)
    return d


# The order in which a report lists its findings (12.5): the deciding reason
# first. RATE_MISMATCH follows polarity and LEVEL goes with GAIN; a code not
# listed here is an error.
ORDER = (
    "DSD_TO_PCM", "RESAMPLED",
    "CHANNEL_MAP", "DOWNMIX",
    "POLARITY",
    "RATE_MISMATCH",
    "GAIN", "LEVEL",
    "FLOAT32",
    "TRUNCATED", "ROUNDED", "DITHERED",
    "BIT_ERRORS",
    "LOW_BIT_NOISE", "LSB_NOISE",
    "DSD_PROCESSED",
    "UNKNOWN",
)


def in_order(findings):
    """The findings sorted into ORDER (stable for equal codes)."""
    return sorted(findings, key=lambda x: ORDER.index(x["code"]))


FIT_LIMIT = 1 << 29        # fit gains on samples below a quarter of full scale


def mixing_matrix(rx, ref):
    """Least-squares G with rx ~ ref @ G (columns: received channels), fitted
    on frames whose expected samples stay below a quarter of full scale, so
    that clipped bursts do not bias it."""
    sel = np.all(np.abs(ref) < FIT_LIMIT, axis=1)
    if sel.sum() < 1000:
        sel = np.ones(len(ref), dtype=bool)
    a = ref[sel].astype(np.float64)
    b = rx[sel].astype(np.float64)
    G, *_ = np.linalg.lstsq(a, b, rcond=None)
    return G


def quantized(ref, k):
    """ref truncated and rounded to k bits (rounding clamped at the k-bit maximum)."""
    q = 1 << (32 - k)
    return ref & ~(q - 1), np.minimum((ref + q // 2) & ~(q - 1), I32_MAX & ~(q - 1))


def quantized_before_inversion(rx, ref0, depth):
    """TRUNCATED or ROUNDED to k before a polarity inversion: rx is ref0
    quantised to k bits, then inverted at the file depth. A quantised -2^31
    inverts to the largest positive value at the depth or at k bits; k is
    counted without the received samples at the depth's largest value."""
    top = I32_MAX - (1 << (32 - depth)) + 1
    k = effective_bits(rx[rx != top])
    if not 0 < k < depth:
        return None
    top_k = I32_MAX - (1 << (32 - k)) + 1
    for code, verb, x in zip(("TRUNCATED", "ROUNDED"), ("truncated", "rounded"),
                             quantized(ref0, k)):
        if np.all((rx == negate(x, depth)) | ((x == I32_MIN) & (rx == top_k))):
            return finding(code, f"{verb} to {k} bits before the polarity inversion", bits=k)
    return None


def classify(rx, ref, depth, polarity=1):
    """Return a list of findings (empty if rx == ref). With polarity -1, ref is
    the expected before an inversion the caller has found and reported: rx is
    compared against its inversion."""
    rx = rx.astype(np.int64)
    ref = ref.astype(np.int64)
    ref0 = None           # the expected before a polarity inversion, if one holds
    if polarity < 0:
        ref0, ref = ref, negate(ref, depth)
    if np.array_equal(rx, ref):
        return []
    out = []
    nch = rx.shape[1]
    G = mixing_matrix(rx, ref)
    # Downmix: every received channel carries the same mix of all sources.
    if nch > 1 and np.allclose(G, G[:, :1], atol=0.02) and np.all(np.abs(G) > 0.1):
        g = G.mean()
        return [finding("DOWNMIX", f"channels downmixed (each output = "
                        f"{g:.3f} x sum of the {nch} channels)")]
    perm = list(np.argmax(np.abs(G), axis=0))
    if sorted(perm) == list(range(nch)) and perm != list(range(nch)):
        out.append(finding("CHANNEL_MAP", "channels swapped" if nch == 2 else
                           f"channels reordered {perm}", order=[int(p) for p in perm]))
        ref = ref[:, perm]
        if ref0 is not None:
            ref0 = ref0[:, perm]
        G = G[perm, :]
    g = np.array([G[c, c] for c in range(nch)])
    if np.all(g < 0):
        out.append(finding("POLARITY", "polarity inverted"))
        ref0, ref = ref, negate(ref, depth)
        g = -g
    gain_db = 20 * np.log10(np.maximum(np.abs(g), 1e-12))
    if np.max(np.abs(gain_db)) >= GAIN_DB_MIN:
        if np.ptp(gain_db) > 0.01:
            txt = "volume changed (" + ", ".join(f"{x:+.2f} dB" for x in gain_db) + ")"
        else:
            txt = f"volume changed ({gain_db.mean():+.2f} dB)"
        clipped = int(np.sum((np.abs(rx) >= I32_MAX - (1 << (32 - depth)))
                             & (np.abs(ref * g[None, :]) > I32_MAX)))
        if clipped:
            txt += f", {clipped} samples clipped"
        out.append(finding("GAIN", txt, gain_db=[round(float(x), 3) for x in gain_db],
                           clipped=clipped))
        return out
    if np.array_equal(rx, ref):
        return out
    k = effective_bits(rx)
    if depth == 32 and np.array_equal(rx, float32_path(ref)):
        out.append(finding("FLOAT32", "32-bit samples passed through a 32-bit float "
                           "path (24-bit mantissa: low bits of the full-scale "
                           "bursts lost)"))
        return out
    if 0 < k < depth:
        truncated, rounded = quantized(ref, k)
        if np.array_equal(rx, truncated):
            out.append(finding("TRUNCATED", f"truncated to {k} bits", bits=k))
            return out
        if np.array_equal(rx, rounded):
            out.append(finding("ROUNDED", f"rounded to {k} bits", bits=k))
            return out
    if ref0 is not None:
        f = quantized_before_inversion(rx, ref0, depth)
        if f is not None:
            out.append(f)
            return out
    if 0 < k < depth:
        q = 1 << (32 - k)
        e = (rx - ref) / q
        if np.mean(np.abs(e) <= 2.0) >= STAT_FRACTION:
            rms = float(np.sqrt(np.mean(e * e)))
            out.append(finding("DITHERED", f"dithered to {k} bits (error RMS "
                               f"{rms:.2f} LSB)", bits=k, rms_lsb=round(rms, 3)))
            return out
    bad = int(np.sum(np.any(rx != ref, axis=1)))
    if bad < 0.01 * len(rx):
        out.append(finding("BIT_ERRORS", f"{bad} frames differ", frames=bad))
        return out
    lsb = 1 << (32 - depth)
    e = (rx - ref) / lsb
    if np.mean(np.abs(e) <= 2.0) >= STAT_FRACTION:
        if k > depth:
            out.append(finding("LOW_BIT_NOISE", f"noise or dither added below the "
                               f"file's {depth} bits (now {k} bits used)", bits=k))
        else:
            rms = float(np.sqrt(np.mean(e * e)))
            out.append(finding("LSB_NOISE", f"noise or dither at the {depth}-bit "
                               f"LSB (error RMS {rms:.2f} LSB)", rms_lsb=round(rms, 3)))
        return out
    out.append(finding("UNKNOWN", "unknown processing"))
    return out
