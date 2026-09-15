"""What the hook's usage stats say about each rule, read by `status`.

Two deterministic signals a human needs before pruning or narrowing a rule:
a rule that has never fired since stats began, and a rule that fires often
but always under one subfolder of the glob it declares. A rule that also
declares `verify:` carries a third number — how often its command ran and how
often it failed — which is evidence of a different kind: not whether the rule
is read, but whether what it asks for holds."""

import datetime

from .common import HOOK

# Below this many injections a "always under one folder" pattern is noise.
MIN_INJECTIONS_TO_NARROW = 5
GLOB_METACHARS = "*?"

# ---- user-visible text ------------------------------------------------------
USAGE_LABEL = "injected {injections}x in {sessions} session(s), last {last}"
USAGE_REPEATS = "{reinjections} repeat(s)"
USAGE_VERIFICATIONS = "verified {verifications}, failed {failures}"
USAGE_SEPARATOR = ", "
NOTE_NEVER = ("never injected since usage stats began ({since}): {names} — a "
              "glob that matches nothing here, or a rule nobody needs")
NOTE_NARROW = ("{name}: injected {injections}x, always under {common!r}, while "
               "its glob {glob!r} reaches wider — consider `update --rule "
               "{name!r} --glob {suggested!r}`")
# -----------------------------------------------------------------------------


def day_of(timestamp):
    if not timestamp:
        return "?"
    return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).date().isoformat()


def usage_of(stats, scope_dir, name):
    return stats["rules"].get(HOOK.rule_key(scope_dir, name))


def public_usage(entry):
    """The entry as `status --json` shows it: the counters and the dates,
    never the session ids."""
    if entry is None:
        return None
    return {"injections": entry["injections"], "reinjections": entry["reinjections"],
            "sessions": entry["sessions"], "first": day_of(entry["first"]),
            "last": day_of(entry["last"]), "dirs": entry["dirs"],
            "globs": entry["globs"], "verifications": entry["verifications"],
            "failures": entry["failures"]}


def usage_label(entry):
    """The one-line summary `status` prints after a rule, or None when there is
    nothing recorded to print.

    Each half is left out when its counter is zero, and for the same reason:
    a rule that never repeats and never verifies would otherwise carry two
    columns of zeroes on every line. A rule that only ever verified — its glob
    covers writes, its text has never been injected — is not made to claim
    "injected 0x, last ?" either; it reports the number it has."""
    if entry is None:
        return None
    parts = []
    if entry["injections"]:
        parts.append(USAGE_LABEL.format(injections=entry["injections"],
                                        sessions=entry["sessions"],
                                        last=day_of(entry["last"])))
    if entry["reinjections"]:
        parts.append(USAGE_REPEATS.format(reinjections=entry["reinjections"]))
    if entry["verifications"]:
        parts.append(USAGE_VERIFICATIONS.format(
            verifications=entry["verifications"], failures=entry["failures"]))
    return USAGE_SEPARATOR.join(parts) or None


def never_injected(entry):
    """Whether a rule's text has never reached a model since stats began.

    Not the same as "has no entry": a rule whose `verify:` ran has an entry
    with zero injections, and it is exactly as unread as one with no entry at
    all — a command running says nothing about the guidance beside it."""
    return entry is None or not entry["injections"]


def segments_of(path):
    return [segment for segment in path.strip("/").split("/") if segment and segment != "."]


def glob_base_segments(glob):
    """The segments a glob names before it starts describing a set — the
    frame `matched_dir` recorded directories in, so the two compare."""
    base = []
    for segment in segments_of(glob):
        if any(ch in segment for ch in GLOB_METACHARS):
            break
        base.append(segment)
    return base


def common_prefix(dirs):
    """The path segments every recorded directory shares."""
    lists = [segments_of(directory) for directory in dirs]
    if not lists:
        return []
    prefix = lists[0]
    for segments in lists[1:]:
        length = 0
        while length < min(len(prefix), len(segments)) and prefix[length] == segments[length]:
            length += 1
        prefix = prefix[:length]
    return prefix


def narrowing_note(name, globs, entry):
    """A note when every recorded injection sits strictly below the glob's
    own base — only for a single-glob rule whose glob names a place at all
    (`**/x/**` names none, and a rule with several globs was widened on
    purpose)."""
    if entry is None or entry["injections"] < MIN_INJECTIONS_TO_NARROW:
        return None
    if len(globs) != 1 or not entry["dirs"]:
        return None
    glob = globs[0]
    base = glob_base_segments(glob)
    if not base:
        return None
    common = common_prefix(entry["dirs"])
    if len(common) <= len(base) or common[:len(base)] != base:
        return None
    common_path = "/".join(common)
    if glob.startswith("/"):
        common_path = "/" + common_path
    return NOTE_NARROW.format(name=name, injections=entry["injections"],
                              common=common_path + "/", glob=glob,
                              suggested=common_path + "/**")


def usage_notes(stats, scope_dir, rules):
    """Notes for one scope. `rules` is [(name, globs)]."""
    if not stats["rules"]:
        return []  # nothing recorded anywhere yet: silence, not forty "never"s
    notes = []
    never = [name for name, _globs in rules
             if never_injected(usage_of(stats, scope_dir, name))]
    if never:
        notes.append(NOTE_NEVER.format(since=day_of(stats["since"]),
                                       names=", ".join(never)))
    for name, globs in rules:
        note = narrowing_note(name, globs, usage_of(stats, scope_dir, name))
        if note:
            notes.append(note)
    return notes


def usage_since(stats):
    return day_of(stats["since"]) if stats["rules"] else None

