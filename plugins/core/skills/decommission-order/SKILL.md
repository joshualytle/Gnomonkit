---
description: Activates when deleting a cloud resource or removing a feature (infrastructure, onboarding text, policy, feature list), or when a deploy fails on a binding to something cleaned up.
---

> Full trigger text: Activates when deleting or tearing down any cloud resource — a Worker, KV/R2/D1 store, queue, bucket, database, service account — and whenever removing a feature at all, whether it had infrastructure behind it or only the copy that promised it: onboarding text, in-app help, a privacy policy, a website feature list. Also when a deploy starts failing on a binding or reference to something that was cleaned up, and when a secret, key or credential is being moved, wrapped or deleted from where code used to read it.

# Remove the reference before the resource

Deleting a resource does **not** remove the things pointing at it. The pointer
survives, now aimed at nothing, and the failure surfaces later — at the next
deploy, on a platform that validates references at deploy time, with an error
naming a binding rather than the cleanup that caused it. Whoever hits it is
usually not the person who did the deleting.

A typical case: a static site drops a form, and the Worker and KV namespace
behind it are deleted, but the site project keeps its bindings to both.
Nothing is broken *yet* — the pages never read them — but a platform that
validates bindings when it creates a deployment will fail the next build,
because it is configured to bind things that no longer exist.

## The order

1. **Remove the reference** — the binding, mount, env var, DNS record, IAM
   grant, service connection — and let one deploy go out clean.
2. **Then delete the resource.**
3. **Then verify**, by listing what the platform still thinks is configured, not
   by assuming the delete cascaded.

If the order slips, fix the dangling reference immediately rather than at the
next convenient moment: the config is wrong from that instant, and the next
deploy may not be one you are watching.

## Config APIs lie about clearing

Removing a key from a config map is often not the same call as setting the map
empty. On Cloudflare Pages, `PATCH`ing `kv_namespaces` or `services` to `{}`
returns `success: true` and changes nothing; only an explicit per-key `null`
deletes an entry. **Re-read the config after any clearing call** and confirm the
entry is actually gone — a success response is not evidence.

The same shape shows up in Terraform state, Kubernetes manifests applied with
merge patches, and any partial-update API. Assume clearing needs a different
verb than setting, and verify.

## The other dangling reference is a claim

A binding fails loudly at the next deploy. A sentence does not. When a feature
comes out, the places that PROMISED it keep promising it, and nothing tells
you: the onboarding dialog, the in-app help, the privacy policy, the FAQ, the
marketing page, the feature list a salesperson reads from. Each one goes on
describing a capability the product no longer has, to people who then go
looking for it.

This can run for months. Picture an app that drops a third-party lookup
integration: long afterwards, the welcome dialog every new user sees still
describes the lookup, the in-app help still explains it, the privacy policy
still lists the data it sent, and the public website still advertises it.
Nobody lied; the removal was simply never swept.

**A removal is not done until the claims are swept.** In the same change:

- Grep for the feature by NAME and by what it DID — a capability survives
  under words that do not contain its name ("look it up for you", "find one
  someone else made").
- Sweep onboarding and first-run copy specifically. It is shown to the people
  least able to tell that it is wrong, and it is the copy nobody re-reads.
- Sweep the policies. A privacy policy or a data-flow table describing a
  transfer that no longer happens is wrong in the direction that gets
  reviewed, and it will be read by someone who trusts it.
- Sweep whatever drives public copy: the feature list, the FAQ, the pricing
  page, the reference document a website is written from.
- Public claims are the owner's to change, not yours. Fix what is inside the
  product, and hand the owner the exact list of public strings with the two
  options — correct the copy, or build the thing.

The tell that this has happened to you: writing a menu label and wondering
whether the app can actually do the thing it says. Go and check the code
before you write the label, not after.

## Secrets are resources too

A common way this goes wrong: a security change wraps a stored key under
the user's credential and deletes the plaintext copy. The main code path
that reads the key is updated; a secondary one (backups, export, a
background job) still reads the old location, gets nothing, and fails in a
confusing way. Every migrated install is broken there, and the tests pass
because they never run the migration first.

- **Before deleting or moving a secret, grep every reader by name** and
  list them in the PR. `loadStoredKey`, `readKey`, the env var, the file
  path. Each reader is a pointer, exactly like a binding.
- **A reader that cannot be updated is a decision, not an oversight.** A
  background job with no session cannot get a key the session holds. Say
  so where the user looks, stop scheduling it, and open the decision.
- **Test the after state, not the before.** One test that performs the
  migration and then exercises each former reader. A suite that only tests
  fresh installs passes on the broken code.

## When NOT to use this

Ephemeral or preview environments torn down wholesale, where nothing outlives
the teardown to hold a stale pointer.
