# Installing games

## The flow

1. Pick a game and press Install. The server runs the installer and the client downloads the files as they are written,
   without waiting for the install to finish. Installs can be resumed.
2. When it is done, "Choose executable" lists what can start the game (redistributables are left out). The choice creates
   the launch script, the desktop entry and the Steam shortcut.
3. If the server runs as many installs as it was told to, a new one waits in its queue (in the order asked) and shows in
   the sidebar as "Waiting in the queue". An older server that refuses such a start with `429` is asked again every 15
   seconds.
4. A finished install on the server that holds only empty files (an installer that stopped before it installed anything) is
   not reused: a new install is started.

## Where games go

Settings > General > "Install folders" > "Edit install dirs" opens the folders in order of priority (on a Steam Deck, for
example, `/media/deck/decksd/mog-games`, `/media/deck/decksd2/mog-games` and `/home/deck/mog-games`).

- `+` adds one, `-` removes the selected one from the list (the games in it stay on disk), Move up / Move down change
  which is tried first. Each folder shows its free space or "not connected". Changes are kept at once.
- A new game goes into the first folder that is connected and has room for it (its size on the server plus a margin). A
  drive that is not connected is passed over without a word. When a connected one is full, you are asked before the next
  one is used.
- With no folder listed, games go into `games/` in the client's data folder. A game stays where it was installed, whatever
  the list says later.
- The sidebar shows the free space left across the registered folders.

When a game's folder is not there (the drive is unplugged) the game stays in the library with a grey cover and "Not
available". Its page offers "Check again" instead of Play, nothing is backed up or restored for it, and it comes back by
itself when the drive does. Resuming a half-finished install waits for its drive too.

## Games that need no installer

When the game the server would install is an archive (`.rar`, `.zip`, `.7z`, an ISO) with no installer inside, only the
game in folders, the client asks "Extract its contents and use them as they are?".

- **Yes**: the server unpacks the archive as it is into the install cache, nothing is run, and the client downloads it like
  any install (then asks which file starts the game).
- **No**: it still tries to install by running what is found inside.

A game already begun is not asked again and keeps its choice. `mog --game-id N --extract-only` does the same from the
command line, and the web UI asks the same question from its Install tab.

## Linux games (GOG's `.sh` installers)

A game installed from a native Linux installer is not run through Faugus, umu, Proton or Wine and gets no Wine prefix.

- "Choose executable" lists `start.sh` first, then any `.exe`, then other scripts. The installer's own leftovers
  (`gog-system-report.sh`, `postinst.sh`, `preuninst.sh`, `reuninst.sh`, `yad.sh`, `uninstall-*.sh`, the `.mojosetup`
  folder) are never offered.
- The script is made executable and started as it is, by the launch script, the desktop entry and the Steam shortcut.
- Save sync finds saves through the Wine prefix, so it leaves such a game alone.

## Installing from the web UI

The Install button on a game's page in MOG-Server's web UI opens `mog://install/<game id>?server=<the page's address>`.
This client handles it: it comes forward (or starts), opens the game and begins its install, while the server starts the
installer in the background without leaving the page.

- The client registers itself as the handler of `mog://` links every time it starts (a `mog-client-url.desktop` entry in
  `~/.local/share/applications` on Linux, a key under `HKCU\Software\Classes\mog` on Windows), so run it once after
  installing or moving it.
- A second start does not open a second window: it hands its link to the running client.
- When the link comes from a different address than the one set in Settings (a host name against an IP, say), the client
  asks before installing from the server it is connected to.
- If the page gets no answer from a client it offers the latest release to download.
