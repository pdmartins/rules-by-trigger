"""`doctor --uninstall`: the clean-up before `/plugin uninstall`, which removes
the plugin but leaves the user's own files behind.

What it removes is only what the plugin put in the user's space: the four
exact protection lines in `~/.claude/settings.json` and the state folders under
the cache and the temporary directory (the plugin data folder is deleted by
`/plugin uninstall` itself). What it keeps, and lists as information: the rule
folders, the hand-written `//**/` spellings of the protection, and the
`block --sync` lines of the current project's `.claude/settings.json`, which
this module only reads.

Every step is independent and idempotent: a failure in one is warned about and
the others still run, and running the command again finishes whatever is left.
Plain `doctor` prints `print_preview`, the same list without removing anything."""

import os
import shutil
import sys
import tempfile

from .block import (SETTINGS_RELPATH, blocking_rules, deny_entries,
                    existing_deny_entries)
from .common import HOOK, AdminError, rules_in, warn
from .hardening import (HARDENING_DENY_ENTRIES, SYMLINK_REASON,
                        equivalent_entries, remove_hardening, settings_problem,
                        user_settings_path)
from .environment import scope_dir_and_anchor, scope_targets

# Where the hook keeps session state besides the plugin data folder; the same
# two places as `state_dir_candidates` in the hook's state.py.
CACHE_RELPATH = os.path.join(".claude", "cache", "rules-by-trigger")
TEMP_STATE_NAME = "rules-by-trigger-state"
PLUGIN_UNINSTALL_COMMAND = "/plugin uninstall rules-by-trigger@pdmartins"
EXIT_PROBLEM = 1

# ---- user-visible text, in display order ----------------------------------
REFUSAL_NOT_OURS = "it is not owned by you, or anyone can write to it"
UNINSTALL_DENY = "removed {count} deny entr{plural} from {path}"
UNINSTALL_DENY_NONE = "no deny entries about rules-by-trigger in {path}"
UNINSTALL_DENY_HAND_ADDED = ("note: only the four exact lines the plugin writes "
                             "were removed; a line identical to one of them "
                             "added by hand was removed too")
UNINSTALL_SETTINGS_MISSING = "no {path}: nothing to remove there"
UNINSTALL_SETTINGS_SKIPPED = ("{path} left as it was ({reason}); fix it by hand "
                              "and run `doctor --uninstall` again to remove its lines")
UNINSTALL_SETTINGS_WRITE = ("cannot write {path}: {reason}; run the same "
                            "command again once it is writable")
UNINSTALL_STATE = "removed state folder: {path}"
UNINSTALL_STATE_REFUSED = ("not removing {path}: {reason}; remove it by hand if "
                           "it is yours")
UNINSTALL_STATE_FAILED = ("cannot remove {path}: {reason}; run the same command "
                          "again once it is removable")
UNINSTALL_KEPT = "kept (your rules, {count} file(s)): {path}"
UNINSTALL_EQUIVALENTS = ("kept (written by hand, recognised as protection, not "
                         "the plugin's lines): {path}")
UNINSTALL_PROJECT_LINES = ("kept ({count} `block --sync` line(s) of this project, "
                           "not touched): {path}")
UNINSTALL_PROJECT_LINE = "  {entry}"
UNINSTALL_NEXT = ("\nnext: run {command} in Claude Code. The rule directories "
                  "above are yours; delete them by hand only if you will not "
                  "reinstall.")
PREVIEW_TITLE = "\n--- `doctor --uninstall` would remove ---"
PREVIEW_DENY = "{count} deny line(s) in {path}"
PREVIEW_DENY_LINE = "  {entry}"
PREVIEW_SETTINGS_BLOCKED = ("cannot remove lines from {path}: {reason}; fix it "
                            "by hand first")
PREVIEW_STATE = "state folder: {path}"
PREVIEW_STATE_REFUSED = "cannot remove {path}: {reason}"
PREVIEW_NOTHING = "nothing: no protection lines and no state folders"
PREVIEW_KEEPS = "--- and would keep ---"
PREVIEW_KEPT = "{path} ({count} file(s))"
PREVIEW_EQUIVALENTS = "{path}: written by hand, recognised as protection"
PREVIEW_PROJECT_LINES = "{count} `block --sync` line(s) in {path}"
PREVIEW_NEXT = "then run {command}"
# ---------------------------------------------------------------------------


def state_folders():
    """[(path, why it is refused or None)] for the cache and temporary state
    folders that exist. A symlink or a folder that is not safely owned is not
    one the hook would use (see `state_dir` in the hook), and deleting through
    it could hit someone else's files, so it is refused, not removed."""
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    candidates = [os.path.join(os.path.expanduser("~"), CACHE_RELPATH),
                  os.path.join(tempfile.gettempdir(), TEMP_STATE_NAME + suffix)]
    folders = []
    for path in candidates:
        if not os.path.isdir(path):
            continue
        if os.path.islink(path):
            folders.append((path, SYMLINK_REASON))
        elif not HOOK.is_safely_owned(path):
            folders.append((path, REFUSAL_NOT_OURS))
        else:
            folders.append((path, None))
    return folders


def protection_lines_present():
    """The four exact lines `--uninstall` would take out of
    `~/.claude/settings.json`, whichever are there."""
    deny = existing_deny_entries(user_settings_path())
    return [entry for entry in HARDENING_DENY_ENTRIES if entry in deny]


def kept_equivalents():
    """The hand-written `//**/` spellings in the user's settings: they stay."""
    return equivalent_entries(existing_deny_entries(user_settings_path()))


def kept_rule_folders(args):
    """[(path, rule count)] of the rule folders that exist: they stay."""
    folders = []
    for _label, target in scope_targets(args):
        scope_dir, _anchor = scope_dir_and_anchor(target)
        if os.path.isdir(scope_dir):
            folders.append((scope_dir, len(rules_in(scope_dir))))
    return folders


def project_block_lines(args):
    """(settings path, [`Edit(<glob>)` lines]) the current project's
    `block --sync` wrote and still wants; read-only."""
    if args.use_global:
        return None, []
    scope_dir, anchor = scope_dir_and_anchor(
        next(target for _label, target in scope_targets(args) if not target.use_global))
    settings_path = os.path.join(anchor, SETTINGS_RELPATH)
    synced = set(existing_deny_entries(settings_path))
    wanted = deny_entries(blocking_rules(scope_dir)) if os.path.isdir(scope_dir) else []
    return settings_path, [entry for entry in wanted if entry in synced]


def remove_protection_lines():
    """Take the protection lines out of the user's settings; True when it
    went wrong and the exit code must be non-zero: a failed write, or a file
    that cannot be read as JSON (left byte for byte as it was)."""
    settings_path = user_settings_path()
    if not os.path.isfile(settings_path):
        print(UNINSTALL_SETTINGS_MISSING.format(path=settings_path))
        return False
    problem = settings_problem(settings_path)
    if problem:
        warn(UNINSTALL_SETTINGS_SKIPPED.format(path=settings_path, reason=problem))
        return True
    try:
        removed = remove_hardening()
    except AdminError as exc:
        warn(UNINSTALL_SETTINGS_SKIPPED.format(path=settings_path, reason=exc))
        return True
    except OSError as exc:
        warn(UNINSTALL_SETTINGS_WRITE.format(path=settings_path, reason=exc))
        return True
    if not removed:
        print(UNINSTALL_DENY_NONE.format(path=settings_path))
        return False
    print(UNINSTALL_DENY.format(count=len(removed), path=settings_path,
                                plural="y" if len(removed) == 1 else "ies"))
    print(UNINSTALL_DENY_HAND_ADDED)
    return False


def remove_state_folders():
    """Remove every state folder, warning about each one that is refused or
    cannot go and carrying on with the rest; True when any was not removed."""
    failed = False
    for path, refusal in state_folders():
        if refusal:
            warn(UNINSTALL_STATE_REFUSED.format(path=path, reason=refusal))
            failed = True
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            warn(UNINSTALL_STATE_FAILED.format(path=path, reason=exc))
            failed = True
            continue
        print(UNINSTALL_STATE.format(path=path))
    return failed


def cmd_uninstall(args):
    settings_failed = remove_protection_lines()
    folders_failed = remove_state_folders()
    for path, count in kept_rule_folders(args):
        print(UNINSTALL_KEPT.format(count=count, path=path))
    equivalents = kept_equivalents()
    if equivalents:
        print(UNINSTALL_EQUIVALENTS.format(path=user_settings_path()))
        for entry in equivalents:
            print(UNINSTALL_PROJECT_LINE.format(entry=entry))
    settings_path, lines = project_block_lines(args)
    if lines:
        print(UNINSTALL_PROJECT_LINES.format(count=len(lines), path=settings_path))
        for entry in lines:
            print(UNINSTALL_PROJECT_LINE.format(entry=entry))
    print(UNINSTALL_NEXT.format(command=PLUGIN_UNINSTALL_COMMAND))
    if settings_failed or folders_failed:
        sys.exit(EXIT_PROBLEM)


def print_preview(args):
    """What `--uninstall` would remove and keep, as information after a plain
    `doctor`: it is not a check and never changes the exit code."""
    lines = protection_lines_present()
    folders = state_folders()
    problem = settings_problem(user_settings_path())
    print(PREVIEW_TITLE)
    if problem:
        print(PREVIEW_SETTINGS_BLOCKED.format(path=user_settings_path(), reason=problem))
    elif lines:
        print(PREVIEW_DENY.format(count=len(lines), path=user_settings_path()))
        for entry in lines:
            print(PREVIEW_DENY_LINE.format(entry=entry))
    for path, refusal in folders:
        if refusal:
            print(PREVIEW_STATE_REFUSED.format(path=path, reason=refusal))
        else:
            print(PREVIEW_STATE.format(path=path))
    if not lines and not folders and not problem:
        print(PREVIEW_NOTHING)
    print(PREVIEW_KEEPS)
    for path, count in kept_rule_folders(args):
        print(PREVIEW_KEPT.format(path=path, count=count))
    equivalents = kept_equivalents()
    if equivalents:
        print(PREVIEW_EQUIVALENTS.format(path=user_settings_path()))
        for entry in equivalents:
            print(PREVIEW_DENY_LINE.format(entry=entry))
    settings_path, project_lines = project_block_lines(args)
    if project_lines:
        print(PREVIEW_PROJECT_LINES.format(count=len(project_lines), path=settings_path))
        for entry in project_lines:
            print(UNINSTALL_PROJECT_LINE.format(entry=entry))
    print(PREVIEW_NEXT.format(command=PLUGIN_UNINSTALL_COMMAND))
