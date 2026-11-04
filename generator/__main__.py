"""Write the BPC1 v1 test-file set, or package it for release.

    python3 -m generator [--out out] [--32bit] [--no-flac] [--no-aiff] [--no-alac]
                         [--only TEXT] [--jobs N]
    python3 -m generator --sweep [--out out-sweep] [--no-flac] [--no-aiff] [--no-alac] ...
    python3 -m generator --gapless [--out out-gapless] [--no-flac] [--no-aiff] [--no-alac] ...
    python3 -m generator package [--sweep | --gapless | --negative] [--out out] [--dist dist]
                                 [--out-sweep out-sweep] [--out-gapless out-gapless]

--sweep writes the code-sweep set (kind 5) instead of the regular set;
--gapless the gapless set (regular files cut in two, one folder per format,
by default in out-gapless). Every file name starts with its five-digit
number (docs/bpc1.md, 11.2); the negative files are WAV only. Every
16- and 24-bit PCM and DoP WAV also comes as FLAC (flac >= 1.4.0), AIFF
(written here) and ALAC in .m4a (Apple's reference encoder, built with the
host C++ compiler); a missing tool skips its format.
"""

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

from . import alac, containers, fileset, spec


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["package"]:
        from . import package
        return package.main(argv[1:])
    ap = argparse.ArgumentParser(prog="python3 -m generator",
                                 description="Write the BPC1 v1 test-file set "
                                             "('package' builds the release zips).")
    ap.add_argument("--out", help="output directory (default: out; with --gapless: out-gapless)")
    ap.add_argument("--32bit", dest="pcm32", action="store_true",
                    help="also write the ten 32-bit PCM WAVs (not part of the release)")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--sweep", action="store_true",
                       help="write the code-sweep set instead (full-scale; about 5.4 GB, all formats)")
    which.add_argument("--gapless", action="store_true",
                       help="write the gapless set instead (pairs of parts, all formats)")
    ap.add_argument("--no-flac", action="store_true", help="skip FLAC encoding")
    ap.add_argument("--no-aiff", action="store_true", help="skip the AIFF files")
    ap.add_argument("--no-alac", action="store_true", help="skip ALAC encoding")
    ap.add_argument("--only", action="append", default=[], metavar="TEXT",
                    help="only files whose name contains TEXT (repeatable)")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=spec.DEFAULT_SEED,
                    help="file seed (default 0x%08X; other seeds give a "
                         "different, non-published set)" % spec.DEFAULT_SEED)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--list", action="store_true", help="list the files and exit")
    a = ap.parse_args(argv)
    if a.gapless and a.pcm32:
        ap.error("--32bit is not part of the gapless set")
    a.out = a.out or ("out-gapless" if a.gapless else "out")

    if a.sweep:
        files = fileset.sweep_set(seed=a.seed)
    elif a.gapless:
        files = fileset.gapless_set(seed=a.seed)
    else:
        files = fileset.test_set(seed=a.seed, pcm32=a.pcm32)
    if a.only:
        files = [f for f in files if any(t in f.name for t in a.only)]
    if a.list:
        for f in files:
            print("\n".join([f.relpath] + [f.relpath_as(x) for x in f.formats]))
        return 0

    want_flac = not a.no_flac
    if want_flac and not containers.flac_usable():
        v = containers.flac_version()
        why = "not found" if v is None else "version %s is older than 1.4.0" % ".".join(map(str, v))
        print(f"FLAC skipped: the flac command-line tool is {why}. "
              "Install flac >= 1.4.0 to write the FLAC files.", file=sys.stderr)
        want_flac = False

    want_alac = not a.no_alac
    if want_alac and not containers.alac_usable():
        print(f"ALAC skipped: {alac.missing()}.", file=sys.stderr)
        want_alac = False
    if want_alac:
        alac.tool()                     # build the helper once, before the workers

    os.makedirs(a.out, exist_ok=True)
    t0 = time.monotonic()
    results = []
    jobs = [(f, a.out, want_flac, not a.no_aiff, want_alac) for f in files]
    # Largest files first keeps the pool busy until the end.
    jobs.sort(key=lambda j: -j[0].cfg.total_units * max(j[0].cfg.depth, 16))
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for res in pool.map(fileset.job, jobs):
            for name, size, digest in res:
                print(f"{size:>11,d}  {name}")
                results.append((name, size, digest))
    results.sort()
    if not a.only:
        with open(os.path.join(a.out, "SHA256SUMS"), "w") as f:
            for name, _, digest in results:
                f.write(f"{digest}  {name}\n")
    total = sum(r[1] for r in results)
    print(f"{len(results)} files, {total / 1e6:.1f} MB, {time.monotonic() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
