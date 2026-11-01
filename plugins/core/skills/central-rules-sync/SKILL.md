---
description: Activates when writing or changing a rule or lesson, setting up a repo, seeing a stale-plugin warning, or about to write something portable in a project-local file.
---

> Full trigger text: Where a rule lives — the shared Gnomonkit core, your private overlay, or one project — how projects link to them, and how agents stay current. Activates when writing or changing a rule, adding a lesson learned to CLAUDE.md or a skill, setting up a new repo, seeing a stale-plugin warning, closing a session, or whenever you catch yourself about to write down something portable in a project-local file.

# One ruleset, everywhere

Rules that outlive one repo live in exactly one place, and every project links to that place
instead of copying from it. An agent that writes a portable lesson into a project file has hidden
it from every other project, and the next agent on the next repo pays for the same mistake again.

A common way this goes wrong: a long session works out several release-pipeline traps that
apply to any repo that publishes artifacts, and records every one in project-local memory. The
lessons are real, but only that one repo will ever see them.

## Where a rule lives

Ask two questions: **would this be true on a different project?** and **would it be fine for
anyone to read?**

| The rule is… | It lives in… | Example |
|---|---|---|
| True anywhere, and safe to share | Gnomonkit `core` (a pull request to the shared repo) | A token-created release raises no `release: published` event |
| True across your projects, but personal or private | your private overlay plugin | How you want status reported; your own naming conventions |
| True of this repo's domain, stack or infrastructure | the project's `CLAUDE.md` or `.claude/skills/` | This app's offline-mode contract; this DB's schema-dump procedure |
| A fact about this project's current state | project memory | Which branch the current work is on |

No overlay yet? The `overlay-setup` skill makes one (`gnomonkit overlay init`) or brings an
existing rules repository into the format (`gnomonkit overlay convert`).

Two failure modes to watch for:

- **Portable rule written locally.** The commonest one. If the lesson mentions a vendor, a
  protocol, a language, a tool or an operating system rather than *this product*, it is portable.
- **Central rule copied locally.** A project restating a central rule in its own words forks it:
  the two drift, and nobody knows which is current. Link to it instead, and delete the local copy
  when you find one.

When a rule is genuinely both — a portable trap plus a project-specific call site — put the rule
centrally and leave a one-line pointer locally with the file and line the project has to care about.

## Every project links, in its `CLAUDE.md`

Near the top, before the project's own rules:

```markdown
## Central rules (do not copy rules here)

Shared working rules come from the Gnomonkit `core` plugin; rules that are mine alone come from my
private overlay plugin. Both load automatically. This file holds only what is specific to THIS repo.
When a lesson would be true on another project, it belongs centrally, not here.
```

- Declare both marketplaces in `.claude/settings.json` (`extraKnownMarketplaces` +
  `enabledPlugins`) so a trusted folder installs them without per-machine setup.
- **Precedence:** the user's direct instructions win, then the project's own rules, then the
  overlay, then the shared core. A project may override a central rule, but it must say that it is
  overriding and why — silently contradicting the centre is how two machines end up behaving
  differently.

## Staying current

The plugin cache updating is not enough: a running session keeps whatever it loaded at start, so
**an update applies only once the session reloads it**. `/reload-plugins` does that in place (in every client; `/plugin` itself exists only in the terminal CLI, so update with `claude plugin ...` from a shell);
a restart is needed only for what is injected once at startup (SessionStart context).

- core's `plugin_freshness.py` does the check at SessionStart: every enabled plugin from a GitHub
  marketplace, the version this project loads against the marketplace's default branch. It is
  fail-soft: offline or unauthenticated machines skip silently. It re-checks in the background
  while a session runs, so open sessions hear about an update too. Don't add a project-local copy.
- When the "Plugin update pending" line appears, say so and finish the current step, then run or
  ask for the update it names. Do not spend the session on rules you know are out of date.
- At session start, if the skills you expect are not listed, **say so** rather than proceeding as
  if they are loaded. Silent absence is what makes a session write everything locally.

## Feeding it back — same session, not later

When a session learns something portable:

1. Decide where it goes with the table above: shared core or private overlay.
2. Write it as a skill (or a section of the right existing skill) in the house voice: what was
   earned, on what kind of project, and the rule that follows from it.
3. Bump the plugin's version in `.claude-plugin/plugin.json`, and extend its `description` when the
   skill list changes — that description is how the plugin advertises what it covers.
4. Open a pull request. Never push to `main` directly. A contribution to the shared core must pass
   its leak gates.
5. In the project that earned it, keep only a pointer, if anything.

Do this in the session that learned it. A lesson deferred to "session close" is a lesson lost when
the window ends, and the next repo repeats the failure.

## What never goes in the shared core

Anything with a secret in it, anything naming a person, customer, product or host, and anything
that only makes sense with one project's file layout in front of you. Rules there are read by
agents on repos nobody has written yet, by people you will never meet.
