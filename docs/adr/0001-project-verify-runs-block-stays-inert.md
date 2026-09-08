# 1. A project-scope `verify:` runs; a project-scope `block:` stays inert

Date: 2026-09-07

## Status

Accepted

## Context

Rules live in two scopes. The global scope (`~/.claude/rules-by-path/`) is the
machine owner's and is trusted input. A project scope
(`<root>/.claude/rules-by-path/`) arrives with whatever repository is checked
out. Two rule verbs act beyond injecting text:

- `block: true` refuses a tool call, with the rule's text as the reason.
- `verify:` runs a shell command at the end of a turn in which a file the
  rule covers was written, and holds the turn open when it fails.

`block:` is honoured only from the global scope. The question was whether
`verify:`, which executes a command rather than refusing one, should be gated
the same way, or more strictly (an allowlist of approved commands, a one-time
approval per command).

## Decision

A project-scope `verify:` runs, with no gate. A project-scope `block:` remains
inert.

## Consequences

The two verbs answer to different questions, so the asymmetry is deliberate:

- Blocking is the plugin acting on the machine owner's behalf **against** the
  repository. A cloned repository that could deny the user's own tool calls
  would be an escalation nothing else in Claude Code grants, so only the
  owner's scope may block.
- Executing a command from a project file is what Claude Code already does:
  hooks declared in a repository's `.claude/settings.json` run once the
  directory is trusted, with no approval of their own. A project `verify:`
  opens no door the native hooks have not opened.

An allowlist was considered and rejected: it would pin the command's string,
not its behaviour. A cloned repository shipping its own `Makefile` runs
whatever the approved target does, so the allowlist would promise a boundary
it cannot keep. A gate that is not a boundary is worse than none, because it
is read as one.

What this costs: a user who trusts a directory trusts its `verify:` commands
in the same gesture. The README says so next to the key. If Claude Code ever
adds an approval step for project hooks, the plugin should follow it rather
than keep its own.
