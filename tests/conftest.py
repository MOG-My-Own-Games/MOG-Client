import sys

import pytest


@pytest.fixture(autouse=True)
def _keep_the_users_data_untouched(monkeypatch, tmp_path):
    """Tests write logs and launch scripts: into a temp folder, never the real data directory."""
    from mog_client import config, launcher, played, snapshot, steam, trace
    from mog_client.saves import devices, state

    monkeypatch.setattr(launcher, "_known", None)  # the saved launcher scan, if a test loaded one
    monkeypatch.setattr(steam, "steam_running", lambda: False)  # not whether the developer has Steam open
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")  # installed.json
    monkeypatch.setattr(trace, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(state, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(played, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(snapshot, "data_dir", lambda: tmp_path / "data")
    window_module = sys.modules.get("mog_client.gui.app")  # covers, icons and art are cached under the data folder
    if window_module is not None:
        monkeypatch.setattr(window_module, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(devices, "config_dir", lambda: tmp_path / "config")
