import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mog_client.gui import headerart  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def picture(color="#ff0000", size=(800, 300)):
    pix = QPixmap(*size)
    pix.fill(QColor(color))
    return pix


def alpha(img: QImage, x: int, y: int) -> int:
    return img.pixelColor(x, y).alpha()


def test_the_art_melts_into_the_page_on_every_side(qapp):
    out = headerart.faded(picture(), QSize(1000, 300)).toImage()
    assert alpha(out, 0, 150) < 16 and alpha(out, 999, 150) < 16  # left and right edges, all but gone
    assert alpha(out, 500, 0) < 16 and alpha(out, 500, 299) < 16  # top and bottom
    assert alpha(out, 700, 110) > 200  # solid in the middle-right


def test_the_left_is_shaded_for_the_text(qapp):
    out = headerart.faded(picture("#ffffff"), QSize(1000, 300)).toImage()
    left, right = out.pixelColor(150, 110), out.pixelColor(850, 110)
    assert left.red() < right.red()  # the shade of the page colour darkens what sits behind the text


def test_an_empty_or_missing_picture_gives_a_blank_one(qapp):
    assert headerart.faded(QPixmap(), QSize(100, 100)).isNull()
    assert headerart.faded(picture(), QSize(0, 0)).isNull()


def test_the_widget_only_paints_once_it_has_a_picture(qapp):
    widget = headerart.HeaderArt()
    widget.resize(400, 200)
    assert not widget.has_image
    widget.set_image(picture())
    assert widget.has_image
    plain = widget.grab().toImage()
    widget.set_image(None)
    assert widget.grab().toImage() != plain
