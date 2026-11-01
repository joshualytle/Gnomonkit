---
description: Activates before drafting an issue or PR to a project you do not own, citing a CVE or changelog as a current risk, or building a local workaround for an upstream gap.
---

> Full trigger text: Before proposing work to a project you do not own, read its tracker including CLOSED items, and check a security advisory against the installed artifact rather than the changelog. Activates before drafting an issue/PR to an upstream project, before citing a CVE or changelog entry as a current risk, and before building a local workaround for an upstream gap.

# Upstream before proposing

Two failures, same root: **treating the historical record as the current state.**

## 1. Read the tracker, including what was rejected

Before drafting an issue or PR to a project you do not own, search its tracker —
**and do not filter to open items.** A closed issue is the higher-value read: it
tells you whether the idea was already proposed, and whether it was rejected on
grounds that will apply to you too.

```bash
gh api -X GET search/issues -f q="repo:<org>/<repo> <keyword> in:title"
gh api -X GET search/issues -f q="repo:<org>/<repo> <keyword> is:closed"
gh api "repos/<org>/<repo>/issues/<n>/comments"    # the WHY lives here
```

Read the maintainer's closing comment in full. A well-argued rejection tells you
the project's actual design constraints, which is worth more than the issue itself
— it usually reveals both what will never be accepted and where the maintainer
*would* take the work.

**Illustration.** Suppose a storage project promises to keep data verbatim, and
someone proposes a filter that redacts credentials on the write path. A
maintainer may reasonably reject it: a lossy filter at ingest is a one-way door
where a false positive destroys data unrecoverably. The same closing comment
often names the boundary they *would* accept — say, the outbound path, where
content actually leaves the machine — where the same patterns and tests apply
unchanged and only the call site moves.

Re-filing the original framing gets closed the same way in a sentence.
Proposing at the boundary the maintainer named is a different conversation.

**When the answer is "they will not take this":** that is a finding, not a defeat.
Keep the local layer, and write down *why* it stays local — a workaround whose
rationale is recorded is maintainable; one that looks like an unexplained
duplicate of upstream gets deleted by a future reader.

## 2. An advisory is history, not a finding

A `CHANGELOG`, `HISTORY.md`, release note or CVE describes a vulnerability that
**existed**. It does not tell you whether the code you have is vulnerable.

Check the artifact you installed:

```powershell
# Does the dangerous construct still exist in the installed package?
Select-String -Path "<site-packages>\<pkg>\*.py" -Pattern 'shell\s*=\s*True' -Recurse
Select-String -Path "<site-packages>\<pkg>\*.py" -Pattern 'os\.system|os\.popen' -Recurse

# Is the vulnerable component even shipped?
Get-ChildItem "<site-packages>\<pkg>" -Recurse -Include '*hook*.sh'
```

**Illustration.** A changelog line such as "fixed shell injection in hooks" can
read as a live risk and hold a feature back. Reading the installed package may
show the fix is *architectural*: no `shell=True`, no `os.system`, subprocesses
spawned with an argv **list**, and the vulnerable legacy `.sh` hooks not
shipped in the wheel at all. The injection class is gone, not patched — and
any real reason to hold off only appears by reading the code.

A stale advisory costs twice: it blocks something safe, and it hides the actual
risk behind a plausible-sounding one.

## The shared discipline

In both cases the record is a *pointer to a question*, never the answer:

| The record says | The question it raises | Where the answer is |
|---|---|---|
| "feature X was requested" | was it accepted, and on what terms? | the closing comment |
| "vulnerability Y was fixed" | is my installed copy affected? | the installed artifact |
| "this is planned" | did it ship, in my version? | the code / release notes for *your* version |

Cite the record; verify against the thing.

## When NOT to use this

Reading a changelog to learn what changed, or a tracker to understand a project's
direction. This is about the moment a historical document is about to drive a
decision — to file, to build, or to hold something back.
