# MOG Client

CLI client for [MOG-Server](https://github.com/MOG-My-Own-Games/MOG-Server).
Part of [MOG - My Own Games](https://github.com/MOG-My-Own-Games).

The CLI is pure stdlib Python. The GUI (Qt, controller friendly, Steam Deck
ready) needs the `gui` extra.

## Install

```bash
pip install -e '.[gui]'   # or plain `pip install -e .` for the CLI only
```

## GUI

```bash
mog
```

Lists the server's games, installs/resumes/streams them, asks which
executable to run once the install finishes, then creates a desktop entry and
a Steam shortcut (with the server's scraped artwork). Games launch through
Faugus Launcher, umu or wine on Linux and directly on Windows. Fully usable
with a gamepad (D-pad/stick, A, B, Start for settings).

## CLI

```bash
mog --base http://localhost:5000 --user admin --pass <password> \
    --game-id 1 --out ./my-game
```

Starts (or resumes) the install for game 1 on that server and streams the
result into `./my-game` as it's written - no need to wait for the install to
finish before files start arriving. Run `mog --help` for every option
(`--auto-mode`, `--cancel`, `--clear`, ...).

## Releases

Pushing a tag like `v0.2.0` runs `.github/workflows/release.yml`, which builds
a Linux AppImage and a Windows exe and attaches them (plus `SHA256SUMS.txt`)
to a **draft** release. Nothing goes public until you open the draft on
GitHub, check it and click "Publish release".

```bash
git tag v0.2.0 && git push origin v0.2.0
```

The workflow can also be started by hand from the Actions tab
(`Run workflow`, giving the tag name).

## Build it yourself

The same scripts the workflow uses. Output goes to `dist/`, scratch files to
`build/` (both git-ignored). The version defaults to the one in
`pyproject.toml`; pass another to override.

**Linux (AppImage)**, needs `python3` with `venv` and `curl`:

```bash
scripts/build-appimage.sh            # dist/MOG-Client-<version>-x86_64.AppImage
scripts/build-appimage.sh 1.2.3
```

Build on the oldest distro you want to support: the AppImage bundles Python
and Qt but uses the host's glibc. On the target machine Qt may also need
`libxcb-cursor0` (Debian/Ubuntu) or `xcb-util-cursor` (Fedora/Arch).

**Windows (exe)**, needs Python 3.10+ on `PATH`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1 -Version 1.2.3
```

This produces `dist\MOG-Client-<version>.exe`, a windowed single file: the GUI
opens on double click. Its CLI flags work too, but output is not shown in a
console; use `pip install -e .` and `mog` when you need the CLI's output.

## License

GPL-3.0-or-later. See `LICENSE`.
