# The Claude project format

A portable arrangement for making an AI coding agent useful on a specific
codebase. Project-agnostic: copy the layout, fill in your own content.

Stack-neutral by design. The "What not to do" section at the end lists the
failure modes this format tends to develop as it matures, so you can check for
them before they set in.

---

## The one idea

**`CLAUDE.md` is rent paid on every message. Skills are paid only when used.**

Everything else follows. A rule that must apply to every message goes in
`CLAUDE.md` and justifies its cost. A rule with a narrower trigger goes in a
skill, where it costs nothing until something matches.

The failure mode is entropy: every incident adds a paragraph to `CLAUDE.md`,
nothing is ever removed, and eventually the file is so large the model skims
it — at which point you are paying maximum cost for minimum compliance.

**Budget: `CLAUDE.md` stays under ~200 lines.** When it grows past that, the
excess is not trimmed, it is *moved* into a skill. Enforce this or the format
degrades into a single enormous file within a few months.

---

## Layout

```
CLAUDE.md                  operating rules, always loaded, hard budget
AGENTS.md                  vendor-neutral: architecture, not workflow
.claudeignore              attention budget: what not to read
.claude/
  settings.json            hooks and permissions, committed
  skills/
    README.md              index, conventions, maintenance policy
    <skill-name>/
      SKILL.md             frontmatter + body
      references/*.md      optional, when SKILL.md passes ~300 lines
      scripts/*            optional, runnable directly and in CI
scripts/
  claude_session_start.*   SessionStart hook
  claude_branch_guard.*    PreToolUse hook
docs/
  CLAUDE_TEMPLATE.md       this file
```

Deliberately absent: `.claude/agents/` and `.claude/commands/`. Agent
descriptions are always-on cost; prefer skills, which route on demand.

---

## What belongs in CLAUDE.md

Six things, and nothing else:

1. **A grep index.** A table of `| Looking for… | Look in… | Keyword |`. The
   point is the third column: give the literal search term. Follow it with the
   rule *"grep it, don't read it"* for any long document.
2. **Hard constraints.** The handful of things that waste a session or damage
   the project. Each one states its **consequence**, not just the prohibition —
   "don't do X" gets re-litigated by the next agent, "don't do X, because it
   silently corrupts Y and nothing downstream detects it" does not.
3. **Verification discipline.** See below.
4. **Code standards**, in a few lines. Anything stack-specific that runs long
   belongs in a skill.
5. **The required checks**, as a copy-pasteable block. Lint, types, tests.
6. **Commit and branch rules.** Short.

Plus a closing session-finish checklist if you want one.

What does *not* belong: procedure templates (put them in the skill that uses
them), incident narratives, per-framework conventions, or anything you would
describe as "background".

### Verification discipline

This is the highest-value rule in the whole format and the one most projects
lack:

> Before stating a fact about the system, confirm it against the source, then
> state it as verified. Do not assert from memory, from a doc that can drift,
> or from a plausible-sounding inference.
>
> - Grep-answerable questions get grepped, never asked of the user.
> - Third-party behaviour gets fetched from current docs, or measured. Model
>   knowledge has a cutoff and API shapes change.
> - Never report a fix as working without running it. Not "this should now
>   work" — run it and paste the output.
> - When you state something unverified, say so explicitly.
> - When a documented fact and an observed fact disagree, the observation
>   wins, and the disagreement gets written down with a date.

### Proactive completion, with an ask-first list

> If a tool gives you write access, use it. Don't file a task for something you
> can do yourself, and don't stop at "drafted but not deployed".

That rule is only safe with an explicit list of exceptions. Always ask before:
changing a contract a third party depends on; renaming anything with external
callers; reversing a documented tradeoff; destructive operations (dropping
data, rewriting history, force-pushing); spending money; **adopting a new
dependency**; changing a public interface or exit code.

---

## Skills

### Frontmatter — exactly two fields

```yaml
---
name: <kebab-case, matches the directory name>
description: >
  <when to use this>
---
```

No `version`, `author`, `tools`, or `model`. The `description` does all the
routing.

**Descriptions load every session; bodies do not.** So a long, precise
description is a feature and a long body is nearly free. Write the description
as *situations*, not *topics*: "Use when editing `x.py`, adding an endpoint, or
when a documented field returns null" fires correctly. "API reference" never
fires at all.

Name concrete triggers, including literal phrases the user might type.

### Body conventions

- Open with the **consequence** — why this skill exists, what goes wrong
  without it. That paragraph is what stops a future agent undoing the rule.
- Prefer **verified observations to documented ones**, and date them.
- Cite `file:line` for known call sites of an invariant, so a refactor can
  find everything the rule protects.
- Include a **"When NOT to use this"** section. Nearly every good skill has
  one; it prevents the skill firing on adjacent-but-different work.
- Keep under ~300 lines. Past that, split detail into `references/` and leave
  `SKILL.md` as the orchestration layer.

### The maintenance rule

> When an agent produces output that violates a project convention, the fix
> goes into the relevant **skill** — not into another line of `CLAUDE.md`.

This is the single most important structural rule in the format. It is what
keeps the always-loaded file from growing without bound.

---

## Hooks

Rules in a prompt are advisory. A hook is not. Use hooks for the invariants
that must hold even when the model is wrong.

Write them in the project's own language rather than shell, unless the project
is shell. It removes a dependency and makes them testable.

### PreToolUse — a deny gate

Contract: **JSON on stdin** with the proposed call under `tool_input`;
**exit 0** allows; **exit 2** blocks and feeds stderr back to the model.

Two things learned the hard way:

- **Decode stdin as `utf-8-sig`.** A BOM makes `json.load` throw, and if your
  handler treats a parse failure as "allow", the guard silently stops guarding
  while still appearing to be installed.
- **Fail open, but never silently.** Print a warning when the check could not
  run. A guard that is trusted and absent is worse than no guard.

Parse the command properly — tokenize, don't substring-match. Naive matching
misses `git push origin HEAD:main` and false-positives on a branch named
`maintenance`.

### SessionStart — a state banner

Prints once, so the agent doesn't spend its first three tool calls
rediscovering the branch. **Counts, not content**: say how many things there
are and give the command to see them. Dumping rows costs context every session
to answer a question rarely asked.

Worth including: current branch with a warning if it is protected; uncommitted
file count; one line of domain state; **tripwires** for patterns the project
has decided against.

Make tripwires match *code*, not prose — a tripwire that greps for a banned
library's name also fires on the comments explaining why it was banned. A
tripwire that cries wolf gets ignored. Always exit 0; a banner must never
block a session.

---

## AGENTS.md

Vendor-neutral, for agents that don't read `CLAUDE.md`. Carry only what is
genuinely different: what the program is, the module map, the dependency
direction, and **where the difficulty actually is**.

Do not restate branch and commit rules "for convenience". Point at
`CLAUDE.md`. Two copies of a rule is one rule and one future lie.

---

## .claudeignore

An attention budget, not a security boundary. Exclude generated code, lock
files, vendored dependencies, build output, large assets, verbatim licence
text, and any harness-managed worktree directory. Anything listed can still be
opened deliberately when a task calls for it.

---

## Adapting this

1. Fill the grep index with your files and your literal keywords.
2. Replace the hard constraints with yours — each with its consequence.
3. Set your check commands and protected branch names.
4. Write one skill per domain you keep having to re-explain. Start with the
   one where a mistake is most expensive.
5. Install the two hooks and **test them by planting a violation**. An
   untested guard is decoration.

---

## What not to do

These are the ways a mature implementation of this format typically degrades.
All of them are the same disease — a document that only ever gets appended to.

- **Letting `CLAUDE.md` reach thousands of lines** while its own advice says
  keep it short. This is the default outcome without an enforced budget.
- **Marking most sections "(mandatory)".** When everything is mandatory the
  marker conveys nothing. Reserve it for a handful, or drop it.
- **Section numbering fossils** (`0a`, `1b.2` and the like) from repeatedly
  inserting before section 1. Cross-references become brittle. Use stable named
  anchors.
- **Direct contradictions between sections**, such as one rule forbidding an
  action and a later one requiring it in some situation. The
  predictable result of append-only editing. Do a consistency pass whenever you
  add a rule.
- **Duplicating full procedure templates** in both `CLAUDE.md` and the skill
  that owns them. Procedure in the skill; a one-line pointer in `CLAUDE.md`.
- **Incident-narrative padding.** A paragraph of forensics after each rule is
  rent paid forever. Compress to a parenthetical and link the full write-up.
- **Unpruned local permission files.** Accreted per-command grants become a
  larger static cost than your skills. Collapse them periodically.
- **Unverifiable statistics stated as fact** to justify a rule. Keep the rule;
  drop the false precision.
