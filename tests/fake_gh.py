"""A synthetic `gh` for the work-item tests: a tiny GitHub kept in one JSON file.

install(folder) writes a `gh` shim (gh.cmd on Windows, a script elsewhere) that runs this file, and
returns a FakeGitHub to seed repositories and issues, set a failure, and read back every call. The
shim goes first on PATH, so nothing reaches the real GitHub: a command this fake doesn't know exits 3
and says so. All names in the seeded data are made up by the tests.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class FakeGitHub:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.bindir = self.folder / "bin"
        self.state = self.folder / "github.json"
        self.log = self.folder / "gh-calls.jsonl"

    # ---- seeding ------------------------------------------------------------------------------
    def load(self) -> dict:
        if self.state.exists():
            return json.loads(self.state.read_text(encoding="utf-8"))
        return {"repos": {}, "cwd": {}, "fail": None, "next_comment": 1}

    def save(self, data: dict) -> None:
        self.state.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def repo(self, name: str, visibility: str = "PRIVATE", labels=(), cwd=None) -> None:
        d = self.load()
        d["repos"][name] = {"visibility": visibility, "labels": [{"name": x} for x in labels], "issues": {}}
        if cwd:
            d["cwd"][os.path.normcase(os.path.abspath(str(cwd)))] = name
        self.save(d)

    def issue(self, repo: str, number: int, title: str, body: str = "", labels=(), state: str = "OPEN",
              comments=(), updated: str | None = None) -> None:
        d = self.load()
        r = d["repos"][repo]
        for lab in labels:
            if not any(x["name"] == lab for x in r["labels"]):
                r["labels"].append({"name": lab})
        cs = []
        for c in comments:
            cs.append(_comment(d, repo, number, c["body"], c.get("createdAt") or _now(), c.get("login")))
        r["issues"][str(number)] = {
            "number": number, "title": title, "body": body, "state": state, "labels": [{"name": x} for x in labels],
            "url": "https://github.com/%s/issues/%d" % (repo, number), "createdAt": updated or _now(),
            "updatedAt": updated or (cs[-1]["createdAt"] if cs else _now()), "closedAt": None, "comments": cs}
        self.save(d)

    def fail(self, kind) -> None:
        d = self.load()
        d["fail"] = kind
        self.save(d)

    # ---- reading back -------------------------------------------------------------------------
    def get(self, repo: str, number: int) -> dict:
        return self.load()["repos"][repo]["issues"][str(number)]

    def labels_of(self, repo: str, number: int) -> set:
        return {x["name"] for x in self.get(repo, number)["labels"]}

    def calls(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]

    def writes(self) -> list:
        return [c["args"] for c in self.calls()
                if c["args"][:2] in (["issue", "create"], ["issue", "edit"], ["issue", "comment"], ["issue", "close"],
                                     ["label", "create"])]

    def env(self) -> dict:
        return {"FAKE_GH_DIR": str(self.folder)}


def install(folder) -> FakeGitHub:
    fake = FakeGitHub(Path(folder))
    fake.bindir.mkdir(parents=True, exist_ok=True)
    me = os.path.abspath(__file__)
    if os.name == "nt":
        (fake.bindir / "gh.cmd").write_text('@"%s" "%s" %%*\r\n' % (sys.executable, me), encoding="utf-8")
    else:
        exe = fake.bindir / "gh"
        exe.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, me), encoding="utf-8")
        exe.chmod(0o755)
    fake.save(fake.load())
    return fake


# ---- the executable ---------------------------------------------------------------------------------
_VALUED = {"--repo", "--json", "--label", "--state", "--limit", "--search", "--title", "--body-file",
           "--add-label", "--remove-label", "--color", "--description", "--comment", "--body"}


def _parse(argv):
    pos, opts, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in _VALUED:
            opts.setdefault(a, []).append(argv[i + 1])
            i += 2
        else:
            pos.append(a)
            i += 1
    return pos, opts


def _comment(d, repo, number, body, created, login=None):
    cid = d.get("next_comment", 1)
    d["next_comment"] = cid + 1
    return {"id": "IC_fake%d" % cid, "author": {"login": login or "synthetic-user"}, "body": body,
            "createdAt": created, "url": "https://github.com/%s/issues/%d#issuecomment-%d" % (repo, number, cid)}


def _die(msg, code=1):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def _pick(issue, fields):
    return {f: issue.get(f) for f in fields.split(",")}


def _split(values):
    out = []
    for v in values or []:
        out += [x for x in v.split(",") if x]
    return out


def main(argv) -> int:
    folder = Path(os.environ["FAKE_GH_DIR"])
    fake = FakeGitHub(folder)
    with open(fake.log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"args": argv, "cwd": os.getcwd(),
                             "prompt_disabled": os.environ.get("GH_PROMPT_DISABLED")}) + "\n")
    d = fake.load()
    if d.get("fail") == "auth":
        _die("To get started with GitHub CLI, please run:  gh auth login", 4)
    if d.get("fail") == "offline":
        _die("error connecting to api.github.com", 1)
    pos, o = _parse(argv)
    one = lambda k: (o.get(k) or [None])[-1]  # noqa: E731
    repo = one("--repo")
    if pos[:2] == ["repo", "view"]:
        name = pos[2] if len(pos) > 2 else d["cwd"].get(os.path.normcase(os.path.abspath(os.getcwd())))
        if name is None:
            _die("no git remotes found")
        r = d["repos"].get(name)
        if r is None:
            _die("GraphQL: Could not resolve to a Repository with the name '%s'. (repository)" % name)
        print(json.dumps({"nameWithOwner": name, "isPrivate": r["visibility"] != "PUBLIC",
                          "visibility": r["visibility"]}))
        return 0
    r = d["repos"].get(repo or "")
    if r is None:
        _die("GraphQL: Could not resolve to a Repository with the name '%s'. (repository)" % repo)
    if pos[:2] == ["label", "list"]:
        print(json.dumps(r["labels"]))
        return 0
    if pos[:2] == ["label", "create"]:
        if any(x["name"] == pos[2] for x in r["labels"]):
            _die("label with name \"%s\" already exists; use `--force` to update its color and description" % pos[2])
        r["labels"].append({"name": pos[2]})
        fake.save(d)
        return 0
    if pos[:2] == ["issue", "list"]:
        want_state = (one("--state") or "open").upper()
        label = one("--label")
        since, order = None, lambda i: -i["number"]  # noqa: E731
        for term in (one("--search") or "").split():
            if term == "sort:updated-asc":
                order = lambda i: (i["updatedAt"], i["number"])  # noqa: E731
                continue
            assert term.startswith("updated:") and term.endswith("..*"), term
            since = term[len("updated:"):-3]
        out = []
        for issue in sorted(r["issues"].values(), key=order):
            if want_state != "ALL" and issue["state"] != want_state:
                continue
            if label and label not in {x["name"] for x in issue["labels"]}:
                continue
            if since and issue["updatedAt"] < since:
                continue
            out.append(_pick(issue, one("--json")))
        print(json.dumps(out[: int(one("--limit") or 30)]))
        return 0
    if pos[:2] == ["issue", "create"]:
        names = _split(o.get("--label"))
        for n in names:
            if not any(x["name"] == n for x in r["labels"]):
                _die("could not add label: '%s' not found" % n)
        number = max([int(k) for k in r["issues"]] + [0]) + 1
        body = Path(one("--body-file")).read_text(encoding="utf-8")
        r["issues"][str(number)] = {
            "number": number, "title": one("--title"), "body": body, "state": "OPEN",
            "labels": [{"name": x} for x in names], "url": "https://github.com/%s/issues/%d" % (repo, number),
            "createdAt": _now(), "updatedAt": _now(), "closedAt": None, "comments": []}
        fake.save(d)
        print("https://github.com/%s/issues/%d" % (repo, number))
        return 0
    issue = r["issues"].get(pos[2] if len(pos) > 2 else "")
    if issue is None:
        _die("GraphQL: Could not resolve to an issue or pull request with the number of %s. (repository.issue)"
             % (pos[2] if len(pos) > 2 else "?"))
    if pos[:2] == ["issue", "view"]:
        print(json.dumps(_pick(issue, one("--json"))))
        return 0
    if pos[:2] == ["issue", "edit"]:
        have = [x["name"] for x in issue["labels"]]
        for n in _split(o.get("--remove-label")):
            if n not in have:
                _die("fake gh: removing a label the issue does not have: %s" % n, 5)
            have.remove(n)
        for n in _split(o.get("--add-label")):
            if not any(x["name"] == n for x in r["labels"]):
                _die("'%s' not found" % n)
            if n not in have:
                have.append(n)
        issue["labels"] = [{"name": x} for x in have]
        if one("--body-file"):
            issue["body"] = Path(one("--body-file")).read_text(encoding="utf-8")
        issue["updatedAt"] = _now()
        fake.save(d)
        print(issue["url"])
        return 0
    if pos[:2] == ["issue", "comment"]:
        c = _comment(d, repo, issue["number"], Path(one("--body-file")).read_text(encoding="utf-8"), _now())
        issue["comments"].append(c)
        issue["updatedAt"] = _now()
        fake.save(d)
        print(c["url"])
        return 0
    if pos[:2] == ["issue", "close"]:
        issue["state"], issue["closedAt"], issue["updatedAt"] = "CLOSED", _now(), _now()
        fake.save(d)
        return 0
    _die("fake gh: unsupported command %s" % " ".join(pos[:2]), 3)
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
