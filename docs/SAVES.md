# Save sync

A game's save files are backed up to the server and follow you between machines. Each upload is filed under the machine it
came from, and a newer save from another machine is brought over, so you can pick up a game where you left it. The server
keeps the last three versions from each machine (the server's administrator can change that number, 1 to 20) and lists them under the game's Files > Saves tab. A version can be
downloaded, and a zip can be uploaded by hand.

Save sync can be switched off for all games in Settings or for one game in its Options.

## Where the saves are found

- Usually inside the game's Wine prefix, the `pfx/` folder MOG gives the launcher.
- For a game without that folder it falls back to the Faugus entry with that executable, the Steam
  shortcut's compatdata, the environment of the running game, or the default of Wine and umu. When none of those tells, you
  are asked to pick one. This only says where to look; it does not change how the game starts.
- Only the Documents, Saved Games, AppData, ProgramData and Public folders of the prefix are looked at, plus files the game
  wrote in its own folder. Caches, logs and system folders are skipped. If the prefix is shared with other programs, you
  tick which folders are this game's.
- What MOG itself puts in a game's folder is never taken for a save, whichever installer it came from (the game's own, a
  patch or DLC installed later, a reinstall). The client keeps the server's list of every install's files and also looks at
  the folder before and after an install. Mods you add by hand are not known to MOG and can be mistaken for saves.
- On Windows only the files in the game's own folder are covered for now.

## When it syncs

- **At startup** (Settings > General > "Check saves at startup", on by default): every game is looked over. What changed
  here is sent; a newer version from another machine is put back when nothing changed here (what it replaces is backed up
  first). When both sides changed nothing is overwritten, you are told, and the choice is offered when the game is started
  from MOG.
- **When the game ends** (started from MOG, its desktop entry or its Steam shortcut), when MOG opens (for games started by
  hand), on Options > Saves... > "Back up saves now" and when the game is uninstalled.
- **Before the game starts**: a newer save from another machine is offered when started from MOG, after an install, and
  under Options > Saves... > "Restore a saved version...".
- **Offline** (the server cannot be reached, or Offline mode was chosen, see [GUI.md](GUI.md)): nothing is sent and no save
  question is asked before a game starts. A backup that was due is left as it is, marked "waiting for the server" in the game's
  last check, and you are told. Nothing is changed in the baseline, so when the server is back the next look over the saves
  finds what changed and sends it, even with "Check saves at startup" off. A game started from Steam or the desktop checks
  the server for a few seconds first and goes on without it.
- A restore never replaces a file without first copying it to a backup (`saves/<game id>/backups` in the client's data
  folder).

## Saves waiting for a new game

Saves from another machine wait for a prefix the game has not made yet (a game just installed). They are put in as soon as
the launcher has made it, while the game starts. If you do not see them in the game, close it and start it again.

Until they are in, nothing is backed up from this machine: a game's first run writes files of its own, and sending them would
replace the waiting saves as the newest version. "Back up saves now" still does it.

## Games started outside MOG

A game started from its shortcut or Steam calls the client when it is installed:

- **Before it starts**: puts back a restore that was waiting and takes a newer save another machine left. When the saves
  changed here too, a small window asks: use it, keep this machine's, or ask next time (Enter, a click or A answer; Esc or
  B leaves it for next time).
- **When it ends**: backs the saves up behind a small window (Settings > General > "Show a window when saving").

The shortcut finds the client through a file MOG writes in its config folder (`client-path`) at every start, so a moved or
renamed client is found again after one start from its new place. Without a client the game starts as usual. On Windows the
shortcut runs a `.cmd` next to the game that starts it, waits for it and then backs the saves up.

## Mods, patches and DLC are not saves

Files a mod, a patch or a DLC adds are written into the game's install manifest when MOG installs or applies them (the same
record an install makes), so save sync tells them from what the game writes and never backs them up as saves. Mods you copy
in by hand are in no manifest; they are told apart by where they are, as above.

- To tell the installer's files from the game's, a file is read once and compared with the manifest. Its size, time and
  checksum are then kept (`manifests/<game id>.verified.json`), and a backup only reads a file again when its size or time
  changed. An install fills this list as it finishes, so even the first backup does not read the whole game: that took
  minutes for a big game on a Steam Deck's SD card.

## Command line

`mog --save-sync GAME_ID` backs a game up (add `--window` to show the small "Syncing saves" window). `--save-pre` and
`--save-watch` are what the shortcut runs.

## Logs

What the save steps do is written to `logs/client.log` in the client's data folder (and to the Logs tab for a game started
from MOG):

- the game's processes when they are seen, a line every five minutes while it keeps running (with the processes that count
  as the game, so one that never ends is visible), and when it ended;
- each backup's start, the scan, the upload and the result, with how long it took.

Settings > General > "Send the log with saves" (off by default) also sends this device's log (the client log and the game's
launch log, the newest part) to the server whenever a backup uploads something or goes wrong, at most every 30 seconds. A game that never ran has no
saves to back up, so its log is sent apart, whenever the launch settings could not be taken at the start or the game ends within two
minutes of starting (or never showed up), with winetricks' and winecfg's output (`tool-<id>.log`) added. The
server keeps the newest one per device under Profile > My devices > Log.

## The library marks

A game whose files are gone from the server but whose saves are kept has an amber corner and a floppy disk. One that has only
mods or DLC and nothing that installs it has a violet corner and a puzzle piece.
