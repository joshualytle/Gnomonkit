"""Fixtures for the sync gate's guard (sync_guard.py). Self-contained: run it directly, exits 1 on failure.

Both directions matter: a missed merge of a gnomonkit-sync/* pull request is an agent approving a plugin
update, and a false block on an ordinary merge or a harmless read trains people to switch the guard off.
Synthetic data only: a temporary GNOMONKIT_HOME, made-up repositories, and a fake `gh pr view`.
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "sync_guard.py")
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="sync-guard-test-")
atexit.register(shutil.rmtree, TMP, True)
HOME = os.path.join(TMP, "gk")
os.environ["GNOMONKIT_HOME"] = HOME
os.makedirs(os.path.join(HOME, "sync"))
with open(os.path.join(HOME, "sync", "config.toml"), "w", encoding="utf-8") as fh:
    fh.write('[overlay]\nremote = "https://github.com/example-org/overlay.git"\n')
PROJECT = os.path.join(TMP, "project")
os.makedirs(PROJECT)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

import sync_guard as g  # noqa: E402

SYNC = os.path.join(HOME, "sync")
HELD_ID = "examplemkt-0123456789ab"
LOOKUPS = {
    "12": {"head": "gnomonkit-sync/" + HELD_ID, "files": ["sync/approvals/examplemkt.toml"], "changed": 1},
    "13": {"head": "feature/better-docs", "files": ["README.md", "docs/a.md"], "changed": 2},
    "14": None,                                                           # gh could not say
    "15": {"head": "approve-update", "files": ["README.md", "Sync/approvals/examplemkt.toml"], "changed": 2},
    "16": {"head": "big-change", "files": ["README.md"], "changed": 140},  # gh listed only some of them
    "17": {"head": "docs", "files": None, "changed": 1},
}
HOMEF = HOME.replace(os.sep, "/")
UNSET = "GK_TEST_" + "UNSET_REPO"
os.environ.pop(UNSET, None)
for _k in ("GH_REPO", "GH_HOST"):
    os.environ.pop(_k, None)
asked = []


def lookup(slug, sel):
    asked.append((slug, sel))
    return LOOKUPS.get(sel)


def payload(tool, cwd=PROJECT, **ti):
    return {"tool_name": tool, "tool_input": ti, "cwd": cwd}


DENY, ALLOW = "deny", "allow"
CASES = [
    # --- the gate's own files ------------------------------------------------------------------------
    ("write into the approved checkout", DENY,
     payload("Write", file_path=os.path.join(SYNC, "approved", "examplemkt", "plugins", "x", "hooks", "a.py"))),
    ("edit the overlay setting", DENY, payload("Edit", file_path=os.path.join(SYNC, "config.toml"))),
    ("multi-edit a held record", DENY, payload("MultiEdit", file_path=os.path.join(SYNC, "held", HELD_ID + ".json"))),
    ("notebook under the sync folder", DENY, payload("NotebookEdit", notebook_path=os.path.join(SYNC, "n.ipynb"))),
    ("relative path that lands in it", DENY, payload("Write", cwd=SYNC, file_path="approved/examplemkt/x.py")),
    ("redirect into it", DENY, payload("Bash", command="echo x > ~/.gnomonkit/sync/approved/examplemkt/a.py")),
    ("rm in it", DENY, payload("Bash", command="rm -rf ~/.gnomonkit/sync/held")),
    ("cd then write", DENY, payload("Bash", command="cd ~/.gnomonkit/sync && git -C approved/examplemkt checkout HEAD~1")),
    ("git -c on its checkout", DENY,
     payload("Bash", command="git -C ~/.gnomonkit/sync/approved/examplemkt -c core.hooksPath=x log")),
    ("powershell Set-Content", DENY,
     payload("PowerShell", command="Set-Content $env:USERPROFILE\\.gnomonkit\\sync\\config.toml 'x'")),
    ("python writing to the resolved path", DENY,
     payload("Bash", command="python -c \"open(r'%s','w')\"" % os.path.join(SYNC, "sources.json"))),
    ("cp into it", DENY, payload("Bash", command="cp evil.py ~/.gnomonkit/sync/approved/examplemkt/plugins/x.py")),
    ("git log --output into it", DENY,
     payload("Bash", command="git --git-dir ~/.gnomonkit/sync/quarantine/examplemkt.git log --output=x")),
    # --- merging an approval -------------------------------------------------------------------------
    ("gh pr merge naming a sync branch", DENY,
     payload("Bash", command="gh pr merge gnomonkit-sync/%s --repo example-org/overlay --squash" % HELD_ID)),
    ("gh pr merge by number, a sync branch", DENY, payload("Bash", command="gh pr merge 12 --repo example-org/overlay")),
    ("gh pr merge, lookup fails", DENY, payload("Bash", command="gh pr merge 14 -R example-org/overlay")),
    ("gh pr merge by URL", DENY,
     payload("Bash", command="gh pr merge https://github.com/example-org/overlay/pull/12 --merge")),
    ("gh pr merge --auto", DENY, payload("PowerShell", command="gh pr merge 12 --auto --repo example-org/Overlay")),
    ("gh api pulls merge", DENY, payload("Bash", command="gh api -X PUT repos/example-org/overlay/pulls/12/merge")),
    ("gh api merges", DENY,
     payload("Bash", command="gh api repos/example-org/overlay/merges -f base=main -f head=topic")),
    ("gh api graphql merge", DENY,
     payload("Bash", command="gh api graphql -f query='mutation { mergePullRequest(input:{pullRequestId:\"x\"}) "
                             "{ clientMutationId } }'")),
    ("git merge a sync branch", DENY, payload("Bash", command="git merge origin/gnomonkit-sync/" + HELD_ID)),
    ("git pull a sync branch", DENY, payload("Bash", command="git -C x pull origin gnomonkit-sync/" + HELD_ID)),
    ("push a sync branch onto main", DENY,
     payload("Bash", command="git push origin gnomonkit-sync/%s:main" % HELD_ID)),
    # --- the repository named the ways gh reads it ---------------------------------------------------
    ("GH_REPO before gh", DENY, payload("Bash", command="GH_REPO=example-org/overlay gh pr merge 12 --squash")),
    ("attached -R", DENY, payload("Bash", command="gh pr merge 12 -Rexample-org/overlay")),
    ("--repo with a host", DENY, payload("Bash", command="gh pr merge 12 --repo github.com/example-org/overlay")),
    ("--repo= a URL", DENY, payload("Bash", command="gh pr merge 12 --repo=https://github.com/example-org/overlay.git")),
    ("--repo before merge", DENY, payload("Bash", command="gh pr --repo example-org/overlay merge 12")),
    ("export GH_REPO first", DENY, payload("Bash", command="export GH_REPO=example-org/overlay && gh pr merge 12")),
    ("powershell $env:GH_REPO", DENY,
     payload("PowerShell", command='$env:GH_REPO = "example-org/overlay"; gh pr merge 12')),
    ("env GH_REPO", DENY, payload("Bash", command="env GH_REPO=example-org/overlay gh pr merge 12")),
    ("a variable it can read", DENY, payload("Bash", command="R=example-org/overlay; gh pr merge 12 --repo $R")),
    ("a variable it can't read", DENY, payload("Bash", command="gh pr merge 13 --repo $" + UNSET)),
    ("api placeholders with GH_REPO", DENY,
     payload("Bash", command="GH_REPO=example-org/overlay gh api -X PUT repos/{owner}/{repo}/pulls/12/merge")),
    # --- what the pull request changes, not only its branch ------------------------------------------
    ("a PR that changes sync/ from another branch", DENY, payload("Bash", command="gh pr merge 15 -R example-org/overlay")),
    ("a PR with more files than gh listed", DENY, payload("Bash", command="gh pr merge 16 -R example-org/overlay")),
    ("a PR whose files gh did not give", DENY, payload("Bash", command="gh pr merge 17 -R example-org/overlay")),
    ("api merge of a PR that changes sync/", DENY,
     payload("Bash", command="gh api -X PUT repos/example-org/overlay/pulls/15/merge")),
    # --- paths written the ways a shell reads them ---------------------------------------------------
    ("a dot in the path", DENY, payload("Bash", command="cp evil.toml ~/.gnomonkit/./sync/config.toml")),
    ("a quoted part", DENY, payload("Bash", command='cp evil.toml ~/.gnomonkit/"sync"/config.toml')),
    ("cd into the parent first", DENY, payload("Bash", command="cd ~/.gnomonkit && cp evil.toml sync/config.toml")),
    ("dot-dot through a sibling", DENY, payload("Bash", command="cp evil.toml %s/x/../sync/config.toml" % HOMEF)),
    ("cd to the configured home", DENY, payload("Bash", command="cd %s && cp evil.toml ./sync/config.toml" % HOMEF)),
    ("pushd twice then touch", DENY, payload("Bash", command="pushd %s; pushd sync; touch config.toml" % HOMEF)),
    ("the call's folder is the home", DENY, payload("Bash", cwd=HOME, command="cp evil.toml sync/config.toml")),
    ("the call's folder is the sync folder", DENY, payload("Bash", cwd=SYNC, command="rm config.toml")),
    ("a wildcard", DENY, payload("Bash", command="cp evil.toml %s/s*/config.toml" % HOMEF)),
    ("braced GNOMONKIT_HOME", DENY, payload("Bash", command="cp evil.toml ${GNOMONKIT_HOME}/./sync/config.toml")),
    ("dd of=", DENY, payload("Bash", command="dd if=evil.toml of=%s/./sync/config.toml" % HOMEF)),
    ("powershell Copy-Item -Destination", DENY,
     payload("PowerShell", command="Copy-Item evil.toml -Destination %s" % os.path.join(HOME, ".", "sync", "config.toml"))),
    # --- everything else is allowed ------------------------------------------------------------------
    ("write elsewhere", ALLOW, payload("Write", file_path=os.path.join(PROJECT, "a.py"))),
    ("write elsewhere in gnomonkit home", ALLOW, payload("Write", file_path=os.path.join(HOME, "personal.toml"))),
    ("a folder that only starts like it", ALLOW, payload("Write", file_path=os.path.join(HOME, "sync-notes", "a.md"))),
    ("list it", ALLOW, payload("Bash", command="ls -la ~/.gnomonkit/sync/held 2>&1")),
    ("read a held record", ALLOW, payload("Bash", command="cat ~/.gnomonkit/sync/held/%s.json | jq ." % HELD_ID)),
    ("read the quarantine", ALLOW,
     payload("Bash", command="git --git-dir ~/.gnomonkit/sync/quarantine/examplemkt.git log -p a..b")),
    ("powershell read", ALLOW, payload("PowerShell", command="Get-Content $HOME\\.gnomonkit\\sync\\sources.json")),
    ("the sync command", ALLOW,
     payload("Bash", command='python "/plugins/core/gnomonkit.py" sync approve-draft %s --json' % HELD_ID)),
    ("the sync command naming the folder", ALLOW,
     payload("Bash", command="gnomonkit sync status; ls ~/.gnomonkit/sync")),
    ("merge on another repository", ALLOW, payload("Bash", command="gh pr merge 12 --repo example-org/other")),
    ("merge an ordinary overlay pull request", ALLOW, payload("Bash", command="gh pr merge 13 --repo example-org/overlay")),
    ("push the sync branch itself", ALLOW,
     payload("Bash", command="git push origin gnomonkit-sync/%s:gnomonkit-sync/%s" % (HELD_ID, HELD_ID))),
    ("an ordinary merge", ALLOW, payload("Bash", command="git merge feature/x")),
    ("api merge on another repository", ALLOW, payload("Bash", command="gh api -X PUT repos/example-org/other/pulls/3/merge")),
    ("reading is not writing", ALLOW, payload("Read", file_path=os.path.join(SYNC, "config.toml"))),
    ("an ordinary overlay PR through GH_REPO", ALLOW, payload("Bash", command="GH_REPO=example-org/overlay gh pr merge 13")),
    ("GH_REPO names another repository", ALLOW, payload("Bash", command="GH_REPO=example-org/other gh pr merge 12")),
    ("api merge of an ordinary overlay PR", ALLOW,
     payload("Bash", command="gh api -X PUT repos/example-org/overlay/pulls/13/merge")),
    ("reading from the home as the call's folder", ALLOW,
     payload("Bash", cwd=HOME, command="ls sync && cat sync/config.toml")),
    ("cd then read", ALLOW, payload("Bash", command="cd %s && cat ./sync/config.toml" % HOMEF)),
    ("a sibling folder after cd", ALLOW, payload("Bash", command="cd %s && cp a.md sync-notes/a.md" % HOMEF)),
    ("a project's own sync folder", ALLOW, payload("Bash", command="cp sync/a.toml sync/b.toml")),
    ("cd .. in a project", ALLOW, payload("Bash", command="cd .. && cp a.txt b.txt")),
    ("a wildcard in a project", ALLOW, payload("Bash", command="rm -f *.pyc build/*.o")),
    ("a plain command", ALLOW, payload("Bash", command="python -m pytest -q")),
]


def run_hook(stdin_text):
    r = subprocess.run([sys.executable, HOOK], input=stdin_text, capture_output=True, text=True,
                       env=dict(os.environ), creationflags=NO_WINDOW)
    return r.returncode, r.stderr


def git(*args, cwd):
    subprocess.run(["git"] + list(args), cwd=cwd, check=True, capture_output=True, creationflags=NO_WINDOW,
                   env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull))


def main() -> int:
    failures = []
    for name, want, p in CASES:
        got = DENY if g.decide(p, pr_lookup=lookup) else ALLOW
        if got != want:
            failures.append("%s: wanted %s, got %s" % (name, want, got))
    if ("example-org/overlay", "13") not in asked:
        failures.append("an overlay pull request was merged without looking up its branch")

    # the current branch is a sync branch: `gh pr merge` with no selector merges it
    repo = os.path.join(TMP, "overlay-clone")
    os.makedirs(repo)
    git("init", "-q", cwd=repo)
    git("checkout", "-q", "-b", "gnomonkit-sync/" + HELD_ID, cwd=repo)
    if not g.decide(payload("Bash", cwd=repo, command="gh pr merge --squash"), pr_lookup=lookup):
        failures.append("gh pr merge on a checked-out sync branch was allowed")
    # the overlay is the folder's origin: a bare number is looked up there
    git("remote", "add", "origin", "https://github.com/example-org/overlay.git", cwd=repo)
    git("checkout", "-q", "-b", "topic", cwd=repo)
    if not g.decide(payload("Bash", cwd=repo, command="gh pr merge 12"), pr_lookup=lookup):
        failures.append("gh pr merge 12 inside the overlay clone was allowed")
    if g.decide(payload("Bash", cwd=repo, command="gh pr merge 13"), pr_lookup=lookup):
        failures.append("an ordinary pull request inside the overlay clone was blocked")
    # the overlay is a second remote: gh may pick it, so it counts
    fork = os.path.join(TMP, "fork-clone")
    os.makedirs(fork)
    git("init", "-q", cwd=fork)
    git("remote", "add", "origin", "https://github.com/example-org/fork.git", cwd=fork)
    git("remote", "add", "upstream", "@".join(["git", "github.com:example-org/overlay.git"]), cwd=fork)  # ssh form
    if not g.decide(payload("Bash", cwd=fork, command="gh pr merge 12"), pr_lookup=lookup):
        failures.append("gh pr merge 12 in a clone whose second remote is the overlay was allowed")
    # GH_REPO from the environment the hook runs in
    os.environ["GH_REPO"] = "example-org/overlay"
    try:
        if not g.decide(payload("Bash", command="gh pr merge 12"), pr_lookup=lookup):
            failures.append("gh pr merge 12 with GH_REPO set in the environment was allowed")
        if g.decide(payload("Bash", command="gh pr merge 13"), pr_lookup=lookup):
            failures.append("an ordinary pull request with GH_REPO set in the environment was blocked")
    finally:
        os.environ.pop("GH_REPO", None)

    # the hook itself: exit 2 with the reason; unreadable input fails closed only when it names the targets
    code, err = run_hook(json.dumps(CASES[0][2]))
    if code != 2 or "BLOCKED" not in err:
        failures.append("the hook did not exit 2 on a denied write (%s, %r)" % (code, err[:200]))
    code, _ = run_hook(json.dumps(payload("Bash", command="ls")))
    if code != 0:
        failures.append("the hook did not allow a plain command (%s)" % code)
    code, _ = run_hook("{not json ~/.gnomonkit/sync/approved")
    if code != 2:
        failures.append("unreadable input naming the sync folder was allowed")
    code, _ = run_hook("{not json at all")
    if code != 0:
        failures.append("unreadable input naming nothing was blocked")

    for f in failures:
        print("FAIL", f)
    print("%d cases, %d failures" % (len(CASES) + 10, len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
