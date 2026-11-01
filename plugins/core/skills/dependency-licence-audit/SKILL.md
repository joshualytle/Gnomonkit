---
description: Activates when adding or updating any dependency, model, font, asset, vendored source or base image, and when writing or loosening a licence guard.
---

> Full trigger text: Every dependency that ships must carry a licence valid for commercial selling, checked across the whole closure the build actually loads, not just the package that was chosen. Activates when adding or updating any dependency - a package, a native library, a build flag that changes what links, an AI model, a font or asset, a vendored source tree, a base image - and when writing or loosening a licence guard.

# Dependency licence audit

The rule: **whenever a dependency is added, audit it to make sure its
licensing is valid for commercial selling.**

The way this usually goes wrong is not the package anyone chose. Native
libraries pull in runtimes by default: a build library that turns OpenMP on
brings the GCC OpenMP runtime (`libgomp`), a distro BLAS package links the
Fortran runtime (`libgfortran`). Nobody adds either on purpose, and neither
appears in any manifest anyone reviews. A licence guard that deliberately
allows "GPL with runtime exception" then lets them through, and the owner
believes the product contains no GPL code until someone looks at what
actually ships. The fix is to build without the defaults that pull them in,
and to make the guard fail on those libraries by name.

Two failures, and the rule covers both: an audit that looks at what was
chosen instead of what shipped, and a guard that makes a licence decision
the owner was never asked.

## When it applies

Anything that changes what ends up inside, or beside, what you sell:

- a package (any ecosystem), including a version bump that changes the licence
- a native library, a system package a build links against, a base image
- **a build flag that changes what links** (`-fopenmp`, `USE_BLAS=ON`, a
  backend toggle, a static/dynamic switch)
- an AI model, a font, an icon set, a sample file, any bundled asset
- a vendored or copied source tree, however small

## 1. Audit the closure that ships, not the thing you picked

The package you chose is the smallest part of the answer. After building,
list what the shipped artefacts actually load, and diff it against the
previous build:

| Ships as | Enumerate with |
|---|---|
| Linux ELF | `readelf -d <lib> \| grep NEEDED`, recursively; `ldd` on the final binary |
| Windows PE | `dumpbin /dependents <dll>`, or `objdump -p <dll> \| grep "DLL Name"` |
| Packages | the lockfile's full transitive set, not the manifest |
| Container | the image's installed-package list (`dpkg -l`, `rpm -qa`, an SBOM) |
| Installer | the file list of the installer itself, unpacked |

Every **new** entry gets its licence read from the source (the project's
`LICENSE`, the distro package's copyright file), not inferred from its name or
from what the parent library is licensed under.

## 2. Classify, and know which class is yours to decide

| Class | Examples | Who decides |
|---|---|---|
| Permissive | MIT, BSD, Apache-2.0, zlib, ISC | Fine. Add the notice to the project's third-party notices in the same change. |
| Copyleft | GPL, LGPL, AGPL, MPL | **Owner, before it lands.** |
| GPL with a runtime or linking exception | GCC Runtime Library Exception, Classpath exception | **Owner, before it lands.** |
| Non-commercial, source-available, custom or field-of-use terms | CC-BY-NC, BSL, SSPL, a model's own terms of use | **Owner, before it lands.** |
| No licence found | a repo with no `LICENSE`, a blob with no provenance | Treat as all rights reserved. Owner. |

**An exception does not make it "not GPL" for this purpose.** It may well make
the dependency acceptable, but that is a judgement about the product, its
distribution and its promises, and it belongs to the owner. "It's only the
runtime" is the exact reasoning that ships the failure above.

Raise it as a numbered question before the code reaches the main branch: what
the dependency is, what pulled it in, its licence and any exception, and the
cheaper alternatives you found (below). Not in a PR footnote after it merged.

## 3. Look for the cheaper alternative first

Most unwanted licences arrive by default, so they can usually be removed by
choice:

- **A build flag that drops it** - a library's own thread pool instead of
  OpenMP, its built-in kernels instead of an external BLAS, a feature toggle
  that removes an optional codec.
- **A permissive equivalent** - the same capability under MIT/BSD/Apache.
- **A vendored permissive implementation** of the small piece actually used.
- **Dynamic linking or a separate process**, where the licence permits it, is
  a mitigation to put in front of the owner, not a reason to skip asking.

Bring the alternative with the question. "Here is a GPL runtime, and here is
the flag that removes it at no cost" is a decision the owner can make in one
line.

## 4. Guards encode owner decisions, not agent assumptions

A licence guard that waves a class through is a decision made silently on the
owner's behalf, re-made on every build, by nobody. So:

- Every allowed class in a guard traces to an explicit owner answer. If you
  cannot point at the answer, the class fails.
- A guard checks the **built artefacts** (the dependency lists above), not
  only the manifest.
- Known-unwanted libraries are named and fail the build, so a dependency that
  drifts back in through a default is caught the day it returns.
- Never loosen a guard to get a build green. A guard failing on a licence is
  the question being asked; answer it with the owner.

## When NOT to use this

Development-only tooling that never ships (a linter, a test runner, a CI
action) does not need the owner, although its licence still has to permit
the use. Re-check if it ever starts producing something that ships, such as a
code generator whose output carries its licence.
