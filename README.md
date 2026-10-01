# MOG Client

CLI client for [MOG-Server](https://github.com/MOG-My-Own-Games/MOG-Server).
Part of [MOG - My Own Games](https://github.com/MOG-My-Own-Games).

Pure stdlib Python, no dependencies to run. A multi-platform GUI client is
planned (see `docs/TODO.md`).

## Install

```bash
pip install -e .
```

## Use

```bash
mog --base http://localhost:5000 --user admin --pass <password> \
    --game-id 1 --out ./my-game
```

Starts (or resumes) the install for game 1 on that server and streams the
result into `./my-game` as it's written - no need to wait for the install to
finish before files start arriving. Run `mog --help` for every option
(`--auto-mode`, `--cancel`, `--clear`, ...).

## License

GPL-3.0-or-later. See `LICENSE`.
