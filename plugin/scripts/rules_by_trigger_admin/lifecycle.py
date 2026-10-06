"""A rule's life after it is written: switching it off and on, deleting it, and
refusing to create a twin of one that is switched off.

`remove` disables by default and deletes only with `--delete`; `update
--enable` switches a rule back on. Each of those touches more than one thing —
the rule file, the project's `.claude/settings.json` (the `Edit(<glob>)` lines
a `block: true` project rule needs) and the usage stats — so each runs in a
fixed order, and re-running it after a failure finishes the job:

    disable  rule file (`enabled: false`), then the settings lines
    enable   rule file (drop `enabled`), then the settings lines
    delete   the settings lines, then the usage stats, the rule file LAST — it
             is what names the globs, so it stays until nothing needs them

Global rules have no lines: the hook blocks for them directly, so switching
one off stops the block at once and there is nothing to write."""

import os

from .block import (SETTINGS_RELPATH, apply_deny_lines, blocking_rules,
                    deny_entries)
from .common import (ENABLED_KEY, HOOK, atomic_write, fail, read_regular_file,
                     rule_path, rules_in, scope_for)

BOM = "﻿"
FRONTMATTER_FENCE = "---"

# --- Everything the user reads from this module --------------------------------
MESSAGE_DISABLED = ("ok: disabled {name} (kept, with its history); "
                    "`update --rule {name} --enable` switches it back on, "
                    "`remove --delete` deletes it")
MESSAGE_DELETED = "ok: removed {name}"
MESSAGE_ENABLED = "ok: enabled {name}"
MESSAGE_LINES_REMOVED = "ok: {count} deny line(s) removed from {path}: {entries}"
MESSAGE_LINES_ADDED = "ok: {count} deny line(s) written to {path}: {entries}"
ERROR_NO_GLOB_MATCH = "no rule declares the glob {glob!r}"
ERROR_AMBIGUOUS_GLOB = ("{count} rules declare that glob ({names}); pick one "
                        "with --rule")
ERROR_SYMLINK = "{name} is a symlink; refusing to delete through it"
ERROR_NO_SUCH_RULE = "no such rule in this scope: {name}"
ERROR_NOT_A_RULE = ("{name} is not a rule (no frontmatter), so it has no state "
                    "to change; `remove --delete` deletes the file")
ERROR_CANNOT_READ = "cannot read {name}"
ERROR_STATS = ("the usage stats of {name} could not be updated, so the rule was "
               "NOT deleted; run the same command again")
ERROR_ENABLE_ALONE = ("`update --enable` without a body only switches the rule "
                      "on; to change {flags} send the rule's body on stdin too")
ERROR_ENABLED_SUBMITTED = ("the submitted rule carries an `{key}:` line that "
                           "would change whether the rule is on, and that has "
                           "to move the settings lines too: drop the `{key}:` "
                           "line (the current state is kept), or use `remove` "
                           "to disable and `update --enable` to enable")
ERROR_ADD_ENABLED = ("a new rule cannot carry an `{key}:` line; create it, then "
                     "use `remove` to disable it")
ERROR_DUPLICATE = ("refusing to add: the disabled rule {names} in this scope "
                   "already declares {what}. Switch it back on with `update "
                   "--rule {first} --enable`, or pass --allow-duplicate to "
                   "create this one anyway")
WHAT_GLOB = "the glob {glob!r}"
WHAT_CALL = "the call trigger {call!r}"
# (flag as typed, attribute of the parsed arguments) — what `update` can change
FLAGS_THAT_CHANGE_A_RULE = (("--glob", "glob"), ("--call", "call"),
                            ("--exclude", "exclude"), ("--tool", "tool"),
                            ("--verify", "verify"),
                            ("--remember-again-after", "remember_again_after"))
# --------------------------------------------------------------------------------


def with_enabled(text, enabled):
    """`text`, a rule file, with its `enabled:` line removed and — to disable —
    written again as `enabled: false` at the end of the frontmatter. Nothing
    else in the file changes, which is why this edits the text instead of
    rendering the rule again: switching a rule off must work on a rule whose
    other settings `render_rule` would refuse, and must not rewrite its body.

    Reads the frontmatter the way `parse_frontmatter` does: a `- ` line is an
    item of the key above it and is never a key itself."""
    bom = BOM if text.startswith(BOM) else ""
    text = text[len(bom):]
    lines = text.split("\n")
    end = next((index for index in range(1, len(lines))
                if lines[index].strip() == FRONTMATTER_FENCE), None)
    if not text.startswith(FRONTMATTER_FENCE) or end is None:
        fail("the rule has no closing '---' in its frontmatter")
    # Each line keeps its own ending (a CRLF file leaves a "\r" on every one),
    # so the line written below borrows the opening fence's.
    ending = "\r" if lines[0].endswith("\r") else ""
    kept = []
    inside_enabled = False
    for line in lines[1:end]:
        stripped = line.strip()
        if stripped.startswith("- "):
            if not inside_enabled:
                kept.append(line)
            continue
        inside_enabled = stripped.partition(":")[0].strip() == ENABLED_KEY
        if not inside_enabled:
            kept.append(line)
    if not enabled:
        kept.append(f"{ENABLED_KEY}: {HOOK.ENABLED_FALSE}{ending}")
    return bom + "\n".join([lines[0], *kept, *lines[end:]])


def rewrite_enabled(path, enabled):
    """Write the rule at `path` with `enabled` switched, whole file read."""
    text = read_regular_file(path, os.path.getsize(path), newline="")
    atomic_write(path, with_enabled(text, enabled), newline="")


def settings_path_of(anchor):
    return os.path.join(anchor, SETTINGS_RELPATH)


def release_lines(args, scope_dir, anchor, name, fields):
    """Take out of the project's settings the `Edit(<glob>)` lines of a
    `block: true` project rule's globs that no OTHER active `block: true` rule
    of the same project still asks for. A line already gone is not an error."""
    if args.use_global or not HOOK.block_of(fields):
        return
    still_needed = deny_entries(blocking_rules(scope_dir, skip_name=name))
    mine = [entry for entry in deny_entries([(name, HOOK.globs_of(fields), [])])
            if entry not in still_needed]
    _added, removed = apply_deny_lines(anchor, remove=mine)
    if removed:
        print(MESSAGE_LINES_REMOVED.format(
            count=len(removed), path=settings_path_of(anchor),
            entries=", ".join(removed)))


def restore_lines(args, anchor, fields, globs):
    """Put back the `Edit(<glob>)` lines of a `block: true` project rule that is
    switched on, for the globs it has NOW. Nothing records which lines an
    earlier sync wrote, so this adds them even for a rule never synced."""
    if args.use_global or not HOOK.block_of(fields):
        return
    added, _removed = apply_deny_lines(
        anchor, add=deny_entries([(None, globs, [])]))
    if added:
        print(MESSAGE_LINES_ADDED.format(
            count=len(added), path=settings_path_of(anchor),
            entries=", ".join(added)))


def holder(name, fields, globs=None):
    """(name, globs) a rule contributes to the project's deny lines: its globs
    (or `globs`, the ones it is about to have) while it is active and carries
    `block: true`, none otherwise — a disabled rule holds no line."""
    if not (HOOK.block_of(fields) and HOOK.is_enabled(fields)):
        return name, []
    return name, list(HOOK.globs_of(fields) if globs is None else globs)


def sync_lines(is_global, scope_dir, anchor, before, after):
    """Bring the project's `Edit(<glob>)` lines in line with an operation that
    changes rules of `scope_dir`: `before` and `after` are `holder` results for
    the rules the operation replaces or removes, as they are now, and for the
    rules it leaves in their place.

    A line goes when only `before` asked for it — no other active `block: true`
    rule of the project, and nothing in `after`, still does; a line comes when
    only `after` asks for it. A glob a rule already held is neither taken out
    nor put back, so a rule never synced stays as it was. Idempotent, like
    `apply_deny_lines`: the same call after a failed run finishes the job.
    The global scope has no lines (the hook blocks for it directly)."""
    if is_global:
        return
    changing = {name for name, _globs in (*before, *after)}
    others = [rule for rule in blocking_rules(scope_dir) if rule[0] not in changing]
    held = deny_entries([(name, globs, []) for name, globs in before])
    wanted = deny_entries([(name, globs, []) for name, globs in after])
    still_needed = deny_entries(others) + wanted
    added, removed = apply_deny_lines(
        anchor, add=[entry for entry in wanted if entry not in held],
        remove=[entry for entry in held if entry not in still_needed])
    for message, entries in ((MESSAGE_LINES_ADDED, added),
                             (MESSAGE_LINES_REMOVED, removed)):
        if entries:
            print(message.format(count=len(entries), path=settings_path_of(anchor),
                                 entries=", ".join(entries)))


def rule_to_remove(scope_dir, args):
    """The rule name `remove` was pointed at, by name or by its one glob."""
    if args.rule:
        return args.rule
    matches = [name for name, fields, _body in rules_in(scope_dir)
               if args.glob in HOOK.globs_of(fields)]
    if not matches:
        fail(ERROR_NO_GLOB_MATCH.format(glob=args.glob))
    if len(matches) > 1:
        fail(ERROR_AMBIGUOUS_GLOB.format(count=len(matches),
                                         names=", ".join(matches)))
    return matches[0]


def cmd_remove(args):
    scope_dir, anchor = scope_for(args)
    name = rule_to_remove(scope_dir, args)
    path = rule_path(scope_dir, name)
    # Before either branch: switching a rule off rewrites the file, and writing
    # through a link would let a cloned repository pick which file changes.
    if os.path.islink(path):
        fail(ERROR_SYMLINK.format(name=name))
    if not os.path.isfile(path):
        fail(ERROR_NO_SUCH_RULE.format(name=name))
    result = HOOK.read_rule_file(scope_dir, name, body_limit=0)
    fields = result[0] if result else {}
    if args.delete:
        delete_rule(args, scope_dir, anchor, name, path, fields)
    else:
        disable_rule(args, scope_dir, anchor, name, path, fields)


def disable_rule(args, scope_dir, anchor, name, path, fields):
    if not fields:
        fail(ERROR_NOT_A_RULE.format(name=name))
    if HOOK.enabled_word(fields) != HOOK.ENABLED_FALSE:
        rewrite_enabled(path, enabled=False)
    release_lines(args, scope_dir, anchor, name, fields)
    print(MESSAGE_DISABLED.format(name=name))


def delete_rule(args, scope_dir, anchor, name, path, fields):
    release_lines(args, scope_dir, anchor, name, fields)
    if fields and HOOK.drop_rule_usage(scope_dir, name) is None:
        fail(ERROR_STATS.format(name=name))
    os.unlink(path)
    print(MESSAGE_DELETED.format(name=name))


def enable_rule(args, scope_dir, anchor, name, path):
    """`update --enable` with no body: only the state changes, so the file is
    edited in place and the globs the rule has now decide the lines."""
    changing = [flag for flag, attribute in FLAGS_THAT_CHANGE_A_RULE
                if getattr(args, attribute, None)]
    if changing:
        fail(ERROR_ENABLE_ALONE.format(flags=", ".join(changing)))
    result = HOOK.read_rule_file(scope_dir, name, body_limit=0)
    if result is None:
        fail(ERROR_CANNOT_READ.format(name=name))
    fields = result[0]
    if not fields:
        fail(ERROR_NOT_A_RULE.format(name=name))
    if ENABLED_KEY in fields:
        rewrite_enabled(path, enabled=True)
    restore_lines(args, anchor, fields, HOOK.globs_of(fields))
    print(MESSAGE_ENABLED.format(name=name))


def refuse_submitted_enabled(submitted):
    """`add` takes no state from stdin at all."""
    if ENABLED_KEY in submitted:
        fail(ERROR_ADD_ENABLED.format(key=ENABLED_KEY))


def refuse_enabled_change(submitted, fields, enable):
    """`update` accepts an `enabled:` line on stdin only when it agrees with
    the state the rule ends up in — its current one, or active under
    `--enable` — which is what a show -> edit -> update round trip of a
    disabled rule submits. One that would flip the state is refused: the flip
    has to go through the commands that move the settings lines too."""
    if ENABLED_KEY not in submitted:
        return
    resulting = True if enable else HOOK.is_enabled(fields)
    if HOOK.is_enabled(submitted) != resulting:
        fail(ERROR_ENABLED_SUBMITTED.format(key=ENABLED_KEY))


def refuse_disabled_duplicate(scope_dir, name, globs, calls):
    """Fail when a DISABLED rule of this scope (other than the one `name` that
    is about to be overwritten) shares at least one identical glob string, or
    one call trigger, with the rule being added — naming it. Only the scope the
    new rule lands in is looked at."""
    if not os.path.isdir(scope_dir):
        return  # a scope not created yet holds no rule to duplicate
    new_calls = {HOOK.parse_call_trigger(call) or call for call in calls}
    for other, fields, _body in rules_in(scope_dir):
        if other == name or HOOK.is_enabled(fields):
            continue
        shared_glob = next((glob for glob in globs
                            if glob in HOOK.globs_of(fields)), None)
        shared_call = next(
            (text for text, tool, field, value in HOOK.calls_of(fields)
             if (tool, field, value) in new_calls), None)
        if shared_glob is not None:
            what = WHAT_GLOB.format(glob=shared_glob)
        elif shared_call is not None:
            what = WHAT_CALL.format(call=shared_call)
        else:
            continue
        fail(ERROR_DUPLICATE.format(names=other, what=what, first=other))
