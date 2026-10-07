"""The recommended hardening: deny-list the rules directories for Claude's
file tools in `~/.claude/settings.json`, so a rule reaches context only
through the hook and is read or written only through this CLI.

Exactly four entries, for two reasons verified against Claude Code 2.1.233:
only `Read(...)` and `Edit(...)` are consulted (Read governs greps too, Edit
governs every editing tool; a `Grep(...)`/`Write(...)`/`MultiEdit(...)` entry
matches nothing and makes Claude Code warn at startup), and a pattern that
does not begin with `/` or `~/` resolves against the cwd, so the `~/`-anchored
pair is what protects the global rules from inside a project outside $HOME.
A `//**/`-anchored entry (absolute from the filesystem root) is equivalent to
BOTH canonical spellings of the same tool at once — it matches whatever
`**/...` matches (any relative depth) and whatever `~/...` matches (the path
under $HOME is still a path from `/`) — so a hand-written `Read(//**/...)` is
recognised as already covering that tool's pair, rather than reported as
missing and duplicated by `--harden`."""

import json
import os

from .common import atomic_write
from .block import (SETTINGS_RELPATH, existing_deny_entries,
                      read_settings_for_sync)

HARDENING_DENY_ENTRIES = (
    "Read(**/.claude/rules-by-trigger/**)",
    "Edit(**/.claude/rules-by-trigger/**)",
    "Read(~/.claude/rules-by-trigger/**)",
    "Edit(~/.claude/rules-by-trigger/**)",
)
# ---- user-visible text -----------------------------------------------------
SYMLINK_REASON = "it is a symlink"
UNREADABLE_REASON = "cannot read it: {error}"
NOT_JSON_REASON = "it is not valid JSON"
NOT_OBJECT_REASON = "it does not hold a JSON object"
# -----------------------------------------------------------------------------
# Any deny entry about the rules directory: the four above, or a spelling
# someone wrote by hand, which `is_obsolete` may find the matcher ignores.
RULES_DIR_MARKER = ".claude/rules-by-trigger/"
HONOURED_TOOLS = ("Read", "Edit")
# The absolute-from-root anchor: see the module docstring's equivalence note.
ABSOLUTE_ROOT_PREFIX = "//**/"


def user_settings_path():
    return os.path.join(os.path.expanduser("~"), SETTINGS_RELPATH)


def settings_problem(settings_path):
    """Why `~/.claude/settings.json` cannot be edited as JSON, or None when it
    is absent or fine: a symlink, unreadable, not valid JSON, not an object."""
    if not os.path.isfile(settings_path):
        return None
    if os.path.islink(settings_path):
        return SYMLINK_REASON
    try:
        with open(settings_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        return UNREADABLE_REASON.format(error=exc)
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return NOT_JSON_REASON
    return None if isinstance(data, dict) else NOT_OBJECT_REASON


def equivalent_entries(deny):
    """The hand-written `//**/` spellings present in `deny` that `is_covered`
    recognises as protection: not the plugin's lines, so never removed."""
    spellings = [f"{tool}({ABSOLUTE_ROOT_PREFIX}{RULES_DIR_MARKER}**)"
                 for tool in HONOURED_TOOLS]
    return [entry for entry in spellings if entry in deny]


def is_rules_dir_entry(entry):
    return isinstance(entry, str) and RULES_DIR_MARKER in entry


def is_obsolete(entry):
    """A rules-directory entry for a tool the permission matcher never
    consults: dead weight that also produces a startup warning."""
    if not is_rules_dir_entry(entry):
        return False
    tool = entry.split("(", 1)[0].strip()
    return tool not in HONOURED_TOOLS


def is_covered(entry, deny):
    """Whether `entry`'s protection is already in force in `deny`: verbatim,
    or via that tool's `//**/` absolute-root spelling, which — anchored at the
    filesystem root — matches everything either canonical entry matches (see
    the module docstring)."""
    if entry in deny:
        return True
    tool = entry.split("(", 1)[0].strip()
    return f"{tool}({ABSOLUTE_ROOT_PREFIX}{RULES_DIR_MARKER}**)" in deny


def hardening_state():
    """What the user's settings currently say about the rules directories."""
    settings_path = user_settings_path()
    deny = existing_deny_entries(settings_path)
    return {
        "settings": settings_path,
        "present": [entry for entry in HARDENING_DENY_ENTRIES if is_covered(entry, deny)],
        "missing": [entry for entry in HARDENING_DENY_ENTRIES if not is_covered(entry, deny)],
        "obsolete": [entry for entry in deny if is_obsolete(entry)],
    }


def deny_list_of(data, settings_path):
    """The `permissions.deny` array of a settings document, created when
    absent, refused when it is the wrong shape."""
    from .common import fail
    permissions = data.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        fail(f"{settings_path}: 'permissions' is not an object; fix it by hand")
    deny = permissions.setdefault("deny", [])
    if not isinstance(deny, list):
        fail(f"{settings_path}: 'permissions.deny' is not an array; fix it by hand")
    return deny


def apply_hardening():
    """Merge the four entries into the user's deny list and drop obsolete
    spellings. Returns (added, removed). Everything else in the file is kept."""
    settings_path = user_settings_path()
    data = read_settings_for_sync(settings_path)
    deny = deny_list_of(data, settings_path)
    removed = [entry for entry in deny if is_obsolete(entry)]
    deny[:] = [entry for entry in deny if not is_obsolete(entry)]
    added = [entry for entry in HARDENING_DENY_ENTRIES if not is_covered(entry, deny)]
    deny.extend(added)
    if added or removed:
        atomic_write(settings_path, json.dumps(data, indent=2) + "\n")
    return added, removed


def remove_hardening():
    """Drop the four exact lines `apply_hardening` writes — the undo for it and
    the first step of an uninstall: without it those paths stay unreadable with
    nothing left to serve them. Any other line stays, the recognised `//**/`
    spellings included. Returns what was removed.

    A file that is not valid JSON raises `AdminError` before anything is
    written; a failed write raises `OSError` and leaves the file as it was."""
    settings_path = user_settings_path()
    if not os.path.isfile(settings_path):
        return []
    data = read_settings_for_sync(settings_path)
    permissions = data.get("permissions")
    if not isinstance(permissions, dict) or not isinstance(permissions.get("deny"), list):
        return []
    deny = permissions["deny"]
    removed = [entry for entry in deny if entry in HARDENING_DENY_ENTRIES]
    if removed:
        deny[:] = [entry for entry in deny if entry not in HARDENING_DENY_ENTRIES]
        atomic_write(settings_path, json.dumps(data, indent=2) + "\n")
    return removed
