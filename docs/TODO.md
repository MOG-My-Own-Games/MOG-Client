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

Sync save files across devices/clients, possibly also with Heroic, GameNative or Playnite or RomM (which
already has its own save-sync for ROM-based saves) for a shared library.
Not started - needs a design for where saves live server-side and how
conflicts are resolved.

## CLI polish

- `--json` output mode for scripting.
- Config file / env vars for `--base`/`--user`/`--pass` so they don't need
  to be passed on every invocation.
