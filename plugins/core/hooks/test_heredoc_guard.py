"""Known-positive and known-negative for the heredoc guard.

The first attempt tested this through printf and shell quoting, and the
known-positive came back exit 0 -- which could mean the guard is broken or the
test payload was mangled by the very escaping problem the guard exists for.
Payloads are built here as Python strings so what the hook receives is exact.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "heredoc_guard.py")

CASES = [
    ("backslash in a python heredoc", 2,
     "python - <<'PY'\nimport re\nre.compile(r\"\\[(Bash)\")\nPY\n"),
    ("triple quotes in a heredoc", 2,
     "python - <<'PY'\nt = '''x'''\nPY\n"),
    ("windows path inside a heredoc", 2,
     "cat > f.txt <<'EOF'\nC:\\Users\\x\nEOF\n"),
    ("prose-only heredoc (commit message)", 0,
     "git commit -q -F - <<'EOF'\nfeat: something\n\nBody text.\nEOF\n"),
    ("backslash OUTSIDE the heredoc is fine", 0,
     "ls C:\\Projects && cat <<'EOF'\nplain\nEOF\n"),
    ("override with a reason", 0,
     "cat <<'EOF' # heredoc-ok: verified byte-exact\nC:\\x\nEOF\n"),
    ("no heredoc at all", 0, "python C:\\x\\y.py --flag"),
    ("not a Bash call", 0, None),
    ("redirect after the opener on the same line", 2,
     "cat <<'EOF' > f.py\nprint('a\\tb')\nEOF\n"),
    ("dash opener closes on a tab-indented terminator", 2,
     "cat <<-EOF\n\tC:\\x\n\tEOF\n"),
    ("two heredocs on one line, the second has the backslash", 2,
     "paste <<A <<B\nplain\nA\nC:\\x\nB\n"),
    ("here-string is not a heredoc", 0, "cat <<<\"EOF\"\nC:\\x\nEOF\n"),
    ("unterminated opener is not a heredoc", 0, "cat <<EOF\nC:\\x\n"),
    ("shift operators and stream inserts", 0, "echo $((1<<4)) && printf 'a<<b c\\\\d'"),
]


def bounded_runtime():
    """Pathological input: many openers, none terminated, plus a long body. The single-regex
    version was quadratic here (seconds at tens of kilobytes); this must stay well under a second."""
    import importlib.util
    import time
    spec = importlib.util.spec_from_file_location("heredoc_guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    worst = [
        "".join(f"cat <<E{i}\nx = {i}\n" for i in range(20000)),           # ~300 KB, no terminators
        "cout << a << b << c\n" * 20000,                                   # stream inserts
        "cat <<EOF\n" + ("\n" + " " * 200) * 5000 + "\nEOX",               # blank-heavy, unterminated
        "python - <<PY\n" + "print(1)\n" * 50000 + "PY\n",                 # one large terminated body
    ]
    out = []
    for text in worst:
        t = time.perf_counter()
        list(mod.heredocs(text))
        out.append(time.perf_counter() - t)
    return out


def run(cmd):
    payload = ({"tool_name": "Bash", "tool_input": {"command": cmd}}
               if cmd is not None else {"tool_name": "Write", "tool_input": {}})
    p = subprocess.run([sys.executable, GUARD], input=json.dumps(payload),
                       capture_output=True, text=True)
    return p.returncode, p.stderr.strip()[:90]


fails = 0
for name, want, cmd in CASES:
    rc, msg = run(cmd)
    ok = rc == want
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'XX '} want {want} got {rc}  {name}")
    if not ok and msg:
        print(f"       {msg}")
times = bounded_runtime()
slow = max(times)
ok = slow < 0.5
fails += 0 if ok else 1
print(f"  {'ok ' if ok else 'XX '} pathological inputs, slowest {slow * 1000:.0f} ms (bound 500 ms)")
print(f"\n  {len(CASES) + 1 - fails}/{len(CASES) + 1}")
sys.exit(1 if fails else 0)
