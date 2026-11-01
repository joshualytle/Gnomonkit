---
description: Activates when adding any notification (Slack, email, pager, webhook), an error handler or a scheduled job that can fail, or when alerts land in the wrong place.
---

> Full trigger text: Every alert needs a destination scoped to the product and the environment it belongs to, created as part of shipping the automation, and proven by sending one real message through the real credential. Activates when adding any notification (Slack, email, pager, webhook), wiring an error handler, choosing or reusing a channel, standing up a scheduled job that can fail, or when the owner says alerts are in the wrong place, are being ignored, or "why didn't this page me".

# An alert nobody reads is not an alert

Two ways alerting fails silently, and both look fine from the code:

1. **It goes somewhere nobody is looking for it** — another product's
   channel, a shared firehose, an inbox rule.
2. **It never arrives at all** — the app is not in the channel, the token
   lost a scope, the address bounces. The config looks correct, and you
   find out on the day it mattered.

A common way the first one happens: new workflows for one product get pointed
at whatever ops channel already exists, which belongs to a *different*
product. The first real failure then lands between that other product's
routine digests and its own unrelated noise, and nobody reads it, because
nobody looking at that channel owns the thing that broke.

## The destination is part of the feature

**Create the channel when you build the automation, not later.** Borrowing an
existing channel "for now" is permanent — nothing ever comes back to move it,
because moving it is never the most urgent thing.

A comment is not a mechanism. A note beside the channel setting saying
"change this once the right channel exists" can be entirely correct, sit
right beside the wrong value, and change nothing indefinitely. If you find
yourself writing "change this later", do the thing instead.

## Scope every destination two ways

- **By product.** One product's alerts never share a channel with another's.
  The cost is not tidiness — it is that a real failure becomes
  indistinguishable from someone else's routine digest, so the channel
  trains people to skim.
- **By environment.** Production never shares with staging, beta or dev.
  Non-production is noisy by design; mixing them teaches the reader that a
  red message is usually nothing, which is precisely the reflex you cannot
  afford in production.

Name the destination for what it carries (`<product>-ops`,
`<product>-ops-staging`), and put the product name in the message text too —
a forwarded or quoted alert loses its channel context.

## Prove it can deliver

**A configured destination is not evidence of delivery.** Send one real
message through the real credential and confirm it arrives.

A typical failure: workflows are repointed to a new channel, and the first
genuine alert fails with `not_in_channel` because the app was never invited.
Every alert would vanish with an error that only appears in an execution log
nobody watches. An invite that is believed done can still be missing;
listing the channel's members is what shows whether the app is there. Read
the membership, or send the test — do not accept "I invited it" as the check.

Specific traps worth knowing:

- Slack apps must be **members** of a channel to post to it. Creating a
  channel does not add the app, and inviting the wrong one looks identical
  until a message fails.
- The check has to run **as the automation**, not as you. Your own client
  posting successfully proves nothing about the bot's token.
- Do this **after any change to the destination**, including a rename.

## The failure handler is the alert you must test first

A shared error workflow is the one component whose breakage is definitionally
silent: when it is broken, the way you find out is by not being told
anything. Exercise it deliberately — force a failure on a throwaway job and
confirm the alert lands — before trusting it to cover everything else.

## Do not page for the retryable

An alert that fires on a transient fault trains the reader to ignore alerts.
A job that polls a vendor API many times a day will see an occasional `503`
as a certainty, not an incident; paging on the first one teaches the reader
to ignore the page.

Set retry on the call (a few attempts, seconds apart) and alert only when the
retries are exhausted. Retry is not the same as hiding the failure: the alert
still fires if the outage is real, it just stops firing for a blip. Where the
distinction matters, say which it was in the message.

## What belongs in the message

An alert is read by someone with no context, often on a phone:

- **Which product and environment**, in the text.
- **What broke**, in a plain sentence — not just an exception class.
- **What it means for a customer or the system**, if anything. "You may need
  to finish provisioning by hand" is worth more than a stack trace.
- **A link to the run**, so the detail is one click away rather than pasted.
- **Whether anything is required of the reader.** An alert that needs no
  action should say so; otherwise every one costs a triage.

## When NOT to use this

A single-repo hobby project with one channel and one environment. The
delivery check (send one real message) still applies the first time you wire
it up — that one is cheap everywhere.
