---
name: overlay-setup
description: Activates when the user asks for a private overlay, to convert a rules repo into one, whether it is connected right, or before hand-writing a gnomonkit-overlay.toml.
---

> Full trigger text: Activates when the user asks for a private overlay ("make me an overlay", "set up my overlay", "where do my private rules go"), asks to bring an existing rules repository into the overlay format ("convert this repo", "turn my rules repo into an overlay"), asks whether their overlay is set up right or how to connect it on a machine, and before writing a gnomonkit-overlay.toml marker or an overlay's marketplace by hand.

# Making, converting and connecting a private overlay

Gnomonkit is the shared half of the setup. The private half is the user's
**overlay**: a private git repository that is also a Claude Code marketplace,
holding their own skills, their personal profile, their leakcheck denylist and
the evidence from their own use. This skill drives the `gnomonkit overlay`
commands that make one, convert an existing repository into one, check it and
connect it.

## The commands

Run them as `python "<core>/gnomonkit.py" overlay ...`, where `<core>` is the
folder two levels above this skill's folder (`<core>/skills/overlay-setup`), or
as plain `gnomonkit overlay ...` once the launcher is installed. Use `python3`
where `python` is missing.

| Command | What it does |
|---|---|
| `overlay init DIR [--name NAME] [--github OWNER/NAME]` | Makes the layout in an empty or missing folder from synthetic examples, runs `git init`, commits only when the git identity is set. Refuses a folder that holds anything. Prints the commands for a private remote; runs none |
| `overlay convert REPO` | Prints the plan for an existing repository: what is copied where, what is left alone, what needs a decision. Changes nothing |
| `overlay convert REPO --apply` | Makes the copies and adds the marker and manifests. Never moves, overwrites or deletes; runs `git init` if it isn't a repository; never commits |
| `overlay check [DIR]` | Validates the layout. Exit 0 passed, 1 problems listed, 2 not an overlay |
| `overlay link [DIR]` | Prints the commands that connect it. Runs nothing |

`check` and `link` default to the overlay the personal profile lives in.

## Making one

1. Ask where it should live and what to call it. The name is the marketplace
   name (`overlay@<name>` installs it): lower case and hyphens, not
   `gnomonkit`. Suggest a folder beside their projects, not inside one.
2. Say what `init` will do (write the files, `git init`, a first commit if
   their git identity is set) and run it once they agree.
3. Run `overlay check DIR` and `overlay link DIR` and show what `link` prints.
   The user runs those commands, in a terminal: they change Claude Code's
   settings and the user's environment.

## Converting an existing repository

1. Run `overlay convert REPO` (the dry run) and show the plan, grouped as it
   prints it. Read the "Needs your decision" items to the user one by one:
   which CLAUDE.md or AGENTS.md rules should follow them to every project
   (those become skills), which repository hooks should run everywhere, which
   rule-like files to turn into skills.
2. Run `--apply` only after the user says to. It copies; the originals stay.
3. Run `overlay check REPO`. Fix what it reports with the user, then they
   review and commit. Removing the originals is the user's call, later, once
   the overlay works.

A repository that is public on GitHub is refused: an overlay holds private
rules and identifiers. Never suggest making it public, and never work around
the refusal.

## Outward-facing steps need the user's word

Creating the remote, pushing, adding the marketplace, installing the plugin
and setting `personal_profile` or `GNOMONKIT_DENYLIST` all reach beyond the
folder. Show the command `init` or `link` printed and let the user run it. Run
one yourself only when the user asks you to in this conversation, and then
exactly as printed. A remote for an overlay is **always private**: never run
`gh repo create` without `--private`, and never change an overlay's
visibility to public.

## What goes where

When the user asks where a rule or a fact belongs:

| It is... | It goes in |
|---|---|
| a rule anyone could use | Gnomonkit, as an issue (outside pull requests are closed) |
| a rule only they hold, or one naming their tools, hosts or people | the overlay's skills (`plugins/overlay/skills/<name>/SKILL.md`) |
| a fact about one project | that project's CLAUDE.md or memory |
| a number or incident from their own use | the overlay's `evidence/` |
| an identifier that must never leak | the overlay's denylist (`private/gnomonkit-denylist.txt`) |

A new or changed skill in the overlay needs a `description:` that names when
it applies, and a version bump in `plugins/overlay/.claude-plugin/plugin.json`,
or installed copies don't update.

## Don't

- Don't write the marker, the marketplace or the plugin manifest by hand: the
  commands write them so `check` agrees with them.
- Don't edit the overlay's `profile/personal.toml` by hand; use
  `gnomonkit profile set --file <overlay>/profile/personal.toml key=value`.
- Don't print the denylist's contents. `check` names bad lines by number only.
- Don't copy anything from an overlay into Gnomonkit or another public
  repository.
