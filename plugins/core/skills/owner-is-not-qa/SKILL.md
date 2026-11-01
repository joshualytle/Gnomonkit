---
description: Activates when a feature or screen is about to be handed over, you write 'please test', before pushing to a PR branch, or when a model is being calibrated.
---

> Full trigger text: A new surface reaches the owner only after the agent has driven every control on it, walked the product's own UX checklist, and added a scripted regression check — the owner tests the product, not the agent's work. The regression scripts are mapped to feature areas and the mapped subset runs before any push. Synthetic fixtures prove a mechanism; the owner's own data proves the numbers. Activates when a feature or screen is about to be handed over, when writing "please test" for the owner, when a test step could be run by the agent instead, before pushing to a PR branch, when a model or estimate is being calibrated, and when a batch of owner feedback comes back with more than one basic defect on one surface.

# The owner is not your QA

A common way this goes wrong: the agent ships a new screen, takes one
screenshot of the happy path, writes "please test", and the owner's first
minutes on it turn up a background job that never starts until the screen
is opened, a list that shows rows with nothing to act on, an editor that is
a row of unformatted checkboxes, and a field that expects a raw code where
a picker belonged. If the agent has tools to drive the UI in dev, handing
over half-built work for the owner to find this is the failure.

Defects like these are not subtle. Each surfaces in a minute of clicking.
A screenshot proves the page rendered; it proves nothing else.

## What "tested" means before handover

1. **Every control, not the happy path.** Each button, picker, row action,
   toggle and text field is driven through the app's automation loop —
   including the empty state, the error state, and the state after the
   action completes. One screenshot of a populated page is the *start*.
2. **The product's own checklist, walked.** If the project has a
   "think like the user" list (for example in its CLAUDE.md: does the
   label match the scope, does work survive leaving the page, is failure
   said in words, is the disabled thing explained…), walk it against the
   new surface and fix what fails before the handover, not after the
   owner reports it.
3. **A scripted check joins the regression battery.** Whatever was driven
   by hand is captured as a script that asserts on real state — rows in
   the database, bytes on disk, text on screen — so the next change to
   that surface cannot break it silently. The fixtures it needs live with
   the battery, so the check runs on every later version that touches
   that component.
4. **Anything the agent can verify, the agent verifies.** A file
   operation's effect on a later scan, a converted file's output streams,
   whether a row selects — these are checks, not owner tasks. If the owner
   has to ask "can you test this and just report?", the agent should have
   pre-empted it.

## Map features to regression surfaces; run the map before a push

When a change touches a feature, the tests mapped to that feature run
again before anything is pushed, even to a development branch.

- A map file in the battery folder pairs path globs (feature areas) with
  the surface scripts that must pass when those paths change. A schema or
  app-shell change maps to the whole battery.
- The runner takes `--changed <base>`: it diffs the base plus the working
  tree, runs the mapped surfaces, and **lists every changed source file that
  maps to nothing**. An unmapped file is a map bug, fixed in the same
  commit; `--strict` fails on it.
- Areas that have no surface yet are still mapped — to a surface name that
  does not exist — so every run reports "no script yet" for them instead of
  passing silently. The gap is visible until someone closes it.
- Run the mapped subset before pushing to a PR branch and always before
  marking a PR ready; put the summary line in the PR body. A red surface
  blocks the push. A full run is also how a surface that went red on an
  unnoticed side effect, such as a layout shift, gets caught.

## Synthetic fixtures prove the mechanism; the owner's data proves the numbers

A small synthetic fixture proves that a pipeline maps its inputs, tags its
outputs and handles cancel. It cannot tell you that the real inputs behave
differently from what the model assumes — say, a compression setting that
yields far more output than predicted on one kind of content. An estimate
tuned only on fixtures can confidently promise a saving on a real file
that comes out larger than its source.

- When a feature carries a model, threshold or estimate, index the owner's
  real folders **read-only** into the QA instance (never their live
  database) and run the feature on a few of their files. Delete every
  output you wrote beside their files before reporting.
- Write the measured numbers into the design doc as the seed and the
  reason for each constant. A constant with no measurement behind it is a
  guess the owner will find.
- Report estimate versus actual per file, and the verdict-level outcome
  ("would not save space" was right / wrong), not only the model's error.

## What the owner's time is for

Judgement the agent cannot make: whether a workflow *feels* right, whether
copy reads like a person wrote it, whether a default matches how they
actually work, which of two designs to keep. Handing them a screen with
dead controls spends that time on bug triage instead, and — worse — it
teaches them the agent's "done" cannot be trusted.

## The handover message

Say what was driven, what the checklist turned up and how it was fixed,
and where the regression script lives. A test list for the owner is for
the judgement calls above, not for finding out whether the buttons work.

## The owner's test list holds only what the agent cannot run

A validation page written from the feature list, rather than from what the
agent can and cannot reach, fills up with items the owner will skip with
the same note: the agent could have driven and measured this itself. Most
such items are reachable with the automation loop, the regression battery,
a fixture or a drive the agent can dismount.

The validation page is two things, and the boundary is strict:

- **Part 1, the owner's list, holds only what the agent genuinely cannot
  drive**: a real credential or licence state that would touch their
  installed copy, their own server, a phone or TV, a second OS user, a
  reinstall they do not want yet. Each item says in one line *why it is
  theirs*. If the agent could reach it — through the app's automation
  loop, the battery, the QA corpus, a fixture, a drive it can dismount,
  another machine — it is not theirs: the agent drives it and reports in
  Part 2 with what it saw.
- **Part 1 is split into "General"** — recurring checks that survive
  across builds, such as sign-in or confirmation prompts, carrying their
  earlier results — **and one section per version** for that build's own
  items.
- **A test for a feature that is not in the build is not on the page.**
  Before publishing, cross-check every carried-over item against what the
  branch actually contains and drop or mark the ones that cannot pass; a
  test for a feature gated off in this build only comes back as a failure.
- **Results reach the agent only from the published page.** A copy saved
  beside the installer keeps its results in that browser alone; read the
  page's results before asking for feedback, and say on the local copy
  where to enter them.

## When this does not apply

A surface the agent genuinely cannot reach with the tools it has — say so
explicitly in the handover ("I could not drive X; it is untested") rather
than implying it was verified. Silence about coverage is the failure this
skill exists to stop.
