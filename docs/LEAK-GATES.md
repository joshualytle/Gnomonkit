# Leak gates

Gnomonkit will be public. Its contributors' private details must never enter it, not even in history.
These gates run before anything is committed and again in CI.

| Gate | Where | What it stops |
|---|---|---|
| Structure | `.gitignore`, leakcheck file rules | data files: session logs, databases, archives, keys, model weights, benchmark cases, data directories, binaries, files over 1 MiB |
| Private denylist | leakcheck, pre-commit + CI | the maintainer's own identifiers (names, products, domains, hosts, account IDs) in files, paths, commit messages and PR titles and bodies |
| Provenance | provenance_check, pre-commit + CI | evidence measured on one real setup, which names nobody: counts from someone's transcripts or prompts, "one user's" or "the owner's" data, results "on this machine", paths on a personal data drive. Added lines, commit messages, PR titles and bodies |
| Built-in patterns | leakcheck | private IPv4 ranges, home-directory paths with real user names, real e-mail addresses |
| Secret scanning | gitleaks, pre-commit + CI (full history) | credentials and tokens |
| Synthetic test data | `tests/` | tests build any private-looking string from fragments at runtime, so the test files pass the gates themselves |

leakcheck **never prints matched text**, only `path:line: category`, so CI logs can't leak the
identifiers the denylist exists to protect. It **fails closed**: with no denylist configured it exits 2.

**The one binary exception: images under `assets/`.** Binary files are blocked because their text
can't be scanned. A PNG is allowed only when it sits under `assets/` and holds nothing but pixels:
every chunk must be an image or colour chunk (`IHDR`, `PLTE`, `IDAT`, `IEND`, `tRNS`, `sRGB`,
`gAMA`, `cHRM`, `pHYs`, `bKGD`), and nothing may follow the end marker. Text, EXIF, embedded colour
profiles and private chunks can carry a hostname, a path or a prompt that no reviewer would see in
the picture, so any of them blocks the file. Re-export or strip metadata (`optipng -strip all`,
or render it fresh) rather than adding an exception.

## The private denylist

The denylist lives **outside this repository**, in the maintainer's private overlay. Format: one rule
per line, `category<TAB>regex`, `#` for comments, matched case-insensitively. Point leakcheck at it:

```powershell
# Windows (persists for new terminals)
setx GNOMONKIT_DENYLIST "C:\path\to\your-overlay\private\gnomonkit-denylist.txt"
```

```sh
# macOS / Linux
export GNOMONKIT_DENYLIST="$HOME/path/to/your-overlay/private/gnomonkit-denylist.txt"
```

CI reads it from the encrypted Actions secret `GNOMONKIT_DENYLIST` (never from this repo). Update the
secret whenever the list changes, on every repository the gates run in (with a private development
repository and a public one, both; see [RELEASING.md](RELEASING.md)):

```powershell
gh secret set GNOMONKIT_DENYLIST --repo <owner>/<repo> < $env:GNOMONKIT_DENYLIST
```

CI is kept cheap on purpose: pull requests only, drafts skipped, superseded runs cancelled, one
GitHub-hosted Linux job with a 5-minute ceiling. Mark a PR as draft while iterating to spend nothing.

## Local setup

```sh
python scripts/install_hooks.py      # pre-commit + commit-msg hooks
python scripts/leakcheck.py --all    # scan the whole tree on demand
```

## Rules here, evidence in the overlay

The private overlay is the personal half of the setup, and that is where anything learned from your
own use goes: the numbers from replaying your transcripts, fixtures paraphrased from your prompts,
your machine's drives and folders, incidents from your projects. Gnomonkit gets the rule that came
out of it, synthetic examples that exercise it, and PR text that describes the method. The provenance
gate catches the phrasings this kind of evidence usually takes; it is a heuristic, so it backs the
habit up rather than replacing it.

## Exceptions

If text is genuinely safe to publish but trips a rule, add a regex for it to `.leakcheck-allow`
with a comment saying why. Never weaken the denylist or the built-in patterns to get a commit through.
A line that only *looks* like real-setup evidence (a made-up example, a generic threshold) carries
`provenance-ok: <why>` on the same line.
