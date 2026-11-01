"""Arms: what loads for a run. The rule text is identical across `written` and `gnomonkit`; only the
delivery differs (skills that wait to be found, versus skills plus the hooks that make them fire)."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Arm:
    name: str
    plugin_dirs: tuple[Path, ...]
    plant_claude_md: bool


def _written_plugin(core_dir: Path, work: Path) -> Path:
    """Core's skills with no hooks: the rule exists and the model can find it, nothing pushes it."""
    dest = work / "written-plugin"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(core_dir / "skills", dest / "skills")
    (dest / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "written", "version": "0.0.0",
                "description": "Gnomonkit Bench control arm: core's skills, no hooks."}
    (dest / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return dest


def build_arms(core_dir: Path, work: Path, custom: tuple[Path, ...] = ()) -> dict[str, Arm]:
    work.mkdir(parents=True, exist_ok=True)
    arms = {
        "bare": Arm("bare", (), False),
        "written": Arm("written", (_written_plugin(core_dir, work),), True),
        # Full Gnomonkit: core first, then every other plugin in the same folder (e.g. the interjector).
        "gnomonkit": Arm("gnomonkit", (core_dir,) + tuple(
            sorted(p for p in core_dir.parent.iterdir()
                   if p != core_dir and (p / ".claude-plugin" / "plugin.json").is_file())), True),
    }
    if custom:
        arms["custom"] = Arm("custom", tuple(custom), True)
    return arms
