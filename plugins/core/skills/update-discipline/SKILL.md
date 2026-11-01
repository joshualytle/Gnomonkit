---
description: Activates when asked to check for, vet or apply an update, evaluate a Renovate/Dependabot PR, or bump any dependency, chart, image or tool version.
---

# Update discipline

Portable rules for taking any version/dependency/chart/image update safely. The
project-specific part — WHICH components are landmines and how to verify each —
belongs in the project's own update skill; this is the discipline that holds on
every project.

1. **Never update something that's already unhealthy.** Do a pre-flight health
   sweep first (workloads up, no firing alerts, reconciliation/CI green) and fix
   the instability before layering a bump on top — often the instability IS the
   real problem the update would otherwise mask.

2. **One risk-bearing change per PR.** Never batch a high-blast-radius bump
   (platform / DB / CNI / CSI / framework / language runtime) with unrelated app
   bumps — you can't bisect a failure or revert cleanly. Low-risk auto-merged
   patches can batch; anything that can take the system down goes alone.

3. **Read the release notes / CHANGELOG / UPGRADING doc for the exact version
   RANGE you are crossing** — not just "latest." Breaking changes hide in the
   minor bumps you skip over. For a major, read the migration guide before you
   touch it.

4. **Pin, don't float.** Pin the exact version (prefer an immutable digest where
   the ecosystem supports it) and keep the human-readable tag alongside it.
   Never `:latest` / untagged.

5. **Confirm the rollback works BEFORE you merge.** Rollback is revert-the-change
   + reapply (or re-pin the previous version). Verify the previous version is
   still available upstream (pullable image / published chart / release asset) so
   the escape hatch actually exists the moment you need it.

6. **Verify against the component after applying.** The thing itself came up, no
   new errors / restarts / alerts, its own health check passes, and for storage
   or a database, the data is intact. A green CI gate is necessary, not
   sufficient.
