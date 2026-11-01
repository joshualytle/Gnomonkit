#!/usr/bin/env python3
"""Fixtures for usage_allocation and gk_usage: synthetic transcripts only, under a temporary home.

Every project name, folder and number here is made up. Covers the count (one message over several lines
once, subagents with their parent's folder, last week left out, an unpriced model, a nested project, a
half-written line), the cache (appended bytes only, a shrunk file, a new week, a scan stopped by its time
limit), and the hook (nothing without a share, quiet below 80%, a warning once per session, a block at
100% with the lift command, lifts for this week and last, and every failure letting the prompt through)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "usage_allocation.py")
sys.path.insert(0, HERE)

FAILS = []
TMP = tempfile.mkdtemp(prefix="gk-usage-")
NOW = time.time()
WEEK_AGO = NOW - 8 * 86400


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def turn(mid, cwd, inp=0, out=0, cr=0, cw=0, model="claude-opus-5-5", t=None):
    return json.dumps({"type": "assistant", "cwd": cwd, "timestamp": iso(NOW if t is None else t),
                       "message": {"id": mid, "role": "assistant", "model": model,
                                   "content": [{"type": "text", "text": "ok"}],
                                   "usage": {"input_tokens": inp, "output_tokens": out,
                                             "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}})


class Home:
    """A fresh home: a Claude config folder with a projects folder, a Gnomonkit folder, two project folders."""
    n = 0

    def __init__(self):
        Home.n += 1
        self.root = os.path.join(TMP, "h%d" % Home.n)
        self.claude = os.path.join(self.root, "claude-config")
        self.projects = os.path.join(self.claude, "projects")
        self.gk = os.path.join(self.root, "gnomonkit")
        self.alpha = os.path.join(self.root, "work", "alpha")
        self.beta = os.path.join(self.root, "work", "beta")
        self.other = os.path.join(self.root, "work", "gamma")
        for d in (self.projects, self.gk, self.alpha, self.beta, self.other):
            os.makedirs(d)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_", "CLAUDE_CONFIG_DIR"))}
        self.env.update(GNOMONKIT_HOME=self.gk, CLAUDE_CONFIG_DIR=self.claude, HOME=self.root, USERPROFILE=self.root)

    def transcript(self, name, lines, slug="proj", sub=None, newline=True):
        d = os.path.join(self.projects, slug)
        if sub:
            d = os.path.join(d, name, "subagents")
            name = sub
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name + ".jsonl")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if newline else ""))
        return p

    def alloc(self, text):
        with open(os.path.join(self.gk, "usage-allocation.toml"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def shares(self, weekly, unit="tokens", alpha=50, beta=None, lifts=()):
        t = 'version = 1\n[budget]\nweekly = %s\nunit = "%s"\n' % (weekly, unit)
        t += '[[project]]\nfolder = %s\nshare = %s\n' % (json.dumps(self.alpha), alpha)
        if beta is not None:
            t += '[[project]]\nfolder = %s\nshare = %s\n' % (json.dumps(self.beta), beta)
        for folder, week in lifts:
            t += '[[lift]]\nfolder = %s\nweek = "%s"\n' % (json.dumps(folder), week)
        self.alloc(t)

    def cache(self):
        return os.path.join(self.gk, "usage-allocation.cache.json")

    def hook(self, cwd, session="s-1"):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": cwd, "prompt": "next"}
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                           env=self.env, timeout=60)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def use(self):
        """In-process: point gk_usage at this home and return the module."""
        for k in ("GNOMONKIT_HOME", "CLAUDE_CONFIG_DIR"):
            os.environ[k] = self.env[k]
        import gk_usage
        return gk_usage


try:
    # --- counting --------------------------------------------------------------------------------------------
    h = Home()
    U = h.use()
    h.transcript("s1", [turn("m1", h.alpha, inp=100, out=5), turn("m1", h.alpha, inp=100, out=40),
                        turn("m1", h.alpha, inp=100, out=40)])
    h.transcript("s1", [turn("m2", h.alpha, inp=1000)], sub="agent-x1")
    h.transcript("s2", [turn("m3", os.path.join(h.alpha, ".claude", "worktrees", "w1"), inp=7)])
    h.transcript("s3", [turn("old", h.alpha, inp=99999, t=WEEK_AGO)])
    h.transcript("s4", [turn("m4", h.beta, inp=10, model="some-unlisted-model")])
    h.transcript("s5", [turn("m5", h.other, inp=123456)])
    h.shares(1000, alpha=50, beta=20)
    alloc = U.load()
    check("a valid allocation loads", alloc.ok)
    state = U.new_state(U.week_id(NOW))
    check("a full scan finishes", U.scan(state, now=NOW))
    tot = U.totals(state, alloc)
    check("one message over three lines counts once, at its largest; subagents and worktrees with the project",
          tot[h.alpha] == 140 + 1000 + 7)
    check("last week's turn doesn't count", "old" not in state["turns"])
    check("an unpriced model adds tokens", tot[h.beta] == 10)
    check("an unpriced model adds no dollars", state["turns"]["m4"][8] is None and U.amount(state["turns"]["m4"],
                                                                                           "usd") == 0)
    check("a priced model has a dollar figure (100 in at 4/M + 40 out at 20/M)",
          abs(state["turns"]["m1"][8] - (100 * 4 + 40 * 20) / 1e6) < 1e-9)
    check("a folder with no share is left out", h.other not in tot and len(tot) == 2)

    nested = os.path.join(h.alpha, "tools")
    os.makedirs(nested)
    h.transcript("s6", [turn("m6", nested, inp=500)])
    h.alloc('version = 1\n[budget]\nweekly = 1000\nunit = "tokens"\n[[project]]\nfolder = %s\nshare = 50\n'
            '[[project]]\nfolder = %s\nshare = 10\n' % (json.dumps(h.alpha), json.dumps(nested)))
    alloc = U.load()
    U.scan(state, now=NOW)
    tot = U.totals(state, alloc)
    check("a nested project is counted apart from its parent", tot[nested] == 500 and tot[h.alpha] == 1147)

    # --- the cache -------------------------------------------------------------------------------------------
    p = h.transcript("s7", [turn("m7", h.beta, inp=1)])
    h.transcript("s7", ['{"type":"assistant","cwd":"half'], newline=False)
    U.scan(state, now=NOW)
    off = state["files"][p]["offset"]
    check("a half-written last line is left for the next scan", off == os.path.getsize(p) - len(
        '{"type":"assistant","cwd":"half'))
    with open(p, "w", encoding="utf-8") as fh:              # the writer finishes the line (same file, longer)
        fh.write(turn("m7", h.beta, inp=1) + "\n" + turn("m8", h.beta, inp=2) + "\n")
    U.scan(state, now=NOW)
    check("the appended bytes are read from the saved offset", "m8" in state["turns"])
    with open(p, "w", encoding="utf-8") as fh:              # replaced by a shorter file
        fh.write(turn("m9", h.beta, inp=3) + "\n")
    U.scan(state, now=NOW)
    check("a shrunk file is read again, without counting old messages twice",
          "m9" in state["turns"] and state["files"][p]["offset"] == os.path.getsize(p)
          and U.amount(state["turns"]["m7"], "tokens") == 1)
    U.save_state(state)
    check("the cache is kept for its week", U.load_state(U.week_id(NOW))["turns"].keys() == state["turns"].keys())
    check("a new week starts the cache over", U.load_state("1999-W01")["turns"] == {})
    s2 = U.new_state(U.week_id(NOW))
    check("a scan stopped by its time limit says so", U.scan(s2, now=NOW, limit=-1) is False and not s2["complete"])
    rounds = 1
    while not U.scan(s2, now=NOW, limit=-1):
        rounds += 1
    full = U.new_state(U.week_id(NOW))
    U.scan(full, now=NOW)
    check("and carries on where it stopped", rounds > 2 and s2["turns"] == full["turns"])

    # --- the allocation file -------------------------------------------------------------------------------
    h.alloc('version = 1\n[budget]\nweekly = 10\n[[project]]\nfolder = "/a"\nshare = 70\n'
            '[[project]]\nfolder = "/b"\nshare = 40\n')
    check("shares above 100 in total are a problem", U.load().status == "invalid")
    h.alloc('version = 1\n[[project]]\nfolder = "relative/x"\nshare = 10\n')
    check("a relative folder is a problem", U.load().status == "invalid")
    h.alloc("version = 1\n[budget\n")
    check("a broken file is malformed", U.load().status == "malformed")

    # --- the hook ------------------------------------------------------------------------------------------
    h = Home()
    h.transcript("s1", [turn("m1", h.alpha, inp=50)])
    check("no allocation file: exit 0, quiet", h.hook(h.alpha) == (0, "", ""))
    check("and no transcript is read", not os.path.exists(h.cache()))
    h.shares(1000, alpha=50)
    check("a folder with no share: exit 0, quiet", h.hook(h.other) == (0, "", ""))
    check("and no transcript is read", not os.path.exists(h.cache()))
    check("below 80% of the share: quiet", h.hook(h.alpha) == (0, "", ""))     # 50 of 500

    h = Home()
    h.transcript("s1", [turn("m1", h.alpha, inp=420)])                       # 84% of 500
    h.shares(1000, alpha=50)
    code, out, err = h.hook(h.alpha, session="s-1")
    msg = json.loads(out) if out else {}
    check("at 80%: a warning, not a block", code == 0 and "84%" in msg.get("systemMessage", ""))
    check("the warning reaches the agent too",
          "84%" in msg.get("hookSpecificOutput", {}).get("additionalContext", ""))
    check("once per session", h.hook(h.alpha, session="s-1") == (0, "", ""))
    check("another session is warned too", h.hook(h.alpha, session="s-2")[1] != "")

    h = Home()
    h.transcript("s1", [turn("m1", h.alpha, inp=300), turn("m2", h.alpha, inp=250)])   # 550 of 500
    h.shares(1000, alpha=50, beta=10)
    code, out, err = h.hook(h.alpha)
    check("at 100%: the prompt is blocked", code == 2 and out == "")
    check("the block gives the use, the share and the budget",
          "550 tokens of 500 tokens" in err and "50% of the 1,000 tokens budget" in err)
    check("and how to lift it", 'gnomonkit.py" usage lift' in err and h.alpha in err)
    check("another project is not stopped", h.hook(h.beta) == (0, "", ""))
    h.shares(1000, alpha=50, lifts=[(h.alpha, U.week_id(NOW))])
    check("a lift for this week lets it through", h.hook(h.alpha) == (0, "", ""))
    h.shares(1000, alpha=50, lifts=[(h.alpha, U.week_id(WEEK_AGO))])
    check("a lift for last week doesn't", h.hook(h.alpha)[0] == 2)
    h.shares(0, alpha=50)
    check("a budget of 0 limits nothing", h.hook(h.alpha) == (0, "", ""))

    h = Home()
    h.transcript("s1", [turn("m1", h.alpha, inp=1_000_000, out=100_000)])   # 4 + 2 = 6 USD
    h.shares(10, unit="usd", alpha=50)
    code, _out, err = h.hook(h.alpha)
    check("a dollar budget blocks on the API-equivalent cost", code == 2 and "6.00 USD of 5.00 USD" in err)

    # --- failures never block -------------------------------------------------------------------------------
    h.alloc("version = 1\n[budget\nweekly = 1\n")
    check("a malformed allocation file lets the prompt through", h.hook(h.alpha) == (0, "", ""))
    h.shares(10, unit="usd", alpha=50)
    with open(h.cache(), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    check("a corrupt cache is rebuilt (and still blocks a real overrun)", h.hook(h.alpha)[0] == 2)
    with open(h.cache(), encoding="utf-8") as fh:
        check("the rebuilt cache is valid", json.load(fh)["turns"])
    check("a payload that isn't an object lets it through", subprocess.run(
        [sys.executable, HOOK], input="[1, 2]", capture_output=True, text=True, env=h.env).returncode == 0)
    check("garbage on stdin lets it through", subprocess.run(
        [sys.executable, HOOK], input="{oops", capture_output=True, text=True, env=h.env).returncode == 0)

    U = h.use()
    import usage_allocation as UA
    real = U.refresh

    def boom(*a, **k):
        raise RuntimeError("synthetic counting failure")

    U.refresh = boom
    old_in = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"session_id": "s-9", "cwd": h.alpha}))
    try:
        check("an exception in the counter lets the prompt through", UA.main() == 0)
    finally:
        sys.stdin = old_in
        U.refresh = real
finally:
    shutil.rmtree(TMP, ignore_errors=True)

if FAILS:
    print("usage allocation: %d failed" % len(FAILS))
    sys.exit(1)
print("usage allocation: all cases pass")
