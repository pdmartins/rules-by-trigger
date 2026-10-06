"""How big the context is and whether it has shrunk: the measure behind
`remember_again_after` and the compaction fallback.

Split out of `state.py` when that module reached its line ceiling; the
session state itself stays there, and `coerce_seen_entry` is the one thing this
module needs back from it."""

import json
import os

from .constants import TOKEN_REGRESSION_SLACK, TRANSCRIPT_TAIL_BYTES, warn
from .state import coerce_seen_entry


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


def detect_context_regression(state, current_tokens):
    """Fallback for when SessionStart(compact|clear)'s async reset loses the
    race against the very next PreToolUse: the reset's `--reset-session`
    delete has not landed yet, so `injected_rules` still carries the
    pre-compaction high-water mark, and a rule whose text just got summarized
    out of context reads as "already delivered" and stays silent exactly when
    it needs to be repeated (this is the failure the reset exists to prevent;
    here it is caught late instead of not at all).

    Clears `injected_rules` in place — `calls` is untouched — and returns True
    when `current_tokens` has fallen more than TOKEN_REGRESSION_SLACK below
    the highest token count recorded on any `injected_rules` entry: a drop
    that size is compaction or /clear, not the ordinary jitter of which turn
    the transcript's last usage record happens to describe. `current_tokens is
    None` (no readable transcript) or no entry with a recorded token count
    both mean there is nothing to compare against, so nothing is cleared — a
    regression is never guessed at, only measured.
    """
    if current_tokens is None:
        return False
    injected_rules = state.get("injected_rules")
    if not isinstance(injected_rules, dict):
        return False
    recorded = [entry[1]
                for entry in map(coerce_seen_entry, injected_rules.values())
                if entry is not None and entry[1] is not None]
    if not recorded:
        return False
    max_recorded = max(recorded)
    if current_tokens + TOKEN_REGRESSION_SLACK < max_recorded:
        warn(f"context tokens dropped from {max_recorded} to {current_tokens}; "
             "compaction/clear likely won the race against the async reset, "
             "clearing injected rules so they re-inject on this call")
        injected_rules.clear()
        return True
    return False
