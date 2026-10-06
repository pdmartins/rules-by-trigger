"""Which rules `status` lists, and which of them cover a path.

The scopes are the global one, the project at the repository root and every
project nested below it. A project folder above the root belongs to no single
repository and is never looked at. Everything here only reads."""

import os
from collections import namedtuple

from .common import HOOK, rules_in
from .statustable import SCOPE_GLOBAL, SCOPE_PROJECT, STATE_ACTIVE, STATE_DISABLED
from .usage import candidate_of, counts_of, usage_of
from .which import COVERAGE_MATCH, coverage_of

# `base` is the folder holding the scope's `.claude`; `subdir` is that folder
# relative to the repository root, with `/` separators ("" for the root).
Scope = namedtuple("Scope", "kind subdir base scope_dir")

PARENT_SEGMENT = ".."
ROOT_SUBDIR = ""
# Folders the walk below the repository root never enters: git's own data and
# the usual heavy folders of dependencies and build output, which hold the most
# files of any repository and no project of the user's own. A nested project
# INSIDE one of them is therefore not listed, although the hook still injects
# its rules when a file under it is touched.
SKIPPED_FOLDERS = (HOOK.GIT_ENTRY_NAME, "node_modules", ".venv", "venv",
                   "__pycache__", ".tox", "dist", "build", "target")


def global_scope_of():
    """The machine owner's scope, or None when it has no usable directory."""
    home = os.path.expanduser("~")
    scope_dir = HOOK.usable_scope(home, is_global=True)
    return Scope(SCOPE_GLOBAL, ROOT_SUBDIR, home, scope_dir) if scope_dir else None


def nested_scopes(repo_root, excluded_real):
    """Every usable project scope at `repo_root` or below it, the root first and
    the rest in name order. Symlinked folders are not followed, `.git` is not
    entered, and a scope whose real path is in `excluded_real` (the global
    one, when the repository sits at home) is not listed twice."""
    scopes = []
    for folder, subfolders, _files in os.walk(repo_root):
        subfolders[:] = sorted(name for name in subfolders
                               if name not in SKIPPED_FOLDERS)
        scope_dir = HOOK.usable_scope(folder)
        if not scope_dir or os.path.realpath(scope_dir) in excluded_real:
            continue
        relative = os.path.relpath(folder, repo_root)
        subdir = ROOT_SUBDIR if relative == os.curdir else relative.replace(os.sep, "/")
        scopes.append(Scope(SCOPE_PROJECT, subdir, folder, scope_dir))
    return scopes


def scopes_of(repo_root, only_global):
    """The scopes whose rules the table lists."""
    owner = global_scope_of()
    scopes = [owner] if owner else []
    if only_global:
        return scopes
    excluded = {os.path.realpath(owner.scope_dir)} if owner else set()
    return scopes + nested_scopes(repo_root, excluded)


def rows_of(scopes, config, stats, repo):
    """One row per rule file of every scope, disabled ones included. A rule
    that has history and no file is not a row: the files are what is listed."""
    rows = []
    for scope in scopes:
        is_global = scope.kind == SCOPE_GLOBAL
        for name, fields, body in rules_in(scope.scope_dir, HOOK.max_rule_chars(config)):
            entry = usage_of(stats, scope.scope_dir, name, repo)
            this_repo, total = counts_of(entry)
            candidate, reason = candidate_of(name, fields, body, config,
                                             is_global, entry)
            rows.append({"name": name, "kind": scope.kind, "subdir": scope.subdir,
                         "state": STATE_ACTIVE if HOOK.is_enabled(fields)
                         else STATE_DISABLED,
                         "this_repo": this_repo, "total": total,
                         "candidate": candidate, "reason": reason})
    return rows


def sort_key(row):
    """Most fired in this repo first, then in total, then by name — compared
    character by character, case counted — then project before global, then
    by folder."""
    return (-row["this_repo"], -row["total"], row["name"],
            0 if row["kind"] == SCOPE_PROJECT else 1, row["subdir"])


def is_inside(path, base):
    relative = os.path.relpath(path, base)
    return relative != PARENT_SEGMENT and not relative.startswith(PARENT_SEGMENT + os.sep)


def anchored_path(path, base):
    """(path, base) in one form that puts the path under the base, or None
    when it is not under it. The path as given is tried first, then both
    resolved — the way the hook matches a path reached through a symlink (see
    `path_targets`): the repository root is always a resolved folder, so a
    `--path` or a `--root` that goes through a link is inside it only once
    resolved."""
    if is_inside(os.path.normpath(path), base):
        return path, base
    real, real_base = os.path.realpath(path), os.path.realpath(base)
    if not is_inside(real, real_base):
        return None
    if path.endswith("/"):
        real += "/"  # a trailing slash says "folder", also for one not yet there
    return real, real_base


def covering_keys(scopes, path, tool):
    """{(name, kind, subdir)} of the active rules the hook would inject for
    `path` (absolute): a rule whose glob matches, no `exclude` takes back, and
    whose `tool:` filter accepts the call. Rules that fire on a skill call
    have no glob and never appear. A file inside a rules folder triggers
    nothing at all."""
    real = os.path.realpath(path)
    if HOOK.is_inside_rules_dir(path.replace(os.sep, "/"), real.replace(os.sep, "/")):
        return set()
    keys = set()
    for scope in scopes:
        is_global = scope.kind == SCOPE_GLOBAL
        shown, base = path, scope.base
        if not is_global:
            anchored = anchored_path(path, scope.base)
            if anchored is None:
                continue  # the hook only reaches the projects the file sits in
            shown, base = anchored
        entries, _shown = coverage_of(scope.scope_dir, base, is_global, shown, tool)
        keys.update((name, scope.kind, scope.subdir)
                    for status, name, _line in entries if status == COVERAGE_MATCH)
    return keys
