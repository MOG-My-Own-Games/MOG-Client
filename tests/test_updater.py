import hashlib
from pathlib import Path

import pytest

from mog_client import updater
from mog_client.updater import UpdateError, UpdateInfo, check_for_update, is_newer


def _release(tag: str, assets: dict[str, str]) -> dict:
    return {"tag_name": tag, "html_url": "https://example/r", "assets": [
        {"name": n, "browser_download_url": u} for n, u in assets.items()
    ]}


def test_is_newer_compares_numerically():
    assert is_newer("v0.10.0", "0.9.5")
    assert not is_newer("v0.9.5", "0.9.5")
    assert not is_newer("v0.9.5", "dev")


def test_picks_asset_for_the_build_method():
    release = _release("v1.0.0", {"MOG-Client.exe": "u1", "MOG-Client-x86_64.AppImage": "u2", "SHA256SUMS.txt": "u3"})
    assert check_for_update(lambda _: release, "0.1.0", "appimage").asset_url == "u2"
    assert check_for_update(lambda _: release, "0.1.0", "exe").asset_url == "u1"


def test_no_update_when_up_to_date_or_flag_off():
    release = _release("v1.0.0", {"MOG-Client.exe": "u"})
    assert check_for_update(lambda _: release, "1.0.0", "exe") is None
    assert check_for_update(lambda _: release, "0.1.0", "none") is None


class _Resp:
    def __init__(self, data: bytes):
        self.data, self.headers = data, {"Content-Length": str(len(data))}

    def read(self, n=-1):
        out, self.data = self.data[:n], self.data[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, sums: str, payload: bytes):
    monkeypatch.setattr(updater, "_open", lambda url: _Resp(sums.encode() if "sums" in url else payload))


def test_download_verifies_checksum(monkeypatch, tmp_path: Path):
    payload = b"new build"
    info = UpdateInfo("1.0.0", "MOG-Client.exe", "u/asset", "u/sums", "")
    _serve(monkeypatch, f"{hashlib.sha256(payload).hexdigest()}  MOG-Client.exe\n", payload)
    dest = tmp_path / "x"
    updater.download(info, dest)
    assert dest.read_bytes() == payload


def test_download_rejects_bad_checksum_and_removes_file(monkeypatch, tmp_path: Path):
    info = UpdateInfo("1.0.0", "MOG-Client.exe", "u/asset", "u/sums", "")
    _serve(monkeypatch, f"{'0' * 64}  MOG-Client.exe\n", b"tampered")
    dest = tmp_path / "x"
    with pytest.raises(UpdateError):
        updater.download(info, dest)
    assert not dest.exists()


def test_download_refuses_without_checksums(tmp_path: Path):
    with pytest.raises(UpdateError):
        updater.download(UpdateInfo("1.0.0", "a", "u", None, ""), tmp_path / "x")


def test_appimage_update_replaces_the_file(monkeypatch, tmp_path: Path):
    target = tmp_path / "MOG.AppImage"
    target.write_bytes(b"old")
    payload = b"new"
    info = UpdateInfo("1.0.0", "MOG-Client-x86_64.AppImage", "u/asset", "u/sums", "")
    _serve(monkeypatch, f"{hashlib.sha256(payload).hexdigest()}  {info.asset_name}\n", payload)
    monkeypatch.setattr(updater, "UPDATE_METHOD", "appimage")
    monkeypatch.setenv("APPIMAGE", str(target))
    assert updater.apply_update(info) == target
    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o111
