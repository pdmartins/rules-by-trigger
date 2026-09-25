# How injection and verification work, and the configuration behind it

Read this when a question is about timing, repeats, scopes, the `verify:` key
or `config.json`.

## How injection works

- The hook reads the rules on every Read/Edit/Write/MultiEdit/NotebookEdit and
  injects the ones whose glob matches the touched file. It also runs for a
  `Skill` call and injects the ones whose `call:` matches the skill loaded —
  see `references/calls.md` for the grammar. A rule may declare a glob, a
  call, or both.
- What reaches the model is the rule bodies and nothing else — an opening tag,
  the bodies separated by a `---` line, a closing tag. No preamble, no rule
  name, no glob, no scope: nothing about a rule's origin is emitted.
- A rule is injected **once per session** in the main conversation, and once in
  each subagent, which starts from an empty context of its own; then it is
  **sent again, whole**, once the context has moved on by `remember_again_after` — and only when the rule's glob
  or call matches again, so a rule for a folder nobody reopens, or a skill
  nobody reloads, is never repeated. A `glob` and a `call` on the same rule
  share one dedup key, so whichever fires first delivers it and the other does
  not re-send it in the same context.
  The value takes tokens (`30k`, `1M`), calls (`25 calls`), or `never`. Each
  rule type carries its own default, which `add` writes into the rule; the
  session-wide default lives in `config.json` and `rules-by-trigger config` prints
  it. `remember_again_after:` in a rule's frontmatter overrides everything, and
  `RULES_BY_TRIGGER_REMEMBER_AGAIN_AFTER` overrides it for one session.
- The record of what each context already received keeps at most 512 entries,
  oldest out first. A rule whose entry drops out is injected once more the next
  time its glob matches: a duplicate, never a rule withheld. Only a session
  that runs many subagents gets near the cap.
- There is no short form of a repeat: with no header there is no way to mark a
  fragment as one, so the whole body is resent. **A short rule is therefore a
  cheap rule** — this is the practical reason to keep one constraint per file.
- Editing a rule re-injects it in full immediately — the dedup key includes the
  content.
- Bash access (`cat`, `sed -i`) does NOT trigger injection; only the five file
  tools and the tools a `call:` can name (`Skill`, today) do.
- The user sees one terminal line per tool call that injected a rule, naming
  every rule it injected (marking repeats, new versions and subagent calls).
  It rides on `systemMessage`, which Claude Code shows to the user, and adds
  nothing to `additionalContext`, the text the hook injects for the model.
  `"show_injections": false` in the GLOBAL config turns it off; a project
  config cannot (a repository whose rules get injected must not be able to
  hide that from the user).
- Scopes: every `.claude/rules-by-trigger/` from the touched file's directory up to
  the filesystem root, plus `~/.claude/rules-by-trigger/`. A `Skill` call touches
  no file, so its scopes are anchored on the session's cwd instead, walked up
  the same way. The walk does not stop at a repository boundary, so a git
  submodule receives its parent repository's rules. The global scope is
  budgeted first and the outermost scope second, so neither can be crowded out
  by rules in nested directories. `<project-root>` is the repository root
  (`git rev-parse --show-toplevel`), not whatever directory happens to be the
  cwd.
- A `Skill` call whose `call:` matches nothing touches session state only if
  one of its scopes still has a legacy `rules-map.yml` (the notice about it
  has to be delivered); with no scope at all, or scopes but neither a
  matching rule nor a legacy map, it does not open, lock or advance the
  counter `remember_again_after: N calls` measures against.
- Changes take effect immediately. No restart.

## How verification works

- `verify:` takes one command on the key's own line, or a list under it — the
  same two shapes `glob` and `exclude` accept:

  ```markdown
  ---
  glob: src/api/**
  verify:
    - pytest -q tests/api
    - ruff check src/api
  ---
  ```

  Each item is one shell command line, so `pytest -q && ruff check .` is a
  single verification. `verify: none` means the key is off, and is what
  `--verify none` leaves behind.
- The commands run at the end of a turn in which Write/Edit/MultiEdit/
  NotebookEdit wrote a file the glob matches. Reads never trigger them, and a
  Bash edit is invisible to them exactly as it is to injection. They run again
  only after a new matching write, so a turn that is held open and then answers
  without writing ends.
- `exclude` applies. `tool` does NOT: a write is the trigger either way, so a
  rule with `tool: read` still verifies while its body never reaches the model
  for that write — `validate` says so.
- Where: a project rule's commands run at the root of the project that owns the
  rule, a global rule's at the root of the project of the file that triggered
  it (the session's cwd when there is none). Each distinct command-and-directory
  pair runs once, global scope first, then project scopes outermost first.
- A failure holds the turn open and hands Claude the rule's name, the command,
  its exit code or timeout, and the last 60 lines it printed; the rule's body
  is not repeated. Commands that passed are one line to the USER and nothing to
  the model.
- A command that never ran — the turn's budget was already spent, or the
  process could not be started — is not a failure: it holds nothing open and
  counts nothing, since it verified nothing. The user is told; the model only
  as a short section appended to a report already being sent.
- A rule file written by Write/Edit during the session has its `verify:`
  deferred to the next session, and the user is told, the way Claude Code's own
  `settings.json` hooks take effect only after a restart. Writing the rule
  through this CLI defers nothing: that path is already a shell.
- Bounds on the key: 8 commands per rule, 512 characters each. The hook drops
  what is past them, so `add` and `update` refuse to write it and `validate`
  names it in a rule written by hand. Bounds at run time: 120 s per command,
  540 s for everything one turn owes, 8k characters of one command's output and
  24k for the whole report.
- Both scopes execute. A project `verify:` is not gated the way `block:` is,
  because a repository's own `.claude/settings.json` hooks already run once its
  directory is trusted.

## Changing the configuration

The taxonomy, the repeat defaults and the size limits live in `config.json`,
in three layers — the plugin's own, then `~/.claude/rules-by-trigger/config.json`,
then `<project>/.claude/rules-by-trigger/config.json`, nearest wins:

```json
{
  "rule_types": [
    {"prefix": "BUSN", "name": "Business Rules",
     "purpose": "Domain invariants — violating one makes the software wrong",
     "remember_again_after": "20k"}
  ],
  "remember_again_after": {"tokens": "30k", "calls": "25 calls"},
  "rule_size": {"max_chars": 4000, "warn_chars": 2000},
  "language": "pt-BR"
}
```

`language` is what you write rule bodies in, and — when the plugin ships a
translation of it (`en`, `pt-BR`) — also the language of the text the hook
injects around them. Any other language is a fine value: the rules come out in
it and only that surrounding text falls back to English, which `config` and
`validate` both say out loud. The project layer wins over the global one on
purpose: a rule written inside a repository should come out in that
repository's language.

`rule_types` is replaced whole by the nearest layer that declares it; the other
keys merge key by key. A project layer is treated as untrusted (it arrives with
whatever repository is checked out): its intervals have a floor, its texts are
bounded, and it may shorten `max_chars` but never lengthen it.

No subcommand writes this file, and it lives **inside** the rules directory —
which the recommended hardening deny-lists for Read and Edit alike. So under
hardening you cannot edit it yourself: write out the exact JSON the user should
put in which layer, let them save it, then run `config` to confirm what took
effect. Without hardening, edit the file directly and run `config` afterwards.

## Reminders

- Files inside `.claude/rules-by-trigger/` never trigger injection themselves.
- Rule content is untrusted input and is not dressed up as anything more
  trustworthy than it is: a rule file carries exactly the authority any file in
  the repository carries. What the hook does defend is the boundary — content
  cannot close the block early nor impersonate the harness.
- The hook never blocks a tool call: on any internal failure it warns on stderr
  and stays silent.
