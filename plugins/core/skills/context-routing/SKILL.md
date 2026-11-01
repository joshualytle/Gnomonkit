---
description: Activates when setting up a project folder, when CLAUDE.md passes one screen, or when one conversation carries unrelated work: load only the context a task needs.
---

> Full trigger text: Structure a workspace as folders and markdown so an agent loads only the context a task needs - a routing table in CLAUDE.md, a CONTEXT.md per work area, skills wired per area, and naming conventions that stand in for a database. Activates when setting up a new project folder, when CLAUDE.md is growing past one screen, when one conversation is carrying several unrelated kinds of work, or when deciding where a piece of context belongs.

# Context routing: folders as the architecture

Source: the Interpretable Context Methodology (ICM) taught by Clief Notes,
arXiv 2603.16021. This is the method restated as working instructions; the
lessons themselves are the course's paid material.

The claim worth taking seriously: **the structure you would design already
exists in any real conversation** — goals, constraints, decisions, assumptions,
process. Putting it on disk is not a new framework, it is that structure
externalised. Which is why it needs no orchestration layer, no multi-agent
framework, and no vector database. One agent, one workspace, files.

## The three layers

**Layer 1 — the map (`CLAUDE.md`).** Read first, every task. It holds identity,
the folder layout, naming conventions, and a routing table. Nothing else.

**Layer 2 — the rooms (`CONTEXT.md`, one per work area).** Loaded only when
working in that area. It describes what the area is for, the process inside it,
what files live there, and which skills it uses.

**Layer 3 — the tools (skills, MCP servers).** Wired into the areas that need
them, never globally. A project can reference a hundred skills as long as each
area pulls only its own.

## The routing table is the load-bearing part

Without it the agent either reads everything (burning context on irrelevant
material) or guesses. Guessing produces output that is good some days and off
others, with no way to tell why.

| Task | Go to | Read |
|---|---|---|
| Write copy | `/script-lab` | `CONTEXT.md` |
| Build or animate | `/production` | `CONTEXT.md` |
| Publish, schedule | `/distribution` | `CONTEXT.md` |

Add a fourth column for skills only if Layer 3 is wired.

## Naming conventions do the work of a database

`api-auth-guide_draft.md`, `2026-03-launch-week.md`, `demo_v2.md`. Encode kind,
date and version in the filename and "pull demo v2 and build a spec from it"
resolves without an index, a query language, or embeddings. State the
conventions in `CLAUDE.md` so they are followed rather than inferred.

## Five failures, in the order they actually happen

**CLAUDE.md grows past one screen.** Past ~40–50 lines it has context files
hiding inside it. The routing instructions get buried in background. Pull the
background out into the area it belongs to.

**No routing table.** Covered above. This is the one that silently degrades
everything else.

**Too many areas.** The test is *"do I shift mental modes between these
tasks?"* Writing and building are different modes — two areas. Drafting and
editing are one mode at two stages — one area with a process inside it. Start
with two or three. If unsure whether something deserves its own area, it does
not; make it a subfolder and split later if it grows.

**Context files that describe the agent instead of the work.** "Be concise, be
professional, think step by step" changes output far less than "the audience is
mid-market HR directors who have tried three tools and distrust AI claims."
Spend ~80% of a context file on the work — what it is, who it is for, what has
been done, what good output looks like, what to avoid — and ~20% at most on
behaviour. If it reads like a personality description, rewrite it.

**Context files never updated.** The project moves, the files do not, output
drifts, and it looks like the model got worse. When output degrades, check
whether the work changed and the context did not, before blaming anything else.

## Two rules that make it hold

**One fact, one location.** No duplication across context files. Duplication
becomes drift: two files disagree and neither is obviously wrong. This applies
to prompts and validation rules as much as prose — if a rule is enforced by code
*and* stated in a prompt, generate both from one source.

**New sessions start clean.** The map is read fresh, the routing table sends the
agent to one area, and nothing from the previous task bleeds in.

## When this does not apply

A single-purpose repo with one kind of work does not need routing — a plain
`CLAUDE.md` is correct and adding areas is overhead. Batch pipelines do not
route either: there is one task, not a choice between modes. What still transfers
to a pipeline is externalising its instructions into files, one per step, not the routing table.

## A worked example

Picture a repo holding several unrelated work areas (say, a data pipeline, a
set of plugins and a style guide) with no routing table: a task touching the
pipeline reads the same root context as one touching the style guide. That is
the gap this method names.
Related: [[claude-project-format]] for the mechanics of `CLAUDE.md`, `.claude/`
and hooks; this skill is about what goes in them and why.
