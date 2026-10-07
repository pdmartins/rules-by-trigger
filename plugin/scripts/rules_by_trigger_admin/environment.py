"""Where the plugin is installed and which scopes a command targets: the
plugin's version and hook launcher, and the global/project scope pairs that
`doctor` walks. `status` no longer reports any of it — the environment is
`doctor`'s to show."""

import json
import os
from types import SimpleNamespace

from .common import HOOK, read_regular_file

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
PLUGIN_MANIFEST_RELPATH = os.path.join(".claude-plugin", "plugin.json")
HOOK_LAUNCHER_RELPATH = os.path.join("bin", "rules-by-trigger-hook")
# A manifest is a few KB; this is a ceiling, not an expectation.
MAX_MANIFEST_BYTES = 1024 * 1024
UNKNOWN_VERSION = "?"


def plugin_version():
    path = os.path.join(HOOK.PLUGIN_ROOT, PLUGIN_MANIFEST_RELPATH)
    try:
        return json.loads(read_regular_file(path, MAX_MANIFEST_BYTES)).get(
            "version", UNKNOWN_VERSION)
    except (OSError, ValueError):
        return UNKNOWN_VERSION


def scope_targets(args):
    """[(label, args namespace)] — the global scope always, the project scope
    when --root was given. Each namespace is what the other commands take, so
    `config_for` and `scope_for` see exactly the scope they expect."""
    targets = [(SCOPE_GLOBAL, SimpleNamespace(use_global=True, root=None))]
    if not args.use_global:
        targets.append((SCOPE_PROJECT, SimpleNamespace(use_global=False,
                                                       root=args.root)))
    return targets


def scope_dir_and_anchor(target):
    if target.use_global:
        anchor = os.path.expanduser("~")
    else:
        anchor = os.path.abspath(target.root)
    return os.path.join(anchor, HOOK.RULES_DIR_RELPATH), anchor
