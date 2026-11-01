---
name: example-house-rule
description: An example private skill made by `gnomonkit overlay init`; replace it with a rule of your own or delete it. Activates when writing a status update or a summary of finished work for the user.
---

# Example: how status updates are written

This is a synthetic example. It shows the shape of a private skill: a rule that is yours alone, or
one that names your own tools, hosts or people, which is why it lives in your private overlay
rather than in Gnomonkit. Replace it with a rule of your own, or delete this folder.

The `description:` above is what tells the agent when the skill applies. Name the moment ("when
writing a status update"), not the topic.

## The rule

- Lead with what changed and what needs a decision; put the details after.
- Name every pull request by its number and say whether to merge it now or wait.
- Say plainly what was not done.

After changing a skill, bump `version` in `plugins/overlay/.claude-plugin/plugin.json`, or
installed copies won't update.
