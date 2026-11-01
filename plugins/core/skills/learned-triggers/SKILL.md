---
name: learned-triggers
description: Activates when the user corrects something you should have done unprompted ('you didn't...', 'I had to tell you again'), or a correction repeats.
---

# A repeated correction is a missing trigger

The same corrections tend to come back: ask before assuming, search the
history before asking, put data in a page the user can answer. Each gets
fixed in the session it happens in and forgotten by the next. Nothing
records that it happened before, so nobody can see which ones cost the
most. The fix: every correction becomes a work item in the user's private
repo, and every repeat is a comment on it. The comment count is the
priority. Later the user sits down with the agent, takes the top items,
and turns each into a real trigger.

## When the user corrects you

1. **Fix what they asked first.** Recording the correction never comes
   before doing the work.
2. **Decide if it is a missed trigger.** A missed trigger is something you
   should have done unprompted in a situation you could have recognised:
   a file type, a command, a kind of request. "Use blue, not green" is a
   one-off preference: record nothing. "You never check X before Y" is a
   trigger.
3. **Look for an existing issue first.** Run `file_correction.py list`
   (it sits next to this file). Issues are sorted by how often they
   repeated. If one covers the same situation, it is a repeat, even when
   the wording differs.
4. **Record it.**
   - Repeat: `file_correction.py repeat <number> --body-file <file>`
   - New: `file_correction.py new --title "<title>" --body-file <file>`

   Write the body to a file first; never pass it on the command line.
5. **Tell the user in one line:** "Logged as #12 (third time)." Nothing more.

The script reads the repo from the core plugin's `corrections_repo`
option. It refuses any repo that is not private, because the body quotes
the user, and it redacts common secrets before sending. If no repo is set,
say once that setting one would track repeats, then drop it.

## What goes in the issue

Title: **"When <situation>, <what to do>"**. Name a situation that can be
detected, not a mood. Good: "When asked for data, answer with a report
page". Bad: "Be more careful".

```markdown
**Situation:** what was happening, in terms a hook could see (a request type, a file, a command).
**Expected:** what should have happened without being told.
**What happened:** one line.
**User's words:** a short quote, only what is needed.
**Proposed trigger:** the signal and where it lives: a prompt phrase on UserPromptSubmit, a path
or command pattern in triggers.yaml, a check in a hook, or a line in CLAUDE.md as a last resort.
**Seen in:** project or repo name, date.
```

A repeat comment is short: date, project, what happened this time, the
user's words.

## Reviewing them with the user

When the user wants to go through them, run `list` and start from the
top. For each one, agree what the trigger is, build it (a pattern with
fire and quiet fixtures, or a hook with tests), and close the issue with a
link to the change. A trigger with no quiet fixture has never been checked
for false alarms, and an alarm that cries wolf gets ignored.

## Related

`report-pages`, `verify-before-asserting`, `learned-corrections` (which is
about estimates refined from history, not about the user's corrections).
