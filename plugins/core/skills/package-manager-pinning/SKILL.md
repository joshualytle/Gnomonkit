---
description: Activates when changing a package manager or its version, a packageManager field or a build-script allow-list, or when CI install fails for an unrelated reason.
---

# Pin the package manager; keep its config in the matching place

CI installs with whatever package manager its build image auto-detects. That
is a moving target you do not control, and when it shifts, the failure appears
during **install** — so the error names a config file rather than anything in
the change, and reads as unrelated.

## The rule

- **Pin it** via `packageManager` in `package.json`, and use the same version
  across sibling repos so they behave identically.
- **The pin and the build-script allow-list must agree.** pnpm moved this
  setting: pnpm 9 reads `pnpm.onlyBuiltDependencies` in `package.json`, while
  pnpm 10/11 read `pnpm-workspace.yaml`. A pnpm-11-style
  `pnpm-workspace.yaml` (an `allowBuilds:` map with no `packages:` key) under
  a pnpm 9 pin fails install outright with `ERROR packages field missing or
  empty`. **If you move the pin across that boundary, move the allow-list in
  the same change.**
- **Confirm the pin took** — the build log names the version it resolved.

## Do not oversell the fix

Read the actual CI log before claiming a pin repairs a broken build. The
unpinned build may have been succeeding the whole time on an auto-detected
newer version that reads the config fine, with only an older pin rejecting it.
Asserting an urgent breakage the logs disprove is wrong, and writing it into
the docs misleads the next reader.

A pin buys **reproducibility and parity across repos** — a real benefit, and
enough on its own. See `verify-before-asserting`.

## When NOT to use this

Throwaway repos with no CI, where auto-detection failing costs nothing.
