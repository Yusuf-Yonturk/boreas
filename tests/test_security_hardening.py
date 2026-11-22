"""Security regressions without root privileges or Unix socket bind permissions."""

import errno
import io
import json
import os
from pathlib import Path
import re
import shlex
import socketserver
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from boreas import aura, client, config, daemon, fans, gpu, gpu_engine, hw

REPO = Path(__file__).resolve().parents[1]


class ScriptedSocket:
    """Socket reads advance a fake clock, honoring the last per-recv timeout."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.now = 0
        self.timeout = None
        self.written = bytearray()

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        if not self.chunks:
            return b""
        delay, data = self.chunks.pop(0)
        if self.timeout is not None and delay > self.timeout:
            self.now += self.timeout
            raise TimeoutError()
        self.now += delay
        if len(data) > size:
            self.chunks.insert(0, (0, data[size:]))
        return data[:size]

    def sendall(self, data):
        self.written.extend(data)

    def makefile(self, *args):
        sock = self

        class Reader:
            def readline(self, limit):
                line = bytearray()
                while len(line) < limit:
                    data = sock.recv(1)
                    if not data:
                        break
                    line.extend(data)
                    if data == b"\n":
                        break
                return bytes(line)

            def close(self):
                pass

        return Reader()


class SocketLimitTests(unittest.TestCase):
    def handle(self, chunks):
        sock = ScriptedSocket(chunks)
        server = Mock()
        server.daemon_obj.handle.return_value = {}
        with patch.object(daemon.time, "monotonic", side_effect=lambda: sock.now):
            daemon.Handler(sock, None, server)
        return sock, server.daemon_obj.handle

    def server(self):
        server = daemon.Server("unused", daemon.Handler, bind_and_activate=False)
        self.addCleanup(server.server_close)
        server.finish_request = Mock()
        server.shutdown_request = Mock()
        server.handle_error = Mock()
        return server

    def test_configured_resource_limits(self):
        self.assertEqual(daemon.MAX_REQUEST, 64 * 1024)
        self.assertEqual(daemon.MAX_CLIENTS, 16)
        self.assertEqual(daemon.REQUEST_TIMEOUT, 10)
        self.assertEqual(daemon.Handler.timeout, 30)

    def test_dripping_request_has_a_total_deadline(self):
        # The first byte may arrive near the idle limit. Every later recv is quick,
        # but the complete line still takes more than ten seconds.
        sock, handle = self.handle([(29, b'{'), (2, b'"cmd":'), (2, b'"status"'),
                                    (2, b' '), (2, b' '), (2, b' '), (2, b'}\n')])
        handle.assert_not_called()
        self.assertEqual(sock.written, b"")
        self.assertLessEqual(sock.now, 39)

    def test_idle_budget_and_deadline_restart_for_each_request(self):
        sock, handle = self.handle([(29, b'{"cmd":'), (9, b'"status"}\n'),
                                    (29, b'{"cmd":'), (9, b'"status"}\n')])
        self.assertEqual(handle.call_count, 2)
        self.assertEqual(sock.written.count(b'"ok": true'), 2)

    def test_pipelined_lines_are_preserved(self):
        line = b'{"cmd":"status","padding":"' + b'x' * 40000 + b'"}\n'
        sock, handle = self.handle([(0, line * 2)])
        self.assertEqual(handle.call_count, 2)
        self.assertEqual(sock.written.count(b'"ok": true'), 2)

    def test_request_size_boundary(self):
        prefix, suffix = b'{"cmd":"status","padding":"', b'"}\n'
        line = prefix + b'x' * (65536 - len(prefix) - len(suffix)) + suffix
        sock, handle = self.handle([(0, line)])
        handle.assert_called_once()
        self.assertTrue(json.loads(sock.written)["ok"])
        sock, handle = self.handle([(0, line[:-1] + b' \n')])
        handle.assert_not_called()
        self.assertEqual(json.loads(sock.written), {"ok": False, "error": "Request too large"})

    def test_connection_limit_rejects_immediately_and_releases_after_errors(self):
        with patch.object(daemon, "MAX_CLIENTS", 2, create=True):
            server = self.server()
        requests = [Mock() for _ in range(5)]
        with patch.object(daemon.threading.Thread, "start") as start:
            for request in requests[:3]:
                server.process_request(request, None)
            self.assertEqual(start.call_count, 2)
            server.shutdown_request.assert_called_once_with(requests[2])
            requests[2].sendall.assert_called_once_with(daemon.TOO_MANY_CLIENTS)
            server.process_request_thread(requests[0], None)
            server.process_request(requests[3], None)
            self.assertEqual(start.call_count, 3)
            server.finish_request.side_effect = RuntimeError("handler failed")
            server.process_request_thread(requests[1], None)
            server.process_request(requests[4], None)
            self.assertEqual(start.call_count, 4)
            server.handle_error.assert_called_once_with(requests[1], None)

    def test_thread_start_failure_releases_slot(self):
        with patch.object(daemon, "MAX_CLIENTS", 1, create=True):
            server = self.server()
        with patch.object(daemon.threading.Thread, "start", side_effect=RuntimeError("no thread")):
            with self.assertRaises(RuntimeError):
                server.process_request(Mock(), None)
        with patch.object(daemon.threading.Thread, "start") as start:
            server.process_request(Mock(), None)
            start.assert_called_once()

    def test_descriptor_exhaustion_backs_off_and_rate_limits_logs(self):
        for code in (errno.EMFILE, errno.ENFILE):
            with self.subTest(errno=code):
                server = self.server()
                with patch.object(socketserver.UnixStreamServer, "get_request", side_effect=OSError(code, "fds")), \
                        patch.object(daemon.time, "monotonic", side_effect=[0, 0.1, 5]), \
                        patch.object(daemon.time, "sleep") as sleep, patch.object(daemon, "log") as log:
                    for _ in range(3):
                        with self.assertRaises(OSError):
                            server.get_request()
                    self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.1)] * 3)
                    self.assertEqual(log.call_count, 2)

    def test_other_accept_errors_have_no_backoff(self):
        server = self.server()
        with patch.object(socketserver.UnixStreamServer, "get_request", side_effect=OSError(errno.EAGAIN, "retry")), \
                patch.object(daemon.time, "sleep") as sleep, patch.object(daemon, "log") as log:
            with self.assertRaises(OSError):
                server.get_request()
            sleep.assert_not_called()
            log.assert_not_called()


def gui_requests():
    """Exercise the GUI builders with all fans, power fields, CPU options and colors."""
    from boreas.gui import app as A, fans as F
    requests = []

    def capture(cmd, **kw):
        requests.append({"cmd": cmd, **kw})
        return {}

    page = SimpleNamespace(
        mode_id=config.MAX_MODES - 1, edits=0, _set_status=Mock(),
        main=SimpleNamespace(status={"mode": config.MAX_MODES - 1}, call=capture),
        fans_apply=Mock(**{"isChecked.return_value": True}),
        power_apply=Mock(**{"isChecked.return_value": True}),
        sliders={k: Mock(slider=Mock(**{"value.return_value": 300}))
                 for k in (*hw.PPT_ATTRS, *hw.NV_ATTRS)},
        cpu_boost_cb=Mock(**{"currentData.return_value": max(hw.CPU_BOOST_MODES, key=len)}),
        cpu_governor_cb=Mock(**{"currentData.return_value": max(hw.CPU_GOVERNORS, key=len)}),
        cpu_temp=Mock(slider=Mock(**{"value.return_value": 95, "maximum.return_value": 96})),
        cpu_floor=Mock(slider=Mock(**{"value.return_value": 300, "minimum.return_value": 1})),
        editors={fan: SimpleNamespace(curve=[110] * fans.POINTS + [100] * fans.POINTS)
                 for fan in fans.FANS}, curve_ready=set(fans.FANS))
    with patch.object(F, "run_async", side_effect=lambda fn, cb: fn()):
        F.FansWindow._apply(page)
    color = Mock(**{"red.return_value": 255, "green.return_value": 255, "blue.return_value": 255})
    window = SimpleNamespace(_show_color2=Mock(), action=capture, color=color, color2=color,
                             zone_colors=[color] * A.AURA_ZONE_COUNT,
                             aura_speed=Mock(**{"currentData.return_value": "normal"}))
    for mode in A.AURA_LABELS:
        window.aura_mode = Mock(**{"currentData.return_value": mode})
        A.MainWindow.set_aura(window)
    return requests


class RequestSizeTests(unittest.TestCase):
    def test_fully_populated_gui_requests_fit_comfortably(self):
        requests = gui_requests()
        update = requests[0]
        self.assertEqual(update["cmd"], "update_mode")
        self.assertEqual(set(update["data"]["ppt"]), set(hw.PPT_ATTRS))
        self.assertEqual(set(update["data"]["nv"]), set(hw.NV_ATTRS))
        for fan in fans.FANS:
            self.assertEqual(len(update["data"][f"fan_{fan}"]), 16)
        # A conservative combined envelope includes aura even though the GUI sends it separately.
        lighting = {"mode": max(aura.ALL_MODES, key=len), "color": [255] * 3,
                    "color2": [255] * 3, "speed": "normal", "zones": [[255] * 3] * aura.ZONE_COUNT}
        requests.append({**update, "data": {**update["data"], "aura": lighting}})
        for request in requests:
            with self.subTest(cmd=request["cmd"], mode=request.get("mode")):
                # Use the real client's serialization, including the newline and encoding.
                sock = Mock()
                sock.__enter__ = Mock(return_value=sock)
                sock.__exit__ = Mock(return_value=False)
                sock.recv.return_value = b'{"ok":true}\n'
                with patch.object(client.socket, "socket", return_value=sock):
                    client.call(**request)
                self.assertLess(len(sock.sendall.call_args.args[0]), 16 * 1024)


class InstallerIsolationTests(unittest.TestCase):
    def test_root_python_calls_are_isolated_except_explicit_source_directory(self):
        for path, expected in (("install.sh", 3), ("tools/install-reviewed.sh", 2)):
            commands = [line for line in (REPO / path).read_text().splitlines()
                        if not line.lstrip().startswith("#") and re.search(r"\bpython3(?:\s|$)", line)]
            isolated = 0
            for line in commands:
                if '(cd "$SRC" && python3' in line:
                    self.assertIn('(cd "$SRC" && python3 -m boreas.ryzen install-choice)', line)
                else:
                    self.assertRegex(line, r"\bpython3 -I -[cm]\b")
                    isolated += 1
            self.assertEqual(isolated, expected)

    def test_pyside_checks_ignore_cwd_and_pythonpath(self):
        script = (REPO / "install.sh").read_text()
        commands = re.findall(r'! (python3 [^\n]+?) 2>/dev/null; then', script)
        self.assertEqual(len(commands), 2)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "executed"
            (Path(tmp) / "PySide6.py").write_text(f"open({str(marker)!r}, 'w').close()\n")
            for command in commands:
                result = subprocess.run(shlex.split(command), cwd=tmp,
                                        env={**os.environ, "PYTHONPATH": tmp}, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(marker.exists())


class SetupUmaskTests(unittest.TestCase):
    def test_umask_precedes_file_writes(self):
        script = (REPO / "data/boreas-setup").read_text()
        self.assertRegex(script, r"set -e\numask 022\n")
        self.assertLess(script.index("umask 022"), script.index('> "$MASKED"'))

    def test_setup_files_are_0644_under_umask_000(self):
        try:
            import test_install
        except ImportError:  # run from the repository root, not through discover -s tests
            from tests import test_install
        fixture = test_install.SetupTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run = subprocess.run
        with patch.object(test_install.subprocess, "run", side_effect=lambda *a, **kw: run(*a, umask=0, **kw)):
            for command in ("enable", "disable"):
                result = fixture.run_setup(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(stat.S_IMODE(fixture.masked.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(fixture.off.stat().st_mode), 0o644)

    def test_setup_tightens_records_an_older_run_left_writable(self):
        try:
            import test_install
        except ImportError:
            from tests import test_install
        fixture = test_install.SetupTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.masked.parent.mkdir(parents=True, exist_ok=True)
        for path in (fixture.masked, fixture.off):
            path.write_text("")
            path.chmod(0o666)
        result = fixture.run_setup("enable", "--keep-off")
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in (fixture.masked, fixture.off):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644, path)


class HelperPathTests(unittest.TestCase):
    path = "/usr/lib/boreas/boreas-gpu-engine"

    def setUp(self):
        self.metadata = {self.path: Mock(st_uid=0, st_mode=stat.S_IFREG | 0o755)}
        for path in ("/usr/lib/boreas", "/usr/lib", "/usr", "/"):
            self.metadata[path] = Mock(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        real_stat = os.stat

        def metadata(path, *args, **kwargs):
            return self.metadata[path] if path in self.metadata else real_stat(path, *args, **kwargs)

        self.stat = patch.object(gpu_engine.os, "stat", side_effect=metadata)
        self.stat.start()
        self.addCleanup(self.stat.stop)
        realpath = patch.object(gpu_engine.os.path, "realpath", return_value=self.path)
        realpath.start()
        self.addCleanup(realpath.stop)
        run = patch.object(gpu_engine.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(
            {"protocol_version": 1, "capabilities": ["status", "eco", "standard"]})))
        self.run = run.start()
        self.addCleanup(run.stop)

    def test_untrusted_file_or_any_parent_is_rejected_before_execution(self):
        for path, original in list(self.metadata.items()):
            for uid, mode in ((1000, original.st_mode), (0, original.st_mode | 0o020),
                              (0, original.st_mode | 0o002)):
                with self.subTest(path=path, uid=uid, mode=oct(mode)):
                    self.metadata[path] = Mock(st_uid=uid, st_mode=mode)
                    try:
                        with self.assertRaises(OSError):
                            gpu_engine.check_protocol(self.path)
                        self.run.assert_not_called()
                    finally:
                        self.metadata[path] = original

    def test_non_regular_file_is_rejected(self):
        for kind in (stat.S_IFDIR, stat.S_IFIFO, stat.S_IFSOCK):
            with self.subTest(kind=kind):
                self.metadata[self.path] = Mock(st_uid=0, st_mode=kind | 0o755)
                with self.assertRaises(OSError):
                    gpu_engine.check_protocol(self.path)
                self.run.assert_not_called()

    def test_symlink_into_sticky_writable_directory_is_rejected(self):
        target = "/tmp/helper-dir/engine"
        self.metadata.update({gpu_engine.LOCAL_HELPER: Mock(st_uid=0, st_mode=stat.S_IFREG | 0o755),
                              target: Mock(st_uid=0, st_mode=stat.S_IFREG | 0o755),
                              "/tmp/helper-dir": Mock(st_uid=0, st_mode=stat.S_IFDIR | 0o755),
                              "/tmp": Mock(st_uid=0, st_mode=stat.S_IFDIR | 0o1777)})
        with patch.object(gpu_engine.os.path, "realpath", return_value=target):
            with self.assertRaises(OSError):
                gpu_engine.check_protocol(gpu_engine.LOCAL_HELPER)
        self.run.assert_not_called()

    def test_trusted_resolved_path_is_used_for_protocol_and_spawn(self):
        self.metadata[gpu_engine.LOCAL_HELPER] = self.metadata[self.path]
        engine = gpu_engine.Engine()
        self.addCleanup(setattr, engine, "process", None)
        self.run.side_effect = [Mock(returncode=1), self.run.return_value]
        with patch.object(gpu_engine, "helper_path", return_value=gpu_engine.LOCAL_HELPER), \
                patch.object(hw, "get_attr", return_value=None), \
                patch.object(gpu_engine.subprocess, "Popen") as spawn:
            engine._start()
        self.assertEqual(self.run.call_args.args[0], [self.path, "--protocol-info"])
        self.assertEqual(spawn.call_args.args[0], [self.path])


class ProcessNameTests(unittest.TestCase):
    def test_comm_replaces_nonprintable_characters_and_limits_length(self):
        self.assertEqual(gpu.clean_comm("a\nb\tc\x00d\x1be\x7ff\u200bg\u202e"), "a?b?c?d?e?f?g?")
        self.assertEqual(gpu.clean_comm("<b>" + "x" * 30), "<b>" + "x" * 12)
        self.assertEqual(gpu.clean_comm("Türkçe"), "Türkçe")

    def test_proc_comm_removes_only_the_kernel_newline(self):
        with patch("builtins.open", return_value=io.StringIO("\n<b>\tname\n\n")):
            self.assertEqual(gpu._comm("123"), "?<b>?name?")

    def test_helper_users_are_cleaned_and_invalid_pids_are_dropped(self):
        status = {"policy": "standard", "users": ["bad\n\x1b<b>(42)", "x" * 20 + "(7)",
                                                     "ok(12)", "x(nope)", "x(１２)", "x()", 1, "no pid"]}
        engine = gpu_engine.Engine()
        engine.buffer = (json.dumps({"ok": True, "status": status}) + "\n").encode()
        engine.process = Mock()
        self.addCleanup(setattr, engine, "process", None)
        with patch.object(engine, "_start"):
            result = engine.request("status")
        self.assertEqual(result["users"], ["bad??<b>(42)", "x" * 15 + "(7)", "ok(12)"])

    def test_gui_tooltip_escapes_process_markup(self):
        from boreas.gui.app import gpu_users_summary
        line, tip = gpu_users_summary(["<b>&game(12)"])
        self.assertTrue(line)
        self.assertEqual(tip, "<qt>&lt;b&gt;&amp;game(12)</qt>")
        # without markup the list stays plain text: "&" and quotes show as written, no entities
        self.assertEqual(gpu_users_summary(["Baldur's Gate(7)", "a&b(8)"])[1],
                         "Baldur's Gate(7), a&b(8)")


if __name__ == "__main__":
    unittest.main()
