---
description: Activates when setting up Claude Code in a repo, when CLAUDE.md grows long, or when deciding whether a rule belongs in CLAUDE.md, a skill or a hook.
---

# The Claude project format

Full write-up, including templates and an opinionated list of anti-patterns:
[references/CLAUDE_PROJECT_FORMAT.md](references/CLAUDE_PROJECT_FORMAT.md).
Read the section you need; don't reload it every session.

## The one idea

**`CLAUDE.md` is rent paid on every message. Skills are paid only when used.**

A rule that must apply always goes in `CLAUDE.md` and justifies its cost. A
rule with a narrower trigger goes in a skill, where it costs nothing until
something matches.

**Budget: under ~200 lines.** When it grows past that the excess is *moved*
into a skill, not trimmed. Without an enforced budget the file accretes a
paragraph per incident until it is large enough that the model skims it — at
which point you pay maximum cost for minimum compliance. A file can carry its
own "keep this short" rule while running many times past it; a budget nobody
enforces does not hold.

## The maintenance rule

**When an agent violates a project convention, the fix goes into the relevant
skill — never into another line of `CLAUDE.md`.** This is what keeps the
always-loaded file bounded.

## Where a rule lives

| Kind | Home |
|---|---|
| Applies on every project, for everyone | the shared Gnomonkit `core` plugin |
| Applies on every project, yours alone | your private overlay plugin |
| Applies to this repo, always | `CLAUDE.md` |
| This repo, narrow trigger | `.claude/skills/<name>/SKILL.md` |
| For non-Claude agents too | `AGENTS.md` — architecture, never workflow |
| Not worth reading at all | `.claudeignore` |

One fact, one file. When a rule graduates upward, the lower copy becomes a
pointer **in the same change**.

## Writing a skill

- Frontmatter description is the trigger. Write it as *situations*, not
  *topics*: "when editing x.py, or a documented field returns null" fires;
  "API reference" never does.
- Lead with the **consequence**, not the rule. "Don't do X" gets re-litigated;
  "don't do X, because it silently corrupts Y and nothing downstream detects
  it" does not.
- Include a **"when NOT to use this"** section.
- Under ~300 lines; heavier material into `references/`.

## Hooks

Rules in a prompt are advisory; a hook is not. Use hooks for invariants that
must hold even when the model is wrong.

Write them in the project's own language rather than shell — it removes a
dependency and makes them testable.

- **PreToolUse** is a deny gate: JSON on stdin, exit 0 allows, exit 2 blocks
  and feeds stderr back to the model. Tokenize the command; don't
  substring-match, or you miss `git push origin HEAD:main` and false-positive
  on a branch named `maintenance`.
- **SessionStart** prints a banner: counts, not content. Say how many things
  there are and give the command to see them.

Both: see `verify-before-asserting` for why an untested guard is worse than no
guard, and `windows-dev-traps` for how a BOM can make one fail open silently.
