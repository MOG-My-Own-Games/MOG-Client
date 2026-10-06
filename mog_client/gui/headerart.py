"""The artwork behind a game page's header, faded into the page on every side, as on MOG-Server's page."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

PAGE = QColor("#14171c")


def faded(image: QPixmap, size: QSize) -> QPixmap:
    """`image` filling `size` (cropped, its top kept), shaded on the left so the text reads over it, and
    transparent towards the edges so it melts into the page."""
    if size.isEmpty() or image.isNull():
        return QPixmap()
    out = QImage(size, QImage.Format_ARGB32_Premultiplied)  # a QPixmap may have no alpha channel at all
    out.fill(Qt.transparent)
    scaled = image.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    painter = QPainter(out)
    painter.drawPixmap((size.width() - scaled.width()) // 2, -(scaled.height() - size.height()) // 4, scaled)
    area = QRect(0, 0, size.width(), size.height())

    painter.fillRect(area, QColor(PAGE.red(), PAGE.green(), PAGE.blue(), 45))  # the whole picture a little dimmer
    shade = QLinearGradient(0, 0, size.width(), 0)  # and darker where the text is, which runs most of the way
    for at, alpha in ((0.0, 235), (0.5, 185), (0.85, 85), (1.0, 20)):
        shade.setColorAt(at, QColor(PAGE.red(), PAGE.green(), PAGE.blue(), alpha))
    painter.fillRect(area, shade)

    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)  # keep only what the masks let through
    for gradient, stops in (
        (QLinearGradient(0, 0, size.width(), 0), ((0.0, 0), (0.16, 255), (0.84, 255), (1.0, 0))),
        (QLinearGradient(0, 0, 0, size.height()), ((0.0, 0), (0.25, 255), (0.6, 255), (1.0, 0))),
    ):
        for at, alpha in stops:
            gradient.setColorAt(at, QColor(0, 0, 0, alpha))
        painter.fillRect(area, gradient)
    painter.end()
    return QPixmap.fromImage(out)


class HeaderArt(QWidget):
    """A container that paints `set_image`'s picture behind whatever is laid out in it."""

    def __init__(self) -> None:
        super().__init__()
        self._image: QPixmap | None = None
        self._cache: QPixmap | None = None

    def set_image(self, image: QPixmap | None) -> None:
        self._image, self._cache = image, None
        self.update()

    @property
    def has_image(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def paintEvent(self, e) -> None:  # noqa: N802 - Qt's name
        if not self.has_image:
            return
        if self._cache is None or self._cache.size() != self.size():
            self._cache = faded(self._image, self.size())
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)
