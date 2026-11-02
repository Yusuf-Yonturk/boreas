"""install.sh resolves and verifies the GPU helper before writing anything, and staged
upgrades preserve locally modified helpers without executing them. No test uses the network:
the helper comes from BOREAS_GPU_ENGINE, a data/bin copy, or a file:// release directory."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
FAKE_HELPER = b'#!/bin/sh\necho fake helper\n'


class InstallTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage = Path(self.tmp.name) / 'stage'
        self.helper = self.stage / 'usr/lib/boreas/boreas-gpu-engine'
        self.local = self.stage / 'etc/boreas/boreas-gpu-engine'
        self.stamp = self.helper.with_suffix('.sha256')
        self.fake = Path(self.tmp.name) / 'fake-boreas-gpu-engine'
        self.fake.write_bytes(FAKE_HELPER)
        # An empty file:// release directory: any download attempt fails instead of reaching GitHub.
        self.releases = Path(self.tmp.name) / 'releases'
        self.releases.mkdir()

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith('BOREAS_GPU_ENGINE')}
        env.update(DESTDIR=str(self.stage), BOREAS_GPU_ENGINE_URL=self.releases.as_uri())
        env.update(extra)
        return env

    def run_install(self, script=None, **extra):
        return subprocess.run(['sh', str(script or REPO / 'install.sh'), '--files-only'],
                              env=self.env(**extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def install(self, **extra):
        extra.setdefault('BOREAS_GPU_ENGINE', str(self.fake))
        result = self.run_install(**extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result


class HelperSourceTests(InstallTestCase):
    def test_explicit_helper_is_installed_with_stamp(self):
        self.install()
        self.assertEqual(self.helper.read_bytes(), FAKE_HELPER)
        self.assertTrue(os.access(self.helper, os.X_OK))
        self.assertEqual(self.stamp.read_text().strip(), hashlib.sha256(FAKE_HELPER).hexdigest())

    def test_missing_explicit_helper_aborts_before_writing(self):
        result = self.run_install(BOREAS_GPU_ENGINE=str(self.fake) + '.missing')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BOREAS_GPU_ENGINE', result.stderr)
        self.assertFalse((self.stage / 'usr').exists())

    def test_data_bin_helper_is_used_without_download(self):
        # A copy of the checkout that carries data/bin/boreas-gpu-engine, as a release tarball would.
        src = Path(self.tmp.name) / 'src'
        for rel in ('install.sh', 'THIRD_PARTY_NOTICES.md', 'gpu-engine/vendor/LICENSE.cardwire'):
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, src / rel)
        shutil.copytree(REPO / 'boreas', src / 'boreas', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(REPO / 'data', src / 'data', ignore=shutil.ignore_patterns('boreas-gpu-engine*'))
        (src / 'data/bin/boreas-gpu-engine').write_bytes(b'tarball helper')
        result = self.run_install(script=src / 'install.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.helper.read_bytes(), b'tarball helper')
        self.assertNotIn('Downloading', result.stdout)

    def test_downloaded_helper_is_verified_and_installed(self):
        (self.releases / 'boreas-gpu-engine').write_bytes(FAKE_HELPER)
        digest = hashlib.sha256(FAKE_HELPER).hexdigest()
        (self.releases / 'boreas-gpu-engine.sha256').write_text(f'{digest}  boreas-gpu-engine\n')
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Downloading the GPU helper for Boreas', result.stdout)
        self.assertEqual(self.helper.read_bytes(), FAKE_HELPER)
        self.assertEqual(self.stamp.read_text().strip(), digest)

    def test_checksum_mismatch_aborts_before_writing(self):
        (self.releases / 'boreas-gpu-engine').write_bytes(FAKE_HELPER)
        (self.releases / 'boreas-gpu-engine.sha256').write_text('0' * 64 + '  boreas-gpu-engine\n')
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse((self.stage / 'usr/lib/boreas').exists())
        self.assertFalse(self.stage.exists())

    def test_failed_download_aborts_with_offline_hint(self):
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Could not download the GPU helper', result.stderr)
        self.assertIn('BOREAS_GPU_ENGINE=', result.stderr)
        self.assertFalse(self.stage.exists())

    def test_default_download_url_follows_the_package_version(self):
        # Checked statically: the default URL must never be fetched from a test.
        script = (REPO / 'install.sh').read_text()
        self.assertIn('https://github.com/Yusuf-Yonturk/boreas/releases/download/v$version', script)
        self.assertIn('"$SRC/boreas/__init__.py"', script)


class PreservationTests(InstallTestCase):
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
