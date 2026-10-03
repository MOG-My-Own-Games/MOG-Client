"""Shared look: the accent gradient (same stops as MOG-Server's --accent-gradient) and the toggle switch."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QCheckBox

ACCENT_STOPS = ((0.0, "#7fa8ff"), (0.55, "#5b8def"), (1.0, "#3c64c4"))
ACCENT_HOVER_STOPS = ((0.0, "#94b7ff"), (0.55, "#709cf2"), (1.0, "#4d75d4"))


def accent_qss(stops=ACCENT_STOPS) -> str:
    parts = ", ".join(f"stop:{pos} {color}" for pos, color in stops)
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:1, {parts})"


def accent_gradient(x0: float, y0: float, x1: float, y1: float) -> QLinearGradient:
    grad = QLinearGradient(x0, y0, x1, y1)
    for pos, color in ACCENT_STOPS:
        grad.setColorAt(pos, QColor(color))
    return grad


class Toggle(QCheckBox):
    """A checkbox drawn as a pill switch, accent gradient when on."""

    TRACK_W, TRACK_H, GAP = 52, 28, 12

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0
        return QSize(self.TRACK_W + (self.GAP + text_w if text_w else 0) + 4, max(self.TRACK_H + 6, self.fontMetrics().height()))

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        top = (self.height() - self.TRACK_H) / 2
        track = QRectF(2, top, self.TRACK_W, self.TRACK_H)
        if self.isChecked():
            painter.setBrush(accent_gradient(track.left(), track.top(), track.right(), track.bottom()))
        else:
            painter.setBrush(QColor("#2c333d"))
        painter.setPen(QPen(QColor("white"), 2) if self.hasFocus() else Qt.NoPen)
        painter.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        knob_d = self.TRACK_H - 8
        knob_x = track.right() - knob_d - 4 if self.isChecked() else track.left() + 4
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(knob_x, top + 4, knob_d, knob_d))
        if self.text():
            painter.setPen(QColor("#e8eaed") if self.isEnabled() else QColor("#6b7380"))
            text_rect = QRectF(track.right() + self.GAP, 0, self.width() - track.right() - self.GAP, self.height())
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
