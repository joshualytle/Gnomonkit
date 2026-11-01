# {{name}}: a private Gnomonkit overlay

This repository is a **private** overlay for [Gnomonkit](https://github.com/joshualytle/Gnomonkit):
the rules, skills, profile and denylist that belong to one person. **Keep it private.** It names
tools, projects and identifiers on purpose, so it must never become a public repository, and nothing
in it is copied into Gnomonkit or any other public project.

| Path | Holds |
|---|---|
| `plugins/overlay/` | private skills (and hooks), installed as `overlay@{{name}}` |
| `profile/personal.toml` | the personal profile: how you like to work, read at every session start |
| `private/gnomonkit-denylist.txt` | identifiers that must never leak, read by Gnomonkit's leakcheck |
| `evidence/` | numbers and incidents from your own use |
| `lessons/register.json` | lessons from your sessions (optional) |
| `gnomonkit-overlay.toml` | marks this folder as a Gnomonkit overlay (format 1) |

- `gnomonkit overlay check .` checks the layout before you commit.
- `gnomonkit overlay link .` prints the commands that connect it on a machine.

Gnomonkit's `docs/OVERLAY.md` explains the format and what goes where.
