---
description: Activates when writing a runbook step, deploy plan or owner checklist, or before running a restart, reload, DNS or tunnel change, live edit or live migration.
---

> Full trigger text: Any step that can interrupt or change a running product — a firewall reload, a container or daemon restart, a DNS or tunnel change, a live workflow edit, a migration on live tables, a deploy that serves traffic — says so in the step itself, with what it affects, for how long and whether it needs a quiet window, and is verified from outside afterwards. Activates when writing an owner checklist, a runbook step, a deploy plan, or before running such an action yourself.

# Say when a step can touch the live product

A common way this goes wrong: a monitoring checklist hands the owner one line
to open a firewall port — add the allow rule, then reload the firewall. On a
host that also runs Docker, some firewall reloads rebuild iptables without
Docker's chains, so every container loses its network; a tunnel drops off
the edge and the product's public endpoints fail until the containers are
restarted, with customers on the other end. Nothing in the step warned that
it could.

The failure is not the firewall. It is that a step with a blast radius was
written in the same voice as "copy these files".

## The rule

Before **any** action that can interrupt or change the running product, the
step itself says three things:

1. **What it can affect** — which service, which customers, which data.
2. **For how long** — the expected window, and the worst case you know of.
3. **Whether it belongs in a quiet window** — and what "quiet" means here.

Then, after the step, **verify from outside and say so**: a public endpoint
answers, the tunnel is registered, the status page is green, the count is
what it should be. "It ran without error" is not verification; the firewall
reload above runs without error.

This applies equally to actions the agent runs itself and to commands it
hands the owner. A step handed over is a step the agent is responsible for,
and it is never buried in a numbered checklist as if it were harmless.

## What always gets the note

- Firewall reloads and rule changes (csf, ufw, iptables, security groups).
- Daemon and container restarts, host reboots, kernel or runtime updates.
- DNS, tunnel, reverse-proxy, TLS and access-policy changes.
- Deploys of anything that serves live traffic: Workers, Pages, images,
  functions, edge config.
- Live edits or publishes of a workflow on a revenue or auth path
  (licensing, trials, revocation, payment webhooks, metadata proxies).
- Migrations or writes on live databases, including "just a column".
- CI variables, secrets or runner labels that route production builds or
  releases.
- Anything a vendor documents as "takes effect on next deploy" — the
  change may land later than the command, or require a deploy you did not
  plan.

## Shape of a step that follows the rule

> **3. Open the exporter port.** *Affects: every container on the box loses
> network for a few minutes while csf rebuilds its chains, so the API and
> its webhooks are unreachable; do this in a quiet hour.* Add the line, run
> `csf -r`, then confirm the tunnel re-registered:
> `docker logs --since 2m example_tunnel | grep -c 'Registered tunnel connection'`
> (expect 1 or more) and that a public webhook answers 200.

If you cannot state the impact, you do not understand the step well enough
to hand it over. Find out first, or say plainly that the impact is unknown
and let the owner decide the window.

## Why it is a rule and not a courtesy

The owner cannot read the blast radius off a command; the agent has the
context that makes it visible. A silent step turns the owner into the
incident detector, after the fact, with customers already affected. Saying
it up front costs one sentence; not saying it can cost an outage and the
owner's trust in every other step on the list.
