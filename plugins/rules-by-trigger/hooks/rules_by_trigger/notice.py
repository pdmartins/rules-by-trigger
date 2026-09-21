"""The terminal line the USER sees when a tool call injects rules.

`additionalContext` is what the model reads; this is what the human watching
the terminal reads, and the two must never be confused with one another. The
model already knows what it received — it is right there in its own context —
so telling it again would spend tokens on something it does not need. The
human has no such view: the injection happens inside a hook, off to the side
of the transcript they are reading, and without this line the only way they
learn a rule fired is by noticing the model behave as if it had read one.

Carried on `systemMessage`, which Claude Code documents as a "Warning message
shown to the user". The docs do not say outright that the model never sees
it, and that was not measured here; what this module does guarantee is that
`additionalContext`, the field the model is given, is the same with or
without the notice. (An ASYNC hook's `systemMessage` goes to Claude instead,
per the same docs; this hook is synchronous.) The harness prefixes the line
with `PreToolUse says: `, a prefix that cannot be suppressed, so the notice
does not repeat what it already says.
ANSI SGR colour codes survive that print path; cursor-movement sequences do
not, which is why a leading `\\n` is what moves the coloured block onto its
own line instead of trying to overwrite the prefix."""

from .constants import NOTICE_MARKER
from .messages import (NOTICE_COLOUR, NOTICE_COLOUR_RESET,
                       NOTICE_NEW_VERSION_KEY, NOTICE_REPEAT_KEY,
                       NOTICE_SUBAGENT_KEY)
from .context import build_context


def rule_blocks_of(blocks):
    """The blocks that are actually rules, in the order they were injected.

    Only a rule block carries `scope_dir` (see `build_blocks`); the
    legacy-format notice rides in the same list but names no rule and must
    never be reported as if it were one."""
    return [block for block in blocks if "scope_dir" in block]


def notice_name(block, messages):
    """One rule's entry in the notice line: its name, plus the one suffix
    that applies to THIS block — `repeat` and `superseded` are set per block
    by `build_blocks` and never both at once, so at most one suffix is ever
    added here, per rule, regardless of what the rest of the call injected."""
    if block.get("repeat"):
        return f"{block['name']} {messages[NOTICE_REPEAT_KEY]}"
    if block.get("superseded"):
        return f"{block['name']} {messages[NOTICE_NEW_VERSION_KEY]}"
    return block["name"]


def build_notice_line(blocks, messages, in_subagent):
    """The coloured line for the terminal, or None when this call injected no
    rule — a legacy-format notice alone says nothing here (see
    `rule_blocks_of`), and neither does an empty call.

    Rule names appear in block order, comma-separated, each carrying its own
    `(repeat)` / `(new version)` suffix; the subagent marker, when this call
    ran inside one, applies to the whole line and so is added once, at the
    end, rather than to each name."""
    rules = rule_blocks_of(blocks)
    if not rules:
        return None
    names = ", ".join(notice_name(block, messages) for block in rules)
    line = f"{NOTICE_MARKER} {names}"
    if in_subagent:
        line = f"{line} {messages[NOTICE_SUBAGENT_KEY]}"
    return f"\n{NOTICE_COLOUR} {line} {NOTICE_COLOUR_RESET}"


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
        "suppressOutput": True,
    }
    if show_injections:
        notice = build_notice_line(blocks, messages, in_subagent)
        if notice:
            output["systemMessage"] = notice
    return output
