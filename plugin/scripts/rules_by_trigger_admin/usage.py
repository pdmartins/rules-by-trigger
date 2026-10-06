"""What the hook's usage stats say about each rule, and the improvement
candidate they make it.

Three deterministic signals a human needs before touching a rule: it never
fired (prune), it fires often but always under one subfolder of the glob it
declares (narrow), or the validator says its repeat distance does not fit its
text (repeat). `status --json` reports the first that applies."""

from .common import HOOK
from .reinforcement import reinforcement_reason

# Below this many injections a "always under one folder" pattern is noise.
MIN_INJECTIONS_TO_NARROW = 5
GLOB_METACHARS = "*?"

# What `status --json` calls each kind of candidate, in priority order.
CANDIDATE_PRUNE = "prune"
CANDIDATE_NARROW = "narrow"
CANDIDATE_REPEAT = "repeat"

# ---- user-visible text ------------------------------------------------------
REASON_PRUNE_PROJECT = "never fired in this repo"
REASON_PRUNE_GLOBAL = "never fired in any repo"
NOTE_NARROW = ("injected {injections}x, always under {common!r}, while its "
               "glob {glob!r} reaches wider — consider `update --rule "
               "{name!r} --glob {suggested!r}`")
# -----------------------------------------------------------------------------


def usage_of(stats, scope_dir, name, repo):
    """What the stats hold for a rule: this repo's entry (an empty one when the
    rule never fired here) plus the rule's total, or None when it has no
    record at all."""
    rule = stats["rules"].get(HOOK.rule_key(scope_dir, name))
    if rule is None:
        return None
    return {**(rule["repos"].get(repo) or HOOK.empty_entry()), "total": rule["total"]}


def counts_of(entry):
    """(fires in this repo, fires in total) — a fire is an injection. A rule
    with no record at all has fired nowhere."""
    if entry is None:
        return 0, 0
    return entry["injections"], entry["total"]


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


def candidate_of(name, fields, body, config, is_global, entry):
    """(candidate, reason) for one rule, or (None, None) when it needs nothing.

    A rule that fits several candidates gets the first in this order: prune,
    narrow, repeat. A disabled rule is never a candidate — it is already off.
    Prune asks for neither age nor minimum use. A project rule never fired
    when it has no fire in THIS repo; a global rule serves every repo, so only
    a total of 0 means nobody ever needed it."""
    if not HOOK.is_enabled(fields):
        return None, None
    this_repo, total = counts_of(entry)
    if (total if is_global else this_repo) == 0:
        return CANDIDATE_PRUNE, REASON_PRUNE_GLOBAL if is_global else REASON_PRUNE_PROJECT
    narrow = narrowing_note(name, HOOK.globs_of(fields), entry)
    if narrow:
        return CANDIDATE_NARROW, narrow
    repeat = reinforcement_reason(name, body, fields, config)
    if repeat:
        return CANDIDATE_REPEAT, repeat
    return None, None
