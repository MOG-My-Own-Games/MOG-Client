# MOG Client - Phase 2+ backlog

## GUI client

A multi-platform desktop client (Linux, Windows) replacing/complementing the
CLI - browse a library, start/watch installs with an embedded VNC view,
manage save files. Not started.

## Non-RomM stream-install integrations

Research whether [GameNative](https://github.com/GameNative/GameNative), Heroic Game Launcher and
[Playnite](https://playnite.link/) extension points could drive a
stream-install against a MOG-Server directly, instead of (or alongside) the
dedicated MOG-Client. Not started - needs scoping first.

## Save-file sync

Done: per-machine backups to the server, last three versions kept per machine, restore with a backup
of what it replaces, prefix discovery without choosing a prefix for the user (see README, "Save sync").

Still open:

- Windows: only the game's own folder is covered. The profile folders need the shell's known-folder
  lookup (Documents can be redirected, e.g. to OneDrive) and must not be scanned without a session window.
- Detecting when a game ends works from the process table on Linux and has only been exercised against
  a fake `/proc`; it still needs checking against a real Faugus (Flatpak), umu and Proton run.
- The registry (HKCU/HKLM) is not captured, and neither are saves outside the scanned folders;
  a per-game list of extra folders would cover the latter.
- Syncing with Heroic, GameNative, Playnite or RomM's own save sync, for a shared library.

## CLI polish

- `--json` output mode for scripting.
- Config file / env vars for `--base`/`--user`/`--pass` so they don't need
  to be passed on every invocation.
