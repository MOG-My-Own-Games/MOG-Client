<p align="center"><img src="mog_client/gui/assets/mascotte.png" width="200" alt="MOG" /></p>

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
with a gamepad (D-pad/stick, A, B, Start for settings, Start+Select to quit). Games the server matched to the same title show as one entry ("N versions"); installing one lists the installers of every version, each under its version's name, and you pick which to run. The library has a sidebar on the left (library filter and active installs) that stays put while the games scroll; L2 shows or hides it. A legend at the bottom lists the controls: the controller's buttons while one is connected, the keyboard shortcuts (F5, Ctrl+F, Ctrl+B, Ctrl+N, Ctrl+, , Ctrl+Q) otherwise. The pad legend uses Xbox, PlayStation, Nintendo or Steam Deck glyphs depending on the pad (icons: Kenney Input Prompts, CC0). On a Steam Deck, text fields open an in-app on-screen keyboard (tap a field, or press A on it). `MOG_OSK=steam` uses Steam's own keyboard instead, `MOG_OSK=0` turns it off and `MOG_OSK=builtin` forces the in-app one on other devices.

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

Every push to `main` runs `.github/workflows/release.yml`. It computes the
version with `scripts/version.py` (from conventional commits and the branch name), builds
a Linux AppImage and a Windows exe and attaches them (plus `SHA256SUMS.txt`)
to a **draft** release tagged `v<version>`. The version is embedded in the
build (shown in Settings) but not in the file names. Nothing goes public until
you open the draft on GitHub, check it and click "Publish release".

## Notifications

The server tells you when something needs attention, for now an auto mode install
that got stuck or failed. The client shows them under "Notifications" (bell button
next to Settings, with the unread count) and in the status line when a new one
arrives; open one to jump to its game, delete it from the list or with X.

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

## License

GPL-3.0-or-later. See `LICENSE`.
