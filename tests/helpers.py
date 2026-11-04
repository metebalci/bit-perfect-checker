"""Shared helpers for the tests."""

import os
import tempfile

import numpy as np

from checker import check, readers
from generator import fileset, spec
from generator.signal import Config, dsd_blocks, pcm_blocks

# Short files with the full structure: two R-header blocks (k = 8 and 24),
# lead-in, body and tail.
SHORT = dict(lead_blocks=2, body_blocks=24, tail_blocks=2)


def short_config(kind, rate, depth):
    return Config(kind=kind, rate=rate, depth=depth, **SHORT)


def pcm_words(cfg):
    """The whole file as 32-bit left-justified words (frames, nch)."""
    s = pcm_blocks(cfg, 0, cfg.total_blocks)
    return (s.astype(np.int64) << (32 - cfg.depth)).astype(np.int32)


def dsd_bits(cfg):
    return dsd_blocks(cfg, 0, cfg.total_blocks)


def pcm_stream(w, rate, slot, container="test"):
    return readers.Stream(False, rate, w.shape[1], np.ascontiguousarray(w), container,
                          slot_bits=slot, valid_bits=slot)


def check_words(w, rate, slot):
    return check.check(pcm_stream(w, rate, slot))


def short_file(container, cfg, encoded=False):
    """A short file; encoded: with the set's FLAC, AIFF and ALAC rule (fileset.formats)."""
    name = "short_%s_%d_%d" % (container, cfg.rate, cfg.depth)
    formats = fileset.formats(container, cfg) if encoded else ()
    return fileset.FileSpec(name, container, cfg, formats, "test " + name)


class TempDir:
    def __enter__(self):
        self._t = tempfile.TemporaryDirectory(prefix="bpc1-test-")
        return self._t.name

    def __exit__(self, *exc):
        self._t.cleanup()


def write_and_check(fspec, out_dir, flac=False):
    path = fileset.write_file(fspec, out_dir)
    reps = [check.check(readers.read_file(path), path)]
    if flac:
        reps.append(check.check(readers.read_file(fileset.write_flac(fspec, path))))
    return reps


def diagnosis(rep):
    """The diagnosis of a check report (or the diagnosis itself)."""
    return rep.get("diagnosis", rep)


def counts(rep):
    """The diagnosis's body counts by kind that are not zero."""
    return {k: v for k, v in diagnosis(rep)["counts"].items() if v and k != "side_events"}


def codes(rep):
    """The diagnosis's findings."""
    return [r["code"] for r in diagnosis(rep).get("reasons", [])]


def file_exists(path):
    return os.path.isfile(path)

