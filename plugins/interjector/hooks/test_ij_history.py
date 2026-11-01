#!/usr/bin/env python3
"""Standalone tests for ij_history: history search command and question detection."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ij_history as H  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


# asks_user ---------------------------------------------------------------------------------------
check("trailing question to the user", H.asks_user("I set up the bucket.\n\nWhich region should it use?")
      == "Which region should it use?")
check("question in the final paragraph", H.asks_user("Done most of it.\n\nShould I also bump the version? "
                                                     "Tests pass either way.") == "Should I also bump the version?")
check("no question", H.asks_user("All done. Tests pass.") is None)
check("question only in a code block", H.asks_user("Added:\n```python\nok = input('Continue?')\n```\nDone.") is None)
check("question only in a quote", H.asks_user("You asked:\n> Can you fix it?\n\nFixed.") is None)
check("rhetorical question earlier, statement last", H.asks_user("Why did it fail? A missing env var.\n\nFixed it.")
      is None)

check("#4 rhetorical question answered in the same paragraph", H.asks_user("Done. Why did it fail? A missing env var, now set.") is None)
check("#4 inline code with ? is not a question", H.asks_user("Changed `cfg?.region` to a default. Done.") is None)
check("#4 URL with a query string is not a question", H.asks_user("Docs: https://example.com/a?b=1 for details.") is None)
check("#4 still catches a real question in the last paragraph", H.asks_user("Done.\n\nDo you want the old file kept?")
      == "Do you want the old file kept?")

# A reply can put its questions in a numbered list and end with an instruction, not a question mark.
# Reading only the last paragraph misses both shapes.
LIST_THEN_TELL_ME = ("I haven't created the file yet. Three questions:\n\n"
                     "1. **Name:** what should the bucket be called?\n"
                     "2. **Region:** which region should it be in (for example `us-east-1`)?\n"
                     "3. **Versioning:** should it be on or off?\n\n"
                     "If you also want encryption, tell me. Otherwise I'll keep the file to those three.")
q = H.asks_user(LIST_THEN_TELL_ME) or ""
check("questions in a list, reply ends 'tell me': all of them searched",
      "region" in q and "bucket be called" in q and "on or off" in q)
ONCE_I_HAVE = ("Two questions:\n\n1. **How many days should objects under `logs/` be kept?** Common choices "
               "are 30 or 90.\n2. **Which schema do you want?**\n\n```yaml\nExpiration: {Days: <N>}\n```\n\n"
               "Once I have the day count, I'll write the file.")
q = H.asks_user(ONCE_I_HAVE) or ""
check("reply ends 'once I have': earlier questions searched", "How many days" in q and "<N>" not in q)
check("hand-off with no question mark at all", "region" in (H.asks_user("Tell me the region and I'll write it.") or ""))
NEED_TO_KNOW = ("Three questions above.\n\nI also need to know which provider this is for, because the keys "
                "differ. I'll default to a flat file like this unless you want a different shape:\n\n"
                "```yaml\nname: <name>\n```")
check("'I also need to know ... unless you want' is a hand-off", H.asks_user(NEED_TO_KNOW) is not None)
ASSUMED = ("I created the `exports` branch from `master` and switched to it.\n\nThe repo has no naming "
           "convention, so I used a plain name. If you'd rather have a prefix like `feature/exports`, I can "
           "rename it with `git branch -m`.")
check("an assumption offered for correction is searched", H.asks_user(ASSUMED) is not None)
check("finished work with an earlier question is not a hand-off",
      H.asks_user("Should I add tests? I did, they pass.\n\nAll done.") is None)

# search ------------------------------------------------------------------------------------------
d = tempfile.mkdtemp(prefix="ij-hist-")
os.environ["GNOMONKIT_HOME"] = os.path.join(d, "home")   # never the real ~/.gnomonkit
os.environ.pop("GNOMONKIT_HISTORY_SEARCH", None)
os.environ.pop("GNOMONKIT_HISTORY", None)
check("no source configured -> None", H.search("anything") is None)

# Core's discovery file stands in when GNOMONKIT_HISTORY_SEARCH is unset.
core = os.path.join(d, "core_search.py")
with open(core, "w", encoding="utf-8") as fh:
    fh.write("import sys\nq=sys.argv[-1]\nprint('[1] core history: ' + q if 'deploy' in q else '')\n")
os.makedirs(os.path.join(d, "home", "history"))
disc = os.path.join(d, "home", "history", "search.json")
with open(disc, "w", encoding="utf-8") as fh:
    json.dump({"argv": [sys.executable, core, "history", "search", "--"]}, fh)
check("discovery file used when nothing is configured", "core history: deploy" in (H.search("deploy target") or ""))
check("discovery file: no match -> empty string", H.search("unrelated") == "")
os.environ["GNOMONKIT_HISTORY_SEARCH"] = "off"
check("GNOMONKIT_HISTORY_SEARCH=off turns the discovery file off too", H.search("deploy") is None)
os.environ.pop("GNOMONKIT_HISTORY_SEARCH")
os.environ["GNOMONKIT_HISTORY"] = "off"
check("GNOMONKIT_HISTORY=off: the discovery file is not used", H.search("deploy") is None)
os.environ.pop("GNOMONKIT_HISTORY")
with open(disc, "w", encoding="utf-8") as fh:
    json.dump({"argv": [sys.executable, os.path.join(d, "gone.py"), "history", "search", "--"]}, fh)
check("discovery file naming a missing script -> None", H.search("deploy") is None)
with open(disc, "w", encoding="utf-8") as fh:
    json.dump({"argv": ["C:/tools/search.cmd"]}, fh)
check("discovery file naming a .cmd wrapper is refused", H.search("deploy") is None)
with open(disc, "w", encoding="utf-8") as fh:
    fh.write("not json")
check("unreadable discovery file -> None", H.search("deploy") is None)
os.unlink(disc)

script = os.path.join(d, "s.py")
with open(script, "w", encoding="utf-8") as fh:
    fh.write("import sys\nq=sys.argv[-1]\nprint('match: region eu-west-2 for ' + q if 'region' in q else '')\n")
os.environ["GNOMONKIT_HISTORY_SEARCH"] = json.dumps([sys.executable, script])
check("source returns matches", "eu-west-2" in (H.search("which region") or ""))
check("source with no matches -> empty string", H.search("unrelated") == "")

big = os.path.join(d, "big.py")
with open(big, "w", encoding="utf-8") as fh:
    fh.write("print('x' * 10000)\n")
os.environ["GNOMONKIT_HISTORY_SEARCH"] = json.dumps([sys.executable, big])
check("output trimmed to 2000 chars", len(H.search("q") or "") <= 2000)

slow = os.path.join(d, "slow.py")
with open(slow, "w", encoding="utf-8") as fh:
    fh.write("import time\ntime.sleep(30)\nprint('late')\n")
os.environ["GNOMONKIT_HISTORY_SEARCH"] = json.dumps([sys.executable, slow])
t0 = time.time()
r = H.search("q")
check("hanging source gives up within ~11 s and returns empty", r == "" and time.time() - t0 < 13)

os.environ["GNOMONKIT_HISTORY_SEARCH"] = json.dumps(["C:/tools/search.cmd"])
check("#5 a .cmd/.bat search wrapper is refused", H.search("x\" & calc & \"") is None)
echo = os.path.join(d, "echo.py")
with open(echo, "w", encoding="utf-8") as fh:
    fh.write("import sys\nprint(repr(sys.argv[1:]))\n")
os.environ["GNOMONKIT_HISTORY_SEARCH"] = json.dumps([sys.executable, echo])
check("#5 a query cannot start with an option dash", "'--rm-everything'" not in (H.search("--rm-everything") or ""))

os.environ["GNOMONKIT_HISTORY_SEARCH"] = "not json"
check("malformed setting -> None", H.search("q") is None)

print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
