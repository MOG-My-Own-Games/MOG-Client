import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from mog_client.gui.busy import BusyOverlay
from mog_client.gui.modal import ModalHost
from mog_client.gui.overlay import MessageOverlay
from mog_client.gui.playing import PlayingOverlay


class CardPage(QWidget):
    """A page of a modal card: its first control takes the focus."""

    def __init__(self) -> None:
        super().__init__()
        self.first = QPushButton("Done")
        QVBoxLayout(self).addWidget(self.first)

    def focus_default(self) -> None:
        self.first.setFocus()


@pytest.fixture
def window():
    """The overlays as the main window stacks them: the modal card, then playing, busy, and the message over all."""
    app = QApplication.instance() or QApplication([])
    central = QWidget()
    central.resize(900, 600)
    central.show()
    central.activateWindow()
    assert QTest.qWaitForWindowActive(central), "focusChanged only fires in an active window"
    overlay_ref = []
    modal = ModalHost(central, suspended=lambda: any(w.isVisible() for w in overlay_ref))
    overlay = MessageOverlay(central)
    playing = PlayingOverlay(central, suspended=overlay.isVisible)
    busy = BusyOverlay(central, suspended=overlay.isVisible)
    overlay_ref += [overlay, playing, busy]  # as the main window wires them
    page = CardPage()
    app.processEvents()
    yield app, central, modal, overlay, playing, busy, page
    for widget in (modal, overlay, playing, busy):
        widget.hide()
    central.close()


@pytest.fixture
def errors(monkeypatch):
    """What a slot raised: PySide6 prints it and goes on, so a test would pass over a RecursionError without this."""
    raised = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: raised.append(f"{kind.__name__}: {value}"))
    return raised


def settled(app) -> None:
    app.processEvents()
    app.processEvents()


def test_a_card_and_the_playing_message_together_do_not_take_the_focus_from_each_other_forever(window, errors):
    """Caught live (winetricks, Done on a card while the Playing message was up): the card gave the focus to its page, the
    Playing message gave it back to Stop, and so on until Python's recursion limit, which froze the window."""
    app, central, modal, overlay, playing, busy, page = window
    modal.show_page(page, "Winetricks")
    page.first.setFocus()
    playing.show_game("Some Game", None, "Starting...")
    settled(app)

    assert errors == []
    assert QApplication.focusWidget() in (playing.stop_button, page.first)


def test_a_card_and_the_busy_message_together_do_not_take_the_focus_from_each_other_forever(window, errors):
    app, central, modal, overlay, playing, busy, page = window
    modal.show_page(page, "Winetricks")
    page.first.setFocus()
    busy.show_busy("Restoring saves", "Some Game")
    settled(app)

    assert errors == []
    assert QApplication.focusWidget() in (busy.cancel_button, page.first)


def test_the_playing_and_the_busy_messages_together_do_not_take_the_focus_from_each_other_forever(window, errors):
    app, central, modal, overlay, playing, busy, page = window
    playing.show_game("Some Game", None, "Starting...")
    busy.show_busy("Restoring saves", "Some Game")
    settled(app)

    assert errors == []
    assert QApplication.focusWidget() in (busy.cancel_button, playing.stop_button)


def test_a_message_over_a_card_gets_the_focus_and_keeps_it(window, errors):
    app, central, modal, overlay, playing, busy, page = window
    modal.show_page(page, "Winetricks")
    page.first.setFocus()
    overlay.show_message("Installing cnc_ddraw with winetricks. This can take a while.", "info")
    settled(app)

    assert errors == []
    assert QApplication.focusWidget() is overlay.ok
