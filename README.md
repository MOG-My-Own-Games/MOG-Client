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
a Steam shortcut (with the artwork the server chose for the game: cover, banner, hero, title logo and icon). Both entries are standalone: they run the game straight through its launcher (Faugus, umu, Proton or Wine, as chosen when they were made) and never go through MOG, so they keep working without the client. Both run a small launch script (`<game name>.sh` in the game's own folder) that execs the launcher, so a game's engine can be changed from its Options page ("Launch engine", per game, overriding the one in Settings; Options also has "Shortcuts / executable", "Regenerate shortcuts", uninstalling from this device, deleting the install cache on the server, or both at once) by rewriting that one script: the desktop entry keeps its file and the Steam shortcut is not touched (entries made by an older version are edited in place once, keeping their artwork). Entries are always edited in place, never deleted and recreated (a new Steam shortcut would count as a new game), by "Regenerate shortcuts" and by "Shortcuts / executable" alike. Games launch through
the first of Faugus Launcher, umu, Proton and Wine that is installed (Linux; Settings lists what was found, in that order, and "Rescan launchers" looks again; the scan also runs at the first start and after an update), and directly on Windows.

Each game gets everything in its own folder: `pfx/` (the Wine prefix, which MOG only gives the launcher a path to; the launcher creates and fills it), `<game name>.sh` (the launch script), `<game name>.desktop` (with a link to it in the applications menu, removed when the game is uninstalled), `.directory` (the game's icon for KDE file managers) and, on Windows, `<game name>.lnk`. On Linux the game starts without MOG: run the script or the `.desktop`. "Refresh metadata" in a game's Options fetches its metadata and artwork from the server again and rebuilds the icons and entries from them, editing the Steam shortcut in place. Uninstalling asks before deleting `pfx/`, which may hold save files; if you keep it, everything else in the folder goes. Fully usable
with a gamepad (D-pad/stick, A, B, Start for settings, Start+Select to quit). Games the server matched to the same title show as one entry ("N versions"); installing one lists the installers of every version, each under its version's name, and you pick which to run. The library has a sidebar on the left (library filter and active installs) that stays put while the games scroll; L2 shows or hides it. A legend at the bottom lists the controls that work on the page being shown, with the controller's button icons while one is connected (Xbox, PlayStation, Nintendo or Steam Deck glyphs, depending on the pad) and key icons for the keyboard otherwise (icons: Kenney Input Prompts, CC0). Errors and results that matter appear in a message laid over the window with an OK button (A or Enter to close it); everything else goes to the log. Settings has four tabs, switched with L2 and R2 (Ctrl+Page Up/Down on the keyboard): General (saves, launcher, updates, games folder, sounds), Server, Logs (everything the client logged, white for info, yellow for warnings, red for errors, filterable by level and scrollable with the right stick) and About. While a game started from MOG runs, a message over the window shows its cover and name with a Stop button (A on the pad); it closes by itself when the game does, and Stop closes the game (politely first, then by force) before its saves are backed up. Settings is a menu of rows, each with what it does under its name and its control on the right; the arrows move between rows and Enter (or A) is what changes or opens one, so a drop-down never changes as the arrows pass over it. The pad is ignored while another window is in front (a game, for one), so nothing in MOG reacts to someone playing. Moving through the menus and starting a game make a short sound, which Settings > General turns off (sounds: Kenney Interface Sounds, CC0). On a Steam Deck, text fields open an in-app on-screen keyboard (tap a field, or press A on it). `MOG_OSK=steam` uses Steam's own keyboard instead, `MOG_OSK=0` turns it off and `MOG_OSK=builtin` forces the in-app one on other devices.

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

## If it freezes or crashes

When the window stops answering for five seconds, or the client is killed by a signal (SIGABRT, SIGSEGV), the Python stack of every thread is appended to `logs/crash.log` in the client's data folder (`~/.local/share/mog-client` on Linux). Attach it to a bug report.

## Notifications

The server tells you when something needs attention, for now an auto mode install
that got stuck or failed. The client shows them under "Notifications" (bell button
next to Settings, with the unread count) and in the log when a new one
arrives; open one to jump to its game, delete it from the list or with X.

## Save sync

A game's save files are backed up to the server per machine, so two PCs that share a game never overwrite each other's saves. The server keeps the last three versions from each machine and lists them under the game's Files > Saves tab, where a version can also be downloaded, and a zip uploaded by hand.

Saves usually live inside the game's Wine prefix, which is the `pfx/` folder MOG gives the launcher, so it knows where to look. For a game that has no such folder it falls back to the Faugus game entry with that executable, the Steam shortcut's compatdata, the environment of the running game, or the default of Wine and umu, and asks you to pick one when none of those tells (this only says where to look, it does not change how the game starts). Only the Documents, Saved Games, AppData, ProgramData and Public folders of the prefix are looked at, plus files the game wrote in its own folder; caches, logs and system folders are skipped. If the prefix is shared with other programs, you tick which folders are this game's.

When MOG starts (Settings > General > "Check saves at startup", on by default and greyed while saves are not backed up) it looks over every game: what changed on this machine is sent, and a newer version another machine left is put back when nothing changed here (what it replaces is backed up first); when both sides changed nothing is overwritten and you are told, and the choice is offered when the game is started from MOG. A backup also happens when the game ends (started from MOG, its desktop entry or its Steam shortcut), when MOG opens (for games started by hand), on "Back up saves now" and when the game is uninstalled. A newer save from another machine is offered before the game starts from MOG, after an install, and under Options > "Restore a saved version...". A restore never replaces a file without first copying it to a backup (`saves/<game id>/backups` in the client's data folder). Save sync can be switched off for all games in Settings or for one game in its Options. The library marks a game whose files are gone from the server but whose saves are kept with an amber corner and a floppy disk, and one that has only mods or DLC and nothing that installs it with a violet corner and a puzzle piece. On Windows only the files in the game's own folder are covered for now. `mog --save-sync GAME_ID` backs a game up from the command line.

## Steam shortcuts

Steam keeps its shortcuts in memory and writes `shortcuts.vdf` when it quits, which undoes anything MOG changed in the meantime. So while Steam is running MOG leaves its shortcut for a game alone and says so; the change is made the next time MOG starts with Steam closed (or with "Regenerate shortcuts"). The entries are written the way Steam writes its own (`appname`, `exe`), an entry Steam has rewritten is still updated in place, and a game's artwork is the server's choice with the cover, the screenshots and the cover again filling the banner, hero and icon it has not chosen; what is still missing is in the log.

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
