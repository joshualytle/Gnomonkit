# Gnomonkit Bench

Does an agent setup apply the rules it already has, at the moment they matter?

Bench runs the same synthetic scenarios under different setups and scores what each run **did**
(files written, commands run, what hooks injected and when), never what the agent said it did.
Design: [docs/specs/2026-10-21-bench-design.md](../docs/specs/2026-10-21-bench-design.md).

## The comparison

| Setup | What loads | Answers |
|---|---|---|
| `bare` | nothing | does the model do it on its own? |
| `written` | the rule as a skill or CLAUDE.md line, no hooks | is writing the rule down enough? |
| `gnomonkit` | Gnomonkit core, same rule text | does making it fire help? |
| `custom` | plugins you name | how does my stack do? (container mode only) |

`written` versus `gnomonkit` is the headline: the rule text is identical, so the difference is
whether it arrives at the right moment.

## The latest round

Sonnet, October 2026, every scenario three times, container mode. Positive scenarios only; a run
passes on what it did.

| family (runs per arm) | bare | written | gnomonkit |
|---|---|---|---|
| interjection (18) | 0/18 | 7/18 | 14/18 |
| skills (30) | 12/30 | 22/30 | 17/30 |
| guards (9) | 9/9 | 8/9 | 9/9 |
| precedent (24) | 3/24 | 24/24 | no arm yet |

Interjection is the hooks' case: the written rule gets the old answer used well under half the
time, the hook that delivers it at the right moment most of the time. On skills, writing the rules
down is the gain; in this round the hooks took five passes off the written text, and three runs per
scenario cannot say whether that holds. Guards are too easy to tell anything yet. Precedent has no
`gnomonkit` arm because the hooks play no part in following a CLAUDE.md rule.

## Scenario families

| Family | Checks |
|---|---|
| skills | a core skill's situation comes up and its behaviour happens; a quiet control must stay quiet |
| precedent | a project rule in CLAUDE.md is followed without being mentioned |
| guards | enforcement: deletes come after a listing, uncommitted work survives a reset, escapes survive a shell write; text that only *mentions* a delete is not blocked |
| interjection | predict-then-confirm: the answer the user already gave is only in their history; the run uses it, or asks with it as the guess, instead of asking cold |

Every family has negative controls, and plain `bare` runs are expected to fail the positives. A
scenario that `bare` passes most of the time is flagged "too easy" in the report.

## Running it

```text
python -m bench dry-run                                   # no Claude; validates and builds every scenario
python -m bench run --mode quick --allow-host             # 1 rep, gnomonkit only (~17 runs)
python -m bench run --mode full --allow-host --yes        # 3 reps x bare/written/gnomonkit
python -m bench report <round>/results.json               # HTML page + README block
python -m bench compare <old>/results.json <new>/results.json
python -m bench gate <round>/results.json --family skills --arm gnomonkit   # exit 0 ship, 1 hold
```

A full round prints its run count and cost estimate and will not start without `--yes`. It stops
scheduling new runs at 1.5x the estimate (`--max-cost` to change).

## Safety: what a run can and cannot touch

Each run is a real Claude Code session that can execute commands. Bench limits what it can reach:

- **No sandbox on native Windows.** Claude Code's Bash sandbox runs only on macOS, Linux and WSL2.
  On a Windows host each run executes as you, so Bench refuses to run there without `--allow-host`.
  Prefer `--container`: each session runs in a throwaway Linux container as a non-root user with no
  capabilities, seeing only its own run directory. The container is the sandbox; Claude Code's own
  sandbox cannot start under those restrictions, and loosening them to allow it would weaken the
  container. `python -m bench dry-run --container` builds the image and checks the lockdown.
  The container's network is not restricted (it needs the Claude API).
- **Your environment is not passed through.** A run gets an allowlist: `PATH`, a few Windows system
  variables, its own temp, home and Claude config directories, and the API key only when the round
  is API-billed. Tokens and passphrases in your shell never reach a run, its hooks, or anything the
  model executes.
- **Your Claude config is not shared, and neither is a run's.** Every run gets its own
  `CLAUDE_CONFIG_DIR`, home, temp and git config, with auto memory and transcript history off. Your
  personal CLAUDE.md and memory are neither read nor written, and nothing one run leaves behind
  reaches the next.
- **Output stays out of your projects.** Results default to your user cache directory. Bench refuses
  an output directory inside a git work tree, or under any root you list in `GNOMONKIT_BENCH_FORBID`
  (`os.pathsep`-separated). Set it to the folder your session-mining tools read from.
- **Nothing is left running.** A run that times out is killed with every process it started, by PID.
- **Reports carry counts, not text.** `report.html` and the README block are built from k/n, cost
  and time only; error tails are stored with your home and run paths replaced by placeholders.
- **Third-party plugins run only in container mode.**

## Billing

`claude -p` uses an API key when `ANTHROPIC_API_KEY` is set. Otherwise it uses a subscription login:
sign in once with `CLAUDE_CONFIG_DIR` set to the `auth` folder next to your round directories (by
default `<user cache>/gnomonkit/bench/auth`) and run `claude` to log in. Each run then gets a copy of
the login file only; nothing else in that folder travels. Each record says which billing path it used.
Publish rounds billed to an API key so the cost figures are real charges.

The `custom` arm runs third-party plugin code, which can read whatever login it runs under. It
requires API billing: use a dedicated key with a low spending limit for custom-arm rounds.
