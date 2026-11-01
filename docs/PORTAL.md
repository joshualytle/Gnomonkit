# Portal

The portal is an optional Gnomonkit plugin (`plugins/portal`) that shows core's effectiveness
metrics as one web page. Run it on the desktop you develop on, where it listens on loopback only, or
on a server behind a token or an authenticating reverse proxy. Or write the page to one static HTML
file that needs no server at all (a [snapshot](#static-snapshot)).

## How it fits together

```text
transcripts ──> core effectiveness.py --json ──> <data dir>/metrics.json ──> view.py ──> /api/metrics ──> page
                 (run by `portal refresh`:        (never served as is)      (whitelist)
                  nightly, CLI, or the button)
```

- **Collector.** Core's `skills/effectiveness/effectiveness.py`. The portal finds it beside itself
  in a Gnomonkit checkout, else the newest core in the Claude Code plugin cache, else
  `GNOMONKIT_EFFECTIVENESS_COLLECTOR`.
- **Data dir.** `GNOMONKIT_PORTAL_DIR`, else `$GNOMONKIT_HOME/portal`, else `~/.gnomonkit/portal`.
  It holds `metrics.json`, `state.json` (the last refresh), the optional `config.json` and `token`,
  and `portal.log` (rotated). On POSIX the folder is 0700 and every file in it 0600 (see
  [Files on disk](#files-on-disk)).
- **Sections.** Effectiveness, readiness and usage are each drawn only when the metrics file carries
  them, so an older core still works and the missing tabs say what they need.
- **Usage and the plan** (core 0.17.0 or newer). With a subscription schedule
  (`~/.gnomonkit/subscription.json`, see core's effectiveness skill), the Usage tab opens on this month:
  - the plan's price, the API-equivalent cost so far, the linear projection for the month and that
    projection as a multiple of the plan, all from `usage.month_to_date`;
  - below that, a month-by-month table from `usage.months`;
  - in the project, session and day tables, a **Share of plan $** column (`subscription_usd_share`)
    beside the API-equivalent $.

  Without a schedule, the collector reports `subscription.status` as `missing` and the tab says how to
  add one. The schedule file's path and the collector's note that names it are never served.
- **Where it goes** (core 0.20.0 or newer), from the collector's `activities` section:
  - a ranked bar per activity, each split into context carry (cache read and write) and work (input
    and output), with the subagent breakdowns and the top "shell other" programs folded below;
  - a strip of the cost by context size (up to 200k, 200k-400k, 400k-600k, above 600k);
  - the repeated processes with runs, sessions, cost per run and total.

  Activity names are rebuilt from the collector's own names plus a subagent type, MCP server or tool
  name; programs and MCP servers are shown by name. A process is redrawn from its step shapes as
  program names, keeping a subcommand only from a fixed list of known words (`git commit`,
  `gh pr create`), so command text, arguments, hosts and paths never reach the page.
- **Updates** (core 0.29.1 or newer), from the sync gate's own files under `~/.gnomonkit/sync/`
  ([SYNC.md](SYNC.md)): each gated source with its branch, state, approved pin and upstream head, and
  each held update with its reasons, the contributor and paths a newcomer touched, and the files changed
  with lines added and deleted. Remote URLs, error messages and file contents are never served. In
  loopback mode a **Draft approval** button runs core's `gnomonkit sync approve-draft <id>`, which opens
  the approval pull request in your overlay; merging it there is the approval.
- **Work** (core 0.30.0 or newer), from `$GNOMONKIT_HOME/work/items.json`, the summary core's
  `gnomonkit work` writes each time it reads a repository: open items by state (active, blocked, in
  review, open), stale claims flagged, with last activity, age and a link to the issue. The portal
  never calls GitHub, so the tab is as fresh as the last work command; the note under it says when
  each repository was read. On a server with no work file the tab says how to fill it.
- **Trust** (core 0.32.0 or newer, loopback mode only), from core's `gnomonkit trust status --json`
  ([folder-trust design](specs/2026-10-26-folder-trust-design.md)): every `[[trust]]` root with its
  level, the repositories under each root with what they resolve to (trusted; pending, which
  `gnomonkit trust sync` would trust; not trusted, with the reason; or unknown), and the folders
  Claude Code trusts that no root covers, grouped by their parent folder. The tab changes nothing:
  it names the terminal command for each change (`gnomonkit trust sync`, `gnomonkit setup security`).

## Refresh

`python run_portal.py refresh` runs the collector once. One run at a time (a lock file); a failed
run keeps the last good data and records the error class, never the collector's stderr, which can
name local paths. The class is kept only when the last stderr line starts with a plain exception
name; any other line, which might hold a path, is recorded as no class at all. The installer schedules a nightly run, and the page's **Refresh data** button
starts one, at most once per `refresh_min_interval` seconds (default 600). The status line under the
title always says when the data was made and how the last refresh went. The nightly run also runs the
sync gate's check (`gnomonkit sync check --if-due`) when core has it; its result goes to the log and
never fails the refresh.

## Settings

`<data dir>/config.json`, all optional:

| Key | Default | Meaning |
|---|---|---|
| `hide_titles` | `false` | never send session titles to the page (also `--hide-titles`, `GNOMONKIT_PORTAL_HIDE_TITLES=1`) |
| `refresh_min_interval` | `600` | seconds between button refreshes |
| `memory` | `"auto"` | `auto`: Memory tab on only in loopback mode (a loopback bind with no token and no `--behind-proxy`) when a `mempalace` CLI or core's history index is found; `on`; `off` |
| `since_days` | `30` | days the refresh looks back |

## Security model

The page shows how one person works with their agents. It is treated as private data.

**Binding.** The default is `127.0.0.1`. A name other than `localhost` must be an IP address, so
what is bound is exactly what was asked for. A non-loopback bind **refuses to start** unless there is
an access token or `--behind-proxy` is passed, and `--behind-proxy` refuses to start without
`--allowed-host` (both exit 2).

**Access modes**, fixed at start:

| Mode | When | Access check |
|---|---|---|
| loopback | loopback bind, no token | the `Host` header must name a loopback host, which defeats DNS rebinding |
| token | a token exists (`<data dir>/token`, `--token-file` or `GNOMONKIT_PORTAL_TOKEN`) | every request except `/healthz` carries it: the `X-Gnomonkit-Token` header, `Authorization: Bearer`, or the session cookie |
| proxy | `--behind-proxy --allowed-host NAME` | the reverse proxy in front must authenticate; the portal refuses any `Host` but the names given |

**The token** is 256 random bits from `secrets`, written once with `O_EXCL`, `O_NOFOLLOW` and mode
`0600`. A token path that is a symlink, or a file another account owns, is refused rather than read
or replaced. On Windows that mode does nothing. The file is private there only because it sits under
the user profile, whose ACL admits that user, SYSTEM and Administrators.

**Signing in from a browser.** Open the link from `run_portal.py login-url` once. The portal checks
the token, sets an `HttpOnly`, `SameSite=Strict` cookie (`Secure` behind HTTPS or with
`--secure-cookie`) and redirects to `/`, so the token does not stay in the address bar. The cookie
never holds the token: it holds the time it was issued and an HMAC of that time keyed by the token.
It lasts 7 days by the server's clock, `/logout` clears it, and a new token (`token init --force`)
ends every session at once.

**`?token=` and logs.** The portal's own access log records method, path and status, never a query
string. Anything else that sees the login request may keep the full URL: a reverse proxy's or load
balancer's access log, a corporate web proxy, or browser history if the redirect is blocked. Behind
a proxy, prefer handing the token over in the proxy (`X-Gnomonkit-Token`) so users never see a
login link. If a login link may have been logged where others can read it, run `token init --force`.

**`--behind-proxy`** tells the portal to trust every request that reaches it. Use it only with an
**authenticating** reverse proxy in front: forward auth to an identity provider, or basic auth at
the least. Bind the portal to `127.0.0.1` with the proxy on the same host, or firewall its port to
the proxy. `--allowed-host <public name>` is required (repeat it for several names): every other
`Host` value is refused, so a DNS-rebinding page cannot reach the API through the proxy port. A proxy
that only forwards traffic, with no authentication, makes the data public.

**Read-only.** GET serves the page, `/api/status`, `/api/metrics`, `/api/sync`, in loopback mode
`/api/trust` and, when on, `/api/memory`. `POST /api/refresh` starts the collector and nothing else. It needs the
token in a header, or a same-origin request (`Origin` matching `Host`, or
`Sec-Fetch-Site: same-origin`), so the cookie alone cannot be borrowed by another site. Other
methods get 405. Requests marked `Sec-Fetch-Site: cross-site` get 403, and no CORS headers are ever
sent.

**Draft approval.** `POST /api/sync/approve` exists only in loopback mode; in token and proxy mode it
answers 404 and the page shows the terminal command instead, because the page may be open on another
device there. It needs a same-origin request (a token does not stand in for one) with a JSON body
naming an update the gate holds, makes one draft at a time, and runs core's
`gnomonkit sync approve-draft`. That pushes a branch and opens a pull request in your overlay; it
approves nothing until you merge it.

**Trust tab.** `/api/trust` exists only in loopback mode; in token and proxy mode it answers 404 and
the tab names `gnomonkit trust status` instead, because the view names this machine's folders and on
a server the portal's account is not the developer's. A read runs core's
`gnomonkit trust status --json` with a 10-second limit, at most once every 30 seconds and one at a
time, so reloading the page cannot start a scan per click. The portal never parses the grants file,
never opens Claude Code's file, and never writes either. `view.py` keeps a whitelist of core's
output: paths under the home folder are shown as `~/...` (in reasons and problems too), repositories
by their path under their root, and strings are capped and cleaned like titles. A failed, slow or
malformed read gives an empty tab with a note, never an error page.

**What is served.** Never the metrics file and never transcripts or prompts. `view.py` rebuilds
each section from numbers, collector-generated labels and reasons, project folder names (never
paths), and session titles (capped; dropped with `hide_titles`; control, bidi-override and
zero-width characters removed, so a title cannot reorder or hide text on the page). Subagent
descriptions, correction samples, working directories and the digest wrapper's name are left out.
`/api/work` serves each work item's repository, number, state, ages and stale flag, a link rebuilt
from the repository and number (never a URL read from the file), and its title and claim unless
`hide_titles` is set. Issue bodies and comments are not in the summary file, so they cannot reach
the network in any access mode. A `metrics.json` or `state.json` that is malformed, of the wrong
shape, or holds NaN or infinity gives an empty view with a 200, never an error page.

**Escaping.** Session titles and memory snippets come from transcripts, and work-item titles from
anyone who can open an issue, so all are untrusted. The page
writes every string with `textContent`; nothing parses a string as HTML. The JSON escapes `<`, `>`
and `&`. The page runs under a per-response nonce CSP with no external origins. Every response,
error pages included, sends `nosniff`, `X-Frame-Options: DENY`, `frame-ancestors 'none'`,
`no-referrer` and `no-store`.

**Limits.** At most 32 connections at once; the next one gets a bare `503` and is closed. An idle
socket times out after 10 seconds. A request body needs a plain decimal `Content-Length` of at most
1024 bytes: a negative, non-numeric or missing length is a `400` and a larger one a `413`, both at
once, without reading the body.

**Memory tab.** When a `mempalace` CLI is found it runs `mempalace search --results N -- <query>`
as one short process per search. On Windows a `mempalace` that resolves to a `.bat` or `.cmd` wrapper
is never run, because cmd.exe would parse the query again as commands; point
`GNOMONKIT_MEMPALACE_CLI` at the real executable instead. Otherwise it reads core's history index
([HISTORY.md](HISTORY.md); `~/.gnomonkit/history/history.db`, or the file `GNOMONKIT_HISTORY_DB`
names), opened read-only, so the portal never writes to it. An index run does not hold it up; a
lock is waited on for 1.5 seconds at most and a query is stopped after 5, and either says "busy,
try again" instead of hanging the page. A portal started before the index exists finds it once it
does. Either way at most two searches run at once, nothing runs while idle,
and no tool schema is loaded into an agent's context, unlike an MCP server. Snippets, the match line
and source names are capped and long token-like strings are masked. Because both hold text from past
sessions, `auto` turns the tab on only in loopback mode: not with a token, not behind a proxy.

**Port.** On Windows the bind is exclusive (`SO_EXCLUSIVEADDRUSE`), so a second process cannot
take the port and the traffic with it.

### Files on disk

On POSIX the data folder is created 0700 and every file in it 0600, and the installed services run
with a private umask (systemd `UMask=0077`, launchd `Umask` 63, which is 077). Every file the portal
replaces is written under a fresh name and moved into place with `os.replace`: `state.json` and the
snapshot through `mkstemp` in the same folder, `metrics.json` through a fresh `mkdtemp` work folder.
A file or symlink planted at a guessable temp name is never written through. The logs and the
refresh lock are opened with `O_NOFOLLOW` and refused unless they are this account's regular files.

The portal refuses a data folder that belongs to another account, or that group or others can write
to, and exits 2 with a message that ends in the fix (`chmod 700 <folder>`). A folder that others can
only read is tightened to 0700 without asking. Some file systems have no POSIX modes at all (a
Windows drive under WSL, exFAT, many network shares): every folder there reports 0777 and `chmod`
changes nothing. Put the data folder on a native file system instead; the message says so too.

**Upgrading from 0.3 or older.** Earlier versions created the data folder with the default umask.
Where that umask is `002` (common on systems that give each user their own group), the folder is
group-writable and 0.4.0 refuses it. Check that nothing in it is unexpected, run the `chmod 700` the
message gives, and start again.

## Static snapshot

A third way to run the portal: no server at all. One command writes the page as a single
self-contained HTML file, to open from disk or to put on a host that serves static files only.

```sh
python run_portal.py snapshot --out FILE [--force] [--hide-titles] [--trust]
python run_portal.py refresh --snapshot FILE [--hide-titles] [--trust]   # refresh, then write the snapshot if the run succeeded
```

- **What it holds.** Exactly the public view the live page fetches from `/api/metrics`, plus the
  refresh status line, embedded as JSON in a `<script type="application/json">` block. The JSON
  escapes `<`, `>` and `&`, so no title can close the script element. It never holds the Memory tab
  or any search result, and it has no refresh button and no sign-out link.
- **Trust only when asked.** The Trust tab names your folders, so a snapshot holds it only with
  `--trust` (the same view the loopback page serves, home folder as `~`). Without the flag the
  snapshot has no Trust tab.
- **No network.** A CSP meta tag allows only the file's own script and style, by `sha256` hash,
  and sets `connect-src 'none'`, so the page cannot fetch, post or beacon anything. The file names
  no URL.
- **File mode.** It is written through a fresh `mkstemp` name and `os.replace`, readable by its owner
  only (0600 on POSIX). An existing file is replaced only with `--force`. `refresh --snapshot` (and
  the installer's `--snapshot`) also replaces a file this command wrote earlier, and refuses
  anything else.
- **Sharing it.** A new snapshot is private. To let a web server read it, grant that yourself
  (`chmod 644 FILE`, or a group). A later snapshot over it keeps that read access, at most 0644.
- **Before you publish one,** remember it carries the same aggregates and session titles as the live
  page. Use `--hide-titles` (or `hide_titles` in `config.json`), and put any public copy behind the
  same authentication you would give the live page.

## Install

`scripts/install_portal.py` (in the plugin) sets the portal up as a background service. Every run
supports `--dry-run`, which prints each file and command and changes nothing. Re-running with the same
options rewrites nothing and restarts the server. Re-running with a different `--port` replaces the
service in place. `--uninstall` removes what was installed and keeps the data dir unless you add
`--purge`.

The default port is **7451**. No well-known service uses it, and it sits below the dynamic range
Windows reserves port blocks from. Two checks keep a broken install from looking like a working one:

- **Before registering anything,** the installer binds the port to check it is free. When another
  program holds it, or the system reserves it, the installer stops, installs nothing and suggests
  `--port`. A port held by an earlier portal is fine, because the new registration replaces it.
- **After starting,** it waits up to 10 seconds for the portal's own `/healthz`, which carries the
  `X-Gnomonkit-Portal` header and a `gnomonkit-portal <version>` body. Any 200 is not enough, so
  another program answering on the port does not count. If the portal does not come up, the
  installer fails. It prints the service's own account of why (the Scheduled Task's
  `LastTaskResult`, `launchctl print`, or `systemctl status`) and the log path.

Every path in a service definition is quoted: the Scheduled Task argument string (for
`CommandLineToArgvW`), each systemd `ExecStart` word, and each launchd `ProgramArguments` entry,
which is an array element. A home folder with a space in its name works. On Windows the data dir
goes into the task as a quoted `--data-dir` argument.

```sh
python scripts/install_portal.py [--mode local|server] [--host IP] [--port N] [--refresh-at HH:MM]
                                 [--behind-proxy --allowed-host NAME] [--secure-cookie] [--hide-titles]
                                 [--snapshot FILE] [--user ACCOUNT] [--data-dir DIR]
                                 [--uninstall [--purge]] [--dry-run]
```

`--snapshot FILE` makes the nightly refresh also write a [static snapshot](#static-snapshot) to
`FILE` after each successful run. In server mode the snapshot's folder is added to the unit's
`ReadWritePaths`. With `--hide-titles` the installer passes it to the nightly refresh as well as to
the server, so the snapshot leaves titles out just as the live page does.

**Local mode** (per user, no admin rights):

| OS | What it installs |
|---|---|
| Windows | Scheduled Tasks `Gnomonkit Portal` (at logon) and `Gnomonkit Portal Refresh` (daily), run by `pythonw.exe` so no console window opens. Registered with PowerShell's ScheduledTasks cmdlets, passed as `-EncodedCommand`, so no path goes through command-line quoting. Runs only while the user is logged on. |
| macOS | launchd agents `gnomonkit.portal` (RunAtLoad, KeepAlive) and `gnomonkit.portal-refresh` (StartCalendarInterval) in `~/Library/LaunchAgents` |
| Linux | `systemd --user` units `gnomonkit-portal.service`, `gnomonkit-portal-refresh.service` and `.timer`. They stop at logout unless you run `loginctl enable-linger`. |

A local install with a non-loopback `--host` (to reach the page from a phone on your network, say)
also creates a token.

**Server mode** (Linux, root): system units that run as `--user`, never as root, under a sandbox
(`ProtectSystem=strict`, `ProtectHome=read-only`, write access only to that user's `~/.gnomonkit`,
`NoNewPrivileges`, `PrivateTmp`, restricted address families, `UMask=0077`). A token is created
unless you pass `--behind-proxy`, which needs `--allowed-host`. The installer prints Caddy and nginx
snippets with placeholders such as `portal.example.com`: put authentication in the marked place,
and either hand the token over in the proxy (`X-Gnomonkit-Token`) so users never see it, or run
with `--behind-proxy --allowed-host portal.example.com`.

Root never creates, chowns or chmods anything inside a folder the service account controls, because
a symlink planted there would turn that into a write to any file on the machine. The token and the
data folder are created by a child process that runs as the service account (its own user and
group, no extra groups, umask 077). Root creates the data folder itself only when `--data-dir` names
a folder whose parent root controls, and then hands it over with `fchown` on the opened folder
(`O_NOFOLLOW`), never by path.

Root reads the data folder's path as written, one folder at a time from `/` with `lstat`, and never
resolves it first. A symlink anywhere on that path is refused, so is a folder that belongs to an
account other than root or the service account. Otherwise a service account that made its own
`~/.gnomonkit` a link to a system folder could have root create, hand over or delete files there.
If your data folder really lives behind a link, pass its real path with `--data-dir`.
`--uninstall --purge` checks the path the same way. Root deletes the folder itself only when every
folder above it is root's own; otherwise the service account deletes its own data, as itself.

**After updating the plugin**, re-run the installer from the new version. The service points at
the folder of the version that installed it.

## Troubleshooting

- **The page says no data.** Run `python run_portal.py refresh`, then `python run_portal.py status`.
- **"refusing to bind".** Create a token (`run_portal.py token init`) or pass `--behind-proxy`
  with `--allowed-host` behind an authenticating proxy.
- **"refusing --behind-proxy without --allowed-host".** Add `--allowed-host` with the public name
  the proxy serves (repeat it for several names).
- **"refusing the data folder".** Another account owns it or can write to it. Read the message: for
  a folder of your own made group-writable by an older version, check its contents, then run the
  `chmod 700` it prints. If the mode does not change, the folder is on a file system without POSIX
  modes (a Windows drive under WSL, exFAT, a network share). Otherwise, or then, point `--data-dir`
  or `GNOMONKIT_PORTAL_DIR` at a folder of yours on a native file system. From the installer in
  server mode, "is a symlink" means a folder on the way is a link: pass the real path.
- **"refusing the token path".** The token path is a symlink or another account's file. Remove it
  and run `run_portal.py token init`.
- **Memory tab says a `.bat` or `.cmd` wrapper is not run.** Set `GNOMONKIT_MEMPALACE_CLI` to the
  `mempalace` executable itself (for a pip install, the `.exe` under the environment's `Scripts`).
- **"could not bind" or "already used by another program".** The port is in use or reserved
  (Windows reserves some ranges; `netsh int ipv4 show excludedportrange protocol=tcp` lists them).
  Pick another `--port`.
- **"the portal did not answer".** Read the service status and the log the installer printed. Run
  `python run_portal.py serve --port N` in a terminal to see the error directly.
- **Sign-in needed.** Print the link with `run_portal.py login-url` on the machine that runs the
  portal.
