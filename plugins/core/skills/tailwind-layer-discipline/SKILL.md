---
description: Activates when adding or editing global CSS in a Tailwind project, or when a utility class seems to have no effect and !important is tempting.
---

# Global element styles go inside @layer base

**Unlayered CSS beats every Tailwind utility.** A plain `h1 { color: navy }`
in a global stylesheet silently defeats `class="text-white"` everywhere, and
nothing reports it: the build succeeds, the class is present in the HTML, and
the page is simply wrong.

A typical failure: a heading colour rule sits unlayered in a global
stylesheet, so every `text-white` heading on a dark section renders
dark-on-dark — invisible text that ships and is found only by looking at a
screenshot.

## The rule

- Base element styles (`body`, `h1`–`h4`, links) → inside `@layer base`
- Shared component classes → inside `@layer components`
- Nothing global stays unlayered

Layered correctly, the cascade works the way the utility-first model assumes:
utilities win over base styles, and per-element overrides stop needing
`!important`.

## Debugging a utility that "doesn't work"

Before adding `!important` or a more specific selector, grep the global
stylesheet for a rule targeting that element and check whether it sits outside
a layer. That is the cause far more often than specificity.

## When NOT to use this

Projects not using Tailwind, and deliberate resets that must outrank
everything — which should be rare and commented as such.
