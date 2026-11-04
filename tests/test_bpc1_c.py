"""The firmware's C implementation of BPC1 (firmware/bpc1/) against the
golden vectors and the Python checker's model. Needs a host C compiler
(gcc or cc); skipped without one."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from checker import model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BPC1 = os.path.join(ROOT, "firmware", "bpc1")
VECTORS = os.path.join(ROOT, "vectors", "bpc1-v1-vectors.json")
CC = shutil.which("gcc") or shutil.which("cc")


def build_tool(directory, sources, name):
    exe = os.path.join(directory, name)
    cmd = [CC, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", BPC1, "-o", exe]
    cmd += [os.path.join(BPC1, s) for s in sources]
    subprocess.run(cmd, check=True)
    return exe


@unittest.skipUnless(CC, "no host C compiler")
class CTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = build_tool(cls.tmp.name, ["bpc1.c", "host/bpc1_tool.c"], "bpc1_tool")
        with open(VECTORS) as f:
            cls.vec = json.load(f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, queries):
        """Run the tool on a list of query lines; returns its output lines."""
        out = subprocess.run([self.exe], input="\n".join(queries) + "\n", capture_output=True,
                             text=True, check=True).stdout
        return out.splitlines()

    @staticmethod
    def sig_query(v):
        return (f"sig {v['kind']} {v['depth_code']} {v['rate_code']} {v['channels']} {v['seed']} "
                f"{v['lead_blocks']} {v['body_blocks']} {v['total_blocks']}")

    def test_primitives(self):
        p = self.vec["primitives"]
        q, want = [], []
        for e in p["lowbias32"]:
            q.append(f"lb32 {e['key']} {e['n']}")
            want.append(e["result"])
        for e in p["splitmix64_first"]:
            q.append(f"sm64 {e['v']}")
            want.append(e["result"])
        q.append("crc " + b"123456789".hex())
        want.append("0x29B1")
        self.assertEqual([w.upper() for w in self.ask(q)], [w.upper() for w in want])

    def test_channel_keys(self):
        for e in self.vec["primitives"]["channel_key"]:
            depth = model.DEPTHS[e["depth_code"]]
            sig = (f"sig {e['kind']} {e['depth_code']} {e['rate_code']} 2 {e['seed']} 1 1 3"
                   if depth else "")
            out = self.ask([sig, f"key {e['channel']}"])
            self.assertEqual(out, ["ok", e["key"].upper().replace("0X", "0x")])

    def test_pcm_vectors(self):
        for v in self.vec["pcm"]:
            with self.subTest(v["name"]):
                q = [self.sig_query(v)]
                for k in v["blocks"]:
                    q.append(f"sync {k}")
                for s in v["samples"]:
                    q.append(f"pcm {s['first_frame']} {len(s['values'])}")
                out = self.ask(q)
                self.assertEqual(out[0], "ok")
                i = 1
                for k, b in v["blocks"].items():
                    for c in range(v["channels"]):
                        self.assertEqual(out[i].split(), [w.upper().replace("0X", "0x")
                                                          for w in b["sync_words"][c]], f"block {k}")
                        i += 1
                for s in v["samples"]:
                    got = [[int(x) for x in out[i + j].split()] for j in range(len(s["values"]))]
                    self.assertEqual(got, s["values"], s["what"])
                    i += len(s["values"])

    def test_dsd_vectors(self):
        for v in self.vec["dsd"]:
            with self.subTest(v["name"]):
                q = [self.sig_query(v)]
                for s in v["dsd_bytes_msb_first"]:
                    q.append(f"dsd {s['first_bit']} {len(s['hex'][0]) // 2}")
                out = self.ask(q)
                self.assertEqual(out[0], "ok")
                i = 1
                for s in v["dsd_bytes_msb_first"]:
                    for c in range(v["channels"]):
                        self.assertEqual(out[i], s["hex"][c].upper(), s["what"])
                        i += 1

    def test_h0_table(self):
        """T2: every h0 (channel 0 of 2, LEAD_IN): the C engine accepts the
        header words exactly when checker.model does, and only kinds 1, 2
        and 5."""
        h0s = range(1 << 16)
        out = self.ask([f"parse {h0} 0x0201" for h0 in h0s])
        self.assertEqual(len(out), 1 << 16)
        py = [model.Fields.parse(h0, 0x0201, 0x4250, 0x4331, 0).valid() for h0 in h0s]
        self.assertEqual([x == "1" for x in out], py)
        self.assertEqual(sorted({(h0 >> 8) & 15 for h0, ok in zip(h0s, out) if ok == "1"}), [1, 2, 5])

    def test_whole_blocks_against_the_model(self):
        """Every unit of a lead-in, an R-header, a body and the last block, for
        each depth and a DSD kind, against checker.model."""
        cases = [(model.KIND_PCM, 2, 0), (model.KIND_PCM, 3, 3), (model.KIND_PCM, 4, 9),
                 (model.KIND_DSD, 1, 10)]
        lead, body, total = 22, 3, 30
        for kind, depth_code, rate_code in cases:
            with self.subTest(kind=kind, depth_code=depth_code, rate_code=rate_code):
                f = model.Fields(1, kind, depth_code, rate_code, 0, 2, 0, 0x42504331, 0)
                sig = model.Signal(f, lead, body, total)
                q = [f"sig {kind} {depth_code} {rate_code} 2 0x42504331 {lead} {body} {total}"]
                U = sig.units_per_block
                blocks = (0, 8, 23, 24, total - 1)
                for k in blocks:
                    q.append(f"dsd {k * U * 8} {U}" if sig.dsd else f"pcm {k * U} {U}")
                out = self.ask(q)
                self.assertEqual(out[0], "ok")
                i = 1
                for k in blocks:
                    ref = sig.block(k)
                    if sig.dsd:
                        got = np.array([bytes.fromhex(out[i + c]) for c in range(2)])
                        got = np.frombuffer(b"".join(got), dtype=np.uint8).reshape(2, -1).T
                        i += 2
                        np.testing.assert_array_equal(got, ref, f"block {k}")
                    else:
                        got = np.array([[int(x) for x in out[i + j].split()] for j in range(U)])
                        i += U
                        want = ref.astype(np.int64) >> (32 - sig.depth)
                        np.testing.assert_array_equal(got, want, f"block {k}")


if __name__ == "__main__":
    unittest.main()
