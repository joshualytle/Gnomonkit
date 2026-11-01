---
description: Activates when adding or editing a page, route or layout on a web project, or adding a third-party script, font, image or API origin.
---

# Every page carries the same baseline

These are the things that are invisible when correct and expensive when not:
screen-reader users silently cannot use a page, and a CSP violation silently
breaks a feature **in production only**. No build step reports either.

**SEO (title, description, h1, canonical, JSON-LD) lives in the `seo` pack**:
`seo:page-pass`, enabled in repos that publish websites; its baseline is the pack's
`references/page-baseline.md`.

## Accessibility (WCAG 2.2 AA)

- Skip-to-content link; visible focus rings; honour `prefers-reduced-motion`
- `<html lang>` set
- Real `<label>`s on controls; `aria-live` on any status message
- Every image has meaningful `alt`, or `alt=""` when purely decorative

## Content-Security-Policy

Security headers (CSP, HSTS, `frame-ancestors 'none'`, Permissions-Policy)
belong in the served response — on static hosts, usually a headers file in the
published directory.

**Adding any third-party origin — a script, a font, an image host, an API you
`fetch` — requires updating the CSP in the same change.** Miss it and the
feature works locally and in preview, then silently fails for real users. The
build will not warn you. The strongest position is a CSP with no third-party
origins at all; every origin added is a permanent commitment.

## When NOT to use this

Do not use it for search work (titles, descriptions, structured data,
sitemaps): that is the `seo` pack. Internal tools with no public surface still
need everything in this file.
