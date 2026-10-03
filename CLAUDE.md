# MOG Client - Agent Guide

CLI client for [MOG-Server](https://github.com/MOG-My-Own-Games/MOG-Server).
Stdlib-only Python today (`mog_client/cli.py`); a multi-platform GUI client
(Linux/Windows) is the planned Phase 2 (see `docs/TODO.md`), along with
GameNative/Playnite integration research and save-file sync.

Read `CLAUDE.local.md` (gitignored, not committed) before writing any
repo-wide text (docs, comments, commit messages, PR descriptions) - it
covers branding and naming rules that stay out of version control on
purpose.

## Never commit or push without being directly asked

Do not run `git commit`, `git push`, or open a PR unless the user explicitly
asks for that specific action in that turn. Implementing and testing a
change is not itself a request to commit it.

## Structure

```
mog_client/
  api.py        HTTP client (Client, MogClient), shared by CLI and GUI
  transfer.py   Stream-install loops (download, verify, poll) with log callbacks
  cli.py        argparse entry point (console script `mog`); no action = GUI
  grouping.py   Games with the same IGDB id are versions of one title (library entry, installer picker)
  updater.py    Self-update from the latest GitHub release (AppImage/exe; UPDATE_METHOD build flag)
  version.py    __version__ + UPDATE_METHOD, written into _version.py at build time
  config.py     Settings + installed-games record (JSON under XDG dirs)
  manager.py    Install / finish-setup / uninstall workflows (GUI side)
  launcher.py   Executable discovery (redists excluded), launch, .desktop entry
  steam.py      shortcuts.vdf read/write + grid artwork
  scrape.py     Artwork URLs from the server's already-scraped metadata
  gui/          PySide6 app (app.py) and stdlib gamepad reader (gamepad.py)
    widgets.py  accent gradient (same stops as MOG-Server's --accent-gradient) + Toggle switch
    keyboard.py in-app on-screen keyboard layout/text logic; osk.py decides when it opens (MOG_OSK)
packaging/      PyInstaller entry point, .desktop file and icon
scripts/        build-appimage.sh (Linux) and build-exe.ps1 (Windows)
.github/workflows/release.yml  push to main -> scripts/version.py -> AppImage + exe -> DRAFT release v<version>
mcp/
  server.py     Dev-loop MCP wrapping the CLI's own commands
```

`mog` with `--game-id`/`--list`/`--launch` is the CLI; with no action (or
`--gui`) it opens the GUI. Everything except `gui/` stays stdlib-only; PySide6
lives in the `gui` extra.

## Conventions

- Stdlib only in `mog_client/` (no third-party runtime deps - see
  `pyproject.toml`'s empty `dependencies`). The `dev` extra (used by
  `mcp/server.py`) is fine to depend on third-party packages; it never ships
  in what a user installs to just run `mog`.
- Same naming/comment-style conventions as MOG-Server (short comments
  focused on why, not what).

- **Single window.** The GUI is one `QMainWindow` with a page stack
  (`MainWindow.push`/`back`). Never open a `QDialog`, `QMessageBox` or native
  file dialog: settings, confirmations, the executable picker and the file
  browser are pages. Back (button, Esc, gamepad B) pops the stack and installs
  keep running on worker threads meanwhile. Every page must be usable with
  arrows + Enter only.

## Commands

```bash
pip install -e '.[gui]'
mog                      # GUI
mog --list
mog --launch 1
mog --base http://localhost:5000 --user admin --pass <password> --game-id 1
```

## GUI
Add a multi platform, controller supported, gui in addition to the cli that will:
- list the available from the server
- makrs the installed ones as installed
- offers an install button
- resume support
- stream install support
- once the insaller is finished it changes state into "awaiting executable info" in this state the game is asking you to browse for the executable (it can list all thep ossibile executables except the redist, directx). Once the user chose the default executable to launch for the game it runs the exe (maybe with faugus launcher in the beginning, then let's see). It creates and scrapes (with the server already scraped data) A desktop entry and a Steam Entry.
- the created desktop/steam entry is recorded by the client and removed once the game is uninstalled
- never delete a local prefix itself without user consent and warning as save files might be in (this rule does not apply for the throwaway server side installation prefix)
- the game can be launched then from the client itself using faugus launcher (we can prompt the user to install it, from flatpak for instance), on windows just laucnhes the exe. Or even manually from Steam or Desktop shortcut.