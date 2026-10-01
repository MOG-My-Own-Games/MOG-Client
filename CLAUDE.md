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
  cli.py        Everything: HTTP client, stream-install loop, argparse entry
                 point (console script `mog`, see pyproject.toml)
mcp/
  server.py     Dev-loop MCP wrapping the CLI's own commands
```

Kept as a single file by design (stdlib-only, no internal layering needed
yet) - split it only when a GUI client needs to reuse the HTTP client
(`MogClient`) independently of the CLI's own argparse/print-based UI, at
which point `MogClient` moves to its own module first.

## Conventions

- Stdlib only in `mog_client/` (no third-party runtime deps - see
  `pyproject.toml`'s empty `dependencies`). The `dev` extra (used by
  `mcp/server.py`) is fine to depend on third-party packages; it never ships
  in what a user installs to just run `mog`.
- Same naming/comment-style conventions as MOG-Server (short comments
  focused on why, not what).

## Commands

```bash
pip install -e .
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