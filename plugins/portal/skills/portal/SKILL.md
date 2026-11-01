---
name: portal
description: Activates when the user wants to see the effectiveness, readiness or usage numbers as a page ("open the dashboard", "show me the portal", "start the portal", "refresh the dashboard"), wants that page as a static file to open from disk or host without a server ("snapshot the dashboard", "export the portal"), or wants to install, reach, secure or remove the Gnomonkit portal on a desktop or a server.
---

# The portal

A read-only page over core's effectiveness collector. Its scripts sit beside this file's plugin
root (`run_portal.py`, `scripts/install_portal.py`).

## Use it

- **Is it running?** Open `http://127.0.0.1:7451/`. If nothing answers, the user has not installed
  it: offer `python scripts/install_portal.py --dry-run` first, then without `--dry-run`.
- **Fresh numbers:** `python run_portal.py refresh` (the page's button does the same, rate-limited).
  A first run over a long history takes minutes; say so instead of waiting silently.
- **No server wanted:** `python run_portal.py snapshot --out <file>.html` writes the page as one
  self-contained file (no network access, no memory data, readable by the user only). It refuses to
  replace an existing file unless `--force` is passed; the installer's `--snapshot <file>` keeps
  it fresh nightly. It still holds session titles: suggest `--hide-titles` before it is shared.
- **Signing in** (server mode or a non-loopback bind): `python run_portal.py login-url` prints a
  sign-in link containing the token. Print it only to the user; never paste it into a commit, a
  PR, an issue or a shared page. `/logout` ends the browser session; `token init --force` ends all.
- **A "refusing the data folder" error** means another account owns the folder or can write to it.
  Show the user the message, including its `chmod 700` hint; do not run the chmod for them.

## Never

- Bind beyond loopback without a token, or pass `--behind-proxy` unless an *authenticating*
  reverse proxy really is in front (it also needs `--allowed-host <public name>`). The server
  refuses the first and a missing `--allowed-host`; whether the proxy authenticates is on you.
- Copy numbers from the portal, or a snapshot file, into anything shared without the user's
  say-so: they describe one person's sessions.

Full reference: `docs/PORTAL.md` in the Gnomonkit repository.
