import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from mog_client.gui.busy import BusyOverlay, size_text


@pytest.fixture
def overlay():
    QApplication.instance() or QApplication([])
    parent = QWidget()
    parent.resize(900, 600)
    parent.show()
    yield BusyOverlay(parent)  # the parent stays alive for the test


def test_sizes_read_as_mebibytes():
    assert size_text(3355443, 9961472) == "3.2 of 9.5 MiB"
    assert size_text(1048576, 0) == "1.0 MiB"


def test_the_bar_moves_until_there_is_a_figure_then_follows_it(overlay):
    overlay.show_busy("Restoring saves", "Some Game")
    assert overlay.showing and overlay.bar.maximum() == 0 and overlay.heading.text() == "Restoring saves"
    overlay.set_progress(512, 1024)
    assert (overlay.bar.maximum(), overlay.bar.value()) == (1024, 512)
    overlay.set_progress(1024, 1024, "Putting the files back...")
    assert overlay.detail.text() == "Putting the files back..."
    overlay.end()
    assert not overlay.showing


def test_cancel_is_offered_only_when_it_can_be_used(overlay):
    pressed = []
    overlay.cancel_clicked.connect(lambda: pressed.append(1))
    overlay.show_busy("Backing up saves", "G", cancellable=False)
    assert overlay.cancel_button.isHidden()
    overlay.show_busy("Restoring saves", "G")
    overlay.cancel_button.click()
    overlay.cancelling()
    assert pressed == [1] and not overlay.cancel_button.isEnabled()
