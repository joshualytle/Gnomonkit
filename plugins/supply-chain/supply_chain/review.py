"""The engine: review one package release, old version to new, from its released artifacts.

Both entry points end here. `patch` calls it once per dependency a lockfile or manifest bumped;
`release` calls it for the package and range a person named.

What decides a flag (deterministic, never the model):
  registry      the new version is yanked or deprecated; a first-time publisher; npm install
                scripts added or changed; a brand-new package; a name close to a popular one
  integrity     the downloaded bytes do not match the registry's published digest
  archive       members that escape the package folder, links, device files
  content       rules.py over every added line of every changed file
  history       a commit author new to the project whose changes ship in the artifact or touch
                install, build, binary or obfuscated files; a build or install file present in
                the artifact but absent from the tagged source
"""
from __future__ import annotations

import datetime as dt
import hashlib
import base64
import os
from dataclasses import dataclass, field

from . import archive, artdiff, history, llm, registry, text, typosquat
from .netfetch import HTTP, FetchError

NEW_PACKAGE_DAYS = 30
FRESH_RELEASE_HOURS = 48


@dataclass
class Flag:
    severity: str            # high | medium
    rule: str
    what: str                # already plain-escaped
    path: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Report:
    eco: str
    name: str
    old: "str | None"
    new: "str | None"
    change: str = "release"            # release | bumped | new
    from_range: bool = False
    direct: "bool | None" = None
    lockfile: str = ""
    page: str = ""
    artifact_old: str = ""
    artifact_new: str = ""
    verified: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    unchecked: list = field(default_factory=list)
    publisher: dict = field(default_factory=dict)
    committers: "history.Result | None" = None
    model: "dict | None" = None
    error: "str | None" = None

    @property
    def flagged(self) -> bool:
        return bool(self.flags)

    @property
    def high(self) -> list:
        return [f for f in self.flags if f.severity == "high"]

    def by_kind(self) -> dict:
        out: dict = {}
        for fc in self.changes:
            for k in artdiff.ordered(fc.kinds):
                out.setdefault(k, []).append(fc.path)
        return out

    def as_dict(self) -> dict:
        return {
            "schema": "gnomonkit/supply-chain-review/v1",
            "eco": self.eco, "name": self.name, "old": self.old, "new": self.new, "change": self.change,
            "from_range": self.from_range, "direct": self.direct, "lockfile": self.lockfile, "page": self.page,
            "artifact_old": self.artifact_old, "artifact_new": self.artifact_new, "verified": self.verified,
            "counts": self.counts, "flagged": self.flagged,
            "flags": [f.as_dict() for f in self.flags],
            "by_kind": {k: v[:100] for k, v in self.by_kind().items()},
            "changes": [fc.as_dict() for fc in self.changes[:500]],
            "publisher": self.publisher,
            "committers": self.committers.as_dict() if self.committers else None,
            "notes": self.notes, "unchecked": self.unchecked, "model": self.model, "error": self.error,
        }


@dataclass
class Options:
    committers: str = "auto"           # auto (GitHub repo from the registry) | off | a clone path | owner/name
    old_ref: "str | None" = None
    new_ref: "str | None" = None
    prefer: str = "auto"               # PyPI: auto | sdist | wheel
    extract_dir: "str | None" = None
    diff_out: "str | None" = None
    model: bool = True                 # use the local model when configured
    now: "dt.datetime | None" = None


class Engine:
    def __init__(self, http: HTTP, regs: "registry.Registries | None" = None, model: "llm.Local | None" = None) -> None:
        self.http = http
        self.regs = regs or registry.Registries(http)
        self.model = model if model is not None else llm.Local()

    # ------------------------------------------------------------------------- helpers
    def _download(self, art: registry.Artifact, rep: Report) -> "archive.Archive | None":
        try:
            data = self.http.artifact(art.url, art.sha256)
        except FetchError as e:
            # Not reviewed is never "nothing flagged": an error makes the CLI exit 2.
            rep.error = "download failed for %s: %s" % (text.plain(art.filename, 80), text.plain(str(e), 120))
            return None
        ok = verify(art, data)
        if ok is False:
            rep.flags.append(Flag("high", "digest-mismatch",
                                  "%s does not match the digest the registry publishes; not diffed"
                                  % text.code(art.filename)))
            return None
        if ok is None:
            rep.flags.append(Flag("medium", "no-digest",
                                  "the registry publishes no digest for %s, so the download cannot be verified"
                                  % text.code(art.filename)))
        else:
            rep.verified.append(art.filename)
        try:
            arc = archive.read(data, art.filename)
        except archive.ArchiveError as e:
            # The registry serves an archive this reader cannot parse, while an installer may: that is
            # exactly the gap a hidden payload would use, so it is a flag, not a gap in coverage.
            rep.flags.append(Flag("high", "unreviewable-artifact",
                                  "%s could not be read (%s); an installer's reader may disagree, so it is "
                                  "unreviewed" % (text.code(art.filename), text.plain(str(e), 120))))
            return None
        for sev, rule, path, why in arc.findings:
            rep.flags.append(Flag(sev, rule, "%s in %s" % (why, text.code(art.filename)), text.plain(path, 200)))
        if arc.truncated:
            rep.unchecked.append("%s is larger than the read limits; only part of it was diffed"
                                 % text.plain(art.filename, 80))
        return arc

    def _source(self, opt: Options, pkg: registry.Package, rep: Report):
        c = (opt.committers or "auto").strip()
        if c == "off":
            return None
        try:
            if c == "auto":
                # The OLD version's repository: the new release's metadata is written by whoever
                # published it, and could point at a clean fork with a tidy history.
                old_repo = registry.github_repo(self.regs.version_repo(pkg, rep.old)) if rep.old else None
                new_repo = registry.github_repo(self.regs.version_repo(pkg, rep.new))
                if old_repo and new_repo and old_repo.lower() != new_repo.lower():
                    rep.flags.append(Flag("medium", "repository-changed",
                                          "the source repository moved from %s to %s in this release; the old one "
                                          "is used for the history check" % (text.code(old_repo), text.code(new_repo))))
                repo = old_repo or new_repo
                if repo and not old_repo:
                    rep.unchecked.append("the old version names no repository; the new version's (publisher-written) "
                                         "repository was used for the history check")
                if not repo:
                    repo = registry.github_repo(pkg.repo_url)
                    if repo:
                        rep.unchecked.append("repository taken from package-level metadata, which follows the "
                                             "latest release")
                if not repo:
                    rep.unchecked.append("new-committer check not run: the registry names no GitHub repository "
                                         "(pass --committers with a local clone or owner/name)")
                    return None
                return history.GitHubSource(repo, self.http)
            if os.path.isdir(c):
                return history.LocalGit(c)
            return history.GitHubSource(c, self.http)
        except history.SourceError as e:
            rep.unchecked.append("new-committer check not run: %s" % text.plain(str(e), 160))
            return None

    # ------------------------------------------------------------------------- the review
    def review(self, eco: str, name: str, new: "str | None", old: "str | None" = None, opt: "Options | None" = None,
               change: str = "release") -> Report:
        opt = opt or Options()
        eco = registry.eco_name(eco)
        now = opt.now or dt.datetime.now(dt.timezone.utc)
        rep = Report(eco, name, old, new, change=change)
        try:
            pkg = self.regs.package(eco, name)
        except LookupError as e:
            rep.error = text.plain(str(e), 200)
            if change == "new":
                rep.flags.append(Flag("high", "not-on-registry",
                                      "%s is not on the public %s registry: a private name resolved publicly "
                                      "is how dependency confusion works" % (text.code(name), eco)))
            return rep
        except FetchError as e:
            rep.error = "registry lookup failed: %s" % text.plain(str(e), 160)
            return rep
        rep.page = pkg.page
        rep.unchecked += pkg.not_checked
        if new in (None, "", "latest"):
            new = pkg.latest
        rep.new = new
        nv = pkg.versions.get(new or "")
        if nv is None:
            rep.error = "version %s is not published" % text.plain(str(new), 60)
            return rep
        if change != "new" and not old:
            old = pkg.previous(new)
            if old is None:
                rep.notes.append("no earlier release: reviewed as a first release")
                change = "new"
        rep.old, rep.change = old, change
        ov = pkg.versions.get(old) if old else None
        if old and ov is None:
            rep.error = "version %s is not published" % text.plain(old, 60)
            return rep

        self._registry_checks(pkg, ov, nv, rep, now)

        old_art, new_art = registry.pick_artifacts(eco, ov, nv, opt.prefer)
        if new_art is None:
            rep.error = "the registry lists no downloadable artifact for %s" % text.plain(new, 60)
            return rep
        if ov is not None and old_art is None:
            rep.unchecked.append("no artifact of the same kind for %s; diffed against nothing" % text.plain(old, 60))
        rep.artifact_new = new_art.filename
        rep.artifact_old = old_art.filename if old_art else ""
        a_new = self._download(new_art, rep)
        a_old = self._download(old_art, rep) if old_art else archive.Archive()
        if a_new is None or a_old is None:
            return rep

        changes = artdiff.diff(a_old.files, a_new.files)
        rep.changes = changes
        rep.counts = {"added": sum(1 for c in changes if c.status == "added"),
                      "removed": sum(1 for c in changes if c.status == "removed"),
                      "modified": sum(1 for c in changes if c.status == "modified"),
                      "files_new": len(a_new.files)}
        seen = set()
        for fc in changes:
            for h in fc.hits:
                key = (h.rule, h.path)
                if key in seen:
                    continue
                seen.add(key)
                where = "%s:%s" % (h.path, h.line) if h.line else h.path
                what = h.why + (" -- `%s`" % h.excerpt.replace("`", "'") if h.excerpt else "")
                rep.flags.append(Flag(h.severity, h.rule, what, text.plain(where, 200)))

        if change == "new":
            self._new_package_checks(pkg, nv, rep, now)

        src = self._source(opt, pkg, rep) if (old and change != "new") else None
        if src is not None:
            res = history.check(src, name, old, new, changes, opt.old_ref, opt.new_ref)
            rep.committers = res
            if not res.ran:
                rep.unchecked.append("new-committer check %s" % text.plain(res.status, 200))
            # Every cap the check hit is a gap in coverage, not a footnote.
            rep.unchecked += ["new-committer check: %s" % text.plain(u, 200) for u in res.unchecked]
            for na in res.new_authors:
                if na.severity in ("high", "medium"):
                    kinds = ", ".join(na.kinds) or "no sensitive kinds"
                    rep.flags.append(Flag(
                        na.severity, "new-committer",
                        "%s has no commit before %s and wrote %d commit(s) in this release, touching %d file(s) "
                        "(%s)%s" % (text.code(na.author), text.code(res.old_ref), len(na.commits), len(na.files),
                                    kinds, "; %d of them ship in the artifact" % len(na.shipped) if na.shipped else "")))
            for path, kinds in res.not_in_source_flagged:
                rep.flags.append(Flag("medium", "not-in-source",
                                      "a %s that is in the artifact but not in the tagged source"
                                      % " / ".join(k.replace("_", " ") for k in artdiff.ordered(kinds & {"install_script", "build_file"})),
                                      text.plain(path, 200)))

        if opt.model:
            subject = "%s %s %s -> %s" % (eco, name, old or "(none)", new)
            body = []
            for fc in sorted(changes, key=lambda c: (-len(c.hits), -len(c.kinds & artdiff.SENSITIVE), c.path))[:8]:
                body.append("FILE %s (%s)" % (fc.path, fc.status))
                body += [ln[:400] for _n, ln in fc.added_text[:80]]
            m = self.model.summarise(subject, "\n".join(body)) if body else {"status": "not needed: no text changes"}
            rep.model = m

        if opt.extract_dir:
            try:
                n1, s1 = archive.extract_to(a_new, os.path.join(opt.extract_dir, "new"))
                n2, s2 = archive.extract_to(a_old, os.path.join(opt.extract_dir, "old")) if old_art else (0, [])
                rep.notes.append("extracted %d + %d files for reading under %s (never run them)"
                                 % (n2, n1, text.plain(opt.extract_dir, 200)))
                for side, skipped in (("new", s1), ("old", s2)):
                    for path, why in skipped[:50]:
                        rep.unchecked.append("not extracted (%s): %s %s" % (side, text.plain(path, 160), text.plain(why, 120)))
                    # A stream name or a file/folder clash hides one file behind another on extraction.
                    # Reserved device names (aux.js) are skipped and listed, not flagged: some honest packages have them.
                    if any(":" in p or "clash" in w for p, w in skipped):
                        rep.flags.append(Flag("medium", "unportable-names",
                                              "the %s archive holds names that collide or open alternate data "
                                              "streams when extracted on Windows" % side))
            except (archive.ArchiveError, OSError) as e:
                rep.unchecked.append("extract failed: %s" % text.plain(str(e), 160))
        if opt.diff_out:
            try:
                with open(opt.diff_out, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(artdiff.unified(a_old.files, a_new.files, changes))
            except OSError as e:
                rep.unchecked.append("could not write the diff: %s" % type(e).__name__)
        rep.flags.sort(key=lambda f: (0 if f.severity == "high" else 1, f.rule, f.path))
        return rep

    def _registry_checks(self, pkg, ov, nv, rep: Report, now: dt.datetime) -> None:
        if nv.yanked:
            rep.flags.append(Flag("medium", "yanked", "%s is yanked on the registry" % text.code(nv.version)))
        if nv.deprecated:
            rep.flags.append(Flag("medium", "deprecated", "deprecated: %s" % text.plain(nv.deprecated, 160)))
        if nv.published:
            age_h = (now - nv.published).total_seconds() / 3600
            if 0 <= age_h < FRESH_RELEASE_HOURS:
                rep.notes.append("published %.0f hours ago: most malicious releases are caught within days, "
                                 "so a fresh release has had the least scrutiny" % age_h)
        if pkg.eco in ("npm", "cargo"):
            earlier = {v.publisher.lower() for v in pkg.versions.values()
                       if v.publisher and v.published and nv.published and v.published < nv.published}
            rep.publisher = {"old": ov.publisher if ov else None, "new": nv.publisher,
                             "first_time": (nv.publisher.lower() not in earlier) if (nv.publisher and earlier) else None}
            if rep.publisher["first_time"]:
                rep.flags.append(Flag("medium", "new-publisher",
                                      "%s published this version and no earlier one" % text.code(nv.publisher)))
            elif ov and ov.publisher and nv.publisher and ov.publisher.lower() != nv.publisher.lower():
                rep.notes.append("publisher changed from %s to %s (both have published before)"
                                 % (text.code(ov.publisher), text.code(nv.publisher)))
            if nv.publisher is None:
                rep.unchecked.append("the registry does not say who published %s" % text.plain(nv.version, 60))
        if pkg.eco == "npm" and ov is not None:
            if ov.maintainers and nv.maintainers and ov.maintainers != nv.maintainers:
                added = sorted(nv.maintainers - ov.maintainers)
                if added:
                    rep.notes.append("maintainers added since %s: %s" % (text.plain(ov.version, 40),
                                                                        ", ".join(text.code(a) for a in added[:10])))
            for k, cmd in nv.install_scripts.items():
                before = ov.install_scripts.get(k)
                if before is None:
                    rep.flags.append(Flag("high", "install-script-added",
                                          "new `%s` script: %s" % (k, text.code(cmd) if len(cmd) < 120 else text.plain(cmd, 120)),
                                          "package.json"))
                elif before != cmd:
                    rep.flags.append(Flag("medium", "install-script-changed",
                                          "`%s` script changed to %s" % (k, text.code(cmd) if len(cmd) < 120 else text.plain(cmd, 120)),
                                          "package.json"))
        if rep.publisher.get("first_time") and (nv.install_scripts or any(f.severity == "high" for f in rep.flags)):
            for f in rep.flags:
                if f.rule == "new-publisher":
                    f.severity = "high"
                    f.what += ", and the release carries an install script or another high finding"

    def _new_package_checks(self, pkg, nv, rep: Report, now: dt.datetime) -> None:
        typo = typosquat.check(pkg.eco, pkg.name)
        install = bool(nv.install_scripts) or any("install_script" in fc.kinds for fc in rep.changes)
        if typo:
            rep.flags.append(Flag("high" if install else "medium", "typosquat",
                                  "name is close to the popular %s (%s)%s" % (
                                      text.code(typo[0]), text.plain(typo[1], 60),
                                      ", and it runs code at install" if install else "")))
        if pkg.created:
            age = (now - pkg.created).total_seconds() / 86400
            if age < NEW_PACKAGE_DAYS:
                rep.flags.append(Flag("high" if install else "medium", "brand-new-package",
                                      "first published %.0f days ago%s" % (age, ", and it runs code at install" if install else "")))
        if nv.install_scripts:
            rep.flags.append(Flag("medium", "install-script",
                                  "runs at install: %s" % ", ".join("%s: %s" % (k, text.plain(v, 80))
                                                                     for k, v in nv.install_scripts.items())))


def verify(art: registry.Artifact, data: bytes) -> "bool | None":
    """True when a published digest matches, False when one does not, None when none is published."""
    if art.sha256:
        return hashlib.sha256(data).hexdigest().lower() == art.sha256.lower()
    if art.sha512_b64:
        try:
            return hashlib.sha512(data).digest() == base64.b64decode(art.sha512_b64)
        except ValueError:
            return False
    if art.sha1:
        return hashlib.sha1(data).hexdigest().lower() == str(art.sha1).lower()
    return None
