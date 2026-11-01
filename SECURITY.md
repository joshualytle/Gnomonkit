# Security policy

Gnomonkit runs hooks inside your coding agent's sessions and can block or allow commands, so a flaw
in it can matter. Thank you for reporting one privately.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: on this repository, open **Security → Report a
vulnerability**. Please do not open a public issue or pull request for a security problem, and do
not include real credentials, transcripts or personal data in the report; a minimal synthetic
reproduction is best.

You should get an acknowledgement within a week. A fix may take longer, and no fix time is
promised: Gnomonkit has one maintainer. Fixes ship in a release, and the advisory credits you
unless you ask otherwise.

The safety hooks are heuristics: they reduce some risks and miss others, and they are not a
sandbox or a security boundary. Gnomonkit is provided as is, without warranty; see
[DISCLAIMER.md](DISCLAIMER.md).

## In scope

- A hook that can be made to run code, leak data, or stay silent when it should block (for example,
  a way past the destructive-command interlock).
- A script that sends data off the machine when its documentation says it doesn't.
- The leak gates (`scripts/leakcheck.py`, `scripts/provenance_check.py`) missing a class of secret or
  private text they claim to catch.
- The supply-chain reviewer executing, importing or extracting package contents it only reads.

## Out of scope

- Rules or skills you disagree with: open an issue.
- Problems in Claude Code itself, MemPalace or other tools Gnomonkit works with: report those to
  their maintainers.

## Supported versions

Only the latest release of each plugin gets fixes. Update with `claude plugin marketplace update
gnomonkit`, then `claude plugin update <plugin>@gnomonkit`.
