---
description: Activates when a driver, GPU backend, acceleration path, codec or vendor runtime is about to be disabled, blocked, swapped or dropped, even by an automatic fallback.
---

# Turning off a hardware path is the owner's call, not a crash's

A backend that crashes gets blocked. A probe that fails drops a candidate. A
fallback quietly selects something slower. Each of those is a reasonable local
decision, and together they amount to **deciding which hardware the product
supports** — a product decision nobody made on purpose.

The failure is not the block. The failure is that the block becomes permanent
and silent, and the agent moves on.

## How it goes wrong

A typical sequence: a GPU backend crashes the process once. The agent builds a
crash-loop breaker, blocks that backend, falls back to the CPU, and moves on.
Correct in the moment.

Nobody re-tests it. The block outlives the bug (an unrelated change fixes the
real cause), and every user on that vendor's hardware silently runs on the
CPU. The owner finds out by accident, and would have pushed back at the time
had anyone said so.

The agent never decides to drop GPU support. It just never decides not to.

## The rule

**Say it, in the turn it happens.** Any of these reaches the owner as a
decision, not as a log line:

- Disabling, blocking, or deprecating a driver, backend, or acceleration path.
- Dropping a build target, a vendor runtime, or a codec.
- Choosing one hardware path over another — including "it crashed, so we use
  the slow one now".
- A fallback that will, in practice, be permanent.

Say what was turned off, what the user loses, and what would turn it back on.

## Automatic blocks need an expiry, not just a switch

A guard that blocks on crash must record **what it was a verdict about** — the
version of the binaries, the driver, the build. When that changes, the verdict
does not carry over. Otherwise the fix reaches everyone *except* the people who
hit the bug, which is the exact inversion of what you want.

A user-facing "try it again" button is not sufficient. It requires the user to
know a thing was turned off, and the whole problem is that nobody was told.

## Re-test before you conclude, and measure before you recommend

- A crash that was diagnosed once is not diagnosed forever. Before treating a
  path as dead, reproduce it **on current binaries** — the cause may already be
  gone.
- "It loads" is not "it is fast", and "it is named X" is not "it is X". Check
  what a library actually links against and time the real workload. A backend
  named after a GPU API that silently runs on the CPU will pass every test that
  only asks whether it started.

## Why this is not over-caution

Hardware support is something users choose the product for, and something
competitors are compared on. It is also the kind of decision that is invisible
once made: nobody files a bug saying "this is running on the CPU", they just
find it slow and assume that is what it costs.
