<p align="center"><img src="docs/logo.png" width="220" alt="MOG Client" /></p>

# MOG Client

CLI client for [MOG-Server](https://github.com/MOG-My-Own-Games/MOG-Server).
Part of [MOG - My Own Games](https://github.com/MOG-My-Own-Games).

The CLI is pure stdlib Python. The GUI (Qt, controller friendly, Steam Deck
ready) needs the `gui` extra.

## What it does

- Lists the games on your MOG-Server, installs them (resumable, streamed while the server installs) and keeps them up to date
  with a repair check.
- Makes a desktop entry and a Steam shortcut for each game, with the server's artwork. They run the game straight through
  Faugus Launcher, umu, Proton or Wine (or directly on Windows) and keep working without the client.
- Backs your saves up to the server and brings them to your other machines.
- Fully usable with a controller, on a Steam Deck included.

## Install

```bash
pip install -e '.[gui]'   # or plain `pip install -e .` for the CLI only
```

Or take the AppImage (Linux) or the exe (Windows) from the releases page.

## GUI

```bash
mog
```

The first start walks you through the server, where games go, saves and Steam.

- [Using the client](docs/GUI.md): library, a game's page and Options, window, controller, Settings, logs.
- [Installing games](docs/INSTALLING.md): the install flow, install folders, archives without an installer, Linux games,
  installing from the web UI.
- [Save sync](docs/SAVES.md): what is backed up, when, and what the logs say.
- [Steam](docs/STEAM.md): accounts, shortcuts and the first-start guide.

## CLI

```bash
mog --base http://localhost:5000 --user admin --pass <password> \
    --game-id 1 --out ./my-game
```

Starts (or resumes) the install for game 1 on that server and streams the result into `./my-game` as it's written, with no
need to wait for the install to finish. Run `mog --help` for every option (`--auto-mode`, `--cancel`, `--clear`,
`--extract-only`, ...).

## Releases

Every push to `main` runs `.github/workflows/release.yml`. It computes the
version with `scripts/version.py` (from conventional commits and the branch name), builds
a Linux AppImage and a Windows exe and attaches them (plus `SHA256SUMS.txt`)
to a **draft** release tagged `v<version>`. The version is embedded in the
build (shown in Settings) but not in the file names. Nothing goes public until
you open the draft on GitHub, check it and click "Publish release".

## Updates

AppImage and exe builds check the latest published release at startup and
offer to update (Settings has a "Check for updates at startup" flag, on by default, and a "Check now" button): the new file is downloaded, verified against the release's
`SHA256SUMS.txt`, swapped in for the running one and the app restarts. Set `MOG_NO_UPDATE_CHECK=1` to turn the check
off.

Self-update is a build flag: the build scripts write `UPDATE_METHOD`
(`appimage` or `exe`) into the build, and `MOG_UPDATE_METHOD=none` builds
without it, which is what a Flatpak or a distro package should use. Dev runs and
pip installs never self-update.

## Build it yourself

The same scripts the workflow uses. Output goes to `dist/`, scratch files to
`build/` (both git-ignored). The version defaults to the one in
`pyproject.toml`; pass another to override.

**Linux (AppImage)**, needs `python3` with `venv` and `curl`:

```bash
scripts/build-appimage.sh            # dist/MOG-Client-x86_64.AppImage
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

This produces `dist\MOG-Client.exe`, a windowed single file: the GUI
opens on double click. Its CLI flags work too, but output is not shown in a
console; use `pip install -e .` and `mog` when you need the CLI's output.

## Troubleshooting

- The Logs tab in Settings (also `logs/client.log` in the client's data folder, `~/.local/share/mog-client` on Linux) shows
  what the client did.
- If the window freezes or the client crashes, `logs/crash.log` in the same folder holds the stack of every thread. Attach it
  to a bug report.

## License

GPL-3.0-or-later. See `LICENSE`.
