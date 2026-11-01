"""Core's lessons register: adding with near-duplicate checks, import, candidates and matching.

Synthetic data only: the register, the profile and the history index live under pytest's tmp_path.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import _history as H  # noqa: E402
from gnomonkit_cmds import _lessons as L  # noqa: E402


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("CLAUDE_PLUGIN_OPTION_") or k.startswith("GNOMONKIT_") or k == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(tmp_path / "nowhere" / "profile.toml"))
    # memory.lessons and memory.history are auto: a MemPalace on the developer's own PATH would turn them
    # off. The auto rule has its own tests (test_memory_backend.py), on a made-up PATH and config.
    monkeypatch.setattr(C, "mempalace_status", lambda cwd=None, refresh=False: {"installed": False, "mcp": False,
                                                                                "where": []})
    C._CACHE.clear()
    yield tmp_path
    C._CACHE.clear()


def cli(capsys, *argv):
    rc = gnomonkit.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


PIN = "Pin the toolchain version in the build file before upgrading any dependency."


# ---- add and near-duplicates ---------------------------------------------------------------------
def test_add_finds_a_near_duplicate_and_says_what_to_do():
    items = []
    it, how = L.add(items, PIN, source="s1")
    assert (how, L.label(it), it["n"]) == ("added", "L1", 1)
    with pytest.raises(L.NearDuplicate) as e:
        L.add(items, "Pin the toolchain version in the build file before upgrading a dependency.", source="s2")
    assert L.label(e.value.matches[0][1]) == "L1"
    it, how = L.add(items, "anything", source="s2", same_as="L1")
    assert (how, it["n"], it["sources"]) == ("merged", 2, ["s1", "s2"])
    it, _ = L.add(items, "anything", source="s2", same_as="L1")
    assert it["n"] == 2                                       # the same session is not counted twice
    other, how = L.add(items, "Pin the toolchain version in the build file before upgrading the runtime.", new=True)
    assert (how, other["id"]) == ("added", 2)


def test_add_through_the_command(capsys):
    rc, out, _ = cli(capsys, "lessons", "add", PIN, "--source", "s1")
    assert rc == 0 and "added L1" in out
    rc, _, err = cli(capsys, "lessons", "add", PIN.replace("any", "a"), "--source", "s2")
    assert rc == 2 and "--same-as L1" in err and "nothing was written" in err
    assert len(L.load()) == 1
    rc, out, _ = cli(capsys, "lessons", "add", PIN, "--source", "s2", "--same-as", "L1")
    assert rc == 0 and "2 sessions" in out
    rc, out, _ = cli(capsys, "lessons", "add", "Keep release notes short.", "--entry", "--pin", "--scope", "repo:demo-app")
    assert rc == 0 and "added E2" in out
    rc, out, _ = cli(capsys, "lessons", "list", "--json")
    d = json.loads(out)
    assert [i["n"] for i in d["items"]] == [2, 1] and d["where"] == "default location"
    rc, out, _ = cli(capsys, "lessons", "retire", "L1")
    assert rc == 0 and len(json.loads(cli(capsys, "lessons", "list", "--json")[1])["items"]) == 1
    rc, _, err = cli(capsys, "lessons", "add", PIN, "--scope", "nowhere")
    assert rc == 2 and "--scope" in err


def test_text_is_scrubbed_and_checked():
    tok = "ghp_" + "Ab3" * 12
    assert tok not in L.check_text("use token " + tok + " for the release")
    with pytest.raises(ValueError):
        L.check_text("   ")
    with pytest.raises(ValueError):
        L.check_text("x" * (L.TEXT_MAX + 1))
    with pytest.raises(ValueError):
        L.check_text("bell\x07char")


def test_a_corrupt_register_is_never_overwritten(capsys):
    path = L.register_path()[0]
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(L.RegisterError):
        L.load()
    assert L.load(strict=False) == []
    rc, _, err = cli(capsys, "lessons", "add", PIN)
    assert rc == 2 and "nothing was written" in err
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == "{not json"


# ---- where the register lives, and off ---------------------------------------------------------
def test_register_path_precedence(tmp_path, monkeypatch):
    assert L.register_path() == (os.path.join(str(tmp_path / "gk"), "lessons", "register.json"), "default location")
    ov = tmp_path / "overlay"
    (ov / "profiles").mkdir(parents=True)
    (ov / "gnomonkit-overlay.toml").write_text("", encoding="utf-8")
    prof = ov / "profiles" / "me.toml"
    prof.write_text("", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(prof))
    C._CACHE.clear()
    assert L.register_path() == (os.path.join(str(ov), "lessons", "register.json"), "overlay")
    monkeypatch.setenv("GNOMONKIT_OVERLAY", str(tmp_path / "other"))
    assert L.register_path()[0] == os.path.join(str(tmp_path / "other"), "lessons", "register.json")
    monkeypatch.setenv("GNOMONKIT_LESSONS_REGISTER", str(tmp_path / "r.json"))
    assert L.register_path() == (str(tmp_path / "r.json"), "GNOMONKIT_LESSONS_REGISTER")


def test_lessons_off_by_env_or_profile(tmp_path, monkeypatch):
    assert L.lessons_off() is False
    monkeypatch.setenv("GNOMONKIT_LESSONS", "off")
    assert L.lessons_off() is True
    monkeypatch.delenv("GNOMONKIT_LESSONS")
    prof = tmp_path / "profile.toml"
    prof.write_text('[memory]\nlessons = "off"\n', encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(prof))
    C._CACHE.clear()
    assert L.lessons_off() is True


# ---- import --------------------------------------------------------------------------------------
def test_import_json_jsonl_and_a_lessons_dict(tmp_path, capsys):
    items = []
    L.add(items, PIN, source="s1")
    a = tmp_path / "a.json"
    a.write_text(json.dumps([
        {"text": PIN.replace("any", "a"), "sources": ["s2", "s3"]},                 # merges into L1
        {"lesson": "Write the changelog entry in the same pull request.", "scope": "global", "count": 4},
        {"text": "An idea that was turned down.", "status": "rejected"},
        {"text": "An idea that was retired.", "retired": True},
        {"no_text": "here"},
    ]), encoding="utf-8")
    assert L.import_items(items, str(a)) == {"added": 1, "merged": 1, "skipped": 3, "unpinned": 0}
    assert items[0]["n"] == 3 and items[0]["sources"] == ["s1", "s2", "s3"]
    assert (items[1]["by"], items[1]["n"], items[1]["scope"]) == ("import", 4, "everywhere")
    b = tmp_path / "b.jsonl"
    b.write_text(json.dumps({"rule": "Run the formatter before each commit.", "kind": "rule", "scope": "project:Demo App"})
                 + "\n\n" + json.dumps({"content": "Check the lock file into version control."}) + "\n", encoding="utf-8")
    assert L.import_items(items, str(b)) == {"added": 2, "merged": 0, "skipped": 0, "unpinned": 0}
    assert (items[2]["kind"], items[2]["scope"]) == ("entry", "repo:Demo-App")
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"lessons": [{"text": "Prefer small pull requests over large ones."}]}), encoding="utf-8")
    rc, out, _ = cli(capsys, "lessons", "import", str(c), "--dry-run")
    assert rc == 0 and "would import: 1 new" in out and L.load() == []
    rc, out, _ = cli(capsys, "lessons", "import", str(c))
    assert rc == 0 and len(L.load()) == 1


def test_imported_pins_wait_for_review_unless_kept(tmp_path, capsys):
    f = tmp_path / "pinned.json"
    f.write_text(json.dumps([{"text": "Always deploy on a Friday afternoon.", "pinned": True},
                             {"text": "Keep the docs folder in step with the code.", "pinned": "yes"}]),
                 encoding="utf-8")
    items = []
    assert L.import_items(items, str(f)) == {"added": 2, "merged": 0, "skipped": 0, "unpinned": 1}
    assert [i["pinned"] for i in items] == [False, False]
    assert L.always_load(items, "demo-app") == []          # nothing imported reaches a session unread
    items = []
    L.import_items(items, str(f), keep_pins=True)
    assert [i["pinned"] for i in items] == [True, False]   # only a real true pins
    rc, out, _ = cli(capsys, "lessons", "import", str(f))
    assert rc == 0 and "1 came pinned in the file and came in unpinned" in out and "--keep-pins" in out
    assert not any(i["pinned"] for i in L.load())
    rc, out, _ = cli(capsys, "lessons", "import", str(f), "--keep-pins")
    assert rc == 0 and "unpinned" not in out


# ---- matching ------------------------------------------------------------------------------------
def _register():
    items = []
    for text, scope in (
            (PIN, "everywhere"),
            ("Run the database migrations against a copy before production.", "repo:demo-app"),
            ("The release script needs the signing key loaded in the agent.", "repo:other-app"),
            ("Write the changelog entry in the same pull request.", "everywhere"),
            ("Keep session_profile.py free of network calls.", "everywhere")):
        L.add(items, text, scope=scope, new=True)
    return items


def test_prompt_matching_gates():
    items = _register()
    hits = L.match_prompt(items, "please upgrade the toolchain version and fix the build", "demo-app")
    assert [L.label(i) for _, i in hits] == ["L1"]
    assert L.match_prompt(items, "the toolchain is weird today", "demo-app") == []     # one shared term
    assert L.match_prompt(items, "load the signing key for the release script", "demo-app") == []  # other repo
    assert [L.label(i) for _, i in L.match_prompt(items, "load the signing key for the release script", "other-app")] == ["L3"]
    assert L.match_prompt(items, "please upgrade the toolchain version and fix the build", "demo-app", shown=[1]) == []


def test_this_repo_is_boosted_and_weak_matches_drop():
    items = _register()
    L.add(items, "Run the test migrations on a copy of the data first.", new=True)
    hits = L.match_prompt(items, "run the database migrations on a copy first", "demo-app")
    assert L.label(hits[0][1]) == "L2"
    assert all(s >= L.WITHIN_TOP * hits[0][0] for s, _ in hits)


def test_short_and_acknowledgement_prompts_are_skipped():
    for p in ("ok", "thanks!", "yes please", "/commit", "", "fix it"):
        assert L.skip_prompt(p), p
    assert not L.skip_prompt("upgrade the toolchain version now")


def test_always_load_and_file_matching():
    items = _register()
    L.add(items, "anything", source="a", same_as="L4")
    L.add(items, "anything", source="b", same_as="L4")
    items[0]["pinned"] = True
    assert [L.label(i) for i in L.always_load(items, "demo-app")] == ["L1", "L4"]
    for n in range(12):
        it, _ = L.add(items, "Lesson number %d about widget %d." % (n, n * 7), new=True, pinned=True)
    assert len(L.always_load(items, "demo-app")) == L.ALWAYS_MAX
    assert [L.label(i) for i in L.match_file(items, "C:/x/hooks/session_profile.py", "demo-app")] == ["L5"]
    assert L.match_file(items, "C:/x/profile.py", "demo-app") == []
    assert L.match_file(items, "C:/x/Makefile", "demo-app") == []
    assert L.tag(items[1], "demo-app") == "[L2 · 1 session · this repo]"


# ---- the hook's token index ----------------------------------------------------------------------
PROMPTS = ("please upgrade the toolchain version and fix the build", "load the signing key for the release script",
           "run the database migrations on a copy first", "write the changelog entry in this pull request")


def test_the_index_save_writes_matches_what_a_scan_finds(tmp_path):
    items = _register()
    L.add(items, "Run the test migrations on a copy of the data first.", new=True)
    items[3]["retired"] = True
    path = str(tmp_path / "register.json")
    L.save(items, path)
    assert os.path.isfile(L.index_path(path))
    got, idx = L.load_for_hook(path)
    assert idx is not None and [i["id"] for i in got] == [i["id"] for i in items if not i["retired"]]
    for repo in ("demo-app", "other-app", ""):
        for p in PROMPTS:
            assert [(round(s, 6), i["id"]) for s, i in L.match_prompt(got, p, repo, index=idx)] == \
                   [(round(s, 6), i["id"]) for s, i in L.match_prompt(items, p, repo)], (repo, p)
        assert L.match_file(got, "x/session_profile.py", repo, index=idx) == L.match_file(items, "x/session_profile.py", repo)


def test_a_register_changed_by_hand_is_indexed_again_once(tmp_path):
    path = tmp_path / "register.json"
    L.save(_register(), str(path))
    body = json.loads(path.read_text(encoding="utf-8"))
    body["items"].append({"id": 9, "text": "Tag every release with the version from the build file.", "n": 2})
    path.write_text(json.dumps(body), encoding="utf-8")       # an overlay pull, or a hand edit
    got, idx = L.load_for_hook(str(path))
    assert 9 in [i["id"] for i in got] and idx["stamp"] == L._stamp(str(path))
    with open(L.index_path(str(path)), encoding="utf-8") as fh:
        assert json.load(fh)["stamp"] == L._stamp(str(path))   # written back: the next prompt reads it
    hits = L.match_prompt(got, "tag the release with the build version", "demo-app", index=idx)
    assert [i["id"] for _, i in hits] == [9]
    path.write_text("{ not json", encoding="utf-8")
    assert L.load_for_hook(str(path)) == ([], None)            # a broken register: nothing, never an error


def test_a_large_register_is_matched_on_the_items_that_count_most(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SCAN_MAX", 3)
    raw = [{"id": 1, "text": "Pin the toolchain before any upgrade.", "pinned": True},
           {"id": 2, "text": "Old toolchain advice nobody repeated.", "n": 1},
           {"id": 3, "text": "Toolchain lesson from many sessions.", "n": 5},
           {"id": 4, "text": "A standing toolchain entry.", "kind": "entry"},
           {"id": 5, "text": "Newest toolchain lesson.", "n": 1},
           {"id": 6, "text": "Retired toolchain lesson.", "n": 9, "retired": True}]
    path = tmp_path / "register.json"
    path.write_text(json.dumps({"schema": 1, "items": raw}), encoding="utf-8")
    got, idx = L.load_for_hook(str(path))
    assert [i["id"] for i in got] == [1, 3, 4]                 # pinned, the entry, then the most sessions
    assert [i["id"] for i in L.build_index(L.load(str(path)), str(path))["items"]] == [1, 3, 4]
    assert [i["id"] for i in L._first([i for i in L.load(str(path)) if not i["retired"]])] == [1, 3, 4]


# ---- candidates ----------------------------------------------------------------------------------
def test_candidates_group_corrections_across_sessions(tmp_path, capsys):
    cwd = tmp_path / "work" / "demo-app"
    cwd.mkdir(parents=True)
    lines = []
    for n, (sid, text) in enumerate((
            ("s-one-0001", "stop asking me before running the tests, just run them"),
            ("s-two-0002", "stop asking me before running the tests"),
            ("s-two-0002", "add a footer to the page"),
            ("s-three-03", "from now on put the changelog entry in the same pull request")), 1):
        lines.append(json.dumps({"type": "user", "uuid": "u%04d" % n, "sessionId": sid, "cwd": str(cwd),
                                 "timestamp": "2026-02-%02dT10:00:00Z" % n,
                                 "message": {"role": "user", "content": text}}) + "\n")
    proj = tmp_path / "claude" / "projects" / "-work-demo-app"
    proj.mkdir(parents=True)
    (proj / "all.jsonl").write_text("".join(lines), encoding="utf-8")
    H.run_index()
    L.save([dict(L.normalise({"id": 1, "text": "Run the tests without asking first."}))])
    rc, out, _ = cli(capsys, "lessons", "candidates", "--json")
    groups = json.loads(out)["groups"]
    assert rc == 0 and [(g["sessions"], g["count"]) for g in groups] == [(2, 2), (1, 1)]
    assert groups[0]["examples"][0].startswith("stop asking me") and groups[0]["projects"] == ["demo-app"]
    rc, out, _ = cli(capsys, "lessons", "candidates")
    assert rc == 0 and "1. 2 sessions, 2 messages" in out and "lessons add" in out


def test_candidates_without_an_index(capsys):
    rc, _, err = cli(capsys, "lessons", "candidates")
    assert rc == 2 and "no history index yet" in err
