"""What a rule's calls, filters and verification read like to a human: the
words `add`/`update` compute and print after writing, and the words
`list`/`status` add to an inventory line.

Kept out of rules.py so that module stays about reading and writing rule
FILES, not about how a written rule is described afterwards — and to stay
under the plugin's 400-line-per-module ceiling."""

from .common import CALL_KEY, EXCLUDE_KEY, HOOK, TOOL_KEY, VERIFY_KEY, fail

# The word that clears `call:` on `update`, exactly like `HOOK.VERIFY_NONE`
# clears `verify:` — there is no hook-side equivalent to import, because the
# hook never reads a "clear this" sentinel; only the admin writes one.
CALL_NONE = "none"
# What `list`/`status` show in place of a rule's triggers when it declares
# neither a `glob:` nor a `call:` — the one thing that makes a rule dead.
NO_TRIGGER_LABEL = "(NEITHER GLOB NOR CALL — never injected)"


def calls_for(args, source):
    """The call triggers a rule being written should carry: what `--call`
    declares, else what `source` frontmatter does — the same precedence, and
    for the same reason, as `filters_for`/`verify_for` in rules.py.

    `--call none` clears the key the way `--verify none` clears the
    verification: a rule may have neither a glob nor a call to begin with, so
    dropping the calls has to be sayable without a show -> edit -> update
    round trip. An empty `--call ''` is refused rather than silently treated
    as "clear everything" — unlike `--exclude`/`--verify`, "none" is already
    the word for that, and a blank value reaching this far is far more likely
    a mistake (a stray flag, a shell quoting slip) than an intentional wipe."""
    if args.call:
        stripped = [value.strip() for value in args.call]
        if any(not value for value in stripped):
            fail(f"'--{CALL_KEY}' cannot be empty; pass '--{CALL_KEY} "
                 f"{CALL_NONE}' to clear the calls")
        return [value for value in stripped if value.lower() != CALL_NONE]
    return HOOK.call_values_of(source)


def describe_verify(commands):
    """The lines `add` and `update` print under a written rule for its
    verification, one per command and in full: what will run at the end of a
    turn is worth reading back verbatim, and it does not fit on a shared line."""
    return [f"    {VERIFY_KEY}: {command}" for command in commands]


def verify_label(commands):
    """What a `list` line says about a rule's verification: how many commands
    there are, not what they are. An inventory is one line per rule, and a
    single command may run to MAX_VERIFY_COMMAND_CHARS — `show` is where the
    text belongs."""
    if not commands:
        return []
    plural = "" if len(commands) == 1 else "s"
    return [f"{VERIFY_KEY}: {len(commands)} cmd{plural}"]


def filter_parts(excludes, tool, calls=()):
    """The filters a rule carries, one readable phrase each — assembled once so
    every command names them the same way.

    `calls` defaults to empty: `filters_label` (the `list`/`status` bracket)
    never passes it, because those two already show the calls next to the
    globs, on the trigger line itself — repeating them here would say the same
    thing twice. `describe_filters` (the `add`/`update` confirmation, which has
    no such line) does pass it."""
    parts = []
    if calls:
        parts.append(f"{CALL_KEY}: {', '.join(calls)}")
    if excludes:
        parts.append(f"{EXCLUDE_KEY}: {', '.join(excludes)}")
    if tool:
        parts.append(f"{TOOL_KEY}: {', '.join(tool)}")
    return parts


def describe_filters(excludes, tool, calls=()):
    """The line `add` and `update` print under a written rule, or "" when it
    declares no filter and no call at all."""
    parts = filter_parts(excludes, tool, calls)
    return f"    {'  |  '.join(parts)}" if parts else ""


def filters_label(fields):
    """What a `list` line adds after the globs, or "".

    An inventory that omits them hides the two reasons a rule someone is
    looking at will not fire where its glob says it should. The tool filter is
    reported as the hook READS it, so a value the hook ignores shows as no
    filter — which is what it is; `validate` is where the typo is named. A
    verification is listed in the same slot: it is not a filter, but it is the
    other thing a rule does that its glob alone does not say."""
    parts = filter_parts(HOOK.excludes_of(fields), HOOK.tools_of(fields))
    parts.extend(verify_label(HOOK.verify_of(fields)))
    return f"  [{'; '.join(parts)}]" if parts else ""


def triggers_label(fields):
    """The globs and calls a rule fires on, joined on one line — `list`'s
    "<-" and `status`'s equivalent — or `NO_TRIGGER_LABEL` for a rule that
    declares neither and so can never be injected."""
    triggers = [*HOOK.globs_of(fields), *HOOK.call_values_of(fields)]
    return ", ".join(triggers) if triggers else NO_TRIGGER_LABEL
