---
description: MUST run when the user signals the session is ending ('wrap up', 'we're done', 'before I open a new session'); never let a session end without it.
---

> Full trigger text: MUST run whenever the user signals a session is ending - "close out", "wrap up", "we're done", "before I open a new session", "anything else before we close". Runs the project's own session-close checklist skill when one exists; otherwise runs the minimal portable close. Never let a session end without this.

# Session close is never skipped

Every session close runs a close-out discipline. Chat context does not
survive into the next session — anything not written to a file is lost.

**Step 1 — prefer the project's own checklist.** If the repo has a
session-close skill (e.g. a project's own multi-step close checklist) or a
CLAUDE.md session-close section, invoke/follow THAT — it encodes
project-specific registers this generic skill can't know.

**Step 2 — otherwise, the minimal portable close:**

1. **Unverified-work register:** anything the owner still needs to
   manually verify goes into the project's tracking file (bug log, TODO,
   issue) with enough detail to run the test cold weeks later.
2. **Continuation note:** last commit SHA, what shipped (one line per
   arc), the single next item, and constraints — somewhere the next
   session will find it. Name the file with "handoff" in it: core's
   `compact_carryover` hook points the post-compaction context at a
   handoff file the session wrote, alongside the open PRs, running
   tasks and pending questions it extracts (see the token-efficiency skill, §11.3).
   **Work items:** for each GitHub work item this session claimed or wrote
   to (`gnomonkit work list --mine`), set its state and next step before
   the session ends: `gnomonkit work next <ref> "..."`, then
   `gnomonkit work state <ref> blocked|review|open "..."` if it is not
   still being worked, or `gnomonkit work close <ref> --note "..."` when
   it is done. The next session reads the item, not this chat (see the
   work-items skill).
3. **Follow-ups written down:** grep your own messages for "remember",
   "make sure", "when you", "don't forget" — each one must live in a
   file, not just chat.
4. **Version + release notes** per the project's rules, when user-visible
   changes shipped.
5. **PR opened/refreshed** for the working branch (owner merges; agent
   never merges).
6. **Paste-ready handoff note** as the final message, in a single fenced
   markdown block: picking-up-at SHA, shipped arcs, owner-state notes,
   next item, open blockers.
7. **Rule harvest:** anything this session taught that generalizes across
   projects → propose it for Gnomonkit core or the user's overlay (see the
   central-rules-sync skill); project-specific lessons → the project's
   skills/CLAUDE.md.
8. **Lessons from the history** (before the handoff note in step 6):
   run `gnomonkit lessons candidates --since 30d`. If it says lessons are
   off here (`memory.lessons` off, or auto with MemPalace set up), skip
   this step. Otherwise it lists the user's own
   corrections from past sessions, grouped, the most repeated first.
   Nothing in it is a lesson yet.
   - Offer the top few to the user in one short numbered list. For each,
     give a one-sentence lesson in your own words, its scope (everywhere,
     or `repo:NAME`), and the count it shows.
   - Add only the ones the user agrees to:
     `gnomonkit lessons add "<lesson>" --scope <scope> --source <session> --by agent`.
   - If `add` reports a near-duplicate, show it to the user. Then run it
     again with `--same-as L<n>` when it is the same lesson (its count goes
     up), or with `--new`.
   - Never add one the user did not agree to. Nothing here calls a model:
     the user's yes is the whole review.
