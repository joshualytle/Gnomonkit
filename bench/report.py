"""Render a round as an HTML review page and as a README block.

Both are built from counts only (k/n, cost, time). No run text, stderr or hook output ever reaches
them, so a report can be shared without leaking what a run saw.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from pathlib import Path

from bench.aggregate import family_tally, floor_flags, fmt_kn, tally

FAMILY_TITLES = {
    "skills": ("Skills", "A core skill's situation comes up. Did the behaviour it teaches happen?"),
    "precedent": ("Precedent", "A project rule exists in writing. Did the run follow it without being told?"),
    "guards": ("Guards (enforcement)", "Enforcement, not triggering: a hook can block the risky step until it "
                                       "is earned. Read these as 'did the guard hold', not 'did the rule fire'."),
    "interjection": ("Interjection", "The answer the user already gave lives only in their history. Did the run "
                                     "use it, or ask with it as the guess, instead of asking cold?"),
}
FAMILY_ORDER = tuple(FAMILY_TITLES)
ARM_ORDER = ("bare", "written", "gnomonkit", "custom")


def as_of(results: dict, path: Path | None = None) -> str:
    """The date the round ran, not the date the report was rendered: results.json carries it from
    runner 1.1 on, older rounds are named after their start time, and anything else falls back to the
    file's own modification time."""
    started = results.get("started")
    if isinstance(started, str) and len(started) >= 10:
        return started[:10]
    if path is not None:
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})-\d{6}", Path(path).resolve().parent.name)
        if m:
            return "-".join(m.groups())
        if Path(path).exists():
            return datetime.fromtimestamp(Path(path).stat().st_mtime).date().isoformat()
    return date.today().isoformat()

STYLE = """
:root{--bg:#eef2f1;--card:#fff;--ink:#142023;--muted:#56676a;--rule:#d2dbda;--teal:#139c8c;
--go:#2f7d4f;--change:#a76a10;--no:#b23a3a;--display:Sora,"Segoe UI",system-ui,sans-serif;
--body:"Source Serif 4",Georgia,serif;--mono:"JetBrains Mono",ui-monospace,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1518;--card:#172024;--ink:#e9efee;
--muted:#9db0ae;--rule:#2a3a3d;--teal:#1ec8b4;--go:#5cc488;--change:#e0a640;--no:#ef7b7b;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0f1518;--card:#172024;--ink:#e9efee;--muted:#9db0ae;--rule:#2a3a3d;--teal:#1ec8b4;
--go:#5cc488;--change:#e0a640;--no:#ef7b7b;color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 var(--body)}
.wrap{max-width:46rem;margin:0 auto;padding-block:28px 64px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2{font-family:var(--display);line-height:1.15;margin:0;text-wrap:balance}h1{font-size:1.8rem}h2{font-size:1.2rem}
.eyebrow{font:600 .72rem/1 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--teal)}
.muted{color:var(--muted)}header{display:grid;gap:8px;margin-bottom:24px}
section{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:18px;display:grid;gap:12px;margin-bottom:16px}
.tablebox{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--rule)}
th{font:600 .7rem/1.2 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
td.n{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.verdict{border-top:1px dashed var(--rule);padding-top:12px;display:grid;gap:8px}
.seg{display:flex;flex-wrap:wrap;gap:8px}
.seg button,#copy{font:600 .85rem/1 var(--display);padding:10px 16px;border-radius:8px;cursor:pointer;background:transparent;
color:var(--ink);border:1px solid var(--rule);min-height:40px}
.seg button[data-v="go"][aria-pressed="true"]{background:var(--go);border-color:var(--go);color:var(--card)}
.seg button[data-v="change"][aria-pressed="true"]{background:var(--change);border-color:var(--change);color:var(--card)}
.seg button[data-v="no"][aria-pressed="true"]{background:var(--no);border-color:var(--no);color:var(--card)}
button:focus-visible,textarea:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
textarea{width:100%;box-sizing:border-box;min-height:56px;font:.95rem/1.4 var(--body);color:var(--ink);background:var(--bg);
border:1px solid var(--rule);border-radius:8px;padding:8px 10px}
"""

SCRIPT = """
(function(){var KEY='gnomonkit-bench-report';var s={},db=null;
try{s=JSON.parse(localStorage.getItem(KEY)||'{}')||{}}catch(e){s={}}
function keep(){try{localStorage.setItem(KEY,JSON.stringify(s))}catch(e){}}
var boxes=[].slice.call(document.querySelectorAll('.verdict'));
boxes.forEach(function(box){var id=box.dataset.key;var seg=document.createElement('div');seg.className='seg';
[['go','Go'],['change','Change'],['no','No']].forEach(function(o){var b=document.createElement('button');b.type='button';
b.dataset.v=o[0];b.textContent=o[1];b.setAttribute('aria-pressed','false');b.addEventListener('click',function(){
var x=s[id]||(s[id]={});x.verdict=x.verdict===o[0]?'':o[0];render();save(id)});seg.appendChild(b)});
var ta=document.createElement('textarea');ta.id='note-'+id;ta.placeholder='Note (optional)';
ta.addEventListener('input',function(){var x=s[id]||(s[id]={});x.note=ta.value;keep();save(id)});
box.appendChild(seg);box.appendChild(ta)});
function render(){boxes.forEach(function(box){var x=s[box.dataset.key]||{};box.querySelectorAll('button').forEach(function(b){
b.setAttribute('aria-pressed',String(b.dataset.v===x.verdict))});var ta=box.querySelector('textarea');
if(document.activeElement!==ta)ta.value=x.note||''})}
function save(id){keep();if(!db)return;var x=s[id]||{};db.collection('verdicts').doc(id).set({verdict:x.verdict||'',
note:x.note||'',updatedAt:new Date().toISOString()}).catch(function(){})}
document.getElementById('copy').addEventListener('click',function(){var t=JSON.stringify({'bench-verdicts':s});
var box=document.getElementById('answers');box.value=t;box.select();
try{navigator.clipboard.writeText(t).catch(function(){})}catch(e){}});
render();if(window.claude&&typeof window.claude.use==='function'){window.claude.use('db').then(function(d){
if(!d)return;db=d;d.collection('verdicts').onSnapshot(function(snap){snap.docs.forEach(function(doc){var v=doc.data()||{};
s[doc.id]={verdict:v.verdict||'',note:typeof v.note==='string'?v.note:''}});keep();render()},function(){})},function(){})}})();
"""


def _family_section(fam: str, records: list[dict], t: dict) -> str:
    title, blurb = FAMILY_TITLES.get(fam, (fam, ""))
    arms = [a for a in ARM_ORDER if any(r["arm"] == a and r["family"] == fam for r in records)]
    scenarios = sorted({r["scenario"] for r in records if r["family"] == fam})
    head = "".join(f"<th>{html.escape(a)}</th>" for a in arms)
    rows = []
    for sc in scenarios:
        cells = []
        for a in arms:
            c = t.get((fam, sc, a))
            txt = fmt_kn(c["k"], c["n"]) + (f" · {c['errors']} err" if c["errors"] else "") if c else "–"
            cells.append(f'<td class="n">{html.escape(txt)}</td>')
        rows.append(f"<tr><td>{html.escape(sc)}</td>{''.join(cells)}</tr>")
    return (f'<section data-key="{html.escape(fam)}"><span class="eyebrow">{html.escape(fam)}</span>'
            f"<h2>{html.escape(title)}</h2><p class=\"muted\">{html.escape(blurb)}</p>"
            f'<div class="tablebox"><table><thead><tr><th>Scenario</th>{head}</tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
            f'<div class="verdict" data-key="{html.escape(fam)}"></div></section>')


def render_html(results: dict, out: Path, when: str | None = None) -> Path:
    records = results.get("records") or []
    t = tally(records)
    fams = [f for f in FAMILY_ORDER if any(r["family"] == f for r in records)]
    errors = sum(1 for r in records if r.get("error"))
    flags = floor_flags(records)
    cost = sum(r.get("cost_usd") or 0 for r in records)
    secs = sum(r.get("secs") or 0 for r in records)
    summary = (f"{len(records)} runs · {errors} errored (excluded from counts) · ${cost:.2f} · "
               f"{secs / 60:.0f} min of run time · model {results.get('model', '?')} · billing {results.get('billing', '?')}")
    flag_html = (f"<p class=\"muted\">Too easy (plain Claude passed most runs): {html.escape(', '.join(flags))}</p>"
                 if flags else "")
    page = (f"<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            f"<title>Gnomonkit Bench Report</title>"
            f"<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;800&family="
            f"Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=JetBrains+Mono:wght@400;600&display=swap\">"
            f"<style>{STYLE}</style></head><body><div class=\"wrap\"><header>"
            f"<span class=\"eyebrow\">Gnomonkit Bench · {when or as_of(results)}</span>"
            f"<h1>Does the setup apply its own rules?</h1><p class=\"muted\">{html.escape(summary)}</p>{flag_html}</header>"
            + "".join(_family_section(f, records, t) for f in fams)
            + "<section><button id=\"copy\" type=\"button\">Copy answers</button>"
              "<textarea id=\"answers\" aria-label=\"Answers to paste back to Claude\" readonly></textarea></section>"
            + f"</div><script>{SCRIPT}</script></body></html>")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "report.html"
    path.write_text(page, encoding="utf-8")
    return path


def readme_block(results: dict, when: str | None = None) -> str:
    records = results.get("records") or []
    ft = family_tally(records)
    fams = [f for f in FAMILY_ORDER if any(k[0] == f for k in ft)]
    arms = [a for a in ARM_ORDER if any(k[1] == a for k in ft)]
    cost = results.get("spent_usd")
    if cost is None:
        cost = sum(r.get("cost_usd") or 0 for r in records)
    lines = [f"Gnomonkit Bench, {when or as_of(results)} · model `{results.get('model', '?')}` · billing "
             f"{results.get('billing', '?')} · total ${cost:.2f}. Positive scenarios only; errored runs excluded.",
             "", "| family | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for f in fams:
        cells = [fmt_kn(ft[(f, a)]["k"], ft[(f, a)]["n"]) if (f, a) in ft else "–" for a in arms]
        lines.append(f"| {f} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def load_results(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
