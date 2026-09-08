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

from .constants import MAX_WRITTEN_PATHS, warn

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


def coerce_written(value):
    """The list of written paths from whatever is on disk, or [] when it is
    unusable.

    Coerced entry by entry, the way `seen` is, and for the same reason: the
    state file may have been hand-edited or half-written, and a number where a
    path belongs would crash the glob matching at the end of the turn — after
    the writes have happened, with nothing left to fail open into. What
    survives is the non-empty strings, in the order the file had them, deduped
    and capped."""
    if not isinstance(value, list):
        return []
    written = []
    for entry in value:
        if isinstance(entry, str) and entry.strip() and entry not in written:
            written.append(entry)
    if len(written) > MAX_WRITTEN_PATHS:
        del written[:len(written) - MAX_WRITTEN_PATHS]
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
    written = state.get("written")
    if not isinstance(written, list):
        written = []
        state["written"] = written
    if abs_path in written:
        return
    written.append(abs_path)
    if len(written) > MAX_WRITTEN_PATHS:
        del written[:len(written) - MAX_WRITTEN_PATHS]
        _warn_cap()


def take_written(state):
    """The paths written since the last verification, clearing the list.

    Taken rather than read: the list answers "is there anything new to verify?",
    so the verification that consumed it must not be handed the same paths
    again. That is what ends a turn whose checks have already run, instead of
    verifying the same writes for as long as the turn stays open."""
    written = coerce_written(state.get("written"))
    state["written"] = []
    return written
