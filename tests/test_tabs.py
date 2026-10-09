"""The tabs of Settings: standing on a line in the accent gradient, right under the bar."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLabel

from mog_client.gui import app as gui
from mog_client.gui.widgets import FocusTabs


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(gui.STYLE)
    return app


def tabs(qapp):
    widget = FocusTabs()
    for name in ("General", "Server", "Logs"):
        widget.addTab(QLabel(name), name)
    palette = widget.palette()
    palette.setColor(palette.ColorRole.Window, QColor("#14171c"))  # the window's own dark, so a lit pixel is a tab
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)
    widget.resize(600, 200)
    widget.show()
    qapp.processEvents()
    return widget


def test_the_line_sits_between_the_tab_bar_and_the_page_and_is_the_accent_gradient(qapp):
    widget = tabs(qapp)
    bar, line, page = widget.tabBar().geometry(), widget.line.geometry(), widget.pages.geometry()
    assert line.top() == bar.bottom() + 1 and page.top() == line.bottom() + 1 + FocusTabs.GAP  # nothing overlaps
    assert line.height() == FocusTabs.LINE == 3 and line.width() == widget.width()
    assert widget.tabBar().drawBase() is False

    image = widget.grab().toImage()
    left, right = image.pixelColor(10, line.center().y()), image.pixelColor(590, line.center().y())
    assert left.red() > right.red() and right.blue() > 200  # purple to blue, left to right


def test_the_pages_follow_the_bar_and_the_signal_says_which(qapp):
    widget = tabs(qapp)
    seen = []
    widget.currentChanged.connect(seen.append)
    widget.setCurrentIndex(2)
    assert seen == [2] and widget.currentWidget().text() == "Logs" and widget.tabText(2) == "Logs" and widget.count() == 3
    widget.setCurrentWidget(widget.pages.widget(1))
    assert widget.currentIndex() == 1 and seen == [2, 1]


def test_the_selected_tab_stands_taller_than_the_others(qapp):
    widget = tabs(qapp)
    bar = widget.tabBar()

    def lit(index):
        c = widget.grab().toImage().pixelColor(bar.tabRect(index).center().x(), 3)  # the top of the bar
        return max(c.red(), c.green(), c.blue()) > 100

    assert lit(0) and not lit(1)  # General is selected: it reaches higher
    widget.setCurrentIndex(1)
    qapp.processEvents()
    assert lit(1) and not lit(0)
