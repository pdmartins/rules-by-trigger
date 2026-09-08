# rules-by-path

Path-scoped policy for Claude Code: markdown rules that reach the model's
context, refuse a tool call, or run a check, selected by the file being
touched. This glossary fixes the words the code, the skills and the docs use.

## Language

### Rules

**Rule**:
A markdown file with frontmatter whose body is guidance about the files its
glob covers.
_Avoid_: memory, note, instruction file

**Scope**:
Where a rule lives and therefore how far it is trusted. The **global scope**
(`~/.claude/rules-by-path/`) belongs to the machine owner and is trusted input.
A **project scope** (`<root>/.claude/rules-by-path/`) arrives with whatever
repository is checked out.
_Avoid_: level, layer (reserved for config.json), tier

**Rule type**:
The category a rule declares by its filename prefix (`BUSN`, `ARCH`, `CONV`,
`OTHR` by default). It says what violating the rule costs and sets the rule's
repeat cadence. The taxonomy comes from `config.json`, never from the code.
_Avoid_: category, kind, class

### Selection

**Touch**:
Any of the five file tools (`Read`, `Edit`, `Write`, `MultiEdit`,
`NotebookEdit`) acting on a path that a rule's glob matches. A touch is what
selects a rule.
_Avoid_: hit, access, open

**Write**:
A touch by one of the four editing tools, `Read` excluded. Files changed
through a shell command are not writes: the plugin cannot see them.
_Avoid_: edit (the tool name), change, modification

**Turn**:
The span between two `Stop` events, i.e. one answer from Claude. What was
written during a turn is what the turn's checks look at.
_Avoid_: interaction, request, call

### Verbs

**Injection**:
Sending a rule's text into the model's context because a matching file was
touched. Happens once per rule version per session, then again after the
context has moved on by `remember_again_after`.
_Avoid_: loading, activation, firing (reserved for globs matching)

**Block**:
A global-scope rule refusing a write to the paths it covers, with the rule's
own text as the reason shown. A project-scope block is inert.
_Avoid_: enforce, deny (the native permissions vocabulary it is built on)

**Verification**:
A command a rule declares with `verify:`, run at the end of a turn in which a
matching file was written. A failing verification holds the turn open and
returns the command's output to Claude. Both scopes may declare one.
_Avoid_: validation (the `validate` subcommand checks rule files, not code),
check, test hook

**Path-scoped policy**:
The three verbs a rule can carry, all selected by path: inject, block, verify.
_Avoid_: by-hook (the hook is the mechanism, the path is the selector)
