"""Check BPC1 test files or captures.

    python3 -m checker FILE...
    python3 -m checker --raw s32le --rate 96000 --channels 2 CAPTURE.raw
    python3 -m checker --allow-silence FILE...

FILE may be any file the project publishes: WAV, FLAC (read with the flac
tool), AIFF, ALAC in .m4a (decoded with Apple's reference ALAC decoder), DSF
or DFF; DoP comes in WAV, FLAC, AIFF or ALAC. A stream that carries several files (joined by the
host) gives one report per file, and one UNKNOWN_SIGNAL report per stretch
outside every file, silence included; with --allow-silence, a stretch of
silence gives none. Each report gives the verdict first (BIT_PERFECT,
NOT_BIT_PERFECT or UNKNOWN_SIGNAL), then an informative diagnosis of what
changed. --json
prints one JSON object per report, one per line. The exit status is 0 only
when every report is BIT_PERFECT and there is at least one.
"""

import argparse
import json
import sys

from . import check, readers, report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m checker",
                                 description="Check BPC1 v1 test files or captures.")
    ap.add_argument("files", nargs="+",
                    help="WAV, FLAC (needs flac), AIFF, ALAC .m4a (needs a C++ compiler), DSF or DFF "
                         "files, or raw captures with --raw")
    ap.add_argument("--raw", choices=readers.RAW_FORMATS,
                    help="raw capture format (needs --rate and --channels)")
    ap.add_argument("--rate", type=int, help="raw: frame rate in Hz (ALSA convention)")
    ap.add_argument("--channels", type=int, default=2, help="raw: channel count")
    ap.add_argument("--json", action="store_true",
                    help="print one JSON object per file, one per line")
    ap.add_argument("--allow-silence", action="store_true",
                    help="silence outside every file gives no result (default: unknown signal)")
    a = ap.parse_args(argv)
    if a.raw and not a.rate:
        ap.error("--raw needs --rate")

    def default(o):
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    worst = 0
    for path in a.files:
        try:
            s = readers.read_file(path, raw=a.raw, rate=a.rate, nch=a.channels)
            reps = check.check_stream(s, name=path, allow_silence=a.allow_silence)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"{path}: error: {e}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        if not reps:
            print(f"{path}: no audio, no result")
            worst = max(worst, 1)
        for rep in reps:
            if rep["verdict"] != "BIT_PERFECT":
                worst = max(worst, 1)
            if a.json:
                print(json.dumps(rep, default=default))
            else:
                print(report.render(rep, several=len(reps) > 1))
    return worst


if __name__ == "__main__":
    sys.exit(main())
