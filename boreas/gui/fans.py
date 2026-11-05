"""The Fans and power page. Opens as a page inside the main window."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..fans import POINTS, fix_curve, safety_floor
from ..i18n import _, mode_name, tr_msg
from .common import Hold, T, fs, run_async, u, uv
from .widgets import (CheckDot, Segmented, Switch, ValueSlider, card, footer_bar, hline, row,  # noqa: F401
                      align_sliders, scroll_page, set_info)

FLOOR_SLACK_W = 2  # the controller's recovery step (thermal.RECOVER_STEP_W): how far above the floor "(floor)" is held

T_MIN, T_MAX = 20, 110

PPT_LABELS = {
    "ppt_pl1_spl": "CPU sustained (SPL)",
    "ppt_pl2_sppt": "CPU short boost (SPPT)",
    "ppt_pl3_fppt": "CPU fast boost (FPPT)",
    "ppt_apu_sppt": "APU SPPT",
    "ppt_platform_sppt": "Platform SPPT",
}
NV_LABELS = {"nv_dynamic_boost": "GPU Dynamic Boost", "nv_temp_target": "GPU temperature target"}
NV_UNITS = {"nv_dynamic_boost": "W", "nv_temp_target": "°C"}
TITLES = {"cpu": "CPU fan", "gpu": "GPU fan", "mid": "Mid fan"}


class CurveEditor(QWidget):
    """8-point fan curve; the points can be dragged, temperature and speed stay monotonic.
    role: 'cpu' | 'gpu' | 'mid'; the line color comes from the theme."""

    changed = Signal()

    def __init__(self, title: str, role: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.role = role
        self.curve = [30, 40, 50, 60, 70, 80, 90, 100, 0, 10, 20, 30, 40, 50, 60, 70]
        self.drag = -1
        self.setMinimumHeight(uv(210))
        self.setMouseTracking(True)

    @property
    def color(self) -> QColor:
        return QColor(T.get(self.role, T["cpu"]))

    def set_curve(self, curve):
        self.curve = list(curve)
        self.update()

    def _area(self) -> QRectF:
        return QRectF(u(44), uv(34), self.width() - u(44) - u(22), self.height() - uv(34) - uv(30))

    def _pt(self, i: int) -> QPointF:
        a = self._area()
        t, s = self.curve[i], self.curve[POINTS + i]
        return QPointF(a.left() + (t - T_MIN) / (T_MAX - T_MIN) * a.width(),
                       a.bottom() - s / 100 * a.height())

    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        a = self._area()
        col = self.color
        # title: colored dot + name
        qp.setPen(Qt.NoPen)
        qp.setBrush(col)
        qp.drawEllipse(QPointF(u(6), u(12)), u(5.5), u(5.5))
        qp.setPen(QColor(T["text"]))
        f = QFont(self.font())
        f.setPixelSize(fs(15))
        f.setBold(True)
        qp.setFont(f)
        qp.drawText(QRectF(u(20), 0, self.width() - u(20), u(24)), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        small = QFont(self.font())
        small.setPixelSize(fs(12, 11))
        qp.setFont(small)
        grid = QPen(QColor(T["grid"]))
        for pct in range(0, 101, 20):
            y = a.bottom() - pct / 100 * a.height()
            qp.setPen(grid)
            qp.drawLine(QPointF(a.left(), y), QPointF(a.right(), y))
            qp.setPen(QColor(T["dim"]))
            qp.drawText(QRectF(0, y - 8, u(40), 16), Qt.AlignRight | Qt.AlignVCenter, f"{pct}%")
        for t in range(T_MIN, T_MAX + 1, 10):
            x = a.left() + (t - T_MIN) / (T_MAX - T_MIN) * a.width()
            qp.setPen(grid)
            qp.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
            qp.setPen(QColor(T["dim"]))
            qp.drawText(QRectF(x - 20, a.bottom() + 5, 40, 16), Qt.AlignCenter, f"{t}°")

        path = QPainterPath(self._pt(0))
        for i in range(1, POINTS):
            path.lineTo(self._pt(i))
        pen = QPen(col, 2.4)
        pen.setJoinStyle(Qt.RoundJoin)
        qp.setPen(pen)
        qp.setBrush(Qt.NoBrush)
        qp.drawPath(path)
        for i in range(POINTS):
            pt = self._pt(i)
            qp.setBrush(QColor("#ffffff") if i == self.drag else col)
            qp.setPen(QPen(col, 2) if i == self.drag else Qt.NoPen)
            qp.drawEllipse(pt, 6, 6)
            if i == self.drag:
                qp.setPen(QColor(T["text"]))
                qp.drawText(QRectF(pt.x() - 40, pt.y() - 28, 80, 16), Qt.AlignCenter,
                            f"{self.curve[i]}° · {self.curve[POINTS + i]}%")

    def _nearest(self, pos) -> int:
        best, dist = -1, 14.0
        for i in range(POINTS):
            d = (self._pt(i) - pos).manhattanLength()
            if d < dist:
                best, dist = i, d
        return best

    def mousePressEvent(self, e):
        self.drag = self._nearest(e.position())
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag < 0:
            return
        a = self._area()
        pos = e.position()
        t = round(T_MIN + (pos.x() - a.left()) / a.width() * (T_MAX - T_MIN))
        s = round((a.bottom() - pos.y()) / a.height() * 100)
        i = self.drag
        lo_t = self.curve[i - 1] if i > 0 else T_MIN
        hi_t = self.curve[i + 1] if i < POINTS - 1 else T_MAX
        self.curve[i] = max(lo_t, min(hi_t, t))
        self.curve[POINTS + i] = max(safety_floor(self.curve[i]), min(100, s))
        # keep speeds monotonic: push the points on the right up and the ones on the left down
        for j in range(i + 1, POINTS):
            self.curve[POINTS + j] = max(self.curve[POINTS + j], self.curve[POINTS + i])
        for j in range(i - 1, -1, -1):
            self.curve[POINTS + j] = min(self.curve[POINTS + j], self.curve[POINTS + i])
        self.update()

    def mouseReleaseEvent(self, _):
        if self.drag >= 0:
            self.drag = -1
            self.curve = fix_curve(self.curve)
            self.update()
            self.changed.emit()


class FansWindow(QWidget):
    """Mode choice, fan curves, power limits and the battery card; Saved / Apply at the bottom."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.mode_id = None
        self.mode_names: dict = {}
        self.entry = None
        self._loading = False
        self._floor_hold = Hold()  # keeps "(floor)" on the Power card's status line from blinking
        self._floor_key = None     # the mode, target and floor the hold belongs to

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(u(24), uv(18), u(24), uv(16))
        root.setSpacing(uv(16))

        # Mode
        mc = QFrame()
        mc.setObjectName("card")
        ml = QVBoxLayout(mc)
        ml.setContentsMargins(u(20), uv(14), u(20), uv(14))
        ml.setSpacing(uv(10))
        top = QHBoxLayout()
        top.setSpacing(u(12))
        mod_lb = QLabel(_("Mode"))
        mod_lb.setContentsMargins(0, 6, 0, 0)
        top.addWidget(mod_lb, 0, Qt.AlignTop)
        self.mode_seg = Segmented(lambda m: self._select_mode(m, activate=True), width=0, flow=True)
        top.addWidget(self.mode_seg, 1, Qt.AlignTop)
        ml.addLayout(top)
        btns = QHBoxLayout()
        btns.setSpacing(u(8))
        btns.addStretch(1)
        self.add_btn = QPushButton(_("Add mode"))
        self.rename = QPushButton(_("Rename"))
        self.remove = QPushButton(_("Delete"))
        for b, fn in ((self.add_btn, self._add_mode), (self.rename, self._rename_mode), (self.remove, self._remove_mode)):
            b.setObjectName("btn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            btns.addWidget(b)
        ml.addLayout(btns)
        root.addWidget(mc)

        # Fan curves
        fc, fl, _info = card(_("Fan curves"))
        self.editors: dict[str, CurveEditor] = {}
        fl.addSpacing(uv(10))
        for fan in main.status.get("fan_curves", []):
            ed = CurveEditor(_(TITLES[fan]), fan)
            ed.changed.connect(self._dirty)
            self.editors[fan] = ed
            fl.addWidget(ed)
        if not self.editors:
            lb = QLabel(_("This device doesn't support custom fan curves."))
            lb.setObjectName("hint")
            fl.addWidget(lb)
        fl.addWidget(hline())
        self.fans_apply = Switch(48, 28)
        self.fans_apply.toggled.connect(self._dirty)
        reset = QPushButton(_("Factory curve"))
        reset.setObjectName("btn")
        reset.setCursor(Qt.PointingHandCursor)
        reset.clicked.connect(self._factory)
        rw = QWidget()
        rw.setFixedHeight(uv(64))
        rh = QHBoxLayout(rw)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(u(16))
        rh.addWidget(self.fans_apply)
        rh.addWidget(QLabel(_("Apply custom fan curve")))
        rh.addStretch(1)
        rh.addWidget(reset)
        fl.addWidget(rw)
        root.addWidget(fc)

        # Power
        pc, pl, self.power_info = card(_("Power"))  # the live state of the CPU temperature target
        # CPU Boost (boost + EPP selector) at the top of the Power card, above SPL
        self.cpu_boost_cb: QComboBox | None = None
        if main.status.get("cpu_boost_supported", True):
            cb = QComboBox()
            cb.setFixedWidth(u(220))
            cb.setCursor(Qt.PointingHandCursor)
            self.cpu_boost_cb = cb
            self._fill_boost_items(performance=False)
            cb.currentIndexChanged.connect(self._dirty)
            pl.addWidget(row(_("CPU Boost"), cb, height=56))
            pl.addWidget(hline())
        # Power profile (cpufreq governor) under CPU Boost: what the desktop's power menu set
        self.cpu_governor_cb: QComboBox | None = None
        governors = main.status.get("cpu_governors") or []
        if len(governors) > 1:
            cb = QComboBox()
            cb.setFixedWidth(u(220))
            cb.setCursor(Qt.PointingHandCursor)
            for g_key, g_label in (
                ("default", _("Default")),
                ("powersave", _("Power saver")),
                ("performance", _("Performance")),
            ):
                if g_key in governors:
                    cb.addItem(g_label, g_key)
            cb.currentIndexChanged.connect(self._dirty)
            cb.currentIndexChanged.connect(self._sync_boost_items)
            self.cpu_governor_cb = cb
            r = row(_("Power profile"), cb, height=56, info=_("Default leaves the governor to the driver. Power saver lets the clock drop when it can and keeps the CPU Boost preference in effect. Performance holds every core at its highest clock; CPU Boost is then only on or off."))
            self.cpu_governor_info = r.info
            pl.addWidget(r)
            pl.addWidget(hline())
        self.sliders: dict[str, ValueSlider] = {}
        attrs = main.status.get("attrs", {})
        for name, label in {**PPT_LABELS, **NV_LABELS}.items():
            a = attrs.get(name)
            if not a or a.get("min") is None or a.get("max") is None:
                continue
            s = ValueSlider(_(label), a["min"], a["max"], a["current"] or a["min"], NV_UNITS.get(name, "W"))
            s.slider.valueChanged.connect(self._dirty)
            self.sliders[name] = s
            pl.addWidget(s)
        # CPU temperature target, independent of the power box; top value disables feedback
        self.cpu_temp = None
        ct = main.status.get("cpu_temp")
        if ct:
            pl.addWidget(hline())
            self.cpu_temp_info = _("Lowers the CPU power limits toward the target; short bursts can still overshoot by a few degrees, since the firmware sets a floor on the limits.")
            self.cpu_conflict = _("With the Performance power profile the EPP cannot be lowered: when the target is exceeded the controller switches boost off and falls back to the default power profile until the CPU cools.")
            self.cpu_temp = ValueSlider(_("CPU temperature target"), ct["min"], ct["max"], ct["max"], "°C",
                                        max_text=_("Default"), info=self.cpu_temp_info)
            self.cpu_temp.slider.valueChanged.connect(self._dirty)
            pl.addWidget(self.cpu_temp)
            self.cpu_temp.slider.valueChanged.connect(self._sync_conflict_hint)
            if self.cpu_governor_cb is not None:
                self.cpu_governor_cb.currentIndexChanged.connect(self._sync_conflict_hint)
        # Sustained power floor: the target never requests SPL below it, the ladder takes over there
        self.cpu_floor = None
        spl = attrs.get("ppt_pl1_spl") or {}
        if ct and spl.get("min") is not None and spl.get("max") is not None:
            self.cpu_floor = ValueSlider(_("Sustained power floor"), spl["min"], spl["max"], spl["min"], "W",
                                         min_text=_("Off"), info=_("The controller never lowers the sustained limit (SPL) below this value. Once it is reached, the controller lowers the EPP, then switches boost off, then the power profile, and restores them in reverse order when the CPU cools. At the lowest position no floor is set and the firmware's own minimum applies."))
            self.cpu_floor.slider.valueChanged.connect(self._dirty)
            pl.addWidget(self.cpu_floor)
        pl.addWidget(hline())
        self.power_apply = Switch()
        self.power_apply.toggled.connect(self._dirty)
        pl.addWidget(row(_("Apply power limits"), self.power_apply, height=56))
        pl.addSpacing(uv(4))
        root.addWidget(pc)

        # The battery card is built in the main window; the design shows it on this page
        pil = getattr(main, "pil_card", None)
        if pil is not None:
            root.addWidget(pil)
        root.addStretch(1)
        align_sliders(content.findChildren(ValueSlider))  # rows with an info icon have a wider label column
        outer.addWidget(scroll_page(content), 1)

        # Bottom bar: status + Apply
        bottom = QHBoxLayout()
        bottom.setSpacing(u(10))
        self.result_icon = CheckDot()
        self.result_icon.hide()
        self.result = QLabel()
        self.result.setObjectName("dim")
        self.result.setWordWrap(True)  # do not cut off a long error message
        self.result.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        apply = QPushButton(_("Apply"))
        apply.setObjectName("primary")
        apply.setCursor(Qt.PointingHandCursor)
        apply.setFixedSize(u(130), uv(44))
        apply.setDefault(True)
        apply.clicked.connect(self._apply)
        bottom.addWidget(self.result_icon)
        bottom.addWidget(self.result, 1)
        bottom.addWidget(apply)
        outer.addWidget(footer_bar(bottom))

        self.reload_modes(main.status["mode"])

    # ---- status row ----

    def _fill_boost_items(self, performance: bool, keep: str | None = None):
        """The CPU Boost choices. Under the performance governor the kernel fixes the EPP, so only
        boost on/off is left: the list shrinks to Performance / Disabled."""
        cb = self.cpu_boost_cb
        if cb is None:
            return
        if performance:
            items = (("max", _("Performance")), ("disabled", _("Disabled")))
        else:
            items = (("default", _("Default")), ("disabled", _("Disabled")), ("efficient", _("Efficient")),
                     ("balanced", _("Balanced")), ("aggressive", _("Aggressive")), ("max", _("Max")))
        keys = [k for k, _l in items]
        if keep is not None and keep not in keys:
            keep = "disabled" if keep == "disabled" else keys[0]
        cb.blockSignals(True)
        cb.clear()
        for key, label in items:
            cb.addItem(label, key)
        idx = cb.findData(keep) if keep is not None else -1
        cb.setCurrentIndex(idx if idx >= 0 else 0)
        cb.blockSignals(False)

    def _sync_conflict_hint(self, *_args):
        """The target's info icon also explains the Performance power profile's effect while both are selected."""
        if self.cpu_temp is None or self.cpu_governor_cb is None:
            return
        on = self.cpu_temp.slider.value() < self.cpu_temp.slider.maximum()
        conflict = on and self.cpu_governor_cb.currentData() == "performance"
        set_info(self.cpu_temp.info, self.cpu_temp_info + ("\n\n" + self.cpu_conflict if conflict else ""))

    def render_cpu_temp(self, ct: dict | None, active_mode) -> None:
        """The Power card's info line: what the target is doing right now in the active mode."""
        if not ct or ct.get("target") is None or self.mode_id != active_mode:
            self._floor_hold.clear()
            self.power_info.setText("")
            return
        if ct.get("state") == "error" and ct.get("error"):
            self._floor_hold.clear()
            self.power_info.setText(tr_msg(ct["error"]))
            return
        floor = ct.get("floor_w")
        key = (active_mode, ct["target"], floor)
        if key != self._floor_key:  # another mode, target or floor: nothing seen before applies to it
            self._floor_key = key
            self._floor_hold.clear()
        parts = [_("Target {temp} °C").format(temp=ct["target"])]
        pl1 = (ct.get("requested_w") or {}).get("ppt_pl1_spl")
        if pl1 is None:
            pl1 = (ct.get("ceilings_w") or {}).get("ppt_pl1_spl")
        if pl1 is not None:
            # At the floor is a fact about the limit, whatever the temperature is this second. Around the
            # target the controller also takes one recovery step and cuts it back at once; only that step
            # is bridged, by showing the floor a little longer. Any other value is shown as it is.
            if floor is not None and pl1 <= floor:
                self._floor_hold(True)
                text = _("PL1 {watts} W").format(watts=pl1) + f" ({_('floor')})"
            elif floor is not None and pl1 <= floor + FLOOR_SLACK_W and self._floor_hold(False):
                text = _("PL1 {watts} W").format(watts=floor) + f" ({_('floor')})"
            else:
                self._floor_hold.clear()
                text = _("PL1 {watts} W").format(watts=pl1)
            parts.append(text)
        engaged = (ct.get("ladder") or [])[:ct.get("stage") or 0]
        kinds = [r.split(":")[0] for r in engaged]
        if "epp" in kinds:
            parts.append(_("EPP lowered"))
        if "boost" in kinds:
            parts.append(_("boost off"))
        if "governor" in kinds:
            parts.append(_("power profile lowered"))
        self.power_info.setText(" · ".join(parts))

    def _sync_boost_items(self, *_args):
        if self.cpu_governor_cb is None or self.cpu_boost_cb is None:
            return
        performance = self.cpu_governor_cb.currentData() == "performance"
        current = self.cpu_boost_cb.currentData()
        if performance != (self.cpu_boost_cb.count() == 2):
            self._fill_boost_items(performance, keep=current)
            self._dirty()

    def _set_status(self, text: str):
        self.result.setText(tr_msg(text))
        self.result_icon.setVisible(text == "Saved")

    def _dirty(self, *_args):
        if not self._loading:
            self._set_status("Not saved")

    # ---- modes ----

    def reload_modes(self, select: int):
        modes = self.main.status["modes"]
        self.mode_names = {m["id"]: mode_name(m) for m in modes}
        self.mode_seg.clear()
        for m in modes:
            self.mode_seg.add(m["id"], mode_name(m))
        self._select_mode(select if select in self.mode_names else next(iter(self.mode_names), None))

    def _select_mode(self, mode_id, activate: bool = False):
        if mode_id is None:
            return
        self.mode_id = mode_id
        self.mode_seg.set_checked(mode_id)
        builtin = mode_id in (0, 1, 2)
        self.rename.setEnabled(not builtin)
        self.remove.setEnabled(not builtin)
        def fetch():
            if activate and self.main.status.get("mode") != mode_id:
                self.main.call("set_mode", mode=mode_id)
            return self.main.call("get_mode", mode=mode_id)
        def after(r):
            self._show_entry(r)
            if activate:
                self.main.refresh()
        run_async(fetch, after)

    def _show_entry(self, r):
        if isinstance(r, Exception):
            self.result_icon.hide()
            self.result.setText(tr_msg(str(r)))
            return
        self._loading = True
        try:
            self.entry = r["mode"]
            self.fans_apply.setChecked(self.entry["fans_apply"])
            self.power_apply.setChecked(self.entry["power_apply"])
            stored = {**(self.entry.get("ppt") or {}), **(self.entry.get("nv") or {})}
            for name, s in self.sliders.items():
                v = stored.get(name)
                # without a saved value, do not keep the previous mode's value (back to the default)
                s.slider.setValue(int(v if v is not None else self._fallback(name)))
            if self.cpu_governor_cb is not None:
                idx = self.cpu_governor_cb.findData(self.entry.get("cpu_governor") or "default")
                self.cpu_governor_cb.setCurrentIndex(idx if idx >= 0 else 0)
            if self.cpu_boost_cb is not None:
                performance = self.cpu_governor_cb is not None and self.cpu_governor_cb.currentData() == "performance"
                self._fill_boost_items(performance, keep=self.entry.get("cpu_boost") or "default")
            if self.cpu_temp:
                self.cpu_temp.slider.setValue(self.entry.get("cpu_temp") or self.cpu_temp.slider.maximum())
                self._sync_conflict_hint()
            if self.cpu_floor:
                self.cpu_floor.slider.setValue(self.entry.get("cpu_temp_floor_w") or self.cpu_floor.slider.minimum())
            for fan, ed in self.editors.items():
                curve = self.entry.get(f"fan_{fan}")
                if curve:
                    ed.set_curve(curve)
                else:
                    self._load_default(fan)
        finally:
            self._loading = False
        self._set_status("Saved")

    def _fallback(self, name: str) -> int:
        a = self.main.status.get("attrs", {}).get(name) or {}
        if self.mode_id == self.main.status.get("mode") and a.get("current") is not None:
            return a["current"]
        for k in ("default", "current", "min"):
            if a.get(k) is not None:
                return a[k]
        return 0

    def _load_default(self, fan):
        mode = self.mode_id

        def done(r):
            if not isinstance(r, Exception) and mode == self.mode_id:
                self.editors[fan].set_curve(r["curve"])
        run_async(lambda: self.main.call("default_curve", fan=fan, mode=mode), done)

    def _factory(self):
        # factory settings: reset the curves, the power limits and both check boxes
        for fan in self.editors:
            self._load_default(fan)
        self.fans_apply.setChecked(False)
        self.power_apply.setChecked(False)
        for name, s in self.sliders.items():
            a = self.main.status.get("attrs", {}).get(name) or {}
            if a.get("default") is not None:
                s.slider.setValue(a["default"])
        if self.cpu_governor_cb is not None:
            self.cpu_governor_cb.setCurrentIndex(0)
        if self.cpu_boost_cb is not None:
            self._fill_boost_items(performance=False, keep="default")
        if self.cpu_temp:
            self.cpu_temp.slider.setValue(self.cpu_temp.slider.maximum())
        if self.cpu_floor:
            self.cpu_floor.slider.setValue(self.cpu_floor.slider.minimum())
        self._set_status("Not saved")

    def _add_mode(self):
        def done(r):
            if isinstance(r, Exception):
                self._set_status(str(r))
                return
            self.main.refresh(lambda: self.reload_modes(r["mode"]))
        run_async(lambda: self.main.call("add_mode", source=self.mode_id), done)

    def _rename_mode(self):
        name, ok = QInputDialog.getText(self, _("Mode name"), _("New name:"), text=self.mode_names.get(self.mode_id, ""))
        if ok and name.strip():
            mode = self.mode_id
            run_async(lambda: self.main.call("update_mode", mode=mode, data={"name": name.strip()}),
                      lambda _: self.main.refresh(lambda: self.reload_modes(mode)))

    def _remove_mode(self):
        mode = self.mode_id
        run_async(lambda: self.main.call("remove_mode", mode=mode),
                  lambda _: self.main.refresh(lambda: self.reload_modes(self.main.status["mode"])))

    def _apply(self):
        data = {
            "fans_apply": self.fans_apply.isChecked(),
            "power_apply": self.power_apply.isChecked(),
            "ppt": {n: s.slider.value() for n, s in self.sliders.items() if n in PPT_LABELS},
            "nv": {n: s.slider.value() for n, s in self.sliders.items() if n in NV_LABELS},
        }
        if self.cpu_boost_cb is not None:
            data["cpu_boost"] = self.cpu_boost_cb.currentData() or "default"
        if self.cpu_governor_cb is not None:
            data["cpu_governor"] = self.cpu_governor_cb.currentData() or "default"
        if self.cpu_temp:
            v = self.cpu_temp.slider.value()
            data["cpu_temp"] = v if v < self.cpu_temp.slider.maximum() else None
        if self.cpu_floor:
            v = self.cpu_floor.slider.value()
            data["cpu_temp_floor_w"] = v if v > self.cpu_floor.slider.minimum() else None
        for fan, ed in self.editors.items():
            data[f"fan_{fan}"] = ed.curve
        mode = self.mode_id
        self._set_status("Applying…")

        def apply_and_activate():
            if self.main.status.get("mode") != mode:
                self.main.call("set_mode", mode=mode)
            return self.main.call("update_mode", mode=mode, data=data)

        def done(r):
            self._set_status(str(r) if isinstance(r, Exception) else "Saved")
            self.main.refresh()
        run_async(apply_and_activate, done)
