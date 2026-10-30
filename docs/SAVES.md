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
- On Windows only the files in the game's own folder are covered for now (a game added from outside MOG is read from the real profile too).

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

## Games from outside MOG

A game that is already on this computer, from itch.io or anywhere else, can have its saves synced: user menu > **Add local game**.
The game's folder and its prefix stay where they are; MOG only records them. Its launch script, `.desktop` file and
icon are kept in MOG's data folder (`imports/<game id>`), never in the game's folder, which another program may rewrite.

- **What it is made for**: Windows, Linux or macOS, found from the programs in the folder and confirmed by you. A Windows game
  needs a Wine prefix everywhere but on Windows, where it is read from your real profile (Documents, Saved Games, AppData).
- **The name**: filled in from the folder's name. If MOG Server has a game with a name like it, you pick it (so a second computer
  joins the first one's saves instead of making a copy) or add a new one; the new entry is shown on the server with a green
  corner and a floppy disk until files for it are added, and a scan that finds a folder named like it takes the entry over.
- **The library**: for a new entry you pick which library of MOG Server it is listed under, or **New Library...** (a name and the
  folder as the server sees it; administrators only). It is only a label: MOG never writes into a library's
  folder, and a scan that finds the game's folder there makes it an ordinary game.
- **The executable**: once it is added you are asked which file starts the game, with the same shortcuts questions as after an
  install; Back leaves it for later (**Add a launcher...** on the game's page).
- **The prefix of a Windows game** (not on Windows): **Browse Prefix** picks the one the game runs in, **New Prefix** makes one
  for it (in the game's folder, or where you say; the launcher creates it the first time the game starts, so you pick the
  game's executable too). Do not pick a Default prefix (`~/.wine`, umu's `umu-default`, a folder called `default`): other games
  run in it and their saves are in it too, so such a prefix is marked in the list and asks again. A new prefix starts empty: the
  saves the game already has may be in the prefix it uses now, and the warning says so.
- **The saves' folders**: the places that look like the game's saves, the ones named like the game first, ticked. They are in the
  prefix or the profile (Windows), below `~/.config`, `~/.local/share`, `~/Documents` (and `~/Library` on macOS) or inside the
  game's own folder (a `saves` folder, `*.sav`). Your own name never counts as the game's: a folder named like you is not
  taken for its saves, even when the game sits in your home folder. Change them later under Options > Saves... > **Choose the saves' folders...**.
- **The first sync takes every file in those folders**, not only what the game writes while it runs: with eight saves of which one
  is touched, all eight are on the server and in every later version. When another computer already left saves, the one with
  the older ones is asked: use the server's (what is here is copied to a backup first) or keep this computer's and send it.
- **Playing**: choose the executable (**Add a launcher...**) and the game gets its Play button, desktop entry and Steam
  shortcut like any MOG game, with the saves restored before it starts and backed up when it ends. Without one, the saves are
  looked over when MOG starts and with Options > Saves... > Back up saves now.
- **Remove from MOG** forgets the game on this computer (after a last backup); it never deletes the folder or the prefix, and the
  saves stay on the server. **Move into MOG...** is the other way: it moves the folder, and the prefix if you say so, into the
  install folder and makes it an ordinary MOG game. It deletes them from where they were, which breaks whatever else started
  the game from there, and moving a prefix that is a Default or shared one takes the other games' data with it: it asks twice.
- **Games started from a Flatpak app** (itch's, for one): Flatpak moves a game's `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` to
  `~/.var/app/<app id>/config`, `.../data` and `.../.local/state`, so its saves are not in `~/.config`. A folder named like the game in
  the folders of an app is offered with "(Flatpak: app id)", and ticking it records the app for the game. The saves keep the usual keys
  (`xdg-config/...`), so another computer where the game runs outside Flatpak finds them in its `~/.config`. A game cannot save in two
  places: ticking folders of the real home and of an app together is refused. While it is on, MOG starts the game (Play, the desktop
  entry, the Steam shortcut) with the app's folders, so it finds the saves and settings it left under the app; Linux Options >
  **Flatpak sandbox** switches this off.
- macOS is implemented but has not been tried on a Mac.

## Import external saves

A game installed by MOG that you also had outside MOG (itch.io, a Wine prefix of its own) can take your old progress in place of
the fresh saves it starts with: Options > Saves... > **Import external saves...**.

- It works once the game has saved from here at least once: that is how MOG learns which folders the game writes its saves to.
  Before that the button only says so (start the game, save, close it, come back).
- You say where the old saves are: **another Wine prefix** (the list of prefixes MOG knows, with Default ones marked, or any
  folder) for the saves the game keeps in its prefix, or **the old game folder** for those it keeps next to itself. A game that
  uses both is done in two steps. A native game that keeps its saves below `~/.config` or `~/.local/share` has nothing to
  import: an older install wrote to the same places.
- MOG looks in the other place for the same folders (never a whole Documents folder: the game's own subfolder, or the one file)
  and lists what is not the same as here. If some of those files are older than the ones the game has now, you choose between
  all of them and only the new and newer ones.
- What is put in replaces the fresh files, which are first copied to a backup archive (the same one a restore makes), and the result
  is backed up to the server as a new version. A game added with **Add local game** does not have the button: its saves are the
  old ones already.

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

A game whose files are gone from the server but whose saves are kept has an amber corner and a floppy disk; a game added from
outside MOG that the server never had files for has the same floppy on a green corner. One that has only
mods or DLC and nothing that installs it has a violet corner and a puzzle piece.
