import pytest


@pytest.fixture(autouse=True)
def _keep_the_users_data_untouched(monkeypatch, tmp_path):
    """Tests write logs and launch scripts: into a temp folder, never the real data directory."""
    from mog_client import launcher, steam, trace
    from mog_client.saves import devices, state

    monkeypatch.setattr(launcher, "_known", None)  # the saved launcher scan, if a test loaded one
    monkeypatch.setattr(steam, "steam_running", lambda: False)  # not whether the developer has Steam open
    monkeypatch.setattr(trace, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(launcher, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(state, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(devices, "config_dir", lambda: tmp_path / "config")
