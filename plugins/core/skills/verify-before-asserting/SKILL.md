---
description: Activates before stating a fact about a system, claiming a fix works, citing documented behaviour, or asking the owner a question the repo or live system could answer.
---

> Full trigger text: Before stating a fact about a system — or asking the owner one — confirm it against the system. Activates whenever about to describe what an API returns, claim a fix works, cite a documented behaviour, generalise from a small sample, or write a guard/hook. Also when a documented fact and an observed one disagree, and ALWAYS before asking the owner a question that the repo, its docs, the project's memory files, or the live system could answer.

# Verify before asserting

Documentation, a handoff note written by a previous session, and the model's
own reasoning can each be confidently wrong about a live system — and each
costs far more to chase than it would have cost to test first.

## The rule

**Confirm against the source, then state it as verified.** Never assert from
memory, from a doc that can drift, or from a plausible-sounding inference.

- **Grep-answerable questions get grepped**, never put to the owner. Their
  time is not a substitute for a search. This is a hard gate, not a
  preference — see [Before you ask, search](#before-you-ask-search).
- **Third-party behaviour gets called, not recalled.** Model knowledge has a
  cutoff and API shapes change. Fetch the current docs, then still test.
- **Never report a fix as working without running it.** Not "this should now
  work" — run it and paste what happened.
- **When you state something unverified, say so explicitly.**
- **When a documented fact and an observed fact disagree, the observation
  wins** — and the disagreement gets written into the project's fact file
  with a date, so the next session doesn't re-derive it.

## Before you ask, search

**Every question to the owner asserts that the answer isn't written down.**
Make that assertion true before you send it.

A typical miss: in the middle of an incident, the agent asks the owner for a
machine's specs and which disk holds which volume, when the repo already has
an inventory doc listing exactly that — often written by an earlier session of
the same agent. Projects document things precisely so the owner is not
re-interviewed; re-asking tells them the documenting was wasted.

Before any question to the owner, check in this order:

1. **The repo** — grep it. Inventories, `docs/`, READMEs, manifests, config.
2. **The project's memory / fact files** — especially ones you wrote yourself.
3. **The live system** — query it. A running system outranks any document.
4. **Git history** — a decision already made and recorded is not an open
   question. Re-litigating it is the same failure wearing a different hat.

Only what survives all four is a real question. When you do ask, say where you
already looked, so the owner can fix the map instead of reciting it.

**The split that decides it:** *checkable* → check it, and asking is a defect.
*Owner's call* — intent, priorities, a preference, anything no artefact can
settle — → ask, and guessing is the defect. Confusing the two is the error in
both directions.

The failure mode to watch for is asking as a reflex while *feeling* diligent —
"let me confirm the specs before recommending." Confirmation you could perform
yourself is not diligence; it is offloading your work onto the person who
already did it once.

## Documentation is a hypothesis

Typical ways docs are unambiguous and still wrong:

- A vendor's own guide describes registering a loopback redirect URI without a
  port, and their dashboard rejects exactly that form as insecure.
- Reference pages still document fields that the live API has stopped
  returning. The keys can still be present with null values, which reads as
  available until you check the value.
- Endpoints silently move in a migration, and the old ones return a bare
  `403` with an empty body — indistinguishable from a permissions problem,
  which sends the investigation entirely the wrong way.

**When a 403 or an empty result looks like a permissions problem, prove it.**
OAuth token endpoints typically return the granted `scope` on refresh: print
it. Knowing the permission *was* granted redirects you from a scope hunt to
the real cause in one step.

## Writes need their own verification

Reading an API correctly does not mean you can write to it. A vendor's
published spec and the live account can disagree in several places, and the
expensive way to find out is a real customer action failing in production
rather than a test.

**Before any create/update/delete against a third-party API runs on a
customer's behalf, prove the exact payload against the real account.** Build a
throwaway path that makes one call — a temp workflow, a scratch script, a
`--dry-run` — read the actual response, then delete it. Minutes, against a
class of failure that otherwise surfaces in production.

**Read shape is not write shape.** The single most expensive assumption: a
field appearing in a `GET` response is not necessarily accepted in a `PUT`
body. For example, an `id_code` field that comes back on every object can be
rejected with `400 Unknown field` when written back — and a sync job that
echoes the object it read then fails silently on every run until something
downstream notices.

Other spec-legal payloads a live system may reject: a field the spec nests
that the API wants top-level; a field the spec does not mark required that is;
enum casing differing from the docs (`Complete`, not `complete`).

**A rejected write is not always the wrong shape.** Check the cheaper causes
first — token scope, the object being in a state that forbids the operation,
or a value required to be in the future computed as "now" and already stale by
the time the request lands. Read the error text before rewriting the body.

## Your own hypotheses count as claims

A plausible mechanism is not a finding. "This library rounds its summary
value, so recomputing it from the raw samples will be more precise" sounds
reasonable and specific, and is wrong if the raw samples were rounded to the
same grid — the recomputation returns identical values. A few minutes of
testing is cheaper than shipping a pointless refactor.

**Test the fix before recommending it, not after.**

## Small samples lie, in both directions

A rate measured on a five-item hand sample can land far from the same code's
rate over fifty items. Neither number is wrong; the first is just too small to
mean anything.

Say the sample size next to the number, and treat anything under a couple of
dozen as an indication rather than a measurement.

## Guards must be tested by planting a violation

An untested guard is decoration, and worse than nothing because it is trusted.

Two typical failures this catches, both in code that looks correct:

- A hook parses stdin as UTF-8. A BOM makes the parse throw, the handler
  treats a parse failure as "allow", and the guard silently permits
  everything it was written to block — while still appearing installed.
- A tripwire grepping for a banned library name fires on the comments
  explaining why the library was banned. A tripwire that cries wolf gets
  ignored, so match code, not prose.

**Fail open if you must, but never silently.** When a check cannot run, say so
on stderr. A guard that quietly stops guarding is the worst of both worlds.

**Write the test that plants the violation**, confirm it trips, then confirm a
clean tree stays quiet.

## What a system stores is proven by the write path and a live count

A data inventory audited only against a schema dump and the repo's copies of
the workflows can still be wrong. The schema says what a column *could* hold;
only the live INSERT plus `SELECT COUNT(*)` says what it does.

- A column that "exists but is never written" and a column with thousands of
  rows are different facts. Count before you write either.
- Read the write step on the live system, not the repo's mirror of it. An
  INSERT that names a column which does not exist can fail silently on every
  request for months while the mirror looks fine.
- A retention rule is a graph question. "Purged after 90 days" may be true
  only for rows with a flag such as `processed = 1`; whether every path sets
  that flag takes tracing the workflow, not reading the purge query.
- Run history is storage. An automation engine keeps the payloads it
  received per execution, independent of what it writes to a table.

## The owner's claims about their own system are hypotheses too

An owner can be sure a log table does not store some identifier when every
row in fact holds it. Verify their assertions exactly as you verify a document's, and report
the finding with the number — then either make the system match their
intent (when nothing depends on it) or put the decision to them. Neither
writing around it nor silently correcting the document is acceptable.

## A value another program reads has a TYPE, not just a name

Picture an app that writes a setting into the registry so a third-party
updater stops asking the user a question. The name is right, the value is
right, the write succeeds, and the updater asks anyway on every launch. The
setting was written as a `REG_DWORD`; the updater stores all of its settings
as `REG_SZ` and its reader asks the registry for a string, so the value is
invisible to it. The app then rewrites the wrong type on every launch, so
nothing ever converges.

Whenever you write into a store that a DIFFERENT program reads — a registry
hive, a plist, an ini, a shared JSON or sqlite file, an env file — the
contract includes the encoding:

- **Find a value that program wrote itself and match it.** It is the
  specification. In a case like the one above, an older key the updater
  wrote itself would show the user's own answer stored as the string `"1"`,
  next to the app's unreadable `DWORD 1`.
- **Dump the type, not just the value.** Most tooling prints `1` for the
  string `"1"` and the number `1` alike, so a casual check confirms the bug
  is absent. Ask for the type explicitly (`GetValueKind`, `reg query /v`,
  `plutil -p`, `typeof`).
- **A write that returns success proves nothing about the read.** The only
  evidence that counts is the other program's behaviour changing.

Ordering is part of the same contract: the value has to be in place before
the other program reads it. A setting written just after the call that
initialises the other program — when that call is the one that reads it —
arrives a launch late even with the correct type.
