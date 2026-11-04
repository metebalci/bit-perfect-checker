"""Numbered file names (docs/bpc1.md, 11.2) and titles (11.3).

Every published file name starts with five digits TOFPP: the golden list of
all of them; their uniqueness; each digit rebuilt from the file's own
properties with tables of this test, not the generator's; the order of a
folder that holds the files of every archive; what each archive holds; and
the titles, from which each file name can be read."""

import os
import re
import unittest
from dataclasses import replace

from checker import readers
from generator import fileset, package as P, spec

from tests import helpers as H

# Every published file, sorted by name: the regular, code-sweep and gapless
# sets and the negative files.
GOLDEN = """
00000-bpc1_pcm_044k1_16bit.wav
00001-bpc1_pcm_048k_16bit.wav
00002-bpc1_pcm_088k2_16bit.wav
00003-bpc1_pcm_096k_16bit.wav
00004-bpc1_pcm_176k4_16bit.wav
00005-bpc1_pcm_192k_16bit.wav
00006-bpc1_pcm_352k8_16bit.wav
00007-bpc1_pcm_384k_16bit.wav
00008-bpc1_pcm_705k6_16bit.wav
00009-bpc1_pcm_768k_16bit.wav
00030-bpc1_pcm_044k1_24bit.wav
00031-bpc1_pcm_048k_24bit.wav
00032-bpc1_pcm_088k2_24bit.wav
00033-bpc1_pcm_096k_24bit.wav
00034-bpc1_pcm_176k4_24bit.wav
00035-bpc1_pcm_192k_24bit.wav
00036-bpc1_pcm_352k8_24bit.wav
00037-bpc1_pcm_384k_24bit.wav
00038-bpc1_pcm_705k6_24bit.wav
00039-bpc1_pcm_768k_24bit.wav
00100-bpc1_pcm_044k1_16bit.flac
00101-bpc1_pcm_048k_16bit.flac
00102-bpc1_pcm_088k2_16bit.flac
00103-bpc1_pcm_096k_16bit.flac
00104-bpc1_pcm_176k4_16bit.flac
00105-bpc1_pcm_192k_16bit.flac
00106-bpc1_pcm_352k8_16bit.flac
00107-bpc1_pcm_384k_16bit.flac
00108-bpc1_pcm_705k6_16bit.flac
00109-bpc1_pcm_768k_16bit.flac
00130-bpc1_pcm_044k1_24bit.flac
00131-bpc1_pcm_048k_24bit.flac
00132-bpc1_pcm_088k2_24bit.flac
00133-bpc1_pcm_096k_24bit.flac
00134-bpc1_pcm_176k4_24bit.flac
00135-bpc1_pcm_192k_24bit.flac
00136-bpc1_pcm_352k8_24bit.flac
00137-bpc1_pcm_384k_24bit.flac
00138-bpc1_pcm_705k6_24bit.flac
00139-bpc1_pcm_768k_24bit.flac
00200-bpc1_pcm_044k1_16bit.aiff
00201-bpc1_pcm_048k_16bit.aiff
00202-bpc1_pcm_088k2_16bit.aiff
00203-bpc1_pcm_096k_16bit.aiff
00204-bpc1_pcm_176k4_16bit.aiff
00205-bpc1_pcm_192k_16bit.aiff
00206-bpc1_pcm_352k8_16bit.aiff
00207-bpc1_pcm_384k_16bit.aiff
00208-bpc1_pcm_705k6_16bit.aiff
00209-bpc1_pcm_768k_16bit.aiff
00230-bpc1_pcm_044k1_24bit.aiff
00231-bpc1_pcm_048k_24bit.aiff
00232-bpc1_pcm_088k2_24bit.aiff
00233-bpc1_pcm_096k_24bit.aiff
00234-bpc1_pcm_176k4_24bit.aiff
00235-bpc1_pcm_192k_24bit.aiff
00236-bpc1_pcm_352k8_24bit.aiff
00237-bpc1_pcm_384k_24bit.aiff
00238-bpc1_pcm_705k6_24bit.aiff
00239-bpc1_pcm_768k_24bit.aiff
00300-bpc1_pcm_044k1_16bit.m4a
00301-bpc1_pcm_048k_16bit.m4a
00302-bpc1_pcm_088k2_16bit.m4a
00303-bpc1_pcm_096k_16bit.m4a
00304-bpc1_pcm_176k4_16bit.m4a
00305-bpc1_pcm_192k_16bit.m4a
00306-bpc1_pcm_352k8_16bit.m4a
00307-bpc1_pcm_384k_16bit.m4a
00308-bpc1_pcm_705k6_16bit.m4a
00309-bpc1_pcm_768k_16bit.m4a
00330-bpc1_pcm_044k1_24bit.m4a
00331-bpc1_pcm_048k_24bit.m4a
00332-bpc1_pcm_088k2_24bit.m4a
00333-bpc1_pcm_096k_24bit.m4a
00334-bpc1_pcm_176k4_24bit.m4a
00335-bpc1_pcm_192k_24bit.m4a
00336-bpc1_pcm_352k8_24bit.m4a
00337-bpc1_pcm_384k_24bit.m4a
00338-bpc1_pcm_705k6_24bit.m4a
00339-bpc1_pcm_768k_24bit.m4a
01000-bpc1_dsd064_dop-176k4_24bit.wav
01001-bpc1_dsd128_dop-352k8_24bit.wav
01002-bpc1_dsd256_dop-705k6_24bit.wav
01100-bpc1_dsd064_dop-176k4_24bit.flac
01101-bpc1_dsd128_dop-352k8_24bit.flac
01102-bpc1_dsd256_dop-705k6_24bit.flac
01200-bpc1_dsd064_dop-176k4_24bit.aiff
01201-bpc1_dsd128_dop-352k8_24bit.aiff
01202-bpc1_dsd256_dop-705k6_24bit.aiff
01300-bpc1_dsd064_dop-176k4_24bit.m4a
01301-bpc1_dsd128_dop-352k8_24bit.m4a
01302-bpc1_dsd256_dop-705k6_24bit.m4a
01400-bpc1_dsd064.dsf
01401-bpc1_dsd128.dsf
01402-bpc1_dsd256.dsf
01500-bpc1_dsd064.dff
01501-bpc1_dsd128.dff
01502-bpc1_dsd256.dff
10000-bpc1_pcm_044k1_16bit_sweep.wav
10001-bpc1_pcm_048k_16bit_sweep.wav
10002-bpc1_pcm_088k2_16bit_sweep.wav
10003-bpc1_pcm_096k_16bit_sweep.wav
10004-bpc1_pcm_176k4_16bit_sweep.wav
10005-bpc1_pcm_192k_16bit_sweep.wav
10006-bpc1_pcm_352k8_16bit_sweep.wav
10007-bpc1_pcm_384k_16bit_sweep.wav
10008-bpc1_pcm_705k6_16bit_sweep.wav
10009-bpc1_pcm_768k_16bit_sweep.wav
10030-bpc1_pcm_044k1_24bit_sweep.wav
10031-bpc1_pcm_048k_24bit_sweep.wav
10032-bpc1_pcm_088k2_24bit_sweep.wav
10033-bpc1_pcm_096k_24bit_sweep.wav
10034-bpc1_pcm_176k4_24bit_sweep.wav
10035-bpc1_pcm_192k_24bit_sweep.wav
10036-bpc1_pcm_352k8_24bit_sweep.wav
10037-bpc1_pcm_384k_24bit_sweep.wav
10038-bpc1_pcm_705k6_24bit_sweep.wav
10039-bpc1_pcm_768k_24bit_sweep.wav
10100-bpc1_pcm_044k1_16bit_sweep.flac
10101-bpc1_pcm_048k_16bit_sweep.flac
10102-bpc1_pcm_088k2_16bit_sweep.flac
10103-bpc1_pcm_096k_16bit_sweep.flac
10104-bpc1_pcm_176k4_16bit_sweep.flac
10105-bpc1_pcm_192k_16bit_sweep.flac
10106-bpc1_pcm_352k8_16bit_sweep.flac
10107-bpc1_pcm_384k_16bit_sweep.flac
10108-bpc1_pcm_705k6_16bit_sweep.flac
10109-bpc1_pcm_768k_16bit_sweep.flac
10130-bpc1_pcm_044k1_24bit_sweep.flac
10131-bpc1_pcm_048k_24bit_sweep.flac
10132-bpc1_pcm_088k2_24bit_sweep.flac
10133-bpc1_pcm_096k_24bit_sweep.flac
10134-bpc1_pcm_176k4_24bit_sweep.flac
10135-bpc1_pcm_192k_24bit_sweep.flac
10136-bpc1_pcm_352k8_24bit_sweep.flac
10137-bpc1_pcm_384k_24bit_sweep.flac
10138-bpc1_pcm_705k6_24bit_sweep.flac
10139-bpc1_pcm_768k_24bit_sweep.flac
10200-bpc1_pcm_044k1_16bit_sweep.aiff
10201-bpc1_pcm_048k_16bit_sweep.aiff
10202-bpc1_pcm_088k2_16bit_sweep.aiff
10203-bpc1_pcm_096k_16bit_sweep.aiff
10204-bpc1_pcm_176k4_16bit_sweep.aiff
10205-bpc1_pcm_192k_16bit_sweep.aiff
10206-bpc1_pcm_352k8_16bit_sweep.aiff
10207-bpc1_pcm_384k_16bit_sweep.aiff
10208-bpc1_pcm_705k6_16bit_sweep.aiff
10209-bpc1_pcm_768k_16bit_sweep.aiff
10230-bpc1_pcm_044k1_24bit_sweep.aiff
10231-bpc1_pcm_048k_24bit_sweep.aiff
10232-bpc1_pcm_088k2_24bit_sweep.aiff
10233-bpc1_pcm_096k_24bit_sweep.aiff
10234-bpc1_pcm_176k4_24bit_sweep.aiff
10235-bpc1_pcm_192k_24bit_sweep.aiff
10236-bpc1_pcm_352k8_24bit_sweep.aiff
10237-bpc1_pcm_384k_24bit_sweep.aiff
10238-bpc1_pcm_705k6_24bit_sweep.aiff
10239-bpc1_pcm_768k_24bit_sweep.aiff
10300-bpc1_pcm_044k1_16bit_sweep.m4a
10301-bpc1_pcm_048k_16bit_sweep.m4a
10302-bpc1_pcm_088k2_16bit_sweep.m4a
10303-bpc1_pcm_096k_16bit_sweep.m4a
10304-bpc1_pcm_176k4_16bit_sweep.m4a
10305-bpc1_pcm_192k_16bit_sweep.m4a
10306-bpc1_pcm_352k8_16bit_sweep.m4a
10307-bpc1_pcm_384k_16bit_sweep.m4a
10308-bpc1_pcm_705k6_16bit_sweep.m4a
10309-bpc1_pcm_768k_16bit_sweep.m4a
10330-bpc1_pcm_044k1_24bit_sweep.m4a
10331-bpc1_pcm_048k_24bit_sweep.m4a
10332-bpc1_pcm_088k2_24bit_sweep.m4a
10333-bpc1_pcm_096k_24bit_sweep.m4a
10334-bpc1_pcm_176k4_24bit_sweep.m4a
10335-bpc1_pcm_192k_24bit_sweep.m4a
10336-bpc1_pcm_352k8_24bit_sweep.m4a
10337-bpc1_pcm_384k_24bit_sweep.m4a
10338-bpc1_pcm_705k6_24bit_sweep.m4a
10339-bpc1_pcm_768k_24bit_sweep.m4a
20000-bpc1_pcm_044k1_16bit_gapless_part1.wav
20001-bpc1_pcm_044k1_16bit_gapless_part2.wav
20002-bpc1_pcm_048k_16bit_gapless_part1.wav
20003-bpc1_pcm_048k_16bit_gapless_part2.wav
20030-bpc1_pcm_044k1_24bit_gapless_part1.wav
20031-bpc1_pcm_044k1_24bit_gapless_part2.wav
20032-bpc1_pcm_048k_24bit_gapless_part1.wav
20033-bpc1_pcm_048k_24bit_gapless_part2.wav
20100-bpc1_pcm_044k1_16bit_gapless_part1.flac
20101-bpc1_pcm_044k1_16bit_gapless_part2.flac
20102-bpc1_pcm_048k_16bit_gapless_part1.flac
20103-bpc1_pcm_048k_16bit_gapless_part2.flac
20130-bpc1_pcm_044k1_24bit_gapless_part1.flac
20131-bpc1_pcm_044k1_24bit_gapless_part2.flac
20132-bpc1_pcm_048k_24bit_gapless_part1.flac
20133-bpc1_pcm_048k_24bit_gapless_part2.flac
20200-bpc1_pcm_044k1_16bit_gapless_part1.aiff
20201-bpc1_pcm_044k1_16bit_gapless_part2.aiff
20202-bpc1_pcm_048k_16bit_gapless_part1.aiff
20203-bpc1_pcm_048k_16bit_gapless_part2.aiff
20230-bpc1_pcm_044k1_24bit_gapless_part1.aiff
20231-bpc1_pcm_044k1_24bit_gapless_part2.aiff
20232-bpc1_pcm_048k_24bit_gapless_part1.aiff
20233-bpc1_pcm_048k_24bit_gapless_part2.aiff
20300-bpc1_pcm_044k1_16bit_gapless_part1.m4a
20301-bpc1_pcm_044k1_16bit_gapless_part2.m4a
20302-bpc1_pcm_048k_16bit_gapless_part1.m4a
20303-bpc1_pcm_048k_16bit_gapless_part2.m4a
20330-bpc1_pcm_044k1_24bit_gapless_part1.m4a
20331-bpc1_pcm_044k1_24bit_gapless_part2.m4a
20332-bpc1_pcm_048k_24bit_gapless_part1.m4a
20333-bpc1_pcm_048k_24bit_gapless_part2.m4a
21400-bpc1_dsd064_gapless_part1.dsf
21401-bpc1_dsd064_gapless_part2.dsf
21500-bpc1_dsd064_gapless_part1.dff
21501-bpc1_dsd064_gapless_part2.dff
90000-bpc1_pcm_044k1_24bit_altered.wav
90001-bpc1_pcm_044k1_24bit_incomplete.wav
90002-bpc1_pcm_044k1_16bit_sweep_altered.wav
90003-bpc1_pcm_044k1_16bit_gapless_part1_gap.wav
90004-bpc1_pcm_044k1_16bit_gapless_part2_gap.wav
91000-bpc1_dsd064_dop-176k4_24bit_altered.wav
91001-bpc1_dsd064_dop-176k4_24bit_incomplete.wav
""".split()

EXT_DIGIT = {".wav": 0, ".flac": 1, ".aiff": 2, ".m4a": 3, ".dsf": 4, ".dff": 5}
EXT_FORMAT = {".wav": "WAV", ".flac": "FLAC", ".aiff": "AIFF", ".m4a": "ALAC", ".dsf": "DSF",
              ".dff": "DFF"}
PCM_TOKENS = ("044k1", "048k", "088k2", "096k", "176k4", "192k", "352k8", "384k", "705k6", "768k")
DSD_TOKENS = ("dsd064", "dsd128", "dsd256")
BASE = {"16": 0, "24": 30, "32": 60}
# The negative files: fixed positions, in their 9 O F group
NEGATIVES = {"90000": "bpc1_pcm_044k1_24bit_altered", "90001": "bpc1_pcm_044k1_24bit_incomplete",
             "90002": "bpc1_pcm_044k1_16bit_sweep_altered",
             "90003": "bpc1_pcm_044k1_16bit_gapless_part1_gap",
             "90004": "bpc1_pcm_044k1_16bit_gapless_part2_gap",
             "91000": "bpc1_dsd064_dop-176k4_24bit_altered",
             "91001": "bpc1_dsd064_dop-176k4_24bit_incomplete"}
NAME = re.compile(r"(\d{5})-(bpc1_(pcm_(\w+?)_(\d+)bit|(dsd\d{3})(_dop-\w+?_24bit)?)"
                  r"(_sweep)?(_gapless_part([12]))?(_altered|_incomplete|_gap)?)(\.\w+)$")


def published():
    """(set, file spec, format) of every published file."""
    out = []
    for setname, files in (("regular", fileset.test_set()), ("sweep", fileset.sweep_set()),
                           ("gapless", fileset.gapless_set())):
        for f in files:
            out += [(setname, f, x) for x in (f.format,) + f.formats]
    return out


def digits(name):
    """TOFPP of a file name, from what the name says: its rate, depth,
    DoP or DSD, set, part, fault and extension (and fixed positions for the
    negative files)."""
    m = NAME.match(name)
    num, base, _, rate, depth, dsd, dop, sweep, part, p, fault, ext = m.groups()
    pcm = rate is not None
    if fault:
        return num, NEGATIVES[num] == base
    o = 0 if pcm else 1
    t = 2 if part else 1 if sweep else 0
    r = PCM_TOKENS.index(rate) if pcm else DSD_TOKENS.index(dsd)
    b = BASE[depth] if pcm else 0
    pp = b + 2 * r + int(p) - 1 if part else b + r
    return num, num == f"{t}{o}{EXT_DIGIT[ext]}{pp:02d}"


class Names(unittest.TestCase):
    def test_golden(self):
        names = sorted(f.filename_as(x) for _, f, x in published())
        self.assertEqual(names, GOLDEN)
        self.assertEqual(len(GOLDEN), 221)

    def test_unique(self):
        files = (fileset.test_set(pcm32=True) + fileset.sweep_set() + fileset.gapless_set())
        nums = [f.number(x) for f in files for x in (f.format,) + f.formats]
        self.assertEqual(len(nums), 231)
        self.assertEqual(len(set(nums)), len(nums))
        self.assertEqual(sorted(n for n in nums if n[1:3] == "00" and n[3:] >= "60"),
                         [f"0006{i}" for i in range(10)])           # the unpublished 32-bit WAVs

    def test_digits_from_the_name(self):
        for name in GOLDEN:
            with self.subTest(name):
                num, ok = digits(name)
                self.assertTrue(ok, num)
        self.assertEqual(sorted(n for n in (x[:5] for x in GOLDEN) if n[0] == "9"), sorted(NEGATIVES))

    def test_digits_from_the_files(self):
        """O, the rate and the depth of each kind of file as the checker's
        readers read them from a short file written with its spec."""
        files = {f.name: f for f in fileset.test_set(pcm32=True) + fileset.sweep_set()
                 + fileset.gapless_set()}
        picks = ["bpc1_pcm_044k1_16bit", "bpc1_pcm_096k_32bit", "bpc1_pcm_768k_24bit",
                 "bpc1_pcm_048k_24bit_sweep", "bpc1_dsd128", "bpc1_dsd256_dop-705k6_24bit",
                 "bpc1_pcm_048k_24bit_gapless_part2", "bpc1_dsd064_dop-176k4_24bit_altered"]
        rates = {r: i for i, r in enumerate(spec.PCM_RATES)} | {r: i for i, r in enumerate(spec.DSD_RATES)}
        with H.TempDir() as d:
            for name in picks:
                f = files[name]
                for c in ([f.container] if f.container != "dsf" else ["dsf", "dff"]):
                    g = replace(f, container=c)
                    if not g.part:                     # a part needs the whole file's length
                        g = replace(g, cfg=replace(g.cfg, **H.SHORT))
                    with self.subTest(g.filename):
                        s = readers.read_file(fileset.write_file(g, d))
                        dop = readers.detect_dop(s)
                        if dop:
                            s = readers.unpack_dop(s)
                        n = g.number()
                        self.assertEqual(n[1], "1" if s.dsd else "0")
                        self.assertEqual(n[2], str(EXT_DIGIT[os.path.splitext(g.filename)[1]]))
                        if g.fault:
                            continue
                        r = rates[s.rate]
                        b = 0 if s.dsd else BASE[str(s.valid_bits)]
                        self.assertEqual(int(n[3:]), b + (2 * r + g.part - 1 if g.part else r))

    def test_merged_order(self):
        """All files in one folder: byte order, case-folded order and a
        natural sort agree; the groups play regular PCM, regular DoP and
        DSD, sweep, gapless, negatives; a part 1 comes right before its
        part 2."""
        natural = sorted(GOLDEN, key=lambda n: [int(x) if x.isdigit() else x
                                                for x in re.split(r"(\d+)", n)])
        self.assertEqual(sorted(GOLDEN, key=str.casefold), GOLDEN)
        self.assertEqual(natural, GOLDEN)
        groups = [n[:2] for n in GOLDEN]
        self.assertEqual(list(dict.fromkeys(groups)), ["00", "01", "10", "20", "21", "90", "91"])
        for i, n in enumerate(GOLDEN):
            if "part1" in n:
                self.assertEqual(GOLDEN[i + 1], n.replace("part1", "part2")[:4] + str(int(n[4]) + 1)
                                 + n.replace("part1", "part2")[5:], n)

    def test_archives(self):
        """Format archives hold types 0 and 1 in their format; the gapless
        archive type 2 with F matching its folder; the negative archive
        type 9 at its top level."""
        for pkg in P.RELEASE + P.SWEEP_RELEASE:
            fdig = {"wav": "045", "flac": "1", "aiff": "2", "alac": "3"}[pkg.files]
            for n in P.members(pkg):
                with self.subTest(pkg=pkg.name, name=n):
                    self.assertNotIn("/", n)
                    self.assertEqual(n[0], "1" if pkg.sweep else "0")
                    self.assertIn(n[2], fdig)
        folders = {"wav": "0", "flac": "1", "aiff": "2", "alac": "3", "dsf": "4", "dff": "5"}
        for n in P.members(P.GAPLESS):
            folder, base = n.split("/")
            self.assertEqual((base[0], base[2]), ("2", folders[folder]), n)
        neg = P.members(P.NEGATIVE)
        self.assertEqual([n[:5] for n in neg], sorted(NEGATIVES))
        self.assertTrue(all("/" not in n and n.endswith(".wav") for n in neg))
        every = sorted(n.split("/")[-1] for p in P.RELEASE + P.SWEEP_RELEASE + (P.GAPLESS, P.NEGATIVE)
                       for n in P.members(p))
        self.assertEqual(every, GOLDEN)


class Titles(unittest.TestCase):
    """Every title starts with the warning, then gives the file's number and
    format, and the file name follows from it."""

    TITLE = re.compile(r"TEST - DO NOT PLAY ON SPEAKERS - (\d{5}) BPC1 "
                       r"(?:PCM ([\d.]+) kHz (\d+)-bit( code sweep)?|DSD(\d+)( DoP ([\d.]+) kHz 24-bit)?) "
                       r"(WAV|FLAC|AIFF|ALAC|DSF|DFF)(, FULL-SCALE NOISE)?( gapless, part ([12]) of 2)?"
                       r"( - NEGATIVE TEST, (DELIBERATELY ALTERED|DELIBERATELY INCOMPLETE|GAP INSERTED))?$")

    @staticmethod
    def token(khz):
        k, _, d = khz.partition(".")
        return "%03dk%s" % (int(k), d)

    def name_from(self, title):
        m = self.TITLE.match(title)
        self.assertIsNotNone(m, title)
        num, rate, depth, sweep, dsd, dop, carrier, fmt, noise, gapless, part, _, fault = m.groups()
        self.assertEqual(bool(sweep), bool(noise), title)
        if rate:
            name = f"bpc1_pcm_{self.token(rate)}_{depth}bit" + ("_sweep" if sweep else "")
        else:
            name = f"bpc1_dsd{int(dsd):03d}" + (f"_dop-{self.token(carrier)}_24bit" if dop else "")
        name += f"_gapless_part{part}" if gapless else ""
        name += {None: "", "DELIBERATELY ALTERED": "_altered", "DELIBERATELY INCOMPLETE": "_incomplete",
                 "GAP INSERTED": "_gap"}[fault]
        ext = {v: k for k, v in EXT_FORMAT.items()}[fmt]
        return f"{num}-{name}{ext}"

    def test_titles(self):
        got = []
        for _, f, x in published():
            title = f.title(x)
            with self.subTest(title):
                self.assertEqual(self.name_from(title), f.filename_as(x))
                self.assertTrue(title.isascii())
            got.append(self.name_from(title))
        self.assertEqual(sorted(got), GOLDEN)

    def test_tag_writers(self):
        """The title in the tags of each written format is the one of that
        format."""
        f = replace(fileset.pcm_file(48000, 16), cfg=replace(fileset.pcm_file(48000, 16).cfg, **H.SHORT))
        self.assertEqual(f.title(), f.title("wav"))
        self.assertIn(("TITLE", f.title("flac")), fileset.flac_tags(f))
        self.assertIn(f.title("wav").encode("ascii"), fileset.info_tags(f))
        d = fileset.dsd_file(2822400, "dff")
        self.assertIn(d.title("dff").encode("ascii"), fileset.id3_tag(d))


if __name__ == "__main__":
    unittest.main()
