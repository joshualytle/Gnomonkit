"""Fixtures for the irreversible-action interlock.

Both directions are load-bearing and they fail differently:

  a MISSED destructive command is the very failure the gate exists to stop
  a FALSE block trains instant dismissal, and a gate people dismiss is worse
  than no gate at all

So the negative cases here are not filler. They are the reason the scope is the
owner's enumerated list rather than a general "looks dangerous" heuristic.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "destructive_interlock.py")

BLOCK = 2
ALLOW = 0

CASES = [
    # --- must BLOCK ---------------------------------------------------------
    ("zpool create on a disk", BLOCK, "zpool create -f tank /dev/sdb"),
    ("zpool destroy", BLOCK, "sudo zpool destroy tank"),
    ("wipefs", BLOCK, "wipefs -a /dev/sdc"),
    ("mkfs", BLOCK, "mkfs.ext4 /dev/sdd1"),
    ("dd to a device", BLOCK, "dd if=/dev/zero of=/dev/sdb bs=1M count=100"),
    ("delete a PVC", BLOCK, "kubectl delete pvc example-data -n example"),
    ("force delete a pod", BLOCK,
     "kubectl delete pod example-worker-abc --force --grace-period=0"),
    ("DROP TABLE", BLOCK, 'psql -c "DROP TABLE sessions;"'),
    ("restore over a live database", BLOCK, "psql exampledb < backup.sql"),
    ("rm -rf", BLOCK, "rm -rf /var/lib/app/data"),
    ("git push --force", BLOCK, "git push --force origin main"),
    ("recursive bucket delete", BLOCK, "aws s3 rm s3://backups/prod --recursive"),
    ("rclone purge", BLOCK, "rclone purge r2:backups/old"),
    ("lvremove", BLOCK, "lvremove /dev/vg0/data"),
    # A quoted argument that is itself a command still runs (issue #10 keeps these).
    ("bash -c runs its argument", BLOCK, "bash -c 'rm -rf /srv/app/data'"),
    ("powershell -Command runs its argument", BLOCK, 'powershell -Command "git push --force origin main"'),
    ("ssh runs its remote argument", BLOCK, "ssh host 'sudo zpool destroy tank'"),
    ("SQL passed to psql -c", BLOCK, 'psql -c "DROP DATABASE app"'),
    ("SQL passed to mysql -e", BLOCK, 'mysql -e "DROP DATABASE app"'),
    ("SQL passed to sqlite3", BLOCK, 'sqlite3 app.db "DROP TABLE t"'),
    ("SQL fed to psql by heredoc", BLOCK, "psql app <<'EOF'\nDROP TABLE users;\nEOF"),
    ("piped into rm", BLOCK, "echo y | rm -rf build/cache"),
    ("xargs runs rm", BLOCK, "grep -l old * | xargs rm -rf"),
    ("command substitution inside double quotes runs", BLOCK, 'echo "$(rm -rf /srv/x)"'),
    ("after &&", BLOCK, "cd /srv && rm -rf data"),
    ("behind sudo", BLOCK, "sudo rm -rf /srv/data"),
    ("a quote that closes before the verb", BLOCK, "echo 'done'; rm -rf /srv/data"),
    ("git's own unquoted flags", BLOCK, 'git commit -m "wip" && git push --force origin main'),
    ("echo output piped to a shell", BLOCK, "echo 'rm -rf /srv/data' | sh"),
    ("a heredoc script piped to a remote shell", BLOCK,
     "cat <<'SH' | ssh build-host bash\ncd /opt/app\nrm -rf releases/old\nSH"),
    ("a heredoc fed to psql, output trimmed", BLOCK,
     "cat <<'SQL' | psql app 2>&1 | grep -v NOTICE | tail -1\nDROP TABLE staging_rows;\nSQL"),
    ("a container command after an echo", BLOCK,
     "echo '--- step 3 ---'; docker exec -i store-1 lvremove -f vg0/cache"),

    # --- must ALLOW ---------------------------------------------------------
    ("a graceful pod delete", ALLOW, "kubectl delete pod web-1"),
    ("reading pool state", ALLOW, "zpool list; zpool status tank"),
    ("listing a bucket", ALLOW, "aws s3 ls s3://backups/prod"),
    ("a normal rm", ALLOW, "rm build/output.txt"),
    ("force-with-lease is not force", ALLOW,
     "git push --force-with-lease origin feature"),
    ("SELECT is not DROP", ALLOW, 'psql -c "SELECT count(*) FROM sessions;"'),
    ("grepping FOR the pattern is not the pattern", ALLOW,
     "grep -rn 'zpool destroy' docs/"),
    ("a comment mentioning it", ALLOW, "# never run wipefs on the array"),
    # Destructive words only as TEXT inside a quoted argument (issue #10).
    ("a grep pattern listing risky phrases", ALLOW,
     "grep -rnE 'rm -rf|wipefs|DROP TABLE|git push --force' vendor/pkg"),
    ("an issue body describing the bug", ALLOW,
     'gh issue create --title "interlock bug" --body "it blocks rm -rf when it is only text"'),
    ("a commit message", ALLOW, 'git commit -m "docs: say why git push --force is blocked"'),
    ("echo into tee", ALLOW, 'echo "never zpool destroy a live pool" | tee notes.txt'),
    ("PowerShell Select-String", ALLOW, "Select-String -Pattern 'rm -rf|DROP TABLE' -Path *.md"),
    ("PowerShell Write-Host", ALLOW, 'Write-Host "DROP TABLE is blocked by the interlock"'),
    ("a heredoc written to a file", ALLOW, "cat > notes.md <<'EOF'\nrun rm -rf only after listing\nEOF"),
    ("Set-Content with text", ALLOW, "Set-Content -Path notes.txt -Value 'wipefs is irreversible'"),
    ("unquoted echo text", ALLOW, "echo rm -rf is irreversible"),
    ("a read benchmark into /dev/null", ALLOW, 'dd if="$F" of=/dev/null bs=1M count=200'),
    ("issue text piped through grep and head", ALLOW,
     "gh issue view 9 --json body --jq .body | grep -n 'DROP TABLE' | head -5"),
    ("text written to a file, then an unrelated script", ALLOW,
     "echo 'rm -rf only after listing' > notes.txt; python build.py"),
    ("a quoted pipe inside a grep pattern", ALLOW, 'grep -E "zpool (create|destroy)" runbook.md'),
    ("the override, once enumerated", ALLOW,
     "zpool create tank /dev/sdb  # interlock-ok: zpool list shows no pool, disk is blank"),
    ("a non-Bash tool is not our business", ALLOW, None),
]


def run(cmd, tool="Bash"):
    payload = ({"tool_name": tool, "tool_input": {"command": cmd}}
               if cmd is not None else {"tool_name": "Read", "tool_input": {}})
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stderr


fails = 0
print(f"  {'':<4s}{'want':<6s}{'got':<5s}case")
print("  " + "-" * 72)
for name, want, cmd in CASES:
    rc, err = run(cmd)
    ok = rc == want
    fails += 0 if ok else 1
    print(f"  {'ok ' if ok else 'XX ':<4s}{want:<6d}{rc:<5d}{name}")
    if not ok and err:
        print(f"       {err.strip().splitlines()[:2]}")

# The shell decides what a backtick means: substitution in bash, an escape in PowerShell.
for name, want, cmd, tool in [
    ("bash runs a backtick inside double quotes", BLOCK, 'gh issue comment 1 --body "see `rm -rf /srv/x`"', "Bash"),
    ("PowerShell backtick is an escape", ALLOW, 'gh issue comment 1 --body "rm -rf is blocked`nsee docs"', "PowerShell"),
    ("PowerShell $( ) still runs", BLOCK, 'gh issue comment 1 --body "$(git push --force origin main)"', "PowerShell"),
]:
    rc, err = run(cmd, tool)
    ok = rc == want
    fails += 0 if ok else 1
    CASES.append((name, want, cmd))
    print(f"  {'ok ' if ok else 'XX ':<4s}{want:<6d}{rc:<5d}{name}")

# The block must survive the lesson store being unavailable -- an interlock that
# fails open when its index is down is not an interlock.
env = dict(os.environ, GNOMONKIT_MEMORY_GLOB=os.path.join("does", "not", "exist", "*.md"))
p = subprocess.run([sys.executable, HOOK],
                   input=json.dumps({"tool_name": "Bash",
                                     "tool_input": {"command": "zpool destroy tank"}}),
                   capture_output=True, text=True, env=env, timeout=120)
ok = p.returncode == BLOCK and "enumerate before destroying" in p.stderr
fails += 0 if ok else 1
print(f"  {'ok ' if ok else 'XX ':<4s}{'2':<6s}{p.returncode:<5d}"
      f"blocks even when the lesson store is unreachable")

print(f"\n  {len(CASES) + 1 - fails}/{len(CASES) + 1}")
sys.exit(1 if fails else 0)
