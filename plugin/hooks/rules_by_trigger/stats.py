"""Per-rule usage, kept across sessions: how often a rule was injected, in how
many sessions, when last, under which directories and through which glob —
counted per repository and rule, with a total for the rule (see
`statsformat.py` for the shape).

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
                        MAX_STATS_RECENT_SESSIONS, STATS_FILE_NAME,
                        STATS_READ_LIMIT_BYTES, STATS_VERSION, warn)
from .statsconstants import (ASIDE_SUFFIX, LEGACY_STATS_FILE_NAME,
                             LOCK_SUFFIX, TEMP_SUFFIX)
from .repo import counts_for
from .state import lock_exclusive, state_dir
from .statsformat import (coerce_rules, drop_rule, empty_entry, empty_rule,
                          empty_stats, enforce_caps, keep_one_repo, move_rule)
from .statsmigrate import convert_v1

LEGACY_VERSION = 1
TRIM_FRACTION = 10  # a write over the size ceiling drops 1/10th of the repo entries
# What reading the bytes of the file concluded.
READ_FRESH = "fresh"          # nothing there yet
READ_CURRENT = "current"      # a version-2 file, nothing to do
READ_REJECTED = "rejected"    # corrupt or an unknown version: set it aside


def stats_path():
    directory = state_dir()
    return os.path.join(directory, STATS_FILE_NAME) if directory else None


def legacy_stats_path():
    directory = state_dir()
    return os.path.join(directory, LEGACY_STATS_FILE_NAME) if directory else None


def rule_key(scope_dir, name):
    return f"{os.path.realpath(scope_dir)}::{name}"


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


def parse_stats(raw):
    """(stats, what was read) for the bytes of the usage file. A file that is
    not JSON, not an object, of another version or with no `rules` is
    rejected."""
    if not raw.strip():
        return empty_stats(), READ_FRESH
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return empty_stats(), READ_REJECTED
    if (not isinstance(data, dict) or not isinstance(data.get("rules"), dict)
            or data.get("version") != STATS_VERSION):
        return empty_stats(), READ_REJECTED
    since = data.get("since")
    return {"version": STATS_VERSION,
            "since": since if isinstance(since, int) else int(time.time()),
            "rules": coerce_rules(data["rules"])}, READ_CURRENT


def read_legacy_stats():
    """Version-2 stats converted from the first format's file, or an empty
    structure when there is none or it reads fine but cannot be used. The file
    is only ever read, never written: sessions still running the old hook keep
    writing it, and what they add after this conversion is not counted again.

    A file that exists but cannot be READ (permission, I/O, a lock held
    elsewhere) raises OSError: starting a new file from zero over a passing
    failure would lose the old history for good, so the caller records nothing
    this time and the next read tries the conversion again."""
    path = legacy_stats_path()
    try:
        raw = read_raw(path) if path else None
    except OSError as exc:
        raise OSError(f"first-format usage stats {path} unreadable ({exc}); "
                      f"nothing recorded, the conversion is retried next time") from exc
    if raw is None:
        return empty_stats()
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        data = None
    if (not isinstance(data, dict) or not isinstance(data.get("rules"), dict)
            or data.get("version") != LEGACY_VERSION):
        warn(f"{path} is not a usable first-format usage file; counting from zero")
        return empty_stats()
    return convert_v1(data)


def read_raw(path):
    """The file's bytes, None when there is none. Never follows a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"usage stats path {path} is not a regular file")
        return os.read(fd, STATS_READ_LIMIT_BYTES)
    finally:
        os.close(fd)


def set_aside(path):
    """Keep a file the plugin cannot use next to where it was, so the count
    that starts over is not also a count that was thrown away."""
    aside = f"{path}{ASIDE_SUFFIX}{int(time.time())}"
    os.replace(path, aside)
    warn(f"usage stats {path} unreadable or of an unknown version; kept as "
         f"{aside} and starting from zero")


def serialize(stats):
    """The bytes of the file. Past the size ceiling the repo entries fired
    longest ago are dropped first (rule totals stay), so what is written can
    always be read back."""
    payload = json.dumps(stats).encode("utf-8")
    while len(payload) > STATS_READ_LIMIT_BYTES:
        entries = sorted(((entry["last"] or 0, key, repo)
                          for key, rule in stats["rules"].items()
                          for repo, entry in rule["repos"].items()),
                         key=lambda item: item[0])
        if not entries:
            raise ValueError("usage stats exceed the size ceiling with no repo entry to drop")
        for _last, key, repo in entries[:max(1, len(entries) // TRIM_FRACTION)]:
            del stats["rules"][key]["repos"][repo]
        payload = json.dumps(stats).encode("utf-8")
    return payload


def write_atomic(path, stats):
    """Write the file whole under a temporary name and rename it into place:
    a reader sees the old file or the new one, never half of either."""
    payload = serialize(stats)
    temp = f"{path}{TEMP_SUFFIX}{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        os.unlink(temp)
    except FileNotFoundError:
        pass
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def update_stats(apply):
    """Read the usage file under an exclusive lock, hand the parsed stats to
    `apply`, and write back what it left behind — returned, or None when
    nothing could be read or written.

    Because the file is replaced by renaming, the lock lives on a file of its
    own: a lock on the data file would stop meaning anything the moment a
    writer swapped it. What there is to start from is decided here, under the
    lock: with no file yet, the first format's file is converted once into the
    new one; a corrupt one is set aside — so two processes arriving together
    cannot both convert."""
    path = stats_path()
    if path is None:
        return None
    lock_fd = None
    try:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(path + LOCK_SUFFIX, flags, 0o600)
        lock_exclusive(lock_fd)
        raw = read_raw(path)
        if raw is None:
            stats, verdict = read_legacy_stats(), READ_FRESH
        else:
            stats, verdict = parse_stats(raw)
        if verdict == READ_REJECTED:
            set_aside(path)
        apply(stats)
        enforce_caps(stats)
        write_atomic(path, stats)
        return stats
    except Exception as exc:  # noqa: BLE001 - usage is never worth a failed call
        warn(f"usage stats not recorded: {exc}")
        return None
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass


def record_injections(session_id, repo, deliveries, abs_path):
    """Count one firing per delivered rule, for `repo` and in the rule's total.
    `deliveries` is [(scope_dir, base_dir, name, glob, repeat)], in the order
    they were sent.

    `abs_path` is None for a call trigger (see `main.inject_for_call`): there
    is no touched file, so `matched_dir` — which would crash on
    `os.path.dirname(None)` — is never called; `record_entry` then leaves the
    `dirs` counter alone and records everything else as usual. A project rule
    above the repository's root is not counted (see `repo.counts_for`)."""
    deliveries = [d for d in deliveries if counts_for(d[0], repo)]
    if not deliveries:
        return

    def apply(stats):
        now = int(time.time())
        for scope_dir, base_dir, name, glob, repeat in deliveries:
            rule = stats["rules"].setdefault(rule_key(scope_dir, name), empty_rule())
            rule["total"] += 1
            rule["last"] = now
            if repo is None:
                continue
            directory = matched_dir(abs_path, base_dir) if abs_path is not None else None
            record_entry(rule["repos"].setdefault(repo, empty_entry()),
                         session_id, now, directory, glob, repeat)

    update_stats(apply)


def record_verifications(session_id, repo, outcomes):
    """Count one verification per rule that asked for a command that ran, in
    `repo`'s entry. `outcomes` is [(scope_dir, name, passed)], in the order the
    commands ran.

    A command two rules share is counted for both: it ran once, and it answered
    for each of them. What this does NOT touch is any of the injection fields —
    `sessions`, `recent_sessions` and `last` say how often a rule's TEXT
    reached a model, and a verification puts no text in front of anyone, so it
    does not touch the rule's total either. That is also why `session_id` is
    taken and not stored: the parameter keeps the two recorders one shape."""
    outcomes = [o for o in outcomes if counts_for(o[0], repo)]
    if not outcomes or repo is None:
        return

    def apply(stats):
        for scope_dir, name, passed in outcomes:
            rule = stats["rules"].setdefault(rule_key(scope_dir, name), empty_rule())
            entry = rule["repos"].setdefault(repo, empty_entry())
            entry["verifications"] += 1
            if not passed:
                entry["failures"] += 1

    update_stats(apply)


def drop_rule_usage(scope_dir, name):
    """A rule was deleted: forget its usage. Answers what `update_stats` does:
    None when the file could not be read or written, so a caller that must not
    carry on after a failed drop (the CLI's `remove --delete`) can tell."""
    return update_stats(lambda stats: drop_rule(stats, rule_key(scope_dir, name)))


def move_rule_usage(old_scope_dir, old_name, new_scope_dir, new_name):
    """A rule was renamed, or moved between scopes: its usage follows."""
    update_stats(lambda stats: move_rule(stats, rule_key(old_scope_dir, old_name),
                                         rule_key(new_scope_dir, new_name)))


def keep_one_repo_usage(scope_dir, name, repo):
    """A global rule became a project rule of `repo`: keep only that repo."""
    update_stats(lambda stats: keep_one_repo(stats, rule_key(scope_dir, name), repo))


def load_stats():
    """The stored usage, or an empty structure when there is none — for the
    admin CLI. Reading is lock-free; a file to set aside, or the first
    format's file with no new one yet, is handed to `update_stats`, which
    decides again under the lock and writes, so the first reader converts and
    every later one finds it done."""
    path = stats_path()
    if path is None or os.path.islink(path):
        return empty_stats()
    if not os.path.exists(path):
        legacy = legacy_stats_path()
        if legacy is None or not os.path.isfile(legacy):
            return empty_stats()
        written = update_stats(lambda _stats: None)  # warns when it cannot
        return empty_stats() if written is None else written
    try:
        stats, verdict = parse_stats(read_raw(path) or b"")
    except OSError as exc:
        warn(f"usage stats unreadable: {exc}")
        return empty_stats()
    if verdict == READ_REJECTED:
        written = update_stats(lambda _stats: None)
        return stats if written is None else written
    return stats
