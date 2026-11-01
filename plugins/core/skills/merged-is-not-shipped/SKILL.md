---
description: Activates after merging a PR another system consumes, when reporting work as shipped, or when an integration 'should be working' but the consumer shows old behaviour.
---

# A merge is not a deploy

Reporting a change as shipped because its PR went green is a claim about the
repo, not about production. In a repo that promotes `dev` → `main`, or
`develop` → `release`, merging a PR lands the code in a branch **nothing
deploys from**. The artifact never gets published, and the PR looks complete
from every angle a reviewer checks.

A typical case: a repo adds a `product.json` descriptor so a sibling site can
stop hand-copying product copy. The PR merges into `dev` and sits there, while
the deploying branch is `main`. The file keeps returning 404, the integration
never exists in production, and nobody has reason to look — because the
consumer has a fallback.

## The check

Before calling a change shipped, confirm all three:

1. **It reached the deploying branch.** `git log origin/<deploy-branch> --
   <path>`, or check the file exists on that ref — not on the branch you merged
   into.
2. **A deploy actually ran after it.** A merge with no subsequent successful
   build has changed nothing that anyone can see.
3. **The consumer sees it.** For anything another system fetches, request the
   real URL and read the status code. A 404 is the whole answer.

## Graceful fallbacks hide this indefinitely

What lets this go unnoticed for weeks is the thing that was supposed to make
it safe. In the example above, the consumer fetches the descriptor at build time
and falls back to a committed snapshot on failure, so the site renders correct
copy and the build goes green. Correct-looking output is not evidence the dependency exists.

**A fallback that is silently permanent is a failure, not resilience.** When you
build one, make its use loud — log it at warn level with the reason — and when
you own the consumer, treat "fallback engaged" in a build log as an alert rather
than noise. See `silent-no-op`: green is not done.

## When NOT to use this

Trunk-based repos where merging to the deploy branch *is* the deploy, and the
build that follows is verification enough — though check 3 still applies to
anything a separate system consumes.
