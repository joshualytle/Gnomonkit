---
description: Activates when writing anything the owner will send under their own name (counsel briefing, vendor letter, compliance statement, memo) or editing one after critique.
---

> Full trigger text: Activates when writing anything the owner will send under their own name — a briefing to counsel, a letter to a vendor, a data inventory, a compliance statement, an investor or partner memo — or when editing such a document after the owner's critique. Every claim in it is the owner's assertion to someone who will act on it, so each one is proven live, hedges and disclaimers are gone, and unknowns become questions.

# Documents written in the owner's voice

A document like this can pass several "final" sweeps and still come back from
the owner line by line: a "this is not legal advice" line in a briefing to
their own lawyer, a "why I'm sending you this" section for someone they have
already met, a worry the owner never had, invented as a hedge, a "not
confirmed" cell in a document the owner is signing, and inventory rows that
are wrong against the live system. The usual cause is auditing against a
schema dump and the repo, not against the running system — and never against
the owner's own knowledge of what the reader already has.

## The reader already has the context the owner has

The document goes to someone the owner has already talked to. Write from
inside that relationship:

- **No introduction of things they know.** No "why I'm sending you this", no
  restating who the owner is or what the product is beyond what the
  document itself needs.
- **No disclaimers aimed at a professional.** "This is not legal advice" to a
  lawyer, "I am not a security expert" to a security reviewer — delete.
- **No history the reader cannot act on.** "We used to do X; that stopped
  last year" is noise unless the change is itself a question.
  If it is not a decision they need to make or a fact they need in order to
  make one, it goes.
- **Structure follows what the reader must decide**, in the order the owner
  wants decisions. Not the order the agent discovered things.

## Every sentence is the owner's assertion

The owner signs it. A hedge reads as the owner not knowing their own
business; a wrong fact reads as the owner lying.

- **Delete hedges**: "I believe", "I think", "as far as I can tell", "I
  understand", "probably", "not confirmed". Either the fact is verified and
  stated, or it is the owner's genuine unknown — in which case it is written
  as the question for the reader: not "I do not know whether X applies" but
  "Does X apply?"
- **Do not invent concerns to sound careful.** A line like "I may have got the
  ordering wrong" reads as diligent, but if the owner has no such concern
  they end up explaining the actual structure back. If the owner has not said
  they are worried about something, the document does not say they are.
- **State the thing, not the feeling about the thing.** "These are ordered by
  how much they worry me" → "In priority order."

## What a system holds is proven live, row by row

An inventory of what the business stores, sends, or can see is the part
most likely to be wrong, because it is the part most tempting to write from
documentation. Every row needs, before it is written:

1. **The write path** — the actual INSERT or the actual call that puts the
   data there, read from the live system (not the repo copy, which drifts).
2. **A live count** — how many rows exist, how many are populated, the
   oldest date. A table that "would hold an IP address" with zero rows and
   a table with thousands of populated rows are different sentences.
3. **The purge path** — what deletes it, on what condition. A retention
   period often applies only to rows in one state (say, `done = 1`), and
   whether failed rows ever reach that state is a question about the code
   that runs, not the schema.
4. **Run histories, execution logs and alerts** — a workflow engine that
   receives personal data keeps the full payload in its execution history
   even when the row it writes is scrubbed. Inventory those too, and turn
   them off when the owner's rule is "no logging".
5. **What the owner can see in a vendor's dashboard** — from the owner's
   screenshot, not from what the vendor "processes". A CDN or hosting
   dashboard may list client IP addresses even when the document says the
   owner can see none.

## The owner's own claims are hypotheses too

An owner may say a log does not store a user identifier while the live table
holds it in every row. Verify what the owner asserts about their own system
the same way, and when it is wrong:

- **If nothing depends on the discrepancy, make the system match the
  owner's intent** — for example, make the log anonymous — and say so
  plainly with the number that proved it. The document then states what is
  now true.
- **If something depends on it, state the truth and put the decision to the
  owner** as a numbered choice, before the document goes out.

Never word the document around a discrepancy the owner does not know about.

## Delivery

- The rebuilt document goes where the owner reads it (a shared drive, a
  repo file), not into chat. Chat scrolls away.
- Every item still owed by the owner — a date they must supply, a migration
  to apply, a decision to make — lands on the tracking issue in the same
  turn, and the summary to the owner is a numbered list.
