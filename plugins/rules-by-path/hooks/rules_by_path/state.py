"""Per-session state: what has already been injected, how far the context
has moved since, and when a rule is due to be sent again.

Every failure in here degrades to "stateless but still injecting" — never to
a blocked tool call."""

import hashlib
import json
import os
import re
import stat
import time

from .constants import (MAX_RULES_WRITTEN, MAX_SESSION_ID_CHARS,
                        STATE_MAX_AGE_SECONDS,
                        STATE_READ_CHUNK_BYTES, STATS_FILE_NAME,
                        TOKEN_REGRESSION_SLACK, TRANSCRIPT_TAIL_BYTES,
                        coerce_int, warn)
from .discovery import is_safely_owned
# Re-exported: `is_due` and `pop_superseded_entries` moved to `due.py` when this
# module reached its line ceiling, and their callers still address them here.
from .due import is_due, pop_superseded_entries  # noqa: F401
from .written import coerce_written


def lock_exclusive(fd):
    """Best-effort exclusive lock on fd. POSIX flock, msvcrt on Windows;
    silently degrades to no lock (dedup then tolerates a rare double
    injection instead of ever blocking the tool call)."""
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    except ImportError:
        pass
    except Exception as exc:
        warn(f"flock failed: {exc}")
        return
    try:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    except Exception as exc:
        warn(f"lock unavailable ({exc}); proceeding without it")


def state_dir_candidates():
    """The directories to try, in order: the plugin's own data directory, then
    ~/.claude/cache, then a per-uid temp directory.

    A generator so the last one is never built unless the ones before it fail,
    and `tempfile` is imported inside it for the same reason. That import pulls
    in `shutil`, `zlib`, `bz2` and `lzma`, and `gettempdir()` probes by creating
    and unlinking a file — startup the hook was paying on every single tool call
    to produce a path it almost never reaches."""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        yield os.path.join(plugin_data, "state")
    yield os.path.join(os.path.expanduser("~"), ".claude", "cache",
                       "rules-by-path")
    import tempfile
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    yield os.path.join(tempfile.gettempdir(), f"rules-by-path-state{suffix}")


def state_dir(create=True):
    """Where per-session state lives — the first candidate that exists, is
    ours, and is writable.

    `create=False` asks only where the state ALREADY is, and says nothing when
    there is none. The `Stop` hook runs at the end of every turn of every
    session, including the sessions of users who have no rules anywhere: for
    them the answer is "no state, nothing to verify", and it must cost neither
    a directory left behind nor a warning about a directory nobody asked for.
    """
    for candidate in state_dir_candidates():
        try:
            if create:
                os.makedirs(candidate, mode=0o700, exist_ok=True)
            if os.path.islink(candidate) or not os.path.isdir(candidate):
                continue
            if not is_safely_owned(candidate):
                warn(f"ignoring state directory {candidate}: not safely owned")
                continue
            if os.access(candidate, os.W_OK):
                return candidate
        except Exception:
            continue
    if create:
        warn("no writable state directory; rules will re-inject on every tool call")
    return None


def state_file_for(session_id, create=True):
    """The state file for a session id, which arrives as JSON from another
    process and is therefore not to be trusted as a string.

    `create` is passed straight to `state_dir`: a caller that only wants to
    READ an existing state (the Stop hook) asks for no directory to be made.

    Everything else in this area degrades to "stateless but still injecting";
    this used to be the one line that could do worse. `re.sub` raises TypeError
    on a non-string, and this call sits outside main()'s try, so a numeric or
    absent-typed id took the whole injection down — the user's global rules
    included — instead of costing only the dedup. An over-long id had the
    mirror-image effect: ENAMETOOLONG on every save, so every rule re-injected
    in full on every single tool call."""
    directory = state_dir(create)
    if directory is None:
        return None
    raw = session_id if isinstance(session_id, str) and session_id.strip() else "default"
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", raw)
    if len(safe_id) > MAX_SESSION_ID_CHARS:
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
        safe_id = safe_id[:MAX_SESSION_ID_CHARS - len(digest) - 1] + "-" + digest
    return os.path.join(directory, (safe_id or "default") + ".json")


def coerce_seen_entry(value):
    """[call number, context tokens or None, reinjections already sent] from
    whatever is on disk, or None when the entry is unusable. Accepts the bare
    integer written by earlier versions (call number only) and the
    two-element list that predates the reinjection budget — both are missing
    the third slot, which is filled with 0: an entry written before the
    budget existed has spent none of it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return [value, None, 0]
    if not isinstance(value, list) or not value:
        return None
    calls = coerce_int(value[0], None)
    if calls is None:
        return None
    tokens = coerce_int(value[1], None) if len(value) > 1 else None
    reinjections = coerce_int(value[2], 0) if len(value) > 2 else 0
    return [calls, tokens, reinjections]


def context_size(payload):
    """Tokens of context in this session, or None when it cannot be measured.

    The count is read from the transcript the harness already writes: the last
    `usage` record is what the API itself billed, not an estimate from character
    counts. Only the tail of the file is read — a transcript reaches several
    megabytes, and reading one per tool call would cost more than every other
    thing this hook does put together.

    Two known imprecisions, both acceptable against a threshold of tens of
    thousands: the record describes the *previous* request, so it lags by one
    turn; and after a compaction the number drops, which is exactly when
    SessionStart(compact) already clears the state.

    Returns None when there is no transcript, it cannot be read, or no usage
    record is found — the caller then falls back to counting tool calls.
    This is a capability, not a dependency: losing it costs precision, not
    function.
    """
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > TRANSCRIPT_TAIL_BYTES:
                handle.seek(size - TRANSCRIPT_TAIL_BYTES)
                handle.readline()  # drop the partial line the seek landed in
            tail = handle.read()
    except OSError as exc:
        warn(f"transcript not readable ({exc}); counting tool calls instead")
        return None
    # Backwards: the answer is the LAST usable record in the tail, so the first
    # one found from the end is it — the records before it were parsed in full
    # only to be overwritten.
    for line in reversed(tail.decode("utf-8", "replace").splitlines()):
        if '"usage"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        message = record.get("message")
        message = message if isinstance(message, dict) else {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        counted = 0
        for key in ("input_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value > 0:
                counted += value
        if counted:
            return counted
    return None


def empty_state(rules_written=None):
    """The state shape shared by every caller that builds one from scratch —
    `open_state` when there is nothing to read yet, and `reset_session` in
    `main.py` when a compact/clear drops everything but the session's own
    rule files. One definition means a key added here later cannot be
    silently dropped by whichever caller forgot to add it too.

    `rules_written` seeds the one key a reset deliberately keeps; every
    other caller leaves it at the default, empty list."""
    return {"calls": 0, "seen": {}, "written": [],
            "rules_written": list(rules_written) if rules_written else []}


def open_state(state_path):
    """Open the session state under an exclusive lock: (fd, state).

    state = {"calls": int,
             "seen": {dedup_key: [call number, context tokens or None,
                                  reinjections already sent]},
             "written": [absolute path, ...],
             "rules_written": [absolute path, ...]}.

    Both measures are recorded because rules choose their own unit: one rule may
    ask to be repeated every 30k tokens and another every 25 calls, in the same
    session. Storing only the session's preferred unit would silently ignore
    whichever rule disagreed with it.

    `written` is the third key and answers a different question: which files
    this turn has written since the last verification ran. It lives in the
    session state because `Stop`, the only hook that closes a turn, is told
    nothing about files, so a `verify:` command can only learn what to check
    from the trail the write path leaves here (see `written.py`). A missing or
    malformed value is coerced to [] like the rest, so a state file nobody can
    parse costs a verification rather than the turn.

    `rules_written` is the fourth, and the only one that outlives a turn: the
    rule files this SESSION wrote itself, whose `verify:` therefore waits for
    the next session (see `record_rules_written`).

    Parallel tool calls each spawn a hook process, so the read-decide-write
    cycle is serialized; on any failure the hook proceeds statelessly rather
    than blocking the tool call."""
    empty = empty_state()
    if state_path is None:
        return None, empty
    try:
        # O_NOFOLLOW: this is the one file the hook opens for WRITING, and
        # save_state truncates it. A symlink planted at that path would have its
        # target destroyed and replaced with the hook's JSON. The ELOOP lands in
        # the except below, which degrades to stateless — the fail-open contract.
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(state_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            warn(f"state path {state_path} is not a regular file; ignoring it")
            os.close(fd)
            return None, empty
        lock_exclusive(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while True:
            chunk = os.read(fd, STATE_READ_CHUNK_BYTES)
            if not chunk:
                break
            raw += chunk
        if not raw.strip():
            return fd, empty
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            # Unreadable state (a crash mid-write, or an older format): start
            # over and KEEP the fd so the next save overwrites it. Returning
            # without the fd would leave it broken for the whole session, and
            # every rule would re-inject in full on every single tool call.
            warn(f"state file {state_path} unreadable; starting a fresh one")
            return fd, empty
        if not isinstance(data, dict):
            return fd, empty
        # Coerce the shape while the fd is still held, so a value of the wrong
        # type repairs on the next save instead of dropping the fd (which would
        # make every tool call re-parse the same corrupt file all session). A
        # non-int `calls` must not spam full re-injections, and a malformed
        # `seen` entry must not crash the arithmetic in main() — that crash
        # aborts the whole injection, taking the user's global rules with it, on
        # every single tool call until the session ends.
        calls = coerce_int(data.get("calls") or 0, 0)
        raw_seen = data.get("seen")
        seen = {}
        if isinstance(raw_seen, dict):
            for entry_key, entry_value in raw_seen.items():
                entry = coerce_seen_entry(entry_value)
                if entry is not None:
                    seen[entry_key] = entry
        written = coerce_written(data.get("written"))
        rules_written = coerce_written(data.get("rules_written"),
                                       MAX_RULES_WRITTEN)
        return fd, {"calls": calls, "seen": seen, "written": written,
                    "rules_written": rules_written}
    except Exception as exc:
        warn(f"failed reading state {state_path}: {exc}")
        return None, empty


def save_state(fd, state):
    if fd is None:
        return
    try:
        payload = json.dumps(state).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.truncate(fd, 0)
        os.write(fd, payload)
    except Exception as exc:
        warn(f"failed writing state: {exc}")


def close_state(fd):
    if fd is None:
        return
    try:
        os.close(fd)  # releases the lock
    except Exception as exc:
        warn(f"failed closing state: {exc}")


def cleanup_stale_state():
    directory = state_dir()
    if directory is None:
        return
    try:
        cutoff = time.time() - STATE_MAX_AGE_SECONDS
        with os.scandir(directory) as it:
            for entry in it:
                if entry.name == STATS_FILE_NAME:
                    continue  # usage outlives sessions by design
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    os.unlink(entry.path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        warn(f"state cleanup failed: {exc}")


def detect_context_regression(state, current_tokens):
    """Fallback for when SessionStart(compact|clear)'s async reset loses the
    race against the very next PreToolUse: the reset's `--reset-session`
    delete has not landed yet, so `seen` still carries the pre-compaction
    high-water mark, and a rule whose text just got summarized out of context
    reads as "already delivered" and stays silent exactly when it needs to be
    repeated (this is the failure the reset exists to prevent; here it is
    caught late instead of not at all).

    Clears `seen` in place — `calls` is untouched — and returns True when
    `current_tokens` has fallen more than TOKEN_REGRESSION_SLACK below the
    highest token count recorded on any seen entry: a drop that size is
    compaction or /clear, not the ordinary jitter of which turn the
    transcript's last usage record happens to describe. `current_tokens is
    None` (no readable transcript) or no seen entry with a recorded token
    count both mean there is nothing to compare against, so nothing is
    cleared — a regression is never guessed at, only measured.
    """
    if current_tokens is None:
        return False
    seen = state.get("seen")
    if not isinstance(seen, dict):
        return False
    recorded = [entry[1] for entry in map(coerce_seen_entry, seen.values())
                if entry is not None and entry[1] is not None]
    if not recorded:
        return False
    max_recorded = max(recorded)
    if current_tokens + TOKEN_REGRESSION_SLACK < max_recorded:
        warn(f"context tokens dropped from {max_recorded} to {current_tokens}; "
             "compaction/clear likely won the race against the async reset, "
             "clearing seen rules so they re-inject on this call")
        seen.clear()
        return True
    return False
