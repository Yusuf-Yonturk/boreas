"""install.sh resolves and verifies the GPU helper before writing anything, and staged
upgrades preserve locally modified helpers without executing them. No test uses the network:
the helper comes from BOREAS_GPU_ENGINE, a data/bin copy, or a file:// release directory."""
import hashlib
import os
from pathlib import Path
import re
import shlex
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

    def test_arch_packages_require_qt6_svg(self):
        for package in ('boreas', 'boreas-git'):
            with self.subTest(package=package):
                text = (REPO / f'packaging/aur/{package}/PKGBUILD').read_text()
                depends = re.search(r'^depends=\((.*?)\)', text, re.M | re.S)
                self.assertIsNotNone(depends)
                self.assertIn('qt6-svg', shlex.split(depends.group(1)))

    def test_ui_dependency_warnings_distinguish_pyside6_and_qtsvg(self):
        text = (REPO / 'install.sh').read_text()
        warnings = re.search(
            r'if ! python3 -c "import PySide6" 2>/dev/null; then\n(?P<pyside6>.*?)'
            r'elif ! python3 -c "import PySide6\.QtSvg" 2>/dev/null; then\n(?P<qtsvg>.*?)fi\n',
            text, re.S)
        self.assertIsNotNone(warnings, 'PySide6 and QtSvg need separate dependency checks')
        self.assertIn('Warning: the UI needs PySide6', warnings.group('pyside6'))
        self.assertIn('pacman -S pyside6', warnings.group('pyside6'))
        self.assertIn('Warning: the UI needs QtSvg', warnings.group('qtsvg'))
        self.assertIn('pacman -S qt6-svg', warnings.group('qtsvg'))

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


class InstallConfigTests(unittest.TestCase):
    def test_language_config_is_readable_under_restrictive_umask(self):
        text = (REPO / 'install.sh').read_text()
        language = text.split('CONF=/etc/xdg/boreas/boreas.conf\n', 1)[1].split('# UI dependencies', 1)[0]
        # Run only the language section, with both system paths redirected into a temporary root.
        language = 'CONF="$TEST_CONF"\n' + language.replace(
            'install -d /etc/xdg/boreas', 'install -d "$(dirname "$CONF")"')
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as d:
                conf = Path(d) / 'xdg/boreas/boreas.conf'
                if existing:
                    conf.parent.mkdir(parents=True)
                    conf.write_text('[General]\nlanguage=en\n')
                    conf.chmod(0o600)
                env = dict(os.environ, TEST_CONF=str(conf), BOREAS_LANG='tr')
                result = subprocess.run(['sh', '-c', 'set -e\numask 077\n' + language], env=env,
                                        input='', capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(conf.read_text(), '[General]\nlanguage=tr\n')
                self.assertEqual(oct(conf.stat().st_mode & 0o777), '0o644')


class UninstallTests(unittest.TestCase):
    def test_packaged_copy_is_refused_before_services_or_files_change(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            commands = root / 'bin'
            commands.mkdir()
            log = root / 'calls'
            for name, body in (
                    ('id', 'echo 0\n'),
                    ('systemctl', 'echo "systemctl $*" >> "$CALL_LOG"\n'),
                    ('rm', 'echo "rm $*" >> "$CALL_LOG"\n')):
                command = commands / name
                command.write_text('#!/bin/sh\n' + body)
                command.chmod(0o755)
            mark = root / 'packaged'
            mark.write_text('aur\n')
            env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}",
                       CALL_LOG=str(log), BOREAS_PACKAGED_MARK=str(mark),
                       BOREAS_MASKED_UNITS=str(root / 'masked-units'))
            result = subprocess.run(['sh', str(REPO / 'uninstall.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('package manager', result.stderr)
            self.assertIn('pacman -R', result.stderr)
            self.assertFalse(log.exists(), log.read_text() if log.exists() else '')
            self.assertEqual(mark.read_text(), 'aur\n')


UPDATE_FROM_OLD = 'update'   # a step: install.sh over a 0.1.6 copy (adopt, then enable --keep-off)


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
        self.installed = root / 'usr/bin/boreas-setup'   # the copy already installed, if any
        self.masked = root / 'etc/masked-units'
        self.off = self.masked.parent / 'switched-off'
        self.fake('systemctl', f'''#!/bin/sh
echo "$*" >> "{self.log}"
case "$1" in
    is-enabled) [ -f "{self.state}/$2" ] || exit 1
                cat "{self.state}/$2"
                case "$(cat "{self.state}/$2")" in enabled|static) exit 0 ;; *) exit 1 ;; esac ;;
    list-unit-files) [ -f "{self.state}/$3" ] && echo "$3 x" ;;
    mask) [ ! -f "{self.state}/fail-mask-$3" ] || exit 1
          echo masked > "{self.state}/$3"
          [ ! -f "{self.state}/fail-stop-$3" ] || exit 1 ;;
    unmask) echo static > "{self.state}/$2" ;;
    enable) shift; for unit do echo enabled > "{self.state}/$unit"; done ;;
    disable) echo disabled > "{self.state}/$3" ;;
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
                   BOREAS_MASKED_UNITS=str(self.masked), BOREAS_INSTALLED_SETUP=str(self.installed))
        env.pop('SUDO_USER', None)
        return subprocess.run(['sh', str(REPO / 'data/boreas-setup'), *args], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def update(self, previous='0.1.6'):
        """What install.sh runs over an installed copy: adopt before its files are replaced,
        enable --keep-off at the end."""
        r = self.run_setup('adopt', f'--previous={previous}')
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.run_setup('enable', '--keep-off')

    def step(self, args):
        return self.update() if args == UPDATE_FROM_OLD else self.run_setup(*args)

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

    def test_keep_off_leaves_a_switched_off_boreas_off(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log.unlink()
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertEqual([call for call in calls if call.startswith('mask ')], [])
        self.assertNotIn('enable boreasd.service', calls)
        self.assertNotIn('restart boreasd.service', calls)
        self.assertEqual(calls, ['daemon-reload', 'try-restart boreasd.service'])  # a no-op for a stopped unit
        self.assertEqual(r.stdout, 'Boreas is switched off (boreas-setup disable): files updated, '
                                  'services left as they are. Turn it on with: sudo boreas-setup enable\n')
        self.assertEqual(self.masked.read_text(), '')  # emptied: this install keeps a record
        self.assertTrue(self.off.exists())
        self.log.unlink()
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('mask --now asusd.service', self.calls())
        self.assertIn('mask --now supergfxd.service', self.calls())
        self.assertIn('enable boreasd.service', self.calls())
        self.assertIn('restart boreasd.service', self.calls())
        self.assertFalse(self.off.exists())

    def test_keep_off_enables_a_fresh_install(self):
        self.assertFalse(self.masked.exists())
        self.unit('boreasd.service', 'disabled')
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertIn('enable boreasd.service', calls)
        self.assertIn('restart boreasd.service', calls)

    def test_keep_off_starts_again_what_install_stopped(self):
        # disable, then systemctl enable --now by hand: install.sh stops boreasd before it swaps
        # the SMU module, and try-restart would leave it stopped
        self.unit('asusd.service', 'static')
        self.assertEqual(self.run_setup('enable').returncode, 0)
        self.assertEqual(self.run_setup('disable').returncode, 0)
        self.log.unlink()
        r = self.run_setup('enable', '--keep-off', '--was-running')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['daemon-reload', 'restart boreasd.service'])
        self.assertTrue(self.off.exists())          # still switched off: nothing masked or enabled

    def test_install_keeps_a_switched_off_boreas_off(self):
        lines = (REPO / 'install.sh').read_text().splitlines()
        self.assertIn('/usr/bin/boreas-setup enable --keep-off $was_running', lines)
        # the running state is read before the early stop, and only that stop exists
        check = lines.index('if systemctl is-active --quiet boreasd.service 2>/dev/null; then')
        stops = [i for i, line in enumerate(lines) if 'systemctl stop boreasd' in line]
        self.assertEqual(len(stops), 1)
        self.assertLess(check, stops[0])

    def old_install(self, boreasd='enabled', masked=('asusd.service', 'supergfxd.service',
                                                     'power-profiles-daemon.service')):
        """An install that masked without leaving a record: 0.1.6 or older, or 0.1.6 updated to
        0.1.7 (0.1.7 records only what it masks itself). install.sh hands boreas-setup the
        version it replaces (self.update())."""
        self.unit('boreasd.service', boreasd)
        for unit in masked:
            self.unit(unit, 'masked')
        return masked

    def test_old_install_update_preserves_legacy_unmasking(self):
        units = self.old_install()
        self.assertFalse(self.masked.exists())
        r = self.update()
        self.assertEqual(r.returncode, 0, r.stderr)
        # the masks of the old install are taken over into the record, as the legacy rule had it
        self.assertEqual(self.masked.read_text().splitlines(),
                         ['asusd.service static', 'supergfxd.service enabled',
                          'power-profiles-daemon.service static'])
        self.log.unlink()
        r = self.run_setup('disable', '--legacy')
        self.assertEqual(r.returncode, 0, r.stderr)
        for unit in units:
            self.assertIn(f'unmask {unit}', self.calls())
            self.assertNotEqual((self.state / unit).read_text().strip(), 'masked')
        self.assertIn('enable supergfxd.service', self.calls())
        self.assertEqual((self.state / 'supergfxd.service').read_text().strip(), 'enabled')
        self.assertFalse(self.off.exists())

    def test_plain_disable_on_an_old_install_gives_its_masks_back(self):
        # an install made by 0.1.6 or older kept no record; disable must not leave the masks
        # behind a switched-off mark that also stops uninstall.sh from undoing them
        units = self.old_install()
        self.assertEqual(self.update().returncode, 0)
        self.log.unlink()
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        for unit in units:
            self.assertIn(f'unmask {unit}', self.calls())
        self.assertIn('enable supergfxd.service', self.calls())
        self.assertTrue(self.off.exists())
        self.log.unlink()
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        self.assertFalse([c for c in self.calls() if c.startswith('unmask')])   # nothing masked is left

    def test_old_install_with_boreasd_disabled_by_hand_is_undone(self):
        # 0.1.6, then `systemctl disable --now boreasd`: an update, a disable or uninstall.sh
        # must still give the old masks back
        for steps in ((UPDATE_FROM_OLD, ('disable', '--legacy')),
                      (UPDATE_FROM_OLD, ('disable',), ('disable', '--legacy'))):
            with self.subTest(steps=steps):
                self.setUp()
                units = self.old_install(boreasd='disabled')
                for args in steps:
                    r = self.step(args)
                    self.assertEqual(r.returncode, 0, r.stderr)
                for unit in units:
                    self.assertNotEqual((self.state / unit).read_text().strip(), 'masked', unit)
                self.assertEqual((self.state / 'supergfxd.service').read_text().strip(), 'enabled')

    def test_old_install_with_one_unit_given_back_for_a_while_is_undone(self):
        # README: unmask power-profiles-daemon to use it for a while; then an update
        units = self.old_install(masked=('asusd.service', 'supergfxd.service'))
        self.unit('power-profiles-daemon.service', 'static')
        for args in (UPDATE_FROM_OLD, ('disable', '--legacy')):
            r = self.step(args)
            self.assertEqual(r.returncode, 0, r.stderr)
        for unit in units + ('power-profiles-daemon.service',):
            self.assertNotEqual((self.state / unit).read_text().strip(), 'masked', unit)

    def test_package_disable_leaves_the_users_own_units(self):
        # a package install switched on by hand (systemctl enable --now boreasd), never through
        # boreas-setup: nothing is Boreas's to give back
        self.unit('boreasd.service', 'enabled')
        self.unit('power-profiles-daemon.service', 'masked')
        self.unit('supergfxd.service', 'disabled')
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('unmask power-profiles-daemon.service', self.calls())
        self.assertNotIn('enable supergfxd.service', self.calls())
        self.log.unlink()
        r = self.run_setup('enable')        # the window's Enable button later
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('power-profiles-daemon.service masked', self.masked.read_text())
        self.assertNotIn('power-profiles-daemon', self.masked.read_text())

    def test_a_mask_that_fails_to_stop_the_unit_is_still_recorded(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        (self.state / 'fail-stop-supergfxd.service').touch()   # masked, but stopping it failed
        r = self.run_setup('enable')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('supergfxd.service enabled', self.masked.read_text().splitlines())
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        self.assertEqual((self.state / 'supergfxd.service').read_text().strip(), 'enabled')

    def test_only_a_released_version_is_taken_to_have_masked_without_a_record(self):
        # 0.1.7 too: a copy updated from 0.1.6 to 0.1.7 still has no record of 0.1.6's masks
        for previous, adopted in (('0.1.7', True), ('0.1.6', True), ('0.1.3', True),
                                  ('0.1.8', False), ('0.2.0', False), ('', False)):
            with self.subTest(previous=previous):
                self.setUp()
                self.unit('asusd.service', 'masked')
                r = self.run_setup('adopt', f'--previous={previous}')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.masked.exists(), adopted)
                if adopted:
                    self.assertEqual(self.masked.read_text(), 'asusd.service static\n')

    def test_an_update_from_0_1_6_through_0_1_7_is_undone(self):
        units = self.old_install()
        self.assertEqual(self.update('0.1.7').returncode, 0)
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        for unit in units:
            self.assertNotEqual((self.state / unit).read_text().strip(), 'masked', unit)

    def test_adopt_keeps_a_record_that_is_there(self):
        # an install that was stopped after adopt (Ctrl-C at the language question) is run
        # again: it now reads its own version, and the record from the first run stays
        units = self.old_install()
        self.run_setup('adopt', '--previous=0.1.6')
        r = self.run_setup('adopt', '--previous=0.1.8')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.masked.read_text().splitlines()), len(units))
        self.assertEqual(self.run_setup('enable', '--keep-off').returncode, 0)
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        for unit in units:
            self.assertNotEqual((self.state / unit).read_text().strip(), 'masked', unit)

    def test_adopt_completes_a_partial_record_left_by_0_1_7(self):
        # 0.1.7 recorded only what it masked itself: a copy updated 0.1.6 --> 0.1.7 lists
        # power-profiles-daemon while 0.1.6's asusd and supergfxd masks are in no record
        units = self.old_install()
        self.masked.parent.mkdir(parents=True)
        self.masked.write_text('power-profiles-daemon.service static\n')
        r = self.run_setup('adopt', '--previous=0.1.7')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self.masked.read_text().splitlines()),
                         ['asusd.service static', 'power-profiles-daemon.service static',
                          'supergfxd.service enabled'])
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        for unit in units:
            self.assertNotEqual((self.state / unit).read_text().strip(), 'masked', unit)
        self.assertEqual((self.state / 'supergfxd.service').read_text().strip(), 'enabled')

    def test_adopt_leaves_a_record_kept_by_an_installed_boreas_setup(self):
        # a copy with boreas-setup keeps a full record of its own: masks it does not list are the user's
        self.old_install()
        self.masked.parent.mkdir(parents=True)
        self.masked.write_text('power-profiles-daemon.service static\n')
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text('#!/bin/sh\n')
        r = self.run_setup('adopt', '--previous=0.1.7')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.masked.read_text(), 'power-profiles-daemon.service static\n')

    def test_adopt_without_masks_found_leaves_no_record(self):
        # systemctl answering nothing for a moment must not leave an empty record behind that
        # stops the next run from taking the old masks over
        self.assertEqual(self.run_setup('adopt', '--previous=0.1.6').returncode, 0)
        self.assertFalse(self.masked.exists())
        units = self.old_install()
        self.assertEqual(self.run_setup('adopt', '--previous=0.1.6').returncode, 0)
        self.assertEqual(len(self.masked.read_text().splitlines()), len(units))

    def test_removal_leaves_no_record_behind(self):
        # uninstall.sh / pacman -R: a later install of an old version, updated again, starts
        # from no record, so its masks are taken over as on any old copy
        self.unit('asusd.service', 'static')
        for removal in ('--legacy', '--removing'):
            with self.subTest(removal=removal):
                self.assertEqual(self.run_setup('enable').returncode, 0)
                self.assertEqual(self.run_setup('disable', removal).returncode, 0)
                self.assertFalse(self.masked.exists())
                self.assertFalse(self.off.exists())

    def test_uninstall_without_a_record_ignores_a_stray_marker(self):
        # disable, then an uninstall.sh that knows no marker removed the record, then 0.1.6
        # masked again: the legacy rule still applies at uninstall
        units = self.old_install()
        self.off.parent.mkdir(parents=True, exist_ok=True)
        self.off.touch()
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        for unit in units:
            self.assertNotEqual((self.state / unit).read_text().strip(), 'masked', unit)

    def test_a_copy_that_already_has_boreas_setup_is_not_adopted(self):
        # 0.1.6 and 0.1.7 shipped no boreas-setup. A copy that has it kept its own record (a
        # source copy of main still says 0.1.7, and a run stopped at the language question has
        # already put the new one in place): its missing record means nothing was Boreas's
        self.unit('power-profiles-daemon.service', 'masked')
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text('#!/bin/sh\n')
        self.assertEqual(self.run_setup('adopt', '--previous=0.1.7').returncode, 0)
        self.assertFalse(self.masked.exists())

    def test_adopt_stops_the_install_when_systemctl_does_not_answer(self):
        # an empty answer would leave the old masks unrecorded while install.sh replaces the
        # version that tells they are Boreas's
        self.old_install()
        broken = Path(self.tmp.name) / 'broken'
        broken.mkdir()
        (broken / 'systemctl').write_text('#!/bin/sh\nexit 1\n')
        (broken / 'systemctl').chmod(0o755)
        r = self.run_setup('adopt', '--previous=0.1.6', path=f"{broken}:{self.bin}:{os.environ['PATH']}")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('systemctl', r.stderr)
        self.assertFalse(self.masked.exists())

    def test_plain_disable_without_a_record_keeps_boreas_off(self):
        # a package switched on by hand, or never enabled: disable on purpose, so the window
        # stops offering it and an update keeps it off
        self.unit('boreasd.service', 'enabled')
        self.assertEqual(self.run_setup('disable').returncode, 0)
        self.assertEqual(self.masked.read_text(), '')
        self.assertTrue(self.off.exists())
        self.log.unlink()
        self.assertEqual(self.run_setup('enable', '--keep-off').returncode, 0)
        self.assertNotIn('enable boreasd.service', self.calls())

    def test_a_mask_that_fails_is_not_recorded(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        (self.state / 'fail-mask-supergfxd.service').touch()   # mask refused, nothing changed
        self.assertNotEqual(self.run_setup('enable').returncode, 0)
        self.assertEqual(self.masked.read_text().splitlines(), ['asusd.service static'])

    def test_a_fresh_install_keeps_the_users_masks(self):
        self.unit('asusd.service', 'static')
        self.unit('power-profiles-daemon.service', 'masked')
        self.assertEqual(self.run_setup('adopt', '--previous=').returncode, 0)
        self.assertEqual(self.run_setup('enable', '--keep-off').returncode, 0)
        self.assertNotIn('power-profiles-daemon', self.masked.read_text())
        self.assertEqual(self.run_setup('disable', '--legacy').returncode, 0)
        self.assertNotIn('unmask power-profiles-daemon.service', self.calls())

    def test_a_marker_left_without_a_record_does_not_keep_a_fresh_install_off(self):
        # disable, then an uninstall.sh of 0.1.7 (which knows no marker) removed the record
        self.unit('asusd.service', 'static')
        self.off.parent.mkdir(parents=True)
        self.off.touch()
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('enable boreasd.service', self.calls())
        self.assertFalse(self.off.exists())

    def test_unknown_options_are_refused(self):
        for args in (('enable', '--bogus'), ('disable', '--bogus'), ('enable', '--previous=0.1.6'),
                     ('adopt',), ('adopt', '--bogus')):
            with self.subTest(args=args):
                r = self.run_setup(*args)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(self.calls(), [])

    def test_adopt_is_skipped_only_for_a_staged_destdir(self):
        # --files-only without DESTDIR (tools/install-reviewed.sh) replaces the live copy too,
        # so the old version's masks must still be taken over before its files go
        text = (REPO / 'install.sh').read_text()
        self.assertIn('D="${DESTDIR:-}"\n', text)
        before = text[:text.index('    sh "$SRC/data/boreas-setup" adopt --previous="$previous"\n')]
        guard, comment = before.splitlines()[-1], before.splitlines()[-2]
        self.assertEqual(guard, 'if [ -z "$D" ]; then')   # DESTDIR only, not --files-only
        self.assertTrue(comment.startswith('#') and '--files-only' in comment, comment)

    def test_install_hands_over_the_version_it_replaces(self):
        text = (REPO / 'install.sh').read_text()
        adopt = text.index('sh "$SRC/data/boreas-setup" adopt --previous="$previous"')
        self.assertLess(text.index('/usr/lib/boreas/boreas/__init__.py'), adopt)
        self.assertLess(adopt, text.index('rm -rf "$D/usr/lib/boreas/boreas"'))   # before it is replaced
        self.assertIn('/usr/bin/boreas-setup enable --keep-off $was_running\n', text)

    def test_keep_off_restarts_a_boreasd_the_user_turned_back_on(self):
        # switched off with boreas-setup disable, then `systemctl enable --now boreasd` by hand:
        # an update must not leave the old daemon running next to the new files
        self.unit('asusd.service', 'static')
        for args in (('enable',), ('disable',)):
            self.assertEqual(self.run_setup(*args).returncode, 0)
        self.unit('boreasd.service', 'enabled')
        self.log.unlink()
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('try-restart boreasd.service', self.calls())
        self.assertNotIn('mask --now asusd.service', self.calls())

    def test_reinstall_after_legacy_removal_enables_boreas(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        for args in (('enable',), ('disable', '--legacy')):
            r = self.run_setup(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.log.unlink()
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('enable boreasd.service', self.calls())
        self.assertIn('restart boreasd.service', self.calls())
        self.assertIn('mask --now asusd.service', self.calls())
        self.assertIn('mask --now supergfxd.service', self.calls())
        self.assertEqual((self.state / 'boreasd.service').read_text().strip(), 'enabled')
        self.assertFalse(self.off.exists())

    def test_keep_off_retries_a_failed_first_enable(self):
        self.unit('boreasd.service', 'disabled')
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        failure = self.state / 'fail-mask-supergfxd.service'
        failure.touch()
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual((self.state / 'boreasd.service').read_text().strip(), 'disabled')
        self.assertIn('asusd.service static', self.masked.read_text().splitlines())
        self.assertFalse(self.off.exists())
        failure.unlink()
        self.log.unlink()
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('mask --now supergfxd.service', self.calls())
        self.assertIn('enable boreasd.service', self.calls())
        self.assertEqual((self.state / 'boreasd.service').read_text().strip(), 'enabled')

    def test_package_removal_clears_switched_off_marker(self):
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        self.unit('power-profiles-daemon.service', 'masked')
        for args in (('enable',), ('disable',), ('disable', '--removing')):
            r = self.run_setup(*args)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.off.exists())
        self.assertFalse(self.masked.exists())            # removed together with the package
        self.assertNotIn('unmask power-profiles-daemon.service', self.calls())
        self.log.unlink()
        r = self.run_setup('enable', '--keep-off')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('enable boreasd.service', self.calls())
        self.assertIn('restart boreasd.service', self.calls())
        self.assertIn('mask --now supergfxd.service', self.calls())
        self.assertFalse(self.off.exists())

    def test_package_hooks_reload_before_restart_and_remove_without_marking_off(self):
        for package in ('boreas', 'boreas-git'):
            with self.subTest(package=package):
                script = (REPO / f'packaging/aur/{package}/boreas.install').read_text()
                pre_remove = script.split('pre_remove() {', 1)[1].split('}', 1)[0]
                self.assertIn('/usr/bin/boreas-setup disable --removing || true', pre_remove)
                post_upgrade = script.split('post_upgrade() {', 1)[1].split('}', 1)[0]
                self.assertLess(post_upgrade.index('systemctl daemon-reload 2>/dev/null || true'),
                                post_upgrade.index('systemctl try-restart boreasd.service'))

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
        self.assertEqual(self.masked.read_text(), '')  # emptied: this install keeps a record

    def test_disable_without_a_record_touches_nothing_else(self):
        self.unit('asusd.service', 'masked')                       # masked by the user, Boreas never enabled
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ['disable --now boreasd.service', 'daemon-reload'])

    def test_legacy_disable_after_disable_leaves_the_users_masks(self):
        self.unit('power-profiles-daemon.service', 'masked')
        self.unit('asusd.service', 'static')
        self.unit('supergfxd.service', 'enabled')
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_setup('disable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log.unlink()
        r = self.run_setup('disable', '--legacy')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertNotIn('unmask power-profiles-daemon.service', calls)
        self.assertNotIn('enable supergfxd.service', calls)
        self.assertFalse(any(call.startswith('start ') and 'power-profiles-daemon.service' in call.split()
                             for call in calls))
        self.assertFalse(self.off.exists())

    def test_legacy_disable_keeps_units_the_user_masked_before_install(self):
        for unit in ('asusd.service', 'asus-shutdown.service', 'supergfxd.service',
                     'power-profiles-daemon.service'):
            self.unit(unit, 'masked')
        r = self.run_setup('enable')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log.unlink()
        r = self.run_setup('disable', '--legacy')
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.calls()
        self.assertEqual([call for call in calls if call.startswith('unmask ')], [])
        self.assertNotIn('enable supergfxd.service', calls)

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

    def test_not_offered_after_the_user_switched_boreas_off(self):
        from unittest.mock import patch
        off = Path(self.tmp.name) / 'switched-off'
        record = Path(self.tmp.name) / 'masked-units'
        with patch.object(self.setup, 'SWITCHED_OFF', str(off)), \
                patch.object(self.setup, 'MASKED', str(record)):
            self.assertTrue(self.needed('disabled'))
            off.touch()               # a mark alone (disable on a copy never enabled) counts for nothing
            self.assertTrue(self.needed('disabled'))
            record.touch()                                # sudo boreas-setup disable after an enable
            self.assertFalse(self.needed('disabled'))

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

