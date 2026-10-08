"""Loading pictures the way the client wants them (Qt only)."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QImageReader, QPixmap


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
