"""Generate the Gnomonkit brand assets from one drawing.

    python assets/brand/build_brand.py

Writes badge-dark.svg / badge-light.svg (pure shapes, no fonts: safe anywhere, including a
GitHub README) and render/*.html pages that lay out the wordmark lockup and the 1280x640
social preview. Render those pages to PNG with any headless browser at 2x; the committed PNGs
were made that way. Every shape here was drawn for Gnomonkit: no stock art, no third-party marks.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))

THEMES = {
    "dark": {"bg": "#0f1518", "ring": "#1ec8b4", "ground": "#1b2a2e", "beard_line": "#2a3a3e"},
    "light": {"bg": "#ffffff", "ring": "#139c8c", "ground": "#dfe6e3", "beard_line": "#d3d9d6"},
}
FIXED = {
    "hat": "#e2382f", "hat_shade": "#b92a23", "skin": "#f3c8a2", "nose": "#f08a78", "eye": "#1d1a18",
    "beard": "#f8f6f0", "tunic": "#2f5f95", "belt": "#3b2a1e", "boot": "#5a3b26",
    "box": "#1fb5a4", "box_lid": "#168e81", "latch": "#f2c230", "steel": "#b9c3c7", "steel_dark": "#8a979c",
}


def gnome(c: dict) -> str:
    """The gnome, origin at his feet, 52 units wide and 96 tall before scaling."""
    return f"""
    <ellipse cx="-8" cy="0" rx="8.5" ry="4.5" fill="{c['boot']}"/>
    <ellipse cx="8" cy="0" rx="8.5" ry="4.5" fill="{c['boot']}"/>
    <path d="M-15 -2 Q-17 -30 0 -36 Q17 -30 15 -2 Z" fill="{c['tunic']}"/>
    <rect x="-15" y="-14" width="30" height="4" fill="{c['belt']}"/>
    <circle cx="-17" cy="-20" r="5" fill="{c['skin']}"/>
    <path d="M-14 -46 Q-16 -24 0 -10 Q16 -24 14 -46 Z" fill="{c['beard']}" stroke="{c['beard_line']}" stroke-width="0.8"/>
    <circle cx="0" cy="-52" r="12" fill="{c['skin']}"/>
    <circle cx="-5" cy="-54" r="1.8" fill="{c['eye']}"/>
    <circle cx="5" cy="-54" r="1.8" fill="{c['eye']}"/>
    <circle cx="0" cy="-49" r="4.4" fill="{c['nose']}"/>
    <path d="M-17 -58 L0 -94 L17 -58 Q0 -62 -17 -58 Z" fill="{c['hat']}"/>
    <path d="M0 -94 L17 -58 Q8 -60 4 -60 Z" fill="{c['hat_shade']}"/>"""


def wrench(c: dict) -> str:
    """Open-end wrench, grip at origin, head up. Notch is cut with the badge background."""
    return f"""
    <rect x="-3.2" y="-40" width="6.4" height="44" rx="3" fill="{c['steel']}"/>
    <circle cx="0" cy="-44" r="10" fill="{c['steel']}"/>
    <rect x="-3.6" y="-58" width="7.2" height="13" rx="1" fill="{c['bg']}"/>
    <rect x="-1" y="-36" width="2" height="34" rx="1" fill="{c['steel_dark']}"/>"""


def badge(theme: str) -> str:
    c = {**FIXED, **THEMES[theme]}
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="Gnomonkit: a garden gnome holding a wrench beside a toolbox">
  <circle cx="128" cy="128" r="120" fill="{c['bg']}" stroke="{c['ring']}" stroke-width="10"/>
  <ellipse cx="124" cy="200" rx="80" ry="15" fill="{c['ground']}"/>
  <g transform="translate(139 168) rotate(14)">{wrench(c)}
  </g>
  <g transform="translate(112 198) scale(1.55)">{gnome(c)}
    <circle cx="17" cy="-22" r="5" fill="{c['skin']}"/>
  </g>
  <path d="M170 166 v-9 a5 5 0 0 1 5 -5 h22 a5 5 0 0 1 5 5 v9" fill="none" stroke="{c['belt']}" stroke-width="5"/>
  <rect x="158" y="164" width="56" height="36" rx="5" fill="{c['box']}"/>
  <rect x="158" y="164" width="56" height="10" rx="3" fill="{c['box_lid']}"/>
  <rect x="180" y="172" width="12" height="9" rx="2" fill="{c['latch']}"/>
</svg>
"""


ICONS = {
    # memory: three stacked layers
    "memory": '<path d="M24 8 L42 17 L24 26 L6 17 Z" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>'
              '<path d="M6 25 L24 34 L42 25" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>'
              '<path d="M6 33 L24 42 L42 33" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>',
    # triggers: lightning bolt
    "triggers": '<path d="M27 4 L10 27 H22 L19 44 L38 19 H26 Z" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>',
    # guards: shield with check
    "guards": '<path d="M24 4 L40 10 V22 C40 33 33 40 24 44 C15 40 8 33 8 22 V10 Z" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>'
              '<path d="M16 24 L22 30 L32 18" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
}


def pillars() -> str:
    out = []
    for key, label in (("memory", "Memory"), ("triggers", "Triggers"), ("guards", "Guards")):
        out.append(f'<div class="pillar"><svg viewBox="0 0 48 48" aria-hidden="true">{ICONS[key]}</svg><span>{label}</span></div>')
    return '<span class="sep"></span>'.join(out)


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Sora:wght@400;700;800&display=swap">
<style>
html,body{{margin:0;background:{bg};}}
.stage{{width:{w}px;height:{h}px;box-sizing:border-box;display:flex;align-items:center;gap:{gap}px;padding:0 {pad}px;
  background:{bg};font-family:Sora,"Segoe UI",system-ui,sans-serif;color:{fg};}}
.badge{{width:{badge}px;height:{badge}px;flex:none}}
.text{{display:grid;gap:{tgap}px}}
.word{{font-weight:800;font-size:{word}px;letter-spacing:-0.035em;line-height:0.9}}
.word span{{color:{teal}}}
.tag{{font-weight:400;font-size:{tag}px;color:{muted};line-height:1.35}}
.tag .line{{display:block;white-space:nowrap}}
.pillars{{display:flex;align-items:center;gap:22px;color:{teal}}}
.pillar{{display:flex;align-items:center;gap:10px}}
.pillar svg{{width:34px;height:34px}}
.pillar span{{color:{fg};font-weight:700;font-size:17px;letter-spacing:0.14em;text-transform:uppercase}}
.sep{{width:1px;height:30px;background:{rule}}}
</style></head><body><div class="stage">
<img class="badge" src="../badge-{theme}.svg" alt="">
<div class="text"><div class="word">gnomon<span>kit</span></div>{extra}</div>
</div></body></html>
"""


def page(theme: str, w: int, h: int, social: bool) -> str:
    dark = theme == "dark"
    extra = ""
    if social:
        extra = ('<div class="tag"><span class="line">Memory, triggers and guards for Claude Code.</span>'
                 '<span class="line">The rules you already have, applied when they matter.</span></div>'
                 f'<div class="pillars">{pillars()}</div>')
    return PAGE.format(
        w=w, h=h, theme=theme, extra=extra,
        bg="#0f1518" if dark else "#ffffff", fg="#f4f7f6" if dark else "#121a1c",
        teal="#1ec8b4" if dark else "#139c8c", muted="#a9b8b6" if dark else "#4d5c5a",
        rule="#2c3a3d" if dark else "#d3d9d6",
        gap=56 if social else 28, pad=90 if social else 24, badge=360 if social else 150,
        tgap=26 if social else 0, word=124 if social else 84, tag=24)


def main() -> None:
    os.makedirs(os.path.join(HERE, "render"), exist_ok=True)
    for theme in THEMES:
        with open(os.path.join(HERE, f"badge-{theme}.svg"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(badge(theme))
        with open(os.path.join(HERE, "render", f"wordmark-{theme}.html"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(page(theme, 720, 200, social=False))
    with open(os.path.join(HERE, "render", "social-preview.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page("dark", 1280, 640, social=True))
    print("wrote badges and render pages in", HERE)


if __name__ == "__main__":
    main()
