"""Markdown for a person or an agent to read. Every external string is already inert (text.py)."""
from __future__ import annotations

from . import artdiff, text

KIND_LABEL = {"install_script": "install script", "build_file": "build file", "network": "network calls",
              "exec": "exec / eval calls", "obfuscated": "obfuscated or minified", "binary": "binary"}


def _verdict(r) -> str:
    if r.error and not r.flags:
        return "COULD NOT REVIEW"
    if r.flags:
        hi = len(r.high)
        return "FLAGGED (%d high, %d medium)" % (hi, len(r.flags) - hi)
    if r.unchecked:
        return "nothing flagged, %d thing(s) not checked" % len(r.unchecked)
    return "nothing flagged"


def report(r, max_files: int = 12) -> str:
    rng = "%s -> %s" % (text.plain(r.old or "(none)", 40), text.plain(r.new or "?", 40))
    out = ["## %s %s %s: %s" % (r.eco, text.code(r.name), rng, _verdict(r))]
    meta = []
    if r.change == "new":
        meta.append("new dependency")
    if r.lockfile:
        meta.append("from %s" % text.code(r.lockfile))
    if r.direct is not None:
        meta.append("direct" if r.direct else "transitive")
    if r.artifact_new:
        meta.append("artifact %s%s" % (text.code(r.artifact_new),
                                       " vs %s" % text.code(r.artifact_old) if r.artifact_old else ""))
    if r.verified:
        meta.append("digests verified (%d)" % len(r.verified))
    if meta:
        out.append("_" + "; ".join(meta) + "_")
    if r.error:
        out.append("")
        out.append("**Could not review:** %s" % r.error)
    if r.counts:
        out.append("")
        out.append("Files: %d added, %d removed, %d modified (of %d in the new artifact)."
                   % (r.counts["added"], r.counts["removed"], r.counts["modified"], r.counts["files_new"]))
    if r.flags:
        out += ["", "### Flags"]
        for f in r.flags[:40]:
            out.append("- **%s** `%s`%s: %s" % (f.severity.upper(), f.rule, " at %s" % f.path if f.path else "", f.what))
        if len(r.flags) > 40:
            out.append("- ... %d more in the JSON" % (len(r.flags) - 40))
    kinds = r.by_kind()
    if kinds:
        out += ["", "### What changed, by kind", "", "| kind | files |", "|---|---|"]
        for k in artdiff.KIND_ORDER:
            if k in kinds:
                paths = kinds[k]
                shown = ", ".join(text.plain(p, 120) for p in paths[:max_files])
                more = " (+%d more)" % (len(paths) - max_files) if len(paths) > max_files else ""
                out.append("| %s | %s%s |" % (KIND_LABEL[k], shown, more))
    c = r.committers
    if c is not None:
        out += ["", "### Who wrote it"]
        if not c.ran:
            out.append("New-committer check %s." % text.plain(c.status, 300))
        else:
            out.append("%s, %s..%s: %d commit(s) by %d author(s) (bots excluded)." % (
                text.plain(c.source, 80), text.code(c.old_ref), text.code(c.new_ref), c.commits, c.authors))
            if not c.new_authors:
                out.append("Every author has committed before %s." % text.code(c.old_ref))
            for na in c.new_authors:
                touched = ", ".join("%s%s" % (text.plain(p, 100), " [%s]" % ", ".join(artdiff.ordered(k)) if k else "")
                                    for p, k in sorted(na.files.items())[:max_files])
                more = " (+%d more)" % (len(na.files) - max_files) if len(na.files) > max_files else ""
                out.append("- %s **new to the project** (%s): %d commit(s); touched %s%s%s" % (
                    text.code(na.author), na.severity, len(na.commits), touched or "nothing listed", more,
                    "; ships in the artifact as %s" % ", ".join(text.plain(p, 100) for p in na.shipped[:max_files])
                    if na.shipped else "; none of it ships in the artifact"))
            if c.not_in_source:
                out.append("- %d changed artifact file(s) are not in the tagged source (often build output): %s%s" % (
                    len(c.not_in_source), ", ".join(text.plain(p, 100) for p, _k in c.not_in_source[:max_files]),
                    " ..." if len(c.not_in_source) > max_files else ""))
            for n in c.notes:
                out.append("- note: %s" % text.plain(n, 200))
    if r.publisher.get("new"):
        out += ["", "Publisher: %s%s." % (text.code(r.publisher["new"]),
                                          " (first release by this account)" if r.publisher.get("first_time") else "")]
    if r.notes:
        out += ["", "### Notes"] + ["- %s" % n for n in r.notes]
    if r.model:
        out += ["", "### Local-model summary"]
        m = r.model
        if m.get("summary"):
            out.append("_%s_: %s" % (m.get("label", ""), text.plain(m["summary"], 600)))
            if m.get("signs"):
                out.append("Signs it named (not flags): %s" % ", ".join(m["signs"]))
        else:
            out.append(text.plain(m.get("status", ""), 200))
    if r.unchecked:
        out += ["", "### Not checked"] + ["- %s" % u for u in r.unchecked]
    return "\n".join(out) + "\n"


def patch(res) -> str:
    out = ["# Dependency patch review: %s vs %s" % (text.plain(res.head, 60), text.plain(res.base, 60)), ""]
    if not res.files and not res.errors:
        out.append("No lockfile or manifest changed; nothing to review.")
        return "\n".join(out) + "\n"
    out.append("Changed: %s." % ", ".join(text.code(f) for f in res.files[:20]))
    flagged = [r for r in res.reports if r.flags]
    couldnt = [r for r in res.reports if r.error and not r.flags]
    out.append("Reviewed %d package(s): %d flagged, %d could not be reviewed, %d nothing flagged."
               % (len(res.reports), len(flagged), len(couldnt), len(res.reports) - len(flagged) - len(couldnt)))
    for e in res.errors:
        out.append("- error: %s" % e)
    for s in res.skipped:
        out.append("- skipped: %s" % s)
    order = sorted(res.reports, key=lambda r: (-len(r.high), -len(r.flags), not r.error, r.name))
    for r in order:
        out.append("")
        out.append(report(r))
    return "\n".join(out) + "\n"
