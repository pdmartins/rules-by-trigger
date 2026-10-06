"""The terminal block the USER sees when a tool call injects rules: a header
line, then one line per rule.

`additionalContext` is what the model reads; this is what the human watching
the terminal reads, and the two must never be confused with one another. The
model already knows what it received — it is right there in its own context —
so telling it again would spend tokens on something it does not need. The
human has no such view: the injection happens inside a hook, off to the side
of the transcript they are reading, and without this block the only way they
learn a rule fired is by noticing the model behave as if it had read one.

Carried on `systemMessage`, which Claude Code documents as a "Warning message
shown to the user". The docs do not say outright that the model never sees
it, and that was not measured here; what this module does guarantee is that
`additionalContext`, the field the model is given, is the same with or
without the notice. (An ASYNC hook's `systemMessage` goes to Claude instead,
per the same docs; this hook is synchronous.) The harness prefixes the message
with `PreToolUse:<Tool> says:`, a prefix that cannot be suppressed, so the
notice does not repeat what it already says.
ANSI SGR colour codes survive that print path; cursor-movement sequences do
not, which is why a leading `\\n` is what moves the coloured block onto its
own line instead of trying to overwrite the prefix. Each line carries its own
colour and reset, so no SGR sequence spans a newline."""

from .constants import NOTICE_MARKER
from .messages import (NOTICE_COLOUR, NOTICE_COLOUR_RESET, NOTICE_GLOBAL_KEY,
                       NOTICE_NEW_VERSION_KEY, NOTICE_REPEAT_KEY,
                       NOTICE_RULE_BULLET, NOTICE_SUBAGENT_KEY)
from .context import build_context

# Layout of the block: it opens on its own line, lines are joined by a
# newline, and every line is padded by one space on both sides inside its
# colour. A rule's line is indented one space beyond that padding, so its
# text sits two spaces in, one deeper than the header's. The verification block
# of the Stop hook (see `verifyreport.py`) uses this same layout.
NOTICE_BLOCK_START = "\n"
NOTICE_LINE_SEPARATOR = "\n"
NOTICE_ITEM_INDENT = " "
NOTICE_LINE_PADDING = " "


def rule_blocks_of(blocks):
    """The blocks that are actually rules, in the order they were injected.

    Only a rule block carries `scope_dir` (see `build_blocks`); the
    legacy-format notice rides in the same list but names no rule and must
    never be reported as if it were one."""
    return [block for block in blocks if "scope_dir" in block]


def notice_name(block, messages):
    """One rule's name in the notice: its name, plus the one suffix
    that applies to THIS block — `repeat` and `superseded` are set per block
    by `build_blocks` and never both at once, so at most one suffix is ever
    added here, per rule, regardless of what the rest of the call injected."""
    if block.get("repeat"):
        return f"{block['name']} {messages[NOTICE_REPEAT_KEY]}"
    if block.get("superseded"):
        return f"{block['name']} {messages[NOTICE_NEW_VERSION_KEY]}"
    return block["name"]


def notice_item(block, messages):
    """One rule's whole line, before colouring: indent, bullet, the tag that
    marks a rule from the global scope (project rules carry none), then the
    name with its own suffix. The tag sits before the name so the suffix
    stays the last thing on the line."""
    tag = f"{messages[NOTICE_GLOBAL_KEY]} " if block.get("is_global") else ""
    return (f"{NOTICE_ITEM_INDENT}{NOTICE_RULE_BULLET} {tag}"
            f"{notice_name(block, messages)}")


def coloured_line(text, colour=NOTICE_COLOUR):
    """One line of a block inside its own colour and reset, padded by one
    space on each side. Colouring line by line keeps every SGR sequence
    inside a single line, whatever the renderer does at a newline. The colour
    is the notice's unless the caller (the verification block) brings its own."""
    return (f"{colour}{NOTICE_LINE_PADDING}{text}"
            f"{NOTICE_LINE_PADDING}{NOTICE_COLOUR_RESET}")


def coloured_block(lines, colour=NOTICE_COLOUR):
    """The block the user sees: each of `lines` (the header first) coloured on
    its own, joined by a newline with none at the end, and preceded by a
    newline so the block begins on its own line. The one place that layout is
    written down, shared by the injection notice and the verification block."""
    return NOTICE_BLOCK_START + NOTICE_LINE_SEPARATOR.join(
        coloured_line(line, colour) for line in lines)


def build_notice_line(blocks, messages, in_subagent):
    """The coloured block for the terminal, or None when this call injected no
    rule — a legacy-format notice alone says nothing here (see
    `rule_blocks_of`), and neither does an empty call.

    The block is a header line (`NOTICE_MARKER`) followed by one line per
    rule, in block order: the bullet, `[global]` when the rule comes from the
    global scope, the rule's name and its own `(repeat)` / `(new version)`
    suffix. The subagent marker, when this call
    ran inside one, applies to the whole block and so is added once, at the
    end of the header, rather than to each rule. Lines are joined by a
    newline with none at the end, and the block starts with a newline so it
    begins on its own line."""
    rules = rule_blocks_of(blocks)
    if not rules:
        return None
    header = NOTICE_MARKER
    if in_subagent:
        header = f"{header} {messages[NOTICE_SUBAGENT_KEY]}"
    lines = [header] + [notice_item(block, messages) for block in rules]
    return coloured_block(lines)


def build_pretooluse_output(blocks, messages, in_subagent, show_injections):
    """The whole PreToolUse payload: `additionalContext` for the model,
    unconditionally, and `systemMessage` for the user, only when the
    configuration allows it and this call actually injected a rule.

    `additionalContext` never depends on `show_injections` — the setting is
    about what the user sees on screen, and must never change what reaches
    the model, which is the whole point of keeping the two on separate
    fields instead of folding the notice into the context text."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": build_context(blocks, messages),
        },
    }
    if show_injections:
        notice = build_notice_line(blocks, messages, in_subagent)
        if notice:
            output["systemMessage"] = notice
    return output
