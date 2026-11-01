---
description: Activates when developing on Windows, writing a scheduled task, parsing piped input, or when something fails only on Windows or only on Linux CI.
---

> Full trigger text: Cross-project Windows development traps that look like application bugs. Activates when developing on Windows, writing a scheduled task, parsing piped input, comparing file permissions, committing a long message from PowerShell, or handling timezones. Also when something works on Linux/macOS and fails only on Windows, when a test passes on a Windows dev box and fails only on the Linux CI runner, when an app starts child processes (an encoder, FFmpeg, a converter) that must stop when it closes or crashes, when a PowerShell script redirects a native command's stderr, loops over parsed JSON, or names backup copies after their paths.

# Windows development traps

Each of these presents as an application bug and is not, which is what makes
them expensive to debug.

## PowerShell writes a UTF-8 BOM, and it breaks parsers

`Set-Content -Encoding utf8` and piping a string to a native process both
emit a leading BOM in Windows PowerShell 5.1. `json.load` rejects it outright,
and any `^`-anchored regex silently fails to match because a BOM is not
whitespace.

**Always decode as `utf-8-sig`, never `utf-8`,** when reading a file or stdin
that could have been produced on Windows. It strips a BOM when present and is
identical otherwise, so there is no downside.

The usual symptoms: a hook that silently stops guarding, or a tripwire that
silently stops matching.

**When PowerShell writes JSON that other tools read, write it without a BOM:**
`[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding $false))`.
`Set-Content -Encoding UTF8` in 5.1 always starts the file with `EF BB BF`.

## Scheduled tasks steal focus

A Task Scheduler action pointing at a console executable opens a window on
every run, over whatever the user is doing, and takes the mouse with it.

**Use the windowless host** — for Python, `pythonw.exe -m package.module`.

But `pythonw` discards stdout and stderr entirely, so **add file logging in
the same change** or a failing scheduled run leaves no trace at all. A rotating
handler, because a job running every 30 minutes writes forever.

Keep the task as "run only when the user is logged on": switching to "whether
logged on or not" changes the logon session and silently breaks access to
Credential Manager / the OS keychain.

## `os.chmod` does not write an ACL

On Windows it only toggles the read-only attribute. Code calling `chmod(0o600)`
for privacy is doing nothing, and documentation claiming the file is
owner-only is wrong there.

The protection usually still holds — files under the user profile inherit an
ACL admitting only that user, SYSTEM, and Administrators — but it holds for a
different reason, and local administrators are in scope where a POSIX `0600`
would exclude them. **Document the real mechanism rather than the intended
one.**

## Long commit messages die in PowerShell quoting

Both `@"…"@` and `@'…'@` here-strings mangle multi-line commit messages
containing quotes, and git then reports a wall of `pathspec … did not match`
errors that look like a git problem.

**Write the message to a file and use `git commit -F <file>`.** It is reliable
regardless of content, and it is the only approach that survives quotes,
apostrophes and backticks in the body.

Similarly, `gh api` arguments containing `?`, `|` or jq expressions get split
by PowerShell. Prefer `gh repo clone` and local reads over fighting the
quoting.

## Windows ships no IANA timezone database

`zoneinfo` finds **zero** zones on a stock Windows Python — not even `UTC`. Any
`ZoneInfo("Area/City")` call raises `ZoneInfoNotFoundError`.

**Depend on `tzdata` under a platform marker:**

```toml
dependencies = ['tzdata; platform_system == "Windows"']
```

Also: Windows timezone names ("Mountain Standard Time") are not IANA
identifiers and never will be. Validate a configured zone at load time rather
than at first use, or the failure surfaces deep inside a job after it has
already done expensive work.

## Sockets rebind, so "port busy" tests do not fail

Windows lets a second socket bind an already-bound port, so a test that
occupies a port and expects the next bind to fail will instead succeed — and
then sit waiting on whatever the code does next.

**Inject the failure** (monkeypatch the constructor to raise) rather than
trying to create it for real.

## Git Bash mangles a native tool's `/switches` into paths

MSYS/Git Bash rewrites any argument that looks like a POSIX path, so a
Windows tool called with DOS-style switches receives nonsense. Inno
Setup's compiler invoked from Git Bash as

```
ISCC.exe /DAppVer=1.2.3 /Odist /FMyApp-setup installer.iss
```

reads `/Odist` and `/FMyApp-setup` as file paths and fails with "You may
not specify more than one script filename" — an error that says nothing
about the real cause.

**Call native Windows tools from PowerShell**, not Git Bash, whenever
their arguments start with `/`. (Setting `MSYS2_ARG_CONV_EXCL=*` also
works but has to be remembered at every call site.)

## Git Bash cannot be trusted to count carriage returns

`git cat-file -p HEAD:file | grep -c $'\r'` can report hundreds of
CR-bearing lines in a blob that `od -c` and a Python byte count both show
as pure LF — and say the same about a copy that runs green on Linux CI
every day. Acting on it means rewriting commits and writing false commit
messages to fix a problem that does not exist. The reading is an artifact
of the MSYS pipe, not the blob, and it is not even consistent: the same
check with `head -c 4000` in the pipeline can give the right answer.

**Measure line endings in bytes**: pipe `git cat-file -p REV:path` into
`od -c | head` or into Python with `sys.stdin.buffer.read().count(b'\r')`.
A "CRLF in the blob" claim about a script whose Linux CI is green is
self-refuting — check the measurement before the file.

Related: MSYS path conversion rewrites a `rev:path` git argument —
`origin/dev:.github/workflows/x.yml` arrives as
`origin\dev;.github\workflows\x.yml` and git reports an ambiguous
argument. Set `MSYS_NO_PATHCONV=1` for any git command whose argument
contains a colon-separated path.

## A Git Bash path handed to the next CI step is invisible to cmd and pwsh

Say a `shell: bash` step on a Windows runner finds an SDK under `$HOME`
and writes `$HOME/sdks/example-sdk/1.2.3/bin` to `GITHUB_PATH`. `$HOME` in
Git Bash is a POSIX spelling such as `/c/Users/runner`, which only Git
Bash understands. The `cmd` and `pwsh` steps that follow get that string
on their PATH, cannot resolve it, and fail at once with `'example-sdk' is
not recognized` — after the bash step has printed its own success line
with the path it found.

`GITHUB_PATH`, `GITHUB_ENV` and `GITHUB_OUTPUT` are read by the runner,
not by Git Bash, and every later step sees the raw text.

- Anything a bash step hands to a later step on Windows goes through
  `cygpath -w` first (a no-op guard — `command -v cygpath` — keeps the
  script portable to Linux runners).
- A path that "works" inside the bash step proves nothing about the
  steps after it. Test the hand-off by reading `GITHUB_PATH` back and
  checking it for a drive letter.
- The same applies to `$HOME`, `$TMPDIR` and `mktemp -d` on the Windows
  runner: all POSIX spellings until converted.

## Shipping a DLL once is permanent: upgrades add, they never remove

A common way this goes wrong: an old copy of a system graphics or runtime
DLL, bundled in an early release, keeps crashing the app on upgraded
machines several versions after the build stopped shipping it.

- **An upgrade installer copies and overwrites. It does not delete what the
  new version no longer ships.** "We stopped shipping it" describes fresh
  installs only; every upgraded machine still has the file, forever, until
  something explicitly removes it. Inno Setup calls this `[InstallDelete]`;
  MSI has RemoveFile. Whatever the installer, the removal is a line you
  write, not a consequence of dropping the file from the payload.
- **On Windows the application directory precedes the system directory in
  the default search order**, so a leftover copy beside the executable wins
  over the one the driver or OS installs — for every module in the process
  that imports that name, not just the code that shipped it.
- **A module already mapped under a name cannot be displaced.** Opening the
  system copy by absolute path returns the module that is already loaded, so
  a "pin the right one" guard placed after something else has loaded it is a
  no-op. If a guard must win, it has to run before any other importer, which
  in a GUI app usually means before the graphics layer starts — i.e. not
  from application code at all.
- **So the fix is two-sided**: remove it in the installer for the upgrade,
  and sweep it at startup for the installs that already have it (delete, and
  fall back to renaming when the file is mapped, so the next launch is
  clean). Detection alone cannot save the current run.

The shape generalises past DLLs: any file an install once placed next to the
binary — a plugin, a config, a native library, a cached model — is still
there on every upgraded machine and is still being found first.

## A Windows-shaped path in a test fixture meets the host's path functions

A test that passes on a Windows dev box and fails on the Linux runner is
often a raw drive-letter literal fed to a function that follows the HOST's
separator: `dirname` of `C:\fixtures\demo\a.bin` on Linux is `.` (no
separator in the string), so every row folds into one group;
`p.canonicalize` lower-cases on Windows only, so `contains('example show')`
is true on one and false on the other. Production has none of the bugs.
The failures only ever show on the runner, so the round trip is a CI cycle
per guess.

**Build the fixture with the host's join** (`p.join`, `os.path.join`,
`Path(...)`), or reshape a literal once with `replaceAll(r'\', p.separator)`.
**Build the expectation with the same function the code uses** — compare
against `p.canonicalize(expected)`, never against a lower-cased literal.
Before pushing a test that touches paths, grep it for a raw drive-letter
literal; that is the whole check.

## A child process outlives the app that started it

A process started with `Process.start`, `subprocess.Popen` or `CreateProcess`
is not tied to its parent on Windows. When the app exits, whether by a normal
close, an end-task or a crash, the child keeps running. For example: the
user closes an app mid-conversion, the window goes, and the encoder it
started keeps writing the output for minutes. On the next launch a restored
job deletes that file and starts a second encoder on the same path.

- **Track the long-running children you own and kill them in the close
  path.** Keep one registry (add on start, drop on exit) and call one
  `killAll()` at shutdown, before anything slow.
- **A crash skips the close path.** Only the OS survives a crash. Create a
  job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` at startup and
  **assign children explicitly**: set `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`,
  then call `AssignProcessToJobObject` for each tracked child. Letting every
  child inherit the job also kills what must outlive the app: the updater's
  installer, a browser a link opened, a player the user launched.
- **Never track what belongs to the user.** The installer, Explorer and a
  media player are the user's, not the app's.


## A native tool's stderr chatter stops a PowerShell 5.1 script

In Windows PowerShell 5.1, redirecting a native command's stderr
(`tool … 2>&1`, or running the whole script under `*>` so its log can be
read afterwards) turns every stderr line into an error record
(`NativeCommandError`). Under `$ErrorActionPreference = 'Stop'` the first
harmless line ends the script, with a red error that reads like a build
failure — for example a build tool's informational "downloading a helper
tool" notice on stderr kills a release build that was otherwise fine.

**In a script that runs native tools, leave `$ErrorActionPreference` at
`Continue` around them (or run them through `cmd /c "tool args > log 2>&1"`)
and check `$LASTEXITCODE` after each tool.** The exit code is the failure
signal a native tool promises; stderr is where many of them put progress.

## A parsed JSON array loops once in PowerShell 5.1

`ConvertFrom-Json` in Windows PowerShell 5.1 emits a JSON array as ONE
pipeline object, so `foreach ($e in @(Get-Content m.json -Raw | ConvertFrom-Json))`
runs once, with `$e` holding the whole array: a manifest of several entries
is processed as one. PowerShell 7 unrolls the array, so the same script
passes there, and only a run under 5.1 shows the bug.

**Assign first, then loop:** `$x = Get-Content m.json -Raw | ConvertFrom-Json; foreach ($e in $x) { … }`.
Assignment enumerates the array; wrapping the pipeline in `@( )` does not.

## A backup name built from a full path overruns MAX_PATH

A backup scheme that names each copy after its original path
(`C__Users_me_…_settings.json`) works until the encoded name plus the
backup folder pass 260 characters. Then `Copy-Item` fails with "Could not
find a part of the path", which reads like a missing folder, not a long
name.

**Use short numbered names** (`0001.json`, `0002.json`) **and record the
original path in a manifest** beside them. The manifest is also what a
restore reads, so it is not extra work.
