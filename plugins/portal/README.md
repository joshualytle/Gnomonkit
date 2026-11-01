# portal

> Provided as is, without warranty; see the [disclaimer](../../DISCLAIMER.md). Serving it beyond
> your own machine exposes metrics from your sessions; securing that server is your responsibility.

A small read-only web dashboard over core's effectiveness collector, for the desktop you develop on
or for a server. Optional: nothing else in Gnomonkit needs it.

| Tab | What it shows |
|---|---|
| Effectiveness | subagents (spend, reads avoided, the spawns that did not pay back), main context, compaction per project, context budget suggestions, hook uptake |
| Readiness | each launch-readiness row against its target, failing rows first, on-target rows folded |
| Usage | this month's plan against the API-equivalent cost so far, the projection and the multiple; tokens, API-equivalent $ and share of plan $ by day, project and session |
| Where it goes | API-equivalent cost per activity (shipping, tests, reading code, each subagent type, each MCP server, ...) split into context carry and work, repeated processes with their cost per run, and the share of cost spent at large context sizes |
| Updates | what core's sync gate holds for review: each gated source and its pin, each held update with its reasons and files changed, and (on a loopback portal only) a Draft approval button that opens the approval pull request in your overlay. See [docs/SYNC.md](../../docs/SYNC.md) |
| Work | open GitHub work items (core's `gnomonkit work`) by state: active first, stale claims flagged, with their age, last activity and a link to the issue. Read from core's local summary; never an issue body, and titles and claims are dropped with `hide_titles` |
| Trust | your folder-trust roots with their levels, the repositories under each and what they resolve to, and the folders Claude Code trusts that no root covers. Loopback portal only (a snapshot holds it only with `--trust`); read-only, it names the `gnomonkit trust` command for each change |
| Memory | read-only search of your past sessions: a local `mempalace` CLI when one is installed, otherwise core's history index; hidden when there is neither |

A tab whose section is missing from the metrics file says so instead of drawing empty charts. The
Work tab never calls GitHub: any `gnomonkit work` command refreshes the summary it reads.

## Install

From any terminal:

```text
claude plugin marketplace add joshualytle/Gnomonkit
claude plugin install portal@gnomonkit
```

Then run the commands below from the plugin's folder. Re-run the installer after each portal update:
the service runs from one version's folder.

## Quick start

```sh
python scripts/install_portal.py --dry-run     # see every file and command first
python scripts/install_portal.py               # local: starts at logon, refreshes nightly
python run_portal.py refresh                   # first data now
# then open http://127.0.0.1:7451/
```

Server (Linux, systemd):

```sh
sudo python3 scripts/install_portal.py --mode server --user <account> --dry-run
sudo python3 scripts/install_portal.py --mode server --user <account>
python3 run_portal.py login-url --base-url https://portal.example.com
```

Behind an authenticating reverse proxy, pass `--behind-proxy --allowed-host portal.example.com`
instead of using a token; `--behind-proxy` alone is refused.

No server at all: write the page as one self-contained HTML file (no network access, no memory
data, readable by you only), and optionally keep it fresh nightly:

```sh
python run_portal.py snapshot --out <file>.html [--force]
python scripts/install_portal.py --snapshot <file>.html
```

Standard library only. Python 3.10 or newer. Full reference, including the security model and the
reverse-proxy setup: [docs/PORTAL.md](../../docs/PORTAL.md).

## Files

| Path | Role |
|---|---|
| `run_portal.py` | entry point: `serve`, `refresh`, `snapshot`, `token`, `login-url`, `status` |
| `gnomonkit_portal/server.py` | HTTP server, access modes, session cookie, limits, routes |
| `gnomonkit_portal/view.py` | the whitelisted view the page receives |
| `gnomonkit_portal/refresh.py` | runs the collector into the data dir |
| `gnomonkit_portal/snapshot.py` | the page as one static HTML file with the view embedded |
| `gnomonkit_portal/paths.py` | the data dir, its checks, and private (0600, no-symlink) writes |
| `gnomonkit_portal/token.py` | the access token file |
| `gnomonkit_portal/memory.py` | memory search: a mempalace CLI, or core's history index read-only |
| `gnomonkit_portal/updates.py` | the Updates tab's view of the sync gate, and the call to `gnomonkit sync approve-draft` |
| `gnomonkit_portal/trust.py` | the Trust tab's read of `gnomonkit trust status --json` (loopback only) |
| `gnomonkit_portal/page.html` | the page (no external requests) |
| `scripts/install_portal.py` | install, update, uninstall on Windows, macOS and Linux |
