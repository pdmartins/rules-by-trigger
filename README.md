# rules-by-trigger

**Path-scoped rules for Claude Code.** Markdown rules are injected into
context automatically — by a `PreToolUse` hook — the moment Claude touches a
file matching a glob. Nothing loads until it is relevant; each rule loads at
most once per session.

A scalable replacement for nested `CLAUDE.md` files.

## Why

`CLAUDE.md` is all-or-nothing: everything in it is in context on every
session, whether or not the work touches that part of the tree. Scattering
nested `CLAUDE.md` files through subfolders sort of works, but pollutes the
repo, and the guidance still isn't tied to what the agent actually touches.

`rules-by-trigger` inverts this:

- A rule is one markdown file that declares the glob it applies to, in its own
  frontmatter. There is no index to keep in sync.
- A `PreToolUse` hook watches `Read`/`Edit`/`Write`/`MultiEdit`/`NotebookEdit`.
- The first time Claude touches a matching file, the rule is injected into
  context (`additionalContext`) — the body, and nothing else:

  ```
  <rules-by-trigger>
  Every endpoint must validate its input.
  ---
  Never log the request body.
  </rules-by-trigger>
  ```

- Injection happens **once per rule version per session**, then the rule is
  **sent again, whole**, once the context has moved on by
  `remember_again_after`, so it does not fade out of a very long context.
  Editing a rule re-injects it immediately.
- Zero context cost for rules that never become relevant.

Selection by path is the mechanism, and injecting text is only the first thing
it can drive. One rule carries up to three verbs, all chosen by the same glob:
**inject** its body, **block** a write to the paths it covers (*Blocking a
write*), and **verify** — run the command it declares at the end of a turn in
which one of those paths was written (*Verifying a write*).

What this buys you is convention adherence and token economy, not task
correctness: two 2026 ablation studies found that injecting a rule into
context, on its own, barely moves correctness on the underlying task. `verify:`
does not change that: it guarantees that the check you declared runs, and that
a failure reaches Claude before the turn ends, not that the check is the right
one.

## Install

In Claude Code:

```
/plugin marketplace add pdmartins/rules-by-trigger
/plugin install rules-by-trigger@pdmartins
```

Then run `/rules-by-trigger:doctor` once — it checks prerequisites, smoke-tests
the hook, asks which language the plugin's messages should use, and offers the
recommended permission hardening (`doctor --harden`), asking before it touches
`~/.claude/settings.json`. Your answers are saved in
`~/.claude/rules-by-trigger/config.json`. Until that file exists, every CLI
command except `doctor`, `show` and `status --json` starts its output with a
one-line reminder to run this setup; declining it saves an empty file, and the
reminder stops.

**Requirements:** Python 3.8+ on `PATH` as `python3`, `python` or (Windows)
the `py` launcher. Standard library only — nothing to install. Tested on Linux
and macOS; Windows support is implemented but not yet verified by the author.

## Quick start

Just ask Claude, in any language:

> "Add a rule for `src/api`: every endpoint needs input validation and must
> return ProblemDetails on errors."

The `rules-by-trigger:manage` skill writes one file:

```
.claude/rules-by-trigger/
└── CONV_api-returns-problemdetails.md
```

```markdown
---
glob: src/api/**
remember_again_after: 50k
---
Every endpoint validates its input and returns ProblemDetails on error.
```

The `CONV_` prefix is the rule's **type** — what violating it would cost. Four
types ship (`BUSN` business rules, `ARCH` architecture decisions, `CONV`
conventions, `OTHR` memory pills), each with its own repeat distance, and you
can declare your own (see *Configuration*). Only `BUSN` repeats by default:
prohibition-shaped constraints are the ones measured to decay under long
context, so the other types default to `never` and pay reinforcement's token
cost only when a rule opts back in.

The type is the one thing Claude does not guess — it says what violating the
rule costs, not what the rule is about — so when your request leaves it open,
it asks, with the configured types as the options to pick from.

A rule can declare several globs, and several rules can share one glob — they
all inject together.

**Keep a rule to one scope.** Every file a glob matches receives the *whole*
rule, so constraints that govern different paths belong in different rules. A
rule saying "controllers look like X, the DI file looks like Y, no file over 300
lines" under `src/Api/**` tells a controller how DI works and the DI file how
controllers are shaped; as three rules — `src/Api/**`, `src/Api/Controllers/**`,
`src/Api/DependencyInjection.cs` — each file gets only what changes what you do
to it. `add`, `update`, `migrate` and `validate` flag the detectable version of
this: a rule whose text names a file or folder that exists under its own glob.

From then on, whenever Claude touches anything under `src/api/`, the rule
appears in its context — once per session, exactly when it matters.

Other things you can ask for: *"list the path rules"*, *"remove the rule for
docs/"*, *"update the terraform rule"*.

## Scopes

| Scope | Location | Globs match |
|---|---|---|
| **Project** | `<project-root>/.claude/rules-by-trigger/` | paths relative to the project root |
| **Global** | `~/.claude/rules-by-trigger/` | absolute paths |

Nested projects work: every `.claude/rules-by-trigger/` above a touched file
applies, all the way up to the filesystem root. The walk does not stop at a
repository boundary, so a git submodule receives its parent repository's rules.
Your global rules are budgeted first, so rules arriving with a cloned repo can
never crowd them out. Project rules are committed with the repo, so the whole
team shares them.

A rule can change scope — `move --rule <name> --root <root> --to-global`, or
`--global … --to-root <root>` — and the CLI rewrites its globs for the new
frame: bare names and `**/`-floating globs travel as they are, an absolute glob
becomes project-relative, and a root-anchored glob going global is refused
until you say whether it should hold in any project (`--anchor any-project`,
giving `**/src/api/**`) or only in this one (`--anchor this-project`).

## Narrowing a rule further

A glob answers *where*. Two more frontmatter keys answer *which paths inside
it* and *when* — both restrictive, and ANDed with the glob and with each other.
A rule reaches the model when one `glob` matches, no `exclude` matches, and the
tool call is of a kind `tool:` accepts.

```markdown
---
glob: src/**
exclude: src/**/*.test.ts
tool: write
---
Every exported function is documented with TSDoc.
```

- **`exclude:`** takes paths back out of a glob. Same syntax as `glob` (one
  value or a list), same limits. It says the thing a glob alone cannot:
  `src/**` *except* the tests, the generated code, the vendored tree.
- **`tool:`** restricts the rule to `write` calls (Write, Edit, MultiEdit,
  NotebookEdit) or to `read` ones. `any`, or leaving the key out, means both.

`tool: write` is the one that pays for itself twice. Most conventions govern
what you *create*, not what you *read* — and a rule spent on a Read is both a
context cost with no decision attached and, because dedup is per session, a
rule that may no longer be there when the Write finally happens.

A filter can only ever narrow a rule, so a value the hook cannot read is
**ignored, not enforced**: `tool: wirte` leaves the rule unfiltered rather than
silently switching it off. `validate` reports the typo, and refuses outright the
two shapes that disable a rule without saying so — an `exclude` that takes back
every path, and one that cancels every glob the rule declares.

`which` explains the outcome for a concrete path:

```
$ rules-by-trigger which --root . --path 'src/api/users.test.ts'
excluded: rule CONV_tsdoc.md — 'src/**' covers this path, exclude: 'src/**/*.test.ts' takes it back

$ rules-by-trigger which --root . --path 'src/api/users.ts' --tool read
filtered: rule CONV_tsdoc.md — 'src/**' covers this path, but the rule is tool: write only
```

## Repeating a rule

A rule is injected the first time it is relevant, then sent again once the
context has moved on by 30k tokens (the shipped default). On a long-context session a rule injected
hundreds of thousands of tokens ago has effectively faded.

The distance is measured in **context tokens**, read from the session
transcript — the count the API itself billed. That is the honest unit: a session
that reads three huge files burns 200k tokens in three tool calls, while one
doing fifty tiny greps burns 20k in fifty. Where the transcript cannot be read,
the hook falls back to counting file-tool calls (default 25) and says so in
`/rules-by-trigger:status`. There is no conversion between the two units.

There is no short form of a repeat: with no header in the emitted text, there is
no way to mark a fragment as one, so the whole body is resent. **A short rule is
a cheap rule.**

A rule is only ever repeated when its glob matches the file being touched. A
rule governing a folder nobody opens again is never repeated, however long the
session runs.

The default comes from the rule's type, then from `config.json` (see below);
`RULES_BY_TRIGGER_REMEMBER_AGAIN_AFTER` overrides it for one session, and
`remember_again_after:` in a rule's own frontmatter overrides everything —
tokens (`30k`, `1M`), calls (`25 calls`), or `never`:

```markdown
---
glob: infra/**
remember_again_after: 50k
---
```

## Usage stats, and improving rules with them

Every injection is counted, per rule, in one small file beside the session
state (`usage-stats.json`): injections, repeats, distinct sessions, first and
last date, the directories the rule fired under and the glob that matched —
every collection bounded, session ids never shown. A rule that declares
`verify:` carries a third number — how often its commands ran and how often
they failed, printed as `verified N, failed M`, and counted for every rule that
asked for a command when two share one. `status` prints all of it next to each
rule and derives two notes from it: a rule **never injected** since stats
began, and a rule that fires often but **always under one subfolder** of a
wider glob, with the narrower glob to use. A rule whose command ran but whose
text has never been delivered still counts as never injected — a command
running says nothing about the guidance beside it.

The `rules-by-trigger:improve` skill turns that, plus the validator's notes, into
proposals — prune, narrow, split, reword — and harvests path-bound
instructions out of `CLAUDE.md` files and native `.claude/rules/*.md` into
rules that also fire on writes. Its second input, `digest --root <root>`,
lists those sources and distills your own turns from this project's most
recent sessions (harness noise dropped, everything bounded), pairing each
session with the rules injected in it, so a correction that followed an
injection is visible as a rule that did not land. It reads only this
project's transcripts, and only when asked.

## Configuration

The rule taxonomy, the repeat defaults, the size limits and the language rules
are written in all live in a `config.json` read from three layers, each
overriding the one before it:

| Layer | Where | Trusted |
|---|---|---|
| Plugin | `<plugin>/config.json` — the shipped default | yes |
| User | `~/.claude/rules-by-trigger/config.json` | yes |
| Project | `<project>/.claude/rules-by-trigger/config.json` | **no** |

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

`rules-by-trigger config --root <root>` prints the effective result and names the
layer each value came from. `rule_types` is replaced whole by the nearest layer
that declares it — merging two taxonomies by prefix would produce a hybrid
nobody wrote; the other keys merge key by key.

A project layer arrives with whatever repository is checked out, so it is
treated like any other repository content: its intervals are clamped to a floor
(a clone cannot ask for a repeat on nearly every tool call), its type texts are
bounded to one printable line, its prefixes must be ASCII letters and digits,
and it may **shorten** `max_chars` but never lengthen it. A layer that cannot be
parsed is skipped with a warning; nothing about a config can stop injection.

### `language`

`language` is what the manage skill writes rule **bodies** in, so the choice
stops being re-made from the language of each conversation. It is also the
language of the text the hook injects around them — the session notice, the
supersede and truncation notices, the reason a `block: true` gives —
whenever the plugin ships a translation of it. Shipped: `en` (the default) and
`pt-BR`; `pt_br` and `PT-BR` select the same one.

Any other language is a perfectly good value: the rules are written in it, and
only that surrounding text falls back to English — `config` and `validate` both
say so rather than letting it be a surprise. The scaffolding is never taken
from configuration, only selected by it: a layer arrives with a cloned
repository, and supplying the wording of the text the model trusts most is not
something a clone may do. For the same reason the value itself is bounded to 32
characters of visible letters, digits, spaces and `-_()`, normalized to NFKC so
a lookalike of `en` is `en`, and anything else is warned about and ignored. The
allowlist buys exactly one thing — the value cannot forge a delimiter, a
frontmatter key or a second line — so the CLI quotes it rather than reading it
out as prose. The `block: true` reason is the one exception to the project
winning: that sentence speaks for you against the repository being blocked, so
only your own layers choose the language it arrives in.

Rule file names, type prefixes (`BUSN`, `ARCH`, …) and frontmatter keys are
identifiers, not prose, and never translate.

## Glob semantics

| Glob | Matches |
|---|---|
| `src/api/**` | anything under `src/api/` |
| `docs` or `docs/` | the folder `docs/` and everything under it |
| `src/config.json` | that file exactly |
| `*.cs` | any file with that basename, at any depth |
| `**/deploy/**` | a `deploy/` folder at any depth |
| `/repos/x/**` | absolute-path prefix (global scope) |
| `?` | exactly one character; `*` never crosses `/` |

The same syntax reads an `exclude:` entry — it is a glob like any other, only
matched to take a path back rather than to cover it.

A bare name with no `/` (like `docs` or `Makefile`) is matched two ways: against
the project-root path (so `docs` covers the root `docs` entry and everything
under it) **and** against the file's basename at any depth (so `Makefile` catches
every `Makefile`, and `docs` also matches a file literally named `docs`
anywhere). To target a `docs/` folder wherever it appears, use `**/docs/**`.

## Design guarantees

- **Never blocks work by accident**: any internal hook failure goes to stderr
  and the tool call proceeds untouched. The hook denies a tool call only
  through one deliberate, narrow path — a **global** rule with `block: true`
  matching a write (see *Blocking a write* below) — never as a side effect of a
  failure. A failing `verify:` is not that path either: it holds the *turn*
  open at `Stop` and denies no tool call (see *Verifying a write*). The
  recommended hardening's own `permissions.deny` entries (see *Security model*)
  are a second, independent way to deny, which the hook has no part in
  enforcing.
- **Bounded**: a rule is truncated at 4k chars (the CLI warns above 2k), one
  injection is capped at 24k, a scope is capped at 256 rules and a glob at 256
  chars, and at most 8 scopes are consulted per tool call.
- **No pathological matching**: globs are matched by a non-backtracking
  segment matcher, not a regex, so no single glob can blow up. Aggregate cost
  is bounded by a wall-clock budget *divided among the scopes*, so a scope full
  of expensive globs can only ever spend its own share — not the repository
  root's, and not your global rules'. Frontmatter is parsed by one small parser
  with no comment syntax, anchors or optional YAML dependency — there is no
  second parser to disagree with.
- **Symlinks do not change which rule applies**: a file reached through a
  directory link is matched on both the literal and the resolved path (the
  resolved one only while it stays inside the same project), so a monorepo
  alias neither loses a rule nor borrows one.
- **Stays inside the scope**: `.claude/rules-by-trigger` must physically live
  inside the project it claims to belong to, rule files are opened without
  following symlinks and must be regular files, and rule names must be plain,
  bounded `*.md` names. A hostile repository cannot reach a private key,
  `/etc`, `/proc/self/environ`, or your global rules.
- **Bounded trust**: a rules directory owned by another user, or in a
  world-writable parent (a shared `/tmp`, say), is ignored. Since the upward
  search runs to the filesystem root, this ownership check is what stands
  between you and a rules directory you do not control. It relies on POSIX
  permission bits and is not enforced on Windows.
- **The outermost rules always apply**: your global scope is consulted first
  and the outermost project scope next, and both keep their slot when the
  8-scope cap is reached. Nested `.claude/rules-by-trigger/` directories — which
  anyone opening a PR can add — cannot crowd out the rules declared above them.
- **No forgeable provenance, because none is emitted**: the injected text is the
  rule bodies between a pair of tags. Nothing states a rule's name, glob or
  scope, so there is no authority claim for content to forge. What content is
  stopped from doing is closing the block early or impersonating the harness
  (`<system-reminder>`, `<function_calls>`) — those markers are defanged inside
  rule bodies. A rule file carries the authority any file in your repository
  carries; treat one arriving in a clone the way you would treat its
  `CLAUDE.md`.
- **Concurrency-safe dedup**: parallel tool calls serialize on a per-session
  lock file, so a simultaneous first touch still injects a rule exactly once.
  The dedup key includes the rule's content, so editing a rule re-injects it
  in the same session.
- **Never loses your rules**: each rule is an independent file, so no operation
  rewrites a shared index; writes go through `mkstemp` + `os.replace`, so a
  planted symlink cannot redirect one.
- **Bash is out of scope by design**: `cat`/`sed` via Bash don't trigger
  injection — parsing paths out of arbitrary shell commands would be fragile
  and easy to spoof. The five file tools are the reliable signal.

## Security model

**Rule content is trusted input, at the same level as a repository's
`CLAUDE.md`.** Project rules ride with the repo, so cloning a repository means
trusting whatever instructions its rules contain, and you should review
`.claude/rules-by-trigger/` in code review like any other instruction file. What
the plugin guarantees is narrower and mechanical: a rule can only ever inject
*its own text*, it cannot read other files, impersonate a more trusted scope,
or hang your session.

That boundary is enforced by the containment, provenance and matching
guarantees listed above, each covered by a regression test in
`tests/test_security.py`. One asymmetry inside it is deliberate:

- **A project rule's `verify:` command runs; a project rule's `block: true`
  does not.** Running a command that arrived with a repository is what Claude
  Code already does for hooks in that repository's `.claude/settings.json`;
  letting the repository refuse the machine owner's own tool calls is not.

### Recommended hardening

`/rules-by-trigger:doctor` offers deny-list entries for your
`~/.claude/settings.json` (`doctor --harden` writes them, after you agree):

```json
"permissions": {
  "deny": [
    "Read(**/.claude/rules-by-trigger/**)",
    "Edit(**/.claude/rules-by-trigger/**)",
    "Read(~/.claude/rules-by-trigger/**)",
    "Edit(~/.claude/rules-by-trigger/**)"
  ]
}
```

Two tools, two anchors. Both halves are counter-intuitive enough to be worth
stating, and both were verified against Claude Code 2.1.233:

- **`Read` and `Edit` only.** `Read(...)` governs reads *and greps* — the Grep
  tool checks its `path` argument as a read — so a `Grep(...)` entry is never
  consulted by anything and sits dead in your settings. `Edit(...)` governs
  every file-editing tool (Write, Edit, NotebookEdit alike); a separate
  `Write(...)` entry is not matched and makes Claude Code warn at startup.
- **Both anchors.** A pattern that does not start with `/` or `~/` is resolved
  against the current working directory, so the `**/...` pair only covers the
  project you have open. The `~/`-anchored pair is what protects your **global**
  rules whenever Claude Code runs in a project outside your home directory.

With this, the *file tools* can no longer read or rewrite rule files, so rules
reach context through the hook and changes go through the bundled CLI, which
validates what it writes. Reading and updating a rule stay available through
`rules-by-trigger show` and `rules-by-trigger update`.

So that the deny-list is not something Claude discovers the hard way, a
`SessionStart` hook says it once, up front: the rules directory is managed by
the plugin, its contents arrive automatically, and the CLI is the way in.
Without that, the agent meets the directory by listing or reading it and
collects a permission denial — which explains nothing, so the attempt repeats
next session. The notice is emitted only when a scope actually exists.

It raises the bar; it is not a sandbox — it constrains Claude's file tools,
not arbitrary subprocesses. Optional, but it is how the system is meant to run.

### Blocking a write (`block: true`)

Native `permissions.deny` blocks a tool call with no explanation attached. A
rule can ask for the same block, plus one thing native deny does not offer: its
own body as the reason a human or model actually reads.

```markdown
---
glob: infra/prod/**
block: true
---
Production infrastructure is changed through the deploy pipeline only, never
by hand. Open a PR against `infra/` instead.
```

This setting was spelled `enforce: deny` until 0.7.0. Rules still carrying
that spelling keep working untouched; `migrate` rewrites them, and `validate`
points them out.

The hook still does not read a rule for CORRECTNESS — it only ever matches a
path — so `block: true` is exactly the native deny, with the rule's
(defanged) text attached as `permissionDecisionReason`. It fires only for
`Write`, `Edit`, `MultiEdit` and `NotebookEdit`; `Read` is never denied.

**Trust gate: honoured from the GLOBAL scope only.** A project's
`.claude/rules-by-trigger/` arrives with whatever repository is checked out —
exactly as untrusted as its `CLAUDE.md` — so a project rule that declares
`block: true` is inert to the hook, silently, no matter how it is worded.
There is no config, environment variable or project layer that widens this: it
is keyed to which scope actually matched, not to anything a repository could
set. `validate` still points it out, with the way around it:

```bash
"<plugin>/bin/rules-by-trigger" block --root <project-root> --list   # what would fire, and what it maps to
"<plugin>/bin/rules-by-trigger" block --root <project-root> --sync   # write the native deny entries for real
```

`--sync` writes one `Edit(<glob>)` entry per glob into that project's own
`.claude/settings.json` — `Edit(...)` alone, because it already covers every
file-editing tool (see *Recommended hardening* above); idempotent, and it
creates a minimal `settings.json` if the project has none yet. A global rule
needs no such sync: the hook already blocks directly, so `--sync --global`
is refused.

## Verifying a write (`verify:`)

Injection buys adherence to a convention; it cannot say whether what came out
actually holds. A rule can declare the check that answers that, and a `Stop`
hook runs it at the end of the turn:

```markdown
---
glob: src/api/**
verify: pytest -q tests/api
---
Every endpoint validates its input and returns ProblemDetails on error.
```

Several checks go in a list. Each item is one shell command line, so
`pytest -q && ruff check .` is one verification, not two:

```markdown
---
glob: src/api/**
verify:
  - pytest -q tests/api
  - ruff check src/api
---
Every endpoint validates its input and returns ProblemDetails on error.
```

The CLI writes it: `--verify '<command>'` on `add` and `update`, repeated for
several, and `--verify none` clears the key the way `--tool any` clears the
tool filter.

**When it runs.** At the end of a turn in which one of the four editing tools
— `Write`, `Edit`, `MultiEdit`, `NotebookEdit` — wrote a file the rule's glob
matches. A `Read` never triggers it. `exclude:` applies exactly as it does to
injection: same matcher, same answer. `tool:` is deliberately *not* consulted,
because a write is the trigger either way — so a rule narrowed to `tool: read`
still verifies, and `validate` points that out, since the check then runs with
the rule's own guidance never delivered for that write.

**Where it runs.** A project rule's commands run at the root of the project
that owns the rule — where its `pytest.ini`, its `Makefile` and its relative
paths mean what they say. A global rule has no root of its own, so it borrows
the project of the file that triggered it — the nearest directory above the
file holding a `.claude` — falling back to the session's working directory
when that file belongs to no project. Your home directory's own `.claude` does
not count as that project: it is the global scope, not a repository, so a file
written directly under home falls back to the session's working directory the
same way a file that belongs to no project at all does. The same global rule
therefore runs once per repository it touched, which is the point: `pytest`
names a different suite in each.

**What comes back.** A failure holds the turn open (`decision: block`) and
hands Claude, for each failed command, the name of the rule that asked for it,
the command itself, one status line — `exit code N`, `timed out after 120s and
was killed`, or `not finished: this turn's verification time budget ran out` —
and the last 60 lines it printed, stdout and stderr interleaved in the order a
terminal would have shown them, cut to 8k characters when a single line is
longer than that, keeping the end. The whole reason is capped at 24k, with the
failures first. The rule's body is not repeated: it was injected when the file
was touched, and paying for it twice buys nothing. Every command runs even
after one has failed, so a turn that wrote in three folders hears about all
three at once, and the ones that passed are listed at the end, `passed:
<command> (rule '<name>')`, one line each.

**A command that never ran is not a failure.** One the turn's budget was
already spent before (`not started`) and one the system refused to launch
(`could not be started`) verified nothing, so neither holds the turn open and
neither is counted in the usage stats — only a command that actually ran is
evidence about your code. The user gets one line each. Claude is told too, but
only as a short "these never ran" section appended to a report it was being
sent anyway: it should know the verification was incomplete, and there is
nothing in it for it to fix.

The order is the global scope first, then project scopes from the outermost to
the innermost, with each distinct command-and-directory pair run once. Two
sibling projects written in the same turn therefore interleave rather than
arriving grouped.

When everything passes, Claude is told nothing at all. The **user** gets one
line per command instead — `rules-by-trigger: verified — <command> (rule
'<name>')` — because the check was theirs to ask for, and the model's context
should not pay for good news.

**It runs again only after a new write.** Each verification takes the turn's
list of written paths and clears it, so a turn that is held open and then
answers without writing has nothing left to check and ends. A `verify:` that
can never pass therefore holds the turn for exactly as long as Claude keeps
writing to the paths it covers, with Claude Code's own cap of eight
consecutive `Stop` blocks as the outer backstop.

**Timeouts.** 120 s per command: past that the command *and everything it
started* are killed, and it is reported as a failure carrying whatever it had
already printed. 540 s for all of a turn's verifications together: a command
that starts near the end of that budget gets only what is left of it, and is
reported as a failure when the budget kills it — it ran. One that finds nothing
left is not started at all, and falls under *a command that never ran* above.
Neither is reported as the command's own timeout, because that is not what
they hit. The turn's budget sits well below the 600 s the `Stop` hook itself is
given in `hooks.json`, so the hook always outlives its commands and gets to
print its report. A hook the harness kills prints nothing, and a turn
ending with a failing check unreported is the one outcome worse than a slow
turn.

**Both scopes execute.** Unlike `block: true`, a `verify:` in a project's own
`.claude/rules-by-trigger/` runs, with no gate. The two verbs answer different
questions: blocking is the plugin acting on the machine owner's behalf
*against* the repository, an escalation nothing else in Claude Code grants a
clone; running a command that arrived with a repository is what Claude Code
already does, since hooks declared in that repository's
`.claude/settings.json` run once the directory is trusted — so a project
`verify:` opens no door the native hooks have not opened. An allowlist was
considered and rejected: it would pin the command's string and not its
behaviour (an approved `make check` runs whatever the cloned `Makefile` says),
and a gate that is not a boundary is worse than none, because it is read as
one.

The honest limits:

- **Edits made through the shell are invisible.** `sed -i`, or a script that
  rewrites a file, is not a write the plugin can see — exactly as it is not a
  touch it can inject for. Nothing is verified for it.
- **The command is trusted the way a project hook is.** Trusting a directory
  trusts its `verify:` commands in the same gesture. Review a clone's
  `.claude/rules-by-trigger/` the way you review its `.claude/settings.json`.
- **Still not a claim about correctness.** The plugin does not judge what your
  command asserts, only that it ran and that its failure reached Claude before
  the turn ended. A check that tests nothing passes.
- **A `verify:` Claude wrote itself waits for the next session.** When one of
  the four editing tools writes a rule file, that rule's commands are skipped
  for the rest of the session and the user is told, one line per rule —
  otherwise a model that can write a rule has written itself a shell for the
  same turn. Claude Code protects its own `settings.json` hooks exactly this
  way, by snapshotting them at startup. The CLI path is immediate: `add
  --verify` runs through Bash, which is already a shell, so a rule you or the
  manage skill add starts verifying at once.
- **Bounded**: at most 8 commands per rule, 512 chars each, at most 512 written
  paths remembered per turn and 64 rule files per session, 8k characters of one
  command's output and 24k for the whole report. `add` and `update` refuse to
  write what the hook would drop, and `validate` reports it in a rule written
  by hand.

## Uninstalling

`/plugin uninstall rules-by-trigger@pdmartins` removes the hook and the
skills. Three things outlive it. `doctor --uninstall` removes the first two —
the deny-list entries above (otherwise those paths stay unreadable) and the
cached state at `~/.claude/cache/rules-by-trigger` — and deliberately keeps the
third, your authored rules in `~/.claude/rules-by-trigger/` and each project's
`.claude/rules-by-trigger/`, listing them so you can decide.

## Troubleshooting

Two commands answer nearly everything; `/rules-by-trigger:status` and the
`rules-by-trigger:doctor` skill run them for you:

```bash
# both scopes, their findings, what covers a path, the config in force, usage
"<plugin>/bin/rules-by-trigger" status --root <root> [--path <file>] [--json]
# every setup check, each finding naming its fix; --fix applies the safe ones
"<plugin>/bin/rules-by-trigger" doctor --root <root> [--fix]
```

- **Rule not injecting?** Each rule version injects once in the main
  conversation and once in each subagent, which starts from an empty context.
  The state lives in `$CLAUDE_PLUGIN_DATA/state/` for a plugin install (falling
  back to `~/.claude/cache/rules-by-trigger/`); delete `<state-dir>/<session_id>.json`
  to force re-injection. Check that a scope containing the rule is actually on
  the path from the touched file up to the filesystem root — the walk does not
  stop at a repository boundary, so this is rarely the cause — and that
  `validate` reports it.
- **Upgrading from the `rules-map.yml` format, or from pre-0.4.0 names?**
  `doctor` reports it and `doctor --fix` runs `migrate` for you. Until then,
  a scope holding a `rules-map.yml` injects nothing and the hook says so.
- **Nothing happens at all?** `doctor` smoke-tests the hook and reports
  whether Python was found.
- **Hook errors** are printed to stderr (visible in verbose mode) and never
  block the tool call.

## Repository layout

The plugin is one directory. Everything else in this repository is the
marketplace that publishes it, or scaffolding that builds and tests it — none
of which is installed on a user's machine.

```
.claude-plugin/marketplace.json   the marketplace (this repo is one)
plugins/
└── rules-by-trigger/                THE PLUGIN — this, and only this, is installed
    ├── .claude-plugin/plugin.json
    ├── hooks/                    PreToolUse injection, Stop verification, SessionStart
    ├── bin/                      launchers (POSIX + .cmd), on PATH when installed
    ├── scripts/                  the management CLI the skills drive
    ├── skills/                   manage, doctor, improve
    └── commands/                 /rules-by-trigger:status
tests/                            development only
publish.sh                        development only
README.md  CHANGELOG.md  LICENSE
```

If it is not under `plugins/rules-by-trigger/`, Claude Code never sees it.

## Development

```bash
python3 -m unittest discover -s tests    # the suite, standard library only
claude plugin validate . --strict        # both manifests
bash publish.sh --local                  # install the working tree on this machine
```

`--local` reinstalls rather than updating on purpose: the version is
`MAJOR.MINOR.REVISION` and changes **only** on a release, so `claude plugin
update` would compare two identical version strings and keep serving the cached
copy.

The mode also decides where the install comes from, and the script repoints the
marketplace to match: a release installs from **GitHub** — exactly what it just
published, exactly what a user gets — while `--local` installs from **this
directory**, the only way to run code that is not released yet. The marketplace
name never changes, so the install id stays `rules-by-trigger@pdmartins` either way
and the two can never both be installed.

`bash publish.sh --minor` (or `--major` / `--revision`) is the release. It
refuses on a dirty tree, a failing suite or invalid manifests; then it bumps
both manifests, merges `develop` into `main`, pushes, points GitHub's default
branch at `main` — `/plugin marketplace add` reads that branch — and refreshes
the local install. `--dry-run` prints the plan without touching anything.

## Roadmap

- Adapters for other agents that support pre-tool hooks or rule injection
  (Codex, Antigravity, …) — the core is plain Python with no Claude-specific
  logic beyond the hook I/O envelope.

## License

[MIT](LICENSE)
