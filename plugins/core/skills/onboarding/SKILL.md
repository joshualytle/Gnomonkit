---
name: onboarding
description: Activates on a FIRST-RUN SETUP notice, on 'Gnomonkit setup' or 'change my preferences', or before hand-editing a personal.toml profile or onboarding.json.
---

> Full trigger text: Activates when the session-start block carries a FIRST-RUN SETUP notice and the user agrees to the setup, when the user asks for "Gnomonkit setup", "set up Gnomonkit", "re-run setup", "change my preferences", "what can I set" or "what does Gnomonkit know about me", and when the user wants one profile value changed ("always use prose", "shorter replies", "turn lessons off"), and before writing or editing a personal.toml profile or Gnomonkit's onboarding.json by hand.

# First-run setup, and changing it later

A new user has no personal profile, so nothing tells you how they want
decisions laid out, how long replies should be or what always needs their
confirmation. This skill is the one-time conversation that sets those values,
and the same conversation run again later. It takes about two minutes, every
question has a default, and the user can stop at any point.

## The command

Everything is saved by the `gnomonkit` command. **Never write or edit the
profile TOML yourself**: `profile set` checks every value against the schema,
keeps a `.bak` copy of the old file and writes nothing when a value is wrong.

Run it as the session-start notice shows, or as
`python "<core>/gnomonkit.py" <command>`, where `<core>` is the folder two
levels above this skill's folder (`<core>/skills/onboarding`). Once the user has
installed the launcher, plain `gnomonkit <command>` works as well. Use
`python3` where `python` is missing.

| Command | What it does |
|---|---|
| `profile show` | Every key with its value, its default, where the value comes from and the choices |
| `profile show --json` | The same, for you to read |
| `profile set key=value [key=value ...]` | Validates all of them, then saves them together |
| `profile unset key [key ...]` | Back to the default |
| `setup done` / `setup skipped` | Records that setup ran or was declined, so it isn't offered again |
| `setup status` | Setup state, profile path, disclaimer, launcher, security settings |
| `install-launcher` | Writes `gnomonkit` and `gnomonkit.cmd` to `~/.gnomonkit/bin` and prints the PATH line |

Quote any value that holds spaces: `safety.always_confirm="deleting data,publishing anything"`.
A list is items separated by commas.

## First run

The session-start block tells you when it is a first run. Answer whatever the
user asked first, then offer the setup in a sentence or two. If they decline,
run `setup skipped` and drop it. If they say "later", do nothing: the offer is
made again at the next session start, at most twice in all.

When they agree, go through these steps in order, one message each.

### 1. The disclaimer

Run `setup status`. If it says the disclaimer is acknowledged, go to step 2.
Otherwise show this paragraph and the link it prints, then ask them to
acknowledge it or skip:

> Before you rely on Gnomonkit: it is written and maintained by one person in
> their own time, with no support or fix promised. It is provided as is, with
> no warranty, and what results from running it is your responsibility (lost
> or changed data, commands allowed or blocked, agent, CI and API costs,
> anything published from your machine). The safety hooks are heuristics, not
> a sandbox or a security boundary, and no substitute for backups, review and
> least-privilege credentials. Agents it starts or steers act as you, with your
> account and permissions. Your data stays on your machine and stays your
> responsibility; secret scrubbing is best effort. Claude Code, GitHub and the
> other tools it works with have their own terms, and Gnomonkit is not made or
> endorsed by Anthropic or any of them. At work, check your employer's policy on
> AI tools first. Nothing in it is legal, security or other professional advice.
> The full text: <the link `setup status` printed>

On "yes" / "acknowledged", run `profile set acknowledged.disclaimer=1`. On
"skip", save nothing: the session-start block keeps one line pointing at the
disclaimer until they acknowledge it.

### 2. The default set

Run `profile show --json` and build one message from the `settings` whose
`group` is `default`: each question numbered, with its `choices` and its
`default` (on a re-run, the current `value`). Leave out `machine.shell` unless
its `choices` name more than one shell, and leave out `machine.kind` when it
already has a value. When it has none, ask "Is this a personal or a work
machine?" with the choices personal, work and "rather not say", which saves
`machine.kind=unknown` (treated as work, and not asked again). It is asked once
per machine, even when the personal profile came from another machine.
Roughly:

```text
1. Decisions: numbered list or prose? (default: numbered)
2. Reply length: short, normal or detailed? (default: normal)
3. Ask before acting: always, risky steps only, or rarely? (default: risky)
...
Answer by number ("2 short, 7 direct-ok"), "defaults" for all of them, or
"skip" to stop here. Say "advanced" for the rest (explanations, subagents,
plan and price, CI, code style, scratch folder, alerts, corrections repo).
```

For `orchestrator.mode`, say in a line what each choice means: `claude` runs
`claude -p` with their own login and counts against their plan, `api` needs
`ANTHROPIC_API_KEY` in the environment (never in the profile) and sends the
last few messages, redacted, to Anthropic, and `ollama` needs a local Ollama
server and a model named in `orchestrator.model`. It does nothing unless the
interjector plugin is installed. Default `off`.

Read their answer back as `key=value` pairs and save **the whole set in one
`profile set` call**: their answers plus the defaults for the questions they
left alone, so the session-start block states every one. "defaults" saves the
defaults. "skip" saves nothing from this step. If `profile set` refuses a
value, it prints the valid choices: ask again about that one only.

**`working.remote` is the one answer saved differently.** The question is
"Do you work remotely or from the phone a lot?". On yes, leave it out of the
`profile set` call and run `setup remote on` instead: it turns Claude Code's
`remoteControlAtStartup` on in the user-level settings (the old file is kept
as `.bak`), records `working.remote=yes` in the profile, and prints what is
still theirs to do. Pass `--project DIR` once per project they dispatch
sessions from (ask which), and show them the printed checklist as it is: one
block per project with the exact `grants add` and trust commands. **Never run
the grant or trust commands yourself**, not even when asked: they are
permissions, and the grants guard refuses them outside a terminal anyway.
On no, `working.remote=no` goes in the `profile set` call like any other
answer, and nothing else changes. Later, "I'm on the phone more now" is
`setup remote on` again; "turn Remote Control off" is `setup remote off`.

`machine.shell` and `machine.kind` go to the machine profile (one person can
have a personal and a work machine), and `plan.label` / `plan.monthly_usd` go
to the subscription file the effectiveness collector reads; `profile set`
routes them, so treat them like any other key.

### 3. Memory

Run `setup status` and read its `Memory backend:` line.

- **Personal machine, MemPalace not set up:** the line recommends MemPalace
  as the memory backend. Pass that on in a few sentences: what it is, its
  licence (MIT), the project link and that its README has the install and
  verify steps, what can leave the machine, and that Gnomonkit never installs
  it. Use the wording `setup status` prints, not your own. Don't install it,
  run its setup or add its MCP server yourself: if they want it, they follow
  its README.
- **Work machine, or kind unknown or not set:** the built-in local history
  index is the default. **Don't mention MemPalace at all**, not even as an
  alternative.
- **Already set up:** say so in a line. With MemPalace present,
  Gnomonkit's own history index and lessons stay off unless the user turns
  them on (`memory.history` and `memory.lessons` are `auto`); when
  `setup status` prints the line saying so, pass it on with the command it
  gives, and move on.

### 4. Advanced, when asked

Same pattern, from the `advanced` group. Ask only what they want to set; never
guess a corrections repo or a price. A corrections repo must be private.

### 5. The launcher

Offer `install-launcher` so they can type `gnomonkit` in a terminal. It prints
the line that puts `~/.gnomonkit/bin` on PATH. Show that line; **don't edit
their shell profile**: they add it themselves.

### 6. Finish

Run `setup done`. Then tell them, in one short paragraph:

- what was saved, and that `gnomonkit profile show` (or asking you) shows it
  and "change my preferences" changes it;
- that the settings which grant permissions (which sessions may be started
  for them, which folders are trusted) are not part of this conversation on
  purpose: they run `gnomonkit setup security` **themselves, in a terminal**.
  It refuses to run without one. **Never run it for them**, not even when
  asked: say why and give them the command.

If the user stops part way, run `setup skipped` only when they say they don't
want it; otherwise leave the state alone so the next session start offers it
again.

## Later: "change my preferences", "what can I set"

Run `profile show`, summarise what is set and what is at its default, and ask
what to change. Save only what they name, in one `profile set` call. "What
can I set" is answered from the same output, grouped as the default set and
advanced. A value from a plugin option wins over the profile (`profile show`
says "plugin option"): say so and point at `claude plugin configure` rather
than saving a profile value that will be ignored.

"Re-run setup" is the first-run conversation again, with the current values
shown as the defaults.

## Don't

- Don't write `personal.toml`, `machine.toml`, `onboarding.json` or the
  subscription file by hand.
- Don't run `setup security`, and don't set anything the user didn't answer.
- Don't start the setup in a session nobody is watching: dispatched and remote
  sessions never get the notice, and you don't start it there yourself.
