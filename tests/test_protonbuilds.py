import pytest

from mog_client import launcher, protonbuilds
from mog_client.config import InstalledGame


def _steam(monkeypatch, tmp_path):
    root = tmp_path / "steam"
    for rel in ("steamapps/common/Proton 9.0", "compatibilitytools.d/GE-Proton7-17", "compatibilitytools.d/GE-Proton9-27"):
        (root / rel).mkdir(parents=True)
        (root / rel / "proton").write_text("x" * 10)
    monkeypatch.setattr(launcher, "steam_roots", lambda: [root])
    return root


def _game(gid, name, proton):
    return InstalledGame(game_id=gid, name=name, install_dir="/x", executable="/x/a.exe", proton=proton)


def test_only_what_sits_in_compatibilitytools_can_be_removed(monkeypatch, tmp_path):
    _steam(monkeypatch, tmp_path)

    found = {b.name: b.removable for b in protonbuilds.installed()}

    assert found == {"GE-Proton7-17": True, "GE-Proton9-27": True, "Proton 9.0": False}


def test_the_games_using_a_build_are_listed_by_name(monkeypatch, tmp_path):
    games = [_game(1, "zeta", "GE-Proton7-17"), _game(2, "Alpha", "GE-Proton7-17"), _game(3, "Other", "GE-Proton9-27"), _game(4, "None", None)]

    assert [g.name for g in protonbuilds.used_by("GE-Proton7-17", games)] == ["Alpha", "zeta"]
    assert protonbuilds.used_by("GE-Proton8-1", games) == []


def test_removing_a_build_deletes_its_folder_only(monkeypatch, tmp_path):
    root = _steam(monkeypatch, tmp_path)
    build = next(b for b in protonbuilds.installed() if b.name == "GE-Proton7-17")

    assert protonbuilds.size(build) == 10
    protonbuilds.remove(build)

    assert not (root / "compatibilitytools.d/GE-Proton7-17").exists()
    assert (root / "compatibilitytools.d/GE-Proton9-27/proton").is_file()


def test_a_build_of_steams_own_is_never_removed(monkeypatch, tmp_path):
    root = _steam(monkeypatch, tmp_path)
    build = next(b for b in protonbuilds.installed() if b.name == "Proton 9.0")

    with pytest.raises(RuntimeError, match="Steam"):
        protonbuilds.remove(build)
    assert (root / "steamapps/common/Proton 9.0/proton").is_file()


def test_a_linked_build_loses_the_link_and_not_what_it_points_to(monkeypatch, tmp_path):
    root = _steam(monkeypatch, tmp_path)
    real = tmp_path / "elsewhere" / "GE-Proton8-5"
    real.mkdir(parents=True)
    (real / "proton").write_text("x")
    (root / "compatibilitytools.d/GE-Proton8-5").symlink_to(real)
    build = next(b for b in protonbuilds.installed() if b.name == "GE-Proton8-5")

    protonbuilds.remove(build)

    assert not (root / "compatibilitytools.d/GE-Proton8-5").exists() and (real / "proton").is_file()
