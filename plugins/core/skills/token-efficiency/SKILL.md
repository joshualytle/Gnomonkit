---
description: Activates at the start of substantive work, when a session runs long, when planning delegation to subagents, or when reviewing how a session spent its budget.
---

# Token efficiency

The canonical portable ruleset lives in
[references/CLAUDE_CODE_EFFICIENCY_RULES.md](references/CLAUDE_CODE_EFFICIENCY_RULES.md)
(11 sections). Read the section relevant to the moment —
don't reload the whole file every session.

Quick reference of the highest-leverage rules:

- **Grep before Read; never read tracking docs top-to-bottom.** One grep that
  answers the question is all you should spend.
- **Batch independent tool calls in one message.** Serial Grep→Read chains on
  unrelated files are the #1 waste.
- **Delegate only when it pays (§5.3):** a large investigation with a small
  answer and plenty of session left. Never the last step; do that inline.
  Brief bounded jobs with a stop-and-report point, prefer read-only
  specialists (Explore, docs lookups) for searching, and ask for a short
  report with detail in files. Fresh-eyes review, parallel work and
  isolation are valid non-token reasons; say which one applies.
- **Context budget (§1):** past the budget (default 250k), write the
  handoff (state, decisions, next step) to the memory or handoff file,
  then ask the user to `/compact` or start a fresh session with it.
  Auto-compaction may happen; once a session has been compacted
  automatically `max_compactions` times (default 2), write the handoff
  (`session-close`). If the hook says manual compactions are left, tell
  the user they can `/compact` or start fresh; otherwise recommend a fresh
  session started from it, sooner if you are forgetting things.
- **After a compaction (§11.3):** the `compact_carryover` hook injects
  what the summary tends to drop (open PRs, running task and subagent
  ids, edited files, the unanswered question, the handoff file). Treat
  it as leads: check each item (task output, `gh pr view`, the file)
  before acting on it. It does not replace the handoff.
- **Script what repeats:** a multi-step process that comes round again gets
  a script or skill, not another manual run (`automate-repeats`).
- **Never re-read a file already in context.**
- **Split long payloads:** Writes ≤200 lines, Reads of big files by
  offset/limit, halve any failed payload before retrying.
- **Checkpoint every ~8–10 commits** so the owner can redirect before drift
  compounds.
- **Sweep records per owner message, not per session** — decisions/deferrals
  land in their tracking file the turn they happen; a follow-up that lives
  only in chat is a silent drop (§7.5–7.6).
- **Grep git history before implementing a named item** — it may already have
  shipped (§7.7); **write the design doc before undocumented code** (§7.8).
- **Proactive completion:** if a tool can finish the step, do it — ask first
  only for destructive/paid/contract/new-dependency steps (§7.9).
- **Session review:** at close, note what burned tokens unnecessarily; fold
  recurring waste patterns back into the ruleset file (same commit
  discipline as any other doc).
