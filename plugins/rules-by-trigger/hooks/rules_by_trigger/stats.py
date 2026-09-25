"""Per-rule usage, kept across sessions: how often a rule was injected, in how
many sessions, when last, under which directories and through which glob.

Session state answers "was this rule already delivered?" and is thrown away;
this file answers "is this rule earning its place?" and is kept. It is what
lets `status` say a rule has never fired, or has fired forty times but always
under one subfolder of the glob it declares — the two signals a human needs to
prune or narrow a rule with evidence instead of a hunch.

Written only on the calls that actually inject, after the payload has been
flushed, under its own lock, and every failure degrades to "no usage recorded"
— never to a blocked or delayed tool call."""

import json
import os
import stat
import time

from .constants import (MAX_STATS_DIRS_PER_RULE, MAX_STATS_GLOBS_PER_RULE,
                        MAX_STATS_RECENT_SESSIONS, MAX_STATS_RULES,
                        STATS_FILE_NAME, STATS_READ_LIMIT_BYTES, warn)
from .state import lock_exclusive, state_dir

STATS_VERSION = 1


def stats_path():
    directory = state_dir()
    return os.path.join(directory, STATS_FILE_NAME) if directory else None


def rule_key(scope_dir, name):
    return f"{os.path.realpath(scope_dir)}::{name}"


def empty_stats():
    return {"version": STATS_VERSION, "since": int(time.time()), "rules": {}}


def empty_entry():
    """A rule with nothing recorded yet. `verifications`/`failures` are part of
    the shape rather than added on first use, so a stats file written before
    `verify:` existed loads with them at zero instead of raising here."""
    return {"injections": 0, "reinjections": 0, "sessions": 0,
            "recent_sessions": [], "first": None, "last": None,
            "dirs": {}, "globs": {}, "verifications": 0, "failures": 0}


def matched_dir(abs_path, base_dir):
    """The directory of the touched file, relative to the scope's base for a
    project scope and absolute for the global one — the same frame the rule's
    own glob is written in, so the two can be compared later."""
    if base_dir is None:
        return os.path.dirname(abs_path).replace(os.sep, "/") or "/"
    relative = os.path.relpath(abs_path, base_dir).replace(os.sep, "/")
    return os.path.dirname(relative) or "."


def bump_bounded(counter, key, cap):
    """Count `key`, keeping at most `cap` keys: a newcomer past the cap evicts
    the least-counted key, so what survives is the frequent set, not the first
    set seen."""
    if key in counter:
        counter[key] += 1
        return
    if len(counter) >= cap:
        smallest = min(counter, key=counter.get)
        if counter[smallest] > 1:
            return  # the newcomer has 1 and would only evict something rarer
        del counter[smallest]
    counter[key] = 1


def record_entry(entry, session_id, now, directory, glob, repeat):
    """`directory` is None for a call trigger — there is no touched file to
    place under a folder — and then the `dirs` counter is simply left alone;
    everything else is recorded exactly as for a path trigger, including the
    `globs` counter, which holds the call's trigger text instead of a glob."""
    entry["injections"] += 1
    if repeat:
        entry["reinjections"] += 1
    entry["first"] = entry["first"] or now
    entry["last"] = now
    if session_id and session_id not in entry["recent_sessions"]:
        entry["sessions"] += 1
        entry["recent_sessions"].append(session_id)
        del entry["recent_sessions"][:-MAX_STATS_RECENT_SESSIONS]
    if directory is not None:
        bump_bounded(entry["dirs"], directory, MAX_STATS_DIRS_PER_RULE)
    if glob:
        bump_bounded(entry["globs"], glob, MAX_STATS_GLOBS_PER_RULE)


def coerce_entry(value):
    """A stored entry with every field present and of the right type, so a
    hand-edited or half-written file cannot crash the arithmetic above."""
    entry = empty_entry()
    if not isinstance(value, dict):
        return entry
    for key in ("injections", "reinjections", "sessions",
                "verifications", "failures"):
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool):
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


def read_stats(raw):
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        return empty_stats()
    since = data.get("since")
    return {"version": STATS_VERSION,
            "since": since if isinstance(since, int) else int(time.time()),
            "rules": {key: coerce_entry(value)
                      for key, value in data["rules"].items() if isinstance(key, str)}}


def evict_oldest(rules):
    """Keep the file bounded: past the cap, the rules injected longest ago go."""
    while len(rules) > MAX_STATS_RULES:
        oldest = min(rules, key=lambda key: rules[key]["last"] or 0)
        del rules[oldest]


def update_stats(apply):
    """Read the usage file under an exclusive lock, hand the parsed stats to
    `apply`, and write back what it left behind.

    The file handling is the delicate part — a lock, a symlink refusal, a
    bounded read, a truncating rewrite — and both recorders below need exactly
    it. Moved here verbatim from `record_injections`, whose body it was, so
    there is one copy to keep right instead of two to keep in step."""
    path = stats_path()
    if path is None:
        return
    fd = None
    try:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            warn(f"usage stats path {path} is not a regular file; ignoring it")
            return
        lock_exclusive(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, STATS_READ_LIMIT_BYTES)
        stats = read_stats(raw)
        apply(stats)
        evict_oldest(stats["rules"])
        payload = json.dumps(stats).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, payload)
    except Exception as exc:  # noqa: BLE001 - usage is never worth a failed call
        warn(f"usage stats not recorded: {exc}")
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def record_injections(session_id, deliveries, abs_path):
    """Count one injection per delivered rule. `deliveries` is
    [(scope_dir, base_dir, name, glob, repeat)], in the order they were sent.

    `abs_path` is None for a call trigger (see `main.inject_for_call`): there
    is no touched file, so `matched_dir` — which would crash on
    `os.path.dirname(None)` — is never called; `record_entry` then leaves the
    `dirs` counter alone and records everything else as usual."""
    if not deliveries:
        return

    def apply(stats):
        now = int(time.time())
        for scope_dir, base_dir, name, glob, repeat in deliveries:
            entry = stats["rules"].setdefault(rule_key(scope_dir, name), empty_entry())
            directory = matched_dir(abs_path, base_dir) if abs_path is not None else None
            record_entry(entry, session_id, now, directory, glob, repeat)

    update_stats(apply)


def record_verifications(session_id, outcomes):
    """Count one verification per rule that asked for a command that ran.
    `outcomes` is [(scope_dir, name, passed)], in the order the commands ran.

    A command two rules share is counted for both: it ran once, and it answered
    for each of them. What this does NOT touch is any of the injection fields —
    `sessions`, `recent_sessions` and `last` say how often a rule's TEXT
    reached a model, and a verification puts no text in front of anyone. That
    is also why `session_id` is taken and not stored: the parameter keeps the
    two recorders one shape, and counting a verification as a session would
    make `status` claim an injection that never happened."""
    if not outcomes:
        return

    def apply(stats):
        for scope_dir, name, passed in outcomes:
            entry = stats["rules"].setdefault(rule_key(scope_dir, name), empty_entry())
            entry["verifications"] += 1
            if not passed:
                entry["failures"] += 1

    update_stats(apply)


def load_stats():
    """The stored usage, or an empty structure when there is none — for the
    admin CLI, which reads it and never writes it."""
    path = stats_path()
    if path is None or not os.path.isfile(path) or os.path.islink(path):
        return empty_stats()
    try:
        with open(path, "rb") as handle:
            return read_stats(handle.read(STATS_READ_LIMIT_BYTES))
    except OSError as exc:
        warn(f"usage stats unreadable: {exc}")
        return empty_stats()
