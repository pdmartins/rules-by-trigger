"""`validate`: everything that can be said about a scope without changing it.

Notes are advice — a long rule, a shared glob, a rule that looks like it wants
to be split. Errors mean something will not work at all."""

import os
import re
import sys

from .common import (BLOCK_KEY, EXCLUDE_KEY, HOOK, INTERVAL_KEY,
                     LEGACY_BLOCK_KEY, LEGACY_INTERVAL_KEY, LEGACY_MAP_NAME,
                     OWN_KEYS, TOOL_KEY, VERIFY_KEY, call_problem,
                     other_markdown_in, rules_in, scope_for)
from .config import TYPE_SEPARATOR, config_for, name_convention, split_type_prefix
from .splitting import split_candidates

# The keys that only ever narrow a PATH trigger — declared on a rule with
# calls and no glob, they are dead weight rather than a mistake: `validate`
# says so as a note, not an error, because the rule still fires on its calls.
IRRELEVANT_ON_CALL_ONLY_KEYS = (set(HOOK.EXCLUDE_KEYS) | set(HOOK.TOOL_KEYS)
                                | {BLOCK_KEY, LEGACY_BLOCK_KEY, VERIFY_KEY})

# Case-insensitive: a rule stating a prohibition needs the opposite
# reinforcement default from one stating a requirement or convention — only
# prohibition-shaped constraints are known to decay under long context
# (arXiv:2604.20911). Advice for a human reading `validate`, never a judgement
# injected by the hook, which never looks at a rule's own text this way.
PROHIBITION_PATTERN = re.compile(
    r"never|do not|don't|must not|forbidden|nunca|não (deve|pode)|proibido",
    re.IGNORECASE)
# A repeat this tight, on a rule with no prohibition language at all, is more
# often a copy-pasted interval than a deliberate choice.
AGGRESSIVE_INTERVAL_TOKENS = 10_000
AGGRESSIVE_INTERVAL_CALLS = 10
# The glob that matches every path: as an `exclude` it is not a filter, it is
# an off switch, and one nothing in the rule says out loud.
MATCH_EVERYTHING_GLOB = "**"


def effective_interval(name, fields, config):
    """(value, unit) this rule would actually repeat at, following the same
    precedence the hook applies: the rule's own `remember_again_after`, else
    its type's default. Returns None when neither says anything — the
    session/global default then applies, and that is a property of the
    session, not of this rule, so there is nothing here worth a note about."""
    own = HOOK.remember_again_after_of(fields)
    if own is not None:
        return own
    prefix, _rest = split_type_prefix(name, config)
    type_default = HOOK.remember_again_after_for_type(config, prefix) if prefix else None
    return HOOK.parse_remember_again_after(type_default, name) if type_default else None


def filter_problems(globs, excludes, calls=()):
    """(problems, notes) — reasons a rule's own filters mean its globs can
    never match, unprefixed: the caller names the rule, because `render_rule`
    uses this to refuse to WRITE one of these and has no name to give.

    Only the two decidable cases are reported — a glob cancelled by an identical
    exclude, and an exclude that swallows every path. Anything subtler is a
    judgement, and `which` answers it for a concrete path.

    `exclude:` only ever concerns PATH matching (see the frontmatter contract),
    so a rule that also carries a `call:` still fires on that regardless of
    what its excludes did to its globs — "can never inject" would be simply
    wrong for it. A call-only rule (no globs at all) says nothing here: the
    exclude is inert, and the call-only irrelevant-key note already says so.
    A glob+call rule is reported as a NOTE instead, naming what still fires
    it — never a `problems` entry, so it never costs the exit code."""
    if not excludes:
        return [], []
    if MATCH_EVERYTHING_GLOB in excludes:
        dead = (f"{EXCLUDE_KEY}: {MATCH_EVERYTHING_GLOB!r} takes back every "
                f"path")
    elif globs and set(globs) <= set(excludes):
        dead = f"every glob it declares is also an {EXCLUDE_KEY}"
    else:
        return [], []
    if not calls:
        return [f"{dead}, so the rule can never inject"], []
    if globs:
        return [], [f"{dead}, so its globs never match — it only fires on "
                    f"its call"]
    return [], []


def filter_notes(name, fields):
    """Notes about a rule's `tool:` filter: a value the hook does not
    recognise, and therefore ignores.

    Ignoring it is the deliberate choice — a filter only ever narrows a rule,
    and a typo in one must not be why a rule silently stops arriving — so this
    is where the typo is said out loud instead."""
    known = set(HOOK.TOOL_KINDS) | set(HOOK.TOOL_ANY_VALUES)
    unknown = [value for value in HOOK.tool_values_of(fields) if value not in known]
    if not unknown:
        return []
    return [f"{name}: {TOOL_KEY}: {', '.join(repr(v) for v in unknown)} "
            f"not understood — only {'/'.join(HOOK.TOOL_KINDS)} are honoured; "
            f"ignored, so the rule applies to every tool call it matches"]


def block_notes(name, fields, is_global, globs=()):
    """Notes about a rule's `block:` setting: the spelling it carried until
    0.7.0, a value the hook does not recognise, or a block declared somewhere
    the hook will never honour it.

    A block only ever binds from the GLOBAL scope (see `HOOK.blocking_rule`): a
    project rule arrives with whatever repository is checked out, and letting it
    deny the user's own tool calls would be an escalation. This is where that
    trust gate is explained to a human, with the way around it — a native deny
    via `block --sync` — spelled out.

    `globs` is only consulted for that last part: `block --sync` writes one
    native deny entry per glob, so a call-only rule (no globs at all) has
    nothing for it to sync — pointing at it would be advice that does
    nothing. The call-only irrelevant-key note (see `scope_findings`) already
    says `block:` has no effect here at all, so this stays silent rather than
    repeat that in a narrower, and in this one case misleading, way."""
    declared = HOOK.first_value(fields, BLOCK_KEY)
    legacy = HOOK.first_value(fields, LEGACY_BLOCK_KEY)
    if declared is None and legacy is None:
        return []
    notes = []
    if declared is None:
        notes.append(f"{name}: {LEGACY_BLOCK_KEY}: {HOOK.LEGACY_BLOCK_VALUE} is "
                     f"the spelling this setting carried until 0.7.0 — still "
                     f"honoured, but `{BLOCK_KEY}: {HOOK.BLOCK_TRUE_VALUES[0]}` "
                     f"is the name now; `migrate` rewrites it")
    if not HOOK.block_of(fields):
        key = LEGACY_BLOCK_KEY if declared is None else BLOCK_KEY
        raw = legacy if declared is None else declared
        accepted = "/".join(HOOK.BLOCK_TRUE_VALUES) if declared is not None \
            else HOOK.LEGACY_BLOCK_VALUE
        return notes + [f"{name}: {key}: {str(raw)[:32]!r} is not understood — "
                        f"only {accepted} turns a block on; ignored"]
    if HOOK.tools_of(fields) == (HOOK.TOOL_KIND_READ,):
        # Both settings are honoured, and together they cancel: a block only
        # ever fires on a write, and this rule has just excused itself from
        # every write there is.
        return notes + [f"{name}: {BLOCK_KEY} on a {TOOL_KEY}: "
                        f"{HOOK.TOOL_KIND_READ} rule never fires — a block only "
                        f"ever acts on a write, and reads are never blocked"]
    if not is_global and not globs:
        return notes
    if not is_global:
        return notes + [f"{name}: {BLOCK_KEY} only takes effect from the GLOBAL "
                        f"scope (project rules are untrusted input); the hook "
                        f"ignores it here. Run `block --sync` to write an "
                        f"equivalent native deny into this project's "
                        f"permissions instead"]
    return notes


def verify_notes(name, fields, is_global=False):
    """Notes about a rule's `verify:` key: a command the hook would drop, and
    the two settings a verification sits badly beside.

    The RAW field is read rather than `HOOK.verify_of`, because that parser's
    whole job is to hand the hook only the commands it can run — what it
    dropped is exactly what a human needs told, and once it has run an absent
    key and a `verify:` with nothing under it look identical.

    `is_global` decides whether a `block:` on the same rule actually binds —
    see `block_notes` — and defaults to False, so a caller that has not been
    updated to pass it loses that one note rather than claiming a project block
    stops a write it does not stop."""
    if VERIFY_KEY not in fields:
        return []
    raw = fields[VERIFY_KEY]
    declared = [str(value).strip() for value in
                (raw if isinstance(raw, list) else [raw])]
    if declared and all(value.lower() == HOOK.VERIFY_NONE for value in declared):
        return []  # the word that turns the key off, said out loud
    # A quoted blank (`verify: "  "`) reaches here as an item the parser kept
    # and the hook drops, which is why an emptiness test cannot be `not raw`.
    blank = [value for value in declared if not value]
    if len(blank) == len(declared):
        return [f"{name}: {VERIFY_KEY}: declared with no command, so nothing is "
                f"run — give it one or remove the key"]
    notes = []
    if blank:
        notes.append(f"{name}: {VERIFY_KEY}: {len(blank)} blank command(s) are "
                     f"ignored — an empty entry runs nothing; remove it")
    too_long = [value for value in declared
                if len(value) > HOOK.MAX_VERIFY_COMMAND_CHARS]
    if too_long:
        notes.append(f"{name}: {VERIFY_KEY}: {len(too_long)} command(s) over "
                     f"{HOOK.MAX_VERIFY_COMMAND_CHARS} characters are ignored "
                     f"({too_long[0][:60]!r}...) — a command that long belongs "
                     f"in a script, and the script is what to verify")
    kept = [value for value in declared
            if value and len(value) <= HOOK.MAX_VERIFY_COMMAND_CHARS]
    if len(kept) > HOOK.MAX_VERIFY_COMMANDS:
        notes.append(f"{name}: {VERIFY_KEY}: {len(kept)} commands, and only the "
                     f"first {HOOK.MAX_VERIFY_COMMANDS} are run — chain them "
                     f"into one command, or split the rule")
    if HOOK.tools_of(fields) == (HOOK.TOOL_KIND_READ,):
        # NOT the same shape as the `block:` conflict below: the hook ignores
        # `tool:` when it verifies, because a WRITE is the trigger. The command
        # still runs; what the filter cancels is the guidance beside it.
        notes.append(f"{name}: {VERIFY_KEY} with {TOOL_KEY}: "
                     f"{HOOK.TOOL_KIND_READ} — the verification still runs, "
                     f"since a write is what triggers it and the {TOOL_KEY} "
                     f"filter narrows only the injection; but the rule's own "
                     f"text never reaches Claude for that write, so the check "
                     f"fails with its guidance unread")
    if is_global and HOOK.block_of(fields):
        notes.append(f"{name}: {VERIFY_KEY} and {BLOCK_KEY} pull against each "
                     f"other — the block refuses every write this rule covers, "
                     f"so the verification has nothing new to check")
    return notes


def reinforcement_notes(name, body, fields, config):
    """Notes about a mismatch between what a rule's text asks for and how
    often it is set to repeat (own frontmatter or inherited type default):
    a prohibition with reinforcement off, or a non-prohibition reinforced as
    tightly as one — never an error, since both are legitimate choices."""
    if not body:
        return []
    interval = effective_interval(name, fields, config)
    if interval is None:
        return []
    value, unit = interval
    prohibits = bool(PROHIBITION_PATTERN.search(body))
    if prohibits and not value:
        return [f"{name}: reads like a prohibition but remember_again_after "
                f"is 'never' — only prohibition constraints are known to decay "
                f"under long context; consider giving it a repeat distance"]
    aggressive = (unit == "tokens" and value < AGGRESSIVE_INTERVAL_TOKENS) or \
                 (unit == "calls" and value < AGGRESSIVE_INTERVAL_CALLS)
    if not prohibits and value and aggressive:
        return [f"{name}: repeats every {value} {unit} with no prohibition "
                f"language in its body — requirements and conventions hold up "
                f"without reinforcement; this may be over-treatment"]
    return []


def scope_findings(scope_dir, anchor=None, config=None, is_global=False):
    """(notes, problems, rule count) for a scope directory that exists. Notes
    are advice (a long rule, a shared glob, a rule that looks like it should
    be split); problems mean something will not work.

    Computed here and printed by `validate_scope`, so `status` can fold the
    same findings into its own report without capturing another command's
    output.

    `is_global` decides whether a `block: true` rule here would actually be
    honoured by the hook — see `block_notes` — and defaults to False so a
    caller that has not been updated to pass it merely loses that one note
    rather than misreporting a global scope as a project one."""
    config = config or {}
    problems = []
    notes = []
    chosen = HOOK.language(config)
    if not HOOK.has_translation(chosen):
        # Not an error: rules are written in whatever language is configured,
        # and only the plugin's own scaffolding around them needs a translation
        # a human wrote. Said here so the fallback is never a surprise.
        notes.append(f"language is {chosen!r}, which the plugin ships no "
                     f"translation of: rules are written in it, but the text "
                     f"the hook injects around them stays in "
                     f"{HOOK.DEFAULT_LANGUAGE} (shipped: "
                     f"{', '.join(HOOK.SHIPPED_LANGUAGES)})")
    if HOOK.has_legacy_map(scope_dir):
        problems.append(f"a legacy {LEGACY_MAP_NAME} is present and is NOT used; "
                        f"run `migrate` to convert it")
    soft_limit = HOOK.warn_rule_chars(config)
    hard_limit = HOOK.max_rule_chars(config)
    rules = rules_in(scope_dir, hard_limit)
    by_glob = {}
    total = 0
    for name, fields, body in rules:
        globs = HOOK.globs_of(fields)
        calls = HOOK.call_values_of(fields)
        excludes = HOOK.excludes_of(fields)
        if not globs and not calls:
            problems.append(f"{name}: no glob and no call declared, so it can "
                            f"never be injected")
        filter_errors, exclude_notes = filter_problems(globs, excludes, calls)
        problems.extend(f"{name}: {reason}" for reason in filter_errors)
        notes.extend(f"{name}: {reason}" for reason in exclude_notes)
        for value in calls:
            problem = call_problem(value)
            if problem:
                problems.append(f"{name}: {problem}")
        if calls and not globs:
            irrelevant = sorted(set(fields) & IRRELEVANT_ON_CALL_ONLY_KEYS)
            if irrelevant:
                notes.append(f"{name}: {', '.join(irrelevant)} only applies to "
                             f"a path trigger ({HOOK.GLOB_KEYS[0]}:); it has no "
                             f"effect on this call-only rule")
        for glob in globs:
            by_glob.setdefault(glob, []).append(name)
        if not body:
            problems.append(f"{name}: empty body")
        total += len(body)
        if len(body) > soft_limit:
            notes.append(f"{name}: {len(body)} chars — a rule should state "
                         f"constraints, not document behaviour (soft limit "
                         f"{soft_limit}, truncated at {hard_limit})")
        unknown = set(fields) - OWN_KEYS
        if unknown:
            notes.append(f"{name}: unknown frontmatter key(s): "
                         f"{', '.join(sorted(unknown))}")
        if LEGACY_INTERVAL_KEY in fields and INTERVAL_KEY not in fields:
            notes.append(f"{name}: uses `{LEGACY_INTERVAL_KEY}:`, renamed to "
                         f"`{INTERVAL_KEY}:` in 0.4.0. It is still honoured; "
                         f"`migrate` rewrites it")
        notes.extend(split_candidates(name, globs, body, anchor))
        notes.extend(reinforcement_notes(name, body, fields, config))
        notes.extend(filter_notes(name, fields))
        notes.extend(block_notes(name, fields, is_global, globs))
        notes.extend(verify_notes(name, fields, is_global))
    convention = name_convention(config)
    off_convention = []
    if convention:
        off_convention = [name for name, _f, _b in rules
                          if not convention.match(name)]
    if off_convention:
        notes.append(f"name(s) outside the `TYPE{TYPE_SEPARATOR}what-it-asserts.md` "
                     f"convention: {', '.join(off_convention)} — the type prefix "
                     f"({'/'.join(HOOK.type_prefixes(config))}) says what "
                     f"violating the rule costs, and the rest should assert what "
                     f"the rule requires. These still load; renaming is a "
                     f"curation choice")
    others = other_markdown_in(scope_dir)
    if others:
        notes.append(f"ignored (no frontmatter, so not rules): {', '.join(others)}")
    for glob, names in sorted(by_glob.items()):
        if len(names) > 1:
            notes.append(f"{len(names)} rules share the glob {glob!r} "
                         f"({', '.join(names)}) — they all inject together")
    if total > HOOK.MAX_TOTAL_CHARS:
        notes.append(f"rules total {total} chars; one injection is capped at "
                     f"{HOOK.MAX_TOTAL_CHARS}, so a file matching many of them "
                     f"gets the rest on later tool calls")
    return notes, problems, len(rules)


def validate_scope(scope_dir, anchor=None, quiet=False, config=None, is_global=False):
    """Print notes and errors; return the number of errors."""
    if not os.path.isdir(scope_dir):
        if not quiet:
            print("(no rules in this scope — nothing to validate)")
        return 0
    notes, problems, rule_count = scope_findings(scope_dir, anchor, config,
                                                 is_global)
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if not quiet and not problems:
        print(f"validation ok: {rule_count} rule(s)")
    return len(problems)


def cmd_validate(args):
    scope_dir, anchor = scope_for(args)
    if validate_scope(scope_dir, anchor, config=config_for(args),
                      is_global=args.use_global):
        sys.exit(1)
