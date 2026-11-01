# Your private overlay

Gnomonkit is the shared half of your setup: rules that are true anywhere and safe for anyone to
read. The other half is yours alone: how you like to work, the rules that name your own tools,
hosts and people, the identifiers that must never leak, and what you have learned from your own
use. That half lives in an **overlay**, a private git repository of your own that Claude Code
installs from the same way it installs Gnomonkit.

You don't need one to start. First-run setup keeps your profile in `~/.gnomonkit/personal.toml`
([PROFILES.md](PROFILES.md)). An overlay is worth making when you want your rules and profile to
follow you between machines, when you have private skills of your own, or when you contribute to
a project with leak gates and need a denylist.

## The format

An overlay is a private git repository that is also a Claude Code marketplace, so its private
skills and hooks install like Gnomonkit's:

```text
<overlay>/
  gnomonkit-overlay.toml          # marker: format = 1, name, which parts are in use
  README.md                       # what this repository is; says it must stay private
  CLAUDE.md                       # how agents work inside the overlay itself
  .claude-plugin/marketplace.json # the marketplace "<name>", listing plugins/overlay
  plugins/overlay/
    .claude-plugin/plugin.json
    skills/<skill>/SKILL.md       # your private skills
    hooks/hooks.json              # optional: your private hooks
  profile/personal.toml           # your personal profile
  lessons/register.json           # lessons from your sessions (optional)
  private/gnomonkit-denylist.txt  # your identifiers, for leakcheck
  evidence/README.md              # numbers and incidents from your own use
  .gitignore
```

The marker is what tells Gnomonkit's tools that a folder is an overlay; they refuse to treat any
other repository as one. It looks like this:

```toml
format = 1
name = "my-overlay"
github = "example-org/my-overlay"     # optional: the private GitHub repository, when you have one
parts = ["plugin", "profile", "denylist", "evidence"]
```

The name is the marketplace's name, so the plugin installs as `overlay@my-overlay`. Use lower-case
letters, digits and hyphens; `gnomonkit` and names that look official are refused.

## The commands

All four are part of the `gnomonkit` command ([Getting started](ONBOARDING.md#first-run-setup)
explains it). Without the launcher on your PATH, run `python "<core>/gnomonkit.py" overlay ...`
instead. You can also ask Claude: "make me an overlay", "convert this repo into an overlay", "check
my overlay".

None of them creates a remote, pushes, or runs `gh repo create`. They print those commands for you
to run, always for a **private** repository; there is no option for a public one.

### Make one: `overlay init`

```text
gnomonkit overlay init <projects folder>/my-overlay
gnomonkit overlay init <projects folder>/my-overlay --name my-overlay --github example-org/my-overlay
```

`init` makes the layout in an empty or missing folder, from a template of synthetic examples: one
sample skill to replace, a profile with every setting commented out (so the defaults apply until
you change one), and an empty denylist with its format explained. It runs `git init` and makes the
first commit, but only when your git identity is set; otherwise it says how to set it and commit
yourself. A folder holding only an empty clone (a `.git` with no commits) counts as empty, so you
can make the private repository on GitHub first and clone it.

It refuses a folder that holds anything else. For an existing repository, use `convert`.

`--github OWNER/NAME` records the repository in the marker and prints the command to make it:

```text
gh repo create example-org/my-overlay --private --source <projects folder>/my-overlay --remote origin
git -C <projects folder>/my-overlay push -u origin main
```

### Bring in an existing repository: `overlay convert`

If you already keep your rules in a repository, `convert` brings it into the format:

```text
gnomonkit overlay convert <projects folder>/my-rules            # the plan; changes nothing
gnomonkit overlay convert <projects folder>/my-rules --apply    # makes the copies
```

It looks at `CLAUDE.md`, `AGENTS.md` and other agents' instruction files, `.claude/skills/`, the
hooks in `.claude/settings.json`, markdown files that read like rules, and any personal profile or
denylist already in the repository, and prints the plan in four parts:

- **copied**: `.claude/skills/<skill>/` to `plugins/overlay/skills/<skill>/`, a `personal.toml` to
  `profile/personal.toml`, a denylist to `private/gnomonkit-denylist.txt`;
- **made from the template**: the marker, the manifests, and whichever of README, CLAUDE.md,
  `.gitignore`, `evidence/README.md`, the profile and the denylist are missing;
- **left as they are**: anything already in place;
- **needs your decision**: which CLAUDE.md or AGENTS.md rules should follow you to every project
  (those become skills), which repository hooks should run everywhere, which rule-like files to
  turn into skills, an existing marketplace that doesn't list `plugins/overlay` yet.

Without `--apply` nothing changes. With it, `convert` copies: it never moves, overwrites or deletes
anything, so the originals stay where they were until you remove them. It reports every file it
wrote, runs `git init` if the folder isn't a repository yet, and doesn't commit: review the changes
and commit them yourself. An existing `.claude-plugin/marketplace.json` is never rewritten; the plan
prints the entry to add to it.

A repository that is public on GitHub is refused (checked with `gh` when it is installed). An
overlay holds private rules and identifiers, so make a new private one with `init` and copy across
what you need.

### Check it: `overlay check`

```text
gnomonkit overlay check <projects folder>/my-overlay
```

It checks that the marker parses and names parts that exist; that the marketplace and plugin
manifests parse and name plugins that exist; that every skill has a `description:`; that the
profile passes the [profile schema](PROFILES.md#settings); that every denylist line is
`category<TAB>regex` and compiles; that no `.gitignore` rule leaves out `private/`, the profile or
`evidence/` by mistake; and, when the GitHub repository is known, that it is private. Problems name
the denylist's lines by number, never their text.

Exit 0 means it passed, 1 that problems are listed, 2 that the folder isn't an overlay. Without a
folder, `check` and `link` use the overlay your personal profile is in.

### Connect it: `overlay link`

```text
gnomonkit overlay link <projects folder>/my-overlay
```

`link` prints the commands that connect the overlay on this machine and runs none of them:

1. `claude plugin marketplace add <overlay>` and `claude plugin install overlay@<name>`;
2. core's `personal_profile` setting pointed at `<overlay>/profile/personal.toml`;
3. `GNOMONKIT_DENYLIST` pointed at `<overlay>/private/gnomonkit-denylist.txt`, for leakcheck
   ([LEAK-GATES.md](LEAK-GATES.md)).

If you already have a profile in `~/.gnomonkit/personal.toml`, copy it over the overlay's
template first so you keep your answers. On another machine, add the marketplace from GitHub
(`claude plugin marketplace add example-org/my-overlay`; it needs access to the private repository)
and set the same two settings there.

## Keeping it private

- Make the repository private when you create it, and keep it that way. `check` reports a
  public or internal GitHub repository.
- Fill in the denylist with the names, products, hosts, domains and account IDs that must never
  appear in public. leakcheck refuses a list with no rules, so it can't pass silently with an
  empty one.
- Don't copy from the overlay into a public repository. A rule worth sharing goes to Gnomonkit as
  an issue, written in general terms; the evidence behind it stays in your `evidence/`.

## What goes where

| It is... | It goes in |
|---|---|
| a rule anyone could use | Gnomonkit (an issue, since outside pull requests are closed) |
| a rule only you hold, or one naming your tools, hosts or people | the overlay's skills |
| a fact about one project | that project's CLAUDE.md or memory |
| a number or incident from your own use | the overlay's `evidence/` |
| an identifier that must never leak | the overlay's denylist |

### Moving a rule between the overlay and a project

- **From a project into the overlay**, when a rule in one project's CLAUDE.md turns out to apply to
  all your projects: write it as a skill in `plugins/overlay/skills/<name>/SKILL.md`, with a
  `description:` that says when it applies, bump `version` in
  `plugins/overlay/.claude-plugin/plugin.json`, and leave a one-line pointer in the project (or
  nothing). Run `claude plugin update overlay@<name>` and `/reload-plugins` to pick it up.
- **From the overlay into a project**, when a skill turns out to be about one project only: move
  the text into that project's CLAUDE.md or `.claude/skills/`, delete the skill from the overlay,
  and bump the overlay plugin's version so installed copies drop it.

The `central-rules-sync` skill has the longer version of these rules, and `overlay-setup` drives
the commands when you ask Claude.
