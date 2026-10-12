# Using the client

How the window, a game's page and its entries behave. For installing see [INSTALLING.md](INSTALLING.md), for saves
[SAVES.md](SAVES.md), for Steam [STEAM.md](STEAM.md).

## The library

- The sidebar on the left holds the library filter and what is going on in the background; it stays put while the games
  scroll. L2 shows or hides it, B (Esc) closes it while it is open.
- The sidebar's sections (active installs, libraries, sort and filter) fold with a click, Enter or A on their heading, and
  stay folded between runs. Where the sidebar is too short for all of them the active installs keep their room first, then
  the libraries, then the sort and filter list.
- "Sort and filter" in the sidebar has switches (last played first, installed first, cached first: the games the server
  holds a finished install of) and two filters (got saves, got mods: only the games that have them), then the order:
  by release date, name, size, or when the server first saw the game (recently added, earliest added).
- Games the server matched to the same title show as one entry ("N versions"). Installing one lists the installers of
  every version, each under its version's name, and you pick which to run.
- A game with a task going on (an install, saves moving, a mod, a file check) has its cover dimmed with a pie that fills
  as far as the task has got (empty when it has no figure). Nothing on it is animated, and a download's progress reaches
  the window a few times a second however many chunks arrive.
- The sidebar lists each task as a row. Installs the server cannot start yet are listed last, as "Waiting in the queue,
  number N".
- Installs and mod downloads that were running when the client closed start again at the next start.
- A game waiting for its executable to be chosen has an orange corner with a gear (bottom right of its cover) and is listed
  right after the installs under way, before the installed ones. Choosing the executable, like uninstalling, shows on
  the game's main button ("Setting up...", "Uninstalling...") with the turning ring Play shows while saves are on the move.
- The arrow on a cover points down while saves are downloaded and up while they are uploaded (the server is above).
- Everything the window tells you in a message is also in the notifications (a message about a game shows its icon at the
  left), and a message can carry an option (finishing
  an install offers "Choose executable"). The mouse wheel moves the library a part of a row per notch.
- While a game started from MOG starts, the "Playing" message says in a few words what it waits for: the prefix being made,
  the game starting, then "Playing". Questions about one game show its icon (its cover when it has none) and its name.

## A game's page and its Options

- Play starts the game through its launcher. While a game started from MOG runs, a message over the window shows its
  cover and name with a Stop button (A on the pad). It closes by itself when the game does. Stop closes the game
  (politely first, then by force) before its saves are backed up.
- Options:
  - **Linux Options** (only on Linux; not shown on Windows or other systems) opens a menu on top of this one, with a
    Back button (not Close) that returns here; the engine and Proton lists it opens say Back too, and return to it:
    - **Engine: ..., version** (shown as `Engine: umu-launcher, GE-Proton9-27`; in the second, violet gradient when the
      engine or its version is not the default): one page with the **engine** on the left (Default, Faugus, umu, Proton,
      Wine; the ones this device has) and, beside it, the **versions** that engine can run, which change with the engine
      (Left and Right move between the lists). Picking only selects: **OK** applies the engine and version, **Default**
      selects the default engine and version (apply them with OK), and Back leaves without changing anything.
      - Faugus, umu and Proton run Proton builds: the Default, the builds on this device (Steam's library,
        `compatibilitytools.d`) and the last Proton-GE of every major (GE-Proton8-32, GE-Proton9-27, ...; the list comes
        from GitHub, kept for a day), as an old game often runs best on an old major.
      - Wine runs the system's wine or a build Lutris or Bottles keeps.
      - The engine is per game and overrides the one in Settings; the launch script is rewritten, so the desktop entry and
        Steam shortcut follow. A Proton version is kept on the server (after choosing you say whether it holds for all of the
        game's devices or only this one); the engine and a Wine build stay on this device.
      - A Proton-GE this device lacks is **downloaded when you choose it** (once, checked against its SHA-512, into
        `~/.local/share/Steam/compatibilitytools.d`, where Steam, Faugus and umu look), because Faugus does not fetch a
        build itself. The same happens the first time you play a game on a device that lacks the build its other devices use.
      - When something set here or on another device is not on this machine (an engine, a Proton or Wine build), a warning
        says so when it is chosen and when you play: "The selected Proton build X was not found, so the default Y is used".
        The game starts with the default.
    - **Wine configuration (winecfg)** (Windows games): opens winecfg in the game's prefix; the button reads "winecfg is open..." until it is
      closed. What it changed (Windows version, DLL overrides, Direct3D, DirectSound, X11 and virtual desktop options) is
      kept on the server the same way. When you play on a device, the Proton build and settings the server holds for the
      game are taken first (once for the same settings), and the game starts even when that fails. When the settings of a game
      were deleted on the server (its Settings tab), the game goes back to the launcher's own Proton the next time it starts and
      the prefix is put back as it was before the winecfg settings: only the values they changed are touched (the launcher's
      own defaults in the same keys stay), from a note kept when they were applied or made in a prefix that already existed (the
      engine makes a new one with its own defaults, which cannot be told from settings). Settings applied before that was
      recorded, and what winetricks installed, stay in the prefix.
    - **Winetricks** (Windows games): a list of the usual verbs (vcrun2019, dotnet48, d3dx9, ...); the ones already in the prefix
      are ticked. Pressing Done installs the newly ticked ones into the game's prefix, unattended, through the game's
      engine (`umu-run winetricks` for umu and Faugus; winetricks itself, on the prefix, for Wine and Proton, which needs it
      installed). The button reads "winetricks is running..." meanwhile. Like winecfg, you then say whether the verbs hold
      for all of the game's devices or only this one, and they are kept on the server: when the game is reinstalled, or played on
      another device, the verbs it lacks are installed into the new prefix before the game starts (before the winecfg
      settings are applied). Verbs cannot be removed from here.
    - **Prefix location**: where the game's Wine prefix is, for the save sync (shown when save sync is on).
  - **Shortcuts / executable**: confirming rebuilds the launch script, the desktop entry and the Steam shortcuts.
  - **Refresh metadata**: fetches the metadata and artwork from the server again and rebuilds the icons and entries,
    editing the Steam shortcut in place.
  - **Repair: check the installed files**: compares every file the server installed with the server's hash and
    downloads again each one that differs or is missing. It needs the install cache to still be on the server, shows in
    the sidebar as "Checking files...", and replaces a file you changed yourself.
  - **Saves...** opens a menu (Back returns to Options) with a **Save sync (this game)** switch and, while it is on,
    **Back up saves now**, **Restore a saved version...** and, while a restore is waiting, **Cancel restoring saves**
    (see [SAVES.md](SAVES.md)).
  - **Uninstall...** opens a menu (Back returns to Options) with **Uninstall from this device**, **Uninstall and delete
    the server cache** and **Delete server cache only**. Uninstalling asks before deleting `pfx/`, which may hold save
    files; if you keep it, everything else in the folder goes. For a game that is not installed here, Options shows
    **Delete server cache only** directly.
- **When the server cannot be reached**, what is changed in a game's Linux Options (the Proton version, what winecfg and
  winetricks did) is not lost: it is written to `launch-outbox.json` in the data folder and kept there until the server
  answers that it has it. It is sent again every 15 seconds while the client is open and before a game starts. A later change
  to the same thing replaces the one waiting, and what waits is never replaced by the server's older value (the next Play
  does not undo it). A change the server refuses for good (a 4xx such as an unknown game, or a verb it does not accept) is
  dropped and logged so it does not block the rest. A change meant for "only this device" waits until this machine has a
  name on the server; it is never sent for all devices instead. The file outlives an uninstall of the game.
- Under the header the strip of thumbnails holds the game's videos first (Intro, then Gameplay, with a play mark and
  the kind along the foot) and its screenshots after. Opening a screenshot shows it full size; opening a video opens it
  in the browser, as the client has no player of its own. The videos are the ones the server lists for the game.
- A game whose folder on the server has a `mods` folder gets a Mods button. Each top-level folder or archive in it is
  one mod (`mods/mod1/file.zip` and `mods/mod2.zip` are mod1 and mod2). Picking one downloads it to the `mods` folder of
  the game's own folder (nothing is installed, and save sync leaves the file alone). A folder is zipped by the server
  first; the zipping and the download show as a row in the sidebar, and the server sends a notification when the zip is
  ready. Picking a mod that is being fetched offers to cancel it.
- The install folders keep no empty folders: deleting a mod (or stopping its download) removes the empty `mods` folder,
  and for a game that is not installed its folder as well. The install folders themselves are never removed.

## The game's entries and folder

- Choosing the executable asks separately for an entry in the applications menu, a shortcut on the desktop (a copy of the
  entry, in the desktop folder the system names) and Steam. Uninstalling removes all of them.
- A game gets a desktop entry and a Steam shortcut with the artwork the server chose (cover, banner, hero, title logo
  and icon). Both are standalone: they run a small launch script (`<game name>.sh`) that execs the launcher, so they keep
  working without the client.
- Changing a game's engine rewrites that one script; the desktop entry keeps its file and the Steam shortcut is not
  touched. Entries are always edited in place, never deleted and recreated (a new Steam shortcut would count as a new
  game). Entries made by an older version are edited in place once, keeping their artwork.
- Games launch through the first of Faugus Launcher, umu, Proton and Wine that is installed (Linux). Settings > Library lists what
  was found and "Scan for launchers" looks again; the scan also runs at the first start and after an update. On Windows
  the game starts directly.
- A game's own files go in a folder of their own inside the install folder. When what the server sends has no single
  folder (files at the top, or several folders), the client puts it in a folder named for the game; a game that comes in
  its own folder is left as it came.
- Everything of the game is in that folder: `pfx/` (the Wine prefix, which the launcher creates and fills), the launch
  script, `<game name>.desktop` (also linked from the applications menu, removed on uninstall), `.directory` (the icon for
  KDE file managers) and, on Windows, `<game name>.lnk`.

## Window, controller and keyboard

- Fully usable with a gamepad: D-pad or stick, A, B, Start for the user menu, Start+Select to quit. The pad is ignored
  while another window is in front (a game, for one).
- A legend at the bottom lists the controls that work on the page being shown. It shows the controller's button icons
  (Xbox, PlayStation, Nintendo or Steam Deck glyphs) while a controller is connected and was the last thing used, and key
  icons otherwise; pressing a key switches it to the keyboard at once. Icons: Kenney Input Prompts, CC0.
- In every list you pick from ("Choose an installer", "Choose executable", the lists of saves to put back, the other
  questions with a list, the file and folder pickers, the engine and version of a game, the mods), picking a row (a click,
  Enter, A or a double click) only selects it and moves on to the button; the button ("OK", "Use this executable",
  "Download") does it. A folder in the file picker is the exception: it opens. "Later" and "Skip" leave without choosing,
  so those cards have no Close button.
- Errors and results that matter appear in a message over the window with an OK button (A or Enter); everything else goes
  to the log.
- The window can be made as small as about 720x460 (800x600 and a small desktop mode included). A game's page then keeps
  the cover (smaller, same proportions), the status, the bar and the buttons. Below the size the full page needs it drops
  the description and the screenshots, and when also narrow the HowLongToBeat table; they come back when the window grows.
  A long title is cut short, and the sidebar's lists scroll when the window is too short.
- On a Steam Deck, text fields open an in-app on-screen keyboard (tap a field, or press A on it). `MOG_OSK=steam` uses
  Steam's own keyboard, `MOG_OSK=0` turns it off and `MOG_OSK=builtin` forces the in-app one on other devices.
- Moving through the menus and starting a game make a short sound; Settings > General turns it off (sounds: Kenney
  Interface Sounds, CC0).

## The user menu and Settings

- The user menu (picture and name at the top right; Start or Ctrl+Comma, also Select or Ctrl+U) holds Settings,
  Notifications (with the unread count), Refresh library and Sign out.
- Settings is a menu of rows, each with what it does under its name and its control on the right. The arrows move between
  rows and Enter (or A) changes or opens one, so a drop-down never changes as the arrows pass over it.
- Four tabs, switched with L2 and R2 (Ctrl+Page Up/Down): General (saves, launcher, updates, sounds), Server, Library and
  Logs.
- The Library tab holds the install folders and "Scan for launchers", and below them every game installed on this device
  with the space it takes, the biggest first, and an Uninstall button (the same questions as Uninstall in the game's own
  Options: its saves are backed up first and the prefix is only deleted when asked). A game whose folder is not there
  (a drive that is not connected) is listed last, without a size.
- The Logs tab shows everything the client logged (white for info, yellow for warnings, red for errors), filterable by
  level and scrollable with the right stick. The same log is kept in `logs/client.log` in the client's data folder.

## Notifications

The server tells you when something needs attention, for now an auto mode install that got stuck or failed. They are under
"Notifications" (the bell next to Settings, with the unread count) and in the log when a new one arrives. Open one to jump
to its game, delete it from the list or with X.

## If it freezes or crashes

When the window stops answering for five seconds, or the client is killed by a signal (SIGABRT, SIGSEGV), the Python stack
of every thread is appended to `logs/crash.log` in the client's data folder (`~/.local/share/mog-client` on Linux). Attach
it to a bug report.
