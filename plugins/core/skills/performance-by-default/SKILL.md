---
description: Activates when writing or reviewing a loop over files, rows or items, a per-item process spawn, a progress notifier, or when the UI freezes or a pass is slow.
---

> Full trigger text: Every pass over many items is hardware-sized and parallel, never one at a time; no synchronous filesystem or network call on the UI thread; CPU-heavy analysis runs off the UI isolate; one process spawn per item, not several; progress emissions are throttled; every loop has a cancellation point; and the app can say what is limiting it. Activates when writing or reviewing any loop over files, rows or items, a probe or process spawn per item, a progress notifier, a background pass, or when a user reports "it's slow", "the UI freezes", "CPU is low but nothing happens".

# Performance by default

Everything built should run as fast as the hardware allows without
overwhelming it. The common failure is a first-draft "refresh everything"
pass that walks every file in order, one at a time, spawning several probe
processes per file, while the UI stutters and CPU and disk sit nearly idle.

The same handful of mistakes tend to recur across a codebase, each easy to
write and hard to notice until a real data set is behind it.

## The rules

1. **Size concurrency from the machine, then clamp.** A per-item pass uses
   a bounded worker pool whose width comes from the hardware (half the
   logical cores, clamped to a sane range), never a hard-coded 1 or an
   unbounded fan-out. One shared helper (`runPool` / `hardwareConcurrency`)
   so every pass gets it for free and the number lives in one place.
2. **Never a synchronous filesystem or network call on the UI thread.**
   `existsSync`, `statSync`, `lengthSync`, `listSync` per row on a network
   share are 1–20 ms round-trips each, serialized, on the thread that
   paints. Use the async forms and yield between items. Grep for `Sync(`
   in providers and services before a release.
3. **CPU-heavy analysis leaves the UI isolate.** An offset search over two
   float lists, a hash, a duplicate grouping, a parser over a large text
   blob: run it in an isolate / worker. The interactive dialog may keep an
   inline path for progress; the batch path must not.
4. **One spawn per item.** If a step needs several facts from a file, get
   them from one process invocation (one `ffprobe -show_format
   -show_streams -of json`), not one process per fact. Process spawn cost
   dominates on small files and on network shares.
5. **Throttle progress emissions.** Four workers reporting every sub-step
   flood every watcher in the app bar. Coalesce to ~10 updates a second
   (dirty flag + timer) and emit once per completed item, not once per
   start and once per finish.
6. **Every loop has a cancellation point.** Removing the thing being
   processed (a library folder, a queue row) must stop the loop at the next
   item and drop unflushed work; a scan that keeps indexing a folder the
   user removed is a bug the user will describe as "it's still going".
7. **Say what the limiter is.** When a pass is slow for a reason the code
   knows — two worker slots busy with a transcode, a network share, a
   per-file bound — show it where the progress is shown ("Why it may be
   slow"). Users guess otherwise, and they guess "the app is broken".

## The signature to recognise

Low CPU, no disk, long wall-clock, and a stuttering UI means *serialized
waiting on the UI thread*, not heavy work. Look for `Sync(` calls in
loops, `await` per item with no pool, and process spawns per fact before
touching the algorithm.

## When this does not apply

A pass that must be strictly ordered (a migration, an event-log replay)
stays serial — but still async, still yielding, still cancellable, and it
still shows its position.

## Optimising a measurement: split the cost first, then diff every output

Take, as an example, speeding up a slow FFmpeg audio-analysis filter graph.

- **Split the wall time by stage before touching anything.** Time one run
  per filter on a real file. The stage that looks expensive is often a
  small share; the real cost can be an option nothing downstream reads
  (true-peak oversampling in a loudness meter is a common one), and the
  fix is a flag rather than a rewrite.
- **Diff every output against the baseline, not only the one you meant to
  keep.** Downmixing a side branch to mono at a low sample rate "without
  touching the loudness branch" can still move the LUFS: a format
  constraint on one branch of a split propagates to the split's input, so
  the loudness meter is silently fed the downmixed signal too. Only
  printing every number side by side, not just the one the change was for,
  catches it.
- **A metric nobody reads can change; a metric someone reads cannot.**
  Sample peak instead of true peak is acceptable only after grepping
  that no classifier and no screen uses the field — and the model's doc
  comment should say so, so the next reader does not "fix" it back.
- **Record the rejected attempt in the code**, with the reason. Otherwise
  the next optimiser tries the same downmix, sees the same speed-up and
  ships the moved LUFS.
