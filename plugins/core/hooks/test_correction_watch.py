#!/usr/bin/env python3
"""Fixtures for correction_watch: corrections fire, ordinary requests stay quiet.

Every sentence here is synthetic: written to exercise one pattern family each, with no content taken
from anyone's sessions. Keep a private regression set of your own real phrasings in your overlay.
A quiet case is worth more than a fire case: a nudge on every prompt is noise."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import correction_watch as cw  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


FIRE = [
    "You didn't run the tests",
    "Why did you delete that function",
    "I had to remind you yet again about the version bump.",
    "make sure you search the docs before asking me",
    "you could have just checked the log file first",
    "I already told you the server runs Debian",
    "That's not what I asked for",
    "Stop adding comments to every line",
    "From now on write the summary in bullet points",
    "you forgot the closing tag again",
    # one case per family added in the calibration (issue #29)
    "why are you writing the tests in Java?",
    "why can't you read the config file yourself?",
    "not sure why you renamed the module, it was fine",
    "like I said, the build uses Python 3.12",
    "earlier I said port 8080, you're not tracking this",
    "I said rename but I meant move the file",
    "at no point did I ask for a dark mode",
    "in the future, never push straight to the release branch",
    "you always need to run the linter first",
    "you should never skip the migration step",
    "stop guessing the column names and read the schema",
    "you are not listening, I already set the flag",
    "that's wrong, the function returns a list",
    "I keep trying to tell you the cache is stale",
    "did you not read the error message?",
    "you also never replied about the font size",
    "no, you got it wrong, the test is in the other folder",
    "THAT'S NOT THE ISSUE, the import path is",
    "as I've said before, keep the header short",
]
# Requests and remarks that share words with a correction family but are not corrections.
QUIET_FAMILIES = [
    "can you keep the old endpoint working?",
    "ensure you keep the changelog in order",
    "you should have a settings page for this",
    "the log said the job already ran, is that right?",
    "why is the build still failing on CI?",
    "why is the button grey, can you check the style?",
    "flag anything that's wrong in the report",
    "in the future we might add a second language",
    "the fan keeps spinning, make it stop it's noisy",
    "tell me if you missed any steps in the setup",
]
QUIET = [
    "Can you do this?",
    "Yes, go ahead with that plan",
    "Go ahead and build the parser then",
    "Nice, just make the icon a bit larger so it lines up with the text.",
    "Can you restart the dev server?",
    "Is there anything else you can pick up meanwhile?",
    "Can you test it and add whatever else the spec needs",
    "Why is the nightly export failing?",
    "Ok, start on it",
    "Run it again with five samples",
]
# Calibration table (core 0.18.0). Synthetic, written in the register typed prompts actually take:
# lower case, dropped apostrophes, numbered replies to a numbered question, run-on sentences. The
# negatives are the families that typically fire falsely: approvals and confirmations,
# task instructions that contain "make sure", reassurance ("you didnt need to"), and pasted text.
CAL_POS = [
    "you didnt run the migration before you said it was done",
    "i already told you the staging db is read only",
    "make sure you always check the open PRs before starting a new branch",
    "why did you change the config when i only asked about the readme",
    "why arent you running the tests yourself? you have the command",
    "im not sure why you are asking me this, its in the notes",
    "you never replied about the export format",
    "in the future dont open a new PR for every small fix",
    "you should have checked the logs before blaming the network",
    "thats not what i asked for, i wanted the summary not the full table",
    "like i said the api key lives in the vault",
    "you keep adding emoji to the commit messages",
    "stop guessing at the file names and list the folder",
    "make sure you read the whole error next time",
    "when i ask for a fix that means push it too, not just describe it",
    "no thats wrong, the job runs hourly not daily",
    "did you not see the failing check on the PR?",
    "you forgot to bump the version again",
    "1. this works\n\n2. you missed the second page entirely, it still shows the old title",
    "ok but you always need to test on the phone layout too",
    "why cant you just merge it yourself, you have permission",
    "you are not reading what i write, the port is 3000",
    "i told you twice already about the trailing slash",
    "make sure to actually run it this time before telling me its fixed",
]
CAL_NEG = [
    "yeah go for it",
    "sounds good",
    "yes do that",
    "thats the correct price",
    "yes thats right",
    "ok go ahead and merge",
    "perfect, ship it",
    "1. yes\n2. go with the second option\n3. sounds good",
    "make sure you give me the link when its deployed",
    "and then make sure the backup runs at night",
    "can you make sure the footer matches the header style",
    "make sure to include the changelog in the PR",
    "great, now make sure the tests pass and open the PR",
    "2. yes it can and 1. can wait until monday, just make sure you send me the preview url",
    "you didnt need to change the tests, but its fine",
    "no worries, you didnt have to redo it",
    "if you didnt get to the docs yet thats fine",
    "why is the deploy taking so long?",
    "can you keep going on the parser",
    "what did you find in the logs?",
    'the email said "you never replied" so check the inbox',
    "> you forgot to close the tag\nthis was the review comment, can you fix it",
    "here is the error:\n    you missed a required field: name\nwhat does it mean",
    "run `git log --since=yesterday` and tell me what changed",
    "this version is much faster, nice work",
    "do the same for the other two pages",
    "tell me if you missed anything",
    "lets add a dark mode next",
    "the old version never loaded on safari, does this one?",
    "in the future we may add billing",
    "keep the old endpoint for now",
    "that looks correct to me",
    "the reviewer said the build is broken again",
    "Task for the cleanup agent.\n\nContext:\n  the user said you forgot the temp folders before\n"
    "  never push to main\nReport back when done.",
]
tp = sum(cw.is_correction(p) for p in CAL_POS)
fp = sum(cw.is_correction(p) for p in CAL_NEG)
print(f"  calibration: {tp}/{len(CAL_POS)} positives fire, {fp}/{len(CAL_NEG)} negatives fire, "
      f"precision {tp / max(1, tp + fp):.0%}")
check("calibration table is big enough", len(CAL_POS) >= 20 and len(CAL_NEG) >= 30)
for p in CAL_POS:
    check(f"cal fires: {p[:50]!r}", cw.is_correction(p))
for p in CAL_NEG:
    check(f"cal quiet: {p[:50]!r}", not cw.is_correction(p))

for p in FIRE:
    check(f"fires: {p[:50]}", cw.is_correction(p))
for p in QUIET + QUIET_FAMILIES:
    check(f"quiet: {p[:50]}", not cw.is_correction(p))
check("quoted text is not the user correcting", not cw.is_correction('He wrote "you didn\'t ask me" in the log'))
check("code blocks are ignored", not cw.is_correction("Fix this:\n```\n# you forgot to close it\n```"))


HOME = tempfile.mkdtemp(prefix="correction-watch-test-")


def run(prompt, **env):
    # No option, GNOMONKIT_* setting or personal profile from the developer's own setup.
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_"))}
    e["GNOMONKIT_HOME"] = HOME
    e.update(env)
    r = subprocess.run([sys.executable, os.path.join(HERE, "correction_watch.py")], capture_output=True, text=True,
                       input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt}), env=e)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else None)


rc, out = run("Can you do this?", CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO="me/overlay")
check("quiet prompt: exit 0, no output", rc == 0 and out is None)
rc, out = run("You didn't run the tests", CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO="me/overlay")
ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
check("correction: context names the skill and the repo", rc == 0 and "learned-triggers" in ctx and "me/overlay" in ctx)
check("correction: never blocks", "decision" not in (out or {}) and "permissionDecision" not in json.dumps(out))
check("correction: the user's words are not echoed back", "run the tests" not in ctx)
rc, out = run("You didn't run the tests")
ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
check("no repo configured: says how to set one, still never blocks", rc == 0 and "corrections_repo" in ctx)
with open(os.path.join(HOME, "personal.toml"), "w", encoding="utf-8") as fh:
    fh.write('[corrections]\nrepo = "example-org/agent-notes"\n')
rc, out = run("You didn't run the tests")
ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
check("no option: the personal profile's corrections.repo is used", "example-org/agent-notes" in ctx)
rc, out = run("You didn't run the tests", CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO="me/overlay")
ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
check("the option wins over the profile", "me/overlay" in ctx and "example-org" not in ctx)
os.remove(os.path.join(HOME, "personal.toml"))
rc, out = run("", CLAUDE_PLUGIN_OPTION_CORRECTIONS_REPO="me/overlay")
check("empty prompt: nothing", rc == 0 and out is None)
r = subprocess.run([sys.executable, os.path.join(HERE, "correction_watch.py")], capture_output=True, text=True,
                   input="not json")
check("garbage input: exit 0, no output", r.returncode == 0 and not r.stdout.strip())

shutil.rmtree(HOME, ignore_errors=True)
print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
