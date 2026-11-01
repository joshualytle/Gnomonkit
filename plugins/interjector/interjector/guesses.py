"""Predict-then-confirm hit rate: guesses paired with the user's replies.

The reply is the user's next prompt after a guess. Whether the guess was right is the user's call, so
the HTML page asks for it: Right / Wrong per guess, saved in the browser, copied out as a block to
paste back, and stored with the page when it is published as a claude.ai artifact with a database.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import ij_store as S  # noqa: E402


def collect() -> list[dict]:
    pairs = []
    root = S.home()
    if not root.is_dir():
        return pairs
    for d in sorted(root.iterdir()):
        path = d / "guesses.jsonl"
        if not path.is_file():
            continue
        guesses, replies = {}, {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "guess" and r.get("id"):
                guesses[r["id"]] = r
            elif r.get("kind") == "reply" and r.get("answers"):
                replies[r["answers"]] = r
        for gid, g in guesses.items():
            pairs.append({"id": gid, "session": d.name, "t": g.get("t"), "question": g.get("question", ""),
                          "matches": g.get("matches", ""), "reply": (replies.get(gid) or {}).get("text")})
    return sorted(pairs, key=lambda p: p.get("t") or 0)


def summary(pairs: list[dict]) -> str:
    answered = sum(1 for p in pairs if p["reply"] is not None)
    lines = [f"{len(pairs)} guesses, {answered} answered"]
    for p in pairs:
        reply = p["reply"] if p["reply"] is not None else "(no reply yet)"
        lines.append(f"- {p['question'][:100]}\n    history: {p['matches'][:120]!r}\n    you said: {reply[:120]}")
    return "\n".join(lines)


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Interjector Guess Review</title>
<style>
:root{{--bg:#eef2f1;--card:#fff;--ink:#142023;--muted:#56676a;--rule:#d2dbda;--teal:#139c8c;--go:#2f7d4f;--no:#b23a3a}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#0f1518;--card:#172024;--ink:#e9efee;--muted:#9db0ae;
--rule:#2a3a3d;--teal:#1ec8b4;--go:#5cc488;--no:#ef7b7b;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#0f1518;--card:#172024;--ink:#e9efee;--muted:#9db0ae;--rule:#2a3a3d;--teal:#1ec8b4;
--go:#5cc488;--no:#ef7b7b;color-scheme:dark}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,"Segoe UI",sans-serif}}
.wrap{{max-width:44rem;margin:0 auto;padding-block:24px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px))}}
h1{{font-size:1.5rem;margin:0 0 6px}} .muted{{color:var(--muted)}}
section{{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:14px 16px;margin:12px 0;display:grid;gap:6px}}
.k{{font:600 .7rem/1 ui-monospace,Consolas,monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}
pre{{white-space:pre-wrap;margin:0;font:.85rem/1.4 ui-monospace,Consolas,monospace}}
.seg{{display:flex;gap:8px;flex-wrap:wrap}} .seg button,#copy{{font:600 .85rem/1 system-ui;padding:10px 16px;border-radius:8px;
border:1px solid var(--rule);background:transparent;color:var(--ink);min-height:40px;cursor:pointer}}
.seg button[data-v="right"][aria-pressed="true"]{{background:var(--go);border-color:var(--go);color:var(--card)}}
.seg button[data-v="wrong"][aria-pressed="true"]{{background:var(--no);border-color:var(--no);color:var(--card)}}
textarea{{width:100%;box-sizing:border-box;min-height:48px;font:inherit;color:var(--ink);background:var(--bg);
border:1px solid var(--rule);border-radius:8px;padding:6px 8px}}
</style></head><body><div class="wrap">
<h1>Was the guess right?</h1>
<p class="muted">{n} guesses with your reply. History said one thing; you answered. Mark each Right or Wrong
so the hit rate is your judgement, not a heuristic. <span id="score"></span></p>
{sections}
<section><button id="copy" type="button">Copy answers</button>
<textarea id="answers" aria-label="Answers to paste back" readonly></textarea></section>
</div><script>
(function(){{var KEY='interjector-guess-review';var s={{}},db=null;
try{{s=JSON.parse(localStorage.getItem(KEY)||'{{}}')||{{}}}}catch(e){{s={{}}}}
function keep(){{try{{localStorage.setItem(KEY,JSON.stringify(s))}}catch(e){{}}}}
var boxes=[].slice.call(document.querySelectorAll('.verdict'));
boxes.forEach(function(box){{var id=box.dataset.key;var seg=document.createElement('div');seg.className='seg';
[['right','Right'],['wrong','Wrong']].forEach(function(o){{var b=document.createElement('button');b.type='button';
b.dataset.v=o[0];b.textContent=o[1];b.setAttribute('aria-pressed','false');b.addEventListener('click',function(){{
var x=s[id]||(s[id]={{}});x.verdict=x.verdict===o[0]?'':o[0];render();save(id)}});seg.appendChild(b)}});
var ta=document.createElement('textarea');ta.id='note-'+id;ta.placeholder='Note (optional)';
ta.addEventListener('input',function(){{var x=s[id]||(s[id]={{}});x.note=ta.value;keep();save(id)}});
box.appendChild(seg);box.appendChild(ta)}});
function render(){{var r=0,w=0;boxes.forEach(function(box){{var x=s[box.dataset.key]||{{}};if(x.verdict==='right')r++;
if(x.verdict==='wrong')w++;box.querySelectorAll('button').forEach(function(b){{b.setAttribute('aria-pressed',
String(b.dataset.v===x.verdict))}});var ta=box.querySelector('textarea');if(document.activeElement!==ta)ta.value=x.note||''}});
document.getElementById('score').textContent=(r+w)?('Marked so far: '+r+' right, '+w+' wrong.'):''}}
function save(id){{keep();if(!db)return;var x=s[id]||{{}};db.collection('verdicts').doc(id).set({{verdict:x.verdict||'',
note:x.note||'',updatedAt:new Date().toISOString()}}).catch(function(){{}})}}
document.getElementById('copy').addEventListener('click',function(){{var t=JSON.stringify({{'guess-verdicts':s}});
var box=document.getElementById('answers');box.value=t;box.select();try{{navigator.clipboard.writeText(t).catch(function(){{}})}}catch(e){{}}}});
render();if(window.claude&&typeof window.claude.use==='function'){{window.claude.use('db').then(function(d){{if(!d)return;db=d;
d.collection('verdicts').onSnapshot(function(snap){{snap.docs.forEach(function(doc){{var v=doc.data()||{{}};
s[doc.id]={{verdict:v.verdict||'',note:typeof v.note==='string'?v.note:''}}}});keep();render()}},function(){{}})}},function(){{}})}}}})();
</script></body></html>
"""


def render_html(pairs: list[dict], out: Path) -> Path:
    answered = [p for p in pairs if p["reply"] is not None]
    e = html.escape
    sections = "".join(
        f'<section><span class="k">Claude would have asked</span><div>{e(p["question"])}</div>'
        f'<span class="k">History said</span><pre>{e(p["matches"])}</pre>'
        f'<span class="k">You replied</span><div>{e(p["reply"])}</div>'
        f'<div class="verdict" data-key="{e(p["id"])}"></div></section>' for p in answered)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.format(n=len(answered), sections=sections), encoding="utf-8")
    return out
