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


class Cookie(unittest.TestCase):
    def test_parse(self):
        b = struct.pack(">IBBBBBBHIII", 4096, 0, 24, 40, 10, 14, 2, 255, 17719, 12418987, 384000)
        self.assertEqual(alac.parse_cookie(b)["avg_bit_rate"], 12418987)
        self.assertEqual(alac.parse_cookie(b"\x00\x00\x00\x0cfrma" + b"alac" + b"\x00\x00\x00\x24alac"
                                           + b"\x00" * 4 + b)["sample_rate"], 384000)


if __name__ == "__main__":
    unittest.main()
