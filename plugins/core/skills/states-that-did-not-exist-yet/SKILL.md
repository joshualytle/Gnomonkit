---
description: Activates when adding a state to an enum or status column, writing a migration or idempotency guard, adding an ORM column, or reviewing a counter that enumerates what does NOT count.
---

> Full trigger text: Code written against the states that existed at the time — a guard whose fallthrough nothing exercises, a counter written as the complement of the states you knew about, a write path that must be updated by hand while the read path is generated. Activates when adding a state to an enum or status column, writing a migration or idempotency guard, adding a column to an ORM-backed table, or reviewing a counter or filter that enumerates what does NOT count.

# States that did not exist yet

Three failures with one shape: the code was correct for the world as it was
when it was written, stayed silent as that world changed, and produced a
wrong answer rather than an error.

None of them throws. That is what makes them expensive.

## 1. The fallthrough nothing exercises

A guard shaped `if (already done) return; else do the work` has one branch
that every environment takes and one that almost none do.

Consider a migration helper written to survive a column that already exists:

```dart
if (await _hasColumn(table, column)) return;   // everyone takes this
await _addColumnIfMissing(m, table, column);   // <- calls ITSELF
```

The work branch should call `m.addColumn`; it recurses instead. If the
helper is applied retroactively to existing column-add steps, every install
already has every one of those columns, so the bug can sit on every branch
for a long time without a single failure.

It becomes reachable the first time a migration adds a genuinely new column.
Then: an infinite loop inside startup, before the UI finishes loading, with
nothing printed. The process runs, the window answers the OS, and the app
never becomes usable. A hang logs nothing and fires no error handler.

**Tests cannot catch it as written.** A fresh test database runs `onCreate`
and never upgrades, so the test suite is permanently in the early-return
state too.

**The rule:** read a guard's fallthrough on its own and ask *what puts the
system in the state that reaches this?* If the answer is "a state this
environment is never in", that branch is unreviewed and untested. Write the
test that manufactures the state — for a migration, leave a real database
file at an older version rather than creating a fresh one.

## 2. The counter that enumerates what does NOT count

```dart
// wrong
final open = rows.entries
    .where((e) => e.key != 'resolved' && e.key != 'ignored')
    .fold(0, (sum, e) => sum + e.value);

// right
final open = rows['open'] ?? 0;
```

Add a new status such as `cleared`, and every counter written the first way
starts including the retired rows. A badge can read **999+** beside a list of
a few hundred, and nobody questions it, because a badge saying "more" is
always plausible.

A blocklist over an open set is a bet that the set never grows. It always
grows, and it grows in the commit that is thinking about something else.

**The rule:** count, filter and branch on the state you mean, never on the
complement of the states you happen to know about. When you *do* add a member
to such a set, grep for the existing members appearing together — a pair of
`!=` comparisons on the same field is the signature of every blocklist that
is about to be wrong.

## 3. The write path that is hand-maintained while the read path is generated

An ORM that generates row construction but takes a hand-written mapper for
writes loses every new column in exactly one direction, silently. The field
reads back correctly from a row that has it, and is never stored, so it is
always the default.

The result: every row comes back with the newly added column null after a
pass that had the value in hand for each one. Writes succeed. Reads succeed.
Nothing fails.

**The rule:** when adding a field to a model that an ORM round-trips, find
every mapper between the model and storage and count them before you start —
the generated ones look after themselves and the hand-written ones do not.
Prove it with a round-trip test that writes and reads back, on an insert
**and** on an update of a row that already existed. The second case is the
one every existing install is in, and it is the one a fresh-fixture test
never covers.

## The shared tell

In all three, the code was written while looking at the current set of states
and is only correct for that set. Ask, of anything that enumerates or guards:

> Which state makes this branch run, and what happens to it when someone adds
> a state next year?

If a new state silently changes the answer rather than breaking the build,
the shape is wrong — not the value.
