"""The shape of the usage file (version 2) and the operations on it that
never touch the disk: coercion, the two caps, and the three things a rule's
life does to its history.

    {"version": 2, "since": <epoch>,
     "rules": {"<realpath(scope_dir)>::<name>":
                 {"total": <fires, every repo>, "last": <epoch or null>,
                  "repos": {"<repo root>": <entry>}}}}

An entry holds what one repository's firings of the rule left: injections,
reinjections, sessions, recent_sessions, first, last, dirs, globs,
verifications, failures. `total` is a plain count that outlives the entries:
evicting a repository past its cap leaves it alone, and history migrated from
a global rule lives only there.

Reading and writing the file is `stats.py`'s; the old format is
`statsmigrate.py`'s."""

import time

from .constants import MAX_STATS_REPOS_PER_RULE, MAX_STATS_RULES, STATS_VERSION


def empty_stats():
    return {"version": STATS_VERSION, "since": int(time.time()), "rules": {}}


def empty_entry():
    """A repository's slice of a rule with nothing recorded yet.
    `verifications`/`failures` are part of the shape rather than added on first
    use, so a file written before `verify:` existed loads with them at zero."""
    return {"injections": 0, "reinjections": 0, "sessions": 0,
            "recent_sessions": [], "first": None, "last": None,
            "dirs": {}, "globs": {}, "verifications": 0, "failures": 0}


def empty_rule():
    return {"total": 0, "last": None, "repos": {}}


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def coerce_entry(value):
    """A stored entry with every field present and of the right type, so a
    hand-edited or half-written file cannot crash the arithmetic."""
    entry = empty_entry()
    if not isinstance(value, dict):
        return entry
    for key in ("injections", "reinjections", "sessions",
                "verifications", "failures"):
        if is_count(value.get(key)):
            entry[key] = value[key]
    for key in ("first", "last"):
        if isinstance(value.get(key), int):
            entry[key] = value[key]
    recent = value.get("recent_sessions")
    if isinstance(recent, list):
        entry["recent_sessions"] = [s for s in recent if isinstance(s, str)]
    for key in ("dirs", "globs"):
        counter = value.get(key)
        if isinstance(counter, dict):
            entry[key] = {k: v for k, v in counter.items()
                          if isinstance(k, str) and isinstance(v, int)}
    return entry


def coerce_rule(value):
    rule = empty_rule()
    if not isinstance(value, dict):
        return rule
    if is_count(value.get("total")):
        rule["total"] = value["total"]
    if isinstance(value.get("last"), int):
        rule["last"] = value["last"]
    repos = value.get("repos")
    if isinstance(repos, dict):
        rule["repos"] = {repo: coerce_entry(entry) for repo, entry in repos.items()
                         if isinstance(repo, str)}
    return rule


def coerce_rules(rules):
    return {key: coerce_rule(value) for key, value in rules.items()
            if isinstance(key, str)}


def keep_newest(mapping, cap):
    """Keep the `cap` entries with the latest `last`; ties go to the one
    added later."""
    surplus = len(mapping) - cap
    if surplus <= 0:
        return
    for key in sorted(mapping, key=lambda k: mapping[k]["last"] or 0)[:surplus]:
        del mapping[key]


def enforce_caps(stats):
    """Keep the file bounded: past MAX_STATS_RULES the rules fired longest ago
    go, and each rule keeps at most MAX_STATS_REPOS_PER_RULE repositories —
    the total of a rule does not drop when one of them is evicted."""
    for rule in stats["rules"].values():
        keep_newest(rule["repos"], MAX_STATS_REPOS_PER_RULE)
    keep_newest(stats["rules"], MAX_STATS_RULES)


def drop_rule(stats, key):
    """A rule was deleted: forget everything recorded for it."""
    stats["rules"].pop(key, None)


def move_rule(stats, old_key, new_key):
    """A rule was renamed or moved to another scope: its total and every
    repository's entry follow it, and the old key is gone either way.

    When the new key already holds history, the larger `total` stays and the
    other is dropped; on a tie the new key's stays. That is the case of a run
    finished by a second one: both files exist in between and both count, so
    the history that kept counting is the larger one (see `carry_history` in
    the admin). A history left under the new key by a rule file deleted by hand
    is cleared by the command before it writes, so it cannot win here. When
    only one side holds history it stays or moves, as it always did."""
    if old_key == new_key:
        return
    rule = stats["rules"].pop(old_key, None)
    if rule is None:
        return
    kept = stats["rules"].get(new_key)
    if kept is None or rule["total"] > kept["total"]:
        stats["rules"][new_key] = rule


def keep_one_repo(stats, key, repo):
    """A global rule became a project rule of `repo`: only that repository's
    entry is its history now, and the total says the same. A rule `repo` never
    fired leaves nothing to keep."""
    rule = stats["rules"].get(key)
    if rule is None:
        return
    entry = rule["repos"].get(repo)
    if entry is None:
        del stats["rules"][key]
        return
    rule["total"] = entry["injections"]
    rule["last"] = entry["last"]
    rule["repos"] = {repo: entry}
