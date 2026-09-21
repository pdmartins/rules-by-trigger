"""When an already-delivered rule is due to be sent again, how a delivery is
keyed in `seen`, and the cleanup of the entry a rule edit leaves behind.

Split out of `state.py`, which owns the file on disk; this module owns the
questions the scheduler asks of what that file holds. The first two moved here
verbatim when the written-paths list pushed `state.py` past the 400-line
ceiling."""

import os

from .constants import AGENT_KEY_PREFIX, DEFAULT_REMEMBER_AGAIN_CALLS


def agent_key_prefix(payload):
    """What every `seen` key of this tool call starts with: "" in the main
    conversation, `agent::<agent_id>::` inside a subagent.

    `seen` answers "is this rule already in the context that will read this
    injection?". A subagent starts from an empty context of its own, yet shares
    the main conversation's `session_id`: keyed by session alone, a rule the
    main conversation had received never reached a subagent touching the same
    file. Claude Code sets `agent_id` only on a hook call made inside a
    subagent, one id per subagent — exactly the boundary of a context.

    A prefix inside the one state file, rather than a state file per agent,
    keeps `calls`, `written` and `rules_written` shared: a subagent's writes are
    verified by the main conversation's `Stop` hook, which reads them from that
    file. A prefix rather than a suffix keeps `pop_superseded_entries` from
    matching another context's entries. What it does not separate is the token
    count: `transcript_path` is the main conversation's even inside a subagent,
    so a token distance for a subagent's delivery measures the main context.

    `agent_id` arrives as JSON from another process: anything but a non-empty
    string counts as the main conversation, as every call did before."""
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return ""
    return f"{AGENT_KEY_PREFIX}{agent_id}::"


def rule_key_prefix(agent_prefix, scope_dir, name):
    """Every `seen` key of one rule in one context, up to its content hash."""
    return f"{agent_prefix}{os.path.realpath(scope_dir)}::{name}::"


def pop_superseded_entries(seen, scope_dir, name, digest, agent_prefix=""):
    """Remove and report whether `seen` held an entry for this same rule — same
    scope directory and name — under a DIFFERENT content hash. A hit means the
    rule was edited since that entry was recorded: the dedup key hashes the
    body, so the new text is injected on its own regardless, but the earlier
    wording stays in the transcript as a stale, contradictory instruction
    unless something clears it out. This is that cleanup.

    Only called once the caller has committed to injecting this delivery, so
    an edit that gets deferred (the char budget was full this call, say) is
    left alone — the caller detects the same edit again on the next attempt
    instead of losing the record of it. Left in place, a stale entry no longer
    drives any scheduling decision of its own (nothing keys off a digest that
    stopped matching), but it also never goes away: a rule edited several
    times in one session would otherwise leave one dead entry behind per edit
    for the rest of the session.

    `agent_prefix` confines the sweep to one context (see `agent_key_prefix`):
    a subagent receiving the edited text leaves the main conversation's entry
    for the old text in place, so the main conversation's own next delivery
    still says that it supersedes something.
    """
    prefix = rule_key_prefix(agent_prefix, scope_dir, name)
    current_key = prefix + digest
    stale = [key for key in seen if key.startswith(prefix) and key != current_key]
    for key in stale:
        del seen[key]
    return bool(stale)


def is_due(last_seen, call_number, tokens, interval, budget):
    """Whether a rule already delivered this session should be sent again.

    The question is only ever asked when the rule's glob matched the file being
    touched, so covering the distance is necessary but not sufficient: a rule
    governing a folder nobody opens again is never repeated, however long the
    session runs. Nor is it sufficient once `budget` reinjections have already
    been spent on this rule this session — only prohibition-style constraints
    are known to decay under long context (arXiv:2604.20911), and every
    reinjection adds one more instruction competing for the model's attention
    regardless of type (arXiv:2608.02639), so repetition is capped rather than
    left to run for the rest of a very long session.

    `interval` is (value, unit) as parsed from `remember_again_after`; a value of 0
    means never. A token distance in a session that cannot count tokens falls
    back to the default call count, which prefers a coarser schedule to silence.
    Converting between tokens and calls is never attempted — there is no
    faithful rate, and inventing one would misreport precision.
    """
    value, unit = interval
    if not value:
        return False
    last_calls, last_tokens, reinjections = last_seen
    if reinjections >= budget:
        return False
    if unit == "calls":
        return call_number - last_calls >= value
    if tokens is None or last_tokens is None:
        return call_number - last_calls >= DEFAULT_REMEMBER_AGAIN_CALLS
    return tokens - last_tokens >= value
