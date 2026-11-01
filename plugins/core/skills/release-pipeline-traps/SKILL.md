---
description: Activates when writing or debugging a release workflow, uploading artifacts to R2/S3/B2, wiring a release event, or when a release job dies after the build succeeded.
---

> Full trigger text: Release automation fails in ways that look like flaky infrastructure but are not — token-created releases raise no events, object-storage uploads get rejected by default checksums, and job timeouts sized for the build get eaten by the upload. Activates when writing or debugging a release workflow, publishing artifacts to object storage (R2, S3, B2), wiring anything to a release event, or when a release job dies after the build succeeded.

# Release pipeline traps

A release that takes several attempts is usually failing in the release
plumbing, not the application code — and most of the causes below look
exactly like "the infrastructure is flaky."

## A release created with GITHUB_TOKEN raises no `release: published` event

GitHub refuses to trigger workflows from events raised by the built-in
`GITHUB_TOKEN`, so it cannot recurse. If your release job runs
`gh release create` with `secrets.GITHUB_TOKEN`, **every workflow keyed on
`release: [published]` stays silent.**

The failure is invisible: the release publishes, the follow-up workflow
never runs, and its trigger looks correct in the file. A typical sign: a
follow-up sync that has been "enabled" for days has never once fired on
its own; every run in its history is a manual dispatch, which nobody
notices because the manual runs succeed.

**Rules:**

- Never rely on `release: published` for a release your own workflow cuts.
  Dispatch the follow-up explicitly (`gh workflow run <file> -f tag=...`)
  from the release job, with `permissions: actions: write`.
- Keep the event trigger as a fallback for hand-cut releases, and say so
  in a comment so the next reader does not "fix" the dispatch away.
- A dispatch must never fail the release: the release object already
  exists by then. Warn, do not exit non-zero.
- The same rule covers `push`, `create` and every other event: a
  `GITHUB_TOKEN` action does not wake another workflow. Use a PAT only if
  you genuinely need the event itself.

**Verify, do not assume:** check the follow-up workflow's run history and
look at the `event` column. If every row says `workflow_dispatch`, the
trigger has never worked.

## Default checksums break uploads to S3-compatible storage

`botocore` 1.36 and newer attach a CRC32 checksum to every upload and ask
for one back. Cloudflare R2 (and other S3-compatible stores) answer with
**HTTP 500 InternalError** when they get it — most visibly on
`CompleteMultipartUpload`, but plain `PutObject` fails the same way.

It presents as a flaky provider: retries do not help, and the error names
the storage service, not the client library.

**Fix, and it is the vendor's documented one:**

```yaml
env:
  AWS_REQUEST_CHECKSUM_CALCULATION: when_required
  AWS_RESPONSE_CHECKSUM_VALIDATION: when_required
```

Set it on every step that touches the bucket, not just the one that
failed. A pinned older `awscli` also works but rots.

## Do not "harden" an upload by disabling multipart

Chasing the checksum failure above, the obvious-looking belt-and-braces
move is to force everything under the 5 GiB single-`PUT` ceiling:

```
aws configure set default.s3.multipart_threshold 4GB   # do not do this
```

That removes multipart's parallel chunks. A few-hundred-megabyte file can
go from a one-to-two-minute upload to **most of an hour**, and the job hits
its timeout mid-upload — which is easily misread as a dying runner.

Multipart is the fast path. If multipart is failing, fix the cause (the
checksum setting above), do not switch it off.

## Size the job timeout for the upload, not the build

A release job's clock covers packaging, signing **and** shipping the
bytes. Artifacts grow; upstream bandwidth does not. When two build jobs
run concurrently on self-hosted runners they share one uplink, so each
one's upload can take several times its solo duration.

A typical case: a job builds, packages and signs correctly, then dies at
exactly its `timeout-minutes` inside the upload step — after the artifact
has merely doubled in size since the last successful release.

**Rules:**

- Read the step timings before blaming the runner. A job that ends within
  seconds of its `timeout-minutes` was killed by the timeout, whatever the
  log's last line says. "The operation was canceled" is what a timeout
  looks like; an actual runner death says "the runner has received a
  shutdown signal."
- Give release jobs headroom (2-3x the observed upload), and revisit it
  when artifact sizes jump.
- If uploads are slow enough to matter, the real fixes are smaller
  artifacts or staggered jobs — a bigger timeout only stops the bleeding.

## The publish job has its own clock — and a timeout skips every later step

The build jobs are not the only ones shipping bytes. A separate publish
job that downloads the artifacts and runs `gh release upload` moves the
same hundreds of megabytes again, to GitHub this time, and it usually has
a much smaller default timeout because "it only creates a release."

Picture a publish job with, say, `timeout-minutes: 30` while the builds
have 180. It times out mid-upload: GitHub marks the job **cancelled** (not
failed), the release exists with only the tiny `.sig` assets, and the
step after the upload — say, dispatching a website sync — is **skipped**,
so no install is ever offered the update. Nothing alerts, because from the
outside the release looks published.

**Rules:**

- Every job that uploads gets the upload-sized timeout, not just the
  build jobs.
- After a release, count the assets on the release object and check that
  each follow-up workflow actually ran. A "cancelled" job skips its
  remaining steps silently; the release page will not tell you.
- Repair without a rebuild when the CDN copy is complete: download it,
  verify it against the signature, upload it to the release, then
  re-dispatch the follow-ups by hand. Keep that repair as a
  `workflow_dispatch` workflow on the default branch — GitHub refuses to
  dispatch a workflow file that only exists on a feature branch, and a
  CI runner is a better place than a workstation to push hundreds of
  megabytes (long uploads over typical consumer links are prone to resets).

## Whether the CDN upload may fail is a product question

Before making an upload step non-fatal, find out what the public download
links resolve to. If the website's download buttons or the update feed
point at the CDN bucket, a skipped upload publishes broken links — the
release object alone is not enough.

Grep the site's download-facts generator for the URL base before deciding.
If the CDN is load-bearing, the upload stays mandatory and the timeout
goes up instead.

## The pattern behind all six

Each of these failures is diagnosed correctly only by reading **timings
and the event history**, not the last error line. Retrying an unchanged pipeline
is not a diagnosis, and neither is "the runner is flaky." When a release
fails twice the same way, stop re-running it and go read what the step was
actually doing when it died.

## Staged promotion pipelines: more plumbing, more traps

When one version is promoted through stage branches (for example
`dev → test → beta → main`), each hop adds plumbing that can fail while the
application builds and signs cleanly on every attempt.

### Every artifact name derives from the ONE resolved version

When the release resolves a version that differs from the manifest — a
`-beta.N` suffix derived at run time — every file name, storage prefix and
lookup must come from that single resolved output. Suppose one packaging
step reads the manifest (`1.2.0`) while the storage prefix, the bundle
tarball and the publish job all use the resolved `1.2.0-beta.1`. That
package is built, signed and uploaded, and the publish job 404s looking
for it under the name it expects.

The test stage cannot catch this class: there the resolved version equals
the manifest, so every name agrees. Pass the resolved version into every
producing step as an explicit input, and **assert the expected file name in
the job that produces it** — a mismatch then fails with a directory listing
where the file is made, not as a 404 two jobs later.

### `gh release create` without `--target` tags the default branch

GitHub creates the tag at the default branch's HEAD unless told otherwise.
When the default branch is a development branch, every release built from a
stage branch is tagged at the wrong commit: a stable tag and its beta can
both point at `dev`'s tip, and an older stable tag may not be on `main` at
all. It goes unnoticed until something checks out a tag.

Always `gh release create "$TAG" --target "$GITHUB_SHA"`. Then anything
that reads "the file at the release" (notes, a manifest, a changelog) reads
the commit that was built.

### Read release notes at the release's commit, not the workflow's branch

A follow-up workflow dispatched with `--ref main` runs main's copy of the
workflow **and reads main's files**. A beta's release notes live on the
beta branch for days before they reach main, so a sync reading main will
refuse (or fail) to publish an update feed for a version whose notes it
cannot find. Fetch the notes at the release tag (which, per the previous
trap, has to point at the right commit).

And a pre-release shares its base version's notes: `1.2.0-beta.1` is a
build of `1.2.0`. Strip the suffix before looking up the section; nobody
writes a heading per beta.

### Cancelling a CI run whose commit heads an open PR turns that PR red

Check runs attach to commits, not to runs. A promotion PR whose head is a
pipeline branch displays the CI of that branch's latest push. Cancel that
run to "free the runner" and every cancelled job reads `fail` on the PR —
which can cost a full re-run of CI before a merge guard will pass. Cancel a
pipeline branch's run only **after** the hop that uses it has merged and
the next branch's own runs have started.

### Tooling PRs wait until the release has published

Self-hosted runners execute one job each and GitHub does not prioritise
release runs: whoever queued first wins. A ready tooling PR's build jobs
can take every runner while the release build sits `queued` with no
release run in progress — which looks exactly like dead runners. The way
out is cancelling the tooling run and re-running it later.

A draft PR still runs the light jobs (lint, unit tests, scanners) — that is
all a script change needs validated. Open non-release PRs as drafts during
a release and mark them ready afterwards. Diagnose a "queued" job by
listing the run's jobs with their `runner_name`: a job that names a runner
and is `in_progress` on another run is the answer.

### Verify artifacts by bytes, not by headers

Two `HEAD` requests for the same CDN object a minute apart can return
different `Content-Length`s when a follow-up workflow re-uploads the
release assets to the CDN after publish. A size read from a header is
a snapshot of a moving target. Download the CDN copy and the release asset
and compare SHA-256 before saying they match — and compare the signature
file the same way, since a mismatched pair fails verification in the field.

### A long-lived build folder ships what an old config installed

Local test installers packed from the same `build/.../Release` folder for
weeks drift. A CMake install step copies what the current config names and
never deletes what an older config put there, and a packager that takes
the whole folder ships it all: libraries the project stopped bundling,
files a later filter removed, and a package much larger than a clean
build. A fresh worktree hits the opposite trap: a git-ignored input folder
(a pinned third-party binary) is absent, and the build succeeds, much
smaller, with that dependency missing.

- **Package from a clean output folder**: a fresh worktree, or delete the
  release folder first. "It built" says nothing about what is inside.
- **Diff the packaged file list against the last good one** (or the CI
  artifact) and account for every difference before handing it over. A
  size change of more than a few percent is a question, not a detail.
- **Inputs that live outside git are part of the recipe.** Downloaded
  binaries and pinned tools are fetched the way CI fetches them, and a
  copy already on disk is checked against the pin's version record, not
  trusted because it is there. A stale copy one patch behind the pin ships
  silently in every local build after the bump.
