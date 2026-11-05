"""Boreas main window and tray icon."""

from __future__ import annotations

import html
import os
import subprocess
import sys

from PySide6.QtCore import QPointF, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QMessageBox, QPushButton, QSlider, QStackedWidget,
                               QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import __version__, i18n, update
from ..i18n import _, mode_name, tr_msg
from ..client import DaemonError, call  # noqa: F401  (the tests use DaemonError as A.DaemonError)
from . import screen
from .common import ACCENT, MODE_COLORS, Hold, T, apply_theme, bus, run_async, u, uv, watch_system_theme
from .fans import FansWindow
from .widgets import (Segmented, Switch, ValueSlider, card, fan_icon, footer_bar, hint, hline, row,
                      scroll_page, theme_icon)

AUTOSTART = os.path.expanduser("~/.config/autostart/boreas.desktop")
AUTOSTART_ENTRY = ("[Desktop Entry]\nType=Application\nName=Boreas\n"
                   "Exec=boreas --hidden\nIcon=boreas\nX-GNOME-Autostart-enabled=true\n")
GPU_LABELS = [("eco", "Eco"), ("standard", "Standard"), ("optimized", "Optimized"), ("ultimate", "Ultimate")]
GPU_HINTS = {
    "eco": "dGPU off, longest battery life",
    "standard": "Hybrid: the iGPU drives the display, the dGPU when needed",
    "optimized": "Eco on battery, Standard on AC",
    "ultimate": "The display runs directly on the dGPU (MUX), needs a reboot",
}
# Always hold the dGPU device open; not counted in the status line (the tooltip still lists them)
GPU_SYSTEM_USERS = ("systemd", "systemd-logind", "nvidia-powerd", "nvidia-persiste")
AURA_MODES = [("static", "Static"), ("breathe", "Breathe"), ("cycle", "Color cycle"),
              ("rainbow", "Rainbow"), ("strobe", "Strobe")]
AURA_SPEEDS = [("slow", "Slow"), ("normal", "Normal"), ("fast", "Fast")]

# rows with segmented buttons: keep them off the lines above and below
SEG_ROW = 70

THEME_CYCLE = ["auto", "light", "dark"]
THEME_NAMES = {"auto": "Auto (follows the system)", "light": "Light", "dark": "Dark"}

PAGES = {
    "main": ("Boreas", "Device settings and performance"),
    "fan": ("Fans and power", "Fan curves, power limits and battery"),
}


def gpu_users_summary(users: list[str]) -> tuple[str, str]:
    """A fixed-length count for the status line and the full "command(PID)" list for its tooltip.

    The list can grow long (compositor, Xwayland, every app with a GPU context), and a
    growing label would shift the whole window, so the line shows a count only."""
    if not users:
        return "", ""
    apps = [x for x in users if x.rsplit("(", 1)[0] not in GPU_SYSTEM_USERS]
    if len(apps) == 1:
        line = _("1 process is using the dGPU")
    elif apps:
        line = _("{n} processes are using the dGPU").format(n=len(apps))
    else:
        line = _("only system services hold the dGPU")
    return line, ", ".join(users)


def _wind_paths(simple: bool = False) -> list:
    """Three wind lines on a 64x64 canvas (same as data/boreas.svg). simple: three spaced lines
    without curls for small sizes; at 16-24 px the curls blur into each other."""
    if simple:
        lines = []
        for y, x2 in ((22, 44), (32, 50), (42, 38)):
            p = QPainterPath(QPointF(14, y))
            p.lineTo(x2, y)
            lines.append(p)
        return lines
    a = QPainterPath(QPointF(15, 25))
    a.lineTo(39, 25)
    a.cubicTo(47, 25, 49, 15, 42, 14)
    a.cubicTo(37, 13.5, 35, 18, 37.5, 20)
    b = QPainterPath(QPointF(15, 35))
    b.lineTo(47, 35)
    c = QPainterPath(QPointF(15, 45))
    c.lineTo(33, 45)
    c.cubicTo(41, 45, 43, 55, 36, 56)
    c.cubicTo(31, 56.5, 29, 52, 31.5, 50)
    return [a, b, c]


ICON_SIZES = (16, 20, 22, 24, 32, 48, 64, 128)


def _icon_pixmap(color: str, size: int) -> QPixmap:
    small = size <= 24
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    qp = QPainter(pm)
    qp.setRenderHint(QPainter.Antialiasing)
    qp.scale(size / 64, size / 64)
    qp.setBrush(QColor(color))
    qp.setPen(Qt.NoPen)
    qp.drawEllipse(QPointF(32, 32), 30 if small else 28, 30 if small else 28)
    qp.setPen(QPen(QColor("#1c1c1c"), 7 if small else 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    qp.setBrush(Qt.NoBrush)
    for p in _wind_paths(simple=small):
        qp.drawPath(p)
    qp.end()
    return pm


def tray_icon(color: str) -> QIcon:
    """Every size is drawn separately: a single scaled-down image looks blurry in the tray and title bar."""
    ic = QIcon()
    for size in ICON_SIZES:
        ic.addPixmap(_icon_pixmap(color, size))
    return ic


def _combo(width: int) -> QComboBox:
    cb = QComboBox()
    cb.setFixedWidth(u(width))
    cb.setCursor(Qt.PointingHandCursor)
    return cb


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("Boreas")
        self.setWindowIcon(tray_icon(ACCENT))
        self.settings = QSettings("boreas", "boreas")
        self.status: dict = {}
        self.fans_window: FansWindow | None = None
        self.page = "main"
        self.last_ac = None
        self.busy = False
        self._limited_hold = Hold(30)  # "limited" on the mode line; a spiky load leaves gaps of 10 s and more
        self._limited_key = None       # the mode and target the hold belongs to
        self.stale = False      # a new request came in while a query was running: ask again when it finishes
        self.pending = []       # callbacks to run when a new status arrives
        # a clicked choice stays on screen until the service has applied it and a new status arrives
        # (so a stale status in flight does not make the button jump back): key -> (value, sequence no)
        self.hold: dict = {}
        self._hold_seq = 0
        self.error = ""         # error of the last action (cleared on success)
        self.status_error = ""  # the service cannot be reached
        self.update_version = ""  # version number if GitHub has a newer version
        self.updating = False     # "Update" was pressed, download/install in progress
        self.quitting = False
        # do not refuse to close when the session ends either
        QApplication.instance().commitDataRequest.connect(lambda _: setattr(self, "quitting", True))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header: name + subtitle, "Fans and power" at the top right
        head = QWidget()
        # grid: title and buttons on one row, subtitle and live info on the row below (aligned)
        hg = QGridLayout(head)
        hg.setContentsMargins(u(24), uv(20), u(24), 0)
        hg.setHorizontalSpacing(u(12))
        hg.setVerticalSpacing(uv(4))
        hg.setColumnStretch(0, 1)
        self.title = QLabel(PAGES["main"][0])
        self.title.setObjectName("title")
        self.subtitle = QLabel(_(PAGES["main"][1]))
        self.subtitle.setObjectName("subtitle")
        self.title.setWordWrap(True)      # long translations wrap instead of being cut off
        self.subtitle.setWordWrap(True)
        self.fans_btn = QPushButton(_("Fans and power"))
        self.fans_btn.setObjectName("fanbtn")
        self.fans_btn.setCheckable(True)
        self.fans_btn.setCursor(Qt.PointingHandCursor)
        self.fans_btn.setFixedHeight(uv(38))
        self.fans_btn.setIconSize(QSize(u(18), u(18)))
        self.fans_btn.clicked.connect(self.open_fans)
        self.fans_btn.toggled.connect(lambda _: self._fan_icon())
        # Theme button: Auto → Light → Dark
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("iconbtn")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setFixedSize(uv(38), uv(38))
        self.theme_btn.setIconSize(QSize(u(18), u(18)))
        self.theme_btn.clicked.connect(self.cycle_theme)
        # Language button: picked from a menu, the app restarts
        self.lang_btn = QPushButton()
        self.lang_btn.setObjectName("iconbtn")
        self.lang_btn.setCursor(Qt.PointingHandCursor)
        self.lang_btn.setFixedSize(uv(38), uv(38))
        self.lang_btn.setIconSize(QSize(u(18), u(18)))
        self.lang_btn.setToolTip(_("Language") + f": {i18n.LANGUAGES[i18n.current]}")
        self.lang_menu = QMenu(self)
        for code, native in i18n.LANGUAGES.items():
            act = self.lang_menu.addAction(native)
            act.setCheckable(True)
            act.setChecked(code == i18n.current)
            act.triggered.connect(lambda _c=False, c=code: self.set_language(c))
        self.lang_btn.clicked.connect(
            lambda: self.lang_menu.popup(self.lang_btn.mapToGlobal(self.lang_btn.rect().bottomLeft())))
        btns = QHBoxLayout()
        btns.setSpacing(u(4))
        btns.addWidget(self.lang_btn)
        btns.addWidget(self.theme_btn)
        btns.addWidget(self.fans_btn)
        # Live CPU temperature and fan speeds on the Fans and power page, to watch the result while tuning.
        # Same style and row as the subtitle, so the baselines line up.
        self.live_info = QLabel()
        self.live_info.setObjectName("subtitle")
        self.live_info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.live_info.hide()
        # right edge on the inner edge of the cards: the same line as the card info on the main page ("… rpm")
        self.live_info.setContentsMargins(0, 0, u(20), 0)
        # wrapping labels get no cell alignment (Qt gets their height wrong);
        # the label aligns itself
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.subtitle.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.live_info.setAlignment(Qt.AlignRight | Qt.AlignTop)
        hg.addWidget(self.title, 0, 0)
        hg.addLayout(btns, 0, 1, Qt.AlignRight | Qt.AlignTop)
        # Update notice: right of the subtitle, below "Fans and power", on the same right edge
        self.update_info = QLabel()
        self.update_info.setObjectName("subtitle")
        self.update_info.setTextFormat(Qt.RichText)
        self.update_info.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.update_info.setContentsMargins(0, 0, u(20), 0)
        self.update_info.linkActivated.connect(self._notice_link)
        self.update_info.hide()
        info_col = QVBoxLayout()
        info_col.setSpacing(uv(2))
        info_col.addWidget(self.live_info)
        info_col.addWidget(self.update_info)
        hg.addWidget(self.subtitle, 1, 0)
        hg.addLayout(info_col, 1, 1)
        root.addWidget(head)

        # Warning row (service error, action error)
        self.notice = QLabel()
        self.notice.setObjectName("dim")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.RichText)
        self.notice.setContentsMargins(u(24), uv(10), u(24), 0)
        self.notice.linkActivated.connect(self._notice_link)
        self.notice.hide()
        root.addWidget(self.notice)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # ---- main page ----
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(u(24), uv(18), u(24), uv(16))
        cl.setSpacing(uv(16))

        # Performance
        card_, lay, self.perf_info = card(_("Performance mode"))
        self.mode_seg = Segmented(self.set_mode)
        for key in (2, 0, 1):
            self.mode_seg.add(key, _(i18n.BUILTIN_NAMES[key]))
        self.mode_group = self.mode_seg.group
        lay.addWidget(row(_("Mode"), self.mode_seg, height=SEG_ROW))
        self.mode_label = hint()
        lay.addWidget(self.mode_label)
        cl.addWidget(card_)

        # GPU
        card_, lay, self.gpu_info = card(_("GPU mode"))
        self.gpu_seg = Segmented(self.set_gpu)
        for (key, label), w in zip(GPU_LABELS, (75, 105, 115, 100)):
            self.gpu_seg.add(key, _(label), w)
        self.gpu_group = self.gpu_seg.group
        lay.addWidget(row(_("Mode"), self.gpu_seg, height=SEG_ROW))
        self.gpu_label = hint()
        lay.addWidget(self.gpu_label)
        self.gpu_ebpf_div = hline()
        lay.addWidget(self.gpu_ebpf_div)
        self.gpu_ebpf = Switch()
        self.gpu_ebpf.clicked.connect(self.set_gpu_backend)
        self.gpu_ebpf_row = row(_("eBPF engine (no-reboot Eco)") + " · " + _("Experimental"), self.gpu_ebpf, height=56)
        lay.addWidget(self.gpu_ebpf_row)
        self.gpu_card = card_
        cl.addWidget(card_)

        # Display
        card_, lay, self.screen_info = card(_("Built-in display"))
        self.screen_seg = Segmented(self.set_screen)
        self.screen_group = self.screen_seg.group
        self.screen_row = row(_("Refresh rate"), self.screen_seg, height=SEG_ROW)
        lay.addWidget(self.screen_row)
        lay.addWidget(hline())
        self.overdrive = Switch()
        self.overdrive.clicked.connect(self.set_overdrive)
        self.overdrive_row = row(_("Panel overdrive"), self.overdrive, height=56)
        lay.addWidget(self.overdrive_row)
        cl.addWidget(card_)

        # Keyboard
        card_, lay, self.kbd_info = card(_("Keyboard"))
        self.aura_mode = _combo(266)
        for k, label in AURA_MODES:
            self.aura_mode.addItem(_(label), k)
        self.aura_speed = _combo(340)
        for k, label in AURA_SPEEDS:
            self.aura_speed.addItem(_(label), k)
        self.aura_color = QPushButton()
        self.aura_color.setFixedSize(u(60), uv(40))
        self.aura_color.setCursor(Qt.PointingHandCursor)
        self.aura_color.clicked.connect(self.pick_color)
        self.color = QColor(self.settings.value("aura_color", "#ff0000"))
        self.kbd_level = QSlider(Qt.Horizontal)
        self.kbd_level.setFixedSize(u(340), max(u(28), 22))  # do not clip the handle
        self.kbd_level.setRange(0, 3)
        self.kbd_level.setPageStep(1)
        self.kbd_level.sliderReleased.connect(self.set_kbd)
        self.kbd_level.actionTriggered.connect(lambda a: self._slider_action(a, self.set_kbd))
        efekt = QHBoxLayout()
        efekt.setSpacing(u(14))
        efekt.addWidget(self.aura_mode)
        efekt.addWidget(self.aura_color)
        lay.addWidget(row(_("Effect"), efekt, height=60))
        lay.addWidget(hline())
        lay.addWidget(row(_("Speed"), self.aura_speed))
        lay.addWidget(hline())
        lay.addWidget(row(_("Brightness"), self.kbd_level, height=56))
        self.aura_mode.activated.connect(lambda _: self.set_aura())
        self.aura_speed.activated.connect(lambda _: self.set_aura())
        self._paint_color()
        self.kbd_card = card_
        cl.addWidget(card_)
        cl.addStretch(1)

        # Battery: shown on the "Fans and power" page in the design (FansWindow places it)
        self.pil_card, lay, self.bat_info = card(_("Battery charge limit"))
        self.charge_row = ValueSlider(_("Charge limit"), 40, 100, 80, "%")
        self.charge = self.charge_row.slider
        self.charge.setSingleStep(5)
        self.charge.setPageStep(5)
        self.charge.sliderReleased.connect(self.set_charge)
        self.charge.actionTriggered.connect(lambda a: self._slider_action(a, self.set_charge))
        lay.addWidget(self.charge_row)
        lay.addSpacing(uv(4))

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        pl.addWidget(scroll_page(content), 1)

        # Bottom bar (main page): start at login, version, Quit
        foot = QHBoxLayout()
        foot.setSpacing(u(16))
        self.autostart = QCheckBox(_("Start at login"))
        self.autostart.setChecked(os.path.exists(AUTOSTART))
        self.autostart.setCursor(Qt.PointingHandCursor)
        self.autostart.clicked.connect(self.set_autostart)
        quit_btn = QPushButton(_("Quit"))
        quit_btn.setObjectName("exit")
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setFixedHeight(uv(44))
        quit_btn.clicked.connect(self.quit_app)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("dim")
        foot.addWidget(self.autostart)
        foot.addStretch(1)
        foot.addWidget(ver)
        foot.addWidget(quit_btn)
        foot_bar = footer_bar(foot)
        pl.addWidget(foot_bar)
        self.stack.addWidget(page)

        self.setFixedWidth(u(570))  # changes with the scale
        self.setMinimumHeight(u(420))
        # open sized to the main page content; shorter if it does not fit the screen (a scroll bar appears)
        natural = head.sizeHint().height() + content.sizeHint().height() + foot_bar.sizeHint().height() + 8
        screen_ = QGuiApplication.primaryScreen()
        avail = screen_.availableGeometry().height() if screen_ else 1000
        self.resize(self.width(), max(u(420), min(natural, avail - 80)))

        # Tray
        self.tray = QSystemTrayIcon(tray_icon(MODE_COLORS[0]), self)
        self.tray.activated.connect(self._tray_click)
        self.tray_menu = QMenu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        # update check: shortly after start, then once a day
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_updates)
        self.update_timer.start(24 * 3600 * 1000)
        QTimer.singleShot(10_000, self.check_updates)
        bus.changed.connect(self._fan_icon)
        bus.changed.connect(self._theme_icon)
        self._fan_icon()
        self._theme_icon()
        self._fit_header()
        self._build_screen_buttons()
        self.refresh()

    # ---- daemon ----

    def call(self, cmd, **kw):
        return call(cmd, **kw)

    def action(self, cmd, after=None, hold=None, **kw):
        """Run the command in the background, then refresh the status.
        hold=(key, value): the choice stays on screen until the first fresh status after the action."""
        token = None
        if hold:
            self._hold_seq += 1
            token = self._hold_seq
            self.hold[hold[0]] = (hold[1], token)

        def done(r):
            if isinstance(r, Exception):
                self.error = str(r)
            else:
                self.error = ""
                if after:
                    after(r)
            self.refresh(then=(lambda: self._release(hold[0], token)) if hold else None)
        run_async(lambda: call(cmd, timeout=90, **kw), done)

    def _held(self, key, default):
        return self.hold[key][0] if key in self.hold else default

    def _release(self, key, token):
        if self.hold.get(key, (None, None))[1] == token:  # if there was a newer click in the meantime, wait for that one
            del self.hold[key]
            if self.status:
                self.render()

    def refresh(self, then=None):
        if then:
            self.pending.append(then)
        if self.busy:
            # the running query may be older than the action that just finished; ask again when it is done
            self.stale = True
            return
        self.busy = True
        self.stale = False

        def done(r):
            self.busy = False
            if isinstance(r, Exception):
                if not self.updating:  # the service restarts during an update; a short disconnect is not an error
                    self.status_error = str(r)
                    self.tray.setToolTip(f"Boreas: {tr_msg(str(r))}")
                    self._render_notice(self.status.get("notice") or "")
            else:
                self.status_error = ""
                self.status = r
                self.render()
                if self.fans_window is None and not getattr(self, "_fans_prebuild", False):
                    self._fans_prebuild = True
                    QTimer.singleShot(400, self._prebuild_fans)
            if self.stale:
                self.refresh()
                return
            callbacks, self.pending = self.pending, []
            if not isinstance(r, Exception):
                for cb in callbacks:
                    cb()
        run_async(lambda: call("status"), done)
        self.timer.setInterval(2000 if self.isVisible() else 5000)

    def _render_notice(self, daemon_notice: str):
        msgs = [m for m in (self.error, self.status_error, daemon_notice) if m]
        parts = [html.escape(tr_msg(m)) for m in msgs]
        link = f"color:{T['blue']}"
        if msgs:
            parts[-1] += f"  <a href='dismiss' style='{link}'>{html.escape(_('dismiss'))}</a>"
        self.notice.setVisible(bool(parts))
        if parts:
            self.notice.setText(" · ".join(parts))
        self._render_update()

    def _render_update(self):
        """Two lines right of the title: "Boreas X is available" / "Update · Release notes · Hide"."""
        link = f"color:{T['blue']}; text-decoration:none"
        if self.updating:
            text = html.escape(_("Updating to {version}…").format(version=self.update_version))
        elif self.update_version:
            def a(href, label):
                return f"<a href='{href}' style='{link}'>{html.escape(label)}</a>"
            text = (html.escape(_("Boreas {version} is available.").format(version=self.update_version)) + "<br>"
                    + " · ".join((a("update", _("Update")), a("notes", _("Release notes")), a("hide", _("Hide")))))
        else:
            text = ""
        self.update_info.setText(text)
        self.update_info.setVisible(bool(text))

    def _notice_link(self, href: str):
        if href == "update":
            self.start_update()
        elif href == "notes":
            QDesktopServices.openUrl(QUrl(update.CHANGELOG_URL))
        elif href == "hide":  # do not show it again for this version
            self.settings.setValue("update_dismissed", self.update_version)
            self.update_version = ""
            self._render_update()
        else:
            self._dismiss_notice()

    def start_update(self):
        """Downloads and installs the latest version (pkexec asks for the administrator password), then restarts the app."""
        if self.updating or not self.update_version:
            return
        self.updating = True
        self.error = ""
        self._render_notice(self.status.get("notice") or "")
        expected = self.update_version

        def done(r):
            self.updating = False
            if isinstance(r, update.UpdateCancelled):
                self.error = "Update cancelled."
            elif isinstance(r, update.NoAuthorization):
                self.error = ("Could not get administrator permission (no password dialog). "
                              "Update manually: git pull && sudo ./install.sh")
            elif isinstance(r, Exception):
                self.error = f"Update failed: {r}"
            else:
                restart_app()  # start again with the new code
                return
            self._render_notice(self.status.get("notice") or "")
        run_async(lambda: update.run_update(expected), done)

    # ---- update ----

    def _updates_enabled(self) -> bool:
        if os.environ.get("BOREAS_NO_UPDATE_CHECK"):
            return False
        return bool(self.settings.value("update_check", True, type=bool))

    def check_updates(self):
        """Shows a notice if the version on GitHub is newer; installing is up to the user."""
        if not self._updates_enabled():
            return

        def done(r):
            if isinstance(r, Exception) or not update.is_newer(r, __version__):
                return  # no network: skip silently
            if r == self.settings.value("update_dismissed", ""):
                return
            self.update_version = r
            self._render_notice(self.status.get("notice") or "")
        run_async(update.fetch_latest, done)

    def set_update_check(self, on: bool):
        self.settings.setValue("update_check", bool(on))
        if on:
            self.check_updates()
        else:
            self.update_version = ""
            self._render_notice(self.status.get("notice") or "")

    def _dismiss_notice(self):
        self.error = ""
        if self.status.get("notice"):
            self.action("clear_notice")
        else:
            self._render_notice("")

    def _slider_action(self, action, apply):
        # clicks, the wheel and arrow keys do not emit sliderReleased; the value changes after this signal
        if action != QSlider.SliderMove:
            QTimer.singleShot(0, apply)

    # ---- pages ----

    def _fan_icon(self, *_args):
        col = T["navOnText"] if self.fans_btn.isChecked() else T["dim"]
        ic = fan_icon(col)
        if ic is not None:
            self.fans_btn.setIcon(ic)

    def _theme_pref(self) -> str:
        v = str(self.settings.value("theme", "auto")).lower()
        return v if v in THEME_CYCLE else "auto"

    def _theme_icon(self, *_args):
        pref = self._theme_pref()
        ic = theme_icon(pref, T["dim"])
        if ic is not None:
            self.theme_btn.setIcon(ic)
        self.theme_btn.setToolTip(_("Theme: {name} (click to change)").format(name=_(THEME_NAMES[pref])))
        lic = theme_icon("lang", T["dim"])
        if lic is not None:
            self.lang_btn.setIcon(lic)

    def cycle_theme(self):
        """Auto → Light → Dark → Auto; the choice is saved."""
        nxt = THEME_CYCLE[(THEME_CYCLE.index(self._theme_pref()) + 1) % len(THEME_CYCLE)]
        self.settings.setValue("theme", nxt)
        apply_theme(QApplication.instance(), nxt)

    def show_page(self, name: str):
        self.page = name
        fan = name == "fan" and self.fans_window is not None
        self.title.setText(_(PAGES["fan" if fan else "main"][0]))
        self.subtitle.setText(_(PAGES["fan" if fan else "main"][1]))
        self.fans_btn.setChecked(fan)
        self.live_info.setVisible(fan and bool(self.live_info.text()))
        self._fit_header()
        if fan:
            self.stack.setCurrentWidget(self.fans_window)
        else:
            self.page = "main"
            self.stack.setCurrentIndex(0)

    def _fit_header(self):
        """If the title and the buttons do not fit, the "Fans and power" button shows only its icon (long translations)."""
        label = _("Fans and power")
        self.fans_btn.setText(label)
        self.fans_btn.setToolTip("")
        title_w = max(self.title.fontMetrics().horizontalAdvance(self.title.text()),
                      self.subtitle.fontMetrics().horizontalAdvance(self.subtitle.text()))
        side = u(48) + self.lang_btn.width() + self.theme_btn.width() + u(16)
        if title_w + side + self.fans_btn.sizeHint().width() > self.width():
            self.fans_btn.setText("")
            self.fans_btn.setToolTip(label)
            self.fans_btn.setFixedWidth(uv(38))
        else:
            self.fans_btn.setMinimumWidth(0)
            self.fans_btn.setMaximumWidth(16777215)

    def _prebuild_fans(self):
        """Build the fans page ahead of time while idle, so opening it the first time does not stutter."""
        if self.fans_window is not None or not self.status.get("modes"):
            return
        try:
            self.fans_window = FansWindow(self)
            self.stack.addWidget(self.fans_window)
        except Exception as e:  # if it fails, it is tried again on first open
            self.fans_window = None
            print(f"fan page prebuild failed: {e}", file=sys.stderr)

    def open_fans(self):
        """"Fans and power" at the top right: opens the page, or goes back to the main page from it."""
        if self.page == "fan":
            self.show_page("main")
            return
        if not self.status:
            self.fans_btn.setChecked(False)
            return
        self.render()
        if self.fans_window is None:
            self.fans_window = FansWindow(self)
            self.stack.addWidget(self.fans_window)
        else:
            self.fans_window.reload_modes(self.status["mode"])
        self.show_page("fan")

    # ---- drawing ----

    def render(self):
        s = self.status
        mode = self._held("mode", s["mode"])
        modes = {m["id"]: m for m in s["modes"]}
        cur = modes.get(mode, {"name": "?", "base": 0, "builtin": True})
        self.mode_seg.set_checked(mode)
        t = s.get("temps", {})
        fans = s.get("fans", {})
        parts = []
        if "cpu" in t:
            parts.append(f"CPU {t['cpu']}°")
        if fans:
            parts.append(" / ".join(str(v) for v in fans.values()) + " rpm")
        self.perf_info.setText(" · ".join(parts))
        self.live_info.setText(" · ".join(parts))
        self.live_info.setVisible(self.page == "fan" and bool(parts))
        saved = _("saved for AC") if s["ac"] else _("saved for battery")
        extra = []
        if s.get("custom_power"):
            extra.append(_("custom power"))
        ct = s.get("cpu_temp") or {}
        # The state flips every second around the target, so the word is held for a while; it is not carried
        # into another mode or target, past a stopped controller, or past limits that are back at their ceiling
        # (the mode was applied again).
        key = (s["mode"], ct.get("target"))
        req = (ct.get("requested_w") or {}).get("ppt_pl1_spl")
        ceil, floor = (ct.get("ceilings_w") or {}).get("ppt_pl1_spl"), ct.get("floor_w")
        restored = ceil is not None and (req is None or req >= ceil) and (floor is None or ceil > floor)
        limited = ct.get("state") == "limited"
        if (key != self._limited_key or ct.get("target") is None or ct.get("state") in ("error", "off")
                or (restored and not limited)):
            self._limited_key = key
            self._limited_hold.clear()
        if self._limited_hold(limited):
            extra.append(_("limited"))
        if self.fans_window is not None:
            self.fans_window.render_cpu_temp(s.get("cpu_temp"), s["mode"])
        self.mode_label.setText(f"{mode_name(cur)} ({saved})" + (" · " + ", ".join(extra) if extra else ""))

        g = s.get("gpu")
        self.gpu_card.setVisible(bool(g))
        if g:
            target = s.get("gpu_target") or g["mode"]
            active = g["mode"]
            for b in self.gpu_group.buttons():
                k = b.property("key")
                b.setChecked(k == self._held("gpu", target if target == "optimized" else active))
                if k == "ultimate":
                    b.setEnabled(g["has_mux"])
                if k in ("eco", "optimized"):
                    b.setEnabled(g["has_eco"])
            text = _(GPU_HINTS.get(target, ""))
            tip = ""
            if g["pending_reboot"] or (target == "ultimate") != (active == "ultimate"):
                text += " · " + _("waiting for a reboot")
            elif target == "eco" and active != "eco":
                text += " · " + _("dGPU in use, will be applied on the next boot")
            if g.get("backend") == "ebpf":
                engine = g.get("engine") or {}
                text = g.get("backend_error") or ("eBPF · " + str(engine.get("policy", "?"))
                    + " · " + str(engine.get("power_state") or "?")
                    + " / " + str(engine.get("runtime_status") or "?"))
                rtd3 = engine.get("rtd3_status")
                if rtd3 and any(word in rtd3.lower() for word in ("disabled", "not supported")):
                    text += " · RTD3: " + rtd3
                summary, tip = gpu_users_summary(engine.get("users") or [])
                if summary:
                    text += " · " + summary
            self.gpu_label.setText(text)
            self.gpu_label.setToolTip(tip)
            self.gpu_info.setText(f"GPU {t['gpu']}°" if "gpu" in t else (g.get("dgpu_runtime") or ""))
            ebpf_avail = bool(g.get("ebpf_available") or g.get("backend") == "ebpf")
            self.gpu_ebpf_div.setVisible(ebpf_avail)
            self.gpu_ebpf_row.setVisible(ebpf_avail)
            self.gpu_ebpf.setChecked(self._held("gpu_backend", g.get("backend")) == "ebpf")

        od = s["attrs"].get("panel_overdrive")
        self.overdrive_row.setVisible(od is not None)
        if od:
            self.overdrive.setChecked(bool(od["current"]))

        kb = s.get("kbd", {})
        if kb.get("brightness") is not None and not self.kbd_level.isSliderDown() and "kbd" not in self.hold:
            self.kbd_level.setMaximum(kb["max"] or 3)
            self.kbd_level.setValue(kb["brightness"])
        a = s.get("aura")
        if a and not self.aura_mode.view().isVisible():
            self.aura_mode.setCurrentIndex(max(0, self.aura_mode.findData(a["mode"])))
            self.aura_speed.setCurrentIndex(max(0, self.aura_speed.findData(a.get("speed", "normal"))))
            self.color = QColor(*a["color"])
            self._paint_color()
        self.aura_mode.setEnabled(s.get("aura_device", False))
        self.aura_color.setEnabled(s.get("aura_device", False))
        self.aura_speed.setEnabled(s.get("aura_device", False))

        bat = s.get("battery", {})
        if bat.get("limit") and not self.charge.isSliderDown() and "charge" not in self.hold:
            self.charge.setValue(bat["limit"])
        info = [f"{bat['capacity']}%"] if bat.get("capacity") is not None else []
        if bat.get("status"):
            info.append(_({"Charging": "charging", "Discharging": "discharging",
                           "Full": "full", "Not charging": "not charging"}.get(bat["status"], bat["status"])))
        if bat.get("power_w"):
            info.append(f"{bat['power_w']} W")
        self.bat_info.setText(" · ".join(info))

        self._render_notice(s.get("notice") or "")

        self.tray.setIcon(tray_icon(MODE_COLORS.get(cur["base"], ACCENT)))
        self.tray.setToolTip(f"Boreas · {mode_name(cur)}" + (f" · GPU {_(dict(GPU_LABELS).get(g['mode'], g['mode']))}" if g else ""))
        self._build_tray_menu()

        if self.last_ac != s["ac"]:  # also on the first status (the automatic display setting is applied at start)
            self._auto_screen(s["ac"])
        self.last_ac = s["ac"]
        self._render_screen()

    def _build_tray_menu(self):
        s = self.status
        sig = (tuple((x["id"], x["name"]) for x in s.get("modes", [])), s.get("mode"),
               s.get("gpu_target"), (s.get("gpu") or {}).get("mode"))
        if getattr(self, "_menu_sig", None) == sig or self.tray_menu.isVisible():
            return
        self._menu_sig = sig
        m = self.tray_menu
        m.clear()
        for mode in s.get("modes", []):
            act = QAction(mode_name(mode), m, checkable=True, checked=mode["id"] == s["mode"])
            act.triggered.connect(lambda _=False, i=mode["id"]: self.set_mode(i))
            m.addAction(act)
        if s.get("gpu"):
            m.addSeparator()
            target = s.get("gpu_target") or s["gpu"]["mode"]
            for k, label in GPU_LABELS:
                act = QAction(f"GPU: {_(label)}", m, checkable=True, checked=k == target)
                act.triggered.connect(lambda _=False, key=k: self.set_gpu(key))
                m.addAction(act)
        m.addSeparator()
        upd = QAction(_("Check for updates"), m, checkable=True, checked=self._updates_enabled())
        upd.toggled.connect(self.set_update_check)
        m.addAction(upd)
        m.addAction(_("Show"), self.show_near_tray)
        m.addAction(_("Quit"), self.quit_app)

    # ---- display ----

    def _build_screen_buttons(self):
        info = screen.internal()
        self.screen_info.setText(f"{info['name']} · {info['current']} Hz" if info else "")
        rates = sorted(info["rates"]) if info else []
        items = []
        if rates:
            items.append((rates[0], f"{rates[0]} Hz"))
            if len(rates) > 1:
                items.append((rates[-1], f"{rates[-1]} Hz"))
            items.append(("auto", _("Auto")))
        self.screen_seg.clear()
        for k, label in items:
            self.screen_seg.add(k, label)
        self.screen_row.setVisible(bool(items))  # the row is hidden without kscreen-doctor
        self.screen_rates = rates
        if info:
            self.screen_current = info["current"]
            self.settings.setValue("screen_rate", info["current"])
        self._render_screen(info)

    def _render_screen(self, info=None):
        if not self.screen_rates:
            return
        auto = self.settings.value("screen_auto", False, type=bool)
        # no saved setting on first start: use the last value read from the screen
        cur = ((info or {}).get("current") or getattr(self, "screen_current", 0)
               or self.settings.value("screen_rate", 0, type=int))
        for b in self.screen_group.buttons():
            k = b.property("key")
            b.setChecked(auto if k == "auto" else (not auto and k == cur))

    def set_screen(self, key):
        if key == "auto":
            self.settings.setValue("screen_auto", True)
            self._auto_screen(self.status.get("ac", True))
            self._render_screen()
            return
        self.settings.setValue("screen_auto", False)
        self._apply_rate(key)

    def _auto_screen(self, ac: bool):
        if self.screen_rates and self.settings.value("screen_auto", False, type=bool):
            self._apply_rate(self.screen_rates[-1] if ac else self.screen_rates[0])
            # automatic display: overdrive on with AC, off on battery
            od = self.status.get("attrs", {}).get("panel_overdrive")
            if od and bool(od["current"]) != ac:
                self.action("set_overdrive", value=int(ac))

    def _apply_rate(self, hz):
        def work():
            screen.set_rate(hz)
            return screen.internal()  # kscreen-doctor runs in the background too, so the UI does not freeze

        def done(info):
            if isinstance(info, Exception):
                info = None
            if info:
                self.screen_current = info["current"]
                self.settings.setValue("screen_rate", info["current"])
                self.screen_info.setText(f"{info['name']} · {info['current']} Hz")
            self._render_screen(info)
        run_async(work, done)

    # ---- actions ----

    def set_mode(self, mode):
        self.action("set_mode", hold=("mode", mode), mode=mode)

    def set_gpu(self, target):
        g = self.status.get("gpu") or {}
        if (target == "ultimate") != (g.get("mode") == "ultimate"):
            r = QMessageBox.question(
                self, _("GPU mode"),
                _("Switching to or from Ultimate changes the MUX switch and needs a reboot. Continue?"))
            if r != QMessageBox.Yes:
                self.render()
                return

        def after(r):
            if r.get("reboot"):
                if QMessageBox.question(self, _("Reboot"),
                                        _("The GPU mode will take effect after a reboot. Reboot now?")) == QMessageBox.Yes:
                    subprocess.Popen(["systemctl", "reboot"])
            if r.get("deferred"):
                QMessageBox.information(
                    self, _("Eco postponed"),
                    _("The dGPU is used by these processes:") + "\n\n" + "\n".join(r["busy"]) + "\n\n" +
                    _("Eco will be applied automatically when the dGPU is free; since KWin holds the dGPU, "
                      "this usually happens at reboot."))
        self.action("set_gpu", after=after, hold=("gpu", target), target=target)

    def set_gpu_backend(self):
        backend = "ebpf" if self.gpu_ebpf.isChecked() else "firmware"
        self.action("set_gpu_backend", hold=("gpu_backend", backend), backend=backend)

    def set_overdrive(self):
        self.action("set_overdrive", value=1 if self.overdrive.isChecked() else 0)

    def set_kbd(self):
        self.action("set_kbd", hold=("kbd", self.kbd_level.value()), brightness=self.kbd_level.value())

    def _paint_color(self):
        c = self.color.name()
        self.aura_color.setStyleSheet(
            f"QPushButton {{ background:{c}; border:1px solid rgba(0,0,0,0.15); border-radius:8px; padding:0; }}")

    def pick_color(self):
        c = QColorDialog.getColor(self.color, self, _("Keyboard color"))
        if c.isValid():
            self.color = c
            self.settings.setValue("aura_color", c.name())
            self._paint_color()
            self.set_aura()

    def set_aura(self):
        self.action("set_aura", mode=self.aura_mode.currentData(),
                    color=[self.color.red(), self.color.green(), self.color.blue()],
                    speed=self.aura_speed.currentData())

    def set_charge(self):
        v = round(self.charge.value() / 5) * 5
        self.charge.setValue(v)
        self.action("set_charge_limit", hold=("charge", v), value=v)

    def set_autostart(self):
        if self.autostart.isChecked():
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            with open(AUTOSTART, "w") as f:
                f.write(AUTOSTART_ENTRY)
        elif os.path.exists(AUTOSTART):
            os.remove(AUTOSTART)

    def set_language(self, code: str):
        """Saves the choice and restarts the UI in the new language."""
        if code == i18n.current:
            return
        self.settings.setValue("language", code)
        self.settings.sync()
        restart_app()

    # ---- window ----

    def _tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_near_tray()

    def show_near_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def quit_app(self):
        # In Qt 6 quit() first tries to close the windows; a refused closeEvent cancels the exit
        self.quitting = True
        QApplication.quit()
        # if a window still refuses, quit is silently cancelled; nothing should stop Quit
        QTimer.singleShot(1500, lambda: QApplication.exit(0))

    def closeEvent(self, e):
        if self.quitting:
            e.accept()
            return
        e.ignore()
        self.hide()


def restart_app() -> None:
    """Restarts with the same command (language change). The window opens visible."""
    args = [a for a in sys.argv[1:] if a != "--hidden"]
    if sys.argv[0].endswith(os.path.join("gui", "app.py")):  # python3 -m boreas.gui.app
        cmd = [sys.executable, "-m", "boreas.gui.app", *args]
    else:
        cmd = [sys.executable, sys.argv[0], *args]
    QApplication.instance().processEvents()
    os.execv(sys.executable, cmd)


def main() -> int:
    # do not fall back to XWayland in a Wayland session: it looks blurry on a scaled screen
    if os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"
    app = QApplication(sys.argv)
    if os.environ.get("WAYLAND_DISPLAY") and app.platformName() == "xcb":
        print("Warning: the Qt Wayland plugin could not be loaded, using XWayland (may look blurry on scaled "
              "displays). On Arch: sudo pacman -S qt6-wayland", file=sys.stderr)
    app.setApplicationName("Boreas")
    app.setDesktopFileName("boreas")
    app.setQuitOnLastWindowClosed(False)
    i18n.set_language(i18n.choose(QSettings("boreas", "boreas")))
    if i18n.is_rtl():
        app.setLayoutDirection(Qt.RightToLeft)
    apply_theme(app)
    watch_system_theme(app)

    # single instance: a second launch shows the existing window
    name = f"boreas-gui-{os.getuid()}"
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        print("Boreas is already running; the open window was shown. To apply new settings such as scale, "
              "press Quit in the window first (X only hides it to the tray).", file=sys.stderr)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    w = MainWindow()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), w.show_near_tray()))
    if "--hidden" not in sys.argv:
        w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
