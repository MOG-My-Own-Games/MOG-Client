"""Docker plumbing for the end-to-end test: images, the server container, the two client containers, and the calls the
test makes to the server's API as an admin would. Stdlib only, like the client."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import shlex
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

E2E_DIR = Path(__file__).resolve().parent
CLIENT_SRC = E2E_DIR.parents[1]

# The game: Freeciv (GPL-2.0-or-later), its Windows NSIS installer as published by the project. A real wizard (language,
# licence, components, folder, finish), so the server's auto mode has to read and press its way through it.
FREECIV_URL = "https://files.freeciv.org/packages/windows/Freeciv-3.2.6-msys2-win64-gtk3.22-setup.exe"
FREECIV_SHA256 = "3a06c7c9a083a34140b561de69ae705b46d744b01b721d05e159789d8486787a"
GAME_FOLDER = "Freeciv"  # the library entry, so the game's name on the server
GAME_EXE = "freeciv-server.exe"  # a console program: it plays an AI game from a script and saves, with no window

# What the server installs with: its default build, Proton-CachyOS (`cachyos-latest`), pinned and fetched once into the
# cache, so the installer runs as on a real server (a 64-bit prefix: the image's own Wine is 32-bit only).
PROTON_URL = "https://github.com/CachyOS/proton-cachyos/releases/download/cachyos-11.0-20261005-slr/proton-cachyos-11.0-20261005-slr-x86_64.tar.xz"
PROTON_SHA256 = "096bfe73b506d6565b04ecc45214197a4091818f16ed91f5324b4d2082d0a263"
PROTON_BUILD = "cachyos-latest"

CLIENT_IMAGE = "mog-e2e-client"
SERVER_IMAGE = "mog-server:e2e"
ADMIN = "admin"
# The Windows Python of the client image (see client.Dockerfile), and each client's data folder (config.data_dir()).
WIN_PYTHON = "/opt/winpython/python.exe"
LINUX_DATA = "/root/.local/share/mog-client"
WINDOWS_DATA = "/root/.wine/drive_c/users/root/AppData/Local/mog-client"


def cache_dir() -> Path:
    """Downloads and run folders. They must be visible to the Docker daemon at the same path (bind mounts), which a
    sandboxed shell's /tmp may not be: the home folder is."""
    return Path(os.environ.get("MOG_E2E_CACHE") or Path.home() / ".cache" / "mog-e2e")


def docker_command() -> list[str]:
    if os.environ.get("MOG_E2E_DOCKER"):
        return shlex.split(os.environ["MOG_E2E_DOCKER"])
    if shutil.which("docker"):
        return ["docker"]
    if Path("/.flatpak-info").exists() and shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", "docker"]  # an editor in a Flatpak sandbox reaches the host's Docker this way
    return []


class Docker:
    def __init__(self, command: list[str]):
        self.command = command

    def __call__(self, *args: str, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run([*self.command, *args], capture_output=True, text=True, timeout=timeout, check=False)
        if check and proc.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args[:3])}... failed ({proc.returncode}):\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}")
        return proc

    def build(self, tag: str, dockerfile: Path, context: Path, *extra: str) -> None:
        # The build network is the host's by default: on some hosts Docker's bridge has no way out, and apt needs one.
        network = os.environ.get("MOG_E2E_BUILD_NETWORK", "host")
        self("build", f"--network={network}", "-t", tag, "-f", str(dockerfile), *extra, str(context), timeout=3600)

    def image_exists(self, tag: str) -> bool:
        return self("image", "inspect", tag, check=False).returncode == 0


def fetch(url: str, sha256: str, dest: Path) -> Path:
    """A download kept in the cache, checked against its known hash (again each time it is used)."""
    if not dest.is_file() or _sha256(dest) != sha256:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:
            shutil.copyfileobj(resp, out)
        if _sha256(part) != sha256:
            part.unlink()
            raise RuntimeError(f"{url} does not have the expected SHA-256")
        part.replace(dest)
    return dest


def prepare_proton(builds: Path) -> Path:
    """The server's default Proton build, unpacked as the server unpacks a download (`<builds>/<id>/proton`, the release's
    top folder dropped). Kept between runs."""
    dest = builds / PROTON_BUILD
    if (dest / "proton").is_file() and (dest / "files" / "bin" / "wine").exists():
        return dest
    archive = fetch(PROTON_URL, PROTON_SHA256, cache_dir() / "assets" / PROTON_URL.rsplit("/", 1)[1])
    tmp = builds / f".extract-{PROTON_BUILD}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(archive, "r:xz") as tar:
        members = []
        for member in tar.getmembers():
            parts = member.name.split("/", 1)
            if len(parts) == 2 and parts[1]:
                member.name = parts[1]
                members.append(member)
        tar.extractall(tmp, members=members, filter="data")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


class Api:
    """The server's API as the admin, from the machine running the test."""

    def __init__(self, base: str, password: str):
        self.base = base
        self.auth = "Basic " + base64.b64encode(f"{ADMIN}:{password}".encode()).decode()

    def request(self, method: str, path: str, body=None, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Authorization": self.auth})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                return resp.status, payload if raw else (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, payload.decode(errors="replace")

    def get(self, path: str):
        status, data = self.request("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path}: HTTP {status}: {data}")
        return data

    def save_archive(self, version_id: int) -> dict[str, bytes]:
        """A save version's files by key, as the client put them in the archive."""
        status, payload = self.request("GET", f"/api/saves/{version_id}/download", raw=True)
        if status != 200:
            raise RuntimeError(f"download of save version {version_id}: HTTP {status}")
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            return {name: z.read(name) for name in z.namelist() if not name.endswith("/")}


@dataclass
class ClientBox:
    """One client container: `linux` runs the client natively, `windows` under Wine with Windows' Python."""

    docker: Docker
    name: str
    role: str
    transcript: list[str] = field(default_factory=list)

    def python(self) -> list[str]:
        return ["wine", WIN_PYTHON] if self.role == "windows" else ["python3"]

    def run(self, argv: list[str], timeout: float, check: bool = True) -> subprocess.CompletedProcess:
        began = time.monotonic()
        proc = self.docker("exec", self.name, *argv, check=False, timeout=timeout)
        self.transcript.append(
            f"$ {shlex.join(argv)}  [{proc.returncode}, {time.monotonic() - began:.0f}s]\n{proc.stdout}{proc.stderr}"
        )
        if check and proc.returncode != 0:
            raise AssertionError(f"{self.role}: {shlex.join(argv)} exited with {proc.returncode}:\n{proc.stdout[-6000:]}\n{proc.stderr[-6000:]}")
        return proc

    def driver(self, *args: str, timeout: float = 600) -> dict:
        script = "Z:/e2e/driver.py" if self.role == "windows" else "/e2e/driver.py"
        proc = self.run([*self.python(), script, *args], timeout)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
        if not lines:
            raise AssertionError(f"{self.role}: driver {args[0]} printed no result:\n{proc.stdout[-6000:]}\n{proc.stderr[-6000:]}")
        return json.loads(lines[-1][len("RESULT ") :])

    def mog(self, *args: str, timeout: float = 600, check: bool = True) -> subprocess.CompletedProcess:
        """MOG Client's own command line, as a game's shortcut runs it."""
        return self.run([*self.python(), "-m", "mog_client", *args], timeout, check)

    def read(self, path: str) -> str:
        proc = self.docker("exec", self.name, "cat", path, check=False)
        return proc.stdout if proc.returncode == 0 else f"({path}: not readable)"

    def logs(self) -> str:
        data = (WINDOWS_DATA if self.role == "windows" else LINUX_DATA) + "/logs"
        listing = self.docker("exec", self.name, "sh", "-c", f"for f in {data}/*; do echo \"== $f\"; tail -n 200 \"$f\"; done", check=False)
        return "\n".join(self.transcript) + "\n\n" + listing.stdout


class Stack:
    """The server and both clients on a network of their own, named after the run so runs do not meet."""

    def __init__(self, docker: Docker, run_dir: Path):
        self.docker = docker
        self.run_dir = run_dir
        self.tag = "mog-e2e-" + secrets.token_hex(3)
        self.network = self.tag
        self.server = f"{self.tag}-server"
        self.password = "e2e-" + secrets.token_urlsafe(12)
        self.api: Api | None = None
        self.clients: dict[str, ClientBox] = {}
        self.failed = False
        self.mounts: dict[str, str] = {}  # container -> the folder of the run folder it writes into

    @property
    def internal_base(self) -> str:
        return f"http://{self.server}:5000"

    def start_server(self, image: str, library: Path, source: Path | None = None) -> Api:
        """The server, its data (database, install caches, saves) in the run folder. With `source`, a MOG-Server checkout's
        backend and web UI are mounted over the image's, so an existing image runs the code checked out."""
        self.docker("network", "create", self.network)
        data = self.run_dir / "server"
        data.mkdir(parents=True, exist_ok=True)
        builds = cache_dir() / "proton"
        prepare_proton(builds)
        mounts = ["-v", f"{library}:/library:ro", "-v", f"{data}:/mog", "-v", f"{builds}:/opt/proton", "--tmpfs", "/tmp:mode=1777"]
        if source is not None:
            mounts += ["-v", f"{source / 'backend'}:/src/backend:ro", "-v", f"{source / 'frontend'}:/src/frontend:ro"]
        self.docker(
            "run", "-d", "--name", self.server, "--network", self.network, "--privileged", "--init",
            "-p", "127.0.0.1::5000",
            "-e", f"MOG_ADMIN_USERNAME={ADMIN}", "-e", f"MOG_ADMIN_PASSWORD={self.password}",
            "-e", "LOGLEVEL=DEBUG", "-e", "LUDUSAVI_ENABLED=false", "-e", "HLTB_ENABLED=false",
            "-e", "INSTALL_AUTO_MODE_DEFAULT=true", "-e", f"INSTALL_DEFAULT_PROTON_BUILD={PROTON_BUILD}",
            *mounts,
            image,
        )  # fmt: skip
        self.mounts[self.server] = "/mog /opt/proton"
        # Xvfb wants its socket folder root's with the sticky bit, as the image makes it; the tmpfs hid that one.
        self.docker("exec", "-u", "root", self.server, "sh", "-c", "mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix")
        port = self.docker("port", self.server, "5000/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        self.api = Api(f"http://127.0.0.1:{port}", self.password)
        deadline = time.monotonic() + 180
        while True:
            try:
                status, _ = self.api.request("GET", "/api/libraries")
                if status == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("the server did not answer in 3 minutes:\n" + self.server_logs())
            time.sleep(2)
        return self.api

    def start_client(self, role: str) -> ClientBox:
        """A client container. Its data folder (games, the record of them, the save sync's state, logs) is in the run
        folder: the games are big, and the test reads what is there after a failure."""
        name = f"{self.tag}-{role}"
        data = self.run_dir / role
        data.mkdir(parents=True, exist_ok=True)
        inside = WINDOWS_DATA if role == "windows" else LINUX_DATA
        self.docker(
            "run", "-d", "--name", name, "--hostname", f"e2e-{role}", "--network", self.network,
            "-v", f"{CLIENT_SRC}:/src:ro", "-v", f"{E2E_DIR}:/e2e:ro", "-v", f"{data}:{inside}",
            CLIENT_IMAGE,
        )  # fmt: skip
        self.mounts[name] = inside
        box = ClientBox(self.docker, name, role)
        self.clients[role] = box
        return box

    def server_logs(self) -> str:
        proc = self.docker("logs", "--tail", "400", self.server, check=False)
        return proc.stdout + proc.stderr

    def keep_diagnostics(self) -> Path:
        out = self.run_dir / "diagnostics"
        out.mkdir(parents=True, exist_ok=True)
        (out / "server.log").write_text(self.server_logs())
        for role, box in self.clients.items():
            (out / f"client-{role}.log").write_text(box.logs())
        if self.api is not None:
            try:
                (out / "install-active.json").write_text(json.dumps(self.api.get("/api/games/install/active"), indent=2))
            except (RuntimeError, OSError) as e:
                (out / "install-active.json").write_text(str(e))
        return out

    def down(self) -> None:
        # The containers write as root (or the server's own user): give the run folder back to whoever runs the test.
        owner = f"{os.getuid()}:{os.getgid()}"
        for name, inside in self.mounts.items():
            self.docker("exec", "-u", "root", name, "chown", "-R", owner, *inside.split(), check=False)
        for name in [*(b.name for b in self.clients.values()), self.server]:
            self.docker("rm", "-f", name, check=False)
        self.docker("network", "rm", self.network, check=False)
