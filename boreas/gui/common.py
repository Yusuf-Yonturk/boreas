from __future__ import annotations

import os

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

ACCENT = "#4cc2ff"  # tray / window icon
MODE_COLORS = {2: "#5aa9e6", 0: "#e8e8e8", 1: "#ff5c5c"}  # Silent / Balanced / Turbo

# Design palettes (light / dark). T is updated in place when the theme changes;
# custom-painted widgets read it on every paint.
DARK = dict(
    name="dark", win="#1c1d20", card="#232428", border="#34353a", text="#f2f2f4", dim="#a3a3a8",
    seg="#2a2b2f", segBorder="#37383d", segOn="#4a4b51", segOnText="#ffffff", segText="#d6d6da",
    input="#232428", inputBorder="#3b3c41", hover="#2d2e33", track="#4a4b50", swOff="#4a4b50",
    checkBg="#5a5b60", navOn="#1e3556", navOnText="#f2f2f4", fill="#2F74D0", blue="#2F7CF6",
    cpu="#3A86F7", gpu="#F0604A", mid="#E5B84B", grid="#36373c",
)
LIGHT = dict(
    name="light", win="#f7f7f8", card="#ffffff", border="#e6e6ea", text="#1d1d1f", dim="#6e6e73",
    seg="#f1f1f4", segBorder="#e3e3e8", segOn="#ffffff", segOnText="#1d1d1f", segText="#2c2c30",
    input="#ffffff", inputBorder="#d9d9de", hover="#f1f1f4", track="#dcdce1", swOff="#d6d6db",
    checkBg="#c4c4c9", navOn="#dce6f8", navOnText="#2F6FD6", fill="#2F74D0", blue="#2F7CF6",
    cpu="#2F7CF6", gpu="#F25C45", mid="#D49A00", grid="#e6e6ea",
)
T: dict = dict(DARK)

# Design sizes follow the 592 px wide mockup; a single factor scales them down on a real screen.
# BOREAS_SCALE=1 gives the mockup's exact size, 0.8 a smaller one.
try:
    SCALE = min(1.6, max(0.5, float(os.environ.get("BOREAS_SCALE", "0.78"))))
except ValueError:
    SCALE = 0.78


def u(n: float) -> int:
    """Scale a design pixel."""
    return max(1, round(n * SCALE))


_VCOMP = 0.86  # vertical sizes are a bit tighter than horizontal ones, to keep the window short


def uv(n: float) -> int:
    """Vertical sizes (row height, spacing)."""
    return max(1, round(n * SCALE * _VCOMP))


def fs(n: float, floor: int = 12) -> int:
    """Font size: scaled, with a lower bound so it stays readable."""
    return max(floor, round(n * SCALE))


class _ThemeBus(QObject):
    changed = Signal()


bus = _ThemeBus()


def _assets(p: dict) -> tuple[str, str]:
    """Small SVGs used by the QSS through url() (combo box arrow, check mark)."""
    d = os.path.join(os.path.expanduser("~/.cache"), "boreas")
    os.makedirs(d, exist_ok=True)
    chev = os.path.join(d, f"chevron-{p['name']}.svg")
    check = os.path.join(d, "check.svg")
    with open(chev, "w") as f:
        f.write("<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 14 14' fill='none' "
                f"stroke='{p['text']}' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'>"
                "<path d='M3 5l4 4 4-4'/></svg>")
    with open(check, "w") as f:
        f.write("<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 14 14' fill='none' "
                "stroke='#ffffff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
                "<path d='M3 7.4l2.8 2.8L11 4.4'/></svg>")
    return chev, check


def build_style(p: dict) -> str:
    chev, check = _assets(p)
    both = lambda sel: f"{sel}, QFrame#card {sel}"  # noqa: E731  (same specificity inside cards)
    hw = u(19)                     # slider handle (without the border)
    ht = hw + 2
    groove = u(5)
    hm = -((ht - groove) // 2)
    hr = ht // 2 - 1               # Qt draws a square if the radius exceeds half the height
    return f"""
QWidget {{ background:transparent; color:{p['text']}; font-size:{fs(15)}px; }}
QWidget#root, QDialog, QMessageBox {{ background:{p['win']}; }}
QToolTip {{ background:{p['card']}; color:{p['text']}; border:1px solid {p['border']}; padding:4px; }}
QMenu {{ background:{p['card']}; color:{p['text']}; border:1px solid {p['border']}; padding:4px; }}
QMenu::item {{ padding:6px 18px; border-radius:4px; }}
QMenu::item:selected {{ background:{p['fill']}; color:#ffffff; }}
QMenu::separator {{ height:1px; background:{p['border']}; margin:4px 8px; }}

QLabel#title {{ font-size:{fs(30, 22)}px; font-weight:700; }}
QLabel#subtitle {{ font-size:{fs(15)}px; color:{p['dim']}; }}
QLabel#h2 {{ font-size:{fs(17, 13)}px; font-weight:700; }}
QLabel#dim {{ font-size:{fs(15)}px; color:{p['dim']}; }}
QLabel#hint {{ font-size:{fs(15)}px; color:{p['dim']}; }}

QFrame#card {{ background:{p['card']}; border:1px solid {p['border']}; border-radius:{u(12)}px; }}
{both('QFrame#hline')} {{ background:{p['border']}; border:none; }}

{both('QFrame#seg')} {{ background:{p['seg']}; border:1px solid {p['segBorder']}; border-radius:{u(9)}px; }}
{both('QFrame#seg QPushButton')} {{ background:transparent; border:none; border-radius:{u(7)}px; padding:0 {u(6)}px; color:{p['segText']}; font-size:{fs(15)}px; }}
{both('QFrame#seg QPushButton:hover')} {{ background:{p['hover']}; }}
{both('QFrame#seg QPushButton:checked')} {{ background:{p['segOn']}; color:{p['segOnText']}; font-weight:500; }}
{both('QFrame#seg QPushButton:disabled')} {{ color:{p['dim']}; }}

QPushButton {{ background:{p['input']}; border:1px solid {p['inputBorder']}; border-radius:{u(8)}px; padding:6px 16px; }}
{both('QPushButton#btn')} {{ background:{p['input']}; border:1px solid {p['inputBorder']}; border-radius:{u(8)}px; padding:0 {u(14)}px; font-size:{fs(15)}px; min-height:{uv(36)}px; }}
{both('QPushButton#btn:hover')} {{ background:{p['hover']}; }}
{both('QPushButton#btn:disabled')} {{ color:{p['dim']}; background:transparent; }}
QPushButton#primary {{ background:{p['blue']}; border:none; color:#ffffff; font-size:{fs(16, 13)}px; font-weight:500; border-radius:{u(8)}px; }}
QPushButton#primary:hover {{ background:#4a8ef8; }}
QPushButton#primary:disabled {{ background:{p['swOff']}; }}
QPushButton#exit {{ background:{p['input']}; border:1px solid {p['inputBorder']}; border-radius:{u(8)}px; padding:0 {u(20)}px; font-size:{fs(15)}px; }}
QPushButton#exit:hover {{ background:{p['hover']}; }}
QPushButton#iconbtn {{ background:transparent; border:none; border-radius:{u(8)}px; padding:0; }}
QPushButton#iconbtn:hover {{ background:{p['hover']}; }}
QPushButton#fanbtn {{ background:transparent; border:none; border-radius:{u(8)}px; padding:0 {u(12)}px; font-size:{fs(15)}px; color:{p['dim']}; }}
QPushButton#fanbtn:hover {{ background:{p['hover']}; }}
QPushButton#fanbtn:checked {{ background:{p['navOn']}; color:{p['navOnText']}; }}

{both('QComboBox')} {{ background:{p['input']}; border:1px solid {p['inputBorder']}; border-radius:{u(8)}px; padding:0 {u(14)}px; min-height:{uv(38)}px; color:{p['text']}; }}
QComboBox::drop-down {{ border:none; width:{u(34)}px; }}
QComboBox::down-arrow {{ image:url({chev}); width:{u(14)}px; height:{u(14)}px; }}
QComboBox QAbstractItemView, QComboBoxPrivateContainer {{ background:{p['card']}; color:{p['text']}; border:1px solid {p['border']}; selection-background-color:{p['fill']}; selection-color:#ffffff; outline:none; }}
QLineEdit {{ background:{p['input']}; border:1px solid {p['inputBorder']}; border-radius:6px; padding:4px 8px; }}
QSpinBox {{ background:{p['input']}; border:1px solid {p['inputBorder']}; border-radius:6px; padding:4px 8px; }}

QSlider::groove:horizontal {{ height:{groove}px; background:{p['track']}; border-radius:{groove // 2 + 1}px; }}
QSlider::sub-page:horizontal {{ background:{p['fill']}; border-radius:{groove // 2 + 1}px; }}
QSlider::handle:horizontal {{ background:#ffffff; width:{hw}px; height:{hw}px; margin:{hm}px 0; border-radius:{hr}px; border:1px solid rgba(0,0,0,0.18); }}
QSlider::handle:horizontal:disabled {{ background:{p['swOff']}; }}

QCheckBox {{ spacing:{u(12)}px; }}
QCheckBox::indicator {{ width:{u(18)}px; height:{u(18)}px; border:1px solid {p['inputBorder']}; border-radius:{u(5)}px; background:{p['input']}; }}
QCheckBox::indicator:checked {{ background:{p['fill']}; border-color:{p['fill']}; image:url({check}); }}

QScrollArea {{ border:none; background:transparent; }}
QScrollBar:vertical {{ width:{u(10)}px; background:transparent; margin:0; }}
QScrollBar::handle:vertical {{ background:{p['track']}; border-radius:{u(4)}px; min-height:30px; margin:2px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
"""


def scheme_pref() -> str:
    """auto | light | dark: BOREAS_THEME environment variable or the settings file."""
    try:
        from PySide6.QtCore import QSettings
        v = str(QSettings("boreas", "boreas").value("theme", "auto")).lower()
    except Exception:
        v = "auto"
    if v in ("light", "dark"):  # chosen with the button in the header
        return v
    env = os.environ.get("BOREAS_THEME", "").lower()
    return env if env in ("light", "dark") else "auto"


def resolve_theme(app, pref: str = "auto") -> dict:
    if pref == "light":
        return LIGHT
    if pref == "dark":
        return DARK
    try:
        from PySide6.QtCore import Qt
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return LIGHT
    except Exception:  # old Qt: assume dark
        pass
    return DARK


def apply_theme(app, pref: str | None = None) -> None:
    pref = pref or scheme_pref()
    p = resolve_theme(app, pref)
    T.clear()
    T.update(p)
    app.setStyleSheet(build_style(p))
    for w in app.allWidgets():
        w.update()
    bus.changed.emit()


def watch_system_theme(app) -> None:
    """Apply again when the system switches between light and dark (in auto)."""
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: apply_theme(app) if scheme_pref() == "auto" else None)
    except Exception:
        pass


class _Signals(QObject):
    done = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn, signals):
        super().__init__()
        self.fn, self.signals = fn, signals

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # pass it to the main thread as an error result
            res = e
        self.signals.done.emit(res)


_alive: set = set()


def run_async(fn, callback=None) -> None:
    """Runs fn in a thread and hands the result (or the exception) to callback on the main thread."""
    sig = _Signals()
    _alive.add(sig)

    def finish(res):
        _alive.discard(sig)
        if callback:
            callback(res)
    sig.done.connect(finish)
    QThreadPool.globalInstance().start(_Job(fn, sig))
