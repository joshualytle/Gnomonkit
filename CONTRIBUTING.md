# Contributing

Gnomonkit is open source (MIT) and maintained by one person. You're free to use it, fork it and change
your copy. It does **not** take outside code: pull requests are limited to collaborators, and the
public repository only receives signed release commits built from a private development line.

## Ways to help

- **Report a bug** or an unclear rule as an issue. Say which plugin and version (`claude plugin list`),
  what you expected, and what happened. Leave out anything private: names, hosts, paths, transcripts.
- **Suggest an idea** as an issue. If it's accepted, it's written in the development line and ships in
  a later release, and you're credited in that release's notes.
- **Security problems** go through private reporting, never a public issue. See [SECURITY.md](SECURITY.md).

There's no promise of a reply time or of a fix: Gnomonkit is provided as is, without warranty (see
[LICENSE](LICENSE) and the plain-language [DISCLAIMER.md](DISCLAIMER.md)).

## Why no outside pull requests

- **Nothing private gets in.** Every line comes from the maintainer's development line through the same
  leak gates, so nobody else's data can enter the code or its history.
- **Hooks run code on your machine.** Every release is signed by the maintainer, and every change in it
  is one the maintainer wrote or reviewed line by line.
- **One maintainer** can't review and support outside code at a pace that's fair to contributors.

## For collaborators

If you've been invited as a collaborator, the development rules apply:

- **Nothing private.** No personal or company names, products, hosts, IP addresses, file paths from
  your machine, account IDs, transcripts or session data. If a rule needs something specific to one
  setup, make it a setting the plugin reads instead of a literal.
- **Synthetic test data only.** Build private-looking strings from fragments at runtime so scanners
  don't trip on the test itself.
- **State rules generally.** Explain the rule and why it matters, with invented examples.
- **Bump the plugin version** in `plugins/<plugin>/.claude-plugin/plugin.json` when you change that
  plugin's hooks or skills; a test enforces it.
- **Run the checks locally:** `python -m pytest -q` and `python scripts/leakcheck.py --all`. The same
  gates, plus gitleaks, run on every pull request. See [docs/LEAK-GATES.md](docs/LEAK-GATES.md).

A skill is `plugins/<plugin>/skills/<name>/SKILL.md` with a `description:` naming the situations it
applies to. Lead with the rule and give checks an agent can actually run. To fire on a file type or a
command, add a pattern to the plugin's `hooks/triggers.yaml` with fixtures that must fire and fixtures
that must stay quiet.
