# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""The firmware version (firmware/cmake/version.cmake) in a small git
repository laid out like this one: v<VERSION>-<commit> at every commit, the
release tag firmware-v<VERSION> included, with git's short hash at its
default length, -dirty for uncommitted changes, v<VERSION>-unknown outside
git; other tags, pre-release tags (firmware-v1.1.0-rc1) included, change
nothing; a firmware tag of another number stops it. Needs CMake and git;
skipped without them."""

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "firmware", "cmake", "version.cmake")
CMAKE = shutil.which("cmake")
GIT = shutil.which("git")
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")


@unittest.skipUnless(CMAKE and GIT, "no cmake or no git")
class Version(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(os.path.realpath(self.tmp.name), "repo")
        os.makedirs(os.path.join(self.repo, "firmware", "cmake"))
        shutil.copy(SCRIPT, os.path.join(self.repo, "firmware", "cmake"))
        self.write("firmware/VERSION", "1.1.0\n")
        self.git("init", "--quiet")
        self.commit("first")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run([GIT, *args], cwd=self.repo, env=ENV, check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, rel, text):
        with open(os.path.join(self.repo, *rel.split("/")), "w") as f:
            f.write(text)

    def commit(self, message):
        self.write("file.txt", message + "\n")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)

    def tag(self, name):
        self.git("tag", "-a", "-m", name, name)

    def head(self):
        """HEAD's short hash at git's default length"""
        return self.git("rev-parse", "--short", "HEAD")

    def version(self, src=None):
        """FW_VERSION from version.cmake, or None when it stops."""
        out = os.path.join(self.tmp.name, "version.h")
        r = subprocess.run([CMAKE, f"-DSRC={src or self.repo}", f"-DOUT={out}", "-P",
                            os.path.join(self.repo, "firmware", "cmake", "version.cmake")],
                           env=ENV, capture_output=True, text=True)
        if r.returncode:
            return None
        with open(out) as f:
            text = f.read()
        prefix = '#define FW_VERSION "'
        self.assertTrue(text.startswith(prefix) and text.endswith('"\n'), text)
        return text[len(prefix):-2]

    def test_no_tag(self):
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")
        self.assertEqual(len(self.head()), 7)

    def test_at_tag_and_after(self):
        """The release's tag adds nothing: its commit as any other."""
        self.tag("firmware-v1.1.0")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")
        self.commit("second")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")

    def test_dirty(self):
        self.write("file.txt", "changed\n")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}-dirty")
        self.tag("firmware-v1.1.0")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}-dirty")
        self.commit("second")
        self.write("file.txt", "changed\n")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}-dirty")

    def test_longer_hash(self):
        """The hash at git's length when git uses more than 7 digits (here
        set by core.abbrev, as git does by itself in a larger repository)."""
        self.git("config", "core.abbrev", "12")
        self.assertEqual(len(self.head()), 12)
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")
        self.tag("firmware-v1.1.0")
        self.write("file.txt", "changed\n")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}-dirty")

    def test_other_tags(self):
        """The test files' tag is not a firmware tag."""
        self.tag("bpc1-r1")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")

    def test_pre_release_tag(self):
        """A pre-release tag changes nothing, at it and after it, also with
        an older release tag before it."""
        self.tag("firmware-v1.1.0-rc1")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")
        self.commit("second")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")
        self.tag("firmware-v1.1.0-rc2")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")

    def test_pre_release_after_release(self):
        self.write("firmware/VERSION", "1.0.0\n")
        self.commit("1.0.0")
        self.tag("firmware-v1.0.0")
        self.write("firmware/VERSION", "1.1.0\n")
        self.commit("1.1.0")
        self.tag("firmware-v1.1.0-rc1")
        self.assertEqual(self.version(), f"v1.1.0-{self.head()}")

    def test_tag_of_another_number(self):
        """At a release tag of another number the build stops, dirty or not;
        after it, with VERSION raised, the version is the next one's."""
        self.tag("firmware-v1.0.0")
        self.assertIsNone(self.version())
        self.write("file.txt", "changed\n")
        self.assertIsNone(self.version())
        self.write("firmware/VERSION", "1.2.0\n")
        self.commit("1.2.0")
        self.assertEqual(self.version(), f"v1.2.0-{self.head()}")

    def test_outside_git(self):
        plain = os.path.join(self.tmp.name, "plain")
        shutil.copytree(os.path.join(self.repo, "firmware"), os.path.join(plain, "firmware"))
        self.assertEqual(self.version(plain), "v1.1.0-unknown")


if __name__ == "__main__":
    unittest.main()
