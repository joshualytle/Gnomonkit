# Releasing to the public repository

Gnomonkit is developed in a private repository and published to a public one. The public repository
receives **release commits only**: one signed snapshot per release, holding the files
`release/public-manifest.txt` allows. Development history, pull requests, issues and commit messages
never cross over. Why: a repository's history, pull request text and issue text stay reachable once
public, even after a rewrite, so the only safe public history is one that never held anything else.

## One-time setup

1. **A signing key the agent cannot use on its own.** Agents run as you and can use any key that
   needs nothing from you. Use a hardware-backed key, which needs a touch to sign:

   ```sh
   ssh-keygen -t ed25519-sk -C "release signing"
   ```

   or a key with a passphrase that no agent caches. Then configure the development repository:

   ```sh
   git config gpg.format ssh
   git config user.signingkey ~/.ssh/id_ed25519_sk.pub
   ```

2. **List the key** in `release/allowed_signers` (`<noreply e-mail> <key type> <public key>`), and add
   it on GitHub as a *signing* key so public commits show as Verified.
3. **Set the denylist secret on both repositories** (`gh secret set GNOMONKIT_DENYLIST --repo <repo>`),
   so the public repository can scan contributor pull requests and posted text.
4. **Start the public lineage** once: `python scripts/release_public.py bootstrap` creates an empty
   signed root commit on `public/main` in the development repository.
5. **When the public repository is public:** protect `main` (require signed commits, no force pushes,
   no deletions) so even a mistaken push of an unsigned commit is refused.

## Each release

```sh
python scripts/release_public.py build 0.3.0      # signs the release commit (touch the key)
python scripts/release_public.py check 0.3.0      # gates, tests from the export, personal-content review
python scripts/release_public.py propose 0.3.0    # release PR inside the development repository
#   read the PR diff: it is exactly what the public repository will receive
python scripts/release_public.py publish 0.3.0    # signs the tag (touch the key) and pushes
```

- `check` runs leakcheck on every file and the release message, provenance on everything added since
  the last release, gitleaks over the public lineage, the test suite from the exported tree alone,
  and a **personal-content review**: a model call with no session context whose only job is to find
  text revealing a specific person, their setup, evidence from their own use, or their personal
  reasoning. Patterns cannot find personal reasoning; this review and your read of the diff can.
- A review finding blocks `publish`. Fix the text in development and build again, or, if it is a false
  alarm, record why: `release_public.py accept 0.3.0 <path:line> "<why it is safe>"`. Accepted findings
  are listed in the release PR.
- Never merge the release PR with GitHub's button: that creates a different, unsigned commit.
  `publish` pushes the checked commit itself and the PR shows as merged.

## Signing for the sync gate

Users who put Gnomonkit under the sync gate ([SYNC.md](SYNC.md)) take a release on their own only when
**every** commit since their pin is signed by a key they approved; one unsigned commit holds the whole
update for manual review. So:

- **Sign every commit on the public lineage and every tag.** `build` signs the release commit and
  `publish` signs the tag; `bootstrap` signs the root. Never push an unsigned commit to the public
  `main`, not even a fix-up (branch protection's "require signed commits" refuses one).
- **Publish the release key's public line** in `plugins/core/sync/suggested.allowed_signers`, in
  ssh-keygen's ALLOWED SIGNERS format with the git namespace:

  ```text
  <noreply e-mail> namespaces="git" ssh-ed25519 AAAA...
  ```

  Until a key is filled in, the file holds comments only and core suggests no key. Users can point
  core at another file with `[suggested] signers = "<path>"` in `~/.gnomonkit/sync/config.toml`.
  Publish the key's fingerprint (`ssh-keygen -lf <key>.pub`) where users can check it, such as the
  repository's README and release notes, so they can compare it before merging the drafted approval.

### Rotating the key

1. Make the new key (hardware-backed, as above) and add it to `release/allowed_signers` and to
   `plugins/core/sync/suggested.allowed_signers`, **keeping the old line**.
2. Release that change signed with the **old** key. Users' gates take it on their own; once they update
   core, their next check drafts the overlay pull request that adds the new key.
3. Keep signing with the old key for a release or two, so users have time to merge that pull request.
   Then sign with the new key, and drop the old line from the suggested file in a later release.
   Users remove the old key from their overlay themselves; nothing removes it for them.
4. **If a key is compromised**, don't wait: release the rotation signed with the new key (users' gates
   hold it for review, which is what they should do), and say plainly in the release notes and the
   README which key to remove from `sync/signers/gnomonkit.allowed_signers` in their overlay.

## Contributions on the public repository

Outside contributors open pull requests on the public repository. Their gates run there (file rules
and built-in patterns; a fork's run has no secrets). Accepted changes are ported into development and
reach the public repository in the next release; the contributor is credited in the PR that ports it,
and their pull request is closed with a link to the release.

## What is published

`release/public-manifest.txt` is an allowlist: a path not listed stays private, so a new file is
private until someone adds it. Design docs, plans and `CLAUDE.md` stay private; the benchmark is
published, its results are not (rounds land in the user cache, outside any repository).
