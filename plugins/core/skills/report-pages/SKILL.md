---
name: report-pages
description: Activates when asked for data, a report, status, comparison or decisions, when an answer is a table or many findings, or after the user answers a page you published.
---

> Full trigger text: Activates when the user asks for data, a report, a status, a comparison, a review or a set of decisions, when an answer would be a table or more than a few lines of findings, when writing an HTML page or a report-shaped Markdown file for someone to read, and when the user has answered a page you published.

# Data answers are pages the reader can answer

Without a standard, data answers drift: a terminal table one day, a
Markdown file in a PR the next, a long HTML page with nothing to click the
day after. The reader has to restate what they want each time, and their
reply comes back as prose that costs a round trip to interpret. Readers
often read on a phone. What works is the same page every time, with a way
to hand back a verdict per item, not an essay.

## The standard

When an answer is more than a few lines of findings, or anything someone has
to decide on:

1. **It is a page, not terminal text or a Markdown file.** Start from
   `template.html` in this skill's folder, so every page in every project
   looks and behaves the same.
2. **The answer comes first**, in a sentence, then the evidence. A reader on
   a phone may not scroll.
3. **Every item that needs a verdict is a `<section data-ask id="...">`.** The
   template gives it Agree / Wrong / Needs work / Question plus a note. A
   decision gets its own options: `data-ask="Do it|Not now|Talk first"`.
   Content that needs no answer gets no buttons.
4. **It reads on a phone.** No sideways scrolling. Tables at about four
   columns; drop the widest low-value column rather than make the table
   scroll. Numbers stay as text, never baked into an image.
5. **Answered items fold away.** The template folds a section once it has a
   verdict, so the page shows only what is still open. When you republish
   after reading answers, leave settled items out entirely.

## Where answers go

On claude.ai, publish the page with the Artifact tool and declare the `db`
capability (`capabilities: {db: {}}`). Each answer is saved as a document in
the page's `answers` collection, keyed by the section id:
`{section, title, verdict, note, at}`.

- **Read them back before acting.** List the `answers` collection with the
  artifact data tool. Do this when the user says they answered, and before
  you republish the page (a republish must not drop answers you haven't read).
- **Tell the reader where to answer.** Answers typed into a local copy of the
  file never reach you. Give the published link and say so; the page's own
  status bar also says whether answers are reaching Claude.
- **Private by default.** Published pages are private until the owner shares
  them. If the page is for other people, say they cannot open it until it is
  shared. When the user is done with a page, offer to delete it.

Without the Artifact tool (another host, or no claude.ai account), write the
same template to a file and open it. There is no store there, so the reader
uses **Copy feedback** and pastes back a compact block:

```text
FEEDBACK: <page title>
- answer [Agree]
- choice-1 [Not now] after the release
```

Treat each line as that section's verdict.

## Before you send

- Is the first line the answer?
- Does every item that needs a decision have buttons, and nothing else?
- Would any table scroll sideways on a phone?
- Did you read the answers already on this page before republishing it?
