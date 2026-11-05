# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Apple's ALAC reference codec (generator/third_party/alac) through the helper
generator/alac_tool.cpp: exact round trips, among them the step case on which
ffmpeg's ALAC encoder and Apple's decoder disagree and a BPC1 file on which
ffmpeg's decoder and Apple's encoder disagree, the magic cookie, the decoder's
input checks, and determinism. Needs a host C++ compiler; skipped without one."""

import os
import struct
import subprocess
import unittest

import numpy as np

from generator import alac

USABLE = alac.usable()


def step_case():
    """16-bit stereo, 3 x 4096 frames: the left channel is constant, then steps
    by exactly 32768 (-16384 to +16384) at frame 4096 + 1984; the right channel
    is 0. After the run of zero residuals ffmpeg's encoder codes the step as
    the value 0xFFFF; ffmpeg clamps the Rice history on that value plus the
    zero-run modifier, Apple on the value alone, so Apple's decoder reads the
    rest of ffmpeg's packet differently (the reference decoder returns an
    error on it). Apple's own encoding of the case must round-trip."""
    x = np.zeros((3 * 4096, 2), dtype="<i2")
    x[:4096 + 1984, 0] = -16384
    x[4096 + 1984:, 0] = 16384
    return x.tobytes()


def noise(frames, nch, bits, seed):
    rng = np.random.default_rng(seed)
    x = rng.integers(-(1 << (bits - 1)), 1 << (bits - 1), size=(frames, nch), dtype=np.int64)
    x[frames // 3:frames // 2] = 0                       # a run of silence
    x[frames // 2:frames // 2 + 50] = (1 << (bits - 1)) - 1    # full scale
    if bits == 16:
        return x.astype("<i2").tobytes()
    b = x.astype("<i4").view(np.uint8).reshape(-1, 4)
    return b[:, :3].tobytes()


@unittest.skipUnless(USABLE, f"Apple's ALAC codec not usable: {alac.missing()}")
class Codec(unittest.TestCase):
    def roundtrip(self, pcm, rate, nch, bits):
        cookie, packets = alac.encode_pcm(pcm, rate, nch, bits)
        self.assertEqual(alac.decode_packets(packets, cookie), pcm)
        return cookie, packets

    def test_step_case(self):
        pcm = step_case()
        cookie, packets = self.roundtrip(pcm, 44100, 2, 16)
        self.assertEqual(len(packets), 3)

    def test_bpc1_384k_24bit(self):
        """A short BPC1 file at 384 kHz, 24 bits (tests/helpers.py): Apple's
        encoder codes one of its packets (198) with the value that ffmpeg's
        decoder clamps differently, so ffmpeg mis-decodes it from frame
        812 994; Apple's decoder gives the samples back."""
        from generator import spec
        from generator.signal import pcm_blocks
        from tests import helpers as H
        cfg = H.short_config(spec.KIND_PCM, 384000, 24)
        x = pcm_blocks(cfg, 0, cfg.total_blocks)
        pcm = x.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        _, packets = self.roundtrip(pcm, 384000, 2, 24)
        self.assertEqual(len(packets), 224)

    def test_bpc1_705k6_768k(self):
        """Short BPC1 files at 705.6 and 768 kHz, 16 and 24 bits: Apple's
        reference encoder takes these rates (Apple's AudioToolbox decoder
        refuses them), and its decoder gives the samples back exactly. The
        cookie carries the rate, which the 16.16 rate of the sample entry
        cannot."""
        from generator import spec
        from generator.signal import pcm_blocks
        from tests import helpers as H
        for rate in (705600, 768000):
            for bits in (16, 24):
                with self.subTest(rate=rate, bits=bits):
                    cfg = H.short_config(spec.KIND_PCM, rate, bits)
                    x = pcm_blocks(cfg, 0, cfg.total_blocks)
                    pcm = (x.astype("<i2").tobytes() if bits == 16 else
                           x.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes())
                    cookie, packets = self.roundtrip(pcm, rate, 2, bits)
                    self.assertEqual(len(packets), -(-len(x) // 4096))
                    c = alac.parse_cookie(cookie)
                    self.assertEqual((c["sample_rate"], c["bit_depth"]), (rate, bits))

    def test_round_trips(self):
        for bits in (16, 24):
            for nch in (1, 2):
                for frames in (1, 4095, 4096, 2 * 4096 + 777):
                    with self.subTest(bits=bits, nch=nch, frames=frames):
                        pcm = noise(frames, nch, bits, frames + bits + nch)
                        _, packets = self.roundtrip(pcm, 48000, nch, bits)
                        self.assertEqual(len(packets), -(-frames // 4096))

    def test_no_frames(self):
        cookie, packets = alac.encode_pcm(b"", 44100, 2, 16)
        self.assertEqual(packets, [])
        self.assertEqual(alac.parse_cookie(cookie)["avg_bit_rate"], 0)
        self.assertEqual(alac.decode_packets([], cookie), b"")

    def test_cookie(self):
        """The ALACSpecificConfig, with maxFrameBytes the largest packet and
        avgBitRate the bit rate over the whole file. afconvert fills both too;
        its avgBitRate differs by about 0.05 % because it also counts its
        trailing 8-byte packet."""
        for rate, nch, bits in ((44100, 2, 16), (384000, 2, 24), (352800, 1, 24)):
            with self.subTest(rate=rate, nch=nch, bits=bits):
                frames = 3 * 4096 + 100
                cookie, packets = alac.encode_pcm(noise(frames, nch, bits, rate), rate, nch, bits)
                self.assertEqual(len(cookie), 24)
                c = alac.parse_cookie(cookie)
                total = sum(map(len, packets))
                self.assertEqual(c, dict(frame_length=4096, compatible_version=0, bit_depth=bits,
                                         pb=40, mb=10, kb=14, num_channels=nch, max_run=255,
                                         max_frame_bytes=max(map(len, packets)),
                                         avg_bit_rate=total * 8 * rate // frames, sample_rate=rate))

    def test_deterministic(self):
        pcm = noise(5 * 4096, 2, 24, 7)
        self.assertEqual(alac.encode_pcm(pcm, 96000, 2, 24), alac.encode_pcm(pcm, 96000, 2, 24))

    def test_decoder_checks_its_input(self):
        """A packet whose partial-frame count exceeds the cookie's frame length,
        or that does not start with the element for the channel count, is
        refused before Apple's decoder sees it."""
        cookie, packets = alac.encode_pcm(noise(100, 2, 16, 1), 44100, 2, 16)
        p = bytearray(packets[0])
        # tag 1 (CPE), instance 0, 12 unused bits, partial frame 1: the count follows
        self.assertEqual(p[2] & 0x10, 0x10)
        bad = bytearray(p)
        n = 0x10000
        bits = int.from_bytes(bad[:8], "big")
        count_shift = 64 - 23 - 32
        bits = (bits & ~(0xFFFFFFFF << count_shift)) | (n << count_shift)
        bad[:8] = bits.to_bytes(8, "big")
        with self.assertRaisesRegex(RuntimeError, "frames"):
            alac.decode_packets([bytes(bad)], cookie)
        mono = bytearray(p)
        mono[0] &= 0x1F                      # tag 0 (SCE) in a stereo stream
        with self.assertRaisesRegex(RuntimeError, "element"):
            alac.decode_packets([bytes(mono)], cookie)
        with self.assertRaisesRegex(RuntimeError, "cookie"):
            alac.decode_packets(packets, cookie[:20])

    def test_encoder_checks_its_arguments(self):
        with self.assertRaises(RuntimeError):
            alac.encode_pcm(b"\x00" * 6, 44100, 2, 32)
        with self.assertRaises(RuntimeError):
            alac.encode_pcm(b"\x00" * 6, 44100, 3, 16)
        with self.assertRaises(RuntimeError):
            alac.encode_pcm(b"\x00" * 5, 44100, 2, 16)     # not whole frames

    def test_pinned_commit(self):
        """CODEC_COMMIT, written into the files' tags, is the submodule's commit."""
        top = os.path.dirname(alac.CODEC)
        if not os.path.exists(os.path.join(top, ".git")):
            self.skipTest("the submodule is not a git checkout")
        head = subprocess.run(["git", "-C", top, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        self.assertEqual(head, alac.CODEC_COMMIT)


def config(frame_length=4096, nch=2, bits=16, kb=14, version=0):
    """An ALACSpecificConfig with Apple's defaults."""
    return struct.pack(">IBBBBBBHIII", frame_length, version, bits, 40, 10, kb, nch, 255, 0, 0, 44100)


def packet_header(nch, escape=False, shifted=0, count=None):
    """The 23 header bits of a packet's first element, then the partial-frame
    count if given: (value, bit count)."""
    v = (1 if nch == 2 else 0) << 20 | (count is not None) << 3 | shifted << 1 | escape
    return (v << 32 | count, 55) if count is not None else (v, 23)


def compressed(nch, order, count=None, shifted=0, tail=0):
    """A compressed element with predictor order `order` on each channel and
    all-zero coefficients, then `tail` zero bytes (the Rice data)."""
    v, n = packet_header(nch, False, shifted, count)
    v, n = v << 16, n + 16                                   # mixBits, mixRes
    for _ in range(nch):
        v, n = (v << 16 | 0x09 << 8 | 4 << 5 | order) << 16 * order, n + 16 + 16 * order
    pad = -n % 8
    return (v << pad).to_bytes((n + pad) // 8, "big") + bytes(tail)


@unittest.skipUnless(USABLE, f"Apple's ALAC codec not usable: {alac.missing()}")
class DecoderInput(unittest.TestCase):
    """The helper checks the cookie and each packet before Apple's decoder
    sees them, and afterwards that the packet ended where its elements do. In
    the cases below the decoder without these checks overran its buffers
    (AddressSanitizer: frame lengths 16 and 1) or returned success on PCM it
    read from behind the packet (the truncated escape packets)."""

    def refused(self, packets, cookie, why):
        with self.assertRaisesRegex(RuntimeError, why):
            alac.decode_packets(packets, cookie)

    def test_cookie(self):
        _, packets = alac.encode_pcm(noise(100, 2, 16, 1), 44100, 2, 16)
        for cookie, why in ((config(frame_length=16), "frames per packet"),
                            (config(frame_length=31), "frames per packet"),
                            (config(frame_length=16385), "frames per packet"),
                            (config(bits=8), "bit depth"), (config(bits=33), "bit depth"),
                            (config(nch=3), "one or two channels"),
                            (config(kb=0), "Rice limit"), (config(kb=15), "Rice limit"),
                            (config(version=1), "compatible version"),
                            (config()[:23], "too short"),
                            (b"\x00\x00\x00\x0cfrma" + b"alac" + config()[:20], "too short")):
            with self.subTest(cookie=cookie.hex()):
                self.refused(packets, cookie, "bad cookie: " + why)
        # the frma and alac atoms before the config are skipped, as by Apple's decoder
        cookie, _ = alac.encode_pcm(noise(100, 2, 16, 1), 44100, 2, 16)
        wrapped = b"\x00\x00\x00\x0cfrmaalac" + b"\x00\x00\x00\x24alac" + b"\x00" * 4 + cookie
        self.assertEqual(alac.decode_packets(packets, wrapped), noise(100, 2, 16, 1))

    def test_predictor_order_beyond_small_buffers(self):
        """Frame length 16 with order 30, and frame length 1, mono, with order
        4: the predictor writes out[1..order] beyond the decoder's buffers."""
        self.refused([compressed(2, 30, tail=40)], config(frame_length=16), "frames per packet")
        self.refused([compressed(1, 4)], config(frame_length=1, nch=1), "frames per packet")

    def test_predictor_order_against_samples(self):
        for nch in (1, 2):
            with self.subTest(nch=nch):
                self.refused([compressed(nch, 8, count=8, tail=8)], config(nch=nch),
                             "predictor order not below the sample count")

    def test_header_fields(self):
        self.refused([compressed(2, 4, count=0, tail=8)], config(), "partial-frame count 0")
        self.refused([compressed(1, 4, shifted=2, tail=8)], config(nch=1), "bytes shifted")
        self.refused([compressed(2, 4, shifted=0, tail=8)], config(bits=32), "bytes shifted")
        v, n = packet_header(2, escape=True, shifted=1)
        self.refused([(v << 1).to_bytes(3, "big") + bytes(4096 * 4)], config(),
                     "escape packet with shifted bytes")
        p = bytearray(compressed(2, 4, tail=8))
        p[1] |= 0x01                                     # an unused header bit
        self.refused([bytes(p)], config(), "unused bits")

    def test_truncated_escape_packet(self):
        """The header of an escape packet without its PCM ("20 00 02"), and
        a real escape packet one byte short."""
        self.refused([bytes.fromhex("200002")], config(), "escape packet shorter than its samples")
        pcm = noise(4096, 2, 16, 3)
        cookie, packets = alac.encode_pcm(pcm, 44100, 2, 16)
        self.assertEqual(packets[0][2] & 0x02, 0x02)       # escaped: noise does not compress
        self.assertEqual(len(packets[0]), (23 + 4096 * 32 + 3 + 6) // 8)
        self.assertEqual(alac.decode_packets(packets, cookie), pcm)
        self.refused([packets[0][:-1]], cookie, "packet ends before its END element")
        self.refused([packets[0][:-3]], cookie, "escape packet shorter than its samples")

    def test_packet_length(self):
        """A compressed packet one byte short (the Rice decoder runs past its
        end, or the END element is cut), or one byte long, is refused."""
        pcm = noise(3 * 4096, 2, 24, 4)[:3 * 4096 * 6]
        tone = (np.sin(np.arange(3 * 4096) / 9.0)[:, None] * 10000 * [1, 1]).astype("<i2").tobytes()
        cookie, packets = alac.encode_pcm(tone, 44100, 2, 16)
        self.assertEqual(packets[0][2] & 0x02, 0)          # compressed
        short = "Decode failed|packet ends before its END element"
        self.refused([packets[0][:-1]], cookie, short)
        self.refused([packets[0] + b"\x00"], cookie, "packet longer than its elements")
        cookie, packets = alac.encode_pcm(pcm, 48000, 2, 24)
        self.refused([packets[1][:-1]], cookie, short)


class Cookie(unittest.TestCase):
    def test_parse(self):
        b = struct.pack(">IBBBBBBHIII", 4096, 0, 24, 40, 10, 14, 2, 255, 17719, 12418987, 384000)
        self.assertEqual(alac.parse_cookie(b)["avg_bit_rate"], 12418987)
        self.assertEqual(alac.parse_cookie(b"\x00\x00\x00\x0cfrma" + b"alac" + b"\x00\x00\x00\x24alac"
                                           + b"\x00" * 4 + b)["sample_rate"], 384000)


if __name__ == "__main__":
    unittest.main()
