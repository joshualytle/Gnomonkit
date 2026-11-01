# supply-chain

> Provided as is, without warranty; see the [disclaimer](../../DISCLAIMER.md). A clean review is
> not a guarantee that a package is safe.

Review what a dependency update actually changes: the released artifact, old version against new,
and who wrote the change.

## Why

A changelog is what a maintainer chose to say, and green CI only proves the package still works.
What installs is the registry artifact: an npm tarball, a PyPI sdist or wheel, a crate, a pub
archive. That artifact can carry code no commit shows (a generated build file, a hand-made
tarball), and a project can change hands to a patient newcomer while the publisher account stays
the same. This plugin reads the artifact itself and asks who wrote what is new.

## What it does

| | Patch review | Release review |
|---|---|---|
| Input | a bump in this repo's lockfile or manifest (`git diff`) | a package and a version range |
| Command | `supply_review.py patch [--base REF --head REF]` | `supply_review.py release ECO NAME OLD..NEW` |
| Skill | `patch-review` | `release-review` |

Both run the same engine, per release:

1. Fetch registry metadata and both versions' artifacts (HTTPS, timeouts, size caps). Verify each
   download against the registry's published digest.
2. Read both archives **in memory**. Members that escape the package folder, links and device files
   are findings, not files. Nothing is extracted beside code, imported or run.
3. Diff file by file. Run deterministic rules over every added line (see `supply_chain/rules.py`)
   and label each changed file with its kinds: install script, build file, network calls, exec or
   eval calls, obfuscated or minified, binary.
4. **New-committer check.** Find the source tags for both versions (a local clone or the GitHub
   API), list the commits between them, and for every author with no commit before the old tag say
   which paths they touched, with kinds, and which of those ship in the artifact. Files that changed
   in the artifact but are absent from the tagged source are listed; a build or install file among
   them is flagged.
5. Optional: a local model writes a short summary. Off unless configured, loopback only, labelled
   unverified, and it never adds or removes a flag.

Exit codes: 0 nothing flagged, 1 flagged, 2 nothing could be reviewed.

## Install

From any terminal:

```text
claude plugin marketplace add joshualytle/Gnomonkit
claude plugin install supply-chain@gnomonkit
```

In the terminal CLI, `/plugin marketplace add` and `/plugin install` do the same; the IDE extensions
and the desktop app have no `/plugin` command. To update later: `claude plugin marketplace update
gnomonkit`, `claude plugin update supply-chain@gnomonkit`, then `/reload-plugins` in each open session.
See [Getting started](../../docs/ONBOARDING.md#keeping-it-up-to-date).

Requires Python 3 on the PATH (3.11 or newer for the TOML lockfiles) and git. Standard library only.
The router hook suggests `patch-review` when a command moves a dependency version (`npm install
pkg@x`, `pnpm add`, `pip install --upgrade`, `uv lock --upgrade`, `cargo update`, `flutter pub
upgrade`, checking out a Dependabot or Renovate branch). It never blocks.

## Settings

| Setting | Env var | Meaning |
|---|---|---|
| `ollama_url` | `GNOMONKIT_OLLAMA_URL` | loopback Ollama URL for the optional summary; empty = off |
| `ollama_model` | `GNOMONKIT_OLLAMA_MODEL` | the model for that summary |
| `cache_dir` | `GNOMONKIT_SUPPLY_CACHE` | where archives are cached by digest (default `~/.cache/gnomonkit-supply-chain`) |
| | `GITHUB_TOKEN` / `GH_TOKEN` | raises the GitHub rate limit for the new-committer check; sent only to `api.github.com` |

## Limits

- PyPI and pub.dev do not publish who uploaded a release, so a first-time publisher is checked on
  npm and crates.io only. The report says so.
- Go modules, container images, Helm charts and GitHub Actions pins are not reviewed yet.
- The new-committer check needs findable tags (`1.2.3`, `v1.2.3`, `name@1.2.3`, `name-1.2.3` and
  similar) or `--old-ref/--new-ref`. Unauthenticated GitHub allows about 60 calls an hour.
- A manifest range is reviewed at its floor (`^1.4.0` as 1.4.0); the lockfile is exact.

## Tests

`tests/test_supply_chain_*.py` run offline against synthetic registries, archives built in memory
and throwaway git repos with made-up authors. `hooks/test_skill_router.py` runs the router fixtures.
