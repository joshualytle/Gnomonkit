---
description: Activates when building or reviewing a loop, batch job or scheduled automation, or when a run succeeded but its expected side effect is missing: check counts, not exit status.
---

> Full trigger text: A scheduled job, loop or pipeline that finishes green may have done nothing — check item counts, not exit status. Activates when building or reviewing any loop, batch job, reconciliation or scheduled automation, whenever a run "succeeded" but the expected side effect is missing, and when a periodic job runs on a scheduler tick (check its run spacing against its interval).

# Green is not done

Picture a daily job meant to update a setting for each of three platforms. It
runs, reports **success**, and processes one. Two thirds of its job is skipped
silently, and nothing in the run says so.

"Status: success" means no node threw. It does not mean work happened.

## The loop-advance trap

The cause is worth knowing because it recurs in every loop-based tool (n8n,
Airflow, shell `for` over a filtered list, any map/reduce pipeline):

The advance-to-next-item step is wired to the **end of the work chain**. On
the first iteration where there is nothing to do, the filter emits zero
items, everything downstream of it is skipped — including the advance — and
the loop stops early while reporting success.

**The rule: the advance/continue step must hang off a node that emits on every
iteration, never off a filter, branch, or anything that can legitimately
produce zero items.** Put the work chain on a side branch if you must.

The general form: *a step that legitimately produces nothing must not be the
step that carries control flow forward.*

## Verify by count, not by exit code

When you build or review a scheduled job, ask: **how many items did it process,
and does that match how many it should have?**

- Check the per-node run counts on the first real execution, not just the
  final status.
- Where it is cheap, emit the count — "3 of 3 policies checked, 0 stale" is a
  reviewable claim; "success" is not.
- A job whose normal output is silence needs the count more, not less, because
  silence is indistinguishable from not running.

## Side effects after the response are invisible when they fail

A logging or bookkeeping step placed after a webhook's response node (the
right place for it — it must not delay the reply) has no one watching it.
An INSERT there that names a column that does not exist fails on every
request from the day it ships, and nobody hears: the response is already on
the wire, the error handler only sees failures that stop a run, and nothing
counts the rows. After deploying or editing any
fire-and-forget step, run one request and prove the row landed.

## Distinguish real failure from idempotent repeat

The opposite failure is a job that alarms on something expected, training the
owner to ignore the channel.

If an upstream system legitimately fires twice for one event — a cancellation
and then an expiry for the same subscription — the second call hitting an
already-final object is **success, not failure**. Make those paths tolerant,
and have the notification say which case occurred rather than reporting an
error the owner must re-diagnose every time.

Before treating an error as real, ask whether the desired end state is already
true. Often the work was done and the second attempt is proof of it.

## An empty answer is not a failed request

A common shape: a lookup queue asks a remote service about each item and, on
an empty result, returns early with a comment like "offline / transient —
retry next run". The client may well record a failure code when the
*request* fails and clear it on a successful call with zero results, but the
queue never looks. So an item that matches nothing is searched again
forever, and every feature built on "we searched and found nothing" (a
no-match flag, a needs-a-decision list) silently never fires. Unit tests
pass because the fake never fails; the bug only shows when the surface is
driven against the real service.

The rule: **model "nothing found" and "could not ask" as two different
values, and handle both explicitly.** An empty list from a call that can also
fail is ambiguous by construction. Either the call throws / returns a result
type on failure, or it exposes the last failure beside the empty list. On the
consumer side:

- empty + no failure → the definitive answer; record it (a miss row, a
  status) so the item leaves the retry set and the user sees "no match";
- empty + failure → retry later **and say why in words where the user is
  looking** (offline, no licence, rate limited), not in a log line.

Review question for any loop over a remote lookup: *what happens to an item
the service has genuinely never heard of?* If the answer is "it stays in the
queue", the ambiguity is unhandled.

## A cadence equal to the tick runs every other tick

Take a scheduler that wakes on a fixed tick and runs each job whose interval
has elapsed. A job whose interval equals the tick runs on every **second**
tick. Its last run is stamped when it finishes, a little after its tick
started, so at the next tick it is a few seconds short of due and waits a
whole extra tick. So a "every 15 minutes" job on a 15-minute tick quietly
runs every 30; its own run history shows starts two ticks apart, and nobody
looks, because every run says success.

**Compare with half a tick of slack** (`elapsed + tick/2 >= interval`), so
each job runs on the tick nearest its due time. Alternatively, stamp the
tick's scheduled time instead of the finish time and allow a small grace.
Then check the spacing of the run history against the interval. Spacing is a
count, and the only evidence that a periodic job really runs at its cadence.
