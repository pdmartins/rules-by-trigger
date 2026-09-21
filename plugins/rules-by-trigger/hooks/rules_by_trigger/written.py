"""The paths written since the last verification: the trail `Stop` reads.

`Stop` is the only hook that closes a turn, and it is the one hook that is told
nothing about files. A `verify:` command therefore has no way of knowing what
the turn wrote unless the write path leaves a note — so `PreToolUse` appends
the path of every write it lets through, and the verification takes the list
and clears it. Without the clear, a turn whose verification already ran would
keep re-running it over the same writes instead of ending.

Session state is where the note lives (see `open_state`); this module is only
the three operations on that one key, kept apart so the file on disk and the
list inside it are not the same module's business."""

from .constants import MAX_RULES_WRITTEN, MAX_WRITTEN_PATHS, warn

# Once per process, and the hook is one process per tool call — so past the cap
# every further write warns. That is the honest reading of "warn once": there is
# no cheap way to remember across processes that the user has already been told,
# and inventing one (a flag in the state file) would spend a persisted field on
# saying nothing twice.
_CAP_WARNED = False


def _warn_cap():
    """Say, once in this process, that the oldest paths are being dropped."""
    global _CAP_WARNED
    if _CAP_WARNED:
        return
    _CAP_WARNED = True
    warn(f"more than {MAX_WRITTEN_PATHS} files written since the last "
         f"verification; the oldest paths are being dropped, so a rule that "
         f"covers only those is not verified this turn")


def coerce_written(value, cap=MAX_WRITTEN_PATHS):
    """The list of written paths from whatever is on disk, or [] when it is
    unusable.

    Coerced entry by entry, the way `injected_rules` is, and for the same
    reason: the state file may have been hand-edited or half-written, and a
    number where a path belongs would crash the glob matching at the end of
    the turn — after the writes have happened, with nothing left to fail open
    into. What survives is the non-empty strings, in the order the file had
    them, deduped and capped.

    `cap` is a parameter because the state holds two lists of paths with the
    same shape and different lifetimes — the turn's writes and the session's
    rule-file writes — and they answer to caps of their own."""
    if not isinstance(value, list):
        return []
    written = []
    for entry in value:
        if isinstance(entry, str) and entry.strip() and entry not in written:
            written.append(entry)
    if len(written) > cap:
        del written[:len(written) - cap]
    return written


def record_written(state, abs_path):
    """Note that this turn wrote `abs_path`, unless it is already noted.

    The path recorded is the literal absolute path the tool named — the same
    `abs_path` the injection's candidates are computed from, and deliberately
    not its resolved form. `path_targets` matches a rule's glob against both
    the literal path and the resolved one, and only one of the two recovers the
    other: whoever needs the resolved path calls `os.path.realpath` on this,
    while a path already resolved has lost the text the glob may have been
    written against.

    Deduped and insertion-ordered: a turn that edits the same file eleven times
    names it once, and the order is the order the files were first written in.
    Capped at MAX_WRITTEN_PATHS with the oldest dropped, because the list is
    written back to disk on every single write and the turn that produced a
    thousand of them is the turn that can least afford the state file growing
    without limit."""
    if not isinstance(abs_path, str) or not abs_path:
        return
    unverified_writes = state.get("unverified_writes")
    if not isinstance(unverified_writes, list):
        unverified_writes = []
        state["unverified_writes"] = unverified_writes
    if abs_path in unverified_writes:
        return
    unverified_writes.append(abs_path)
    if len(unverified_writes) > MAX_WRITTEN_PATHS:
        del unverified_writes[:len(unverified_writes) - MAX_WRITTEN_PATHS]
        _warn_cap()


def record_rules_written(state, abs_path, real_abs):
    """Note that this SESSION wrote a rule file, so a `verify:` that file
    carries does not run before the next session.

    The gate this feeds is the one Claude Code already has for its own hooks: a
    hook added to `settings.json` mid-session takes effect at the next startup,
    because the harness snapshots them. Without the mirror image here, a model
    that writes `verify: <anything>` into a rule file hands itself a shell for
    the rest of the turn — the Stop hook re-reads every frontmatter fresh, and
    the write path returns before recording anything, so nothing would notice.

    Both spellings are kept, the literal path the tool named and its resolved
    form, because the end of the turn compares a rule file it found by walking
    the scope, which may be reached through either. Deduped, insertion-ordered
    and capped like `unverified_writes`; unlike `unverified_writes` it is not
    taken by a verification and survives a state reset (see `reset_session`),
    because what it answers is about the session and not about the turn."""
    recorded = state.get("rules_written")
    if not isinstance(recorded, list):
        recorded = []
        state["rules_written"] = recorded
    added = False
    for path in (abs_path, real_abs):
        if not isinstance(path, str) or not path or path in recorded:
            continue
        recorded.append(path)
        added = True
    # Only a write that actually appended something can have pushed the list
    # past the cap. Without the flag, every re-write of an already-recorded rule
    # file in a session sitting at the cap would re-trim a list already at it
    # and warn again about a path nothing just dropped.
    if added and len(recorded) > MAX_RULES_WRITTEN:
        del recorded[:len(recorded) - MAX_RULES_WRITTEN]
        warn(f"more than {MAX_RULES_WRITTEN} rule files written in this "
             f"session; the oldest are forgotten, so a `verify:` this session "
             f"wrote into one of them may run before the next session")


def take_written(state):
    """The paths written since the last verification, clearing the list.

    Taken rather than read: the list answers "is there anything new to verify?",
    so the verification that consumed it must not be handed the same paths
    again. That is what ends a turn whose checks have already run, instead of
    verifying the same writes for as long as the turn stays open."""
    unverified_writes = coerce_written(state.get("unverified_writes"))
    state["unverified_writes"] = []
    return unverified_writes
