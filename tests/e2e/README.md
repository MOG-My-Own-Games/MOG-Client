# End-to-end test: install and save sync between Linux and Windows

`test_freeciv_save_sync.py` runs the whole path a game takes, with real programs and no fakes:

1. A MOG Server container gets a library holding the Windows installer of [Freeciv](https://www.freeciv.org/) (GPL, NSIS
   wizard) and runs it in its sandbox under Proton-CachyOS, the auto mode reading and pressing through the wizard (OCR).
2. Two client containers install it at the same time: one runs MOG Client on Linux (the game through Wine, a prefix in
   the game's folder), the other runs the same source with Windows' own Python under Wine (`sys.platform == "win32"`,
   the game's `.exe` started directly). Both must download from the one install on the server.
3. Linux plays a new game (three turns of an AI game run by `freeciv-server.exe` from `freeciv/new-game.serv`) and backs
   its saves up with `mog --save-sync`.
4. Windows takes them with `mog --save-pre`, loads Linux's last save, plays on to turn 5 (`freeciv/continue.serv`) and
   backs up.
5. The test checks each backup holds the game's saves and nothing else, that Windows' turn 5 is Linux's game (same map
   seed, terrain and leaders), and that Linux gets Windows' saves back with `mog --save-pre`.

## Running it

It is skipped unless `MOG_E2E=1`. It needs Docker (rootful, the server's sandbox runs privileged), about 3 GB in the home
folder for the cache and a run, and takes a few minutes.

```bash
MOG_E2E=1 pytest tests/e2e -s
```

The first run builds the client image (`client.Dockerfile`), builds the server image from the MOG-Server checkout next to
this repo, and downloads Freeciv's installer and Proton-CachyOS into `~/.cache/mog-e2e` (each checked against its SHA-256).

| Variable | Meaning |
| --- | --- |
| `MOG_E2E_SERVER_IMAGE` | Use this server image instead of building one. |
| `MOG_E2E_SERVER_MOUNT=1` | With the above: mount the checkout's `backend/` and `frontend/` over the image's code (no rebuild). |
| `MOG_E2E_SERVER_SRC` | Where the MOG-Server checkout is (default: `../MOG-Server`). |
| `MOG_E2E_REBUILD=1` | Build the client image again. |
| `MOG_E2E_KEEP=1` | Leave the containers running afterwards, to look at them. |
| `MOG_E2E_DIAGNOSTICS=1` | Keep the server and client logs even when the test passes. |
| `MOG_E2E_DOCKER` | The docker command (default `docker`, or `flatpak-spawn --host docker` inside a Flatpak). |
| `MOG_E2E_BUILD_NETWORK` | The network images are built with (default `host`). |
| `MOG_E2E_CACHE` | Where downloads and runs go (default `~/.cache/mog-e2e`); it must be visible to the Docker daemon. |
| `MOG_E2E_INSTALL_TIMEOUT` | Seconds the installs may take (default 2400). |

A failure leaves the server log, both clients' commands and logs, and the server's install state in
`~/.cache/mog-e2e/runs/<run>/diagnostics`; the run folder also holds the server's data and each client's games.

## What it does not cover

- A real Windows: the Windows client is Windows' Python under Wine, so Windows' own shell (the `.lnk` shortcut, which
  needs PowerShell) is left out. The desktop entry and Steam shortcut are left out on both sides.
- Saves in the Windows profile: the Windows client syncs only the game's own folder for now (see `docs/SAVES.md`), which
  is why the game is told to save there.
