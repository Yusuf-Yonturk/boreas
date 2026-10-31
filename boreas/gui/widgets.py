"""Tasarımdaki ortak bileşenler: anahtar, bölmeli düğme, kart, satır, kaydırıcı satırı."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from .common import T, u, uv


class Switch(QAbstractButton):
    """Tasarımdaki açma/kapama anahtarı. QCheckBox gibi checkable; clicked/toggled sinyalleri çalışır."""

    def __init__(self, w: int = 56, h: int = 30, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(u(w), u(h))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        col = QColor(T["blue"] if self.isChecked() else T["swOff"])
        if not self.isEnabled():
            col.setAlphaF(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        m = max(2, round(h * 0.1))
        d = h - 2 * m
        x = w - m - d if self.isChecked() else m
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, m, d, d))
        if self.hasFocus():
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(T["fill"]), 1.5))
            p.drawRoundedRect(QRectF(0.75, 0.75, w - 1.5, h - 1.5), h / 2, h / 2)


class CheckDot(QWidget):
    """Durum satırındaki daire içinde onay işareti."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(u(20), u(20))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.width() / 20, self.height() / 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["checkBg"]))
        p.drawEllipse(QRectF(1, 1, 18, 18))
        path = QPainterPath()
        path.moveTo(6.2, 10.3)
        path.lineTo(8.7, 12.8)
        path.lineTo(13.7, 7.4)
        pen = QPen(QColor("#ffffff"), 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


class FlowLayout(QLayout):
    """Sığmayan öğeleri alt satıra saran yerleşim (Qt FlowLayout örneği)."""

    def __init__(self, parent=None, margin: int = 0, hspacing: int = 6, vspacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = eff.x(), eff.y(), 0
        for it in self._items:
            sz = it.sizeHint()
            if x + sz.width() > eff.right() + 1 and line_h > 0:
                x, y, line_h = eff.x(), y + line_h + self._v, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x += sz.width() + self._h
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y() + m.bottom()


class Segmented(QFrame):
    """Bölmeli düğme (Sessiz | Dengeli | Turbo). Seçim dışarıdan set_checked ile işaretlenir."""

    def __init__(self, on_click=None, width: int = 340, flow: bool = False, parent=None):
        width = u(width) if width else 0
        super().__init__(parent)
        self.setObjectName("seg")
        self.on_click = on_click
        self.flow = flow
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)
        if flow:
            FlowLayout(self, margin=2, hspacing=2, vspacing=2)
        else:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(2, 2, 2, 2)
            lay.setSpacing(0)
        if width:
            self.setFixedWidth(width)

    def add(self, key, label: str, stretch: int = 1) -> QPushButton:
        b = QPushButton(label)
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("key", key)
        b.setFixedHeight(uv(34))
        b.clicked.connect(lambda _=False, k=key: self._clicked(k))
        self.group.addButton(b)
        if self.flow:
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            self.layout().addWidget(b)
        else:
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)  # genişlik yalnız stretch oranından
            self.layout().addWidget(b, stretch)
        return b

    def buttons(self):
        return self.group.buttons()

    def clear(self):
        for b in list(self.group.buttons()):
            self.group.removeButton(b)
            self.layout().removeWidget(b)
            b.deleteLater()

    def _clicked(self, key):
        # grup "tek seçim" değil (hiçbiri seçili olmayabilir); tıklananı hemen tek seçili yap,
        # yoksa eski seçim servis cevabına kadar onunla birlikte işaretli kalır
        self.set_checked(key)
        if self.on_click:
            self.on_click(key)

    def set_checked(self, key):
        for b in self.group.buttons():
            b.setChecked(b.property("key") == key)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.flow:  # sarılan satırlar yüksekliği artırsın (HBox heightForWidth'u iletmez)
            h = self.layout().heightForWidth(self.width())
            if h > 0 and h != self.minimumHeight():
                self.setMinimumHeight(h)


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("hline")
    f.setFixedHeight(1)
    return f


def card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
    """Başlık satırı (sol başlık, sağ bilgi) ve alt çizgili kart. (kart, düzen, bilgi etiketi)"""
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(u(20), uv(16), u(20), uv(6))
    lay.setSpacing(0)
    head = QWidget()
    h = QHBoxLayout(head)
    h.setContentsMargins(0, 0, 0, uv(12))
    t = QLabel(title)
    t.setObjectName("h2")
    info = QLabel()
    info.setObjectName("dim")
    info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    # uzun çevirilerde bilgi metni kartı genişletmesin, kalan yere sığsın
    info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    info.setMinimumWidth(u(40))
    h.addWidget(t)
    h.addWidget(info, 1)
    lay.addWidget(head)
    lay.addWidget(hline())
    return frame, lay, info


def row(label, control=None, height: int = 58) -> QWidget:
    """Sol etiket, sağ denetim. label str ya da QWidget olabilir."""
    w = QWidget()
    w.setFixedHeight(uv(height))
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(u(12))
    if isinstance(label, str):
        label = QLabel(label)
        label.setWordWrap(True)  # uzun çeviri denetimi pencere dışına itmesin, iki satıra insin
    h.addWidget(label, 1)
    if control is not None:
        if isinstance(control, QWidget):
            h.addWidget(control, 0, Qt.AlignVCenter)  # satır boyunca uzayıp çizgilere değmesin
        else:
            h.addLayout(control)
    return w


def hint(text: str = "") -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("hint")
    lb.setWordWrap(True)
    lb.setContentsMargins(0, 2, 0, uv(12))
    return lb


def scroll_page(content: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setWidget(content)
    return sa


def footer_bar(inner: QHBoxLayout) -> QWidget:
    """Üstte ince çizgi, 72 px yüksekliğinde alt çubuk."""
    outer = QWidget()
    v = QVBoxLayout(outer)
    v.setContentsMargins(u(24), 0, u(24), 0)
    v.setSpacing(0)
    v.addWidget(hline())
    body = QWidget()
    body.setFixedHeight(uv(71))
    inner.setContentsMargins(0, 0, 0, 0)
    body.setLayout(inner)
    v.addWidget(body)
    return outer


class ValueSlider(QWidget):
    def __init__(self, label: str, lo: int, hi: int, value: int, unit: str, max_text: str = ""):
        super().__init__()
        self.unit, self.max_text = unit, max_text
        self.setFixedHeight(uv(46))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(u(20))
        self.name = QLabel(label)
        self.name.setFixedWidth(u(176))
        self.name.setWordWrap(True)  # uzun çeviriler iki satıra insin
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setFixedHeight(max(u(28), 22))  # tutamaç (negatif kenar boşluğu) kırpılmasın
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.value = QLabel()
        self.value.setFixedWidth(u(60))
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider.valueChanged.connect(self._show)
        lay.addWidget(self.name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value)
        self._show(value)

    def _show(self, v):
        at_max = self.max_text and v >= self.slider.maximum()
        self.value.setText(self.max_text if at_max else f"{v}{self.unit}")


_FAN_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none' "
    "stroke='{c}' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'>"
    "<circle cx='8' cy='8' r='1.4'/><path d='M8 6.6C7.6 3.4 9.6 1.8 11.4 2.6c1.6.7.6 3.5-2.2 4.6'/>"
    "<path d='M9.3 8.6c2.9 1.4 3.3 3.9 1.8 5-1.4 1-3.3-1.2-2.9-4.1'/>"
    "<path d='M6.7 8.5C4.2 10.4 1.8 9.6 1.7 7.8c-.1-1.7 2.8-2 5-.3'/></svg>"
)


_THEME_SVG = {
    "light": ("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none' "
              "stroke='{c}' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'>"
              "<circle cx='8' cy='8' r='3'/><path d='M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1"
              "M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1'/></svg>"),
    "dark": ("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none' "
             "stroke='{c}' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'>"
             "<path d='M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8z'/></svg>"),
    "auto": ("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none' "
             "stroke='{c}' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'>"
             "<circle cx='8' cy='8' r='5.5'/><path d='M8 2.5a5.5 5.5 0 0 1 0 11z' fill='{c}'/></svg>"),
    "lang": ("<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none' "
             "stroke='{c}' stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'>"
             "<circle cx='8' cy='8' r='6'/><path d='M2 8h12M8 2c1.7 1.7 2.5 3.7 2.5 6S9.7 12.3 8 14"
             "M8 2C6.3 3.7 5.5 5.7 5.5 8s.8 4.3 2.5 6'/></svg>"),
}


def theme_icon(kind: str, color: str) -> QIcon | None:
    """kind: auto | light | dark | lang (dil düğmesi)"""
    return fan_icon(color, _THEME_SVG[kind])


def fan_icon(color: str, svg: str | None = None) -> QIcon | None:
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return None
    r = QSvgRenderer(QByteArray((svg or _FAN_SVG).format(c=color).encode()))
    side = u(18)
    pm = QPixmap(side * 2, side * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p, QRectF(0, 0, side, side))
    p.end()
    return QIcon(pm)
