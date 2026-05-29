"""
testing.py — performance diagnostics overlay for SWAID-ESIS.

TestingOverlay is a full-window child widget of MainWindow, hidden by default.
Press I in the main interface to toggle it.  It renders on top of the main
window using a semi-transparent background so the hand-tracking is still
visible behind it.

Metrics shown:
    Camera FPS, Tracking FPS, Live Feed FPS, UI Update FPS,
    Camera→UI latency, per-core CPU%, RAM%, detection rate,
    hands visible, process CPU%.
"""
import time
from collections import deque
from pathlib import Path

import numpy as np
import psutil
from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from hand_tracking import CAMERA_HEIGHT, CAMERA_WIDTH

_TARGET_FPS = 20
_GOOD_LATENCY_MS = 200
_BAD_LATENCY_MS = 1000


def _mono(size, bold=False):
    f = QFont("Arial", size)
    f.setStyleHint(QFont.Monospace)
    if bold:
        f.setWeight(QFont.Bold)
    return f


def _load_logo():
    path = Path(__file__).parent / "assets" / "LogoFeup.tif"
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("RGB")
        arr = np.array(img, dtype=np.uint8)
        h, w = arr.shape[:2]
        qimg = QImage(arr.data, w, h, w * 3, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(qimg)
    except Exception:
        return None


def _status_color(value, good, bad, lower_is_better):
    if lower_is_better:
        if value <= good:
            return "#00ff25"
        if value <= bad:
            return "#ff8500"
        return "#ff0038"
    if value >= good:
        return "#00ff25"
    if value >= bad:
        return "#ff8500"
    return "#ff0038"


class TestingOverlay(QWidget):
    """Semi-transparent diagnostics overlay drawn on top of MainWindow.

    Call update_stats(dict) every frame to push new metrics in.
    The widget tracks UI-update FPS itself via an internal deque.
    psutil readings are throttled to every 0.5 s to avoid overhead.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)

        self.camera_fps = 0.0
        self.tracking_fps = 0.0
        self.live_fps = 0.0
        self.camera_to_ui_ms = 0.0
        self.detection_rate = 0.0
        self.hands_visible = 0

        self._ui_frame_times = deque(maxlen=60)
        self.ui_fps = 0.0

        self._cpu_percents = []
        self._ram_percent = 0.0
        self._proc_cpu = 0.0
        self._last_sys_update = 0.0
        self._process = psutil.Process()

        self._logo = _load_logo()

    def update_stats(self, stats: dict):
        if self.parent():
            self.setGeometry(self.parent().rect())

        self.camera_fps = stats.get("camera_fps", self.camera_fps)
        self.tracking_fps = stats.get("tracking_fps", self.tracking_fps)
        self.live_fps = stats.get("live_fps", self.live_fps)
        self.camera_to_ui_ms = stats.get("camera_to_ui_ms", self.camera_to_ui_ms)
        self.detection_rate = stats.get("detection_rate", self.detection_rate)
        self.hands_visible = stats.get("hands_visible", self.hands_visible)

        now = time.monotonic()
        self._ui_frame_times.append(now)
        if len(self._ui_frame_times) >= 2:
            self.ui_fps = (len(self._ui_frame_times) - 1) / (
                self._ui_frame_times[-1] - self._ui_frame_times[0]
            )

        if now - self._last_sys_update >= 0.5:
            self._last_sys_update = now
            self._cpu_percents = psutil.cpu_percent(percpu=True)
            self._ram_percent = psutil.virtual_memory().percent
            self._proc_cpu = self._process.cpu_percent()

        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor(2, 2, 5, 150))

        # ── Header ─────────────────────────────────────────────────────────────
        painter.setPen(QColor("#00d9e8"))
        painter.setFont(_mono(26, bold=True))
        painter.drawText(QRectF(40, 16, w - 80, 54), Qt.AlignLeft | Qt.AlignVCenter, "PERFORMANCE TESTING")

        if self._logo and not self._logo.isNull():
            lh = 52
            lw = int(lh * self._logo.width() / self._logo.height())
            painter.drawPixmap(w - lw - 18, 16, lw, lh, self._logo)

        painter.setPen(QPen(QColor("#1a2030"), 1))
        painter.drawLine(40, 80, w - 40, 80)

        # ── Layout constants ───────────────────────────────────────────────────
        col_div = w // 2
        y0 = 94
        row_h = 80
        lx = 40                  # left column x
        rx = col_div + 20        # right column x
        rw = w - rx - 40         # right column width

        # ══ LEFT: Performance metrics ══════════════════════════════════════════
        painter.setPen(QColor("#2a3a55"))
        painter.setFont(_mono(10, bold=True))
        painter.drawText(QRectF(lx, y0, col_div - lx - 20, 16), Qt.AlignLeft, "PERFORMANCE")

        perf_rows = [
            ("Camera FPS",        self.camera_fps,       "fps", _TARGET_FPS,     17.0,              12.0,              False),
            ("Tracking FPS",      self.tracking_fps,     "fps", _TARGET_FPS,     17.0,              12.0,              False),
            ("Live Feed FPS",     self.live_fps,         "fps", _TARGET_FPS,     15.0,              8.0,               False),
            ("UI Update FPS",     self.ui_fps,           "fps", 25.0,            20.0,              12.0,              False),
            ("Camera → UI Delay", self.camera_to_ui_ms,  "ms",  _BAD_LATENCY_MS, _GOOD_LATENCY_MS, _BAD_LATENCY_MS,   True),
        ]

        val_x    = col_div - 230
        unit_x   = col_div - 145
        bar_x    = col_div - 120
        bar_w    = 98
        bar_h    = 9

        for idx, (label, value, unit, bar_max, good, bad, lower) in enumerate(perf_rows):
            y = y0 + 20 + idx * row_h
            color = _status_color(value, good, bad, lower)

            painter.setPen(QColor("#6677aa"))
            painter.setFont(_mono(13))
            painter.drawText(QRectF(lx, y, val_x - lx - 8, 34), Qt.AlignVCenter | Qt.AlignLeft, label)

            painter.setPen(QColor(color))
            painter.setFont(_mono(24, bold=True))
            painter.drawText(QRectF(val_x, y - 4, 80, 42), Qt.AlignVCenter | Qt.AlignRight, f"{value:.1f}")

            painter.setPen(QColor("#445566"))
            painter.setFont(_mono(11))
            painter.drawText(QRectF(unit_x, y, 26, 34), Qt.AlignVCenter | Qt.AlignLeft, unit)

            by = y + 42
            painter.setBrush(QColor("#0d1420"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(bar_x, by, bar_w, bar_h), 4, 4)
            fill = min(1.0, value / max(1.0, bar_max))
            if fill > 0:
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(QRectF(bar_x, by, bar_w * fill, bar_h), 4, 4)
            tick_x = bar_x + bar_w * min(1.0, good / max(1.0, bar_max))
            painter.setPen(QPen(QColor("#ffffff50"), 1))
            painter.drawLine(int(tick_x), by - 2, int(tick_x), by + bar_h + 2)

        # ── Column divider ──────────────────────────────────────────────────────
        painter.setPen(QPen(QColor("#1a2030"), 1))
        painter.drawLine(col_div, 86, col_div, h - 50)

        # ══ RIGHT: CPU ═════════════════════════════════════════════════════════
        ry = y0
        painter.setPen(QColor("#2a3a55"))
        painter.setFont(_mono(10, bold=True))
        ncores = len(self._cpu_percents)
        painter.drawText(QRectF(rx, ry, rw, 16), Qt.AlignLeft, f"CPU  ({ncores} cores)")
        ry += 20

        if ncores > 0:
            per_row = 6
            slot_w = rw / per_row
            core_h = 8
            for ci, pct in enumerate(self._cpu_percents):
                col_i = ci % per_row
                row_i = ci // per_row
                cx = rx + col_i * slot_w
                cy = ry + row_i * 28
                cpu_color = "#00ff25" if pct < 50 else "#ff8500" if pct < 80 else "#ff0038"

                painter.setPen(QColor("#334466"))
                painter.setFont(_mono(8))
                painter.drawText(QRectF(cx, cy, slot_w - 2, 12), Qt.AlignLeft, f"C{ci}")

                painter.setPen(QColor(cpu_color))
                painter.setFont(_mono(8))
                painter.drawText(QRectF(cx, cy, slot_w - 2, 12), Qt.AlignRight, f"{pct:.0f}%")

                painter.setBrush(QColor("#0d1420"))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(QRectF(cx, cy + 13, slot_w - 4, core_h), 3, 3)
                if pct > 0:
                    painter.setBrush(QColor(cpu_color))
                    painter.drawRoundedRect(QRectF(cx, cy + 13, (slot_w - 4) * pct / 100, core_h), 3, 3)

            cpu_rows = (ncores + per_row - 1) // per_row
            ry += cpu_rows * 28 + 8

        # ── RAM ────────────────────────────────────────────────────────────────
        painter.setPen(QPen(QColor("#1a2030"), 1))
        painter.drawLine(rx, ry, rx + rw, ry)
        ry += 10

        painter.setPen(QColor("#2a3a55"))
        painter.setFont(_mono(10, bold=True))
        painter.drawText(QRectF(rx, ry, rw, 16), Qt.AlignLeft, "MEMORY")
        ry += 18

        ram_color = "#00ff25" if self._ram_percent < 60 else "#ff8500" if self._ram_percent < 85 else "#ff0038"
        painter.setBrush(QColor("#0d1420"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(rx, ry, rw, 10), 4, 4)
        if self._ram_percent > 0:
            painter.setBrush(QColor(ram_color))
            painter.drawRoundedRect(QRectF(rx, ry, rw * self._ram_percent / 100, 10), 4, 4)
        painter.setPen(QColor(ram_color))
        painter.setFont(_mono(11))
        painter.drawText(QRectF(rx, ry + 12, rw, 18), Qt.AlignRight, f"RAM  {self._ram_percent:.1f}%")
        ry += 34

        # ── Hand Tracking ──────────────────────────────────────────────────────
        painter.setPen(QPen(QColor("#1a2030"), 1))
        painter.drawLine(rx, ry, rx + rw, ry)
        ry += 10

        painter.setPen(QColor("#2a3a55"))
        painter.setFont(_mono(10, bold=True))
        painter.drawText(QRectF(rx, ry, rw, 16), Qt.AlignLeft, "HAND TRACKING")
        ry += 20

        det_color = _status_color(self.detection_rate * 100, 80, 50, False)
        hand_rows = [
            ("Detection rate",  f"{self.detection_rate * 100:.1f}%",   det_color),
            ("Hands visible",   f"{self.hands_visible} / 2",           "#00d9e8"),
            ("Camera res.",     f"{CAMERA_WIDTH} × {CAMERA_HEIGHT}",   "#6677aa"),
            ("Process CPU",     f"{self._proc_cpu:.1f}%",              "#aaaaaa"),
        ]
        for label, val, color in hand_rows:
            painter.setPen(QColor("#6677aa"))
            painter.setFont(_mono(12))
            painter.drawText(QRectF(rx, ry, rw - 110, 22), Qt.AlignVCenter | Qt.AlignLeft, label)
            painter.setPen(QColor(color))
            painter.setFont(_mono(12, bold=True))
            painter.drawText(QRectF(rx, ry, rw, 22), Qt.AlignVCenter | Qt.AlignRight, val)
            ry += 26

        # ── Footer ─────────────────────────────────────────────────────────────
        painter.setPen(QPen(QColor("#1a2030"), 1))
        painter.drawLine(40, h - 46, w - 40, h - 46)
        painter.setPen(QColor("#334455"))
        painter.setFont(_mono(12))
        painter.drawText(QRectF(0, h - 42, w, 28), Qt.AlignCenter, "Press  [I]  to close")
