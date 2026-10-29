"""How a row of the Notifications list is drawn: the game's icon, the title with how long ago it was at the right end of its
line, then the body wrapped under it. The rows take the list's width (they wrap, so the list never scrolls sideways) and
the time takes only the room its words need."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

ROLE_BODY = Qt.UserRole + 40  # the text under the title
ROLE_AGE = Qt.UserRole + 41  # "5 hours ago"

PAD = 9  # the 3px border and 6px padding that the item has in the stylesheet
ICON_GAP = 10
TIME_GAP = 12
TEXT = QColor("#e8eaed")
MUTED = QColor("#9aa3b0")


def _smaller(font: QFont) -> QFont:
    small = QFont(font)
    small.setBold(False)
    if font.pixelSize() > 0:
        small.setPixelSize(max(9, round(font.pixelSize() * 0.85)))
    elif font.pointSizeF() > 0:
        small.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
    return small


class NotificationDelegate(QStyledItemDelegate):
    def __init__(self, view, icon_size: QSize):
        super().__init__(view)
        self.view = view
        self.icon_size = icon_size

    def _measure(self, font: QFont, index, width: int) -> tuple[int, int, int, int]:
        """(width left for the text, height of the title, height of the body, width of the time) for a row `width` wide."""
        text_w = max(80, width - 2 * PAD - self.icon_size.width() - ICON_GAP)
        age = index.data(ROLE_AGE) or ""
        age_w = QFontMetrics(_smaller(font)).horizontalAdvance(age) + TIME_GAP if age else 0
        fm = QFontMetrics(font)
        title_h = fm.boundingRect(0, 0, max(40, text_w - age_w), 10000, Qt.TextWordWrap, index.data(Qt.DisplayRole) or "").height()
        body = index.data(ROLE_BODY) or ""
        body_h = fm.boundingRect(0, 0, text_w, 10000, Qt.TextWordWrap, body).height() if body else 0
        return text_w, title_h, body_h, age_w

    def _width(self) -> int:
        return self.view.viewport().width() or 400

    def sizeHint(self, option, index) -> QSize:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        width = self._width()
        _text_w, title_h, body_h, _age_w = self._measure(opt.font, index, width)
        return QSize(width, max(self.icon_size.height(), title_h + body_h) + 2 * PAD)

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        icon = opt.icon
        font = QFont(opt.font)
        opt.text = ""
        opt.icon = type(icon)()
        opt.features &= ~QStyleOptionViewItem.HasDecoration  # the border and the selection only: the rest is drawn here
        style = opt.widget.style() if opt.widget else self.view.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        inner = option.rect.adjusted(PAD, PAD, -PAD, -PAD)
        text_w, title_h, body_h, age_w = self._measure(font, index, option.rect.width())
        painter.save()
        if not icon.isNull():
            top = inner.top() + (inner.height() - self.icon_size.height()) // 2
            icon.paint(painter, QRect(inner.left(), top, self.icon_size.width(), self.icon_size.height()))
        left = inner.left() + self.icon_size.width() + ICON_GAP
        top = inner.top() + max(0, (inner.height() - title_h - body_h) // 2)
        painter.setFont(font)
        painter.setPen(TEXT)
        title = QRect(left, top, max(40, text_w - age_w), title_h)
        painter.drawText(title, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, index.data(Qt.DisplayRole) or "")
        age = index.data(ROLE_AGE) or ""
        if age:
            painter.setFont(_smaller(font))
            painter.setPen(MUTED)
            painter.drawText(QRect(left, top, text_w, QFontMetrics(font).height()), Qt.AlignRight | Qt.AlignVCenter, age)  # on the title's first line
            painter.setFont(font)
            painter.setPen(TEXT)
        body = index.data(ROLE_BODY) or ""
        if body:
            painter.drawText(QRect(left, top + title_h, text_w, body_h), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, body)
        painter.restore()
