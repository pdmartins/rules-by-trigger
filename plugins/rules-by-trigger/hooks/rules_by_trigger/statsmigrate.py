"""The first usage file (version 1) turned into the per-repository one.

Version 1 kept one entry per rule, with no repository in it. A project rule's
history goes to the repository its own folder belongs to; a global rule's has
no repository and enters only the rule's total. An entry whose rule file no
longer exists is dropped. The rule key is the same in both versions, which is
what makes converting twice impossible to double count: a converted file is
version 2 and is never converted again."""

import os

from .repo import repo_root_of, rule_base
from .rules import is_valid_rule_name
from .statsformat import coerce_entry, empty_stats


def rule_exists(scope_dir, name):
    return is_valid_rule_name(name) and os.path.isfile(os.path.join(scope_dir, name))


def convert_v1(data):
    """Version-2 stats from a parsed version-1 file whose `rules` is a dict."""
    stats = empty_stats()
    if isinstance(data.get("since"), int):
        stats["since"] = data["since"]
    repos_by_base = {}
    for key, value in data["rules"].items():
        if not isinstance(key, str) or "::" not in key:
            continue
        scope_dir, name = key.rsplit("::", 1)
        if not rule_exists(scope_dir, name):
            continue
        entry = coerce_entry(value)
        rule = {"total": entry["injections"], "last": entry["last"], "repos": {}}
        base = rule_base(scope_dir)
        if base is not None:
            if base not in repos_by_base:
                repos_by_base[base] = repo_root_of(base)
            rule["repos"][repos_by_base[base]] = entry
        stats["rules"][key] = rule
    return stats
