---
description: Activates when writing or editing user-facing copy (headlines, blurbs, product text, release notes) or typing the same sentence, address or URL into a second file.
---

# Plain copy, written once

Two things reliably go wrong in user-facing text: it inflates, and it
duplicates. Inflated copy reads as untrustworthy to exactly the technical
audience it is aimed at. Duplicated copy drifts, and the drift is invisible
until someone reads two pages side by side.

## Say what it does

**Banned, on every project:** revolutionary, seamlessly, unlock,
game-changer, next-gen, "tired of".

The replacement is always the concrete behaviour. Not "seamlessly syncs your
library" but "watches a folder and imports what lands there". A specific claim
is both more persuasive and checkable; a superlative is neither.

Voice beyond that is per-project — the register belongs in the project's own
`CLAUDE.md`.

## Write it once

- Copy appearing on **more than one page** lives in a shared data or i18n
  file, not typed into each page.
- **Never hardcode email addresses or URLs.** They change, and the one you
  miss is the one on the contact page.
- Copy owned by **another product or repo** is pulled, not retyped. If there
  is a descriptor to fetch, render from it; the point is that upstream edits
  arrive without a second repo needing a commit.

## When NOT to use this

Internal docs, commit messages, and code comments — precision matters there,
but this is about text a customer reads.
