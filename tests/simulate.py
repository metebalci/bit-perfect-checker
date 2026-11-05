# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Simulated playback-chain alterations for the tests (numpy only).

All functions take and return 32-bit left-justified words W (int32 arrays,
frames x channels), the representation the checker works on.
"""

import numpy as np

I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1


def quantize(x, bits, rng=None, dither=False):
    """Float (full scale 1.0) -> W at `bits`, rounding, optional TPDF dither."""
    q = 2.0 ** (32 - bits)
    y = x * 2.0 ** 31 / q
    if dither:
        y = y + rng.random(y.shape) - rng.random(y.shape)
    y = np.floor(y + 0.5) * q
    return np.clip(y, I32_MIN, I32_MAX - q + 1).astype(np.int64).astype(np.int32)


def truncate(w, bits):
    return (w & np.int32(np.uint32((0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF).view(np.int32))).astype(np.int32)


def tpdf_dither(w, bits, seed=1):
    rng = np.random.default_rng(seed)
    return quantize(w.astype(np.float64) / 2.0 ** 31, bits, rng, dither=True)


def gain(w, db, bits):
    return quantize(w.astype(np.float64) / 2.0 ** 31 * 10 ** (db / 20), bits)


def swap(w):
    return np.ascontiguousarray(w[:, ::-1])


def invert(w, bits):
    """Polarity inversion at `bits`, clamping the most negative value to the
    largest positive one."""
    return np.minimum(-w.astype(np.int64), I32_MAX - (1 << (32 - bits)) + 1).astype(np.int32)


def drop(w, pos, n=1):
    return np.delete(w, np.s_[pos:pos + n], axis=0)


def repeat(w, pos):
    """The frame before `pos` is played twice."""
    return np.insert(w, pos, w[pos - 1], axis=0)


def zero(w, pos, n):
    w = w.copy()
    w[pos:pos + n] = 0
    return w


def float32_roundtrip(w):
    f = (w.astype(np.float64) / 2.0 ** 31).astype(np.float32)
    return np.clip(np.rint(f.astype(np.float64) * 2.0 ** 31), I32_MIN, I32_MAX).astype(np.int32)


def resample(w, src, dst, bits=24):
    """Band-limited (FFT) resampling src -> dst Hz, re-quantised to `bits`."""
    from math import gcd
    g = gcd(src, dst)
    p, q = dst // g, src // g
    n = (len(w) // q) * q
    x = w[:n].astype(np.float64) / 2.0 ** 31
    m = n * p // q
    X = np.fft.rfft(x, axis=0)
    Y = np.zeros((m // 2 + 1, x.shape[1]), dtype=complex)
    k = min(len(X), len(Y))
    Y[:k] = X[:k]
    if m < n:
        Y[-1] = Y[-1].real          # new Nyquist bin
    y = np.fft.irfft(Y, m, axis=0) * (m / n)
    return quantize(y, bits)


def dsd_to_pcm(bits, factor=32, out_bits=24, level=0.5):
    """DSD bits (nch, nbits) -> PCM: box filter over `factor` bits, then a
    band-limiting FFT low-pass at 0.45 of the output rate, scaled by `level`
    (most converters map 50% DSD modulation to about -6 dBFS)."""
    nch, nb = bits.shape
    n = nb // factor
    s = bits[:, :n * factor].reshape(nch, n, factor).sum(axis=2)
    x = (2.0 * s - factor) / factor
    X = np.fft.rfft(x, axis=1)
    X[:, int(len(X[0]) * 0.9):] = 0
    y = np.fft.irfft(X, n, axis=1).T * level
    return quantize(y, out_bits)
