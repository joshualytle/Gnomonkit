"""Hit-rate review: list guesses paired with the user's replies, and write an HTML page to mark them."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "interjector"))
sys.path.insert(0, str(ROOT / "plugins" / "interjector" / "hooks"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path))
    import importlib
    import ij_store
    importlib.reload(ij_store)
    import interjector.__main__ as cli
    importlib.reload(cli)
    return ij_store, cli


def _pair(store, sid, gid, q, matches, reply=None):
    store.log_guess(sid, {"kind": "guess", "id": gid, "prompt_id": "p", "where": "Stop", "question": q,
                          "matches": matches})
    if reply is not None:
        store.log_guess(sid, {"kind": "reply", "answers": gid, "text": reply})


def test_guesses_lists_pairs_and_counts(env, capsys):
    store, cli = env
    _pair(store, "s1", "g1", "Which region?", "eu-west-2", "yes eu-west-2")
    _pair(store, "s1", "g2", "Which shell?", "PowerShell")
    assert cli.main(["guesses"]) == 0
    out = capsys.readouterr().out
    assert "2 guesses" in out and "1 answered" in out and "Which region?" in out and "yes eu-west-2" in out


def test_html_review_page_has_a_verdict_per_answered_guess(env, tmp_path):
    store, cli = env
    _pair(store, "s1", "g1", "Which region?", "eu-west-2", "yes eu-west-2")
    _pair(store, "s1", "g2", "Which <b>shell</b>?", "PowerShell", "no, bash")
    out = tmp_path / "review.html"
    assert cli.main(["guesses", "--html", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert html.count('class="verdict"') == 2 and 'data-key="g1"' in html and 'data-key="g2"' in html
    assert "Which &lt;b&gt;shell&lt;/b&gt;?" in html and "<b>shell</b>" not in html     # escaped
    assert "<script src=" not in html and "Right" in html and "Wrong" in html
