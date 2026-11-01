---
description: Activates when adding a background job, queue, scheduler or progress indicator, or when a user reports 'nothing happened' or a job that never seems to run.
---

> Full trigger text: Rules for any app that does work the user did not click for — indexing, probing, analysis, queues. Every long-running job reports into one visible feed with a step and a position; work starts on its own after data changes and on a schedule, never on a page visit; a job that "never seems to run" is checked for registration on the normal startup path before its code is touched; cancelled or interrupted work leaves nothing where the user looks. Activates when adding a background job, a queue, a scheduler, a progress indicator, an after-import pipeline, or when a user reports "nothing happened" / "I don't know what it's doing". Also covers phone background work: the OS registration outside the code, secure-storage reads while the device is locked, and OS-capped long-running services.

# Background work is visible, automatic, and survives

Apps tend to grow long-running jobs one at a time, each with its own
progress shape, some living inside a widget's state, and nothing that lists
them all. Users are left guessing what is running, what step it is on, and
whether anything happened at all. A quieter failure hides behind that: a
hook that runs after-import jobs can end up registered only on an unusual
startup branch (recovery, first run), so on a normal start nothing runs,
and page visits or a timer that happen to trigger the same work hide the
gap for a long time.

## One feed, every job

1. **Every job reports into one place** — a registry any job can call
   (`queue` / `start` / `step` / `finish`) plus a feed that composes it with
   the notifiers that already exist. Title, current step, position
   ("12 of 300"), state, and the result or error when it finishes. A job
   with no state of its own gets a registry entry; a job with state is
   composed, not rewritten.
2. **The feed has a door on every screen** — one control in the same place
   everywhere (top-right action row), with a running count, opening a panel
   of Running / Queued / Recent. A word beside the icon: users should not
   guess what a spinning glyph means, and a scripted test can find text.
3. **Recent survives a restart.** Persist finished entries (capped) so the
   panel can say what ran last and when. A job that was running at shutdown
   never comes back as running.
4. **Skipped steps say why.** "nothing new", "off in Settings → X",
   "offline mode" — a step that does not apply stays in the list with its
   reason instead of vanishing. Silence is what makes users guess.

## Work starts on its own

5. **A data change is the trigger; a page visit never is.** After an
   import or scan, the pipeline that the product needs (compliance,
   duplicates, missing files, per-file facts, estimates, analysis) runs from
   the import's completion hook. A scheduled pass covers the backlog. Opening
   a tab may show results; it never starts the work.
6. **Queue the plan before the first step runs.** List every step as queued
   up front so the panel shows what is coming, then tick through it. Order
   the steps so each later one sees the state the earlier ones produced
   (moved/missing files before per-file facts before estimates).
7. **Throttle at one gate.** Heavy steps share a small app-wide slot count
   with the user's own queues (transcodes, generation), and work through
   files one at a time with an event-loop yield between them. Nothing
   spawns more than one external process per step. Not overwhelming the
   machine is a design property, not a setting.
8. **Work survives leaving the page.** The loop lives in a provider or
   service; a widget only mirrors it. A loop in widget state dies with the
   widget, and the user learns the feature is unreliable.

## When a job "never runs"

9. **Check registration on the normal path first.** Before touching the
   job's code, confirm the hook or scheduler entry is installed on the
   startup path a real user takes — not only in a recovery, first-run, or
   debug branch. A scripted surface that drives the trigger and asserts on
   the persisted result is the only test that catches this; unit tests of
   the job body pass either way.
10. **A trigger the test never checks can pass on a no-op.** When a test
    kicks a job through an API or tool, read and assert the trigger's return
    value (row id, item count, error). If the call passes a wrong argument
    name and the tool throws, later checks can still pass on stale page
    text, and a real bug like item 9 stays hidden.

## Interrupted work leaves nothing behind

11. **Cancel or failure cleans up where the user looks.** A partial output
    beside the original gets indexed as a version and listed by a media
    server; a zero-byte file gets indexed as a real item. Move partials to
    the product's trash (recoverable, scan-excluded) and delete empties.
12. **Work running at shutdown is re-queued, not failed.** Persisted queue
    rows that were running when the app closed come back as queued.

## On a phone, the code is half the feature

A phone background task can have code, tests, a settings screen and a
registration call on the startup path, which passes item 9, and still never
do anything useful on a device, for reasons no unit test can see.

13. **The OS registration lives outside the code, and a miss is silent.** An
    iOS `BGTaskScheduler` identifier must be in `Info.plist`
    (`BGTaskSchedulerPermittedIdentifiers`, plus the `fetch` or `processing`
    background mode). Its launch handler must be registered before
    `didFinishLaunching` returns, so it cannot wait for the first
    schedule call. The headless engine or runtime the task runs in needs its
    plugins registered separately. Android release builds need every
    permission the debug manifest quietly supplied, `INTERNET` above all.
    The scheduling call "succeeds" or logs a warning without any of these.
    List them in the PR, and check them on a device before saying it runs.
14. **A notification is a permission, not a call.** Android 13+ and iOS drop
    notifications silently until the user is asked and agrees. Ask once, at
    a moment with context (after the first account exists, never at first
    launch), and show a plain warning where the user looks when it is off.
15. **Secure storage the background reads must be readable while locked.**
    The iOS keychain default is "when unlocked", and background work runs
    while the phone is locked. Use after-first-unlock, this-device-only for
    exactly the items the background reads. An existing item keeps the class
    it was created with. A write under the new class misses it on lookup and
    then collides on add, so migrate by read, delete, rewrite, in the
    foreground, once.
16. **Stop an OS-capped service yourself, and keep the poll underneath.**
    Android 15 stops a `dataSync` foreground service after six hours in 24.
    A service stopped that way cannot restart from the background until the
    app is opened. End the session yourself, before the cap, in a window
    measured from the last app open, and record why. Never cancel the
    periodic poll on the service's behalf. A kill leaves no trace, so show a
    stale heartbeat with no recorded end as "the system stopped it". Also
    give the service no network constraint: with one, every network blip
    stops the service, and the scheduler restarts it from the background as
    a plain, soon-killed worker.

## When this does not apply

A one-shot action the user just clicked and watches to completion (a
rename of ten files, a dialog's own spinner) does not need the feed — but
it still needs progress the user can trust, and it still must not die when
the dialog closes if it writes to disk.
