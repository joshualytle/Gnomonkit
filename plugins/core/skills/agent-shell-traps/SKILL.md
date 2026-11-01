---
description: Activates when running tests or a build through a pipe, writing a PR body, a patch or cleanup script, re-running CI, or starting long jobs: your own tooling can report false success.
---

> Full trigger text: Ways an agent's own tooling reports success it did not earn or destroys work it was asked to edit — pipelines that swallow a test suite's exit code, Python stdout that cannot encode a dash, heredocs that mangle long scripts, CI reruns that kill the live run, a tidy step that globs the working directory, patch scripts that double-apply on a re-run, and a debug binary under test that blocks its own rebuild. Activates when running a test suite or build through a pipe, writing a PR or issue body, running a long generated script, writing a cleanup step that deletes files, writing a search-and-replace patch script, re-running a cancelled CI run, or starting long jobs on a build you will need to replace.

# Traps in the agent's own tooling

None of these are bugs in the project. Each is the agent's shell,
interpreter or CI reporting something that is not true, and each has cost
real time on real projects.

## A pipe hides the exit code of what you actually ran

`some-test-command 2>&1 | tail -2` returns the exit status of `tail`,
which is always zero. The shell these tools run in does not set
`pipefail`, and a backgrounded command's completion notice carries only
the pipeline's final status.

This tends to go unnoticed for several commits: every "full suite" run
in the session reports success while the suite is in fact failing, and CI
is what catches it, not the agent.

**Never read a verdict from a pipeline's exit code.** Redirect to a file,
echo the real status, then look at the summary line:

```bash
run-the-suite > "$LOG" 2>&1; echo "exit=$?"
tail -3 "$LOG"
```

Confirm the suite's own wording ("All tests passed", "0 failed") before
calling it green.

The same trap takes any guard script, not only a suite. A guard run as
`python check.py | tail -3` shows only its footer — often a few lines of
advice it prints after a failure *and* after a pass — so the failure
surfaces later on CI, typically after a version has already been cut. A
guard's last lines often read the same either way; run it
bare and let its exit code stop the chain (`check.py && commit …`), or
redirect and print `exit=$?`, never `| tail`.

## Python's stdout cannot encode a dash on Windows

`python - <<'EOF' ... print(body) EOF > file` dies with a cp1252
`UnicodeEncodeError` on the first em-dash or arrow, **leaves an empty
file**, and a chained `gh pr edit --body-file` then wipes the PR body with
it. `gh` accepts a zero-byte body without complaint.

The usual cost is a PR description that has to be rebuilt from memory.

**Compose bodies with the file-writing tool, not through a shell
pipeline**, then pass the path to `--body-file`, then read the result back
and check its length is non-zero.

## Scripts do not survive a heredoc

Backslash escapes, quotes and `$` in a generated patch get eaten somewhere
between the tool call and the interpreter, and the failure shows up as a
syntax error in code you believe you wrote correctly.

**The test is mechanical, not a judgement call.** This rule used to read
"the threshold is low: if it has escapes and is more than a few lines,
write it", and that wording kept failing — *length* is exactly the thing
that gets misjudged, because every individual heredoc looks short enough.
Use the bright line instead:

> **If the content contains a single backslash, a regex, or triple quotes:
> write it to a file with the file-writing tool and run the file. Length is
> irrelevant. There is no short-enough exception.**

Two typical failures:

- `re.compile(r"\[(Bash|Read|...)")` inside `python - <<'PY'` reaches the
  interpreter as `missing ), unterminated subpattern`, although the pattern
  is correct as written.
- A patch script containing `"C:\\Temp"` emits
  `SyntaxWarning: invalid escape sequence '\T'`.

**A script that writes a script is the worst case**, and it is common when
patching files. That is two escaping layers: `\\` in the outer text becomes
`\` in the inner, so a pattern that is right in your source is wrong in the
file it generates. It usually still *runs*, which is what makes it
dangerous — the damage lands silently in the written file rather than in a
visible error.

Recognise the signature: **a syntax error whose message cannot be true of
the code you can see.** When the reported error is impossible given your
source, suspect the transport before re-reading the logic.

## A tidy step with an empty prefix deletes the working directory

`for f in glob.glob(partial + '*'): os.remove(f)` is a reasonable cleanup
when `partial` is a path. When the check before it found nothing and left
`partial` empty, the pattern is `*` and the loop deletes every file in the
current directory — often the test folder the script itself lives in.
Committed files come back from git; uncommitted edits do not.

- Never delete by glob unless the prefix is non-empty **and** inside the
  folder you meant (`'.app-trash' in partial`), and escape user-shaped
  names first: `glob.escape()` — a version tag in square brackets is a
  character class to `glob`.
- A cleanup that can run with nothing to clean must do nothing.

## Patch scripts must be idempotent by construction

A search-and-replace script that fails half-way is re-run. If its guard
for "already applied" is `old not in file`, and `old` is a substring of
`new` (you inserted a line after an anchor), the guard is false forever and
every re-run inserts the line again. The rule:

- Skip a replacement when the file already contains `new` the expected
  number of times; assert `old` occurs exactly once otherwise.
- Match on the file's own line ending. Read with `newline=''`, detect
  `\r\n`, and convert both `old` and `new` before comparing. A regex
  insert into a CRLF file must emit `\r\n` or the next replacement on
  that region fails to match.
- Prefer writing the script to a file over a heredoc (see above), and run
  it once from the repository root — a stray `cd` in the same shell line
  makes every relative path fail.
- **An abort leaves every later edit unapplied.** A script that asserts per
  edit stops at the first miss; the edits after it never ran, and a chained
  `&& git commit` still fires if a different command was last in the chain.
  Re-run to green before anything commits, and check `git diff --stat`
  names every file the script was meant to touch (otherwise files get
  committed unchanged under a message saying they were updated).
- **A heredoc eats one level of backslashes.** Text fed to `python - <<'EOF'`
  through the agent's Bash tool arrives with `\\` collapsed to `\`: a
  `"\\n"` search reaches Python as a real newline and matches nothing, and a
  `\\n` typed into a regex arrives as a bare newline and breaks the string
  literal. It reads as "not found" or "already applied", never as an
  error. Any script that carries a backslash is written with the file-write
  tool and run from disk (several patch runs in a row can fail this way
  before anyone sees the cause).

## A debug binary under test locks its own rebuild

The build under test holds its executable and DLLs open; the rebuild fails
until it is killed, and killing it interrupts whatever long jobs the test
instance was running (a long batch job cannot be resumed part-way through).
Finish the code changes first, rebuild, then start the long jobs — or
accept re-running them. Do not start a two-hour job on a build you know you
will replace.

## Re-running a cancelled CI run can kill the live one

A rerun re-enters the workflow's concurrency group. If a newer commit is
already running on that ref, the rerun cancels it.

A common way this goes wrong: background monitors keep re-running
superseded runs, kill the live run on the newest commit each time, then
report the cancellations they caused as new failures.

**Before re-running anything, check for a newer run on the same ref.** If
one exists, the cancellation was the concurrency rule doing its job and
the old run should stay dead. Re-run only the failed jobs of the newest
run, and keep one monitor per ref, not several.

## Do not push a fix to find out whether it works

CI is not a debugger. A speculative push re-triggers every heavy job,
occupies a runner slot for the length of a full build, and the owner
watches it churn.

A native build fix tried by pushing can take several CI round-trips that
one short local build would have collapsed into a single push.

**Reproduce locally, fix, verify locally, then push once.** Say in the
commit what you verified locally. Push without local verification only
when the failure genuinely cannot be reproduced off the runner, and say
that explicitly when you do.

## An empty check rollup right after a push is not a green one

A watcher can declare a PR "settled green" seconds after a branch is
merged into it: the rollup for the new head has no checks yet, so "zero
pending" is true and meaningless. A fresh PR's empty rollup fools it the
same way. A merge on that signal skips every check.

- Green means every EXPECTED check completed and passed. Before trusting
  a rollup, require the count the workflow normally produces (or at least
  the named checks that matter); a rollup below that count is "not started
  yet", whatever its pending count says.
- After any push to the head — including `gh pr update-branch` — assume
  the checks are being re-created and give the rollup a minute before
  reading it.
- A merge guard that counts checks (`--min-checks`) is the durable form of
  this; a poll loop is not.

## A path-filtered CI run cannot prove a change to a job it skipped

A typical case: a one-file fix to a CI helper script is opened with "this
PR's own build job is the proof". The PR's run skips that job — the path
filter sees only `scripts/ci` change — and a merge guard passes on the
small checks that did run. The real proof only comes from the next trunk
push that happens to run the job.

- Before claiming a run proves something, look at which jobs that run
  actually executed. `skipped` is not `passed`.
- When the job that matters is gated by a path filter, either touch the
  path that un-gates it, dispatch the full workflow by hand, or say plainly
  that the proof comes from the next trunk run — and watch that one.
