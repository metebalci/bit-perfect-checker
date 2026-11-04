"""The gapless set (docs/bpc1.md, 11.7): the cut, the part files and their
tags, and what checkers give for a pair joined, changed at the cut, or
played as two streams. checker/verify.py and the firmware's engine, through
its host tool (tests/test_bpc1_checker.py), must give the same results. The
set is generated once, through the command line, and once more for the
repeatability test."""

import filecmp
import io
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout

import numpy as np

from checker import readers, verify
from generator import containers, fileset, package as P, spec
from generator.signal import dop_frames, dsd_blocks, pcm_blocks

from tests import test_bpc1_checker as TC
from tests.test_repeatability import ROOT, tree

FORMATS = {"wav": ".wav", "aiff": ".aiff", "flac": ".flac", "alac": ".m4a"}
PCM_PAIRS = ("bpc1_pcm_044k1_16bit", "bpc1_pcm_044k1_24bit", "bpc1_pcm_048k_16bit", "bpc1_pcm_048k_24bit")
DSD_PAIRS = ("bpc1_dsd064_dsf", "bpc1_dsd064_dff")
# The cut as the plan and 11.7 give it: J, its block and its offset in the block
CUTS = {44100: (330751, 80, 3071), 48000: (360001, 87, 3649), 2822400: (21168016, 80, 196496)}
DOP_FRAMES = 1323001                     # DSD64: J / 16
SECOND_PART_DIFF = {44100: "7.523", 48000: "7.509", 2822400: "7.523"}

_gen = {}


def generate(out):
    subprocess.run([sys.executable, "-m", "generator", "--gapless", "--out", out, "--jobs", "12"],
                   cwd=ROOT, check=True, capture_output=True)


def generated():
    """The set, generated on first use by the classes that read it."""
    if "out" not in _gen:
        _gen["tmp"] = tempfile.TemporaryDirectory(prefix="bpc1-gapless-")
        generate(os.path.join(_gen["tmp"].name, "a"))
        _gen["out"] = os.path.join(_gen["tmp"].name, "a")
    return _gen["out"]


def tearDownModule():
    if "tmp" in _gen:
        _gen["tmp"].cleanup()


def label(f):
    """A pair's label: its regular file's name, with the container for DSD
    (bpc1_dsd064_dsf), whose names do not give it."""
    base = f.name.split("_gapless_part")[0]
    return f"{base}_{f.container}" if f.container in ("dsf", "dff") else base


def pairs(fault=""):
    """{label: (part 1, part 2)} of the gapless set's pairs, or of its
    negative pairs (fault "gap")."""
    out = {}
    for f in fileset.gapless_set():
        if f.fault == fault:
            out.setdefault(label(f), []).append(f)
    return out


def path(f, fmt=None):
    rel = f.relpath if fmt in (None, "wav", "dsf", "dff") else f.relpath_as(fmt)
    return os.path.join(_gen["out"], *rel.split("/"))


def regular(name):
    """The regular file of a pair: its spec and its units (PCM words W, or
    DSD bytes, earliest bit in the MSB)."""
    base = {label(f): f for f in fileset.test_set() if not f.fault}[name]
    cfg = base.cfg
    if cfg.dsd:
        return base, np.ascontiguousarray(np.packbits(dsd_blocks(cfg, 0, cfg.total_blocks), axis=1).T)
    s = pcm_blocks(cfg, 0, cfg.total_blocks).astype(np.int64)
    return base, (s << (32 - cfg.depth)).astype(np.int32)


def dop(data, first):
    """DoP words, left-justified in 32 bits, of DSD bytes (bytes, nch) from
    DoP frame `first` on."""
    d = dop_frames(np.unpackbits(np.ascontiguousarray(data.T), axis=1), first)
    return (d.astype(np.uint32) << np.uint32(8)).view(np.int32)


def tags_wav(p):
    with open(p, "rb") as f:
        info = dict(containers.wav_chunks(f.read()))[b"LIST"]
    out, pos = {}, 4
    while pos < len(info):
        cid, size = info[pos:pos + 4].decode(), struct.unpack_from("<I", info, pos + 4)[0]
        out[cid] = info[pos + 8:pos + 8 + size].rstrip(b"\0").decode("ascii")
        pos += 8 + size + (size & 1)
    return out


def tags_flac(p):
    """The Vorbis comments of a FLAC file, and its STREAMINFO max block size
    and total samples."""
    with open(p, "rb") as f:
        data = f.read(1 << 16)
    pos, tags, info = 4, {}, None
    while True:
        head = data[pos]
        size = int.from_bytes(data[pos + 1:pos + 4], "big")
        body = data[pos + 4:pos + 4 + size]
        if head & 0x7F == 0:
            info = (struct.unpack_from(">H", body, 2)[0], int.from_bytes(body[10:18], "big") & ((1 << 36) - 1))
        elif head & 0x7F == 4:
            q = 4 + struct.unpack_from("<I", body)[0]
            n = struct.unpack_from("<I", body, q)[0]
            q += 4
            for _ in range(n):
                m = struct.unpack_from("<I", body, q)[0]
                k, v = body[q + 4:q + 4 + m].decode("utf-8").split("=", 1)
                tags[k] = v
                q += 4 + m
        pos += 4 + size
        if head & 0x80:
            return tags, info


def tags_id3(tag):
    """The text frames of an ID3v2.3 tag (COMM: its text)."""
    assert tag[:5] == b"ID3\x03\x00", tag[:5]
    size = sum(b << (7 * (3 - i)) for i, b in enumerate(tag[6:10]))
    out, pos = {}, 10
    while pos < 10 + size:
        fid, n = tag[pos:pos + 4].decode(), struct.unpack_from(">I", tag, pos + 4)[0]
        body = tag[pos + 10:pos + 10 + n]
        text = body[4:].split(b"\0", 1)[1] if fid == "COMM" else body[1:]   # COMM: language, description
        out[fid] = text.decode("ascii")
        pos += 10 + n
    return out


def dsf_parts(p):
    """(sample count, block size, data bytes per channel (blocks, 4096), ID3 tag) of a DSF file."""
    with open(p, "rb") as f:
        data = f.read()
    total, meta = struct.unpack_from("<QQ", data, 12)
    _, _, _, nch, _, _, count, bsize, _ = struct.unpack_from("<IIIIIIQII", data, 40)
    assert data[80:84] == b"data" and total == len(data)
    size = struct.unpack_from("<Q", data, 84)[0] - 12
    body = np.frombuffer(data, np.uint8, size, 92).reshape(-1, nch, bsize).transpose(1, 0, 2)
    assert 92 + size == meta
    return count, bsize, body, data[meta:]


def dff_chunks(p):
    with open(p, "rb") as f:
        data = f.read()
    out, pos = {}, 16
    while pos < len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from(">Q", data, pos + 4)[0]
        out[cid] = data[pos + 12:pos + 12 + size]
        pos += 12 + size + (size & 1)
    return out


def aiff_chunk_ids(p):
    with open(p, "rb") as f:
        data = f.read()
    ids, pos = [], 12
    while pos < len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from(">I", data, pos + 4)[0]
        ids.append(cid)
        pos += 8 + size + (size & 1)
    assert pos == len(data) == 8 + struct.unpack_from(">I", data, 4)[0]
    return ids


class Cut(unittest.TestCase):
    """T1: where J lies, from the code."""

    def test_rule(self):
        self.assertEqual(len(fileset.gapless_set()), 14)
        self.assertEqual(len(pairs()), 6)
        for name, (p1, p2) in pairs().items():
            cfg = p1.cfg
            with self.subTest(name):
                self.assertEqual(p2.cfg, cfg)
                self.assertEqual((p1.part, p2.part), (1, 2))
                J = fileset.gapless_cut(cfg)
                B = cfg.block_len
                k, off = divmod(J, B)
                self.assertEqual((J, k, off), CUTS[cfg.rate])
                self.assertEqual((p1.units, p2.units), ((0, J), (J, cfg.total_units)))
                # in the payload of a body block that is not an R-header block
                self.assertTrue(cfg.lead_blocks <= k < cfg.lead_blocks + cfg.body_blocks)
                self.assertFalse(spec.is_rhdr_block(k))
                self.assertGreaterEqual(off, spec.SYNC_BITS if cfg.dsd else spec.PAYLOAD_START)
                # on no block, 4096-frame packet, DSF block, 2^k or 6-frame boundary
                n = J // 16 if cfg.dsd else J
                if cfg.dsd:
                    self.assertEqual((J % 16, n), (0, DOP_FRAMES))
                    self.assertNotEqual((J // 8) % containers.DSF_BLOCK, 0)
                self.assertEqual(n % 2, 1)
                self.assertEqual(math.gcd(n, 210), 1)
                self.assertNotEqual(n % 4096, 0)
                # 7.5 s and one unit
                self.assertEqual(J, (cfg.rate * 15 // 2 // (16 if cfg.dsd else 1) + 1) * (16 if cfg.dsd else 1))

    def test_units_at_the_cut(self):
        """The units at J are not silence and differ from their neighbours in
        every channel: frames for PCM, DSD bytes and DoP frames for DSD."""
        for name, (p1, _) in pairs().items():
            cfg = p1.cfg
            J = fileset.gapless_cut(cfg)
            k = J // cfg.block_len
            with self.subTest(name):
                if cfg.dsd:
                    by = np.packbits(dsd_blocks(cfg, k, k + 1), axis=1).T
                    i = (J - k * cfg.block_len) // 8
                    for c in range(cfg.nch):
                        self.assertNotIn(by[i, c], (0x00, 0x69, 0x96))
                        self.assertNotIn(by[i, c], (by[i - 1, c], by[i + 1, c]))
                    fr = by[::2].astype(np.uint32) << 8 | by[1::2]
                    j = i // 2
                    self.assertTrue((fr[j] != fr[j - 1]).all() and (fr[j] != fr[j + 1]).all())
                    self.assertEqual(by[i].tolist(), [0x95, 0xA9])     # both DSD pairs: one kind
                else:
                    s = pcm_blocks(cfg, k, k + 1)
                    i = J - k * cfg.block_len
                    self.assertTrue((s[i] != 0).all())
                    self.assertTrue((s[i] != s[i - 1]).all() and (s[i] != s[i + 1]).all())


class Files(unittest.TestCase):
    """T2 and T7: the part files decode to the regular file's units; the DSF
    parts' counts and padding; the tags of 11.7."""

    @classmethod
    def setUpClass(cls):
        generated()

    def test_names(self):
        names = tree(_gen["out"])
        self.assertEqual(len(names), 39)       # 36 files, the negative pair and SHA256SUMS
        self.assertIn("SHA256SUMS", names)
        for fmt in ("wav", "aiff", "flac", "alac", "dsf", "dff"):
            self.assertEqual(sum(n.startswith(fmt + "/") for n in names), 8 if fmt in FORMATS else 2, fmt)
        self.assertIn("flac/20130-bpc1_pcm_044k1_24bit_gapless_part1.flac", names)
        self.assertIn("alac/20303-bpc1_pcm_048k_16bit_gapless_part2.m4a", names)
        self.assertIn("dsf/21401-bpc1_dsd064_gapless_part2.dsf", names)
        self.assertIn("dff/21500-bpc1_dsd064_gapless_part1.dff", names)
        self.assertEqual([n for n in names if n.startswith("negative/")],
                         ["negative/90003-bpc1_pcm_044k1_16bit_gapless_part1_gap.wav",
                          "negative/90004-bpc1_pcm_044k1_16bit_gapless_part2_gap.wav"])
        with open(os.path.join(_gen["out"], "SHA256SUMS")) as f:
            sums = dict(reversed(line.rstrip("\n").split("  ", 1)) for line in f)
        self.assertEqual(sorted(sums), [n for n in names if n != "SHA256SUMS"])
        for n, d in sums.items():
            self.assertEqual(fileset.sha256(os.path.join(_gen["out"], *n.split("/"))), d, n)

    def test_decode(self):
        """T2: every part, in every format, through checker.readers; part 1
        and part 2 joined are the regular file's units."""
        for name, (p1, p2) in pairs().items():
            base, units = regular(name)
            J = fileset.gapless_cut(base.cfg)
            fmts = ("dsf",) if base.container == "dsf" else ("dff",) if base.container == "dff" else FORMATS
            for fmt in fmts:
                with self.subTest(name=name, fmt=fmt):
                    a, b = (readers.read_file(path(p, fmt)) for p in (p1, p2))
                    self.assertEqual((a.rate, a.nch, b.rate, b.nch), (base.cfg.rate, 2) * 2)
                    n = J // 8 if base.cfg.dsd else J
                    self.assertEqual(len(a.data), n)
                    self.assertTrue(np.array_equal(np.concatenate([a.data, b.data]), units))
                    if fmt == "flac":
                        _, (maxb, total) = tags_flac(path(p1, fmt))
                        self.assertEqual(total, J)
                        self.assertNotEqual(J % maxb, 0)
                    if fmt == "alac":
                        tr = containers.read_alac_track(path(p1, fmt))
                        self.assertEqual(len(tr.packets), -(-J // 4096))
                        with open(path(p1, fmt), "rb") as f:
                            m4a = f.read()
                        at = m4a.index(b"stts") + 8
                        runs = struct.unpack_from(">%dI" % (2 * m4a[at + 3]), m4a, at + 4)
                        self.assertEqual(runs, (J // 4096, 4096, 1, J % 4096))

    def test_dsf_padding(self):
        """T7: the DSF parts' sample counts, and their last blocks: 4082 and 14
        bytes per channel, the rest zero."""
        _, (p1, p2) = next((n, p) for n, p in pairs().items() if n == "bpc1_dsd064_dsf")
        for p, count, used in ((p1, 21168016, 4082), (p2, 14483568, 14)):
            with self.subTest(part=p.part):
                n, bsize, body, _ = dsf_parts(path(p))
                self.assertEqual((n, bsize), (count, 4096))
                self.assertEqual(body.shape[1], -(-count // (8 * 4096)))
                self.assertEqual((count // 8) % 4096, used)
                self.assertFalse(body[:, -1, used:].any())
                self.assertEqual(body.shape[2] - used, 4096 - used)

    def test_tags(self):
        """The tags of 11.7: each title gives the file's number and format,
        each album the format, so a pair of one format plays as an album."""
        albums = set()
        for name, ps in list(pairs().items()) + list(pairs(fileset.GAP).items()):
            base, _ = regular(name)
            for p in ps:
                gap = " - NEGATIVE TEST, GAP INSERTED" if p.fault else ""

                def title(fmt):
                    return (f"TEST - DO NOT PLAY ON SPEAKERS - {p.number(fmt)} BPC1 {base.description} "
                            f"{fmt.upper()} gapless, part {p.part} of 2{gap}")

                comment = (f"BPC1 v1 bit-perfect test signal, gapless pair, part {p.part} of 2: one test file "
                           "cut at 7.500 s. Play both parts gaplessly; a checker gives one result for the pair. "
                           "TEST - DO NOT PLAY ON SPEAKERS OR HEADPHONES.")
                if p.fault:
                    comment += (" NEGATIVE TEST, gap inserted: 441 zero frames (10 ms of silence) before the "
                                "first frame of part 2. Played gaplessly, the pair must give NOT_BIT_PERFECT.")

                def album(fmt):
                    return f"BPC1 gapless {base.description} {fmt.upper()}" + (", GAP INSERTED" if p.fault else "")

                with self.subTest(name=name, part=p.part, fault=p.fault):
                    self.assertEqual((p.title(), p.comment), (title(p.format), comment))
                    albums.add(album(p.format))
                    if base.container in ("dsf", "dff"):
                        if base.container == "dsf":
                            tag = dsf_parts(path(p))[3]
                        else:
                            ch = dff_chunks(path(p))
                            tag = ch[b"ID3 "]
                            self.assertIn(title("dff").encode(), ch[b"DIIN"])
                        self.assertEqual(tags_id3(tag), {"TIT2": title(base.container), "TPE1": fileset.ARTIST,
                                                         "TALB": album(base.container), "TRCK": f"{p.part}/2",
                                                         "COMM": comment})
                        continue
                    self.assertEqual(tags_wav(path(p, "wav")), {"INAM": title("wav"), "IART": fileset.ARTIST,
                                                                "IPRD": album("wav"), "ICMT": comment,
                                                                "ITRK": str(p.part)})
                    if p.fault:
                        self.assertEqual(p.formats, ())             # the negative pair: WAV only
                        continue
                    flac = tags_flac(path(p, "flac"))[0]
                    flac.pop("WAVEFORMATEXTENSIBLE_CHANNEL_MASK", None)   # flac's own, from 24-bit WAVs
                    self.assertEqual(flac,
                                     {"TITLE": title("flac"), "ARTIST": fileset.ARTIST, "ALBUM": album("flac"),
                                      "COMMENT": comment, "TRACKNUMBER": str(p.part), "TRACKTOTAL": "2"})
                    self.assertEqual(aiff_chunk_ids(path(p, "aiff")), [b"COMM", b"NAME", b"ANNO", b"SSND", b"ID3 "])
                    chunks = containers.read_aiff(path(p, "aiff"))[3]
                    self.assertEqual((chunks[b"NAME"], chunks[b"ANNO"]), (title("aiff").encode(), comment.encode()))
                    self.assertEqual(tags_id3(chunks[b"ID3 "]), {"TIT2": title("aiff"), "TPE1": fileset.ARTIST,
                                                                 "TALB": album("aiff"), "TRCK": f"{p.part}/2",
                                                                 "COMM": comment})
                    tr = containers.read_alac_track(path(p, "alac"))
                    self.assertEqual(tr.tags, {b"\xa9nam": title("alac"), b"\xa9too": containers.ALAC_TOOL,
                                               b"\xa9cmt": comment, b"\xa9ART": fileset.ARTIST,
                                               b"\xa9alb": album("alac")})
                    with open(path(p, "alac"), "rb") as f:
                        m4a = f.read()
                    trkn = (struct.pack(">I4sI4sII", 32, b"trkn", 24, b"data", 0, 0)
                            + struct.pack(">HHHH", 0, p.part, 2, 0))
                    pgap = struct.pack(">I4sI4sII", 25, b"pgap", 17, b"data", 21, 0) + b"\x01"
                    self.assertEqual((m4a.count(trkn), m4a.count(pgap)), (1, 1))
        # the DSD albums as before the numbered names
        self.assertLessEqual({"BPC1 gapless DSD64 DSF", "BPC1 gapless DSD64 DFF",
                              "BPC1 gapless PCM 44.1 kHz 16-bit WAV, GAP INSERTED"}, albums)


@unittest.skipUnless(TC.CC, "no host C compiler")
class Streams(unittest.TestCase):
    """T3 to T6: streams of the pairs through the firmware's engine (PCM in
    slots 16, 24 and 32; the DSD pairs as DoP) and checker/verify.py (also
    native DSD), with allow_silence off and on: the expected results, and
    the same ones from both."""

    run_c = TC.Checker.run_c

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="bpc1-gapless-c-")
        cls.exe = TC.build(cls.tmp.name, "bpc1_check")
        cls.n = 0
        generated()
        cls.pcm, cls.dsd = [], []
        for name, (p1, p2) in pairs().items():
            cfg = p1.cfg
            a, b = (readers.read_file(path(p)).data for p in (p1, p2))
            (cls.dsd if cfg.dsd else cls.pcm).append((name, cfg, a, b))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def agree(self, cases):
        """Cases (label, stream, rate, slots, want, unit, why): the engine in
        every slot of `slots` and verify.py, allow_silence off and on, give
        the results `want` [(verdict, end, diff)], the engine the same as
        verify.py; for one NOT_BIT_PERFECT result, its first difference at
        source unit `unit` and, if given, check `why`. Run in threads."""
        def one(case):
            _, w, rate, slots, *_ = case
            low = np.uint32((1 << (32 - min(slots))) - 1)
            same = not (w.view(np.uint32) & low).any()     # the same data in every slot
            py = {sil: TC.py_verify(w, rate, min(slots), sil) for sil in (False, True)}
            c = {(slot, sil): self.results(w, rate, slot=slot, silence=sil) for slot in slots for sil in (False, True)}
            return same, py, c

        with ThreadPoolExecutor(max_workers=min(12, os.cpu_count() or 1)) as pool:
            got = list(pool.map(one, cases))
        for (label, _, _, _, want, unit, why), (same, py, c) in zip(cases, got):
            with self.subTest(label):
                self.assertTrue(same)
                for sil, res in py.items():
                    self.assertEqual([(r["verdict"], r["end"], "-" if r["diff"] is None else "%.3f" % r["diff"])
                                      for r in res], want, ("verify.py", sil))
                for (slot, sil), res in c.items():
                    p = py[sil]
                    self.assertEqual(TC.c_key(res), [(r["verdict"], r["end"], "-" if r["diff"] is None
                                                      else "%.3f" % r["diff"], str(r["bits"])) for r in p],
                                     (slot, sil))
                    self.assertEqual(TC.unit_key(res), TC.unit_key(p), (slot, sil))
                    if unit is not None:
                        self.assertEqual(int(res[0]["diff_unit"]), unit, (slot, sil))
                    if why is not None:
                        self.assertEqual(res[0]["why"], why, (slot, sil))

    def results(self, w, rate, **kw):
        return self.run_c(w, rate, **kw)[0]

    def native(self, data, rate, want, unit=None):
        """Native DSD through verify.py, allow_silence off and on."""
        for sil in (False, True):
            st = readers.Stream(True, rate, 2, np.ascontiguousarray(data), "test")
            res = verify.verify_stream(st, sil)
            self.assertEqual([(r["verdict"], r["end"], "-" if r["diff"] is None else "%.3f" % r["diff"])
                              for r in res], want, sil)
            if unit is not None:
                self.assertEqual(res[0]["diff_unit"], unit)

    @staticmethod
    def slots(cfg):
        return (16, 24, 32) if cfg.depth == 16 else (24, 32)

    def test_joined(self):
        """T3: a pair joined gives one BIT_PERFECT, end LAST."""
        want = [("BIT_PERFECT", "LAST", "-")]
        self.agree([(name, np.concatenate([a, b]), cfg.rate, self.slots(cfg), want, None, None)
                    for name, cfg, a, b in self.pcm]
                   + [(name, np.concatenate([dop(a, 0), dop(b, len(a) // 2)]), cfg.rate // 16, (24, 32), want,
                       None, None) for name, cfg, a, b in self.dsd])
        for name, cfg, a, b in self.dsd:
            with self.subTest(name):
                self.native(np.concatenate([a, b]), cfg.rate, want)

    def test_changed_join(self):
        """T4: units inserted, dropped or repeated at the cut, or a crossfade:
        one NOT_BIT_PERFECT, diff at the cut (7.500), or before it where part 1
        was cut short or changed."""
        at_cut = [("NOT_BIT_PERFECT", "LAST", "7.500")]
        for name, cfg, a, b in self.pcm:
            J, rate, slots = len(a), cfg.rate, self.slots(cfg)
            cases = [(f"{name}: {n} zero frames inserted", np.concatenate([a, TC.zeros(n), b]), J)
                     for n in (1, 6, rate // 100, rate, len(b) + 1)]
            for d in (1, 6):
                cases += [(f"{name}: {d} frames dropped at the cut", np.concatenate([a, b[d:]]), J),
                          (f"{name}: {d} frames dropped before the cut", np.concatenate([a[:-d], b]), J - d),
                          (f"{name}: {d} frames repeated", np.concatenate([a, a[-d:], b]), J)]
            cases = [(label, x, rate, slots, at_cut, unit, None) for label, x, unit in cases]
            # a 10 ms linear crossfade: part 1's end changed, diff before the cut
            n = rate // 100
            g = np.arange(n)[:, None] / n
            s1, s2 = (x.astype(np.int64) >> (32 - cfg.depth) for x in (a[J - n:], b[:n]))
            mix = (np.rint(s1 * (1 - g) + s2 * g).astype(np.int64) << (32 - cfg.depth)).astype(np.int32)
            first = J - n + int(np.nonzero((mix != a[J - n:]).any(axis=1))[0][0])
            self.assertEqual(("%.3f" % (first / rate), first), ("7.490", J - n + 1))
            cases.append((f"{name}: 10 ms crossfade", np.concatenate([a[:J - n], mix, b[n:]]), rate, slots,
                          [("NOT_BIT_PERFECT", "LAST", "7.490")], first, None))
            self.agree(cases)
        for name, cfg, a, b in self.dsd:
            f, rate = len(a) // 2, cfg.rate // 16
            d1, d2 = dop(a, 0), dop(b, f)
            nxt = 0xFA if f % 2 else 0x05                      # the marker frame f carries
            cases = [(f"{name}: {n} DoP idle frames inserted", np.concatenate([d1, TC.dop_idle(n, nxt), d2]), 2 * f)
                     for n in (1, 22, 1764)]
            pad = (TC.dop_idle(7, nxt).view(np.uint32) & np.uint32(0xFF000000)).view(np.int32)
            cases.append((f"{name}: the 14 DSF padding bytes inserted", np.concatenate([d1, pad, d2]), 2 * f))
            for d in (1, 6):
                cases += [(f"{name}: {d} DoP frames dropped at the cut", np.concatenate([d1, d2[d:]]), 2 * f),
                          (f"{name}: {d} DoP frames dropped before the cut", np.concatenate([d1[:-d], d2]),
                           2 * (f - d)),
                          (f"{name}: {d} DoP frames repeated", np.concatenate([d1, d1[-d:], d2]), 2 * f)]
            self.agree([(label, x, rate, (24, 32), at_cut, unit, None) for label, x, unit in cases])
            with self.subTest(name=name, case="native DSD, the 14 DSF padding bytes inserted"):
                self.native(np.concatenate([a, np.zeros((14, 2), np.uint8), b]), cfg.rate, at_cut, len(a))

    def test_two_streams(self):
        """T5: the parts as two streams: part 1 stops in the body at the cut,
        part 2 starts in it: diff at the first block after the cut."""
        stop = [("NOT_BIT_PERFECT", "STOP", "7.500")]
        cases = []
        for name, cfg, a, b in self.pcm:
            second = [("NOT_BIT_PERFECT", "LAST", SECOND_PART_DIFF[cfg.rate])]
            cases += [(name + " part 1", a, cfg.rate, self.slots(cfg), stop, len(a), None),
                      (name + " part 2", b, cfg.rate, self.slots(cfg), second, None, None)]
        for name, cfg, a, b in self.dsd:
            second = [("NOT_BIT_PERFECT", "LAST", SECOND_PART_DIFF[cfg.rate])]
            cases += [(name + " part 1", dop(a, 0), cfg.rate // 16, (24, 32), stop, len(a), None),
                      (name + " part 2", dop(b, 0), cfg.rate // 16, (24, 32), second, None, None)]
            with self.subTest(name):
                self.native(a, cfg.rate, stop, len(a))
                self.native(b, cfg.rate, second)
        self.agree(cases)

    def test_gap_pair(self):
        """T9: the negative pair, 10 ms of zero frames before part 2: joined,
        one NOT_BIT_PERFECT at the cut (7.500); as two streams, two
        NOT_BIT_PERFECT. Its part 1 is the pair's part 1, its part 2 the
        zeros and the pair's part 2."""
        (g1, g2), = pairs(fileset.GAP).values()
        name, cfg, a, b = next(x for x in self.pcm if x[0] == "bpc1_pcm_044k1_16bit")
        x1, x2 = (readers.read_file(path(p)).data for p in (g1, g2))
        self.assertTrue(np.array_equal(x1, a))
        self.assertTrue(np.array_equal(x2, np.concatenate([TC.zeros(441), b])))
        slots = self.slots(cfg)
        self.agree([("gap pair joined", np.concatenate([x1, x2]), cfg.rate, slots,
                     [("NOT_BIT_PERFECT", "LAST", "7.500")], len(a), None),
                    ("gap pair, part 1", x1, cfg.rate, slots, [("NOT_BIT_PERFECT", "STOP", "7.500")], len(a),
                     None),
                    ("gap pair, part 2", x2, cfg.rate, slots,
                     [("NOT_BIT_PERFECT", "LAST", SECOND_PART_DIFF[cfg.rate])], None, None)])

    def test_dop_markers_restarted(self):
        """T6: part 1 holds an odd number of DoP frames, so DoP markers that
        start again with part 2 repeat one: a marker fault at the cut."""
        for name, cfg, a, b in self.dsd:
            self.assertEqual(len(a) // 2 % 2, 1, name)
        self.agree([(name, np.concatenate([dop(a, 0), dop(b, 0)]), cfg.rate // 16, (24, 32),
                     [("NOT_BIT_PERFECT", "LAST", "7.500")], len(a), "marker") for name, cfg, a, b in self.dsd])


class Repeatability(unittest.TestCase):
    """T8: the set and its zip, byte-identical on a second run; the zip's
    contents."""

    @classmethod
    def setUpClass(cls):
        generated()

    def test_twice_and_package(self):
        b = os.path.join(_gen["tmp"].name, "b")
        generate(b)
        names = tree(_gen["out"])
        self.assertEqual(names, tree(b))
        for n in names:
            self.assertTrue(filecmp.cmp(os.path.join(_gen["out"], n), os.path.join(b, n), shallow=False), n)
        dists = []
        for out in (_gen["out"], b):
            dist = out + "-dist"
            subprocess.run([sys.executable, "-m", "generator", "package", "--gapless", "--out", out, "--dist", dist],
                           cwd=ROOT, check=True, capture_output=True)
            dists.append(dist)
        self.assertEqual(sorted(os.listdir(dists[0])), ["SHA256SUMS", "bpc1-r1-gapless.zip"])
        for n in os.listdir(dists[0]):
            self.assertTrue(filecmp.cmp(os.path.join(dists[0], n), os.path.join(dists[1], n), shallow=False), n)
        z = os.path.join(dists[0], "bpc1-r1-gapless.zip")
        with open(os.path.join(dists[0], "SHA256SUMS")) as f:
            self.assertEqual(f.read(), f"{P.sha256_file(z)}  bpc1-r1-gapless.zip\n")
        top = "bpc1-r1-gapless/"
        with zipfile.ZipFile(z) as zf:
            self.assertIsNone(zf.testzip())
            infos = zf.infolist()
            got = [i.filename for i in infos]
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in infos))
            names = [n for n in names if not n.startswith("negative/")]    # in the negative archive
            want = ([top, top + P.WARNING_FILE, top + "README.txt", top + "SHA256SUMS"]
                    + [top + d + "/" for d in ("aiff", "alac", "dff", "dsf", "flac", "wav")]
                    + [top + n for n in names if n != "SHA256SUMS"])
            self.assertEqual(sorted(got), sorted(want))
            self.assertEqual(got, sorted(got))
            self.assertFalse([n for n in got if "negative" in n])
            sums = zf.read(top + "SHA256SUMS").decode("ascii")
            with open(os.path.join(_gen["out"], "SHA256SUMS")) as f:
                self.assertEqual(sums, "".join(x for x in f if "  negative/" not in x))
            readme = zf.read(top + "README.txt").decode("ascii")
        self.assertEqual(readme.encode("ascii"), P.readme(P.GAPLESS))
        self.assertNotIn("\n", readme.replace("\r\n", ""))
        text = " ".join(readme.split())
        for s in ("TEST SIGNALS - DO NOT PLAY ON SPEAKERS OR HEADPHONES", "cut in two at 7.5 s",
                  "wav/ aiff/ flac/ alac/", "lost=0", "diff 7.500", "7.523 or 7.509",
                  "Clipping and level indicators", "MIT"):
            self.assertIn(s, text)
        self.assertNotIn("negative/", text)
        self.assertLessEqual(max(len(x) for x in readme.splitlines()), 79)

    def test_regular_files_unchanged(self):
        """The writers the gapless set changed still write the regular files'
        bytes (no external tool): DSF, DFF, WAV and AIFF. The digests are
        those of the set with numbered names and titles, whose audio data
        equals that of the set before them (DFF: since the one DSD kind)."""
        want = {"01400-bpc1_dsd064.dsf":
                    "df91da9b6f6c82186ba46037a5f6281dd3cdbf78b8971027534e8e485fe527cb",
                "01500-bpc1_dsd064.dff":
                    "120f7487206e41e40912aff8b6f2735f1c14708652cd305d10dfa18db1b4f618",
                "00000-bpc1_pcm_044k1_16bit.wav":
                    "10e6f931c9ec1b570a7a0e24c86060c46f2d6bb94a9454313eb287bdd5b645f0",
                "00200-bpc1_pcm_044k1_16bit.aiff":
                    "e3deb89a32289a32cd23f4aff084dac2a680abed5452664a5a07721bcedcce6e"}
        files = {label(f): f for f in fileset.test_set() if not f.fault}
        with tempfile.TemporaryDirectory(prefix="bpc1-gapless-") as d:
            got = {}
            for name in ("bpc1_dsd064_dsf", "bpc1_dsd064_dff", "bpc1_pcm_044k1_16bit"):
                p = fileset.write_file(files[name], d)
                got[os.path.basename(p)] = fileset.sha256(p)
                if name.startswith("bpc1_pcm"):
                    p = fileset.write_aiff(files[name], p)
                    got[os.path.basename(p)] = fileset.sha256(p)
        self.assertEqual(got, want)


class Join(unittest.TestCase):
    """The software checker's --join: the given files judged as one stream."""

    @classmethod
    def setUpClass(cls):
        generated()

    def run_cli(self, args):
        """Exit status, the verdicts and diffs of --json, and stderr."""
        from checker import __main__ as cli
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--json"] + args)
        reps = [json.loads(line) for line in out.getvalue().splitlines()]
        return code, [(r["verdict"], r["end"], r["diff"] and "%.3f" % r["diff"]) for r in reps], err.getvalue()

    def test_pairs(self):
        """A pair joined gives one BIT_PERFECT, the parts in the wrong order
        do not; without --join each part is NOT_BIT_PERFECT, as before."""
        ps = pairs()
        for name, fmt1, fmt2 in (("bpc1_pcm_044k1_16bit", "wav", "wav"), ("bpc1_pcm_048k_24bit", "flac", "alac"),
                                 ("bpc1_dsd064_dsf", "dsf", "dsf")):
            p1, p2 = ps[name]
            files = [path(p1, fmt1), path(p2, fmt2)]
            with self.subTest(name):
                self.assertEqual(self.run_cli(["--join"] + files)[:2], (0, [("BIT_PERFECT", "LAST", None)]))
                second = SECOND_PART_DIFF[p1.cfg.rate]
                self.assertEqual(self.run_cli(files)[:2], (1, [("NOT_BIT_PERFECT", "STOP", "7.500"),
                                                               ("NOT_BIT_PERFECT", "LAST", second)]))
        self.assertEqual(self.run_cli(["--join"] + files[::-1])[0], 1)
        g1, g2 = pairs(fileset.GAP)["bpc1_pcm_044k1_16bit"]
        self.assertEqual(self.run_cli(["--join", path(g1), path(g2)])[:2],
                         (1, [("NOT_BIT_PERFECT", "LAST", "7.500")]))
        self.assertEqual(self.run_cli([path(g1), path(g2)])[:2],
                         (1, [("NOT_BIT_PERFECT", "STOP", "7.500"), ("NOT_BIT_PERFECT", "LAST", "7.523")]))

    def test_mismatch(self):
        """Files of another rate, channel count or sample format are not joined."""
        ps = pairs()
        a16, a24, b16 = (ps[n][0] for n in ("bpc1_pcm_044k1_16bit", "bpc1_pcm_044k1_24bit", "bpc1_pcm_048k_16bit"))
        dsf = ps["bpc1_dsd064_dsf"][1]
        pcm16 = "PCM at 44100 Hz, 2 channels, 16 bits in 16-bit integer samples"
        for files, what in (([path(a16), path(a24, "flac")], "PCM at 44100 Hz, 2 channels, 24 bits in 24-bit "
                                                              f"integer samples, not {pcm16}"),
                            ([path(a16), path(b16)], f"PCM at 48000 Hz, 2 channels, 16 bits in 16-bit integer "
                                                     f"samples, not {pcm16}"),
                            ([path(a16), path(dsf)], f"DSD at 2822400 Hz, 2 channels, not {pcm16}")):
            with self.subTest(what):
                code, reps, err = self.run_cli(["--join"] + files)
                self.assertEqual((code, reps), (2, []))
                self.assertIn(f"cannot join {files[1]} to {files[0]}: {what}", err)
        mono = os.path.join(_gen["tmp"].name, "mono.wav")
        containers.write_wav(mono, 44100, 1, 16, 16, 4, b"", [np.zeros((4, 1), np.int32)])
        code, _, err = self.run_cli(["--join", path(a16), mono])
        self.assertEqual(code, 2)
        self.assertIn("PCM at 44100 Hz, 1 channels, 16 bits", err)


if __name__ == "__main__":
    unittest.main()
