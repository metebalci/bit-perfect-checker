# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Which u2ps source tree the host tools build against (tests/host_tools.py):
the one U2PS_DIR names when it is set, else the submodule."""

import os
import tempfile
import unittest
from unittest import mock

from tests import host_tools


class U2psDir(unittest.TestCase):
    def test_u2ps_dir_first(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "device"))
            open(os.path.join(d, "device", "u2ps_dev.h"), "w").close()
            with mock.patch.dict(os.environ, {"U2PS_DIR": d}):
                self.assertEqual(host_tools.u2ps_dir(), d)

    def test_u2ps_dir_without_a_tree(self):
        # A U2PS_DIR that names no tree stops the build, not a quiet
        # fallback to the submodule
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"U2PS_DIR": d}):
                self.assertRaises(RuntimeError, host_tools.u2ps_dir)

    @unittest.skipUnless(os.path.exists(os.path.join(host_tools.FW, "lib", "u2ps", "device", "u2ps_dev.h")),
                         "no u2ps submodule checked out")
    def test_submodule_without_u2ps_dir(self):
        env = {k: v for k, v in os.environ.items() if k != "U2PS_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(host_tools.u2ps_dir(), os.path.join(host_tools.FW, "lib", "u2ps"))


if __name__ == "__main__":
    unittest.main()
