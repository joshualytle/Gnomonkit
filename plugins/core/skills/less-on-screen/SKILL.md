---
name: less-on-screen
description: Activates when adding or changing a list, group, header, section or page a person reads: group means fold, match existing surfaces, show only what needs a decision.
---

# Less on screen

A common way this goes wrong: a list gains a header per group, and the change
is tested, merged and shipped. Every other grouped list in the app already
folds, so the first question anyone asks is why these groups cannot be
collapsed. The headers are correct and still wrong, because they add structure
without the control that structure implies.

## The rules

- **Grouping implies folding.** A list that gains headers gains a way to
  close each group, in the same change. A header that cannot be clicked is
  a label, not a group.
- **Match the surface that already does this job.** Before shipping a new
  list, panel, dialog or row, find the existing one closest to it and copy
  its behaviour — how it folds, filters, sorts, selects, confirms. A new
  pattern beside an old one is a design flaw even when both work on their
  own; the person has to learn two.
- **Show what needs a decision; fold what does not.** The default view is
  the one that lets someone act. Rows with nothing to do sit behind a
  toggle; a long list opens with its structure visible and its bulk
  closed.
- **Reduce, then ship.** The test before handing anything over: what on
  this screen could be hidden without losing a decision? If the answer is
  "some of it", it is not done.

## Why it needs a rule

The agent will always be able to argue that the smaller change is correct.
It usually is. The owner is not asking whether it is correct; they are
asking whether it is *less* — fewer rows, fewer clicks, fewer things to
read before acting — and consistent with what they already know. Those are
checked by looking at the rest of the product, not at the change.

## Related

`owner-is-not-qa` (drive every control before handover), `copy-discipline`
(words that say what the control does).
