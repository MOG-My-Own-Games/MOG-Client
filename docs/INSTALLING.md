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

Settings > Library > "Install folders" > "Edit install dirs" opens the folders in order of priority (on a Steam Deck, for
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

When the games folder was moved (or the install folder changed), the games are looked for in the install folders: "Refresh
library", "Check again" and changing the folders do it. A folder with the game's name, holding its executable, is the game
again: the library, the save sync's prefix, the launch script, the menu and desktop entries, the Steam shortcut and the folder
icon follow it to the new place.

A game that is not found that way (it was renamed on the server, or moved to another disk or machine) is pointed to by
hand: Options > "Find game..." is there for any game, installed here or not, and asks for the folder of the game itself, the
one with its launch script.

- A game with a record here has to find its executable in that folder where it was; the library, the entries and the Steam
  shortcut follow it as above, and the record takes the server's name for the game (a game renamed on the server would
  otherwise be set aside as belonging to another one). The files named after the old name are replaced.
- A game with no record here (it came from another machine, or its record was set aside) gets one from any folder that has
  something in it, and asks for its executable and shortcuts as after an install.

## Games that need no installer

When the game the server would install is an archive (`.rar`, `.zip`, `.7z`, an ISO) with no installer inside, only the
game in folders, the client asks "Extract its contents and use them as they are?".

- **Yes**: the server unpacks the archive as it is into the install cache, nothing is run, and the client downloads it like
  any install (then asks which file starts the game).
- **No**: it still tries to install by running what is found inside.

The installer picker ("Choose an installer") always has the row "Just copy the files, run nothing" first under each
version, so a game that comes with an installer can still be taken as it is. It is a row like an installer, ticked and
numbered like the others, and see below for what it does with patches after it.

A game already begun is not asked again and keeps its choice. `mog --game-id N --extract-only` does the same from the
command line, and the web UI asks the same question from its Install tab.

## Linux games (GOG's `.sh` installers)

A game installed from a native Linux installer is not run through Faugus, umu, Proton or Wine and gets no Wine prefix.

- "Choose executable" lists `start.sh` first, then any `.exe`, then other scripts, then AppImages (`*.AppImage`, offered
  even when a download left them unmarked executable). The installer's own leftovers
  (`gog-system-report.sh`, `postinst.sh`, `preuninst.sh`, `reuninst.sh`, `yad.sh`, `uninstall-*.sh`, the `.mojosetup`
  folder) are never offered. A folder with neither a `.exe` nor a script (an itch.io or engine game, `Game.x86_64`) offers
  its Linux programs as the last resort, found by their ELF header: the shallowest first, the one named like the game before
  the others, shared libraries (`*.so`) never.
- The script is made executable and started as it is, by the launch script, the desktop entry and the Steam shortcut.
- Save sync finds saves through the Wine prefix, so it leaves such a game alone.

## Installers inside discs and archives

A game's installer is often inside a disc image or an archive (`DISK1.ISO` with a `setup.exe` in it). The installer picker
lists what the server finds inside each disc image and archive (a few at most), indented under it, and the one you pick
there is run from inside that disc. A `.mds` file next to an image only describes it and is not looked into.

## Several installers, in order

A game can come with patches next to its installer. In the installer picker every installer that can run is a big label with
a flag on the left:

- Tick the ones to run. They go to the top and are numbered in the order they will be installed (1 is the game, the others
  are patches, shown as a `game` or `patch` label like the server's), each with Up and Down arrows. The row you are on is
  filled, so it is clear which one it is. With the keyboard or the pad, Right walks from a ticked row onto its arrows,
  Left walks back, and Enter (or A) moves the row. The ones not ticked stay below. Installers found inside a disc or archive are listed as `DISK1.ISO > setup.exe`
  and can be ticked like the others.
- **Install** runs what is ticked (the selected row when nothing is ticked). Two or more run one after another in one install
  on the server, in one installer display; the files are streamed once, at the end. If one fails, the install stops there and
  the message names the step ("Step 2/3: ..."). The server remembers the order for the game.
- **Install** starts by itself: the server picks the installer and nothing is asked. The picker opens only to
  disambiguate: when the server says it could not tell which installer to run, or when a new game has more than one choice
  to put in order (patches next to the installer, installers inside a disc; if the server already remembers an order for
  the game, those installers are ticked and numbered). A title with several versions first asks which version, nothing
  else. **Cancel** closes the picker; the arrows walk between the buttons as they are laid out (Right from Install is
  Cancel).
- **A game the server already holds the install of** (its cache is still there, the game shows as cached) is never asked
  anything: **Install** only downloads it, with no picker and no "extract it?" question. Delete the install cache on the
  server to be asked again.
- **Just copy the files, run nothing** is the first row of every version and works like an installer. Ticked alone, it
  takes the files as they are (an archive is unpacked). Ticked with others it is always number 1, the game, and the
  installers ticked after it are its patches: the server puts the files in the install's Wine prefix and runs those on
  top, and what they change is kept as their patch. It cannot be moved down, and nothing can be put above it.
- A patch may ask where the game is installed. MOG does not answer that for you: open the installer display from the game's
  page and answer by hand.
- All of them must be Windows installers, or all native Linux ones.

## Addons: mods, patches and DLC

MOG keeps the files a mod, a patch or a DLC adds as an *addon* on the server, apart from the install cache. This client can
send one and take one; it is **not a mod manager** (no load order, no conflict handling, no enabling or disabling), for that
use dedicated software such as Amethyst Mod Manager.

- On an installed game's page, **Addon** lists what the server keeps for it, with Apply and Delete (Delete removes it from
  the server, so no machine can get it any more), and **Install addon** / **Finish and send...** for one you install yourself.
- A mod, patch or DLC you run yourself on the installed game is recorded when it ends: its files go in the game's install
  manifest, so they are never taken for saves. You can then send what changed to the server as a mod, patch or DLC.
- An addon the server kept (made by an install on the server, or sent from another machine) is downloaded and extracted over
  the game, again recorded in the manifest. Nothing is run: it already is the files.
- Sending and applying cover the window with a progress bar (packing the files, bytes sent or downloaded, files put over
  the game, "Waiting for the server..." once everything is sent). Cancel stops a send or an apply that has not started to
  write; once files are going over the game it is greyed out, so the game is never left half patched.
- The files of an addon use the same paths as the install's own files; one that points outside the game's folder is refused.

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
