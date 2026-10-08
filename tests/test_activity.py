from mog_client import activity


def test_what_was_running_is_kept_and_forgotten_when_it_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "data_dir", lambda: tmp_path)
    assert activity.load() == {"installs": [], "mods": []}

    activity.add_install(7)
    activity.add_install(7)  # once
    activity.add_install(9)
    activity.add_mod(7, {"name": "mod1", "kind": "folder"})
    activity.add_mod(7, {"name": "mod1", "kind": "folder"})
    assert activity.load() == {"installs": [7, 9], "mods": [{"game_id": 7, "mod": {"name": "mod1", "kind": "folder"}}]}

    activity.remove_install(7)
    activity.remove_mod(7, "mod1")
    activity.remove_mod(7, "other")  # not there: nothing happens
    assert activity.load() == {"installs": [9], "mods": []}


def test_a_damaged_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "data_dir", lambda: tmp_path)
    (tmp_path / "active.json").write_text("{nope")
    assert activity.load() == {"installs": [], "mods": []}
    (tmp_path / "active.json").write_text('{"installs": ["x", 3], "mods": [{"game_id": "a"}, 5]}')
    assert activity.load() == {"installs": [3], "mods": []}
