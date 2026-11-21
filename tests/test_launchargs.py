import pytest

from mog_client import launchargs


@pytest.mark.parametrize(
    ("typed", "tags"),
    [
        ("", []),
        ("-windowed -nosound", ["-windowed", "-nosound"]),
        ("-res 1280 720 -windowed --nosound", ["-res 1280 720", "-windowed", "--nosound"]),
        ("--width=1280 --height 720", ["--width=1280", "--height 720"]),
        ("intro.avi -w", ["intro.avi", "-w"]),
        ("-x -5 -y", ["-x -5", "-y"]),
        ("-title 'My Game' -w", ["-title 'My Game'", "-w"]),
        ("-w -w -nosound", ["-w", "-nosound"]),
        ("-title 'oops -w", ["-title 'oops", "-w"]),
        ('WINEDLLOVERRIDES="dinput8=n,b" -w', ["WINEDLLOVERRIDES=dinput8=n,b", "-w"]),
        ("-res 1280 720 MANGOHUD=1 -w", ["-res 1280 720", "MANGOHUD=1", "-w"]),
        ("FOO='a b' -x", ["'FOO=a b'", "-x"]),
    ],
)
def test_typed_arguments_split_into_tags_at_each_word_that_starts_with_a_dash(typed, tags):
    assert launchargs.split_tags(typed) == tags


def test_tags_become_the_words_after_the_executable_and_a_quote_left_open_is_an_error():
    assert launchargs.words(["-res 1280 720", "-title 'My Game'", "--nosound"]) == ["-res", "1280", "720", "-title", "My Game", "--nosound"]
    with pytest.raises(ValueError):
        launchargs.words(["-x 'oops"])


def test_what_is_kept_as_tags_whatever_it_was():
    assert launchargs.as_tags(["-a", "-a", "", " -b", "-c"]) == ["-a", "-c"]
    assert launchargs.as_tags("-a -b 1") == ["-a", "-b 1"]
    assert launchargs.as_tags(None) == [] and launchargs.as_tags(3) == []
    assert len(launchargs.as_tags([f"-t{n}" for n in range(80)])) == launchargs.MAX_TAGS


@pytest.mark.parametrize(
    ("base", "ours", "theirs", "merged"),
    [
        (["-a"], ["-a"], ["-a"], ["-a"]),
        (["-a"], ["-a", "-c"], ["-a"], ["-a", "-c"]),  # added here
        (["-a"], ["-a"], ["-a", "-d"], ["-a", "-d"]),  # added there
        (["-a", "-b"], ["-a", "-b", "-c"], ["-a", "-d"], ["-a", "-d", "-c"]),  # one adds, the other adds and removes
        (["-a", "-b"], ["-b"], ["-a", "-b"], ["-b"]),  # removed here
        (["-a", "-b"], ["-a", "-b"], ["-a"], ["-a"]),  # removed there
        (["-a"], ["-a", "-x"], ["-a", "-x"], ["-a", "-x"]),  # both added the same: once
        ([], ["-mine"], [], ["-mine"]),
        (["-a"], ["-a"], [], []),  # all deleted there
    ],
)
def test_two_editors_are_merged_by_tag(base, ours, theirs, merged):
    assert launchargs.merge(base, ours, theirs) == merged


def test_a_variable_among_the_tags_is_set_before_the_game_and_not_passed_to_it():
    tags = ["-res 1280 720", "WINEDLLOVERRIDES=dinput8=n,b", "'FOO=a b'", "MANGOHUD=1", "-w", "MANGOHUD=0"]
    assert launchargs.env(tags) == {"WINEDLLOVERRIDES": "dinput8=n,b", "FOO": "a b", "MANGOHUD": "0"}
    assert launchargs.words(tags) == ["-res", "1280", "720", "-w"]
    assert launchargs.env(["-x NAME=1", "-title 'x"]) == {} and launchargs.words(["-x NAME=1"]) == ["-x", "NAME=1"]


def _game(tmp_path, tags):
    from mog_client.config import InstalledGame

    return InstalledGame(
        game_id=7, name="Game", install_dir=str(tmp_path / "Game"), executable=str(tmp_path / "Game" / "game.exe"), launch_args=tags
    )


@pytest.mark.parametrize("engine", ["umu", "wine", "proton"])
def test_the_variables_reach_the_game_through_each_engine_and_its_arguments_do_not_carry_them(engine, monkeypatch, tmp_path):
    from mog_client import launcher

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": engine)
    monkeypatch.setattr(launcher, "find_proton", lambda: tmp_path / "steamapps" / "Proton" / "proton")
    cmd, env = launcher.launch_command(_game(tmp_path, ["-w", "WINEDLLOVERRIDES=dinput8=n,b"]))
    assert env["WINEDLLOVERRIDES"] == "dinput8=n,b" and "WINEDLLOVERRIDES=dinput8=n,b" not in cmd and "-w" in cmd


def test_faugus_gets_the_variables_in_front_of_the_command_it_runs(monkeypatch, tmp_path):
    from mog_client import launcher

    monkeypatch.setattr(launcher, "detect_launcher", lambda pref="auto": "faugus")
    monkeypatch.setattr(launcher, "faugus_invocation", lambda: (["faugus-run"], str(tmp_path / "umu-run"), False))
    cmd, env = launcher.launch_command(_game(tmp_path, ["WINEDLLOVERRIDES=dinput8=n,b", "-w"]), require_umu=False)
    assert "WINEDLLOVERRIDES=dinput8=n,b " in cmd[-1] and cmd[-1].endswith("-w") and "WINEDLLOVERRIDES" not in env
