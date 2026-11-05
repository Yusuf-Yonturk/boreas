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
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

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
        self.assertIn("Hedefe doğru", f.cpu_temp.info.toolTip())  # no power profile selector: the plain explanation
        self.assertNotIn("EPP düşürülemez", f.cpu_temp.info.toolTip())
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

    def test_power_profile_combo(self):
        self.assertIsNone(A.FansWindow(self.w).cpu_governor_cb)  # no cpufreq governor: hidden
        self.w.status["cpu_governors"] = ["default", "performance"]
        f = A.FansWindow(self.w)
        self.assertEqual([f.cpu_governor_cb.itemData(i) for i in range(f.cpu_governor_cb.count())],
                         ["default", "performance"])  # only the advertised governors are offered
        self.assertIn("Varsayılan governor'ı sürücüye bırakır", f.cpu_governor_info.toolTip().replace("&#x27;", "'"))
        entry = {"fans_apply": False, "power_apply": False, "ppt": {}, "nv": {}, "cpu_governor": "performance"}
        f._show_entry({"mode": entry})
        self.assertEqual(f.cpu_governor_cb.currentData(), "performance")
        sent = []
        self.w.call = lambda cmd, **kw: sent.append(kw["data"]) or {}
        with patch("boreas.gui.fans.run_async", lambda fn, done: done(fn())), \
                patch.object(self.w, "refresh", lambda *a: None):
            f._apply()
            f._show_entry({"mode": {**entry, "cpu_governor": "powersave"}})  # not offered: falls back
            self.assertEqual(f.cpu_governor_cb.currentData(), "default")
            f._show_entry({"mode": entry})
            f._factory()
            self.assertEqual(f.cpu_governor_cb.currentData(), "default")
            f._apply()
        self.assertEqual([d["cpu_governor"] for d in sent], ["performance", "default"])

    def test_boost_list_shrinks_under_the_performance_governor(self):
        self.w.status["cpu_governors"] = ["default", "powersave", "performance"]
        f = A.FansWindow(self.w)
        boost, gov = f.cpu_boost_cb, f.cpu_governor_cb
        keys = lambda: [boost.itemData(i) for i in range(boost.count())]
        full = ["default", "disabled", "efficient", "balanced", "aggressive", "max"]
        entry = {"fans_apply": False, "power_apply": False, "ppt": {}, "nv": {},
                 "cpu_boost": "efficient", "cpu_governor": "default"}
        f._show_entry({"mode": entry})
        self.assertEqual(keys(), full)
        self.assertEqual(boost.currentData(), "efficient")
        gov.setCurrentIndex(gov.findData("performance"))  # the user picks Performance
        self.assertEqual(keys(), ["max", "disabled"])  # only boost on/off is left
        self.assertEqual(boost.currentText(), "Performans")
        self.assertEqual(f.result.text(), "Kaydedilmedi")
        gov.setCurrentIndex(gov.findData("default"))
        self.assertEqual(keys(), full)
        self.assertEqual(boost.currentData(), "max")  # carried over as the strongest EPP
        f._show_entry({"mode": {**entry, "cpu_boost": "disabled", "cpu_governor": "performance"}})
        self.assertEqual((keys(), boost.currentData()), (["max", "disabled"], "disabled"))
        f._factory()
        self.assertEqual((keys(), boost.currentData(), gov.currentData()), (full, "default", "default"))

    def test_info_icon_shows_its_tooltip_at_once(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QEnterEvent
        from boreas.gui import widgets as W
        icon = W.info_icon("first\n\nsecond <b>")
        self.assertEqual(icon.toolTip(), "<p>first</p><p>second &lt;b&gt;</p>")
        with patch.object(W.QToolTip, "showText") as show:
            QApplication.sendEvent(icon, QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))  # no hover delay
            self.assertEqual(show.call_args.args[1], icon.toolTip())
            icon.setToolTip("")
            show.reset_mock()
            QApplication.sendEvent(icon, QEvent(QEvent.Enter))
            show.assert_not_called()

    def test_slider_columns_do_not_follow_the_caption(self):
        from boreas.gui import widgets as W
        long_caption = "Predeterminado y algo más largo"
        s = W.ValueSlider("Target", 75, 96, 96, "°C", max_text=long_caption, info="why")
        plain = W.ValueSlider("CPU sustained (SPL)", 30, 130, 40, "W")
        self.assertGreaterEqual(s.value.width(), s.value.fontMetrics().horizontalAdvance(long_caption))  # not cut
        box = QWidget()
        box.resize(500, 120)
        lay = QVBoxLayout(box)
        lay.addWidget(s)
        lay.addWidget(plain)
        W.align_sliders([s, plain, None])
        box.show()
        qa.processEvents()
        at_max = s.slider.width()
        s.slider.setValue(95)  # the caption becomes "95°C"
        qa.processEvents()
        self.assertEqual(s.slider.width(), at_max)  # a drag away from the caption does not resize the slider
        self.assertEqual(s.slider.geometry().x(), plain.slider.geometry().x())  # the grooves line up
        self.assertEqual(s.slider.width(), plain.slider.width())
        self.assertGreaterEqual(s.name.maximumWidth(), s.name.fontMetrics().horizontalAdvance("Target"))
        box.close()

    def test_cpu_floor_slider(self):
        self.w.status["cpu_temp"] = {"min": 75, "max": 96, "target": None, "state": "off"}
        self.w.status["attrs"] = {}
        self.assertIsNone(A.FansWindow(self.w).cpu_floor)  # no SPL range: hidden
        self.w.status["attrs"] = {"ppt_pl1_spl": SPL}
        f = A.FansWindow(self.w)
        self.assertEqual(f.cpu_floor.value.text(), "Kapalı")
        self.assertIn("altına hiç çekmez", f.cpu_floor.info.toolTip())  # the explanation is on the info icon
        entry = {"fans_apply": False, "power_apply": False, "ppt": {}, "nv": {}, "cpu_temp": 85, "cpu_temp_floor_w": 45}
        f._show_entry({"mode": entry})
        self.assertEqual(f.cpu_floor.value.text(), "45W")
        sent = []
        self.w.call = lambda cmd, **kw: sent.append(kw["data"]) or {}
        with patch("boreas.gui.fans.run_async", lambda fn, done: done(fn())), \
                patch.object(self.w, "refresh", lambda *a: None):
            f._apply()
            f._show_entry({"mode": {**entry, "cpu_temp_floor_w": None}})
            self.assertEqual(f.cpu_floor.slider.value(), 30)
            f._apply()
            f._show_entry({"mode": entry})
            f._factory()
            self.assertEqual(f.cpu_floor.slider.value(), 30)
        self.assertEqual([d["cpu_temp_floor_w"] for d in sent], [45, None])

    def test_power_card_status_line(self):
        self.w.status["cpu_temp"] = {"min": 75, "max": 96, "target": None, "state": "off"}
        f = A.FansWindow(self.w)
        f.mode_id = 0
        ct = {"target": 82, "state": "limited", "error": "", "requested_w": {"ppt_pl1_spl": 30}, "ceilings_w": {"ppt_pl1_spl": 65},
              "floor_w": 30, "stage": 2, "stage_max": 4,
              "ladder": ["epp:balance_performance", "epp:balance_power", "epp:power", "boost:False"]}
        from boreas.gui.common import Hold
        with patch.object(Hold, "clock", staticmethod(lambda: 100.0)):
            f.render_cpu_temp(ct, 0)
        self.assertEqual(f.power_info.text(), "Hedef 82 °C · PL1 30 W (taban) · EPP düşürüldü")
        with patch.object(Hold, "clock", staticmethod(lambda: 200.0)):
            f.render_cpu_temp({**ct, "stage": 4, "state": "active", "requested_w": {"ppt_pl1_spl": 40}}, 0)
        self.assertEqual(f.power_info.text(), "Hedef 82 °C · PL1 40 W · EPP düşürüldü · boost kapalı")
        f.render_cpu_temp({**ct, "state": "error", "error": "Power limits changed externally; controller stopped"}, 0)
        self.assertEqual(f.power_info.text(), "Güç limitleri dışarıdan değiştirildi; denetleyici durdu")
        f.render_cpu_temp(ct, 1)  # another mode is being edited: nothing about the active one
        self.assertEqual(f.power_info.text(), "")
        f.render_cpu_temp({**ct, "target": None}, 0)
        self.assertEqual(f.power_info.text(), "")

    def test_power_card_status_line_does_not_blink(self):
        """Around the target the controller's state flips every second and a 2 W recovery step is taken
        back at once; the line must not change with each of those samples."""
        from boreas.gui.common import Hold
        self.w.status["cpu_temp"] = {"min": 75, "max": 96, "target": None, "state": "off"}
        f = A.FansWindow(self.w)
        f.mode_id = 0
        ct = {"target": 82, "error": "", "ceilings_w": {"ppt_pl1_spl": 40}, "floor_w": 30, "stage": 0, "stage_max": 4,
              "ladder": ["boost:False"]}
        samples = [("limited", 30), ("active", 30), ("active", 32), ("limited", 30), ("active", 30), ("active", 32)]
        seen = set()
        for i, (state, pl1) in enumerate(samples):
            with patch.object(Hold, "clock", staticmethod(lambda i=i: 100.0 + 2 * i)):
                f.render_cpu_temp({**ct, "state": state, "requested_w": {"ppt_pl1_spl": pl1}}, 0)
            seen.add(f.power_info.text())
        self.assertEqual(seen, {"Hedef 82 °C · PL1 30 W (taban)"})
        with patch.object(Hold, "clock", staticmethod(lambda: 130.0)):  # away from the floor for a while: the real value
            f.render_cpu_temp({**ct, "state": "active", "requested_w": {"ppt_pl1_spl": 36}}, 0)
        self.assertEqual(f.power_info.text(), "Hedef 82 °C · PL1 36 W")
        with patch.object(Hold, "clock", staticmethod(lambda: 131.0)):
            f.render_cpu_temp({**ct, "state": "limited", "requested_w": {"ppt_pl1_spl": 30}}, 0)
            f.render_cpu_temp({**ct, "target": None}, 0)  # target switched off: nothing is held over
            f.render_cpu_temp({**ct, "state": "active", "requested_w": {"ppt_pl1_spl": 40}}, 0)
        self.assertEqual(f.power_info.text(), "Hedef 82 °C · PL1 40 W")

    def test_power_card_floor_is_not_held_over_a_change(self):
        """The held "(floor)" bridges the controller's own 2 W recovery step and nothing else."""
        from boreas import thermal
        from boreas.gui import fans as F
        from boreas.gui.common import Hold
        self.assertEqual(F.FLOOR_SLACK_W, thermal.RECOVER_STEP_W)
        self.w.status["cpu_temp"] = {"min": 75, "max": 96, "target": None, "state": "off"}
        ct = {"target": 82, "error": "", "ceilings_w": {"ppt_pl1_spl": 65}, "floor_w": 30, "stage": 0, "stage_max": 4,
              "ladder": ["boost:False"], "state": "active", "requested_w": {"ppt_pl1_spl": 30}}

        def line(*samples):
            """A fresh window, at the floor at t=100, then the given (sample, mode) pairs at t=102."""
            f = A.FansWindow(self.w)
            f.mode_id = 0
            with patch.object(Hold, "clock", staticmethod(lambda: 100.0)):
                f.render_cpu_temp(ct, 0)
            self.assertEqual(f.power_info.text(), "Hedef 82 °C · PL1 30 W (taban)")  # at the floor, state "active"
            with patch.object(Hold, "clock", staticmethod(lambda: 102.0)):
                for sample, mode, edited in samples:
                    f.mode_id = edited
                    f.render_cpu_temp(sample, mode)
            return f.power_info.text()

        up = {**ct, "requested_w": {"ppt_pl1_spl": 32}}
        self.assertEqual(line((up, 0, 0)), "Hedef 82 °C · PL1 30 W (taban)")  # the recovery step: bridged
        self.assertEqual(line(({**ct, "requested_w": {"ppt_pl1_spl": 34}}, 0, 0)), "Hedef 82 °C · PL1 34 W")  # a real recovery
        self.assertEqual(line(({**ct, "requested_w": {}}, 0, 0)), "Hedef 82 °C · PL1 65 W")  # the mode was applied again
        self.assertEqual(line(({**up, "floor_w": 45}, 0, 0)), "Hedef 82 °C · PL1 32 W (taban)")  # floor raised: the real value
        self.assertEqual(line(({**ct, "requested_w": {"ppt_pl1_spl": 40}, "floor_w": 38}, 0, 0)), "Hedef 82 °C · PL1 40 W")
        self.assertEqual(line(({**up, "target": 88}, 0, 0)), "Hedef 88 °C · PL1 32 W")  # another target
        self.assertEqual(line(({**up, "target": 88}, 1, 1)), "Hedef 88 °C · PL1 32 W")  # another mode, chosen on this page
        self.assertEqual(line((up, 0, 1), (up, 0, 0)), "Hedef 82 °C · PL1 32 W")  # another mode was looked at in between
        err = {**ct, "state": "error", "error": "Power limits changed externally; controller stopped"}
        self.assertEqual(line((err, 0, 0), (up, 0, 0)), "Hedef 82 °C · PL1 32 W")  # the controller stopped in between

    def test_conflict_hint_follows_governor_and_target(self):
        self.w.status["cpu_temp"] = {"min": 75, "max": 96, "target": None, "state": "off"}
        self.w.status["cpu_governors"] = ["default", "powersave", "performance"]
        f = A.FansWindow(self.w)
        f.show()
        entry = {"fans_apply": False, "power_apply": False, "ppt": {}, "nv": {}, "cpu_temp": 82, "cpu_governor": "performance"}
        f._show_entry({"mode": entry})
        tip = lambda: f.cpu_temp.info.toolTip()  # noqa: E731
        self.assertIn("Hedefe doğru", tip())  # the target's own explanation stays
        self.assertIn("EPP düşürülemez", tip())
        f.cpu_governor_cb.setCurrentIndex(f.cpu_governor_cb.findData("default"))
        self.assertNotIn("EPP düşürülemez", tip())
        f.cpu_governor_cb.setCurrentIndex(f.cpu_governor_cb.findData("performance"))
        self.assertIn("EPP düşürülemez", tip())
        f.cpu_temp.slider.setValue(96)  # target off: no conflict
        self.assertNotIn("EPP düşürülemez", tip())
        f.close()

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

    def test_main_card_shows_limited(self):
        w = self.w
        from boreas.gui.common import Hold
        with patch.object(Hold, "clock", staticmethod(lambda: 100.0)):
            w.status = {**self.STATUS, "cpu_temp": {"target": 82, "state": "limited"}}
            w.render()
        self.assertIn("sınırlı", w.mode_label.text())
        with patch.object(Hold, "clock", staticmethod(lambda: 102.0)):  # the state flips every second around the target: no blinking
            w.status = {**self.STATUS, "cpu_temp": {"target": 82, "state": "active"}}
            w.render()
        self.assertIn("sınırlı", w.mode_label.text())
        with patch.object(Hold, "clock", staticmethod(lambda: 200.0)):
            w.render()
        self.assertNotIn("sınırlı", w.mode_label.text())
        with patch.object(Hold, "clock", staticmethod(lambda: 201.0)):
            w.status = {**self.STATUS, "cpu_temp": {"target": 82, "state": "limited"}}
            w.render()
            w.status = {**self.STATUS, "cpu_temp": {"target": None, "state": "off"}}  # target off: gone at once
            w.render()
        self.assertNotIn("sınırlı", w.mode_label.text())

    def test_limited_is_not_held_over_a_change(self):
        from boreas.gui.common import Hold
        w = self.w
        lim = {"target": 82, "state": "limited", "floor_w": 30, "ceilings_w": {"ppt_pl1_spl": 40},
               "requested_w": {"ppt_pl1_spl": 30}}
        act = {**lim, "state": "active"}

        def word(status, at=102.0):
            with patch.object(Hold, "clock", staticmethod(lambda: 100.0)):
                w.status = {**self.STATUS, "cpu_temp": lim}
                w.render()
            self.assertIn("sınırlı", w.mode_label.text())
            with patch.object(Hold, "clock", staticmethod(lambda: at)):
                w.status = {**self.STATUS, **status}
                w.render()
            return "sınırlı" in w.mode_label.text()

        self.assertTrue(word({"cpu_temp": act}))
        self.assertTrue(word({"cpu_temp": act}, at=115.0))  # a spiky load leaves gaps of more than 10 s
        self.assertTrue(word({"cpu_temp": {**act, "requested_w": {"ppt_pl1_spl": 32}}}))
        self.assertFalse(word({"cpu_temp": act}, at=131.0))
        self.assertFalse(word({"mode": 1, "cpu_temp": {**act, "target": 88}}))  # another mode was never limited
        self.assertFalse(word({"cpu_temp": {**act, "target": 92}}))  # the target was raised
        self.assertFalse(word({"cpu_temp": {**act, "requested_w": {}}}))  # the mode was applied again: at the ceiling
        self.assertFalse(word({"cpu_temp": {**act, "requested_w": {"ppt_pl1_spl": 40}}}))
        self.assertFalse(word({"cpu_temp": {**lim, "state": "error", "error": "x"}}))  # the controller stopped
        same = {**lim, "ceilings_w": {"ppt_pl1_spl": 30}, "requested_w": {}}  # the user's own limit is the floor
        with patch.object(Hold, "clock", staticmethod(lambda: 100.0)):
            w.status = {**self.STATUS, "cpu_temp": same}
            w.render()
        with patch.object(Hold, "clock", staticmethod(lambda: 102.0)):
            w.status = {**self.STATUS, "cpu_temp": {**same, "state": "active"}}
            w.render()
        self.assertIn("sınırlı", w.mode_label.text())  # no blinking there either

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
