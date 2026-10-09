"""Shared look: the accent gradient (same stops as MOG-Server's --accent-gradient) and the toggle switch."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

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


ROLE_KIND, ROLE_ON, ROLE_DIVIDER = Qt.UserRole + 20, Qt.UserRole + 21, Qt.UserRole + 22


class OptionDelegate(QStyledItemDelegate):
    """List rows drawn as settings: a small pill switch (kind "switch") or a round radio button (kind "radio") in
    front of the text, the accent gradient when on, and a hairline above a row that starts a new group."""

    ROW_H, MARK_W, MARK_H, PAD = 38, 34, 18, 12

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 2, -2, -2)
        if index.data(ROLE_DIVIDER):
            painter.setPen(QColor("#2c333d"))
            painter.drawLine(option.rect.left() + 8, option.rect.top(), option.rect.right() - 8, option.rect.top())
        if option.state & QStyle.State_Selected:
            painter.setPen(QPen(QColor("#4c8dff"), 3))
            painter.setBrush(QColor("#1e2733"))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
        on = bool(index.data(ROLE_ON))
        top = rect.top() + (rect.height() - self.MARK_H) / 2
        left = rect.left() + self.PAD
        if index.data(ROLE_KIND) == "switch":
            track = QRectF(left, top, self.MARK_W, self.MARK_H)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent_gradient(track.left(), track.top(), track.right(), track.bottom()) if on else QColor("#2c333d"))
            painter.drawRoundedRect(track, self.MARK_H / 2, self.MARK_H / 2)
            knob = self.MARK_H - 6
            painter.setBrush(QColor("white"))
            painter.drawEllipse(QRectF(track.right() - knob - 3 if on else track.left() + 3, top + 3, knob, knob))
            text_left = track.right() + self.PAD
        else:
            ring = QRectF(left + (self.MARK_W - self.MARK_H) / 2, top, self.MARK_H, self.MARK_H)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#4c8dff") if on else QColor("#59616e"), 2))
            painter.drawEllipse(ring.adjusted(1, 1, -1, -1))
            if on:
                dot = ring.adjusted(5, 5, -5, -5)
                painter.setPen(Qt.NoPen)
                painter.setBrush(accent_gradient(dot.left(), dot.top(), dot.right(), dot.bottom()))
                painter.drawEllipse(dot)
            text_left = left + self.MARK_W + self.PAD
        painter.setPen(QColor("#e8eaed") if on else QColor("#b8bfca"))
        painter.drawText(QRectF(text_left, rect.top(), rect.right() - text_left - 4, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, index.data(Qt.DisplayRole))
        painter.restore()


def clear_icon(size: int = 18) -> QIcon:
    """A round button in the accent gradient with a white cross, for the corner of a text field that empties it."""
    ratio = 2
    d = size * ratio
    pix = QPixmap(d, d)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(accent_gradient(0, 0, d, 0))
    painter.drawEllipse(QRectF(0, 0, d, d))
    painter.setPen(QPen(QColor("white"), d * 0.1, Qt.SolidLine, Qt.RoundCap))
    m = d * 0.32
    painter.drawLine(m, m, d - m, d - m)
    painter.drawLine(d - m, m, m, d - m)
    painter.end()
    return QIcon(pix)


def avatar_icon(blob: bytes | None, name: str, size: int = 28) -> QIcon:
    """The user's picture cut to a circle, or (without one) the accent gradient with their initial, as on MOG-Server."""
    ratio = 2  # drawn at twice the size so the edge is smooth on a scaled display
    d = size * ratio
    pix = QPixmap(d, d)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, d, d))
    painter.setClipPath(clip)
    picture = QPixmap()
    if blob and picture.loadFromData(blob):
        side = min(picture.width(), picture.height())  # the middle square
        painter.drawPixmap(QRectF(0, 0, d, d), picture, QRectF((picture.width() - side) / 2, (picture.height() - side) / 2, side, side))
    else:
        painter.fillRect(QRectF(0, 0, d, d), accent_gradient(0, 0, d, d))
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setPixelSize(int(d * 0.5))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, d, d), Qt.AlignCenter, (name[:1] or "?").upper())
    painter.end()
    return QIcon(pix)


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
            painter.drawRoundedRect(QRectF(self.tabRect(self.currentIndex())).adjusted(1, 1, -1, 12), 10, 10)  # its bottom corners are cut off: the tab stands on the line


class AccentLine(QWidget):
    """A strip in the accent gradient."""

    def __init__(self, thickness: int) -> None:
        super().__init__()
        self.setFixedHeight(thickness)

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.fillRect(self.rect(), accent_gradient(0, 0, self.width(), self.height()))


class FocusTabs(QWidget):
    """Tabs standing on a line in the accent gradient. The bar, the line and the pages are three rows of one layout, so
    the line is always right under the tabs and above the page (and its scrollbar), whatever the style: a QTabWidget
    draws its own line where its style thinks the bar ends, which on some screens is above the tabs or over the page."""

    LINE = 3  # px
    GAP = 6  # between the line and the page, so a scrollbar never touches it
    currentChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._bar = FocusTabBar()
        self._bar.setDrawBase(False)
        self._bar.setExpanding(False)
        self.line = AccentLine(self.LINE)
        self.pages = QStackedWidget()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._bar)
        top.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(top)
        lay.addWidget(self.line)
        lay.addSpacing(self.GAP)
        lay.addWidget(self.pages, 1)
        self._bar.currentChanged.connect(self._on_bar)

    def _on_bar(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.currentChanged.emit(index)

    def tabBar(self) -> QTabBar:  # noqa: N802 - the QTabWidget's name, which the callers use
        return self._bar

    def addTab(self, page: QWidget, text: str) -> int:  # noqa: N802
        self.pages.addWidget(page)
        return self._bar.addTab(text)

    def count(self) -> int:
        return self._bar.count()

    def tabText(self, index: int) -> str:  # noqa: N802
        return self._bar.tabText(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self._bar.currentIndex()

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        self._bar.setCurrentIndex(index)

    def currentWidget(self) -> QWidget | None:  # noqa: N802
        return self.pages.currentWidget()

    def setCurrentWidget(self, page: QWidget) -> None:  # noqa: N802
        self._bar.setCurrentIndex(self.pages.indexOf(page))


class BadgeButton(QPushButton):
    """A button with a red dot and a number on its top-right corner, as MOG-Server's bell has (an unread count), here on the user's button.
    The dot is drawn over the button's own edge, so the style leaves OVERHANG pixels of room on the right
    (`#userButton` in the stylesheet)."""

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


class ParagraphLabel(QLabel):
    """A paragraph that fits the height the layout gives it: `max_lines` when there is room, down to `min_lines`
    when there is not, shortened with "..." as needed. A plain word-wrapped label in a nested layout reports its
    height for the wrong width, and the rows beside it get squeezed instead."""

    def __init__(self, text: str = "", max_lines: int = 6, min_lines: int = 2):
        super().__init__()
        self.full_text, self.max_lines, self.min_lines = text, max_lines, min_lines
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._shown = -1
        self._fit(self.max_lines)

    def set_paragraph(self, text: str) -> None:
        self.full_text = text
        self._fit(max(self.min_lines, min(self.max_lines, self.height() // self._line())))

    def hasHeightForWidth(self) -> bool:
        return False

    def _line(self) -> int:
        return QFontMetrics(self.font()).lineSpacing()

    def _wanted(self) -> int:
        """Lines the whole text takes at the current width, at most `max_lines`."""
        return min(self._count(self.full_text), self.max_lines) if self.full_text else 0

    def _count(self, text: str) -> int:
        rect = QFontMetrics(self.font()).boundingRect(QRect(0, 0, max(self.width(), 200), 100000), Qt.TextWordWrap, text)
        return rect.height() // self._line()

    def sizeHint(self) -> QSize:
        return QSize(200, self._wanted() * self._line() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, min(self.min_lines, self._wanted()) * self._line() + 2)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit(max(self.min_lines, min(self.max_lines, self.height() // self._line())))

    def _fit(self, lines: int) -> None:
        text = self.full_text
        if self._count(text) > lines:
            words = text.split()
            while words and self._count(" ".join(words).rstrip(" ,.;:") + "...") > lines:
                words.pop()
            text = " ".join(words).rstrip(" ,.;:") + "..."
        if text != self.text():
            self.setText(text)
        wanted = self._wanted()
        if wanted != self._shown:
            self._shown = wanted
            self.updateGeometry()
