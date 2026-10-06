"""Shared look: the accent gradient (same stops as MOG-Server's --accent-gradient) and the toggle switch."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QPushButton, QTabBar, QTabWidget

ACCENT_STOPS = ((0.0, "#9e3bf7"), (1.0, "#2859f9"))
ACCENT_HOVER_STOPS = ((0.0, "#aa53f8"), (1.0, "#426dfa"))


def accent_qss(stops=ACCENT_STOPS) -> str:
    parts = ", ".join(f"stop:{pos} {color}" for pos, color in stops)
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, {parts})"


def scrollbar_qss(alpha: int = 140, hover_alpha: int = 255) -> str:
    """Slim round scrollbars with a transparent track, the accent gradient half see-through until hovered: the
    same as MOG-Server's, which follows RomM's."""

    def stops(a: int, vertical: bool) -> str:
        axis = "x1:0, y1:0, x2:0, y2:1" if vertical else "x1:0, y1:0, x2:1, y2:0"
        parts = ", ".join(f"stop:{pos} rgba({int(c[1:3], 16)}, {int(c[3:5], 16)}, {int(c[5:7], 16)}, {a})" for pos, c in ACCENT_STOPS)
        return f"qlineargradient({axis}, {parts})"

    out = []
    for orientation, vertical in (("vertical", True), ("horizontal", False)):
        size, long = ("width", "min-height") if vertical else ("height", "min-width")
        out.append(
            f"QScrollBar:{orientation} {{ background: transparent; {size}: 12px; margin: 0; border: none; }}\n"
            f"QScrollBar::handle:{orientation} {{ background: {stops(alpha, vertical)}; border-radius: 4px; {long}: 32px; margin: 2px; }}\n"
            f"QScrollBar::handle:{orientation}:hover, QScrollBar::handle:{orientation}:pressed {{ background: {stops(hover_alpha, vertical)}; }}\n"
        )
    out.append(
        "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }\n"
        "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }\n"
    )
    return "".join(out)


def accent_gradient(x0: float, y0: float, x1: float, y1: float) -> QLinearGradient:
    middle = (y0 + y1) / 2  # left to right, whatever the shape it fills
    grad = QLinearGradient(x0, middle, x1, middle)
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
        if not self.isEnabled():  # greyed, but still showing the position it is left in
            painter.setBrush(QColor("#434a56") if self.isChecked() else QColor("#2c333d"))
        elif self.isChecked():
            painter.setBrush(accent_gradient(track.left(), track.top(), track.right(), track.bottom()))
        else:
            painter.setBrush(QColor("#2c333d"))
        painter.setPen(QPen(QColor("white"), 2) if self.hasFocus() else Qt.NoPen)
        painter.drawRoundedRect(track, self.TRACK_H / 2, self.TRACK_H / 2)
        knob_d = self.TRACK_H - 8
        knob_x = track.right() - knob_d - 4 if self.isChecked() else track.left() + 4
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("white") if self.isEnabled() else QColor("#8b929e"))
        painter.drawEllipse(QRectF(knob_x, top + 4, knob_d, knob_d))
        if self.text():
            painter.setPen(QColor("#e8eaed") if self.isEnabled() else QColor("#6b7380"))
            text_rect = QRectF(track.right() + self.GAP, 0, self.width() - track.right() - self.GAP, self.height())
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())


def bell_icon(size: int = 24) -> QIcon:
    """A white notification bell, same shape as MOG-Server's."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    s = size / 24
    bell = QPainterPath()
    bell.moveTo(12 * s, 3 * s)
    bell.cubicTo(8.7 * s, 3 * s, 6 * s, 5.7 * s, 6 * s, 9 * s)
    bell.lineTo(6 * s, 12.5 * s)
    bell.lineTo(4.5 * s, 16 * s)
    bell.lineTo(19.5 * s, 16 * s)
    bell.lineTo(18 * s, 12.5 * s)
    bell.lineTo(18 * s, 9 * s)
    bell.cubicTo(18 * s, 5.7 * s, 15.3 * s, 3 * s, 12 * s, 3 * s)
    bell.closeSubpath()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("white"))
    painter.drawPath(bell)
    painter.drawEllipse(QRectF(10 * s, 17.5 * s, 4 * s, 3 * s))
    painter.end()
    return QIcon(pix)


class FocusTabBar(QTabBar):
    """A tab bar that outlines the selected tab while it has the focus. Drawn here because changing a style
    property from a focus event makes some system styles loop."""

    def focusInEvent(self, e) -> None:  # noqa: N802 - Qt's name
        super().focusInEvent(e)
        self.update()

    def focusOutEvent(self, e) -> None:  # noqa: N802 - Qt's name
        super().focusOutEvent(e)
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt's name
        super().paintEvent(e)
        if self.hasFocus() and self.currentIndex() >= 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("white"), 2))
            painter.drawRoundedRect(QRectF(self.tabRect(self.currentIndex())).adjusted(1, 1, -1, -1), 8, 8)


class FocusTabs(QTabWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setTabBar(FocusTabBar())


class BadgeButton(QPushButton):
    """A button with a red dot and a number on its top-right corner, as MOG-Server's bell has (an unread count).
    The dot is drawn over the button's own edge, so the style leaves OVERHANG pixels of room on the right
    (`#bellButton` in the stylesheet)."""

    DIAMETER = 22
    OVERHANG = 8

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._count = 0

    def set_count(self, count: int) -> None:
        self._count = max(0, count)
        self.update()

    @property
    def count(self) -> int:
        return self._count

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt's name
        super().paintEvent(e)
        if not self._count:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        d = self.DIAMETER
        dot = QRectF(self.width() - d, 1, d, d)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e2574c"))
        painter.drawEllipse(dot)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(14 if self._count < 100 else 11)
        painter.setFont(font)
        painter.drawText(dot, Qt.AlignCenter, "99+" if self._count > 99 else str(self._count))
