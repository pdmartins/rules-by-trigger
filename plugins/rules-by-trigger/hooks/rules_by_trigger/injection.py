"""Everything a matched tool call pays for once its scopes and candidates are
known: the configuration in force, the blocks built from those candidates,
and the shared delivery — session state, the legacy notice, the character
budget, the terminal notice, usage stats.

Moved out of `main.py` verbatim, past the point candidates are known, when a
`call:` trigger (see `matching.collect_call_candidates`) started reaching
this exact same delivery through a second door: the path branch of `main()`
and the call branch (`main.inject_for_call`) both hand off here rather than
each carrying its own copy."""

import hashlib
import json
import sys

from .constants import MAX_TOTAL_CHARS, WRITE_TOOL_NAMES, warn
from .config import (language, load_config, max_rule_chars,
                     remember_again_after_default, show_injections)
from .discovery import global_scope
from .due import agent_key_prefix, rule_key_prefix
from .frontmatter import remember_again_after_of
from .messages import LEGACY_NOTICE_KEY, messages_for
from .notice import build_pretooluse_output
from .reinject import reinject_budget
from .rules import read_rule_file
from .state import (cleanup_stale_state, close_state, context_size,
                    detect_context_regression, is_due, open_state,
                    pop_superseded_entries, save_state, state_file_for)
from .stats import record_injections
from .written import record_written


def config_for_scopes(scopes):
    """The configuration in force for a tool call reaching these scopes.

    The global scope, when there is one, is the first entry and the only
    trusted layer: everything after it is a project scope, whose config arrives
    with whatever repository the touched file belongs to."""
    trusted_count = 1 if global_scope(scopes) else 0
    return load_config([scope_dir for _base, scope_dir, _label in scopes],
                       trusted_count)


def over_budget(blocks, text, what):
    """True when `text` would push this injection past its character ceiling.

    The rules are not the only thing appended to one injection, and everything
    appended to it answers to the same ceiling — so the accounting and the
    wording live here, once, rather than being restated by each caller. A
    delivery held back is deliberately NOT recorded in `injected_rules`: the
    next tool call offers it again."""
    spent = sum(len(block["text"]) for block in blocks)
    if spent + len(text) <= MAX_TOTAL_CHARS:
        return False
    warn(f"injection budget of {MAX_TOTAL_CHARS} chars reached; {what} left "
         f"for the next tool call")
    return True


def build_blocks(candidates, config, injected_rules, call_number, tokens, agent_prefix):
    """The deliveries this tool call should inject, in candidate order.

    A candidate is delivered when this context (`agent_prefix`: the main
    conversation or one subagent) has not seen this exact version of it, or
    when `is_due` says it has moved far enough since. Each delivery goes into
    `injected_rules` as it is appended, so a rule the budget left out is
    retried on the next tool call instead of counting as delivered.
    """
    body_limit = max_rule_chars(config)
    budget = reinject_budget(config)
    default_interval = remember_again_after_default(config, tokens is not None)
    blocks = []
    for scope_dir, _label, name, glob, fields in candidates:
        result = read_rule_file(scope_dir, name, body_limit)
        if result is None:
            continue
        body, was_truncated = result[1], result[2]
        if not body:
            continue
        # The content hash is part of the key so an edited rule counts as a
        # new rule and is injected again, rather than being treated as
        # already delivered for the rest of the session.
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        key = rule_key_prefix(agent_prefix, scope_dir, name) + digest
        last_seen = injected_rules.get(key)

        if last_seen is None:
            # The first delivery is free: it never counts against the
            # reinjection budget, only the repeats that follow it do.
            text, truncated, reinjections = body, was_truncated, 0
        elif is_due(last_seen, call_number, tokens,
                    remember_again_after_of(fields) or default_interval,
                    budget):
            # Repeating means sending the rule again, whole: with no header
            # there is no way to mark a fragment as one. Short rules are
            # what keeps this cheap.
            text, truncated, reinjections = body, was_truncated, last_seen[2] + 1
        else:
            continue

        if over_budget(blocks, text, f"rule '{name}'"):
            continue
        # A fresh digest with an older entry still on file under the same
        # scope+name means the rule was edited: the stale copy is still
        # sitting in the transcript as a contradictory instruction, so the
        # delivery says so and the dead entry is dropped rather than
        # accumulating for the rest of the session. Only checked on a
        # first-time digest — a plain repeat of an already-delivered
        # version is not an edit.
        superseded = last_seen is None and pop_superseded_entries(
            injected_rules, scope_dir, name, digest, agent_prefix)
        blocks.append({"name": name, "text": text, "truncated": truncated,
                       "superseded": superseded, "scope_dir": scope_dir,
                       "glob": glob, "repeat": reinjections > 0})
        injected_rules[key] = [call_number, tokens, reinjections]
    return blocks


def deliver(payload, tool_name, scopes, candidates, legacy_scopes, abs_path):
    """Session state, injection, terminal notice and usage stats for a tool
    call whose scopes and candidates are already known — the common tail of
    both `main()` branches.

    `abs_path` is the touched file, or None for a call trigger (see
    `main.inject_for_call`): `record_written` only ever runs for
    WRITE_TOOL_NAMES, which a call-trigger tool never is, and
    `record_injections` is itself the one that knows what a None `abs_path`
    means (see `stats.record_entry`)."""
    state_path = state_file_for(payload.get("session_id"))
    state_fd, state = open_state(state_path)
    blocks = []
    try:
        state["calls"] = state.get("calls", 0) + 1
        call_number = state["calls"]
        # Every write is noted, whether or not a rule matched it here: the rule
        # whose `verify:` covers this file is selected at the end of the turn,
        # by the Stop hook, and that selection ignores the `tool:` filter this
        # call was matched under — so what matched HERE says nothing about what
        # will need verifying THERE. A denied write never reaches this line
        # (the block returned above): a path that was refused was never
        # written. Recording one string is all this costs; it must stay that
        # cheap, because it happens on the fast path below as well.
        if tool_name in WRITE_TOOL_NAMES:
            record_written(state, abs_path)
        # Nothing this file touches has a rule: the call counter still advances
        # — it is how `remember_again_after` measures distance in calls — but no
        # config.json, no transcript and no rule file is read. That is the shape
        # of most tool calls, and all three used to be paid for regardless.
        if candidates or legacy_scopes:
            # The language is resolved once, here, and travels down by
            # parameter — no module keeps it.
            config = config_for_scopes(scopes)
            messages = messages_for(language(config))
            tokens = context_size(payload)
            # Catches the compaction/clear the async SessionStart reset lost the
            # race against: a hard drop in tokens since the last recorded
            # injection means `injected_rules` still counts the summarized-away
            # text as in context. Must run before any dedup decision below.
            detect_context_regression(state, tokens)
            injected_rules = state["injected_rules"]
            agent_prefix = agent_key_prefix(payload)

            blocks = build_blocks(candidates, config, injected_rules, call_number,
                                  tokens, agent_prefix)

            # The legacy notice is told once per scope per context. Repeating it
            # on every tool call would be noise the user cannot silence except by
            # migrating, which is exactly what they may not be ready to do yet.
            # It rides in the same injection as the rules, so it answers to the
            # same ceiling.
            notice = messages[LEGACY_NOTICE_KEY]
            for label in legacy_scopes:
                key = f"{agent_prefix}legacy::{label}"
                if key in injected_rules:
                    continue
                if over_budget(blocks, notice,
                               f"the legacy-format notice for {label}"):
                    continue
                blocks.append({"name": "legacy-format", "text": notice})
                injected_rules[key] = [call_number, tokens, 0]

            if blocks:
                # Emit the injection and flush it BEFORE recording the rules as
                # injected: if the process dies in the window, the worst case is
                # re-injecting a rule (a harmless duplicate) rather than marking
                # it delivered when the model never received it. The design
                # prefers a rare double injection to loss.
                payload_out = json.dumps(build_pretooluse_output(
                    blocks, messages, bool(agent_prefix), show_injections(config)))
                sys.stdout.write(payload_out)
                sys.stdout.flush()
        save_state(state_fd, state)  # advances the call counter either way
    finally:
        close_state(state_fd)
    if blocks:
        # Usage is recorded after the injection is delivered and the session
        # state released: it is bookkeeping, and bookkeeping never gets to
        # delay or lose a delivery.
        base_dirs = {scope_dir: base_dir for base_dir, scope_dir, _label in scopes}
        record_injections(payload.get("session_id"), [
            (block["scope_dir"], base_dirs.get(block["scope_dir"]),
             block["name"], block["glob"], block["repeat"])
            for block in blocks if "scope_dir" in block], abs_path)
    # Best-effort maintenance, kept off the critical path: it runs after the
    # payload is delivered so a slow directory sweep can never delay or drop it.
    # It must be reached on the far more common no-injection path too — an
    # early `return` there meant the sweep only ever ran as a side effect of a
    # successful injection, so sessions that never matched a rule left their
    # state files behind forever.
    #
    # Once per session, on the call that finds the counter at 1: the sweep stats
    # every state file in the directory, and what it collects goes stale on the
    # order of days. Running it on every tool call spent that on each one.
    if call_number == 1:
        cleanup_stale_state()
