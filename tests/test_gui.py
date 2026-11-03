"""UI tests (headless): python3 -m unittest discover -s tests"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("BOREAS_SOCKET", tempfile.mktemp(prefix="boreas-nosock-"))  # do not connect to the real service
os.environ["BOREAS_NO_UPDATE_CHECK"] = "1"  # the tests stay offline
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from boreas import i18n  # noqa: E402
from boreas.gui import app as A  # noqa: E402

i18n.set_language("tr")  # tested with the Turkish texts, which tests the translation as well

qa = QApplication.instance() or QApplication(sys.argv)
qa.setQuitOnLastWindowClosed(False)


class TestWindow(unittest.TestCase):
    def test_close_hides_to_tray(self):
        w = A.MainWindow()
        w.show()
        w.close()
        self.assertFalse(w.isVisible())
        w.quitting = True
        w.tray.hide()

    def test_quit_exits_even_when_visible(self):
        w = A.MainWindow()
        w.show()
        # with the fans window open, the old closeEvent usually cancelled the exit
        w.status = {"mode": 0, "modes": [{"id": 0, "name": "Dengeli", "base": 0, "builtin": True}],
                    "fan_curves": ["cpu"], "attrs": {}}
        w.busy = True  # a status refresh must not overwrite the fake status
        w.fans_window = A.FansWindow(w)
        w.fans_window.show()
        QTimer.singleShot(200, w.quit_app)
        QTimer.singleShot(3000, lambda: qa.exit(3))  # if quit is cancelled
        self.assertEqual(qa.exec(), 0)

    def test_quit_exits_even_if_a_window_refuses(self):
        w = A.MainWindow()
        w.busy = True

        class Stubborn(QWidget):
            def closeEvent(self, e):
                e.ignore()
        s = Stubborn()
        s.show()
        QTimer.singleShot(100, w.quit_app)
        QTimer.singleShot(6000, lambda: qa.exit(3))
        rc = qa.exec()
        s.hide()
        w.tray.hide()
        self.assertEqual(rc, 0)


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def value(self, key, default=None, type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        pass


SPL = {"current": 55, "min": 30, "max": 130, "default": 120, "choices": None}


class TestPanels(unittest.TestCase):
    def setUp(self):
        self.w = A.MainWindow()
        self.w.busy = True
        self.w.status = {"mode": 0, "fan_curves": [], "attrs": {"ppt_pl1_spl": SPL},
                         "modes": [{"id": 0, "name": "Dengeli", "base": 0, "builtin": True},
                                   {"id": 1, "name": "Turbo", "base": 1, "builtin": True}]}

    def tearDown(self):
        self.w.quitting = True
        self.w.tray.hide()

    def test_power_sliders_do_not_leak_between_modes(self):
        f = A.FansWindow(self.w)
        spl = f.sliders["ppt_pl1_spl"].slider
        entry = {"fans_apply": False, "power_apply": True, "ppt": {"ppt_pl1_spl": 90}, "nv": {}}
        f.mode_id = 1
        f._show_entry({"mode": entry})
        self.assertEqual(spl.value(), 90)
        f._show_entry({"mode": {**entry, "ppt": {}}})
        self.assertEqual(spl.value(), 120)  # another mode: firmware default
        f.mode_id = 0
        f._show_entry({"mode": {**entry, "ppt": {}}})
        self.assertEqual(spl.value(), 55)  # active mode: the current value

    def test_factory_resets_power_too(self):
        f = A.FansWindow(self.w)
        f.mode_id = 1
        f._show_entry({"mode": {"fans_apply": True, "power_apply": True,
                                "ppt": {"ppt_pl1_spl": 90}, "nv": {}}})
        f._factory()
        self.assertFalse(f.fans_apply.isChecked())
        self.assertFalse(f.power_apply.isChecked())
        self.assertEqual(f.sliders["ppt_pl1_spl"].slider.value(), 120)

    def test_cpu_temp_slider(self):
        self.assertIsNone(A.FansWindow(self.w).cpu_temp)  # unsupported CPU: hidden
        self.w.status["cpu_temp"] = {"min": 75, "max": 96, "applied": 96, "cpu": "Dragon Range"}
        f = A.FansWindow(self.w)
        entry = {"fans_apply": False, "power_apply": False, "ppt": {}, "nv": {}, "cpu_temp": 85}
        f._show_entry({"mode": entry})
        self.assertEqual(f.cpu_temp.value.text(), "85°C")
        sent = []
        self.w.call = lambda cmd, **kw: sent.append(kw["data"]) or {}
        with patch("boreas.gui.fans.run_async", lambda fn, done: done(fn())), \
                patch.object(self.w, "refresh", lambda *a: None):
            f._apply()
            f._show_entry({"mode": {**entry, "cpu_temp": None}})
            self.assertEqual(f.cpu_temp.value.text(), "Varsayılan")
            f._apply()
            f._show_entry({"mode": entry})
            f._factory()
            self.assertEqual(f.cpu_temp.slider.value(), 96)
        self.assertEqual([d["cpu_temp"] for d in sent], [85, None])

    def test_fans_button_switches_page(self):
        w = self.w
        w.status.update(ac=True)
        with patch("boreas.gui.fans.run_async", lambda fn, done=None: None):
            w.open_fans()
            self.assertEqual(w.page, "fan")
            self.assertEqual(w.title.text(), "Fanlar ve güç")
            self.assertTrue(w.fans_btn.isChecked())
            self.assertIs(w.stack.currentWidget(), w.fans_window)
            w.open_fans()  # on the page, the same button goes back
        self.assertEqual(w.page, "main")
        self.assertEqual(w.title.text(), "Boreas")
        self.assertFalse(w.fans_btn.isChecked())
        self.assertEqual(w.stack.currentIndex(), 0)

    def test_theme_button_cycles(self):
        from boreas.gui import common
        w = self.w
        w.settings = FakeSettings()
        names = []
        for _ in range(3):
            w.cycle_theme()
            names.append((w.settings.values["theme"], common.T["name"]))
        # auto: follows the system theme (dark offscreen), then light, then dark
        self.assertEqual([n[0] for n in names], ["light", "dark", "auto"])
        self.assertEqual(names[0][1], "light")
        self.assertEqual(names[1][1], "dark")
        self.assertIn("Otomatik", w.theme_btn.toolTip())

        common.apply_theme(A.QApplication.instance(), "dark")

    def test_language_menu_saves_and_restarts(self):
        w = self.w
        w.settings = FakeSettings()
        self.assertEqual(len(w.lang_menu.actions()), len(i18n.LANGUAGES))
        self.assertEqual([a.text() for a in w.lang_menu.actions() if a.isChecked()], ["Türkçe"])
        calls = []
        with patch.object(A.os, "execv", lambda exe, args: calls.append(args)), \
                patch.object(A.sys, "argv", ["/usr/bin/boreas", "--hidden"]):
            w.set_language("tr")  # same language: does nothing
            self.assertEqual(calls, [])
            w.set_language("de")
        self.assertEqual(w.settings.values["language"], "de")
        self.assertEqual(calls, [[sys.executable, "/usr/bin/boreas"]])  # --hidden is dropped, the window opens

    def test_fans_status_follows_edits(self):
        entry = {"fans_apply": False, "power_apply": True, "ppt": {"ppt_pl1_spl": 90}, "nv": {}}
        f = A.FansWindow(self.w)
        f.mode_id = 0
        f._show_entry({"mode": entry})
        self.assertEqual(f.result.text(), "Kaydedildi")  # loading does not mark it "not saved"
        f.sliders["ppt_pl1_spl"].slider.setValue(70)
        self.assertEqual(f.result.text(), "Kaydedilmedi")
        f.fans_apply.setChecked(True)
        self.assertEqual(f.result.text(), "Kaydedilmedi")

    def test_auto_screen_toggles_overdrive(self):
        w = self.w
        w.settings = FakeSettings(screen_auto=True)
        w.screen_rates = [60, 240]
        w.status["attrs"]["panel_overdrive"] = {"current": 1, "min": None, "max": None,
                                                "default": None, "choices": [0, 1]}
        rates, actions = [], []
        w._apply_rate = rates.append
        w.action = lambda cmd, after=None, **kw: actions.append((cmd, kw))
        w._auto_screen(False)
        self.assertEqual(rates, [60])
        self.assertEqual(actions, [("set_overdrive", {"value": 0})])


class TestIcon(unittest.TestCase):
    def test_icon_is_wind_glyph(self):
        self.assertEqual(len(A._wind_paths()), 3)
        self.assertEqual(len(A._wind_paths(simple=True)), 3)
        self.assertEqual(min(s.width() for s in A.tray_icon(A.ACCENT).availableSizes()), 16)


class TestUpdateCheck(unittest.TestCase):
    def setUp(self):
        self.w = A.MainWindow()
        self.w.settings = FakeSettings()
        self.calls = []
        self.orig = A.run_async
        # offline: return the given version instead of fetching
        A.run_async = lambda fn, cb=None: (self.calls.append(fn), cb(self.remote))
        self.remote = "9.9.9"
        os.environ.pop("BOREAS_NO_UPDATE_CHECK", None)

    def tearDown(self):
        A.run_async = self.orig
        os.environ["BOREAS_NO_UPDATE_CHECK"] = "1"
        self.w.quitting = True
        self.w.tray.hide()

    def test_version_compare(self):
        from boreas import update
        self.assertTrue(update.is_newer("0.2.0", "0.1.0"))
        self.assertTrue(update.is_newer("0.10.0", "0.9.0"))
        self.assertFalse(update.is_newer("0.1", "0.1.0"))
        self.assertFalse(update.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(update.is_newer(None, "0.1.0"))
        self.assertEqual(update.parse_version('"""x"""\n\n__version__ = "1.2.3"\n'), "1.2.3")
        self.assertIsNone(update.parse_version("<html>404</html>"))

    def test_newer_version_is_announced_and_can_be_dismissed(self):
        w = self.w
        w.check_updates()
        self.assertEqual(w.update_version, "9.9.9")
        text = w.update_info.text()
        self.assertIn("Boreas 9.9.9 çıktı.", text)
        for href, label in (("update", "Güncelle"), ("notes", "Sürüm notları"), ("hide", "Gizle")):
            self.assertIn(f"href='{href}'", text)
            self.assertIn(label, text)
        self.assertFalse(w.update_info.isHidden())
        w._notice_link("hide")
        self.assertEqual(w.settings.values["update_dismissed"], "9.9.9")
        self.assertTrue(w.update_info.isHidden())
        w.check_updates()  # the same version is not shown again
        self.assertEqual(w.update_version, "")

    def test_same_version_or_network_error_shows_nothing(self):
        w = self.w
        for r in (A.__version__, OSError("ağ yok"), None):
            self.remote = r
            w.check_updates()
            self.assertEqual(w.update_version, "")
        self.assertTrue(w.notice.isHidden())

    def test_update_button_installs_and_restarts(self):
        w = self.w
        w.check_updates()
        restarted = []
        with patch.object(A, "restart_app", lambda: restarted.append(1)):
            self.remote = "9.9.9"  # returned instead of run_update (fake run_async)
            w.start_update()
        self.assertEqual(restarted, [1])
        self.assertEqual(self.calls[-1].__name__, "<lambda>")

    def test_update_cancel_and_failure_are_shown(self):
        from boreas import update
        w = self.w
        w.check_updates()
        for result, text in ((update.UpdateCancelled(), "Güncelleme iptal edildi."),
                             (update.NoAuthorization(), "Yönetici izni alınamadı (şifre penceresi açılamadı)."),
                             (RuntimeError("disk dolu"), "Güncelleme başarısız: disk dolu")):
            self.remote = result
            with patch.object(A, "restart_app", lambda: self.fail("yeniden başlamamalı")):
                w.start_update()
            self.assertFalse(w.updating)
            self.assertIn(text, w.notice.text())
            self.assertIn("href='update'", w.update_info.text())  # can be retried

    def test_service_restart_during_update_is_not_an_error(self):
        w = self.w
        w.updating = True
        w.busy = False
        self.remote = A.DaemonError("Could not connect to boreasd ([Errno 111] Connection refused). "
                                    "Is the service running?")
        w.refresh()  # fake run_async: the status query fails with "connection refused"
        self.assertEqual(w.status_error, "")
        self.assertTrue(w.notice.isHidden())
        w.updating = False
        w.refresh()  # outside an update the same error is shown
        self.assertIn("boreasd", w.notice.text())

    def test_can_be_turned_off(self):
        w = self.w
        w.set_update_check(False)
        w.check_updates()
        self.assertEqual(self.calls, [])
        os.environ["BOREAS_NO_UPDATE_CHECK"] = "1"
        w.settings.values["update_check"] = True
        w.check_updates()
        self.assertEqual(self.calls, [])


class TestHeldSelection(unittest.TestCase):
    """A clicked choice does not jump back until the service has applied it and a fresh status arrives."""

    STATUS = {"mode": 0, "ac": True, "attrs": {}, "temps": {}, "fans": {}, "battery": {}, "kbd": {},
              "modes": [{"id": 2, "name": "Silent", "base": 2, "builtin": True},
                        {"id": 0, "name": "Balanced", "base": 0, "builtin": True},
                        {"id": 1, "name": "Turbo", "base": 1, "builtin": True}]}

    def setUp(self):
        self.w = A.MainWindow()
        self.w.status = dict(self.STATUS)
        self.w.busy = False
        self.jobs = []
        self.orig = A.run_async
        A.run_async = lambda fn, cb=None: self.jobs.append(cb)

    def tearDown(self):
        A.run_async = self.orig
        self.w.quitting = True
        self.w.tray.hide()

    def checked(self):
        return self.w.mode_seg.group.checkedButton().property("key")

    def test_click_is_not_undone_by_stale_status(self):
        w = self.w
        w.render()
        self.assertEqual(self.checked(), 0)
        w.set_mode(1)
        w.render()                       # the stale status in flight (mode 0) arrived
        self.assertEqual(self.checked(), 1)
        self.jobs.pop(0)({})             # the service applied the mode, a fresh status is requested
        self.assertEqual(self.checked(), 1)
        self.jobs.pop(0)({**self.STATUS, "mode": 1})
        self.assertEqual(self.checked(), 1)
        self.assertEqual(w.hold, {})

    def test_click_leaves_only_one_selected(self):
        w = self.w
        w.render()                                   # Balanced selected
        silent = [b for b in w.mode_seg.buttons() if b.property("key") == 2][0]
        silent.click()                               # before the service replies
        self.assertEqual([b.property("key") for b in w.mode_seg.buttons() if b.isChecked()], [2])
        silent.click()                               # clicking the selected one again does not unselect it
        self.assertEqual([b.property("key") for b in w.mode_seg.buttons() if b.isChecked()], [2])

    def test_live_info_only_on_fan_page(self):
        w = self.w
        w.status = {**self.STATUS, "temps": {"cpu": 70}, "fans": {"cpu": 2000, "gpu": 2100}}
        w.render()
        self.assertTrue(w.live_info.isHidden())          # already on the card on the main page
        w.page = "fan"
        w.render()
        self.assertFalse(w.live_info.isHidden())
        self.assertEqual(w.live_info.text(), "CPU 70° · 2000 / 2100 rpm")

    def test_gpu_users_are_summarized(self):
        w = self.w
        engine = {"policy": "standard", "power_state": "D3cold", "runtime_status": "suspended",
                  "users": ["systemd(1)", "systemd-logind(900)", "nvidia-powerd(1200)",
                            "kwin_wayland(1500)", "Xwayland(1600)", "steam(4000)"]}
        w.status = {**self.STATUS, "gpu": {"mode": "standard", "has_mux": True, "has_eco": True,
                                           "pending_reboot": False, "backend": "ebpf", "engine": engine}}
        w.render()
        self.assertEqual(w.gpu_label.text(), "eBPF · standard · D3cold / suspended · 3 süreç dGPU'yu kullanıyor")
        self.assertEqual(w.gpu_label.toolTip(), ", ".join(engine["users"]))  # the PIDs stay reachable
        engine["users"] = ["systemd(1)", "kwin_wayland(1500)"]
        w.render()
        self.assertTrue(w.gpu_label.text().endswith(" · 1 süreç dGPU'yu kullanıyor"))
        engine["users"] = ["systemd(1)"]
        w.render()
        self.assertTrue(w.gpu_label.text().endswith(" · dGPU'yu yalnızca sistem servisleri tutuyor"))
        engine["users"] = []
        w.render()
        self.assertEqual(w.gpu_label.text(), "eBPF · standard · D3cold / suspended")
        self.assertEqual(w.gpu_label.toolTip(), "")

    def test_failed_action_shows_real_state(self):
        w = self.w
        w.render()
        w.set_mode(1)
        self.jobs.pop(0)(A.DaemonError("x"))
        self.jobs.pop(0)(dict(self.STATUS))  # mode unchanged
        self.assertEqual(self.checked(), 0)
        self.assertEqual(w.hold, {})

    def test_second_click_wins(self):
        w = self.w
        w.render()
        w.set_mode(1)
        w.set_mode(2)
        self.jobs.pop(0)({})                 # first action done; queued: second action, status query
        self.jobs.pop(1)({**self.STATUS, "mode": 1})
        self.assertEqual(self.checked(), 2)  # the second click is still pending, it does not jump to 1
        self.assertIn("mode", w.hold)


class TestScreenRate(unittest.TestCase):
    def test_current_rate_selected_on_first_start(self):
        info = {"name": "eDP-1", "current": 240, "rates": {60: 1, 240: 2}}
        with patch.object(A.screen, "internal", lambda: info):
            w = A.MainWindow()
        w.settings = FakeSettings()  # first start: no saved rate
        w.status = dict(TestHeldSelection.STATUS)
        w.render()                   # drawing the status must not clear the choice
        checked = [b.property("key") for b in w.screen_seg.buttons() if b.isChecked()]
        self.assertEqual(checked, [240])
        w.quitting = True
        w.tray.hide()


class TestRefreshAndErrors(unittest.TestCase):
    def setUp(self):
        self.w = A.MainWindow()
        self.w.busy = True
        self.jobs = []
        self.orig = A.run_async
        A.run_async = lambda fn, cb=None: self.jobs.append(cb)

    def tearDown(self):
        A.run_async = self.orig
        self.w.quitting = True
        self.w.tray.hide()

    def test_refresh_callback_not_lost_while_busy(self):
        w = self.w
        w.render = lambda: None
        w.busy = False
        w.refresh()                      # a 2 s status query is running
        done = []
        w.refresh(lambda: done.append(1))  # e.g. refreshing the list after "Add mode"
        self.jobs.pop(0)({"mode": 0})
        self.assertEqual(done, [])       # the old query's result is not enough, ask again
        self.assertEqual(len(self.jobs), 1)
        self.jobs.pop(0)({"mode": 3})
        self.assertEqual(done, [1])

    def test_action_error_stays_visible(self):
        w = self.w
        w.refresh = lambda then=None: w._render_notice("")
        w.action("set_mode", mode=1)
        self.jobs.pop(0)(A.DaemonError("Could not connect to boreasd (No such file). Is the service running?"))
        self.assertIn("bağlanılamadı", w.notice.text())
        self.assertFalse(w.notice.isHidden())
        w.action("set_mode", mode=1)
        self.jobs.pop(0)({})
        self.assertTrue(w.notice.isHidden())

    def test_slider_click_applies(self):
        w = self.w
        sent = []
        w.action = lambda cmd, after=None, **kw: sent.append((cmd, kw))
        w.kbd_level.setValue(0)
        w.kbd_level.triggerAction(A.QSlider.SliderPageStepAdd)
        w.charge.setValue(80)
        w.charge.triggerAction(A.QSlider.SliderPageStepAdd)
        for _ in range(3):
            qa.processEvents()
        self.assertIn(("set_kbd", {"hold": ("kbd", 1), "brightness": 1}), sent)
        self.assertIn(("set_charge_limit", {"hold": ("charge", 85), "value": 85}), sent)


if __name__ == "__main__":
    unittest.main()
