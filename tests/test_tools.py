"""Tests for the helper scripts under tools/ that the release job runs."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)  # run from tests/ too
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


class TestReviewedInstall(unittest.TestCase):
    def test_packaged_copy_is_refused_before_staging_or_live_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            commands = root / 'bin'
            commands.mkdir()
            log = root / 'calls'
            # Stop at the first side effect if the guard regresses; never run the live installer.
            for name in ('mktemp', 'sh', 'systemctl', 'rm', 'install', 'cp', 'python3', 'boreasctl'):
                command = commands / name
                command.write_text('#!/bin/sh\necho "${0##*/} $*" >> "$CALL_LOG"\nexit 97\n')
                command.chmod(0o755)
            (commands / 'id').write_text('#!/bin/sh\necho 0\n')
            (commands / 'id').chmod(0o755)
            mark = root / 'packaged'
            mark.write_text('aur\n')
            env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}",
                       CALL_LOG=str(log), BOREAS_PACKAGED_MARK=str(mark))
            result = subprocess.run(['/bin/sh', os.path.join(ROOT, 'tools', 'install-reviewed.sh')],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('package manager', result.stderr)
            self.assertFalse(log.exists(), log.read_text() if log.exists() else '')
            self.assertEqual(mark.read_text(), 'aur\n')


if __name__ == "__main__":
    unittest.main()
