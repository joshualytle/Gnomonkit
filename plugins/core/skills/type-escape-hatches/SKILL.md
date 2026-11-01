---
description: Activates when writing dynamic, any, object, a raw generic or a cast to satisfy a signature, or when a feature is broken in the app while its unit tests are green.
---

> Full trigger text: Weakening a type to make a constructor or a test compile turns a compile error into a runtime crash, and mocks then hide it — a whole feature can ship, pass its suite, and have never once executed. Activates when writing `dynamic`, `any`, `object`, a raw generic, or a cast to satisfy a signature; when a test needs a dependency it cannot easily build; and when a feature is reported broken in the running app while its unit tests are green.

# A type escape hatch turns a compile error into a runtime one

`dynamic`, `any`, `object`, a raw generic, a `# type: ignore` — each one asks
the compiler to stop checking a call site. The bug that would have been a red
squiggle becomes a crash on the first real input instead.

Then mocks finish the job. A unit test that substitutes a fake for the
weakened dependency never executes the line that fails, so the suite is green
and the feature has never run.

An example of how this goes wrong, in Dart: a class holds a dependency as
`dynamic`. Enum `.name` is an *extension* getter, and extension members do not
resolve on a dynamic receiver — so a call to it compiles and throws
`NoSuchMethodError` at runtime. A broad catch reports it as some other
failure. Everything built on top of that call compiles, passes its mocked unit
tests, and never once executes against real data. Static types would have
refused to compile the line.

## The rule

- **Do not weaken a type to make something compile.** Not a constructor
  parameter, not a field, not a return.
- **If a test cannot easily build a dependency, make the field nullable and
  check it** — or build the real thing. Nullable is honest and still checked;
  `dynamic` is neither.
- **Prefer one end-to-end test that drives the public entry point** over
  several unit tests that mock around it. The mocks are what let this survive.
- **When you inherit `dynamic` in code you are touching, retype it** as part of
  the change. It is a latent crash, not a style preference.

## Why it is worse than it looks

The escape hatch does not fail loudly at the boundary. It fails at whatever
call site first needs the thing the compiler stopped checking — often deep
inside a `try`, where the catch converts it into a plausible-sounding message
about something else. "Sync failed, check your connection" is what a
`NoSuchMethodError` looked like from the outside.

Language-specific traps worth knowing, all of the same shape:

| Language | The trap |
|---|---|
| Dart | Extension members do not resolve on `dynamic`; enum `.name` is an extension |
| TypeScript | `any` disables excess-property and arity checks, not just the member check |
| Python | Duck typing plus `MagicMock` will answer any attribute, so a typo passes |
| Java | Raw generics silently erase, deferring the `ClassCastException` |

## The tell

**A feature reported broken in the running app while its unit tests are
green.** Before debugging the feature, look for the escape hatch between the
caller and the thing that is not running. Also see `owner-is-not-qa`: driving
the real surface is what surfaces this at all.

## When NOT to use this

Genuine boundaries where the type is not knowable — deserialising untyped
JSON, a plugin interface, reflection. There, parse into a real type at the
boundary and keep the dynamic value confined to the parsing function, rather
than letting it travel into the rest of the code.
