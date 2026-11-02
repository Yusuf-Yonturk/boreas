"""Staged upgrades preserve locally modified GPU helpers without executing them."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage = Path(self.tmp.name)
        self.helper = self.stage / 'usr/lib/boreas/boreas-gpu-engine'
        self.local = self.stage / 'etc/boreas/boreas-gpu-engine'
        self.stamp = self.helper.with_suffix('.sha256')

    def install(self):
        subprocess.run(['sh', str(REPO / 'install.sh'), '--files-only'],
                       env={**os.environ, 'DESTDIR': str(self.stage)},
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def test_modified_managed_helper_becomes_local_override(self):
        self.install()
        self.helper.write_bytes(b'custom patched helper')
        self.install()
        self.assertEqual(self.local.read_bytes(), b'custom patched helper')
        self.assertEqual(self.stamp.read_text().strip(), hashlib.sha256(self.helper.read_bytes()).hexdigest())

    def test_existing_override_is_never_replaced(self):
        self.install()
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.write_bytes(b'existing override')
        self.helper.write_bytes(b'other patched helper')
        self.install()
        self.assertEqual(self.local.read_bytes(), b'existing override')
        saved = self.stage / 'etc/boreas/preserved-gpu-engines' / hashlib.sha256(b'other patched helper').hexdigest()
        self.assertEqual(saved.read_bytes(), b'other patched helper')

    def test_unknown_legacy_helper_is_backed_up_without_auto_selection(self):
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b'legacy executable')
        self.install()
        self.assertFalse(self.local.exists())
        saved = self.stage / 'etc/boreas/preserved-gpu-engines' / hashlib.sha256(b'legacy executable').hexdigest()
        self.assertEqual(saved.read_bytes(), b'legacy executable')

    def test_unmodified_upgrade_does_not_create_local_override(self):
        self.install()
        self.install()
        self.assertFalse(self.local.exists())
