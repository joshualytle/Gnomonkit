---
name: learned-corrections
description: Activates when an estimate, ratio or prediction is refined from the user's own history of runs or outcomes.
---

# Learned corrections

A typical failure: an output-size estimate is refined from the machine's
completed jobs. The same test file has been processed dozens of times, so
those repeats alone set the estimate for every other file in a large
library and collapse the shown range to a single value. The maths is right
and the answer is absurd.

## The rules

- **One source, one vote.** Repeated runs on the same input are one data
  point about that input, not N about the world. Dedupe by source before
  counting samples; keep the latest.
- **Refine the prior, do not replace it.** With n own samples and a prior
  worth k, weight the learned value n / (n + k). One sample moves the
  number a little; hundreds own it. A small k (a few samples' worth) suits
  a prior that is a sensible default rather than a measurement.
- **Show the measured spread, not a fixed band.** If the corpus says
  p25–p75 is 0.3–0.5, say "usually 0.3–0.5 of the source", not
  "±25%". A fixed band claims a precision nothing supports.
- **Drive it on real history, not on the unit test.** A unit test with
  tidy synthetic samples passes every time; real job history, with its
  repeats and skew, is what exposes both problems. Any learned correction
  gets one look at the numbers it produces on a real data set before it
  ships.

## Why it needs a rule

Learning from the user's own data sounds self-evidently better than a
shipped constant, so the agent reaches for "prefer learned" without asking
how many independent samples the learned value rests on. The failure is
not visible in tests and is only visible on a library where one file has
been processed many times — which is every real library.

## Related

`verify-before-asserting`, `owner-is-not-qa`.
