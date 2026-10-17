import hashlib
import io
import tarfile
from types import SimpleNamespace

import pytest

from mog_client import protondl

NAME = "GE-Proton9-27"


def _archive(members: dict[str, bytes | tuple[str, str]]) -> bytes:
    """A .tar.gz; a value that is a (kind, target) tuple is a link."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in members.items():
            info = tarfile.TarInfo(path)
            if isinstance(data, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, data[1]
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Web:
    """The release's two files, served; `calls` is what was asked for."""

    def __init__(self, archive: bytes, checksum: str | None = None, name: str = NAME):
        digest = checksum or hashlib.sha512(archive).hexdigest()
        self.files = {
            f"{protondl.RELEASES}/{name}/{name}.tar.gz": archive,
            f"{protondl.RELEASES}/{name}/{name}.sha512sum": f"{digest}  {name}.tar.gz\n".encode(),
        }
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.files:
            raise OSError("404")
        data = self.files[url]
        stream = io.BytesIO(data)
        stream.headers = {"Content-Length": str(len(data))}
        return stream


GOOD = {f"{NAME}/proton": b"#!/bin/sh\n", f"{NAME}/files/bin/wine": b"x", f"{NAME}/files/lib": ("sym", "bin")}


def test_a_release_is_downloaded_checked_and_put_where_the_launchers_look(tmp_path):
    seen = []

    target = protondl.install(NAME, tmp_path / "tools", Web(_archive(GOOD)), lambda done, total: seen.append((done, total)))

    assert target == tmp_path / "tools" / NAME and (target / "proton").is_file() and (target / "files/lib").is_symlink()
    assert seen and seen[-1][0] == seen[-1][1]
    assert [p.name for p in (tmp_path / "tools").iterdir()] == [NAME]  # no scratch folder left behind


def test_a_build_already_there_is_not_downloaded_again(tmp_path):
    (tmp_path / NAME).mkdir()
    (tmp_path / NAME / "proton").write_text("")
    web = Web(b"")

    assert protondl.install(NAME, tmp_path, web) == tmp_path / NAME and web.calls == []


def test_a_download_whose_checksum_differs_is_refused_and_leaves_nothing(tmp_path):
    with pytest.raises(RuntimeError, match="damaged"):
        protondl.install(NAME, tmp_path / "tools", Web(_archive(GOOD), checksum="0" * 128))

    assert list((tmp_path / "tools").iterdir()) == []


@pytest.mark.parametrize(
    "members",
    [
        {"../evil": b"x", f"{NAME}/proton": b""},
        {"/etc/evil": b"x", f"{NAME}/proton": b""},
        {f"{NAME}/proton": b"", f"{NAME}/out": ("sym", "../../../../etc")},
    ],
)
def test_an_archive_that_reaches_outside_its_folder_is_refused(tmp_path, members):
    with pytest.raises(RuntimeError, match="unsafe"):
        protondl.install(NAME, tmp_path / "tools", Web(_archive(members)))

    assert not (tmp_path / "evil").exists() and not (tmp_path / "tools" / NAME).exists()


def test_an_archive_without_a_proton_script_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="proton script"):
        protondl.install(NAME, tmp_path / "tools", Web(_archive({f"{NAME}/readme": b"x"})))


def test_offline_or_a_missing_release_says_so(tmp_path):
    with pytest.raises(RuntimeError, match="could not fetch"):
        protondl.install("GE-Proton1-1", tmp_path, Web(b""))


@pytest.mark.parametrize("name", ["Proton 9.0", "GE-Proton", "../GE-Proton9-27", "GE-Proton9-27/x", ""])
def test_only_release_names_are_taken(tmp_path, name):
    assert not protondl.is_ge(name) and not protondl.is_fetchable(name)
    with pytest.raises(RuntimeError, match="not a Proton-GE or UMU-Proton"):
        protondl.install(name, tmp_path, Web(b""))


def test_the_checksum_is_read_for_the_archive_by_name():
    text = f"{'a' * 128}  other.tar.gz\n{'b' * 128} *{NAME}.tar.gz\n"

    assert protondl.expected_hash(text, f"{NAME}.tar.gz") == "b" * 128
    assert protondl.expected_hash("not a checksum", f"{NAME}.tar.gz") is None


@pytest.mark.parametrize("name", ["UMU-Proton-10.0-4", "UMU-Proton-9.0-2", "UMU-Proton-9.0-3.2", "UMU-Proton-9.0-4e"])
def test_umu_proton_releases_are_fetched_from_their_own_repository(tmp_path, name):
    web = Web(_archive({f"{name}/proton": b"#!/bin/sh\n", f"{name}/files/bin/wine": b"x"}), name=name)
    web.files = {url.replace(protondl.RELEASES, protondl.UMU_RELEASES): data for url, data in web.files.items()}

    target = protondl.install(name, tmp_path / "tools", web)

    assert protondl.is_umu(name) and not protondl.is_ge(name) and protondl.is_fetchable(name)
    assert (target / "proton").is_file() and web.calls[0] == f"{protondl.UMU_RELEASES}/{name}/{name}.sha512sum"


@pytest.mark.parametrize("name", ["UMU-Proton", "UMU-Proton-9", "UMU-Proton-9.0", "umu-proton-9.0-2", "UMU-Proton-9.0-2/x", "UMU-Proton-9.0-2e9"])
def test_other_umu_names_are_not_taken(name):
    assert not protondl.is_umu(name)
