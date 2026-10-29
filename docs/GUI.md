# Using the client

How the window, a game's page and its entries behave. For installing see [INSTALLING.md](INSTALLING.md), for saves
[SAVES.md](SAVES.md), for Steam [STEAM.md](STEAM.md).

## The library

- The sidebar on the left holds the library filter and what is going on in the background; it stays put while the games
  scroll. L2 shows or hides it, B (Esc) closes it while it is open.
- The sidebar's sections (active installs, libraries, sort and filter) fold with a click, Enter or A on their heading, and
  stay folded between runs. Where the sidebar is too short for all of them the active installs keep their room first, then
  the libraries, then the sort and filter list.
- The library chosen in the sidebar is remembered, for the next visit to the library and the next start, instead of
  going back to All games. A library that is gone from the server is forgotten.
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

- Play starts the game through its launcher. It answers at once: the button says "Starting..." and the ring turns round it
  while the launch settings are taken from the server, until the "Playing" message takes over. That wait can be long (a
  Proton-GE build is downloaded once, winetricks verbs are installed, settings are applied), so what it is doing shows under the
  cover in place of "Installed", with the bar following a download. If the settings cannot be taken, or a verb fails, the game
  still starts, and the winecfg settings (DLL overrides among them) are put in even when a verb failed. While a game started from MOG runs, a message over the window shows its
  cover and name with a Stop button (A on the pad). It closes by itself when the game does. Stop closes the game
  (politely first, then by force) before its saves are backed up.
- **Time played** on the game's page is how long you have played the title (all its versions), as the server counts it for you:
  the sum of the runs your clients reported. A run is from the moment the game's first process shows up to the moment its
  processes are gone, so the wait for the prefix, the restore of saves before and the save sync after it are not in it
  (a run under 10 seconds is not counted, and Stop ends the run where it is). It is sent to the server as soon as the game
  closes; when the server cannot be reached it is kept in `playtime-outbox.json` in the client's data folder and sent at the
  next start (the server counts a run once, whatever the number of times it is sent). A game started from a Steam or
  desktop shortcut is counted when its save watch runs, that is, when saves are synced for it.
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
        from GitHub, kept for a day), as an old game often runs best on an old major, and a few other builds some games
        need exactly: UMU-Proton-9.0-2 and UMU-Proton-9.0-4e (the Proton 9.0 line, from the umu-proton releases). Any
        other Proton-GE or UMU-Proton release a game is pinned to on the server (GE-Proton8-4, say) is downloaded too, by
        its name, the first time that game is played here.
      - Wine runs the system's wine or a build Lutris or Bottles keeps.
      - The engine is per game and overrides the one in Settings; the launch script is rewritten, so the desktop entry and
        Steam shortcut follow. A Proton version is kept on the server (after choosing you say whether it holds for all of the
        game's devices or only this one); the engine and a Wine build stay on this device.
      - A Proton-GE or UMU-Proton build this device lacks is **downloaded when you choose it** (once, checked against its SHA-512, into
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
    - **Winetricks** (Windows games): a list of the usual verbs (the Visual C++ runtimes from vcrun6 to vcrun2022, .NET
      Framework and .NET Desktop, d3dx9, XInput, XAudio, Media Foundation, PhysX, fonts...) and of usual settings with a
      value (`vd=1280x1024`, `renderer=gdi`, `csmt=on`, `grabfullscreen=y`, `hidewineexports=enable`...); the ones already in the prefix are ticked, and so are the ones
      the server keeps for the game that the prefix does not have yet (read when the page opens, marked "on the server, not applied
      yet"; Done applies them, and so does the next start of the game). A setting has one value:
      ticking another value of it takes the tick off the first, and **Add settings...** opens a page (the same as the
      launch arguments') where you type settings of your own, `vd=1280x1024 renderer=gdi`, as many as you need (the pad's keyboard
      types into the field): **+** adds what is typed, each one listed has a **-** that takes it out, **OK** ticks them all in
      place of the other values of their names, and **Back**, in red beside OK, leaves without. A setting is kept in the
      same list as the verbs, as `name=value`; when its value changes, the new value is applied again, and the prefix then holds
      one value for the name. When the server's list has a setting
      for all devices and for this one, this device's value wins.
      **Settings are applied without winetricks, silently:** the client writes the ones it knows (`vd=WxH` or `vd=off`, `renderer`,
      `csmt`, `mwo`, `sound`, `ddr`, `glsl`, `windowmanagerdecorated`, `windowmanagermanaged`, `usetakefocus`, `grabfullscreen`, `hidewineexports`, `videomemorysize`) straight
      into the registry of the game's own prefix with the engine's regedit, as winecfg's settings are, so no winetricks window opens
      when the game starts. A `name=value` it does not know (a wrong name such as `virtualdesktop`, say) is not sent to winetricks at
      start, where it would open a window: it is left for Done in this page, which does try it with winetricks. A verb that winetricks
      could not apply is not tried again at each start, so its window does not open every time; changing it, or pressing Done here,
      tries again.
      The rest is as for verbs. Pressing Done installs the newly ticked ones into the game's prefix, unattended, through the game's
      engine (`umu-run winetricks` for umu and Faugus; winetricks itself, on the prefix, for Wine and Proton, which needs it
      installed). The button reads "winetricks is running..." meanwhile. Like winecfg, you then say whether the verbs hold
      for all of the game's devices or only this one, and they are kept on the server: when the game is reinstalled, or played on
      another device, the verbs it lacks are installed into the new prefix before the game starts (before the winecfg
      settings are applied).
      With the **Faugus** engine, every `name=value` word of a command becomes an environment variable for Faugus, so a
      setting the client does not write itself cannot reach winetricks. It is refused with a message (choose umu-launcher
      or Proton as the game's engine for it), rather than starting winetricks with no verb, which opens its own window. A
      verb of the list never takes a value: `vcrun2015=true` is not an entry and is ignored.
      **Unticking takes an entry out**, of the prefix and of the server's lists (each list that had it, all devices' or this
      one's, so it does not come back from the other):
      - A setting the client writes itself is deleted from the prefix's registry, so Wine's default holds again. Nothing is asked.
      - A verb, or a setting left to winetricks, cannot be uninstalled: the prefix is **made again**. A warning
        asks first, since the prefix is deleted. When the game's save sync is on, its saves are backed up first; if that cannot
        be done, you are asked whether to go on anyway. The old prefix is set aside as `pfx.rebuild`, the engine makes a new one,
        and the old one's `drive_c/users` (Documents, AppData, Saved Games: where most saves are) is copied into it. Links are
        copied as links and never followed. Only then is the old prefix deleted. If the new prefix cannot be made, the old one is
        put back. The ticked verbs and the winecfg settings then go into the new prefix. What the game kept in the Wine registry
        is lost. The button reads "Rebuilding the prefix..." meanwhile. Nothing is done while the game runs, and a `pfx.rebuild`
        left by an interrupted rebuild is never deleted for you.
    - **System language: ...** (Windows games): the language the game sees as the system's, for a game that shows boxes or the
      wrong language until it does (a Japanese game on a system in another language, say). Pick one of English, Japanese, Chinese
      (Simplified, Traditional), Korean, German, French, Spanish, Italian, Portuguese (Brazil), Russian or Polish, or "The
      launcher's own" to go back. It does two things: the prefix's locale (`LocaleName`) and code pages (`ACP`, `OEMCP`) are written
      into its registry, as the Winetricks settings are, and the game is started with `LC_ALL` and `LANG` set to it (`ja_JP.UTF-8`),
      by every engine and by the launch script, which is written again, so the desktop entry and the Steam shortcut have it too. It is
      kept as a `locale=ja_JP` entry in the Winetricks list (where it can also be ticked), so the server keeps it and another device
      takes it at its next start: you are asked whether it holds for all devices or only this one. Going back to the default deletes
      the locale and puts the code pages back as the prefix had them before the first language. The language is a game's own, not the
      one of the sites in Settings.
    - **Launch executable...** (Windows games): runs a program in the game's prefix through its engine:
      - Wine's own tools: regedit, a command prompt, the file manager, the control panel, the task manager and the uninstaller.
      - The game's executables: first the ones "Shortcuts / executable" offers, then every other .exe of its folder (settings
        tools, launchers, redistributables).
      - **Browse...** for any other .exe.
      The button reads "A program is open in the prefix..." until it is closed. What **regedit** changed among the settings
      winecfg keeps (DLL overrides, Direct3D, DirectSound, X11 and virtual desktop options, Windows version) is kept on the
      server like winecfg's: you say whether it holds for all of the game's devices or only this one. Any other key it
      changes, and what the other programs do, stays in this prefix.
    - **Prefix location**: where the game's Wine prefix is, for the save sync (shown when save sync is on).
  - **Shortcuts / executable**: confirming rebuilds the launch script, the desktop entry and the Steam shortcuts. Its
    **Launch arguments** row has an **Edit** button for what is put after the game's executable. The arguments are **tags**:
    each is one option with what goes with it (`-res 1280 720`, `--nosound`). In the page it opens, type arguments in the
    field and press **+** (or Enter on the field): the text splits into tags at each word that starts with `-` or `--` (a
    negative number is a value), with quotes kept (`-title 'My Game'`). Each tag in the list has a **-** that takes it out
    (Enter or A on a tag does the same), **OK** keeps the list and **Back**, in red beside OK, leaves without changes: the launch script is rewritten at once and every launch and shortcut passes the tags on, in order.
    - They are kept on the server with the other launch settings (for all of the game's devices, or for this one when the
      server holds an entry of its own for it) and the server's web page edits them too. Because they are tags, the two are
      **merged** instead of one replacing the other: the client keeps the list it last agreed on with the server, and a
      tag either side added stays and one either side took out goes. What the client sends is the tags to add and to remove,
      which the server merges into its own list. If the server cannot be reached, the change waits (the list here differs
      from the last agreed one) and goes with the next look.
    - The client looks at the server's tags for every installed game at start, when its window comes back to the front and
      every minute, and rewrites the launch script of a game whose tags changed. Arguments an earlier version kept as text
      become tags the first time they are read. A tag with a quote that is never closed stops the game with a message.
    - The way out of this page reads Later while the install waits for the executable and Back when it is opened from
      Options; both are red.
  - **Refresh metadata**: fetches the metadata and artwork from the server again and rebuilds the icons and entries,
    editing the Steam shortcut in place.
  - **Repair: check the installed files**: compares every file the server installed with the server's hash and
    downloads again each one that differs or is missing. It checks against the install cache on the server, shows in
    the sidebar as "Checking files...", and on the game's page Play gives way to a turning **Repairing...** while the install's
    bar fills with how far the check is (it only moves back and forth until the first figure). It replaces a file you changed yourself, except the saves: a file the save sync
    backs up for the game (it is one the installer wrote and the game changed afterwards) is left as it is and the Logs
    tab names it. When the cache is no longer on the
    server it does not stop there: it asks whether to have the server **make the install again** (it runs the game's
    saved installers again, which can take a while and uses room on the server until its cache is deleted; nothing is
    downloaded for that), and the check follows when it is done. Saying no checks nothing. An install that needs a
    manual installer pick says so, with the address to finish it.
  - **Saves...** opens a menu (Back returns to Options) with a **Save sync (this game)** switch and, while it is on,
    **Back up saves now**, **Restore a saved version...** and, while a restore is waiting, **Cancel restoring saves**
    (see [SAVES.md](SAVES.md)).
  - **Nuke custom settings** (red) takes away everything the game has set beyond its files and saves:
    - Its launch arguments, its engine, and its Proton or Wine build: it goes back to the defaults of Settings.
    - Its prefix, made again empty as Winetricks does when a verb is unticked (see above): no winetricks verb or setting,
      no winecfg setting, nothing an installer left. The saves under `drive_c/users` are copied into the new one, and backed
      up first when the save sync is on.
    - What the server keeps for the game, for this device and for all devices, so a start does not bring it back. It goes
      through the launch outbox, so it is sent later when the server cannot be reached.
    It first lists all of it ("You have set: ... Continue?"), with what the server keeps too. It does nothing while the game
    runs. The button reads "Clearing custom settings..." meanwhile.
  - **Uninstall...** opens a menu (Back returns to Options) with **Uninstall from this device**, **Uninstall and delete
    the server cache** and **Delete server cache only**. Uninstalling asks before deleting `pfx/`, which may hold save
    files; if you keep it, everything else in the folder goes. For a game that is not installed here, Options shows
    **Delete server cache only** directly.
- **The executable is kept too.** Confirming "Use this executable" also keeps the choice on the server, as a path from the game's
  folder: for this device, and for all of them when you are an administrator. The next install of the game (after an uninstall,
  or on another device) opens the executable page with that file already selected, so "Use this executable" is enough; it is
  only offered when the file is there, and you can still pick another.
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
- umu (and Faugus, which runs through it) applies a game's protonfixes only when it is started with the id of umu's own game
  database as `GAMEID` (`umu-zerozone`). The client keeps that database (`umu-database.csv` in its data folder, fetched at start
  when it is missing or a week old, never waited for) and uses the id of the game found there, by its title or the name of its
  executable; a game that is not in it gets an id of its own (`umu-mog-<id>`). When a new copy of the database comes, the launch
  scripts are written again.
- A game's own files go in a folder of their own inside the install folder. When what the server sends has no single
  folder (files at the top, or several folders), the client puts it in a folder named for the game; a game that comes in
  its own folder is left as it came.
- Everything of the game is in that folder: `pfx/` (the Wine prefix, which the launcher creates and fills), the launch
  script, `<game name>.desktop` (also linked from the applications menu, removed on uninstall), `.directory` (the icon for
  KDE file managers) and, on Windows, `<game name>.lnk`.

## Window, controller and keyboard

- Fully usable with a gamepad: D-pad or stick, A, B, Start for the user menu, Start+Select to quit. The pad is ignored
  while another window is in front (a game, for one).
- A right click on a game of the library, or Select with a game selected in it, opens that game's Options (Back returns
  to the library). The guide shows Select as Options while a game is selected. Without a game selected, Select is the user
  menu.
- In a menu of buttons such as Linux Options, Up from the first one goes to the last one, and Down from the last goes to
  the card's own button (Back or Close).
- When the game that was started from here ends (or a restore or backup under way finishes), the focus goes back to the
  page's first control, Play on a game's page, instead of being lost.
- On a game's page the focus starts on its first button (Play, Install...), and Tab also stops on the cover. In the
  library, a cover is drawn selected only while the grid has the focus: it is remembered when Tab goes to the search or the
  sidebar, and selected again when the focus returns to the grid.
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
  Notifications (with the unread count), Refresh library, Go offline / Go online, About and Sign out.
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
- About (in the user menu) has two tabs, About and Changelog, switched with L2 and R2. Changelog lists what changed in the
  version that is running, under *Changes* and *Fixes*, each with a link to its commit, and has the Ko-fi button. It opens by
  itself once after an update (a build made by hand carries no notes and links to the releases page instead).

## Offline mode

MOG works without its server as far as it can. A yellow **Offline mode** badge beside the account picture says the client
is in it, and its tooltip says why. There are three ways in:

- **No server**: the internet answers, MOG Server does not.
- **No internet**: nothing answers. Updates and downloads of Proton builds are not tried.
- **By choice**: **Go offline** in the user menu, as in Steam. The server is not asked for anything, nothing looks for a way
  out, and only **Go online** in the same menu leaves it (the choice is kept for the next start).
  When the badge came by itself, the same menu row says **Go online** too: it looks for the server at once instead of
  waiting for the next look.

At the start the client looks at the server's health check (a few seconds at most) before loading the library; out of
reach, the library of the last start is shown. Without the choice above it looks again every 30 seconds, and when the server
answers the library is loaded again and what waited for it is done.

While offline:

- Nothing waits on the server: a call to it fails at once instead of after its retries. **Play** starts the game with the
  launch settings it already has, with no save questions; the notifications, the library check and the launch arguments
  are not polled.
- Changes to the Proton version, winecfg and winetricks are kept in the launch outbox (see above) and sent later. A
  Proton-GE build the game needs is still downloaded when the internet is there.
- **Engine** shows the Proton-GE releases from the cache (or the finished majors) instead of waiting on GitHub.
- Games run through umu or Faugus get `UMU_RUNTIME_UPDATE=0`, so umu does not look for a newer Steam Linux Runtime, and use
  the newest Proton-GE found on this device instead of looking for one. The launch scripts of Steam and the desktop entries
  never carry it: they have no way of knowing. Wine and Proton have no such setting.
- The saves of a game that ended are not backed up but marked, and you are told; see [SAVES.md](SAVES.md).

## Notifications

The server tells you when something needs attention, for now an auto mode install that got stuck or failed. They are under
"Notifications" (the bell next to Settings, with the unread count) and in the log when a new one arrives. Open one to jump
to its game, delete it from the list or with X.

Each one says how long ago it was ("10 minutes ago", "last week", "a year ago") at the right end of its title line, kept up to date while the list is open;
with the mouse over it, the full date shows ("Wednesday 28 October 2026, 13:06"). The pad sees only the words. Long texts wrap to the window, so the list never scrolls sideways.

## If it freezes or crashes

When the window stops answering for five seconds, or the client is killed by a signal (SIGABRT, SIGSEGV), the Python stack
of every thread is appended to `logs/crash.log` in the client's data folder (`~/.local/share/mog-client` on Linux). Attach
it to a bug report.
