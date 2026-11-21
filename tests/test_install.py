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

    def test_failed_download_installs_without_the_helper(self):
        # the helper is optional: no network must not block the whole installation
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('could not download the GPU helper', result.stderr)
        self.assertIn('BOREAS_GPU_ENGINE=', result.stderr)
        self.assertTrue((self.stage / 'usr/lib/boreas/boreas/daemon.py').exists())
        self.assertFalse(self.helper.exists())
        self.assertFalse(self.stamp.exists())

    def test_failed_download_keeps_an_installed_helper(self):
        self.install()
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.helper.read_bytes(), FAKE_HELPER)
        self.assertEqual(self.stamp.read_text().strip(), hashlib.sha256(FAKE_HELPER).hexdigest())

    def test_destdir_without_files_only_is_refused(self):
        # otherwise the files go to DESTDIR while masking and the restart hit this machine
        result = subprocess.run(['sh', str(REPO / 'install.sh')], env=self.env(BOREAS_GPU_ENGINE=str(self.fake)),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--files-only', result.stderr)
        self.assertFalse(self.stage.exists())

    def test_plain_http_download_address_is_refused(self):
        result = self.run_install(BOREAS_GPU_ENGINE_URL='http://example.invalid/boreas')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('https://', result.stderr)
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


class PackagingTests(InstallTestCase):
    """What a distribution package asks of install.sh: no download, a mark that it owns the copy."""

    def test_none_skips_the_helper_without_asking_the_network(self):
        result = self.run_install(BOREAS_GPU_ENGINE='none', BOREAS_GPU_ENGINE_URL='https://invalid.invalid/x')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Downloading', result.stdout)
        self.assertFalse(self.helper.exists())
        self.assertTrue((self.stage / 'usr/bin/boreasd').exists())

    def test_setup_command_is_installed(self):
        self.install()
        setup = self.stage / 'usr/bin/boreas-setup'
        self.assertTrue(os.access(setup, os.X_OK))
        self.assertEqual(setup.read_bytes(), (REPO / 'data/boreas-setup').read_bytes())

    def test_update_helper_is_installed_next_to_the_code(self):
        # update.HELPER is where pkexec starts it; it imports the code from the folder it sits in
        self.install()
        helper = self.stage / 'usr/lib/boreas/boreas-update'
        self.assertEqual(helper.read_bytes(), (REPO / 'data/boreas-update').read_bytes())
        self.assertEqual(oct(helper.stat().st_mode & 0o777), oct(0o755))
        self.assertTrue((helper.parent / 'boreas/update.py').exists())

    def test_packaged_mark_only_when_asked(self):
        mark = self.stage / 'usr/lib/boreas/packaged'
        self.install()
        self.assertFalse(mark.exists())
        self.install(BOREAS_PACKAGED='aur')
        self.assertEqual(mark.read_text(), 'aur\n')
        self.install()                      # a later package build without the mark takes it off
        self.assertFalse(mark.exists())

    def test_plain_install_refuses_a_packaged_copy(self):
        # sudo ./install.sh over an AUR install would take files pacman owns and clear the mark,
        # after which the in-app update writes over the package too
        self.install(BOREAS_PACKAGED='aur')
        mark = self.stage / 'usr/lib/boreas/packaged'
        before = {p: p.stat().st_mtime_ns for p in self.stage.rglob('*')}
        result = subprocess.run(['sh', str(REPO / 'install.sh')], env=self.env(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('package (aur)', result.stderr)
        self.assertEqual(mark.read_text(), 'aur\n')
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.stage.rglob('*')}, before)


class SetupTests(unittest.TestCase):
    """boreas-setup against a fake systemctl: what is masked is recorded and only that comes back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bin = root / 'bin'
        self.bin.mkdir()
        self.state = root / 'state'         # one file per unit: its is-enabled answer
        self.state.mkdir()
        self.log = root / 'calls'
        self.masked = root / 'etc/masked-units'
        self.fake('systemctl', f'''#!/bin/sh
echo "$*" >> "{self.log}"
case "$1" in
    is-enabled) [ -f "{self.state}/$2" ] && cat "{self.state}/$2" || exit 1 ;;
    list-unit-files) [ -f "{self.state}/$3" ] && echo "$3 x" ;;
    mask) echo masked > "{self.state}/$3" ;;
    unmask) echo static > "{self.state}/$2" ;;
esac
exit 0
''')
        self.fake('id', '#!/bin/sh\n[ "$1" = -u ] && echo 0 || echo "wheel users"\n')
        self.fake('getent', '#!/bin/sh\necho wheel:x:998:\n')

    def fake(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def unit(self, name, state):
        (self.state / name).write_text(state + '\n')

    def run_setup(self, *args, path=None):
        env = dict(os.environ, PATH=path or f"{self.bin}:{os.environ['PATH']}",
                   BOREAS_MASKED_UNITS=str(self.masked))
        env.pop('SUDO_USER', None)
        return subprocess.run(['sh', str(REPO / 'data/boreas-setup'), *args], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_enable_masks_what_is_there_and_records_how_it_was(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        self.unit('power-profiles-daemon.service', 'masked')      # the user's own mask
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.masked.read_text().splitlines(),
                         ['asusd.service static', 'supergfxd.service enabled'])
        calls = self.calls()
        self.assertIn('mask --now asusd.service', calls)
        self.assertNotIn('mask --now power-profiles-daemon.service', calls)
        self.assertNotIn('mask --now asus-shutdown.service', calls)   # not installed here
        self.assertEqual(calls[-2:], ['enable boreasd.service', 'restart boreasd.service'])
        self.run_setup('enable')                                   # again: nothing is recorded twice
        self.assertEqual(len(self.masked.read_text().splitlines()), 2)

    def test_disable_brings_back_only_what_enable_masked(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        self.unit('power-profiles-daemon.service', 'masked')
        self.run_setup('enable')
        self.log.unlink()
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertEqual(calls[0], 'disable --now boreasd.service')
        self.assertIn('unmask asusd.service', calls)
        self.assertIn('unmask supergfxd.service', calls)
        self.assertNotIn('unmask power-profiles-daemon.service', calls)
        self.assertIn('enable supergfxd.service', calls)
        self.assertIn('start asusd.service supergfxd.service', calls)
        self.assertFalse(self.masked.exists())

    def test_disable_without_a_record_touches_nothing_else(self):
        self.unit('asusd.service', 'masked')                       # masked by the user, Boreas never enabled
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['disable --now boreasd.service', 'daemon-reload'])

    def test_legacy_disable_takes_every_mask_as_ours(self):
        self.unit('asusd.service', 'masked')
        self.unit('supergfxd.service', 'masked')
        self.run_setup('disable', '--legacy')
        calls = self.calls()
        self.assertIn('unmask asusd.service', calls)
        self.assertIn('enable supergfxd.service', calls)

    def test_a_service_that_does_not_start_is_said_and_how_to_go_back(self):
        self.unit('asusd.service', 'static')
        text = (self.bin / 'systemctl').read_text().replace('exit 0', '[ "$1" = restart ] && exit 1\nexit 0')
        self.fake('systemctl', text)
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 1)
        self.assertIn('boreasd did not start', r.stderr)
        self.assertIn('boreas-setup disable', r.stderr)
        self.assertEqual(self.masked.read_text().splitlines(), ['asusd.service static'])   # disable can undo it
        self.fake('systemctl', text.replace('[ "$1" = restart ] && exit 1\n', ''))
        self.assertEqual(self.run_setup('disable').returncode, 0)
        self.assertIn('unmask asusd.service', self.calls())

    def test_needs_root_and_a_known_action(self):
        self.fake('id', '#!/bin/sh\necho 1000\n')
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 1)
        self.assertIn('sudo boreas-setup enable', r.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_setup().returncode, 2)
        self.assertEqual(self.run_setup('remove').returncode, 2)


class PackagedUpdateTests(unittest.TestCase):
    """A copy a package owns does not update itself."""

    def test_mark_switches_the_in_app_update_off(self):
        import sys
        sys.path.insert(0, str(REPO))
        from unittest.mock import patch
        from boreas import update
        with tempfile.TemporaryDirectory() as d:
            mark = Path(d) / 'packaged'
            with patch.object(update, 'PACKAGED_MARK', str(mark)):
                self.assertIsNone(update.packaged())
                mark.write_text('aur\n')
                self.assertEqual(update.packaged(), 'aur')
                with patch.object(update, 'download_source') as download:
                    with self.assertRaises(RuntimeError) as e:
                        update.run_update('9.9.9')
                    download.assert_not_called()
                self.assertIn('package manager', str(e.exception))
        self.assertEqual(Path(update.PACKAGED_MARK), REPO / 'packaged')   # next to the code, not in it


class WindowSetupTests(unittest.TestCase):
    """boreas.setup: when the window offers to enable Boreas, and what pkexec's answer means."""

    def setUp(self):
        import sys
        sys.path.insert(0, str(REPO))
        from boreas import setup
        self.setup = setup
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.script = Path(self.tmp.name) / 'boreas-setup'
        self.script.write_text('#!/bin/sh\n')

    def run_result(self, code=0, out='', err=''):
        return subprocess.CompletedProcess([], code, out, err)

    def needed(self, state, script=True):
        from unittest.mock import patch
        path = str(self.script) if script else str(self.script) + '.missing'
        with patch.object(self.setup, 'SETUP', path), \
                patch.object(self.setup.subprocess, 'run', return_value=self.run_result(1, state + '\n')):
            return self.setup.needed()

    def test_offered_only_for_an_installed_service_that_was_never_switched_on(self):
        self.assertTrue(self.needed('disabled'))
        self.assertFalse(self.needed('enabled'))
        self.assertFalse(self.needed('masked'))          # switched off on purpose
        self.assertFalse(self.needed('not-found'))
        self.assertFalse(self.needed(''))
        self.assertFalse(self.needed('disabled', script=False))   # installed without boreas-setup

    def enable(self, **kw):
        from unittest.mock import patch
        with patch.object(self.setup.shutil, 'which', return_value='/usr/bin/pkexec'), \
                patch.object(self.setup.subprocess, 'run', return_value=self.run_result(**kw)) as run:
            try:
                return self.setup.enable(), run
            except Exception as e:  # noqa: BLE001 (the test looks at which one)
                return e, run

    def test_enable_runs_the_setup_command_through_pkexec(self):
        result, run = self.enable(out='masking: asusd.service\n')
        self.assertEqual(result, '')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/pkexec', '/usr/bin/boreas-setup', 'enable'])

    def test_pkexec_answers(self):
        self.assertIsInstance(self.enable(code=126)[0], self.setup.SetupCancelled)
        self.assertIsInstance(self.enable(code=127)[0], self.setup.NoAuthorization)
        failed = self.enable(code=1, err='Failed to enable unit: boreasd.service not found\n')[0]
        self.assertIn('boreasd.service not found', str(failed))
        warned = self.enable(out="masking: asusd.service\nWarning: ali is not in the 'wheel' group.\n"
                                 "         sudo usermod -aG wheel ali, then log in again.\n")[0]
        self.assertTrue(warned.startswith('Warning: ali'))
        self.assertIn('usermod', warned)

    def test_no_pkexec_names_the_terminal_command(self):
        from unittest.mock import patch
        with patch.object(self.setup.shutil, 'which', return_value=None):
            with self.assertRaises(RuntimeError) as e:
                self.setup.enable()
        self.assertIn('sudo boreas-setup enable', str(e.exception))

