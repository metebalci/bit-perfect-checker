"""Command line for the checker's serial log.

    python3 -m bpcctl [--port PORT] monitor [--json]
    python3 -m bpcctl [--port PORT] status [--json] | result [--json] | version
    python3 -m bpcctl [--port PORT] formats [LIST | default] [--timeout S]
    python3 -m bpcctl [--port PORT] allow-silence true|false [--json]
    python3 -m bpcctl [--port PORT] run [--timeout S] [--expect N] [--json] -- PLAYER [ARGS ...]

`run` marks the log, runs the player command and waits for the device's
result lines of the streams the player sent. The device prints one result
per file, so a stream may have several; a result names its stream with
stream=<id>. A stream is done at its `stream stop` line, which follows its
results and says how many there were (results=<n>); bpcctl warns when
fewer arrived. A result's verdict is BIT_PERFECT, NOT_BIT_PERFECT,
UNKNOWN_SIGNAL (a stretch of the stream outside every recognised file,
silence included) or DEVICE_OVERRUN; its file=<n> is its place in its
stream, stretches of unknown signal included. The stop line also gives
outside=<units>, the units outside every file. Every result is printed,
and the exit status is
  1  any result is NOT_BIT_PERFECT or UNKNOWN_SIGNAL (or another verdict),
     or fewer than N results are BIT_PERFECT or DEVICE_OVERRUN with
     --expect N
  4  else any result is DEVICE_OVERRUN: the device's receive buffer
     overflowed and it could not judge the file; run it again
  0  else: at least one result, all BIT_PERFECT
  2  no result line at all: the device did not answer, or no stream
     arrived
  3  the player command failed

--json (run, result, status, monitor) prints each line as one JSON object,
one per line: time, event, the plain words (if any) as "words", and every
key=value field, numbers as numbers. In a result line, diff (seconds into
the file of the first body unit that did not arrive as expected) is a
number, or null for `-`.

`allow-silence true` lets silence outside every file pass without a result
(the device's default); with false it is UNKNOWN_SIGNAL. It sends
`allow_silence <value>`
and prints the answer. The boot, status and stream start lines show the
setting (allow_silence=true or false); in JSON it is true or false, and with
--json `run` adds the setting of each result's stream to it.

`formats` shows the formats the device offers, or with LIST (e.g. 24/3 or
16/2,32/4; <bits>/<bytes>, in alt-setting order) offers those instead. The
device then disconnects and reconnects; the command waits until a host has
it mounted again, and exits 0, or 1 for an invalid list, 2 if no host
mounts it within the timeout.

The port defaults to $BPC_PORT, else /dev/ttyACM0.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

from .port import Port

EXIT_PASS, EXIT_FAIL, EXIT_NO_RESULT, EXIT_COMMAND, EXIT_OVERRUN = 0, 1, 2, 3, 4


# The fields of a result line, in order (firmware/README.md)
RESULT_FIELDS = ("seq", "stream", "file", "verdict", "kind", "dop", "seed", "nch", "file_rate",
                 "depth", "rate", "alt", "slot", "res", "bits", "end", "diff", "lost", "ovf",
                 "dur")


def result_record(line):
    """A result line's fields, in RESULT_FIELDS order (missing ones left out),
    numbers as numbers and diff None for `-`; fields not in the list follow."""
    rec = {}
    for k in RESULT_FIELDS + tuple(k for k in line.fields if k not in RESULT_FIELDS):
        if k in line.fields:
            v = line.fields[k]
            rec[k] = None if k == "diff" and v == "-" else value(v)
    return rec


def stream_of(result):
    """The id of the stream a result belongs to."""
    return result.fields.get("stream", result.fields.get("id"))


class Tracker:
    """Follows the streams that start after a mark and collects their results."""

    def __init__(self, token):
        self.token = token
        self.marked = False
        self.started = set()
        self.stopped = set()      # stage 0: stopped, its result still to come
        self.done = set()
        self.expected = {}        # stream -> results= of its stop line
        self.settings = {}        # stream -> allow_silence of its start line
        self.results = []

    def feed(self, line):
        if line.event == "mark" and line.words == [self.token]:
            self.marked = True
        elif not self.marked:
            return
        elif line.event == "stream" and line.words[:1] == ["start"]:
            self.started.add(line.fields.get("id"))
            if "allow_silence" in line.fields:
                self.settings[line.fields.get("id")] = line.fields["allow_silence"]
        elif line.event == "stream" and line.words[:1] == ["stop"]:
            sid = line.fields.get("id")
            if sid not in self.started:
                return
            if "results" in line.fields:
                self.expected[sid] = int(line.fields["results"])
                self.done.add(sid)
            else:
                self.stopped.add(sid)
        elif line.event == "result" and stream_of(line) in self.started:
            self.results.append(line)
            if stream_of(line) in self.stopped:
                self.done.add(stream_of(line))

    def streaming(self):
        return bool(self.started - self.done)

    def short(self):
        """(stream, results arrived, results expected) where fewer arrived."""
        got = {}
        for r in self.results:
            got[stream_of(r)] = got.get(stream_of(r), 0) + 1
        return [(s, got.get(s, 0), n) for s, n in sorted(self.expected.items())
                if got.get(s, 0) < n]


def exit_code(results, expect=0):
    """The exit status for the results of a run's streams; expect: the
    number of BIT_PERFECT results the run must have at least."""
    if not results:
        return EXIT_NO_RESULT
    verdicts = [r.fields.get("verdict") for r in results]
    if any(v not in ("BIT_PERFECT", "DEVICE_OVERRUN") for v in verdicts):
        return EXIT_FAIL
    if verdicts.count("BIT_PERFECT") + verdicts.count("DEVICE_OVERRUN") < expect:
        return EXIT_FAIL       # a file did not arrive at all (an overrun arrived, unjudged)
    return EXIT_OVERRUN if "DEVICE_OVERRUN" in verdicts else EXIT_PASS


def value(text):
    """A field value as a number when it is one (decimal; 0x-hex stays text)."""
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", text):
        return float(text)
    return text


def as_json(line, extra=None):
    """One log line as a JSON object (extra: fields to add after its own)."""
    obj = {"time": line.time, "event": line.event}
    words = [w for w in line.words if "=" not in w]
    if words:
        obj["words"] = words
    if line.event == "result":
        obj.update(result_record(line))
    else:
        obj.update((k, value(v)) for k, v in line.fields.items())
    if obj.get("allow_silence") in ("true", "false"):
        obj["allow_silence"] = obj["allow_silence"] == "true"
    for k, v in (extra or {}).items():
        obj.setdefault(k, v)
    return json.dumps(obj)


def show(line, json_out):
    print(as_json(line) if json_out else line.text, flush=True)


def run(port, command, timeout, json_out, expect=0):
    token = f"bpcctl-{os.getpid()}-{int(time.time())}"
    tracker = Tracker(token)
    port.send(f"mark {token}")
    deadline = time.monotonic() + 3.0
    while not tracker.marked:
        line = port.readline(max(0.0, deadline - time.monotonic()))
        if line is None:
            print("bpcctl: the device does not answer", file=sys.stderr)
            return EXIT_NO_RESULT
        tracker.feed(line)

    player = subprocess.Popen(command)
    while player.poll() is None:
        line = port.readline(0.2)
        if line is not None:
            tracker.feed(line)

    # The device reports a stream 50 ms after its last packet
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and (tracker.streaming() or not tracker.done):
        line = port.readline(max(0.0, deadline - time.monotonic()))
        if line is not None:
            tracker.feed(line)

    for r in tracker.results:
        setting = tracker.settings.get(stream_of(r))
        extra = {"allow_silence": setting == "true"} if setting is not None else None
        print(as_json(r, extra) if json_out else " ".join(["result"] + r.words))
    for sid, got, want in tracker.short():
        print(f"bpcctl: stream {sid}: {got} of {want} results arrived", file=sys.stderr)
    if player.returncode != 0:
        print(f"bpcctl: the player exited with {player.returncode}", file=sys.stderr)
        return EXIT_COMMAND
    if not tracker.results:
        print("bpcctl: no result", file=sys.stderr)
    bp = sum(1 for r in tracker.results if r.fields.get("verdict") == "BIT_PERFECT")
    if tracker.results and bp < expect:
        print(f"bpcctl: {bp} of {expect} expected results are BIT_PERFECT", file=sys.stderr)
    return exit_code(tracker.results, expect)


def formats(port, wanted, timeout):
    line = port.command(f"formats {wanted}", "formats")
    if line is None:
        print("bpcctl: the device does not answer", file=sys.stderr)
        return EXIT_NO_RESULT
    print(line.text)
    if line.event == "error":
        return EXIT_FAIL
    if line.fields.get("changed") != "1":
        return EXIT_PASS
    deadline = time.monotonic() + timeout
    while True:
        line = port.readline(max(0.0, deadline - time.monotonic()))
        if line is None:
            print("bpcctl: no host mounted the device again", file=sys.stderr)
            return EXIT_NO_RESULT
        if line.event == "usb" and line.words[:1] == ["mounted"]:
            print(line.text)
            return EXIT_PASS


def allow_silence(port, wanted, json_out):
    if wanted not in ("true", "false"):
        print(f"bpcctl: allow-silence: {wanted!r}: use true or false", file=sys.stderr)
        return EXIT_FAIL
    line = port.command(f"allow_silence {wanted}", "allow_silence")
    if line is None:
        print("bpcctl: the device does not answer", file=sys.stderr)
        return EXIT_NO_RESULT
    show(line, json_out)
    return EXIT_FAIL if line.event == "error" else EXIT_PASS


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m bpcctl", description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", default=os.environ.get("BPC_PORT", "/dev/ttyACM0"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("monitor", help="print the log")
    p.add_argument("--json", action="store_true", help="print each line as a JSON object")
    for name in ("status", "version", "result"):
        p = sub.add_parser(name, help=f"send '{name}' and print the answer")
        if name != "version":
            p.add_argument("--json", action="store_true", help="print the answer as JSON")
    p = sub.add_parser("allow-silence", help="whether silence outside every file gives no result")
    p.add_argument("value", choices=("true", "false"))
    p.add_argument("--json", action="store_true", help="print the answer as JSON")
    p = sub.add_parser("formats", help="show or change the formats the device offers")
    p.add_argument("list", nargs="?", help="e.g. 24/3 or 16/2,32/4, or default")
    p.add_argument("--timeout", type=float, default=10.0,
                   help="seconds to wait for a host to mount the device again (default 10)")
    run_help = ("run a player and wait for the results of its streams: exit 1 when any "
                "result is NOT_BIT_PERFECT or UNKNOWN_SIGNAL (or fewer than --expect are "
                "BIT_PERFECT or DEVICE_OVERRUN), else 4 any DEVICE_OVERRUN (not judged: "
                "rerun), else 0 with at "
                "least one BIT_PERFECT; 2 no result, 3 the player failed")
    p = sub.add_parser("run", help=run_help, description=run_help)
    p.add_argument("--timeout", type=float, default=10.0,
                   help="seconds to wait for the result after the player exits (default 10)")
    p.add_argument("--json", action="store_true", help="print each result as a JSON object")
    p.add_argument("--expect", type=int, default=0, metavar="N",
                   help="exit 1 when fewer than N results are BIT_PERFECT or DEVICE_OVERRUN")
    p.add_argument("command", nargs=argparse.REMAINDER, help="the player command, after --")
    args = ap.parse_args(argv)

    with Port(args.port) as port:
        if args.cmd == "monitor":
            try:
                while True:
                    line = port.readline(3600)
                    if line is not None:
                        show(line, args.json)
            except KeyboardInterrupt:
                return 0
        if args.cmd == "allow-silence":
            return allow_silence(port, args.value, args.json)
        if args.cmd == "formats" and args.list:
            return formats(port, args.list, args.timeout)
        if args.cmd in ("status", "version", "result", "formats"):
            line = port.command(args.cmd, args.cmd)
            if line is None:
                print("bpcctl: the device does not answer", file=sys.stderr)
                return EXIT_NO_RESULT
            show(line, getattr(args, "json", False))
            return 0
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            ap.error("run needs a player command after --")
        return run(port, command, args.timeout, args.json, args.expect)


if __name__ == "__main__":
    sys.exit(main())
