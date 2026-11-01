"""Gnomonkit supply-chain review: what a dependency update actually changes.

The engine downloads the released artifacts (registry tarball, wheel, sdist, crate or pub archive)
for the old and the new version, reads them in memory, diffs them file by file and runs
deterministic rules over what was added. Two entry points share it:

  patch    a dependency bump in this repository's lockfile or manifest
  release  one package and a version range

Standard library only. Downloaded code is never executed, imported or extracted beside code
that runs; the optional local-model summary is off unless configured and never decides a flag.
"""

__version__ = "0.1.1"
