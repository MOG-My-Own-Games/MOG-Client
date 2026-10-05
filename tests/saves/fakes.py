"""A stand-in for the server's saves API, shared by the sync and GUI-flow tests."""

import zipfile
from pathlib import Path


class FakeServer:
    """The server's saves API, holding uploaded archives in memory."""

    def __init__(self, own_device=1):
        self.versions: list[dict] = []
        self.blobs: dict[int, bytes] = {}
        self.devices = {1: {"id": 1, "name": "karasu"}, 2: {"id": 2, "name": "karasu-2"}}
        self.uploads = []

    def upload_save(self, game_id, device_id, archive, trigger):
        data = Path(archive).read_bytes()
        keys = sorted(zipfile.ZipFile(archive).namelist())
        version = {
            "id": len(self.versions) + 1,
            "device_id": device_id,
            "trigger": trigger,
            "created_at": f"2026-10-04T10:00:{len(self.versions):02d}Z",
        }
        self.versions.append(version)
        self.blobs[version["id"]] = data
        self.uploads.append((trigger, keys))
        return {"version": version, "created": True}

    def list_saves(self, game_id):
        by_device = {}
        for v in self.versions:
            by_device.setdefault(v["device_id"], []).append(v)
        return {
            "devices": [{"device": self.devices[d], "versions": list(reversed(vs))} for d, vs in by_device.items()],
            "keep_versions": 3,
        }

    def download_save(self, version_id, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.blobs[version_id])

    def add_foreign_version(self, files: dict[str, bytes], device_id=2):
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for key, data in files.items():
                z.writestr(key, data)
        version = {"id": len(self.versions) + 1, "device_id": device_id, "created_at": f"2026-10-05T10:00:{len(self.versions):02d}Z"}
        self.versions.append(version)
        self.blobs[version["id"]] = buf.getvalue()
        return version
