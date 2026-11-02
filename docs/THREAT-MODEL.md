# Threat model

Gnomonkit's hooks run inside your coding agent's sessions, with your account's permissions. This
page says what the blocking hooks defend against, what they don't, and how each claim is tested.
For reporting a flaw, see [SECURITY.md](../SECURITY.md).

## Who the guards are for

**A well-meaning agent making a mistake.** That is the case the guards are built for. The agent is
debugging, reads an empty listing as "nothing there", and reaches for a forced create or a recursive
delete. Or it pushes over a branch because the push was refused. It isn't trying to get past
anything. It's moving quickly, and the guard puts one required step (list what will be destroyed)
between the mistake and the damage.

**Not an adversary.** A model that is trying to evade the guards, or a prompt injection steering
one, can. Ways around them include a variable holding the verb, an encoded command decoded into a
shell, a script written to a file and then run, or a tool the pattern list doesn't name. The hooks
are heuristics: they reduce some risks and miss others, and they are not a sandbox or a security
boundary. If an agent might act against you, run it in a container or a virtual machine with only
the access the task needs.

## The blocking hooks

| hook | blocks | fails |
|---|---|---|
| `destructive_interlock` | an enumerated list of irreversible shell commands, until what they destroy has been listed | open on unreadable input, closed once a command matches (it blocks even when its lesson search fails) |
| `heredoc_guard` | a Bash heredoc carrying a backslash or triple quote, which the tool-call path mangles | open |
| `sync_guard` | writes into the sync gate's files, and merges that would approve a held plugin update | closed for those, open for everything else |
| `grants_guard` | an agent changing who may start or steer its sessions or which folders are trusted, and starting, stopping or messaging another session the grants don't allow | closed for the protected files and session commands |

Every other core hook only adds context and never blocks. The interjector plugin's hooks can also
refuse a call, but for workflow, not safety: one keeps a dispatched session out of `EnterWorktree`,
another delivers a message the user sent mid-turn. Both fail open.

## What the interlock covers

The interlock's scope is an explicit list (`DESTRUCTIVE` in
`plugins/core/hooks/destructive_interlock.py`): pool and filesystem wipes, raw disk writes, volume,
namespace and database drops, restores over a live database, recursive deletes, force pushes, bucket
and branch deletes, `git reset --hard` and `git clean -f`, Docker volume removal, and `terraform` or
`tofu destroy`. It is a list on purpose. A guard that blocks every `rm` trains people to dismiss
it, and a dismissed guard protects nothing.

Each listed action is matched however it is commonly spelled: bundled or separate flags, long flags,
PowerShell and cmd verbs (`Remove-Item -Recurse`, `rd /s`), `git push -f` and a `+` refspec. It is
matched wherever the shell actually runs it: after `&&`, behind `sudo` or `xargs`, inside `bash -c`,
`ssh` or `$(...)`, in a heredoc fed to a shell or a database client. Text that only mentions a
command (a grep pattern, a commit message, an echo into a file) is not a command and is not blocked.

**Not covered:** variables and aliases that hide the verb, encoded or generated commands, scripts
written with one tool and run with another, and destructive commands outside the list (for example
`find -delete`, `redis-cli FLUSHALL`, `gh repo delete`, `aws s3 rb`). Adding a command to the
list is a decision about false alarms as much as about safety, so each addition needs its own
must-allow cases.

## How the claims are tested

- `plugins/core/hooks/test_destructive_interlock.py` holds a must-block and a must-allow case for each
  claim above. A spelling that should block and doesn't is a bug; so is a mention that blocks.
- The bench's guards family runs the interlock in a real session. A bare model already passes those
  scenarios, so today they show the guard gets in the way of nothing, not that it saves anything.
  Harder scenarios are on the roadmap.
- Plugin updates themselves are a supply-chain risk, since hooks run on every tool call. The sync
  gate ([SYNC.md](SYNC.md)) holds an update until a key you approved has signed it.
