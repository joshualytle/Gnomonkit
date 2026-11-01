---
description: Activates before reading a config, secret, state file or API response that may hold a credential (get -o yaml, env dumps, curl output, describe output).
---

> Full trigger text: Extract the one field you need instead of dumping the whole object, because the transcript is a durable artifact that outlives the command. Activates before reading any config, secret, state file, or API response that could contain a credential - kubectl/talosctl get -o yaml, env dumps, curl responses, connection strings, cloud CLI describe output.

# Secret-safe inspection

## The rule

**Never dump a whole object to read one field.** Extract the field.

```powershell
# NO — prints tokens, CA material, join secrets, everything
talosctl -n <node> get mc v1alpha1 -o yaml

# YES — one field
talosctl -n <node> get mc v1alpha1 -o yaml | Select-String 'installer:'
```

```powershell
# NO — dumps every key's value
kubectl get secret <name> -n <ns> -o yaml

# YES — key NAMES only, never values
kubectl get secret <name> -n <ns> -o jsonpath='{.data}' | ConvertFrom-Json | Get-Member -Type NoteProperty
```

The same shape applies everywhere: `aws ... describe-*`, `gh api`, `docker inspect`,
`terraform show`, `env`, `cat .env`, a raw `curl` of an authenticated endpoint.
If the object can contain a credential, name the field you want.

## Why the transcript is the reason

A command's output does not vanish when you scroll past it. It is written to the
session transcript, which is a durable file on disk, and downstream that file gets
mined, indexed, embedded, backed up, and pasted into issues.

Transcripts from ordinary infrastructure work tend to accumulate many kinds of
credential: cluster join tokens, cloud access keys, API tokens, database
connection strings, JWTs.

They usually arrive the same way: a command that dumped an object when a field
was wanted. Almost never a deliberate `echo $SECRET`.

**Think of every command as writing to a file you will publish.** That reframing
gets the decision right without needing a rule for each tool.

## When it happens anyway

It will. The correct response is not to move on quietly:

1. **Say so immediately**, in the turn it happened, and name what leaked by *kind*
   — never re-print the value to describe it.
2. **Rotate.** Redacting a copy does not un-leak a credential. The value is in the
   transcript on disk, and possibly already mined.
3. **Do not re-run the command** to check what it showed.

A leak disclosed and rotated is a non-event. A leak noticed and skipped past is a
live credential in a searchable index.

## Reading, not dumping

Prefer, in order:

1. **Ask the tool for the field.** `-o jsonpath=`, `--query`, `--format`, `--template`.
2. **Filter the output.** `Select-String 'field:'` / `grep -m1 'field:'`.
3. **Verify indirectly.** Often the question is "is this configured correctly?",
   which a functional check answers without reading the value at all — does the pod
   start, does auth succeed, does the mount appear.
4. **Only then** consider the full object, and only when it provably holds nothing
   sensitive.

## When NOT to use this

Objects with no credential surface — a ConfigMap of feature flags, a Deployment
spec, a `kubectl get pods` listing. Do not turn every read into a field extraction
exercise; the rule is about objects that *can* carry secrets.
