"""`rename <rule> <new-name>`: the same rule under another file name, in the
same scope, history and all.

The file is copied byte for byte, so nothing about the rule changes — not its
state (a disabled rule stays disabled), not its globs, so no settings line
moves — and the usage history follows it to the new name. Written in the
order `carry.py` describes, the old file last."""

import os

from .carry import (DEST_NEW, DEST_OVERWRITE, DEST_SAME, carry_history,
                    clear_overwritten, discard_history, plan_destination)
from .common import (HOOK, atomic_write, existing_rule_path, fail,
                     read_regular_file, rule_path, scope_for)
from .config import config_for, resolve_type
from .lifecycle import holder, sync_lines
from .validate import validate_scope

# ---- user-visible text ------------------------------------------------------
ERROR_SAME_NAME = "{name} already has that name"
ERROR_NOT_A_RULE = "{name} is not a rule (no frontmatter); nothing to rename"
MESSAGE_RENAMED = "ok: renamed {old} -> {new}"
# -----------------------------------------------------------------------------


def cmd_rename(args):
    scope_dir, anchor = scope_for(args)
    config = config_for(args)
    old_name = args.rule
    source_path = existing_rule_path(scope_dir, old_name)
    _prefix, new_name = resolve_type(config, None, args.new_name)
    target = rule_path(scope_dir, new_name)
    if new_name == old_name:
        fail(ERROR_SAME_NAME.format(name=old_name))
    # Read whole and written with `newline=""`: the copy is exactly the file.
    text = read_regular_file(source_path, os.path.getsize(source_path), newline="")
    fields, _body = HOOK.parse_frontmatter(text)
    if not fields:
        fail(ERROR_NOT_A_RULE.format(name=old_name))

    state = plan_destination(target, text, new_name, "this", args.force, newline="")
    if state == DEST_OVERWRITE:
        clear_overwritten(args.use_global, scope_dir, anchor, new_name)
    elif state == DEST_NEW:
        discard_history(scope_dir, new_name)
    if state != DEST_SAME:
        atomic_write(target, text, newline="")
    sync_lines(args.use_global, scope_dir, anchor,
               [holder(old_name, fields)], [holder(new_name, fields)])
    carry_history(scope_dir, old_name, scope_dir, new_name)
    os.unlink(source_path)

    print(MESSAGE_RENAMED.format(old=old_name, new=new_name))
    validate_scope(scope_dir, anchor, quiet=True, config=config,
                   is_global=args.use_global)
