"""Loading pictures the way the client wants them (Qt only)."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QImageReader, QPainter, QPixmap, QPolygon


def largest_pixmap(blob: bytes) -> QPixmap:
    """The picture in `blob`; of a file that holds several sizes of it (an .ico, as some icons come) the largest, where
    loading it plainly gives the first, which is the 16 pixel one."""
    data = QByteArray(blob)
    buffer = QBuffer(data)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    best = QImage()
    for index in range(max(reader.imageCount(), 1)):
        if reader.imageCount() > 1:
            reader.jumpToImage(index)
        image = reader.read()
        if not image.isNull() and image.width() * image.height() > best.width() * best.height():
            best = image
    return QPixmap.fromImage(best) if not best.isNull() else QPixmap()


def with_play_badge(pix: QPixmap, label: str, size: QSize) -> QPixmap:
    """A thumbnail fitted into `size`, with a play triangle over it and `label` along its foot: how a video shows among
    the screenshots (the server's web page draws the same)."""
    out = QPixmap(size)
    out.fill(Qt.black)
    scaled = pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.drawPixmap((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
    centre, radius = QPoint(size.width() // 2, size.height() // 2 - 8), min(size.width(), size.height()) // 6
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(0, 0, 0, 170)))
    painter.drawEllipse(centre, radius, radius)
    painter.setBrush(QBrush(Qt.white))
    half = radius // 2
    painter.drawPolygon(QPolygon([QPoint(centre.x() - half // 2, centre.y() - half), QPoint(centre.x() - half // 2, centre.y() + half), QPoint(centre.x() + half, centre.y())]))
    foot = QRect(0, size.height() - 24, size.width(), 24)
    painter.fillRect(foot, QColor(0, 0, 0, 170))
    painter.setPen(Qt.white)
    painter.drawText(foot, Qt.AlignCenter, label)
    painter.end()
    return out
