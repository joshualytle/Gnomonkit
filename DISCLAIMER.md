# Disclaimer

A plain-language summary of what you take on by using Gnomonkit. The [licence](LICENSE) (MIT) is
the legal text, and where this page and the licence differ, the licence applies.

## What this is

Gnomonkit is written and maintained by one person, in their own time. It is tested, and it still
has bugs, gaps and rules that will turn out to be wrong. There is no support agreement and no
promised fix or release date. Security reports get an acknowledgement within a week, as
[SECURITY.md](SECURITY.md) says; that is the one reply time promised.

## No warranty, no liability

Gnomonkit is provided as is, without warranty of any kind. You use it at your own risk. Whatever
results from installing or running it is your responsibility, including but not limited to:
data that is lost or changed, commands that were allowed or blocked, costs from agent sessions,
CI minutes or API usage, and anything published or sent from your machine. The maintainer is not
liable for any of it.

## The safety hooks are heuristics

The hooks catch some mistakes and miss others. They are not a sandbox, not a security boundary, and
not a substitute for backups, code review, least-privilege credentials or your own judgement. A
hook that stays quiet has not proved that a command is safe.

## Agents act as you

Sessions that Gnomonkit starts or steers run with your account, your credentials and your
permissions, and can do anything you could do from that machine. Check what you allow them to do,
and where.

## Your data stays on your machine, and stays your responsibility

Gnomonkit keeps its indexes, logs and profiles in local files. They hold text from your own
sessions. Secret scrubbing is best effort: it removes the patterns it knows and can miss others.
Back these files up, protect them, and delete them like any other sensitive data.

## Other tools

Claude Code, MemPalace, GitHub, package registries and everything else Gnomonkit works with have
their own terms, and those terms apply to your use of them. Gnomonkit is an independent project.
It is not made, endorsed or supported by Anthropic or by any of those projects.

## At work

Before you install Gnomonkit on a work machine, check your employer's policies on AI tools, on
installing plugins, and on where session data may be stored. If your employer is involved, ask them,
or their lawyers, rather than relying on this page.

## Not advice

Nothing in Gnomonkit's rules, skills or documents is legal, security, financial or other
professional advice. They describe working habits that suit one way of working with coding agents;
decide for yourself whether they suit yours.
