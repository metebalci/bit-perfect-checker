# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The serial-log client: line parsing, result tracking, exit codes."""

import contextlib
import io
import json
import sys
import unittest

from bpcctl.__main__ import (EXIT_COMMAND, EXIT_FAIL, EXIT_NO_RESULT, EXIT_OVERRUN, EXIT_PASS,
                             RESULT_FIELDS, Tracker, allow_silence, as_json, exit_code,
                             result_record, run)
from bpcctl.port import Line


class LineTest(unittest.TestCase):
    def test_fields(self):
        line = Line("   12.345 result id=3 verdict=UNCHECKED rate=96000 lost=0")
        self.assertEqual(line.time, 12.345)
        self.assertEqual(line.event, "result")
        self.assertEqual(line.fields, {"id": "3", "verdict": "UNCHECKED", "rate": "96000", "lost": "0"})

    def test_words(self):
        line = Line("    0.001 stream start id=1 rate=44100")
        self.assertEqual(line.event, "stream")
        self.assertEqual(line.words[0], "start")
        self.assertEqual(line.fields["id"], "1")

    def test_no_timestamp(self):
        line = Line("garbage")
        self.assertIsNone(line.time)
        self.assertEqual(line.event, "garbage")


def feed(tracker, *texts):
    for t in texts:
        tracker.feed(Line(t))


class TrackerTest(unittest.TestCase):
    def test_only_streams_after_the_mark(self):
        t = Tracker("tok")
        feed(t,
             "1.000 stream start id=1",
             "2.000 result seq=1 stream=1 file=1 verdict=BIT_PERFECT units=10",
             "3.000 mark tok",
             "4.000 stream start id=2",
             "5.000 stat id=2",
             "5.800 result seq=2 stream=2 file=1 verdict=NOT_BIT_PERFECT units=20",
             "5.900 stream stop id=2 reason=idle results=1")
        self.assertEqual([r.fields["seq"] for r in t.results], ["2"])
        self.assertFalse(t.streaming())

    def test_done_at_stream_stop(self):
        t = Tracker("tok")
        feed(t, "1.0 mark tok", "2.0 stream start id=7",
             "3.0 result seq=1 stream=7 file=1 verdict=BIT_PERFECT units=1000")
        self.assertTrue(t.streaming(), "a result before the stream stops does not end it")
        feed(t, "3.1 result seq=2 stream=7 file=2 verdict=NOT_BIT_PERFECT units=5000")
        self.assertTrue(t.streaming())
        feed(t, "4.0 stream stop id=7 reason=idle results=2")
        self.assertFalse(t.streaming())
        self.assertEqual([r.fields["file"] for r in t.results], ["1", "2"])
        self.assertEqual(t.short(), [])

    def test_fewer_results_than_announced(self):
        t = Tracker("tok")
        feed(t, "1.0 mark tok", "2.0 stream start id=3",
             "3.0 result seq=4 stream=3 file=1 verdict=BIT_PERFECT units=1000",
             "4.0 stream stop id=3 reason=idle results=3")
        self.assertFalse(t.streaming())
        self.assertEqual(t.short(), [("3", 1, 3)])

    def test_stage0_result_after_stop(self):
        t = Tracker("tok")
        feed(t, "1.0 mark tok", "2.0 stream start id=5", "3.0 stream stop id=5 reason=idle")
        self.assertTrue(t.streaming(), "a stop without results= waits for its result")
        feed(t, "3.1 result id=5 verdict=UNCHECKED frames=1")
        self.assertFalse(t.streaming())

    def test_matched_by_stream_not_by_id(self):
        t = Tracker("tok")
        feed(t,
             "1.0 stream start id=1",
             "2.0 mark tok",
             "3.0 stream start id=2",
             "4.0 result seq=2 stream=1 file=1 verdict=NOT_BIT_PERFECT units=10",
             "4.1 stream stop id=1 reason=idle results=1",
             "5.0 result seq=3 stream=2 file=1 verdict=BIT_PERFECT units=10",
             "5.1 stream stop id=2 reason=idle results=1")
        self.assertEqual([r.fields["seq"] for r in t.results], ["3"])
        self.assertFalse(t.streaming())

    def test_other_mark_ignored(self):
        t = Tracker("tok")
        feed(t, "1.0 mark other", "2.0 stream start id=1", "3.0 result stream=1 units=1")
        self.assertEqual(t.results, [])


def results(*verdicts):
    return [Line(f"1.0 result seq={i} stream=1 file={i} verdict={v}")
            for i, v in enumerate(verdicts, 1)]


class ExitCodeTest(unittest.TestCase):
    def test_every_result_counts(self):
        self.assertEqual(exit_code([]), EXIT_NO_RESULT)
        self.assertEqual(exit_code(results("BIT_PERFECT")), EXIT_PASS)
        self.assertEqual(exit_code(results("BIT_PERFECT", "BIT_PERFECT")), EXIT_PASS)
        self.assertEqual(exit_code(results("BIT_PERFECT", "NOT_BIT_PERFECT")), EXIT_FAIL)
        self.assertEqual(exit_code(results("NOT_BIT_PERFECT", "BIT_PERFECT")), EXIT_FAIL)
        self.assertEqual(exit_code(results("UNCHECKED")), EXIT_FAIL)

    def test_unknown_signal_fails(self):
        # an UNKNOWN_SIGNAL result is a fail even when a file passed: a
        # rounded file with no X-sync left must not pass the run
        self.assertEqual(exit_code(results("UNKNOWN_SIGNAL", "BIT_PERFECT")), EXIT_FAIL)
        self.assertEqual(exit_code(results("BIT_PERFECT", "UNKNOWN_SIGNAL")), EXIT_FAIL)
        self.assertEqual(exit_code(results("UNKNOWN_SIGNAL")), EXIT_FAIL)
        self.assertEqual(exit_code(results("NO_SIGNAL", "BIT_PERFECT")), EXIT_FAIL)

    def test_fail_before_overrun(self):
        self.assertEqual(exit_code(results("OVERRUN")), EXIT_OVERRUN)
        self.assertEqual(exit_code(results("BIT_PERFECT", "OVERRUN")), EXIT_OVERRUN)
        self.assertEqual(exit_code(results("NOT_BIT_PERFECT", "OVERRUN", "BIT_PERFECT")),
                         EXIT_FAIL)
        self.assertEqual(exit_code(results("UNKNOWN_SIGNAL", "OVERRUN")), EXIT_FAIL)

    def test_incomplete_reporting(self):
        # results lost, fewer than announced or a stream unfinished: 4, after 1
        self.assertEqual(exit_code(results("BIT_PERFECT"), incomplete=True), EXIT_OVERRUN)
        self.assertEqual(exit_code(results("BIT_PERFECT", "NOT_BIT_PERFECT"), incomplete=True),
                         EXIT_FAIL)
        self.assertEqual(exit_code(results("BIT_PERFECT"), expect=2, incomplete=True), EXIT_FAIL)
        self.assertEqual(exit_code(results("BIT_PERFECT"), expect=1, incomplete=True), EXIT_OVERRUN)
        self.assertEqual(exit_code([], incomplete=True), EXIT_NO_RESULT)

    def test_expect(self):
        self.assertEqual(exit_code(results("BIT_PERFECT", "BIT_PERFECT"), expect=2), EXIT_PASS)
        self.assertEqual(exit_code(results("BIT_PERFECT"), expect=2), EXIT_FAIL)
        # an overrun is a file that arrived but was not judged: it counts toward N
        self.assertEqual(exit_code(results("BIT_PERFECT", "OVERRUN"), expect=2), EXIT_OVERRUN)
        self.assertEqual(exit_code(results("BIT_PERFECT", "BIT_PERFECT", "OVERRUN"), expect=3),
                         EXIT_OVERRUN)
        self.assertEqual(exit_code(results("BIT_PERFECT", "OVERRUN"), expect=3), EXIT_FAIL)
        self.assertEqual(exit_code([], expect=2), EXIT_NO_RESULT)


class JsonTest(unittest.TestCase):
    def test_line_as_json(self):
        obj = json.loads(as_json(Line("12.5 result seq=3 stream=2 file=1 verdict=BIT_PERFECT "
                                      "seed=0x42504331 chmap=1,0 missing=none dur=10.032 bits=24")))
        self.assertEqual(obj, {"time": 12.5, "event": "result", "seq": 3, "stream": 2, "file": 1,
                               "verdict": "BIT_PERFECT", "seed": "0x42504331", "chmap": "1,0",
                               "missing": "none", "dur": 10.032, "bits": 24})

    def test_result_line(self):
        text = ("12.5 result seq=3 stream=2 file=1 verdict=NOT_BIT_PERFECT kind=PCM dop=0 "
                "seed=0x42504331 nch=2 file_rate=48000 depth=24 rate=48000 alt=2 slot=4 res=24 "
                "bits=24 end=LAST diff=3.141 lost=0 ovf=0 dur=10.032")
        rec = result_record(Line(text))
        self.assertEqual(tuple(rec), RESULT_FIELDS)
        self.assertEqual((rec["verdict"], rec["diff"], rec["seed"], rec["file_rate"]),
                         ("NOT_BIT_PERFECT", 3.141, "0x42504331", 48000))
        obj = json.loads(as_json(Line(text)))
        self.assertEqual(list(obj)[2:], list(RESULT_FIELDS))
        self.assertEqual(obj["diff"], 3.141)

    def test_result_without_diff(self):
        for text in ("1.0 result seq=1 stream=1 file=1 verdict=BIT_PERFECT kind=PCM end=LAST diff=-",
                     "1.0 result seq=2 stream=1 file=1 verdict=UNKNOWN_SIGNAL kind=NONE seed=0 nch=0 "
                     "file_rate=0 depth=0 end=STOP diff=-"):
            with self.subTest(text=text):
                obj = json.loads(as_json(Line(text)))
                self.assertIsNone(obj["diff"])
                self.assertIn("diff", obj)

    def test_diff_dash_only_in_results(self):
        self.assertEqual(json.loads(as_json(Line("1.0 stat id=1 diff=-")))["diff"], "-")

    def test_plain_words(self):
        obj = json.loads(as_json(Line("0.001 stream stop id=1 reason=idle results=2")))
        self.assertEqual(obj, {"time": 0.001, "event": "stream", "words": ["stop"], "id": 1,
                               "reason": "idle", "results": 2})
        self.assertEqual(json.loads(as_json(Line("1.0 x v=inf w=1_0")))["v"], "inf")


class FakePort:
    """Echoes the mark, then hands out the given log lines one per read."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.lines = []

    def send(self, text):
        self.lines += [Line("0.5 " + text)] + [Line(t) for t in self.texts]

    def readline(self, timeout):
        return self.lines.pop(0) if self.lines else None


class CommandPort:
    """Answers one command with the given line."""

    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def command(self, command, event, timeout=2.0):
        self.sent.append(command)
        return Line(self.answer) if self.answer else None


class AllowSilenceTest(unittest.TestCase):
    def call(self, wanted, answer, json_out=False):
        port, out, err = CommandPort(answer), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = allow_silence(port, wanted, json_out)
        return code, port.sent, out.getvalue(), err.getvalue()

    def test_set(self):
        code, sent, out, _ = self.call("true", "1.0 allow_silence true")
        self.assertEqual((code, sent), (EXIT_PASS, ["allow_silence true"]))
        self.assertIn("allow_silence true", out)
        code, sent, out, _ = self.call("false", "1.0 allow_silence false", json_out=True)
        self.assertEqual((code, sent), (EXIT_PASS, ["allow_silence false"]))
        self.assertEqual(json.loads(out)["words"], ["false"])

    def test_invalid_list_not_sent(self):
        for wanted in ("yes", "start", "True"):
            with self.subTest(wanted=wanted):
                code, sent, _, err = self.call(wanted, "1.0 allow_silence false")
                self.assertEqual((code, sent), (EXIT_FAIL, []))
                self.assertIn("allow-silence", err)

    def test_device_error_and_no_answer(self):
        self.assertEqual(self.call("true", "1.0 error unknown command")[0], EXIT_FAIL)
        self.assertEqual(self.call("true", None)[0], EXIT_NO_RESULT)

    def test_setting_in_json(self):
        obj = json.loads(as_json(Line("0.1 boot fw=v1 board=nucleo usb=1209:0001 "
                                      "allow_silence=true")))
        self.assertIs(obj["allow_silence"], True)
        obj = json.loads(as_json(Line("0.1 status formats=24/4 allow_silence=false")))
        self.assertIs(obj["allow_silence"], False)
        obj = json.loads(as_json(Line("2.0 stream stop id=3 reason=idle lost=0 ovf=0 outside=10 "
                                      "silent=10 results=1")))
        self.assertEqual((obj["outside"], obj["silent"], obj["results"]), (10, 10, 1))


class RunTest(unittest.TestCase):
    def run_with(self, *texts, player="pass", json_out=False, expect=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run(FakePort(*texts), [sys.executable, "-c", player], 0.5, json_out, expect)
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_every_file_must_pass(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "2.1 result seq=9 stream=4 file=2 verdict=NOT_BIT_PERFECT units=4000",
            "3.0 stream stop id=4 reason=idle results=2")
        self.assertEqual((code, len(out)), (EXIT_FAIL, 2))

    def test_two_streams(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "3.0 stream stop id=4 reason=idle results=1",
            "4.0 stream start id=5",
            "5.1 result seq=10 stream=5 file=1 verdict=BIT_PERFECT units=100",
            "6.0 stream stop id=5 reason=idle results=1")
        self.assertEqual((code, len(out)), (EXIT_PASS, 2))

    def test_json_carries_the_setting(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4 rate=48000 alt=2 slot=4 res=24 allow_silence=true",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT kind=PCM end=LAST diff=-",
            "3.0 stream stop id=4 reason=idle outside=100 silent=100 results=1", json_out=True)
        obj = json.loads(out[0])
        self.assertEqual((code, obj["verdict"], obj["allow_silence"]),
                         (EXIT_PASS, "BIT_PERFECT", True))

    def test_no_result_line(self):
        code, out, err = self.run_with("1.0 stream start id=4",
                                       "3.0 stream stop id=4 reason=idle results=0")
        self.assertEqual((code, out), (EXIT_NO_RESULT, []))
        self.assertIn("no result", err)

    def test_silence_only_is_unknown_signal(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4",
            "3.0 result seq=1 stream=4 file=1 verdict=UNKNOWN_SIGNAL kind=NONE end=STOP diff=-",
            "3.1 stream stop id=4 reason=idle lost=0 ovf=0 outside=48000 results=1")
        self.assertEqual((code, len(out)), (EXIT_FAIL, 1))

    def test_expect_counts_bit_perfect_results(self):
        lines = ("1.0 stream start id=4",
                 "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
                 "3.0 stream stop id=4 reason=idle results=1")
        self.assertEqual(self.run_with(*lines, expect=1)[0], EXIT_PASS)
        code, _, err = self.run_with(*lines, expect=2)
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("1 of 2", err)

    def test_overrun_says_rerun(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "2.1 result seq=9 stream=4 file=2 verdict=OVERRUN units=4000",
            "3.0 stream stop id=4 reason=idle results=2")
        self.assertEqual(code, EXIT_OVERRUN)
        self.assertEqual(len(out), 2)

    def test_missing_results_not_judged(self):
        """Fewer result lines than the stop line's results=: exit 4, play
        again, unless a result fails."""
        code, _, err = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "3.0 stream stop id=4 reason=idle results=2")
        self.assertEqual(code, EXIT_OVERRUN)
        self.assertIn("stream 4: 1 of 2 results arrived", err)
        code, _, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=NOT_BIT_PERFECT units=100",
            "3.0 stream stop id=4 reason=idle results=2")
        self.assertEqual(code, EXIT_FAIL)

    def test_results_lost_not_judged(self):
        """A stream whose results the device dropped (results_lost above 0):
        exit 4, unless a result fails or --expect is not met."""
        lines = ("1.0 stream start id=4",
                 "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
                 "3.0 stream stop id=4 reason=idle results=1 results_lost=2")
        code, _, err = self.run_with(*lines)
        self.assertEqual(code, EXIT_OVERRUN)
        self.assertIn("stream 4: the device dropped 2 results", err)
        self.assertEqual(self.run_with(*lines, expect=2)[0], EXIT_FAIL)
        self.assertEqual(self.run_with(*lines[:2], "3.0 stream stop id=4 reason=idle results=1 "
                                       "results_lost=0")[0], EXIT_PASS)
        code, _, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=UNKNOWN_SIGNAL units=100",
            "3.0 stream stop id=4 reason=idle results=1 results_lost=1")
        self.assertEqual(code, EXIT_FAIL)

    def test_unfinished_stream_not_judged(self):
        """The timeout ends the wait with a stream that did not stop: exit 4."""
        code, out, err = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100")
        self.assertEqual((code, len(out)), (EXIT_OVERRUN, 1))
        self.assertIn("stream 4 did not stop", err)
        code, _, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "3.0 stream stop id=4 reason=idle results=1",
            "4.0 stream start id=5")
        self.assertEqual(code, EXIT_OVERRUN)

    def test_precedence(self):
        """3 (the player failed) before 2 (no result) before 1 before 4 before 0."""
        fail = ("1.0 stream start id=4",
                "2.0 result seq=8 stream=4 file=1 verdict=NOT_BIT_PERFECT units=100",
                "3.0 stream stop id=4 reason=idle results=1")
        self.assertEqual(self.run_with(*fail, player="raise SystemExit(5)")[0], EXIT_COMMAND)
        self.assertEqual(self.run_with(player="raise SystemExit(5)")[0], EXIT_COMMAND)
        self.assertEqual(self.run_with("1.0 stream start id=4",
                                       "3.0 stream stop id=4 reason=idle results=1 results_lost=1")[0],
                         EXIT_NO_RESULT)
        self.assertEqual(self.run_with(*fail)[0], EXIT_FAIL)

    def test_no_result(self):
        code, _, err = self.run_with("1.0 stream start id=4",
                                     "3.0 stream stop id=4 reason=idle results=0")
        self.assertEqual(code, EXIT_NO_RESULT)
        self.assertEqual(self.run_with()[0], EXIT_NO_RESULT)

    def test_player_failed(self):
        code, _, err = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "3.0 stream stop id=4 reason=idle results=1", player="raise SystemExit(5)")
        self.assertEqual(code, EXIT_COMMAND)
        self.assertIn("exited with 5", err)

    def test_json(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 result seq=8 stream=4 file=1 verdict=BIT_PERFECT units=100",
            "3.0 stream stop id=4 reason=idle results=1", json_out=True)
        self.assertEqual(code, EXIT_PASS)
        self.assertEqual([json.loads(x) for x in out],
                         [{"time": 2.0, "event": "result", "seq": 8, "stream": 4, "file": 1,
                           "verdict": "BIT_PERFECT", "units": 100}])

    def test_old_firmware_result(self):
        code, out, _ = self.run_with(
            "1.0 stream start id=4",
            "2.0 stream stop id=4 reason=idle",
            "2.1 result id=4 verdict=BIT_PERFECT frames=480")
        self.assertEqual((code, out), (EXIT_PASS, ["result id=4 verdict=BIT_PERFECT frames=480"]))


if __name__ == "__main__":
    unittest.main()
