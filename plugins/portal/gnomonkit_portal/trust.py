"""The Trust tab: folder-trust levels per root (docs/specs/2026-10-27-portal-trust-design.md).

READ. read() runs core's `gnomonkit trust status --json` as one short process: every [[trust]] root with
its level, the repositories under each with their state in Claude Code's file, and the folders Claude
Code trusts that no root covers. The portal parses no TOML and never opens Claude Code's file itself.
view.trust_view() keeps a whitelist of that output, with the home folder shown as ~.

READ-ONLY. Nothing here writes the grants file or Claude Code's file: changes go through
`gnomonkit trust sync` and `gnomonkit setup security` in a terminal. The server serves the view in plain
loopback mode only (allowed); a snapshot carries it only when asked (`snapshot --trust`).
"""
from __future__ import annotations

import json
import subprocess

from . import updates

CACHE_S = 30
TIMEOUT_S = 10


def allowed(server_mode) -> bool:
    """Whether /api/trust exists. Only plain loopback: the view names this machine's folders, and on a
    server the portal's account is not the developer's, so its answer would be about the wrong machine."""
    return server_mode == "loopback"


def read(runner=None) -> dict | None:
    """Core's `trust status --json`, parsed; None when no core is found, the run fails or times out, or
    its output is not a JSON object. A levels file with problems still prints its JSON (exit 1)."""
    try:
        r = updates.core(["trust", "status", "--json"], runner, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    if r is None:
        return None
    try:
        data = json.loads(r.stdout or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
