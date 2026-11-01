---
description: Activates when picking up a TODO line, issue, backlog item or milestone task: check it hasn't already shipped, and write the design doc first if intent lives nowhere.
---

# Before you build

Two cheap checks that prevent the two most expensive kinds of wasted session.

## 1. Did it already ship?

**Multi-session and multi-person work routinely produces commits that tracking
files do not yet reflect.** Before implementing anything that names an issue, a
TODO line, a milestone, or a phase:

```bash
git log --all --oneline --grep='<keyword>'
git log --all --oneline -- <expected path>
```

Then:

- **Already shipped** — read the commit, confirm it matches the spec, flip the
  tracking line, and move on. Do not rebuild it.
- **Partially shipped** — narrow scope to the remainder and make *that* the
  checklist.

Skipping this wastes the window, risks regressing working behaviour with a
slightly-different rewrite, and leaves the owner unsure what is actually
deployed.

### The commonest "partially shipped": one layer of two

A fix lands in the layer that holds the data and never in the layer that
shows it. Both halves compile, nothing fails, and the visible half goes on
being wrong — so the item stays open, gets picked up again, and the second
person rewrites the half that was already right.

What makes it expensive is that the fixed half often **carries a comment
saying the item is done**, which is the first thing a grep finds:

```dart
// Running totals for the active batch - finished items leave the
// visible list, so counting rows would always read 0 (fixes #42).
int doneCount = 0;
```

...while the panel four files away still counts rows and still shows zero.

The same shape recurs in many forms: a selected-tab index exposed by a state
holder that the view never reads from, a queue keeping its own counters while
the panel recomputes them wrongly beside it, a batch API taking an
`onProgress` callback that its one caller never passes. Each tends to carry
a confident comment on the model side.

So, when picking up a tracked item:

```bash
grep -rn "#<item-id>" <source>      # who already claims this
```

- A comment claiming the item is fixed is **evidence about one file**, not
  about the feature. Find the other side and read it.
- When the model already exposes what the view needs — a counter, a
  callback, a setter, a public field — and the view computes its own
  version instead, that is the bug, and the fix is usually deleting the
  view's copy rather than writing anything.
- Grep the API the model offers (`onProgress`, the field name) for its
  **call sites**, not just its definition. An optional parameter nobody
  passes is a fix that never shipped.

## 2. Does the intent live anywhere?

If the answer to *"where in this repo does the intent of this work live?"* is
**nowhere yet**, write the doc **before** the code — same branch, ideally a
preceding commit.

Minimum contents: **problem**, **scope** (in vs. deliberately out), **the
surface** (types, signatures, API shape), **rollback**, **tests** (happy path
plus the edge cases needing explicit handling), and **cross-references** so the
work is not tracked in two places.

**Why the order matters:** docs written *after* the code rationalise what was
built; docs written *before* anchor what should be built. They produce different
work, and the second kind surfaces the edge cases while they are still cheap to
handle.

## When NOT to use this

One-line fixes, dependency bumps with no behaviour change, and test-only
additions to an already-documented surface. The commit body is the
documentation.
