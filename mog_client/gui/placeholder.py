"""What stands in for a game's cover when it has none: a grey card with a game controller."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from mog_client.gui.widgets import accent_gradient

_cache: dict[tuple[int, int, str], QPixmap] = {}


# TODO: replace the drawn controller with a custom image (an illustration made for MOG, shipped in assets/).
def cover_placeholder(size: QSize, title: str = "") -> QPixmap:
    """A grey card with a controller and, under it, the game's title in the accent gradient."""
    key = (size.width(), size.height(), title)
    if key not in _cache:
        if len(_cache) > 400:
            _cache.clear()
        _cache[key] = _draw(size, title)
    return _cache[key]


def _draw(size: QSize, title: str) -> QPixmap:
    pix = QPixmap(size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    card = QRectF(0, 0, size.width(), size.height())
    fade = QLinearGradient(card.topLeft(), card.bottomLeft())
    fade.setColorAt(0.0, QColor("#2b313b"))
    fade.setColorAt(1.0, QColor("#20252d"))
    painter.fillRect(card, fade)

    unit = min(size.width(), size.height() * 0.74) / 10  # the controller is ten units wide
    painter.save()
    painter.translate(QPointF(card.center().x(), card.height() * (0.34 if title else 0.5)))
    ink = QColor("#59616e")
    body = QPainterPath()
    body.addRoundedRect(QRectF(-4.2 * unit, -1.7 * unit, 8.4 * unit, 3.4 * unit), 1.7 * unit, 1.7 * unit)
    for side in (-1, 1):  # the grips, reaching down and a little outwards
        grip = QPainterPath()
        grip.addEllipse(QPointF(side * 3.3 * unit, 1.4 * unit), 1.35 * unit, 2.0 * unit)
        body |= grip
    painter.setPen(Qt.NoPen)
    painter.setBrush(ink)
    painter.drawPath(body)

    hole = QColor("#2b313b")  # the controls are cut out of the body
    painter.setBrush(hole)
    arm, thick = 0.95 * unit, 0.52 * unit
    centre = QPointF(-2.6 * unit, -0.1 * unit)
    painter.drawRoundedRect(QRectF(centre.x() - arm, centre.y() - thick / 2, 2 * arm, thick), thick / 3, thick / 3)
    painter.drawRoundedRect(QRectF(centre.x() - thick / 2, centre.y() - arm, thick, 2 * arm), thick / 3, thick / 3)
    for dx, dy in ((0.0, -0.75), (0.0, 0.75), (-0.75, 0.0), (0.75, 0.0)):
        painter.drawEllipse(QPointF(2.6 * unit + dx * unit, -0.1 * unit + dy * unit), 0.32 * unit, 0.32 * unit)
    painter.restore()

    if title:
        area = QRectF(14, card.height() * 0.58, card.width() - 28, card.height() * 0.36)
        font = QFont()
        font.setPixelSize(max(12, round(card.width() / 10)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QBrush(accent_gradient(area.left(), area.top(), area.right(), area.bottom())), 1))
        painter.setClipRect(area)
        painter.drawText(area, int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap), title)
    painter.end()
    return pix
