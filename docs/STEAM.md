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
- The "Add to Steam" switch on the "Choose executable" page is greyed when the integration is off, and an account left out
  is greyed in its list.

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
- The MOG shortcut follows the client if its file is moved or renamed.
