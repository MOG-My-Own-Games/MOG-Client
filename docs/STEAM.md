# Steam

## First start

The first time MOG starts with nothing set up, a guide asks for:

- the server (and checks that it answers),
- where games go,
- how saves are kept,
- when Steam is installed, which Steam accounts MOG and its games go into.

Each step can be left for Settings > General > Application > "Setup guide", which runs it again starting from what is set and
resetting nothing.

## Accounts

- Until the Steam question has been answered, MOG asks it once per version and again over the first install's "Choose
  executable" page. The answer is a tick list of the accounts with two buttons: "All users" (the default: every account
  found, now and later) and "Continue" (the ticked ones; none ticked turns the integration off).
- Settings > General > Application > "Steam Integration" > "Settings" opens the same list. Confirming it adds or removes MOG
  and every installed game in each account.
- The "Add to Steam" switch on the "Choose executable" page is greyed when the integration is off. It adds the game to
  every account that is on in Settings; the accounts are not listed on that page.

## Shortcuts

- Shortcuts are written at once, with Steam open or not (on a Steam Deck in Game Mode it never closes). Steam shows them
  after a restart.
- Steam is said to write its own copy of `shortcuts.vdf` when it quits, which would undo a change made while it ran. So a
  shortcut written then is checked once, the next time MOG starts with Steam closed, and written again if it is gone.
  Nothing watches Steam in the meantime.
- The entries are written the way Steam writes its own (`appname`, `exe`), and an entry Steam has rewritten is still updated
  in place.
- A game's artwork is the server's choice, with the cover, the screenshots and the cover again filling the banner, hero and
  icon it has not chosen. What is still missing is in the log.
- **Controller profile.** A game the server says has no gamepad support (the Steam store lists none for it) gets the Steam Deck's
  **Keyboard (WASD) and Mouse** template in its shortcut, so the Deck does not map a pad to a game that cannot use one. It is
  written into the account's `configset_controller_neptune.vdf` (under `steamapps/common/Steam Controller Configs/<account>/config/`)
  as a block named after the shortcut, when the shortcut is made or updated and each time the game list arrives (the support of a
  game can be learned later). A game that already has a block there, whether a template, a layout of your own or a workshop one,
  is never changed, and the first time the file is copied beside itself as `.mog-backup`. A game with full or partial support, and one
  the server does not know the support of, is left as it is. Settings > General > Games > Gamepad support turns this off (what was
  already written stays). Steam reads the file when it starts, so a change made while it ran shows
  after a restart; if Steam rewrites the file from memory when it quits, the next start of MOG writes the block again.
- The MOG shortcut follows the client if its file is moved or renamed.
