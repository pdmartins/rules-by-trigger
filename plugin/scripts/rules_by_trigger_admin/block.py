"""`block`: bridging `block: true` rules to Claude Code's own
`permissions.deny`.

The hook only ever honours `block: true` from the GLOBAL scope (see
`HOOK.blocking_rule` — a project rule is untrusted repository content, and
letting it deny the user's own tool calls would be an escalation). A project
rule that declares `block: true` therefore needs a NATIVE deny entry to
actually block anything: `--list` shows what that entry would be, `--sync`
writes it into the project's own `.claude/settings.json`, reusing the same
merge-don't-duplicate approach `/rules-by-trigger:setup` uses for the user's
`~/.claude/settings.json`.
"""

import json
import os

from .common import HOOK, atomic_write, fail, rules_in, scope_for

SETTINGS_RELPATH = os.path.join(".claude", "settings.json")


def blocking_rules(scope_dir, enabled=True, skip_name=None):
    """[(name, globs, excludes)] for every rule in the scope that declares
    `block: true` and is switched on (`enabled=False`: switched off), in the
    order `rules_in` already sorts them. `skip_name` leaves one rule out — the
    one a command is about to disable or delete.

    The excludes ride along because a native deny entry cannot express one:
    `--list` has to say so, rather than let a synced entry silently deny more
    than the rule it came from."""
    return [(name, HOOK.globs_of(fields), HOOK.excludes_of(fields))
            for name, fields, _body in rules_in(scope_dir)
            if HOOK.block_of(fields) and HOOK.is_enabled(fields) == enabled
            and name != skip_name]


def deny_entry_for(glob):
    """The native `permissions.deny` entry one glob translates to: `Edit(...)`
    alone, not a separate `Write(...)`/`MultiEdit(...)` pair.
    `/rules-by-trigger:setup` already established (verified against Claude Code
    2.1.233, see the README's *Recommended hardening*) that `Edit(...)` is
    consulted for every file-editing tool — Write, Edit, MultiEdit,
    NotebookEdit alike — and a separate `Write(...)` entry is simply never
    matched, only printing a startup warning."""
    return f"Edit({glob})"


def deny_entries(rules):
    """The native `permissions.deny` entries these rules translate to,
    deduplicated but order-preserving."""
    entries = []
    for _name, globs, _excludes in rules:
        for glob in globs:
            entry = deny_entry_for(glob)
            if entry not in entries:
                entries.append(entry)
    return entries


def read_settings_for_sync(settings_path):
    """The project's `.claude/settings.json` as a dict ready to be mutated in
    place, or `{}` when there is none yet — `--sync` creates a minimal one.

    Unlike `--list`'s read-only `existing_deny_entries`, a file this function
    cannot make sense of is a reason to STOP rather than to proceed as if it
    were empty: silently treating unreadable JSON as "no entries yet" would
    have `--sync` overwrite whatever the human had there with a minimal file
    that has lost it."""
    if not os.path.isfile(settings_path):
        return {}
    if os.path.islink(settings_path):
        fail(f"{settings_path} is a symlink; refusing to write through it")
    try:
        with open(settings_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        fail(f"cannot read {settings_path}: {exc}")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        fail(f"{settings_path} is not valid JSON ({exc}); fix it by hand, then "
             f"re-run --sync")
    if not isinstance(data, dict):
        fail(f"{settings_path} does not hold a JSON object; refusing to touch it")
    return data


def existing_deny_entries(settings_path):
    """The `permissions.deny` array already on disk, read-only and tolerant:
    `--list` reports status, it must never fail just because the file a human
    hand-edited is momentarily not quite valid JSON."""
    if not os.path.isfile(settings_path):
        return []
    try:
        with open(settings_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    permissions = data.get("permissions") if isinstance(data, dict) else None
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    if not isinstance(deny, list):
        return []
    # Only strings: a hand-edited file may hold anything in the array.
    return [entry for entry in deny if isinstance(entry, str)]


def apply_deny_lines(anchor, add=(), remove=()):
    """([entries added], [entries removed]) after bringing the project's
    `permissions.deny` in line: every entry of `add` present, every entry of
    `remove` gone, anything else untouched.

    Idempotent on purpose — a line already there, or already gone (taken out by
    hand), is not an error — and it writes only when something changes, so the
    second run of a command that was interrupted finishes the work and a run
    with nothing to do leaves the file alone, even when it does not exist yet.
    A failed write leaves the file as it was and exits through `fail`, naming
    the file: the commands that call this have already written the rule."""
    settings_path = os.path.join(anchor, SETTINGS_RELPATH)
    if not (add or os.path.isfile(settings_path)):
        return [], []
    data = read_settings_for_sync(settings_path)
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        fail(f"{settings_path}: 'permissions' is not an object; fix it by hand")
    deny = permissions.get("deny", [])
    if not isinstance(deny, list):
        fail(f"{settings_path}: 'permissions.deny' is not an array; fix it by hand")
    removed = [entry for entry in remove if entry in deny]
    added = [entry for entry in add if entry not in deny]
    if not (added or removed):
        return [], []
    kept = [entry for entry in deny if entry not in removed]
    data.setdefault("permissions", permissions)["deny"] = kept + added
    try:
        atomic_write(settings_path, json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        fail(f"cannot write {settings_path}: {exc}; run the same command again "
             f"once it is writable")
    return added, removed


def cmd_block_list(scope_dir, anchor, is_global):
    rules = blocking_rules(scope_dir)
    if not rules:
        print("(no blocking rules in this scope)")
        return
    settings_path = os.path.join(anchor, SETTINGS_RELPATH)
    existing = set(existing_deny_entries(settings_path))
    for name, globs, excludes in rules:
        print(f"{name}  block: true")
        if not globs:
            print("  (no glob declared — never matches, so never denies)")
            continue
        if excludes:
            print(f"  NOTE: this rule excludes {', '.join(repr(e) for e in excludes)}, "
                  f"which a native deny entry cannot express — a synced entry "
                  f"denies those paths too")
        for glob in globs:
            entry = deny_entry_for(glob)
            if is_global:
                status = "already active via the hook (global scope)"
            elif entry in existing:
                status = f"synced in {settings_path}"
            else:
                status = "NOT synced — run `block --sync` to add it"
            print(f"  {entry}  [{status}]")


def cmd_block_sync(scope_dir, anchor, is_global):
    if is_global:
        # A global `block: true` rule is already honoured directly by the
        # hook (see HOOK.blocking_rule) — syncing it would write a SECOND,
        # redundant mechanism into a file (~/.claude/settings.json) that has
        # nothing to do with any one project. --sync exists for the case the
        # hook cannot cover: a project rule, whose scope is untrusted.
        fail("'block --sync' is for a project scope (--root); a global "
             "block: true rule is already honoured by the hook directly, so "
             "there is nothing to sync. Pass --root <project-root> for the "
             "project whose blocking rules need a native deny of their own")
    wanted = deny_entries(blocking_rules(scope_dir))
    # What only a disabled rule still asks for: the line is stale. This is how a
    # hand edit of `enabled:` gets corrected, since `remove` is the command that
    # takes the lines out in the first place.
    stale = [entry for entry in deny_entries(blocking_rules(scope_dir, enabled=False))
             if entry not in wanted]
    if not wanted and not stale:
        print("(no blocking rules to sync)")
        return
    added, removed = apply_deny_lines(anchor, wanted, stale)
    settings_path = os.path.join(anchor, SETTINGS_RELPATH)
    if not added and not removed:
        if wanted:
            print(f"ok: {settings_path} already has every deny entry these "
                  f"rules need")
        else:
            print(f"ok: nothing to change in {settings_path}: it holds no line "
                  f"of a disabled rule")
        return
    if added:
        plural = "y" if len(added) == 1 else "ies"
        print(f"ok: {len(added)} new deny entr{plural} written to {settings_path}")
        for entry in added:
            print(f"  {entry}")
    if removed:
        plural = "y" if len(removed) == 1 else "ies"
        print(f"ok: {len(removed)} deny entr{plural} of disabled rules removed "
              f"from {settings_path}")
        for entry in removed:
            print(f"  {entry}")


def cmd_block(args):
    scope_dir, anchor = scope_for(args)
    if args.sync:
        cmd_block_sync(scope_dir, anchor, args.use_global)
    else:
        cmd_block_list(scope_dir, anchor, args.use_global)
