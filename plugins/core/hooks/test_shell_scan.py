"""Fixtures for the shell word splitter (shell_scan.py) the grants guard reads commands with.

Each case is a command line and what the guard needs from it: the commands, their words with the quotes
removed, the commands nested in them, here-document bodies, and redirection targets. Synthetic commands only.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shell_scan as S  # noqa: E402

failures = []


def words(text, lang="bash"):
    return [[w.text for w in c.words] for c in S.commands(text, lang)]


def expect(name, got, want):
    if got != want:
        failures.append("%s: got %r, wanted %r" % (name, got, want))


expect("commands split on && and ;", words("cd /tmp/x && FOO=1 claude --bg 'do it'; echo done"),
       [["cd", "/tmp/x"], ["FOO=1", "claude", "--bg", "do it"], ["echo", "done"]])
expect("quotes inside a word are removed", words("cat gra''nts.toml"), [["cat", "grants.toml"]])
expect("double quotes keep spaces", words('echo "a b" c'), [["echo", "a b", "c"]])
expect("a pipeline is two commands", words("env -u X python x.py | tee out"),
       [["env", "-u", "X", "python", "x.py"], ["tee", "out"]])

c = S.commands("git commit -m \"$(cat <<'EOF'\nmessage text\nEOF\n)\"", "bash")
expect("$(...) in a word is a nested command", [w.subs for w in c[0].words][-1][0].startswith("cat <<'EOF'"), True)

c = S.commands("python - <<EOF\nprint(1)\nEOF", "bash")
expect("a here-document's body goes to its command", c[0].heredocs, [("print(1)", True)])
c = S.commands("python - <<'EOF'\nprint($x)\nEOF", "bash")
expect("a quoted delimiter doesn't expand", c[0].heredocs, [("print($x)", False)])

c = S.commands("echo hi > out.txt", "bash")
expect("a redirection target", [(w.text, w.role) for w in c[0].words], [("echo", "arg"), ("hi", "arg"),
                                                                       ("out.txt", "redir")])
c = S.commands("run x 2>&1 >log", "bash")
expect("a file descriptor is not an argument", [(w.text, w.role) for w in c[0].words],
       [("run", "arg"), ("x", "arg"), ("1", "redir"), ("log", "redir")])
expect("a spaced number stays an argument", words("echo 2 > f"), [["echo", "2", "f"]])
expect("script -c keeps its command as one word", words("script -qc 'gnomonkit grants list' /dev/null"),
       [["script", "-qc", "gnomonkit grants list", "/dev/null"]])

expect("PowerShell assignment", words("$env:FOO = ''", "ps"), [["$env:FOO", "=", ""]])
expect("PowerShell call operator", words('& "C:/a b/tool.exe" stop abc', "ps"), [["C:/a b/tool.exe", "stop", "abc"]])
expect("PowerShell backslashes are literal", words(r"Get-Content C:\x\y.txt", "ps"), [["Get-Content", r"C:\x\y.txt"]])
expect("cmd set with quotes", words('set "FOO=" && echo x', "cmd"), [["set", "FOO="], ["echo", "x"]])

# Nothing it is given makes it raise: the guard fails closed on an exception, which would block ordinary work.
for text in ("", "'unterminated", '"also', "$(", "a <<", "`", "echo $((1+2))", "x |", "&&", "\\"):
    for lang in S.LANGS:
        try:
            S.commands(text, lang)
        except Exception as e:  # noqa: BLE001
            failures.append("%r (%s) raised %r" % (text, lang, e))

if failures:
    print("\n".join("FAIL " + f for f in failures))
    sys.exit(1)
print("shell scan: all cases pass")
