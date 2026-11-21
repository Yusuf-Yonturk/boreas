#!/usr/bin/env python3
"""Renders docs/demo.gif: two real windows, drawn off screen side by side, stepping through a short tour.

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

WINDOW = (570, 900)       # logical size of each window
CANVAS = (1000, 700)      # size of a frame: landscape, like the banner above it in the README
MARGIN, GAP = 36, 36
RADIUS = 16
READS = {"status", "get_mode"}


class Tour:
    def __init__(self):
        self.qa = QApplication(sys.argv)
        i18n.set_language("en")
        apply_theme(self.qa)
        self.state = client.call("status")
        self.modes = {m["id"]: client.call("get_mode", mode=m["id"]) for m in self.state["modes"]}
        A.call = self.answer                   # from here on the service is not asked again
        self.main = self.window()              # left: the main page
        self.side = self.window()              # right: Fans and power, following the same status
        self.frames: list[tuple[QImage, int]] = []
        self.pump(1.0)
        self.side.status = copy.deepcopy(self.state)   # the page opens only once a status has arrived
        self.side.open_fans()
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
        """Puts the changed status on both windows, the way a status from the service would arrive."""
        for path, value in changes.items():
            d = self.state
            *parents, last = path.split("__")
            for key in parents:
                d = d[key]
            d[last] = value
        for w in (self.main, self.side):
            w.status = copy.deepcopy(self.state)
            w.render()
        self.pump(0.8)

    def frame(self, ms: int, tap=None):
        """One frame: both windows side by side on the banner's gradient. tap = (window, point)."""
        w, h = CANVAS
        out = QImage(w * 2, h * 2, QImage.Format_RGB32)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.scale(2, 2)
        g = QLinearGradient(0, 0, w, h)
        for at, color in ((0.0, "#0a1f6e"), (0.3, "#2456e6"), (0.6, "#6a52ee"), (0.82, "#a04fe6"), (1.0, "#f04fb4")):
            g.setColorAt(at, QColor(color))
        p.fillRect(0, 0, w, h, g)
        scale = (h - 2 * MARGIN) / WINDOW[1]
        ww, wh = WINDOW[0] * scale, WINDOW[1] * scale
        x = (w - 2 * ww - GAP) / 2
        for win in (self.main, self.side):
            box = QRectF(x, MARGIN, ww, wh)
            clip = QPainterPath()
            clip.addRoundedRect(box, RADIUS, RADIUS)
            p.save()
            p.setClipPath(clip)
            p.drawImage(box, win.grab().toImage())
            p.restore()
            if tap is not None and tap[0] is win:    # where the next click lands
                c = QPointF(x + tap[1].x() * scale, MARGIN + tap[1].y() * scale)
                p.setBrush(QColor(255, 255, 255, 70))
                p.setPen(QPen(QColor(255, 255, 255, 220), 2))
                p.drawEllipse(c, 12, 12)
            x += ww + GAP
        p.end()
        self.frames.append((out.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation), ms))

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
        w, side = self.main, self.side
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
        # down the page on the right: power limits, then the charge limit
        bar = side.fans_window.findChild(QScrollArea).verticalScrollBar()
        for step in range(1, 7):
            bar.setValue(round(bar.maximum() * step / 6))
            self.pump(0.05)
            self.frame(90 if step < 6 else 1000)
        self.frame(350, tap=self.at(side.charge))
        for limit in (75, 70, 65, 60):
            self.state["battery"]["limit"] = limit
            side.charge.setValue(limit)
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
            palette = "split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-f", "concat", "-i", "frames.txt",
                            "-vf", palette, "-fps_mode", "vfr", os.path.abspath(path)], cwd=d, check=True)
        total = sum(ms for _, ms in self.frames) / 1000
        print(f"{path}: {len(self.frames)} frames, {total:.1f} s, {os.path.getsize(path) / 1e6:.2f} MB")


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                             "..", "docs", "demo.gif")
    tour = Tour()
    tour.run()
    tour.save(out)
    tour.main.quitting = tour.side.quitting = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
