"""One running client per user. A second start (the system opening a `mog://` link, say) hands what it was given
to the first and exits, instead of opening another window."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def socket_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name to be had: one shared name is still correct
        user = "user"
    return f"mog-client-{user}"


def send_to_running(text: str, name: str | None = None, timeout_ms: int = 1500) -> bool:
    """Hand `text` to the client that is running, if there is one. False when nobody answers."""
    socket = QLocalSocket()
    socket.connectToServer(name or socket_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(f"{text}\n".encode())  # always something to read, even for "just come forward"
    socket.flush()
    if socket.bytesToWrite():
        socket.waitForBytesWritten(timeout_ms)  # False also means "nothing left to write", so ask the buffer
    done = socket.bytesToWrite() == 0
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.UnconnectedState:
        socket.waitForDisconnected(timeout_ms)
    return done


class Listener(QObject):
    """Receives what later starts hand over. Emits `received` with each text, empty when it was only a start."""

    received = Signal(str)

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self.name = name or socket_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connection)

    def listen(self) -> bool:
        QLocalServer.removeServer(self.name)  # a stale file from a client that did not exit cleanly
        return self.server.listen(self.name)

    def _connection(self) -> None:
        while (socket := self.server.nextPendingConnection()) is not None:
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        text = bytes(socket.readAll()).decode("utf-8", "replace").strip()
        self.received.emit(text)
