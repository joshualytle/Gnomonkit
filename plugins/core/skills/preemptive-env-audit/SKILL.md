---
description: Activates when provisioning a runner, VM or container, debugging 'command not found' in CI, or when a job fails on a missing tool: audit every workflow, give one checklist.
---

> Full trigger text: Before any workload first runs on a new or rebuilt machine, VM, CI runner, or container, audit EVERY workflow/script that can target it and hand the owner ONE complete install checklist — never discover missing tools one failure at a time. Activates when provisioning a runner/VM, debugging a "command not found" in CI, or when a job fails on a missing tool.

# Preemptive environment audit

On a fresh machine (a new CI runner, a new VM), discovering missing tools one
failed run at a time costs a full round trip per tool. The owner should be told
everything the machine needs up front, not one issue at a time.

**The rule:** the first missing-tool failure on a machine means the audit is
overdue. Immediately grep every workflow, script, and third-party action
that can run there for tool invocations and produce the COMPLETE checklist —
then the owner visits the machine once.

**How to audit:**
1. Grep all CI workflows + scripts for: package installs (`choco`, `apt-get`,
   `winget`, `pip`, `npm -g`), direct binaries (`aws`, `jq`, `openssl`,
   `curl`, compilers, signers), shell declarations (`shell: pwsh` needs
   PowerShell 7), and downloads.
2. **Inspect third-party actions' internals too** — their `action.yml` shell
   and runtime requirements (e.g. azure/trusted-signing-action hardcodes
   `pwsh`; azure/login needs the `az` CLI preinstalled on self-hosted).
3. Known unelevated-Windows-runner traps: `setup-python` cannot write the
   tool cache (HKLM denied); `choco install` at job time needs admin; `pwsh`
   is absent on stock Windows. Linux: passwordless sudo is usually scoped
   (e.g. apt-get only) — installers needing broader sudo must go user-local.
4. After ANY install that changes PATH on a machine hosting a runner
   SERVICE: restart the service — services cache their environment from
   start time, so a fresh install stays invisible until then.
5. Write the checklist into the project's runner/infra doc in the same
   change, and update it in the SAME commit whenever a workflow gains a new
   tool dependency.

**Log friction when you hit it.** The audit's sibling rule, for limits found
mid-session rather than pre-provisioning: any environment limit or workaround
an agent discovers (sandbox egress, orphaned ports, model naming, tunnel
quirks, path/heredoc traps) gets logged in the project's friction log
(`docs/AGENT_FRICTION_LOG.md` by convention — one dated entry per fact,
prune entries that stop being true) in the same session, so the next session
doesn't rediscover it one failure at a time.
