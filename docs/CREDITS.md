# Credits

Gnomonkit builds on other people's work. This page lists what it draws on, what it works with, and
what it was built with, with each project's licence. None of these projects makes, endorses or
supports Gnomonkit (see [DISCLAIMER.md](../DISCLAIMER.md)).

## Ideas Gnomonkit builds on

- **Interpretable Context Methodology** (ICM). Jake Van Clief and David McDermott,
  [*Interpretable Context Methodology: Folder Structure as Agentic Architecture*](https://arxiv.org/abs/2603.16021),
  licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Core's `context-routing` skill
  restates the method as working instructions in its own words; no text from the paper is copied.
  The [icm-architect](https://github.com/RinDig/icm-architect) skill (MIT) applies the same method.
- **[everything-claude-code](https://github.com/affaan-m/everything-claude-code)** (MIT). The
  always-on overhead section of the `token-efficiency` skill's reference adapts the ideas behind its
  `context-budget` and `strategic-compact` as a habit rather than a script.

## Tools Gnomonkit works with

- **[Claude Code](https://docs.claude.com/en/docs/claude-code)**, by Anthropic. Gnomonkit is a set
  of Claude Code plugins; its hooks, skills and dispatch run there.
- **[MemPalace](https://github.com/MemPalace/mempalace)** (MIT), an optional semantic history store.
  Core detects it and steps its own history index back ([HISTORY.md](HISTORY.md)).
- **[Ollama](https://github.com/ollama/ollama)** (MIT), an optional local model server for the
  interjector's orchestrator and the supply-chain reviewer's summary. Only loopback addresses are
  accepted.
- **[gitleaks](https://github.com/gitleaks/gitleaks)** (MIT), one of the leak gates every commit and
  release passes ([LEAK-GATES.md](LEAK-GATES.md)).

## Built with

- **[superpowers](https://github.com/obra/superpowers)** (MIT), the skill library used to plan and
  build much of Gnomonkit. It is not shipped or required.

Missing a credit, or got one wrong? Open an issue.
