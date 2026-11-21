#!/usr/bin/env python3
"""Renders docs/demo.gif: the real window three times (main page light and dark, Fans and power), drawn off screen side by side, stepping through a short tour.

    python3 tools/demo-gif.py [output.gif]

The window is the application's own code. It reads the status and the modes once from the running
service and from then on is fed copies of that status with the mode, GPU mode, keyboard effect and
charge limit changed, so nothing is written to the hardware: every command other than the two
reads is refused. Needs a running boreasd, PySide6 and ffmpeg.
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = "2"        # drawn at twice the size, scaled down smoothly afterwards
os.environ["BOREAS_SCALE"] = "1"
os.environ["BOREAS_THEME"] = "dark"
os.environ["BOREAS_NO_UPDATE_CHECK"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from boreas import client, i18n  # noqa: E402
from boreas.gui import app as A  # noqa: E402
from boreas.gui.common import apply_theme  # noqa: E402

WINDOW = (570, 950)       # logical size of each window
CANVAS = (1500, 847)      # size of a frame: the proportions of docs/screenshots.png
MARGIN, GAP = 50, 38      # above and below the windows, and between them
RADIUS = 16               # corners of a window
CORNER = 30               # corners of the frame itself; outside them the GIF is transparent
KEY = (0, 255, 0)         # painted outside those corners, then marked as the transparent color
READS = {"status", "get_mode"}


class Tour:
    def __init__(self):
        self.qa = QApplication(sys.argv)
        i18n.set_language("en")
        apply_theme(self.qa)
        self.state = client.call("status")
        self.modes = {m["id"]: client.call("get_mode", mode=m["id"]) for m in self.state["modes"]}
        A.call = self.answer                   # from here on the service is not asked again
        self.main = self.window()              # left: the main page, light
        self.side = self.window()              # middle: the main page, dark
        self.more = self.window()              # right: Fans and power, dark, following the same status
        self.windows = (self.main, self.side, self.more)
        self.frames: list[tuple[QImage, int]] = []
        self.pump(1.0)
        self.more.status = copy.deepcopy(self.state)   # the page opens only once a status has arrived
        self.more.open_fans()
        self.pump(1.2)

    def window(self):
        w = A.MainWindow()
        w.call = self.answer
        w.show()
        w.resize(*WINDOW)
        return w

    def answer(self, cmd, **kw):
        if cmd == "status":
            return copy.deepcopy(self.state)
        if cmd == "get_mode":
            return copy.deepcopy(self.modes[kw["mode"]])
        raise A.DaemonError(f"not sent to the service by the demo: {cmd}")

    def pump(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            self.qa.processEvents()
            # without app.exec() nothing delivers deleteLater(): replaced widgets would stay on screen
            self.qa.sendPostedEvents(None, QEvent.DeferredDelete)
            time.sleep(0.01)

    def show(self, **changes):
        """Puts the changed status on every window, the way a status from the service would arrive."""
        for path, value in changes.items():
            d = self.state
            *parents, last = path.split("__")
            for key in parents:
                d = d[key]
            d[last] = value
        for w in self.windows:
            w.status = copy.deepcopy(self.state)
            w.render()
        self.pump(0.8)

    def frame(self, ms: int, tap=None):
        """One frame: the windows side by side on a gradient with rounded corners. tap = (window, point)."""
        # the theme belongs to the whole application, so the main page is drawn while it is light
        apply_theme(self.qa, "light")
        self.pump(0.1)
        shots = {self.main: self.main.grab().toImage()}
        apply_theme(self.qa, "dark")
        self.pump(0.1)
        for win in (self.side, self.more):
            shots[win] = win.grab().toImage()
        w, h = CANVAS
        out = QImage(w * 2, h * 2, QImage.Format_RGB32)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(2, 2)
        g = QLinearGradient(0, 0, w, h)
        for at, color in ((0.0, "#22b5ee"), (0.45, "#6f6fe8"), (0.75, "#9a4fe0"), (1.0, "#e0468f")):
            g.setColorAt(at, QColor(color))
        p.fillRect(0, 0, w, h, g)
        scale = (h - 2 * MARGIN) / WINDOW[1]
        ww, wh = WINDOW[0] * scale, WINDOW[1] * scale
        x = (w - len(self.windows) * (ww + GAP) + GAP) / 2
        for win in self.windows:
            box = QRectF(x, MARGIN, ww, wh)
            clip = QPainterPath()
            clip.addRoundedRect(box, RADIUS, RADIUS)
            p.save()
            p.setClipPath(clip)
            p.drawImage(box, shots[win])
            p.restore()
            if tap is not None and tap[0] is win:    # where the next click lands
                c = QPointF(x + tap[1].x() * scale, MARGIN + tap[1].y() * scale)
                light = win is self.main             # a dark ring on the light page, a light one on the dark
                p.setBrush(QColor(20, 30, 60, 50) if light else QColor(255, 255, 255, 70))
                p.setPen(QPen(QColor(20, 30, 60, 200) if light else QColor(255, 255, 255, 220), 2))
                p.drawEllipse(c, 12, 12)
            x += ww + GAP
        p.end()
        out = out.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        # the corners, cut after scaling and without smoothing: a GIF pixel is either there or not
        outside = QPainterPath()
        outside.addRect(QRectF(0, 0, w, h))
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(0, 0, w, h), CORNER, CORNER)
        p = QPainter(out)
        p.fillPath(outside.subtracted(frame), QColor(*KEY))
        p.end()
        self.frames.append((out, ms))

    def at(self, widget):
        win = widget.window()
        return win, QPointF(widget.mapTo(win, widget.rect().center()))

    def click(self, widget, hold_ms: int, **changes):
        self.frame(350, tap=self.at(widget))
        self.show(**changes)
        self.frame(hold_ms)

    def segment(self, seg, key):
        return next(b for b in seg.group.buttons() if b.property("key") == key)

    # ---- the tour ----------------------------------------------------------------

    def run(self):
        w, more = self.main, self.more
        base = copy.deepcopy(self.state)
        self.show(mode=0, mode_ac=0, temps__cpu=58, fans={"cpu": 2300, "gpu": 2700},
                  kbd={"brightness": 2, "max": 3}, profile="balanced")
        self.frame(1300)
        # a mode change on the left; the page on the right follows it with that mode's curves and limits
        self.click(self.segment(w.mode_seg, 1), 1500, mode=1, mode_ac=1, temps__cpu=74,
                   fans={"cpu": 4600, "gpu": 4900}, profile="performance")
        self.click(self.segment(w.mode_seg, 2), 1400, mode=2, mode_ac=2, temps__cpu=49,
                   fans={"cpu": 1900, "gpu": 2100}, profile="low-power")
        if self.state.get("gpu"):
            eco = self.segment(w.gpu_seg, "eco")
            self.frame(350, tap=self.at(eco))
            self.state["gpu"]["mode"] = "eco"
            if self.state["gpu"].get("engine"):
                self.state["gpu"]["engine"]["policy"] = "eco"
            self.show(gpu_target="eco")
            self.frame(1100)
        aura = dict(self.state.get("aura") or {"color": [255, 85, 0], "speed": "normal", "zones": None})
        for mode, extra in (("breathe", {"color2": [0, 110, 255]}), ("star", {"color2": None}),
                            ("heatmap", {"color2": None})):
            if mode in (self.state.get("aura_modes") or ()):
                self.click(w.aura_mode, 900, aura={**aura, "mode": mode, **extra})
        # down the page on the right, to the power limits and the charge limit at its end
        bar = more.fans_window.findChild(QScrollArea).verticalScrollBar()
        for step in range(1, 7):
            bar.setValue(round(bar.maximum() * step / 6))
            self.pump(0.05)
            self.frame(90 if step < 6 else 1000)
        self.frame(350, tap=self.at(more.charge))
        for limit in (75, 70, 65, 60):
            self.state["battery"]["limit"] = limit
            more.charge.setValue(limit)
            self.pump(0.05)
            self.frame(110 if limit > 60 else 1700)
        self.state = base

    def save(self, path: str):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise SystemExit("ffmpeg not found")
        with tempfile.TemporaryDirectory() as d:
            lines = ["ffconcat version 1.0"]
            for i, (img, ms) in enumerate(self.frames):
                img.save(os.path.join(d, f"{i:03}.png"))
                lines += [f"file {i:03}.png", f"duration {ms / 1000}"]
            lines.append(f"file {len(self.frames) - 1:03}.png")   # the last duration needs a closing entry
            with open(os.path.join(d, "frames.txt"), "w") as f:
                f.write("\n".join(lines) + "\n")
            palette = "split[a][b];[a]palettegen=max_colors=256:stats_mode=full:reserve_transparent=0[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "concat", "-i", "frames.txt",
                            "-vf", palette, "-fps_mode", "vfr", os.path.abspath(path)], cwd=d, check=True)
        transparent_key(path)
        total = sum(ms for _, ms in self.frames) / 1000
        print(f"{path}: {len(self.frames)} frames, {total:.1f} s, {os.path.getsize(path) / 1e6:.2f} MB")


def transparent_key(path: str):
    """Marks KEY as transparent in the first frame of the GIF.

    Written with real transparency, every frame would be stored whole (about 3 MB instead of 0.2):
    the encoder then cannot use transparency for "same as the frame before". The corners never
    change after the first frame, so it is enough to make them transparent there.
    """
    with open(path, "rb") as f:
        data = bytearray(f.read())
    if not data[10] & 0x80:
        raise SystemExit("the GIF has no global palette")
    size = 2 << (data[10] & 7)
    colors = [tuple(data[13 + 3 * i:16 + 3 * i]) for i in range(size)]
    index = colors.index(KEY)                    # ValueError if the palette did not keep the key color
    gce = data.index(b"\x21\xf9\x04", 13 + 3 * size)   # graphic control extension of the first frame
    data[gce + 3] |= 1
    data[gce + 6] = index
    with open(path, "wb") as f:
        f.write(data)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                             "..", "docs", "demo.gif")
    tour = Tour()
    tour.run()
    tour.save(out)
    for w in tour.windows:
        w.quitting = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
