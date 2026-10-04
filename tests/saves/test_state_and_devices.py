import pytest

from mog_client.saves import devices
from mog_client.saves.devices import HostnameTaken, NameTaken, load_device, register
from mog_client.saves.state import (
    FileInfo,
    SaveState,
    load_install_manifest,
    load_state,
    save_install_manifest,
    save_state,
)


def test_the_state_of_a_game_round_trips_and_ignores_what_it_does_not_know(tmp_path):
    state = SaveState(prefix="/p", prefix_source="faugus", includes=["users/USER/Documents"], last_version_id=4)
    state.tracked["game/a.sav"] = FileInfo(3, 99, "abc")
    save_state(7, state)

    again = load_state(7)
    assert again == state and again.tracked["game/a.sav"].sha256 == "abc"
    assert load_state(8) == SaveState()


def test_a_damaged_state_file_starts_over(tmp_path):
    from mog_client.saves.state import state_path

    state_path(7).parent.mkdir(parents=True)
    state_path(7).write_text("{broken")
    assert load_state(7) == SaveState()


def test_the_install_manifest_is_kept_by_path(tmp_path):
    assert load_install_manifest(7) is None
    save_install_manifest(7, [{"path": "Data/a.dat", "size_bytes": 5, "sha1": "ff"}])
    assert load_install_manifest(7) == {"Data/a.dat": (5, "ff")}


class FakeServer:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def register_device(self, *args):
        self.calls.append(args)
        return self.replies.pop(0)


def test_the_machine_gets_one_uid_for_good():
    first = load_device()
    assert first.device_id is None and len(first.client_uid) >= 8
    assert load_device().client_uid == first.client_uid


def test_registering_remembers_the_device_the_server_names():
    server = FakeServer([(200, {"id": 3, "name": "karasu"})])
    record = register(server)
    assert (record.device_id, record.name) == (3, "karasu")
    assert load_device().device_id == 3 and server.calls[0][0] == record.client_uid


def test_a_taken_hostname_is_raised_with_the_devices_and_suggestions():
    detail = {"code": "hostname_taken", "devices": [{"id": 1, "name": "karasu"}], "suggested_names": ["karasu-fedora"]}
    with pytest.raises(HostnameTaken) as asked:
        register(FakeServer([(409, {"detail": detail})]))
    assert asked.value.devices[0]["id"] == 1 and asked.value.suggested_names == ["karasu-fedora"]
    assert load_device().device_id is None


def test_the_answer_can_be_this_is_that_machine_or_a_new_name(monkeypatch):
    server = FakeServer([(200, {"id": 1, "name": "karasu"}), (200, {"id": 2, "name": "karasu-fedora"})])
    assert register(server, adopt_device_id=1).device_id == 1
    assert server.calls[0][-2:] == (1, None)
    assert register(server, name="karasu-fedora").name == "karasu-fedora"
    assert server.calls[1][-2:] == (None, "karasu-fedora")


def test_a_name_in_use_and_other_errors():
    with pytest.raises(NameTaken):
        register(FakeServer([(409, {"detail": {"code": "name_taken"}})]), name="x")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        register(FakeServer([(500, {"detail": "boom"})]))


def test_os_id_names_the_system(monkeypatch, tmp_path):
    monkeypatch.setattr(devices, "HOST_OS_RELEASE", tmp_path / "no-host-release")
    monkeypatch.setattr(devices.platform, "freedesktop_os_release", lambda: {"ID": "fedora"})
    monkeypatch.setattr(devices.sys, "platform", "linux")
    assert devices.os_id() == "fedora"
    monkeypatch.setattr(devices.sys, "platform", "win32")
    assert devices.os_id() == "windows" and devices.platform_name() == "windows"


def test_inside_a_flatpak_sandbox_the_host_is_named_not_the_runtime(monkeypatch, tmp_path):
    host = tmp_path / "os-release"
    host.write_text('NAME="Fedora Linux"\nID=fedora\nVERSION_ID=44\n')
    monkeypatch.setattr(devices, "HOST_OS_RELEASE", host)
    monkeypatch.setattr(devices.platform, "freedesktop_os_release", lambda: {"ID": "org.freedesktop.platform"})
    monkeypatch.setattr(devices.sys, "platform", "linux")
    assert devices.os_id() == "fedora"


def test_an_id_that_is_not_a_distro_name_is_not_used(monkeypatch, tmp_path):
    monkeypatch.setattr(devices, "HOST_OS_RELEASE", tmp_path / "missing")
    monkeypatch.setattr(devices.platform, "freedesktop_os_release", lambda: {"ID": "org.freedesktop.platform"})
    monkeypatch.setattr(devices.sys, "platform", "linux")
    assert devices.os_id() is None
