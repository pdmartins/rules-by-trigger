"""`status`: how much each rule is used, as a table.

One row per rule of the global scope, of the project at the repository root
and of the projects nested below it, most used in this repo first. `--json`
adds, per rule, the improvement candidate the usage points to (prune, narrow
or repeat) and the reason. The environment is `doctor`'s and the configuration
in force is `config`'s: neither is shown here.

Read-only and deterministic: the output holds no date and nothing else that
changes between two runs with no fire in between. Everything is built before
anything is printed, so a failure leaves stdout empty."""

import json
import os
from types import SimpleNamespace

from .common import HOOK, fail
from .config import config_for
from .statusrules import covering_keys, rows_of, scopes_of, sort_key
from .statustable import (LANGUAGE_EN, LABELS, render_covering, render_table,
                          scope_text, labels_for)

# ---- user-visible text -------------------------------------------------------
ERROR_NO_ROOT = "project root does not exist: {path}"
ERROR_NO_REPO = "cannot tell which repository this is: the current folder is unreadable"
# -----------------------------------------------------------------------------


def start_folder(args):
    """Where `status` starts: `--root`, else `CLAUDE_PROJECT_DIR`, else the
    current folder."""
    if args.root:
        folder = os.path.abspath(args.root)
    else:
        folder = os.path.abspath(os.environ.get(HOOK.PROJECT_DIR_ENV) or os.getcwd())
    if not os.path.isdir(folder):
        fail(ERROR_NO_ROOT.format(path=folder))
    return folder


def repo_of(args, start):
    """(repository, whether git named it), identified as the hook identifies
    it: the git root of the start folder, or the folder itself outside git."""
    if args.root:
        repo, in_git = HOOK.repo_root_and_git(start)
    else:
        repo, in_git = HOOK.repo_identity_and_git()
    if repo is None:
        fail(ERROR_NO_REPO)
    return repo, in_git


def absolute_path(path, start):
    """`--path` as an absolute path, a trailing `/` kept (it says "folder")."""
    return path if os.path.isabs(path) else os.path.join(start, path)


def json_rule(row):
    """A row as `status --json` shows it: the table's values, in English."""
    return {"name": row["name"],
            "scope": scope_text(row["kind"], row["subdir"], LABELS[LANGUAGE_EN]),
            "state": row["state"], "this_repo": row["this_repo"],
            "total": row["total"], "candidate": row["candidate"],
            "reason": row["reason"]}


def cmd_status(args):
    start = start_folder(args)
    repo, in_git = repo_of(args, start)
    config = config_for(SimpleNamespace(use_global=args.use_global, root=repo))
    stats = HOOK.load_stats()
    scopes = scopes_of(repo, args.use_global, in_git)
    rows = sorted(rows_of(scopes, config, stats, repo), key=sort_key)
    covering = None
    if args.path:
        keys = covering_keys(scopes, absolute_path(args.path, start), args.tool)
        covering = [row for row in rows
                    if (row["name"], row["kind"], row["subdir"]) in keys]
    if args.json:
        report = {"repo": repo, "rules": [json_rule(row) for row in rows]}
        if covering is not None:
            report["covering"] = [{"name": row["name"],
                                   "scope": json_rule(row)["scope"]}
                                  for row in covering]
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    labels = labels_for(HOOK.language(config))
    lines = render_table(rows, labels)
    if covering is not None:
        lines.append("")
        lines.extend(render_covering(covering, labels, args.path))
    print("\n".join(lines))
