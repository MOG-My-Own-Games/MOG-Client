"""This machine's identity on the server: who it is, and the question to ask when its name is taken."""

from __future__ import annotations

import json
import platform
import re
import socket
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from mog_client.api import MogClient
from mog_client.config import _read_json, _write_json, config_dir


@dataclass
class DeviceRecord:
    client_uid: str
    device_id: int | None = None
    name: str | None = None


class HostnameTaken(Exception):
    """The server already has a device with this machine's hostname. Either this is that machine
    again (a reinstall) or a new one that needs a name of its own."""

    def __init__(self, devices: list[dict], suggested_names: list[str]):
        super().__init__("hostname taken")
        self.devices = devices
        self.suggested_names = suggested_names


class NameTaken(Exception):
    pass


class ServerLacksSaveSync(RuntimeError):
    """The server answers 404 or 405 where the save API should be: it is a version without save sync
    (an unknown path under its web UI reads as 405, as the UI's file server only allows GET)."""

    def __init__(self) -> None:
        super().__init__("this MOG-Server does not support save sync yet; update it to a version that does")


def device_path() -> Path:
    return config_dir() / "device.json"


def load_device() -> DeviceRecord:
    """The saved identity, created on first use. The uid stays when the name changes."""
    raw = _read_json(device_path(), {})
    uid = raw.get("client_uid")
    if not uid:
        record = DeviceRecord(client_uid=uuid.uuid4().hex)
        save_device(record)
        return record
    return DeviceRecord(uid, raw.get("device_id"), raw.get("name"))


def save_device(record: DeviceRecord) -> None:
    _write_json(device_path(), asdict(record))


def platform_name() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)


HOST_OS_RELEASE = Path("/run/host/os-release")  # a Flatpak sandbox shows its runtime's /etc/os-release, not the system's
_DISTRO_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")


def os_id() -> str | None:
    """A short name of the operating system, to suggest 'karasu-fedora' for a second machine."""
    if sys.platform == "win32":
        return "windows"
    found = None
    try:
        for line in HOST_OS_RELEASE.read_text().splitlines():
            if line.startswith("ID="):
                found = line[3:].strip().strip('"')
    except OSError:
        pass
    if found is None:
        try:
            found = platform.freedesktop_os_release().get("ID")
        except (OSError, AttributeError):
            return None
    return found if found and _DISTRO_ID.fullmatch(found) else None


def hostname() -> str:
    return socket.gethostname().split(".")[0] or "pc"


def register(client: MogClient, adopt_device_id: int | None = None, name: str | None = None) -> DeviceRecord:
    """Tell the server about this machine and remember what it answers.

    Raises HostnameTaken when the hostname belongs to another device and neither `adopt_device_id`
    (this is that machine) nor `name` (it is a new one) was given."""
    record = load_device()
    status, data = client.register_device(
        record.client_uid, hostname(), platform_name(), os_id(), adopt_device_id, name
    )
    if status == 200:
        record.device_id, record.name = data["id"], data["name"]
        save_device(record)
        return record
    detail = data.get("detail") if isinstance(data, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    if status == 409 and code == "hostname_taken":
        raise HostnameTaken(detail.get("devices", []), detail.get("suggested_names", []))
    if status == 409 and code == "name_taken":
        raise NameTaken(name or "")
    if status in (404, 405):
        raise ServerLacksSaveSync
    raise RuntimeError(f"could not register this device: HTTP {status}: {json.dumps(detail or data)[:200]}")


def known_device() -> DeviceRecord | None:
    """The saved identity if the server has already given it a device id."""
    record = load_device()
    return record if record.device_id is not None else None
