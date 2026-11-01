# CLAUDE.md: {{name}}, a private Gnomonkit overlay

This repository is private. Everything in it belongs to one person, so:

- **Never make it public**, and never copy anything from it into Gnomonkit or another public
  repository. A rule that anyone could use goes to Gnomonkit as an issue, in generic terms.
- **Skills** go in `plugins/overlay/skills/<name>/SKILL.md`, each with a `description:` that says
  when it applies. When a skill or hook changes, bump the version in
  `plugins/overlay/.claude-plugin/plugin.json`: installs update only when it changes.
- **The profile** is changed with `gnomonkit profile set --file profile/personal.toml key=value`,
  which checks every value. Don't edit it by hand.
- **The denylist** (`private/gnomonkit-denylist.txt`) is one rule per line: a category, one TAB,
  then a regular expression.
- **Evidence** (session numbers, incidents from your projects) goes in
  `evidence/`. Gnomonkit gets the rule that came out of it, never the evidence.
- Run `gnomonkit overlay check .` before you commit.

| It is... | It goes in |
|---|---|
| a rule anyone could use | Gnomonkit (an issue) |
| a rule only you hold, or one naming your tools, hosts or people | `plugins/overlay/skills/` here |
| a fact about one project | that project's CLAUDE.md or memory |
| a number or incident from your own use | `evidence/` here |
| an identifier that must never leak | `private/gnomonkit-denylist.txt` here |
