---
description: Activates when writing or tuning a scanner, linter, regex matcher, security rule or health check, and before reporting a detector's counts as fact.
---

> Full trigger text: A detector you wrote or configured must be calibrated against known-positive AND known-negative ground truth before its output is reported as fact - a scanner that invents findings is worse than one that misses them. Activates when writing or tuning a secret scanner, linter, regex matcher, security rule, or health check, and before reporting counts from any detector as if they were real.

# Detector calibration

## The rule

**Running a detector is not verifying a finding.** Before reporting what a
scanner found, confirm the detector is right about a case you already know the
answer to — in *both* directions.

[[verify-before-asserting]] says confirm a fact against the system. This is the
case that rule does not cover: the system was consulted, it answered
confidently, and **the detector itself was wrong**.

## Why a false positive is worse than a miss

A miss leaves you where you started. An invented finding actively costs you:

- It sends someone to do expensive, irreversible work — rotating credentials
  that never existed, reverting a good change, chasing a bug that is not there.
- It **poisons every real finding beside it.** Once one item in a report is
  known bogus, the other forty stop being trusted, and the genuine one gets
  skipped.
- It is self-concealing. Nobody investigates a finding that "looks right."

A concrete way this happens: a secret scanner reports several private keys in
a corpus, the count gets repeated as grounds for rotating keys, and there are
none. A complete-block PEM pattern that uses `[\s\S]*?` for the key body
matches anything — including quotes and commas — so a `BEGIN` marker in one
JSON value can match an `END` marker in a *different* value, and the
"finding" is the structure in between. Ground truth is one grep away: *are
there any PEM markers followed by an actual base64 body?*

## Calibrate in both directions

A test suite for a detector needs two halves, and the second is the one people
skip:

| Half | Asserts | Failure it catches |
|---|---|---|
| **Known positives** | real instances ARE flagged | the detector misses |
| **Known negatives** | benign lookalikes are NOT flagged | the detector invents |

A detector that flags everything passes the first half completely and is
worthless. For a secret scanner, the negative half means: prose *about*
credentials, documentation placeholders, git SHAs, image digests, UUIDs,
template variables. Pin them as must-survive.

## Ground-truth the corpus, not just the fixtures

Fixtures prove the pattern works on what you imagined. They do not prove your
count is real.

Before reporting a number from a live scan, ask: **what would the raw data look
like if this finding were true, and is it there?**

```powershell
# The detector says N private keys. Does the corpus contain a key SHAPE at all?
Select-String -Path <corpus> -Pattern '-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]{40,}' -AllMatches
```

If the independent check disagrees with the detector, the detector is wrong
until proven otherwise. It is the thing you just built; the corpus is not.

## Report findings with their confidence

Do not flatten a report into one number. Separate the strongly-typed matches —
those anchored on a structural marker — from broad heuristics, and say which is
which:

> 100 distinct matches: 60 strongly-typed (cloud key IDs, join tokens, JWTs);
> the remaining 40 are a deliberately broad `password: <12+ chars>` rule and
> **will** include false positives — read them before acting.

That framing (numbers illustrative) lets the reader act on the first group
immediately and triage the second, instead of treating all of them as equally
real or dismissing all of them.

## Applies to detectors you did not write

Trivy, Nuclei, gitleaks, a linter rule, an alert expression. Before escalating a
finding, confirm the thing it describes exists. A CVE reported against a package
you ship is not the same as a reachable vulnerability, and an alert that has
fired for a week without a corresponding symptom is usually a broken rule rather
than a week-long outage.

## When NOT to use this

A detector with an established track record on a corpus you have already
calibrated against. The cost is meant to be paid once per detector, not once per
run — and re-paid whenever the rules change.
