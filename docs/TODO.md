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
of what it replaces, finding the game's prefix (see README, "Save sync").

Still open:

- Windows: the game's `.cmd` waits only for the process it starts, so a game that hands over to another program and exits is backed up too early; waiting for the whole process tree (a job object) would fix it. The `.cmd` and the `.lnk` it is started from have only been checked as text, not on Windows.
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

## Launchers and the game folder

- PortProton: it unsets `WINEPREFIX` and keeps its prefixes in `data/prefixes/<name>`, chosen with `PW_PREFIX_NAME`, so
  it cannot be handed `<game>/pfx` directly. The plan is a symlink `data/prefixes/mog-<id>` to `<game>/pfx` and
  that variable; it needs a machine with PortProton to check (here `~/PortProton` is a dead link). It belongs between
  Faugus and umu in the order of preference.
- The Windows `.lnk` is written through PowerShell (`WScript.Shell`) and has only been checked as a command line,
  not on Windows.
- The Faugus Flatpak must be allowed to write where the games are; under the home folder it is, another mount may need
  `flatpak override --filesystem=`.
- A games folder on a filesystem without Unix permissions or symbolic links (NTFS, exFAT, some NAS shares) may not be
  able to hold a Wine prefix; MOG does not warn about it yet.
- "Refresh metadata" keeps the game's name (the files in its folder are named after it), so a name changed on the
  server is not picked up.

