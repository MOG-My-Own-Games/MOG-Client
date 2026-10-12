from types import SimpleNamespace

import pytest

from mog_client import launchsettings, manager
from mog_client.config import InstalledGame
from mog_client.saves import devices

REG = 'Windows Registry Editor Version 5.00\n\n[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n"d3d9"="native"\n'


class Server:
    def __init__(self, answer=None, statuses=None):
        self.answer, self.asked, self.put = answer, [], []
        self.statuses = list(statuses or [])  # what the next puts answer; 200 once they are used up

    def launch_settings(self, game_id, device_id):
        self.asked.append((game_id, device_id))
        return self.answer

    def put_launch_settings(self, game_id, device_id, changes):
        self.put.append((game_id, device_id, changes))
        return self.statuses.pop(0) if self.statuses else 200


@pytest.fixture
def game(tmp_path):
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"x")
    rec = InstalledGame(game_id=7, name="G", install_dir=str(exe.parent), executable=str(exe), state="installed")
    manager.save_library({7: rec})
    return rec


@pytest.fixture(autouse=True)
def _this_machine_is_device_3(monkeypatch):
    monkeypatch.setattr(devices, "known_device", lambda: SimpleNamespace(device_id=3))


def _row(proton=None, reg=None, verbs=None):
    return {"proton": proton, "winecfg_reg": reg, "winetricks": verbs, "updated_at": "2026-10-11T10:00:00Z"}


def test_a_device_takes_its_own_value_first_and_the_shared_one_for_what_that_lacks():
    answer = {"shared": _row("GE-Proton9-1", REG), "device": _row("GE-Proton10-1", None)}

    assert launchsettings.effective(answer) == {"proton": "GE-Proton10-1", "winecfg_reg": REG, "winetricks": []}
    assert launchsettings.effective(None) == {"proton": None, "winecfg_reg": None, "winetricks": []}
    assert launchsettings.effective({"shared": None, "device": None}) == {"proton": None, "winecfg_reg": None, "winetricks": []}


def test_the_winetricks_verbs_of_all_devices_and_of_this_one_add_up_and_a_bad_verb_is_dropped():
    answer = {"shared": _row(verbs=["vcrun2019", "d3dx9"]), "device": _row(verbs=["dotnet48", "vcrun2019", "x; rm -rf ~", "-q"])}

    assert launchsettings.effective(answer)["winetricks"] == ["vcrun2019", "d3dx9", "dotnet48"]


def test_pulling_adopts_the_servers_build_and_applies_the_settings_once(monkeypatch, game):
    applied = []
    monkeypatch.setattr(launchsettings, "apply_reg", lambda rec, pref, reg: applied.append(reg))
    monkeypatch.setattr(manager, "set_proton", lambda rec, name, pref="auto": setattr(rec, "proton", name))
    server = Server({"shared": _row("GE-Proton9-1", REG), "device": None})
    said = []

    launchsettings.pull(server, game, "auto", said.append)
    launchsettings.pull(server, game, "auto", said.append)  # nothing new the second time

    assert server.asked[0] == (7, 3) and game.proton == "GE-Proton9-1"
    assert applied == [REG] and manager.load_library()[7].winecfg_applied == launchsettings.digest(REG)
    assert len(said) == 2


def test_pulling_leaves_alone_what_the_server_does_not_hold(monkeypatch, game):
    game.proton = "Proton 9.0"
    monkeypatch.setattr(launchsettings, "apply_reg", lambda *a: pytest.fail("nothing to apply"))

    launchsettings.pull(Server({"shared": None, "device": None}), game, "auto")
    launchsettings.pull(Server(None), game, "auto")  # an older server

    assert game.proton == "Proton 9.0"


def _synced(game):
    game.launch_synced = True
    manager.save_library({game.game_id: game})


def test_settings_deleted_on_the_server_send_the_game_back_to_the_launchers_own(monkeypatch, game):
    """The server held settings for the game (this device took them) and holds none now: they were deleted there, so the
    game runs under the launcher's own Proton again and the winecfg settings count as not applied."""
    game.proton, game.winecfg_applied = "GE-Proton9-1", launchsettings.digest(REG)
    _synced(game)
    monkeypatch.setattr(manager, "set_proton", lambda rec, name, pref="auto": setattr(rec, "proton", name))
    monkeypatch.setattr(launchsettings, "apply_reg", lambda *a: pytest.fail("nothing to apply"))
    said = []

    launchsettings.pull(Server({"shared": None, "device": None, "devices": []}), game, "auto", said.append)

    kept = manager.load_library()[7]
    assert game.proton is None and kept.winecfg_applied is None and kept.launch_synced is False
    assert "deleted on the server" in said[0] and "nothing was recorded" in said[1]  # an install from before the undo was kept


def test_a_game_the_server_never_held_settings_for_keeps_what_it_has(monkeypatch, game):
    game.proton = "Proton 9.0"
    monkeypatch.setattr(launchsettings, "apply_reg", lambda *a: pytest.fail("nothing to apply"))

    launchsettings.pull(Server({"shared": None, "device": None}), game, "auto")

    assert game.proton == "Proton 9.0" and manager.load_library()[7].launch_synced is False


def test_an_older_server_that_cannot_say_is_not_taken_for_having_deleted_them(monkeypatch, game):
    game.proton = "GE-Proton9-1"
    _synced(game)

    launchsettings.pull(Server(None), game, "auto")

    assert game.proton == "GE-Proton9-1" and manager.load_library()[7].launch_synced is True


def test_a_change_still_waiting_to_be_sent_is_not_undone_by_an_empty_answer(monkeypatch, game):
    game.proton = "GE-Proton10-1"
    _synced(game)
    monkeypatch.setattr(launchsettings, "waiting_fields", lambda game_id: {"proton"})
    monkeypatch.setattr(launchsettings, "flush", lambda client, game_id, say=None: False)

    launchsettings.pull(Server({"shared": None, "device": None}), game, "auto")

    assert game.proton == "GE-Proton10-1"


def test_taking_or_sending_settings_remembers_that_the_server_holds_them(monkeypatch, game):
    monkeypatch.setattr(manager, "set_proton", lambda rec, name, pref="auto": setattr(rec, "proton", name))
    launchsettings.pull(Server({"shared": _row("GE-Proton9-1"), "device": None}), game, "auto")
    assert manager.load_library()[7].launch_synced is True

    manager.save_library({7: InstalledGame(game_id=7, name="G", install_dir=game.install_dir, state="installed")})
    launchsettings.push(Server(), manager.load_library()[7], {"proton": "GE-Proton9-2"}, everywhere=True)
    assert manager.load_library()[7].launch_synced is True


WINE_USER_REG = (
    "WINE REGISTRY Version 2\n\n"
    "[Software\\\\Wine\\\\DllOverrides] 1700000000\n"
    '"msvcp140"="native,builtin"\n'  # the launcher's own default: not ours to touch
    '"d3d9"="builtin"\n'
)
APPLIED = (
    'Windows Registry Editor Version 5.00\n\n[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n'
    '"msvcp140"="native,builtin"\n"d3d9"="native"\n"dxgi"="native"\n'
)


@pytest.fixture
def prefix(monkeypatch, tmp_path, game):
    folder = tmp_path / "pfx"
    folder.mkdir()
    (folder / "user.reg").write_text(WINE_USER_REG)
    monkeypatch.setattr(launchsettings, "prefix_of", lambda rec: folder)
    monkeypatch.setattr(launchsettings.config, "data_dir", lambda: tmp_path / "data")
    imported = []

    def start_tool(rec, pref, program, args=()):
        imported.append(open(args[1]).read())  # the file is gone once regedit has read it
        return SimpleNamespace(wait=lambda timeout: 0)

    monkeypatch.setattr(launchsettings.launcher, "start_tool", start_tool)
    monkeypatch.setattr(launchsettings.watcher, "wait_for_tool", lambda *a, **k: True)
    return SimpleNamespace(folder=folder, imported=imported)


def test_applying_settings_notes_only_what_they_change_in_the_prefix(game, prefix):
    launchsettings.apply_reg(game, "auto", APPLIED)

    undo = launchsettings.undo_path(game).read_text()
    assert '"d3d9"="builtin"' in undo and '"dxgi"=-' in undo  # put back, and removed
    assert "msvcp140" not in undo  # the launcher's own value did not change


def test_taking_the_settings_back_puts_the_prefix_as_it_was(game, prefix):
    launchsettings.apply_reg(game, "auto", APPLIED)
    prefix.imported.clear()

    assert launchsettings.revert_reg(game, "auto") is True

    assert len(prefix.imported) == 1 and '"dxgi"=-' in prefix.imported[0] and "msvcp140" not in prefix.imported[0]
    assert not launchsettings.undo_path(game).exists()
    assert launchsettings.revert_reg(game, "auto") is False  # nothing is kept for a second time


def test_a_second_application_keeps_the_oldest_value_to_put_back(game, prefix):
    launchsettings.apply_reg(game, "auto", APPLIED)
    (prefix.folder / "user.reg").write_text(WINE_USER_REG + '"d3d9"="native"\n"dxgi"="native"\n')
    launchsettings.apply_reg(game, "auto", APPLIED.replace('"d3d9"="native"', '"d3d9"="disabled"'))

    undo = launchsettings.undo_path(game).read_text()
    assert '"d3d9"="builtin"' in undo and '"d3d9"="native"' not in undo  # what it was before either


def test_settings_deleted_on_the_server_are_taken_out_of_the_prefix(monkeypatch, game, prefix):
    launchsettings.apply_reg(game, "auto", APPLIED)
    game.winecfg_applied = launchsettings.digest(APPLIED)
    _synced(game)
    prefix.imported.clear()
    said = []

    launchsettings.pull(Server({"shared": None, "device": None, "devices": []}), game, "auto", said.append)

    assert len(prefix.imported) == 1 and '"dxgi"=-' in prefix.imported[0]
    assert manager.load_library()[7].winecfg_applied is None and "taken out of its prefix" in said[-1]


def test_when_regedit_fails_the_settings_stay_recorded_for_the_next_start(monkeypatch, game, prefix):
    launchsettings.apply_reg(game, "auto", APPLIED)
    game.winecfg_applied = launchsettings.digest(APPLIED)
    _synced(game)
    monkeypatch.setattr(
        launchsettings.launcher, "start_tool", lambda *a, **k: SimpleNamespace(wait=lambda timeout: 1, returncode=1)
    )
    monkeypatch.setattr(launchsettings.launcher, "launch_failure", lambda proc: "regedit broke")
    said = []

    launchsettings.pull(Server({"shared": None, "device": None, "devices": []}), game, "auto", said.append)

    kept = manager.load_library()[7]
    assert kept.winecfg_applied is not None and kept.launch_synced is True  # tried again at the next start
    assert launchsettings.undo_path(game).exists() and "could not be taken out" in said[-1]


def test_winecfg_run_here_notes_what_it_changed(monkeypatch, game, prefix):
    monkeypatch.setattr(launchsettings.launcher, "start_tool", lambda rec, pref, program, args=(): SimpleNamespace(poll=lambda: None))
    changed = WINE_USER_REG + '"dxgi"="native"\n'

    def wait(*a, **k):
        (prefix.folder / "user.reg").write_text(changed)  # winecfg wrote the registry
        return True

    text = launchsettings.run_winecfg(game, "auto", sleep=lambda s: None, wait=wait)

    assert '"dxgi"="native"' in text
    undo = launchsettings.undo_path(game).read_text()
    assert '"dxgi"=-' in undo and "msvcp140" not in undo


def test_settings_from_the_server_are_filtered_before_they_reach_regedit(monkeypatch, game):
    started = []
    monkeypatch.setattr(
        launchsettings.launcher, "start_tool", lambda rec, pref, program, args=(): started.append((program, args)) or SimpleNamespace(wait=lambda timeout: 0)
    )
    monkeypatch.setattr(launchsettings.watcher, "wait_for_tool", lambda *a, **k: True)
    hostile = REG + '\n[HKEY_LOCAL_MACHINE\\Software\\Evil]\n"x"="y"\n'

    launchsettings.apply_reg(game, "auto", hostile)

    program, args = started[0]
    assert program == "regedit" and args[0] == "/S"
    written = open(args[1]).read()
    assert written == REG and "Evil" not in written


def test_nothing_is_run_for_settings_with_nothing_allowed_in_them(monkeypatch, game):
    monkeypatch.setattr(launchsettings.launcher, "start_tool", lambda *a, **k: pytest.fail("regedit must not run"))

    launchsettings.apply_reg(game, "auto", 'Windows Registry Editor Version 5.00\n\n[HKEY_LOCAL_MACHINE\\X]\n"a"="b"\n')


def test_pushing_goes_to_this_device_or_to_all_of_them(game):
    server = Server()

    launchsettings.push(server, game, {"proton": "GE-Proton9-1"}, everywhere=False)
    launchsettings.push(server, game, {"winecfg_reg": REG}, everywhere=True)

    assert server.put == [(7, 3, {"proton": "GE-Proton9-1"}), (7, None, {"winecfg_reg": REG})]


def test_a_machine_the_server_cannot_name_keeps_a_this_device_change_waiting_and_never_sends_it_for_all(monkeypatch, game):
    monkeypatch.setattr(devices, "known_device", lambda: None)

    def refuse(client):
        raise devices.HostnameTaken([], [])

    monkeypatch.setattr(devices, "register", refuse)
    server = Server()

    assert launchsettings.push(server, game, {"proton": "x"}, everywhere=False) is False

    assert server.put == [] and launchsettings.waiting(7) == [{"everywhere": False, "changes": {"proton": "x"}}]


def test_winecfg_returns_what_it_left_in_the_prefix(monkeypatch, game, tmp_path):
    prefix = tmp_path / "Game" / "pfx"
    prefix.mkdir()
    reg = prefix / "user.reg"
    reg.write_text("WINE REGISTRY Version 2\n")

    def start(rec, pref, program, args=()):
        assert program == "winecfg"
        reg.write_text('WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\DllOverrides] 1\n"d3d9"="native"\n')
        return SimpleNamespace()

    monkeypatch.setattr(launchsettings.launcher, "start_tool", start)

    waited = []
    wait = lambda tool, prefix, **k: waited.append((tool, prefix)) or True  # noqa: E731

    assert launchsettings.run_winecfg(game, "auto", sleep=lambda _s: None, wait=wait) == REG
    assert waited == [("winecfg", prefix)]  # winecfg's own process is waited for, not a .exe of the game's folder


def test_winecfg_that_never_starts_is_an_error(monkeypatch, game):
    monkeypatch.setattr(launchsettings.launcher, "start_tool", lambda *a, **k: SimpleNamespace(poll=lambda: 1, log_path=None))

    with pytest.raises(RuntimeError):
        launchsettings.run_winecfg(game, "auto", sleep=lambda _s: None, wait=lambda *a, **k: False)


def _ge_game(game, monkeypatch, tmp_path, engine="faugus", have=()):
    root = tmp_path / "steam"
    for name in have:
        (root / "compatibilitytools.d" / name).mkdir(parents=True)
        (root / "compatibilitytools.d" / name / "proton").write_text("")
    monkeypatch.setattr(launchsettings.launcher, "steam_roots", lambda: [root])
    monkeypatch.setattr(launchsettings, "detect_launcher", lambda pref="auto": engine)
    game.proton = "GE-Proton9-27"
    return root


@pytest.mark.parametrize(("engine", "expected"), [("faugus", True), ("umu", True), ("proton", True), ("wine", False)])
def test_a_ge_build_this_device_lacks_is_fetched_for_every_engine_that_runs_proton(monkeypatch, game, tmp_path, engine, expected):
    _ge_game(game, monkeypatch, tmp_path, engine)

    assert launchsettings.needs_download(game, "auto") is expected


def test_nothing_is_fetched_for_a_build_that_is_here_a_valve_build_or_no_choice(monkeypatch, game, tmp_path):
    _ge_game(game, monkeypatch, tmp_path, have=("GE-Proton9-27",))
    assert not launchsettings.needs_download(game, "auto")

    game.proton = "Proton 9.0"
    assert not launchsettings.needs_download(game, "auto")
    game.proton = None
    assert not launchsettings.needs_download(game, "auto")


def test_the_build_is_fetched_into_the_launchers_folder_and_the_launch_script_follows(monkeypatch, game, tmp_path):
    root = _ge_game(game, monkeypatch, tmp_path)
    fetched, rewritten, said = [], [], []
    monkeypatch.setattr(launchsettings.protondl, "install", lambda name, into, on_progress=None: fetched.append((name, into)))
    monkeypatch.setattr(manager, "set_proton", lambda rec, name, pref="auto": rewritten.append(name))

    assert launchsettings.ensure_build(game, "auto", said.append) is True

    assert fetched == [("GE-Proton9-27", root / "compatibilitytools.d")] and rewritten == ["GE-Proton9-27"]
    assert said and "GE-Proton9-27" in said[0]


def test_a_failed_download_is_an_error_and_leaves_the_script_alone(monkeypatch, game, tmp_path):
    _ge_game(game, monkeypatch, tmp_path)

    def offline(*a, **k):
        raise RuntimeError("could not fetch GE-Proton9-27: offline")

    monkeypatch.setattr(launchsettings.protondl, "install", offline)
    monkeypatch.setattr(manager, "set_proton", lambda *a, **k: pytest.fail("the script must not change"))

    with pytest.raises(RuntimeError, match="offline"):
        launchsettings.ensure_build(game, "auto")


def test_pulling_fetches_the_servers_build_before_the_game_starts(monkeypatch, game, tmp_path):
    _ge_game(game, monkeypatch, tmp_path)
    game.proton = None
    fetched = []
    monkeypatch.setattr(manager, "set_proton", lambda rec, name, pref="auto": setattr(rec, "proton", name))
    monkeypatch.setattr(launchsettings.protondl, "install", lambda name, into, on_progress=None: fetched.append(name))

    launchsettings.pull(Server({"shared": _row("GE-Proton9-27"), "device": None}), game, "auto")

    assert fetched == ["GE-Proton9-27"]


def test_pulling_installs_the_verbs_the_prefix_lacks_first_then_the_winecfg_settings_and_only_once(monkeypatch, game):
    order, ran = [], []
    monkeypatch.setattr(launchsettings, "apply_reg", lambda rec, pref, reg: order.append("reg"))
    monkeypatch.setattr(
        launchsettings, "run_winetricks", lambda rec, pref, verbs: (order.append("verbs"), ran.append(verbs), rec.winetricks.extend(verbs))
    )
    server = Server({"shared": _row(reg=REG, verbs=["vcrun2019", "d3dx9"]), "device": None})
    game.winetricks = ["d3dx9"]
    said = []

    launchsettings.pull(server, game, "auto", said.append)
    launchsettings.pull(server, game, "auto", said.append)

    assert ran == [["vcrun2019"]] and order == ["verbs", "reg"]
    assert any("vcrun2019" in m for m in said)


def test_a_reinstalled_game_gets_every_verb_again_because_its_record_starts_empty(monkeypatch, game):
    ran = []
    monkeypatch.setattr(launchsettings, "run_winetricks", lambda rec, pref, verbs: ran.append(verbs))

    launchsettings.pull(Server({"shared": _row(verbs=["vcrun2019", "dotnet48"]), "device": None}), game, "auto")

    assert game.winetricks == [] and ran == [["vcrun2019", "dotnet48"]]


def test_winetricks_installs_unattended_and_notes_the_verbs_only_when_it_worked(monkeypatch, game):
    started = []
    results = iter([0, 3])

    def start(rec, pref, program, args=()):
        started.append((program, args))
        return SimpleNamespace(wait=lambda: next(results), poll=lambda: 0, log_path=None)

    monkeypatch.setattr(launchsettings.launcher, "start_tool", start)
    monkeypatch.setattr(launchsettings.watcher, "wait_for_tool", lambda *a, **k: True)

    launchsettings.run_winetricks(game, "auto", ["vcrun2019", "d3dx9", "vcrun2019"])
    assert started == [("winetricks", ("-q", "vcrun2019", "d3dx9"))] and game.winetricks == ["vcrun2019", "d3dx9"]
    assert manager.load_library()[7].winetricks == ["vcrun2019", "d3dx9"]

    with pytest.raises(RuntimeError, match="exited with 3"):
        launchsettings.run_winetricks(game, "auto", ["dotnet48"])
    assert game.winetricks == ["vcrun2019", "d3dx9"]


def test_nothing_is_run_for_verbs_that_are_not_verbs(monkeypatch, game):
    monkeypatch.setattr(launchsettings.launcher, "start_tool", lambda *a, **k: pytest.fail("winetricks must not run"))

    launchsettings.run_winetricks(game, "auto", ["a; rm -rf ~", "-q", ""])


def test_a_change_waits_in_a_file_until_the_server_says_200_and_is_sent_again_at_the_next_look(game):
    down = Server(statuses=[None])

    assert launchsettings.push(down, game, {"proton": "GE-Proton9-27"}, everywhere=True) is False
    assert launchsettings.waiting(7) == [{"everywhere": True, "changes": {"proton": "GE-Proton9-27"}}]
    assert launchsettings.outbox_path().is_file() and launchsettings.has_waiting()  # on disk: a restart does not lose it

    up = Server()
    assert launchsettings.flush(up, 7) is True

    assert up.put == [(7, None, {"proton": "GE-Proton9-27"})]
    assert launchsettings.waiting(7) == [] and not launchsettings.has_waiting()


def test_a_later_change_to_the_same_scope_replaces_the_earlier_one_waiting(game):
    down = Server(statuses=[503, 503, 503])

    launchsettings.push(down, game, {"proton": "a", "winetricks": ["vcrun2019"]}, everywhere=True)
    launchsettings.push(down, game, {"proton": "b"}, everywhere=True)
    launchsettings.push(down, game, {"winetricks": ["vcrun2019", "d3dx9"]}, everywhere=False)

    assert launchsettings.waiting(7) == [
        {"everywhere": True, "changes": {"proton": "b", "winetricks": ["vcrun2019"]}},
        {"everywhere": False, "changes": {"winetricks": ["vcrun2019", "d3dx9"]}},
    ]


@pytest.mark.parametrize("status", [None, 500, 502, 503, 401, 403, 408, 429])
def test_an_unreachable_or_unwell_server_keeps_the_change(game, status):
    assert launchsettings.push(Server(statuses=[status]), game, {"proton": "x"}, everywhere=True) is False

    assert launchsettings.waiting(7) == [{"everywhere": True, "changes": {"proton": "x"}}]


@pytest.mark.parametrize("status", [404, 405, 409, 422])
def test_a_change_the_server_refuses_for_good_is_dropped_so_it_does_not_block_what_follows(game, status):
    said = []
    launchsettings.queue(7, {"winetricks": ["bad"]}, True)
    launchsettings.queue(7, {"proton": "x"}, False)
    server = Server(statuses=[status, 200])

    assert launchsettings.flush(server, 7, said.append) is True

    assert [c for _g, _d, c in server.put] == [{"winetricks": ["bad"]}, {"proton": "x"}]
    assert launchsettings.waiting(7) == [] and said and str(status) in said[0]


def test_what_waits_is_sent_oldest_first_and_stops_at_the_first_the_server_cannot_take(game):
    launchsettings.queue(7, {"proton": "a"}, True)
    launchsettings.queue(7, {"winecfg_reg": REG}, False)
    server = Server(statuses=[200, 503])

    assert launchsettings.flush(server, 7) is False

    assert [c for _g, _d, c in server.put] == [{"proton": "a"}, {"winecfg_reg": REG}]
    assert launchsettings.waiting(7) == [{"everywhere": False, "changes": {"winecfg_reg": REG}}]


def test_flush_all_tries_every_game_with_something_waiting_and_keeps_going_only_while_the_server_answers(game):
    launchsettings.queue(7, {"proton": "a"}, True)
    launchsettings.queue(8, {"proton": "b"}, True)
    said = []

    launchsettings.flush_all(Server(statuses=[503]), said.append)
    assert launchsettings.has_waiting() and said == []  # the first could not be sent: no point asking for the second

    launchsettings.flush_all(Server(), said.append)
    assert not launchsettings.has_waiting() and len(said) == 2


def test_a_change_waiting_outlives_the_games_record_so_an_uninstall_does_not_lose_it(game):
    launchsettings.queue(7, {"winetricks": ["vcrun2019"]}, True)
    manager.save_library({})  # the game is uninstalled

    assert launchsettings.waiting(7) == [{"everywhere": True, "changes": {"winetricks": ["vcrun2019"]}}]


def test_pulling_sends_what_waits_first_and_never_replaces_a_change_not_sent_yet_with_the_servers_older_value(monkeypatch, game):
    monkeypatch.setattr(manager, "set_proton", lambda rec, name, pref="auto": setattr(rec, "proton", name))
    monkeypatch.setattr(launchsettings, "apply_reg", lambda *a: pytest.fail("the unsent winecfg settings stay"))
    game.proton = "GE-Proton10-4"  # chosen here, while the server was down
    launchsettings.queue(7, {"proton": "GE-Proton10-4", "winecfg_reg": REG}, True)
    still_down = Server({"shared": _row("GE-Proton9-1", "older reg"), "device": None}, statuses=[503])

    launchsettings.pull(still_down, game, "auto")

    assert game.proton == "GE-Proton10-4" and launchsettings.waiting(7)  # kept, and still waiting
