"""What `rename`, `move` and `split` share: deciding what to do about a
destination that already exists, and making a rule's usage history follow the
operation.

Each of those commands writes several things, always in this order, so that a
failed run is finished by running the same command again:

    destination rule file(s)  ->  settings lines  ->  usage history  ->  the
    source rule file, LAST (it is what names the globs and the history key)

A destination that already holds exactly what the operation would write is the
trace of an earlier run that stopped half way: the command goes on from there,
with no `--force`, and never discards history on that path. A destination with
other content is a name collision."""

import os

from .common import HOOK, existing_is_not_a_rule, fail, read_regular_file
from .lifecycle import holder, sync_lines

# What `plan_destination` found at the path a rule is about to be written to.
DEST_NEW = "new"              # nothing there: write it
DEST_SAME = "same"            # exactly this already: an earlier run got this far
DEST_OVERWRITE = "overwrite"  # something else, and --force said to replace it

# ---- user-visible text ------------------------------------------------------
ERROR_COLLISION = "{name} already exists in the {label} scope; --force replaces it"
ERROR_NOT_A_RULE_THERE = ("{name} already exists in the {label} scope and is NOT a "
                          "rule (no frontmatter); refusing to overwrite a plain "
                          "markdown file, even with --force")
ERROR_UNREADABLE = "cannot read {name} in the {label} scope to compare it: {why}"
ERROR_STATS = ("the usage history of {name} could not be updated; nothing more "
               "was changed — run the same command again")
# -----------------------------------------------------------------------------


def plan_destination(path, text, name, label, force, newline=None):
    """DEST_NEW, DEST_SAME or DEST_OVERWRITE for writing `text` at `path`, or a
    refusal: another content without `--force`, or a file that is not a rule at
    all. `newline` is how `text` was read or will be written, so the comparison
    is made on the same terms."""
    if not os.path.lexists(path):
        return DEST_NEW
    try:
        existing = read_regular_file(path, os.path.getsize(path), newline=newline)
    except OSError as exc:
        fail(ERROR_UNREADABLE.format(name=name, label=label, why=exc))
    if existing == text:
        return DEST_SAME
    if existing_is_not_a_rule(path):
        fail(ERROR_NOT_A_RULE_THERE.format(name=name, label=label))
    if not force:
        fail(ERROR_COLLISION.format(name=name, label=label))
    return DEST_OVERWRITE


def run_stats(apply, name):
    """`apply` on the usage file in one locked read-modify-write. The hook
    swallows its own failures (a tool call must never be blocked by them), so
    here "nothing came back" is what a failed update looks like."""
    if HOOK.update_stats(apply) is None:
        fail(ERROR_STATS.format(name=name))


def discard_history(scope_dir, name):
    """Forget everything recorded for a rule; nothing recorded is not an error."""
    key = HOOK.rule_key(scope_dir, name)
    run_stats(lambda stats: HOOK.drop_rule(stats, key), name)


def carry_history(old_scope_dir, old_name, new_scope_dir, new_name, only_repo=None):
    """Make the history of a rule follow it: the total and every repository's
    entry, or — with `only_repo`, for a global rule becoming a project one —
    only that repository's, whose count becomes the total.

    A run finished by a second one never counts twice: once the old key is gone
    both steps do nothing. One window remains, accepted on purpose: if this step
    succeeds, the source file is not removed, and the rule fires before the
    re-run, the re-run replaces the destination's history with the source's
    small new count. Merging the two instead would double-count the more common
    window, a re-run after a failure with no firing in between."""
    old_key = HOOK.rule_key(old_scope_dir, old_name)
    new_key = HOOK.rule_key(new_scope_dir, new_name)

    def apply(stats):
        if only_repo is not None:
            HOOK.keep_one_repo(stats, old_key, only_repo)
        HOOK.move_rule(stats, old_key, new_key)

    run_stats(apply, new_name)


def clear_overwritten(is_global, scope_dir, anchor, name):
    """`--force` is replacing the rule `name`: its settings lines and its
    history go before the new file is written, once — on the later run that
    finishes a failed one the file already is the new one, and what is recorded
    under its name is the history that followed the rule."""
    result = HOOK.read_rule_file(scope_dir, name, body_limit=0)
    fields = result[0] if result else {}
    sync_lines(is_global, scope_dir, anchor, [holder(name, fields)], [])
    discard_history(scope_dir, name)
