# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Builds the host tools of firmware/app/host/CMakeLists.txt, which run the
checker's USB code against u2ps's PC test kit (u2ps AGENTS.md section 7):
the u2ps device stack with a fake port, and the kit playing the host. Needs
CMake and a host C compiler; u2ps from the folder the environment variable
U2PS_DIR names, else from firmware/lib/u2ps."""

import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware")
HOST = os.path.join(FW, "app", "host")

CC = shutil.which("gcc") or shutil.which("cc")
CMAKE = shutil.which("cmake")
AVAILABLE = CC is not None and CMAKE is not None


def u2ps_dir():
    """The u2ps source tree: U2PS_DIR when set, else the submodule."""
    d = os.environ.get("U2PS_DIR", "")
    if d:
        if not os.path.exists(os.path.join(d, "device", "u2ps_dev.h")):
            raise RuntimeError("U2PS_DIR names no u2ps source tree (no device/u2ps_dev.h): " + d)
        return d
    d = os.path.join(FW, "lib", "u2ps")
    if os.path.exists(os.path.join(d, "device", "u2ps_dev.h")):
        return d
    raise RuntimeError("no u2ps source tree: firmware/lib/u2ps is empty and U2PS_DIR is not set")


def build(directory, targets):
    """Configures and builds the host tools named in targets in directory;
    returns {target: path of its executable}."""
    subprocess.run([CMAKE, "-S", HOST, "-B", directory, "-DU2PS_DIR=" + u2ps_dir(),
                    "-DCMAKE_BUILD_TYPE=RelWithDebInfo"], check=True, capture_output=True)
    for t in targets:
        r = subprocess.run([CMAKE, "--build", directory, "--target", t, "-j", "8"], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("building %s failed:\n%s%s" % (t, r.stdout[-4000:], r.stderr[-4000:]))
    return {t: os.path.join(directory, t) for t in targets}
