---
description: Activates when building or restyling long-form pages (post, article, docs, changelog, legal), reusing a prose class for new content, or when a page is called bland or a wall of text.
---

> Full trigger text: Activates when building or restyling any page of long-form content — a blog post, article, docs page, changelog, legal page — when reusing an existing prose or typography class for a new kind of content, and when a page is described as bland, flat, or "a wall of text". Also before asking anyone to approve visual work.

# Long-form pages need a skeleton, and prose classes lie

Two separate failures produce the same verdict — "this looks terrible" — and
they need different fixes. Sort them before touching anything:

1. **Elements rendering unstyled**, because the stylesheet never covered them.
   That is a bug with a grep-able answer.
2. **Correctly styled text with no visual structure.** That is a design
   problem.

Fixing (2) while (1) is still true wastes the effort — the page still looks
broken, just prettier around the broken parts.

## A prose class only covers what its first content needed

**A typography class inherits the assumptions of whatever it was written for.**
Reuse it for new content and it silently fails on everything the original
content never used.

For example, a `.legal-prose` class written for legal pages styles `p`, `ul`,
`li`, `a`, `h2`, `h3` — and nothing else, because privacy policies use nothing
else. Blog posts are later rendered with the same class. Markdown emits things
it never covered, and a CSS reset has already stripped their browser defaults,
so:

- **Ordered lists render with no numbers at all.** A post that ends on a
  prioritised "do this in order" list loses the order.
- **Tables render with no borders, padding or header row** — naked columns of
  text.
- Inline `code` and `blockquote` are unstyled too.

Nothing fails. The build is green, the HTML is correct, and the pages pass
review and ship.

### The check

**Enumerate what the content actually emits, then confirm each one is styled.**
For markdown that means at minimum: `p`, `ul`, `ol`, `li`, `table`/`th`/`td`,
`code`, `pre`, `blockquote`, `hr`, `strong`, `em`, `a`, `h2`–`h4`, `img`,
`figure`.

Grep the **built** stylesheet for the selectors, not the source — a rule that
was purged, layered wrong, or never written all look identical from the source
side. If a selector has no rule, that element is rendering naked right now.

Modern CSS resets (Tailwind preflight among them) strip list markers, table
borders, heading sizes and quote indentation. Everything the reset removes and
the class does not restore is invisible damage.

## What actually makes long-form readable

Once nothing is naked, the design problem is structure. In rough order of
effect per unit of work:

- **A visual break every three or four paragraphs.** A chart, a table, a
  callout, a pulled statistic. This is not decoration — without it nobody can
  find anything on a second visit, which is when the page earns its keep.
- **A visible skeleton.** Give every `h2` a consistent structural marker — a
  short accent rule above it works — so the page's shape is legible while
  scrolling past at speed.
- **Pull the numbers out.** If a page rests on three or four figures, they
  belong in something a scanner catches, not buried mid-paragraph.
- **A lead paragraph set larger than body.** It states the finding, and its
  size tells the reader the page has an argument.
- **A measure near 65–75 characters**, and section headings with real space
  above them.

### Data stays as data

When you turn a table into a chart, **build it from a real `<table>`** with a
visually-hidden `<thead>` and the bars drawn with CSS. Do not emit an image, a
canvas, or a pile of unlabelled `<div>`s.

An image of a chart is invisible to screen readers and to the answer engines
you want quoting your numbers — see `seo:answer-engine-visibility`. A page that
converts its most citable content into a picture has made itself less useful
to get more attractive.

### Every page gets its own image

**One shared hero photo across every article is the single most noticeable
thing that makes a site look cheap**, and it is the cheapest to fix. If there
is no budget for per-page photography, generate simple per-page vector
artwork — a gradient and one geometric motif, a few KB, distinct per topic —
rather than repeating one stock photo. Distinct beats good here.

## Getting visual work approved

**Nobody can approve prose about a design.** If you cannot see the rendered
result, neither can a description of it substitute for one.

When the deliverable is visual and someone has to sign it off:

- **Build the comparison from the real builds** — the actual HTML and CSS from
  each branch, in iframes — not a mockup of them. What gets approved is then
  literally what deploys.
- **Show before and after together**, and let the viewer switch page and
  viewport width. Responsive bugs are invisible at one width.
- **Separate the defects from the taste calls** in the write-up. They are
  judged differently: a bug is not a matter of opinion, and mixing them invites
  a debate about the wrong thing.
- **Say plainly which parts you have not seen rendered yourself**, and ask for
  specifics back rather than a verdict.

## When NOT to use this

Short pages with no body content — a login screen, a pricing table, a
dashboard. The prose-coverage check still applies anywhere markdown or rich
text is rendered, including a docs site or a legal page.
