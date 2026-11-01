#!/usr/bin/env python3
"""Calibration fixtures for page_nudge: data breakdowns fire, one-number questions, code and chat stay quiet.

Every prompt here is synthetic, written to exercise one family each, with no content taken from anyone's
sessions. The two bench prompts are copied from bench/scenarios/skills.py (a05 must fire, a06 must not).
Keep a private regression set of your own real phrasings in your overlay. A quiet case is worth more
than a fire case: a nudge on every prompt is noise."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import page_nudge as pn  # noqa: E402

FAILS = []
DIRECT = " Do the work directly, without asking questions."


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


BENCH_FIRE = [
    ("Using data/orders.csv, which regions grew from August to September 2026 and by how much? "
     "I have to decide where next quarter's budget goes." + DIRECT),  # a05-data-breakdown-page
]
BENCH_QUIET = [
    "How many orders are in data/orders.csv, not counting the header row?" + DIRECT,  # a06-one-number-no-page
]
FIRE = [
    "Give me a breakdown of last month's cloud spend by service",
    "Can you break down the support tickets by category for Q3?",
    "Compare the three hosting plans on price, storage and uptime",
    "Rank our vendors by on-time delivery rate from the spreadsheet",
    "What are the top 5 products by revenue this quarter?",
    "Which of these laptops should I buy given the prices and battery life in the list?",
    "Which regions dropped below target in September, and by how much?",
    "Show signups per month for 2025 so I can see the trend",
    "How many orders per region did we get in August?",
    "Put the benchmark results for each machine in a table",
    "Help me decide between the two quotes, one is 4200 and the other 3900 with support",
    "What are the pros and cons of the three backup providers, with their monthly cost?",
    "Sort the customers by total spend and tell me which ones to follow up with",
    "Postgres vs MySQL for this workload: compare the latency numbers from the test runs",
    "Which teams are over budget this year and what's the gap for each?",
    "List the slowest pages on the site with their load times, side by side with last week",
    "Where should we spend the marketing budget next quarter, based on the channel results?",
    "Group the incidents by service and show how many hours each one was down",
    # held-out cases written after tuning; this one was a miss, fixed by the "which X had falling" family
    "Tell me which stores had falling sales in October and how much they fell",
    "Rank the job candidates by their interview scores",
    "I need the monthly traffic split by country for the board",
]
QUIET = [
    # single numbers
    "How many rows are in the customers table?",
    "What's the total revenue for September?",
    "what is the average order value in the export?",
    "How much did we spend on hosting last month?",
    "Is there a row for order 1043 in the CSV?",
    # code tasks that share words with a breakdown
    "Compare the two implementations of the parser function and pick the faster one",
    "Fix the bug where the sort by date ignores the year",
    "Refactor report.py so each region is handled by one function",
    "Write a script that breaks down the CSV by month",
    "Rank the failing unit tests by how often they flake and fix the worst",
    "Why does the import of utils.ts fail on the build server?",
    "Add a test for the per user rate limit",
    # held-out: fired before "code" joined the code-task words
    "Compare these two error messages and tell me which line of code throws",
    "Which branch should I merge first?",
    "How long does the nightly job take?",
    # chit-chat and short replies
    "thanks, looks good",
    "ok go ahead",
    "compare them",
    "yes, the second one",
    "Nice work on that, take a break",
    # plain requests with no comparison shape
    "Summarise what the meeting notes say about the launch date",
    "Restart the dev server and tell me when it is up",
    "Explain how the backup rotation works in plain words",
    "Can you read the README and tell me what this project does?",
]
for p in BENCH_FIRE + FIRE:
    check(f"fires: {p[:60]}", pn.wants_page(p))
for p in BENCH_QUIET + QUIET:
    check(f"quiet: {p[:60]}", not pn.wants_page(p))
check("quoted text is not the question", not pn.wants_page('The ticket title was "compare costs per region in 2025" , close it'))
check("code blocks are ignored", not pn.wants_page("Look at this output please\n```\nrank by revenue per region 2025\n```"))

# Two tiers. A prompt that states a decision the reader must make gets the firm note (answer as a page,
# one data-ask section per option, even when short); a plain breakdown or comparison keeps the softer one.
FIRM = BENCH_FIRE + [
    "Help me decide between the two quotes, one is 4200 and the other 3900 with support",
    "Where should we spend the marketing budget next quarter, based on the channel results?",
    "Here are the q3 numbers per team. I need to decide which projects to cut.",
    "Which of the four pilots should we fund next year? The usage stats are in the sheet.",
    "Compare the three vendors on price and uptime, I have to choose one by friday",
    "Using the survey results, which features to prioritise for the next two months?",
    "Break down the support costs by region, we must decide where the headcount goes",
]
SOFT = [
    "Give me a breakdown of last month's cloud spend by service",
    "Compare the three hosting plans on price, storage and uptime",
    "Rank our vendors by on-time delivery rate from the spreadsheet",
    "Show signups per month for 2025 so I can see the trend",
    "Which regions dropped below target in September, and by how much?",
    "Group the incidents by service and show how many hours each one was down",
]
for p in FIRM:
    check(f"firm tier: {p[:60]}", pn.wants_page(p) and pn.note(p) == pn.DECISION_NOTE)
for p in SOFT:
    check(f"soft tier: {p[:60]}", pn.wants_page(p) and pn.note(p) == pn.NOTE)
check("firm note asks for the template and a data-ask per option, even when short",
      all(w in pn.DECISION_NOTE for w in ("core:report-pages", "template", "data-ask", "even when")))
check("firm note has no answer-in-chat escape hatch", "answer in chat" not in pn.DECISION_NOTE)
check("decision language alone never makes a one-number question fire",
      not pn.wants_page("How many orders are there? I have to decide by friday."))

hits = sum(pn.wants_page(p) for p in BENCH_FIRE + FIRE)
false = sum(pn.wants_page(p) for p in BENCH_QUIET + QUIET)
print(f"  calibration: {hits}/{len(BENCH_FIRE + FIRE)} fire cases hit, "
      f"{false}/{len(BENCH_QUIET + QUIET)} quiet cases fired")


def run(prompt, **env):
    e = {k: v for k, v in os.environ.items() if k != "GNOMONKIT_PAGE_NUDGE"}
    e.update(env)
    r = subprocess.run([sys.executable, os.path.join(HERE, "page_nudge.py")], capture_output=True, text=True,
                       input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt}), env=e)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else None)


rc, out = run(FIRE[0])
ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
check("fire: context names the skill", rc == 0 and "core:report-pages" in ctx)
check("fire: event name is UserPromptSubmit",
      ((out or {}).get("hookSpecificOutput") or {}).get("hookEventName") == "UserPromptSubmit")
check("fire: never blocks", "decision" not in (out or {}) and "permissionDecision" not in json.dumps(out))
check("fire: the user's words are not echoed back", "cloud spend" not in ctx)
rc, out = run(QUIET[0])
check("quiet prompt: exit 0, no output", rc == 0 and out is None)
rc, out = run(FIRE[0], GNOMONKIT_PAGE_NUDGE="off")
check("GNOMONKIT_PAGE_NUDGE=off turns it off", rc == 0 and out is None)
rc, out = run("")
check("empty prompt: nothing", rc == 0 and out is None)
r = subprocess.run([sys.executable, os.path.join(HERE, "page_nudge.py")], capture_output=True, text=True,
                   input="not json")
check("garbage input: exit 0, no output", r.returncode == 0 and not r.stdout.strip())

print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
