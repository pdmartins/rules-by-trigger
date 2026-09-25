# Triggering a rule from a tool call: `call:`

Read this when a rule should fire on a skill load rather than, or besides, a
file touch — and for `call:`'s grammar and its limits.

## The grammar

`call:` (or `calls:` for a list) takes one or more triggers of the shape
`Tool(field=value)`:

```markdown
---
call: Skill(skill=workflow-authoring)
---
Before writing a Workflow script, read the script API and its resume rules.
```

- `Tool` is the `tool_name` Claude Code's PreToolUse hook receives — exactly,
  case-sensitive.
- `field` is a key of that call's `tool_input`.
- `value` is everything after the `=` up to the last `)`, whitespace-stripped.
  Colons and parentheses inside it are fine, so a plugin skill's qualified name
  works as-is: `Skill(skill=plugin-name:skill-name)`.
- A rule can declare several: one per line under `call:`, same list syntax
  `glob` and `exclude` already use.

**Match is exact, not a glob.** `tool_name` must equal `Tool`, `tool_input[field]`
must be a string, and that string, stripped, must equal `value` exactly —
character for character, case-sensitive. Write the skill name the way the skill
listing shows it, prefix and all for a plugin skill.

A `call:` that does not parse, or whose value never matches a live call, is
silent: nothing fires, and nothing warns you at runtime — `validate` is where a
bad one is caught (see below).

## Only `Skill` is registered

The hook only listens for calls naming a tool in a short allowlist, currently
just `Skill`. A `call: Bash(command=...)` or any other tool never fires, no
matter how it is written — the harness never even invokes the hook for a tool
outside its `PreToolUse` matcher, so there is no path left to enforce this
manually.

Why `Skill` and not `Workflow`, for a workflow script: a `PreToolUse` hook on
the `Workflow` tool only starts existing once the script it names has already
been picked up and normalized, so by the time the hook could match on it the
choice is already made. Loading the skill that teaches how to write that
script is the one moment early enough to still change what gets written — so
that is the trigger, not the tool it eventually calls.

## What still applies, and what does not

A `call:` rule is delivered through the same door as a `glob:` rule: same
`additionalContext` injection, same once-per-session dedup, same reinjection
once the context has moved on by `remember_again_after`, same usage-stats
entry. `remember_again_after` and the rule's type both still apply.

`exclude:`, `tool:`, `block:` and `verify:` all concern path matching only.
On a rule that carries a `call:` and no `glob:`, they have no effect at
all — there is no path for them to filter, exclude from or verify — and
`validate` notes this rather than treating the keys as an error. On a rule
that carries both, they still narrow the glob side exactly as before; the
call side ignores them.

`glob` and `call` are ORed, not ANDed: a rule with both fires on a matching
file touch OR a matching call, whichever happens first in a context, and the
shared dedup key means the other trigger will not re-send it in the same
session.

## The soft-guarantee limit

The trigger is a real `Skill` tool call, not the user typing `/skill-name`.
Typing a slash command never issues a `Skill` tool call by itself — the model
still has to decide to load the skill, and a rule waiting on that load only
fires once it does. `call:` raises the odds that the right guidance is in
context at the right moment; it is not a guarantee that the skill gets loaded
in the first place.
