"""Tests for the helper scripts under tools/ that the release job runs."""

import importlib.util
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(ROOT, "tools", "release-notes.py")

spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
release_notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_notes)

LOG = """# Changelog

Newest version first.

## Unreleased

- not out yet

## 0.1.10

- ten

## 0.1.1

- one
- **two** with `## not a heading`
"""


class TestReleaseNotes(unittest.TestCase):
    def test_section_of_a_version(self):
        self.assertEqual(release_notes.section(LOG, "0.1.10"), "- ten")
        self.assertEqual(release_notes.section(LOG, "v0.1.1"), "- one\n- **two** with `## not a heading`")

    def test_a_version_is_not_matched_by_its_prefix(self):
        self.assertEqual(release_notes.section(LOG, "0.1"), "")
        self.assertEqual(release_notes.section(LOG, "0.1.100"), "")

    def test_unknown_version_fails(self):
        r = subprocess.run([sys.executable, SCRIPT, "v99.0.0"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")

    def test_the_current_version_has_notes_or_is_unreleased(self):
        """The release job stops without notes, so a version bump must come with its changelog section."""
        from boreas import __version__
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(release_notes.section(text, __version__), __version__)


if __name__ == "__main__":
    unittest.main()
