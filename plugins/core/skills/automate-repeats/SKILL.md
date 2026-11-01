---
name: automate-repeats
description: Activates when the user asks for the same kind of thing again, you retype a command sequence from earlier sessions, or they ask what could be scripted or made a skill.
---

> Full trigger text: Activates when a multi-step process comes round again - the user asks for the same kind of thing they asked for before ("do the release again", "same as last time", "run the usual checks", "set this one up like the others"), you are about to type a command sequence you have typed in earlier sessions, you catch yourself redoing a chain of steps by hand for the second time in a session, or the user asks what could be automated, scripted or turned into a skill. Ask "how do I automate this", not "how do I do this".

# Automate what repeats

The second time a process comes up, doing it by hand again is the expensive choice. Every manual
run re-spends the tokens to rediscover the steps, and each run can drift from the last. A script
does the same thing every time for the cost of one call, and a skill gives the steps a name.

## The rule

When a multi-step process recurs, **propose the script or skill before doing it by hand again**,
then build it, run the current task through it, and say where it lives. Ask "how do I automate
this", not "how do I do this".

It recurs when:

- the user asks for the same kind of thing again (same verbs, same objects, same project), or says
  "again", "like last time", "the usual";
- the same chain of three or more commands shows up across sessions (build, test, commit, push,
  open a PR; fetch, branch, open a worktree; export, convert, upload);
- you are about to paste a long command you have composed before.

## Which shape

| Recurring thing | Build |
|---|---|
| A fixed chain of commands with a few inputs | a script in the repo's `scripts/` (or the overlay's, when it spans repos) with flags for the inputs and `--dry-run` when it changes anything |
| A judgement-heavy routine (which checks, in what order, what to look for) | a skill: when it applies, the steps, and a script for the mechanical part |
| One long command | a script or an alias, named for what it does |
| The same question about the same data | a script that prints the answer |

Before building, grep for an existing script or skill that already does it (`before-you-build`);
extending one beats writing a second. Put it where the process lives: one repo's process in that
repo, a cross-repo process in the shared core or your overlay (`central-rules-sync`).

Do not automate a one-off, a process the user is still changing every time, or something that needs
the user's decision at each step. Then say why you did it by hand.

## Find the candidates

`detect_repeats.py` sits beside this file. It reads the local Claude Code transcripts (read-only,
standard library, nothing over the network) and ranks what was done by hand in several sessions:

```text
python detect_repeats.py                         # last 30 days, top 10 of each kind
python detect_repeats.py --since 90 --project shop --json <scratch>/repeats.json
```

- **Command sequences**: runs of three or more shell steps (and MCP tool calls) seen in at least
  three sessions, each step reduced to its program and subcommand; overlapping windows of one
  process are folded under its highest-ranked form.
- **Recurring commands**: one long command that comes back with only its paths, numbers and quoted
  text changed.
- **Recurring requests**: typed prompts whose content words overlap across sessions.

Counts are MEASURED; tokens are ESTIMATED (characters / 4) and cover each occurrence's tool inputs
and results (for a request, everything up to the next prompt). Treat the list as leads: check one
before building for it, and leave look-around steps (`ls`, `git status`, ...) out unless asked
(`--keep-inspection`).

If a history search tool is available (a MemPalace search, for example), use it as a second source:
search for the candidate's request to see how it was handled each time. The script never needs it.
