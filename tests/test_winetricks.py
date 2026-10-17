import pytest

from mog_client import launcher, winetricks
from mog_client.config import InstalledGame


def test_the_catalog_holds_only_real_verbs_once_each():
    verbs = [v for v, _text in winetricks.CATALOG]

    assert all(winetricks.is_verb(v) for v in verbs) and len(verbs) == len(set(verbs))
    assert "vcrun2019" in verbs and "dotnet48" in verbs


@pytest.mark.parametrize("value", ["vcrun2019", "d3dx11_43", "dotnet48", "a", "xact_x64"])
def test_verbs(value):
    assert winetricks.is_verb(value)


@pytest.mark.parametrize("value", ["", "-q", "--force", "Vcrun", "a b", "a;b", "a/b", "$(x)", "x" * 41, None, 3])
def test_what_is_not_a_verb(value):
    assert not winetricks.is_verb(value)


def test_clean_keeps_verbs_once_each_in_order_and_what_is_not_a_list_is_empty():
    assert winetricks.clean(["b", "a", "b", "-q", "x y"]) == ["b", "a"]
    assert winetricks.clean(None) == [] and winetricks.clean("vcrun2019") == []
    assert len(winetricks.clean([f"v{n}" for n in range(500)])) == winetricks.MAX_VERBS


def test_missing_is_what_is_wanted_and_not_applied_yet():
    assert winetricks.missing(["a", "b", "c", "a"], ["b"]) == ["a", "c"]
    assert winetricks.missing([], ["b"]) == []


def _game(tmp_path, **extra):
    exe = tmp_path / "Game" / "game.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"x")
    return InstalledGame(game_id=7, name="G", install_dir=str(exe.parent), executable=str(exe), **extra)


def test_umu_and_faugus_run_winetricks_as_the_program_like_any_other(monkeypatch, tmp_path):
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    game = _game(tmp_path)

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    cmd, extra = launcher.launch_command(game, program="winetricks", args=("-q", "vcrun2019"))
    assert cmd == ["umu-run", "winetricks", "-q", "vcrun2019"] and extra["WINEPREFIX"].endswith("Game/pfx")

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    cmd, _ = launcher.launch_command(game, program="winetricks", args=("-q", "vcrun2019"))
    assert cmd[:2] == ["faugus-launcher", "--run"] and cmd[2].endswith(f"{umu} winetricks -q vcrun2019")


def test_wine_runs_winetricks_on_the_prefix_with_the_chosen_build(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/winetricks")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    program = tmp_path / "bin" / "wine"
    monkeypatch.setattr(launcher, "resolve_wine", lambda name: (program, None) if name else (None, None))

    cmd, extra = launcher.launch_command(_game(tmp_path, wine="ge"), program="winetricks", args=("-q", "d3dx9"))
    assert cmd == ["winetricks", "-q", "d3dx9"] and extra == {"WINEPREFIX": str(tmp_path / "Game/pfx"), "WINE": str(program)}

    assert "WINE" not in launcher.launch_command(_game(tmp_path), program="winetricks")[1]


def test_proton_runs_winetricks_with_its_own_wine_on_the_prefix_it_keeps_one_level_down(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/winetricks")
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "proton")
    build = tmp_path / "steam" / "compatibilitytools.d" / "GE-Proton9-27"
    (build / "files/bin").mkdir(parents=True)
    (build / "proton").write_text("")
    (build / "files/bin/wine").write_text("")
    monkeypatch.setattr(launcher, "steam_roots", lambda: [tmp_path / "steam"])
    (tmp_path / "Game" / "pfx" / "pfx").mkdir(parents=True)

    cmd, extra = launcher.launch_command(_game(tmp_path, proton="GE-Proton9-27"), program="winetricks", args=("-q", "vcrun2019"))

    assert cmd == ["winetricks", "-q", "vcrun2019"]
    assert extra == {"WINEPREFIX": str(tmp_path / "Game/pfx/pfx"), "WINE": str(build / "files/bin/wine")}


def test_a_missing_winetricks_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")

    with pytest.raises(RuntimeError, match="winetricks is not installed"):
        launcher.launch_command(_game(tmp_path), program="winetricks")


@pytest.mark.parametrize("value", ["renderer=gdi", "vd=1280x1024", "vd=off", "mwo=force", "sound=alsa", "csmt=on", "a=b", "x=1,2:3+4_5.6-7"])
def test_settings_with_a_value_are_entries(value):
    assert winetricks.is_verb(value)


@pytest.mark.parametrize(
    "value",
    ["vd=", "=gdi", "vd=a=b", "vd=1280 x 1024", "vd=a;b", "vd=$(id)", "vd=`id`", "vd='a'", "vd=a|b", "vd=a&b", "vd=../x", "VD=1", "-vd=1", "vd=" + "x" * 41, "v" * 41 + "=a", "vd=a\nb"],
)
def test_what_is_not_a_setting(value):
    assert not winetricks.is_verb(value)


def test_a_setting_has_one_value_a_later_one_replaces_an_earlier_in_the_place_it_had():
    assert winetricks.clean(["vd=1280x1024", "vcrun2019", "renderer=gdi", "vd=1920x1080"]) == ["vd=1920x1080", "vcrun2019", "renderer=gdi"]
    assert winetricks.clean(["a", "b", "a"]) == ["a", "b"]  # flags as before


def test_a_setting_whose_value_changed_is_one_change_to_make_not_another_verb():
    applied = ["vcrun2019", "vd=1280x1024"]

    assert winetricks.missing(["vcrun2019", "vd=1920x1080"], applied) == ["vd=1920x1080"]
    assert winetricks.missing(["vcrun2019", "vd=1280x1024"], applied) == []
    assert winetricks.merge(applied, ["vd=1920x1080", "dotnet48"]) == ["vcrun2019", "vd=1920x1080", "dotnet48"]  # one vd, not two


def test_of_two_values_ticked_for_a_setting_the_one_the_prefix_lacks_is_the_choice_just_made():
    applied = ["vd=1280x1024", "vcrun2019"]

    assert winetricks.resolve(["vcrun2019", "vd=1280x1024", "vd=1920x1080"], applied) == ["vcrun2019", "vd=1920x1080"]
    assert winetricks.resolve(["vd=1920x1080", "vd=1280x1024"], applied) == ["vd=1920x1080"]  # the new one still wins when it comes first
    assert winetricks.resolve(["vd=800x600", "vd=1024x768"], applied) == ["vd=1024x768"]  # neither is new: the last
    assert winetricks.resolve(["a", "b"], []) == ["a", "b"]


def test_the_settings_offered_are_entries_for_each_of_their_values_and_one_name_is_listed_once():
    names = [name for name, _text, _values in winetricks.SETTINGS]

    assert len(names) == len(set(names)) and "vd" in names and "renderer" in names
    assert all(winetricks.is_verb(f"{name}={value}") for name, _t, values in winetricks.SETTINGS for value in values)


@pytest.mark.parametrize(
    ("entry", "changes"),
    [
        ("vd=1280x1024", [("Explorer", '"Desktop"="Default"'), ("Explorer\\Desktops", '"Default"="1280x1024"')]),
        ("vd=off", [("Explorer", '"Desktop"=-')]),
        ("renderer=gdi", [("Direct3D", '"renderer"="gdi"')]),
        ("csmt=on", [("Direct3D", '"csmt"=dword:00000001')]),
        ("csmt=off", [("Direct3D", '"csmt"=dword:00000000')]),
        ("mwo=force", [("DirectInput", '"MouseWarpOverride"="force"')]),
        ("sound=pulse", [("Drivers", '"Audio"="pulse"')]),
        ("sound=disabled", [("Drivers", '"Audio"=""')]),
        ("windowmanagerdecorated=n", [("X11 Driver", '"Decorated"="N"')]),
        ("videomemorysize=512", [("Direct3D", '"VideoMemorySize"="512"')]),
    ],
)
def test_the_settings_the_client_puts_in_the_registry_itself(entry, changes):
    assert winetricks.registry_changes(entry) == changes


@pytest.mark.parametrize("entry", ["vcrun2019", "virtualdesktop=1280x800", "vd=12x1", "vd=1280", "renderer=dx", "csmt=force", "sound=jack", "fontsmooth=rgb", "vd="])
def test_what_the_client_does_not_put_in_the_registry_is_left_alone(entry):
    assert winetricks.registry_changes(entry) is None


def test_the_catalog_has_the_visual_cpp_runtimes_games_ask_for_and_every_setting_value_is_an_entry():
    verbs = [v for v, _text in winetricks.CATALOG]
    assert {"vcrun2015", "vcrun2017", "vcrun2022", "mf", "dotnetdesktop8"} <= set(verbs)
    assert all(winetricks.is_verb(f"{name}={value}") for name, _text, values in winetricks.SETTINGS for value in values)


def test_removed_is_what_was_unticked_and_not_a_setting_given_another_value():
    kept = ["vcrun2019", "vd=1920x1080"]
    assert winetricks.removed(kept, ["vcrun2019", "d3dx9", "vd=1280x1024", "renderer=gl"]) == ["d3dx9", "renderer=gl"]
    assert winetricks.removed(kept, []) == []


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("vd=1280x1024", [("Explorer", '"Desktop"=-')]),
        ("renderer=no3d", [("Direct3D", '"renderer"=-')]),
        ("csmt=on", [("Direct3D", '"csmt"=-')]),
        ("sound=pulse", [("Drivers", '"Audio"=-')]),
        ("grabfullscreen=y", [("X11 Driver", '"GrabFullscreen"=-')]),
        ("videomemorysize=2048", [("Direct3D", '"VideoMemorySize"=-')]),
        ("vcrun2019", None),
        ("fontsmooth=rgb", None),  # winetricks writes it: only a new prefix takes it out
        ("renderer=bogus", None),
    ],
)
def test_unticking_a_setting_the_client_writes_deletes_its_registry_value(entry, expected):
    assert winetricks.registry_reset(entry) == expected


def test_the_new_settings_are_written_without_winetricks():
    assert winetricks.registry_changes("grabfullscreen=y") == [("X11 Driver", '"GrabFullscreen"="Y"')]
    assert winetricks.registry_changes("renderer=no3d") == [("Direct3D", '"renderer"="no3d"')]
    assert winetricks.registry_changes("videomemorysize=default") == [("Direct3D", '"VideoMemorySize"=-')]


def test_every_exe_of_the_game_is_listed_but_none_of_its_prefixes(tmp_path):
    for rel in ("Game.exe", "redist/vcredist_x64.exe", "Config/Setup.exe", "pfx/drive_c/x.exe", "pfx.rebuild/drive_c/y.exe", "start.sh"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")

    found = [str(p.relative_to(tmp_path)) for p in launcher.all_executables(tmp_path)]

    assert found == ["Config/Setup.exe", "Game.exe", "redist/vcredist_x64.exe"]


def test_a_verb_of_the_catalog_with_a_value_is_no_entry():
    assert not winetricks.is_verb("vcrun2015=true") and winetricks.is_verb("vd=1280x1024")
    assert winetricks.clean(["vcrun2015=true", "vcrun2019"]) == ["vcrun2019"]
    assert winetricks.merge(["vcrun2015=true"], ["d3dx9"]) == ["d3dx9"]


def test_faugus_is_never_handed_a_setting_it_would_take_for_an_environment_variable(monkeypatch, tmp_path):
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")

    with pytest.raises(RuntimeError, match="fontsmooth=rgb"):  # winetricks would get no verb, and open its own window
        launcher.launch_command(_game(tmp_path), program="winetricks", args=("-q", "vcrun2019", "fontsmooth=rgb"))
    assert launcher.faugus_takes_as_env(["-q", "a-b=1", "/S", "x=1"]) == ["x=1"]


def test_hidewineexports_is_offered_and_written_straight_under_the_wine_key():
    assert "hidewineexports=enable" in [f"{n}={v}" for n, _t, vs in winetricks.SETTINGS for v in vs]
    assert winetricks.registry_changes("hidewineexports=enable") == [("", '"HideWineExports"="Y"')]
    assert winetricks.registry_changes("hidewineexports=disable") == [("", '"HideWineExports"=-')]
    assert winetricks.registry_reset("hidewineexports=enable") == [("", '"HideWineExports"=-')]
    assert winetricks.registry_changes("hidewineexports=maybe") is None
    reg = launchsettings_reg(winetricks.registry_changes("hidewineexports=enable"))
    assert "[HKEY_CURRENT_USER\\Software\\Wine]\n\"HideWineExports\"=\"Y\"" in reg


def launchsettings_reg(changes):
    from mog_client import winecfg

    return winecfg._reg_of(changes)


def test_the_languages_are_offered_once_each_and_a_game_gets_the_one_it_has():
    codes = [c for c, *_rest in winetricks.LANGUAGES]
    assert len(codes) == len(set(codes)) and all(winetricks.is_verb(f"locale={c}") for c in codes)
    assert winetricks.locale_of(["vcrun2019", "locale=ja_JP"]) == "ja_JP" and winetricks.locale_of(["locale=xx_XX", "vd=off"]) is None
    assert winetricks.locale_env(["locale=zh_CN"]) == {"LC_ALL": "zh_CN.UTF-8", "LANG": "zh_CN.UTF-8"} and winetricks.locale_env([]) == {}


def test_a_language_sets_the_locale_and_the_code_pages_with_their_whole_paths():
    changes = winetricks.registry_changes("locale=ja_JP")
    assert changes == [
        ("HKEY_CURRENT_USER\\Control Panel\\International", '"LocaleName"="ja-JP"'),
        (winetricks.CODEPAGE_KEY, '"ACP"="932"'),
        (winetricks.CODEPAGE_KEY, '"OEMCP"="932"'),
    ]
    assert winetricks.registry_changes("locale=de_DE")[1:] == [(winetricks.CODEPAGE_KEY, '"ACP"="1252"'), (winetricks.CODEPAGE_KEY, '"OEMCP"="850"')]
    assert winetricks.registry_changes("locale=xx_XX") is None
    assert winetricks.registry_reset("locale=ja_JP") == [("HKEY_CURRENT_USER\\Control Panel\\International", '"LocaleName"=-')]
    from mog_client import winecfg

    text = winecfg._reg_of(changes + [("Direct3D", '"csmt"=dword:00000001')])
    assert "[HKEY_CURRENT_USER\\Control Panel\\International]" in text and "[HKEY_CURRENT_USER\\Software\\Wine\\Direct3D]" in text


def test_the_game_is_started_with_its_language_by_every_engine(monkeypatch, tmp_path):
    umu = tmp_path / "umu-run"
    umu.write_text("")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-launcher", "--run"], str(umu), False))
    game = _game(tmp_path, winetricks=["locale=ja_JP"])

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "umu")
    assert launcher.launch_command(game)[1]["LC_ALL"] == "ja_JP.UTF-8"

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    cmd, _ = launcher.launch_command(game)
    assert "LC_ALL=ja_JP.UTF-8" in cmd[2] and "LANG=ja_JP.UTF-8" in cmd[2]

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "wine")
    monkeypatch.setattr(launcher, "resolve_wine", lambda name: (tmp_path / "wine", None))
    assert launcher.launch_command(game)[1]["LANG"] == "ja_JP.UTF-8"
    assert "LC_ALL" not in launcher.launch_command(_game(tmp_path))[1]
