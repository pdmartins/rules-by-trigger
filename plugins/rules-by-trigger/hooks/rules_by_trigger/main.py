"""The four entry points Claude Code calls: the PreToolUse injection (`main`),
the SessionStart notice, the state reset on compact/clear, and the Stop
verification — which lives in `verify.py` and is only dispatched here."""

import hashlib
import json
import os
import sys

from .constants import (DEFAULT_LANGUAGE, MAX_TOTAL_CHARS,
                        WRITE_TOOL_NAMES, warn)
from .config import (language, load_config, max_rule_chars,
                     remember_again_after_default)
from .context import build_context, build_block_reason
from .messages import LEGACY_NOTICE_KEY, SESSION_NOTICE_KEY, messages_for
from .discovery import find_scopes, global_scope
from .due import agent_key_prefix, rule_key_prefix
from .frontmatter import block_of, remember_again_after_of
from .matching import (collect_candidates, extract_file_path,
                       is_inside_rules_dir)
from .reinject import reinject_budget
from .rules import read_rule_file
from .state import (cleanup_stale_state, close_state, context_size,
                    detect_context_regression, empty_state, is_due,
                    open_state, pop_superseded_entries, save_state,
                    state_file_for)
from .stats import record_injections
from .verify import verify_turn
from .written import record_rules_written, record_written


def config_for_scopes(scopes):
    """The configuration in force for a tool call reaching these scopes.

    The global scope, when there is one, is the first entry and the only
    trusted layer: everything after it is a project scope, whose config arrives
    with whatever repository the touched file belongs to."""
    trusted_count = 1 if global_scope(scopes) else 0
    return load_config([scope_dir for _base, scope_dir, _label in scopes],
                       trusted_count)


def messages_for_scopes(scopes):
    """The injected text, in the language these scopes configure.

    Never raises: every caller here emits something the reader needs more than
    they need it in their own language, so an unreadable configuration costs
    the translation and nothing else. `load_layer` already refuses to let one
    layer fail; this is the second belt, on the paths where losing the message
    is the expensive outcome."""
    try:
        return messages_for(language(config_for_scopes(scopes)))
    except Exception as exc:
        warn(f"configuration unreadable ({exc}); falling back to "
             f"{DEFAULT_LANGUAGE}")
        return messages_for(DEFAULT_LANGUAGE)


def blocking_rule(tool_name, scopes, candidates):
    """(rule name, rule body) for the first `block: true` rule that should
    block this tool call, or None when nothing should.

    The hook never validates a rule's content, only its path, so `block:` is
    not a policy engine — it is "the recommended hardening's `permissions.deny`,
    with the rule's own text as the reason a human or model reads for WHY, and
    without hand-authoring a permission entry".

    Trust gate, deliberately narrower than what merely MATCHED: only a rule
    from the GLOBAL scope may ever block. A project scope's rules arrive with
    whatever repository is checked out, and honouring `block:` there would
    let a cloned repository deny the user's own tool calls — an escalation the
    hook must never grant no matter how the frontmatter is worded. `block:`
    on a project-scope rule is simply inert here, silently (no warn on this hot
    path); `validate` is where it is pointed out, with `block --sync` as the
    way to turn it into an actual native deny for that project."""
    if tool_name not in WRITE_TOOL_NAMES:
        return None
    owner = global_scope(scopes)
    if owner is None:
        return None  # no global scope in play this call; nothing to trust
    trusted_scope = owner[1]
    for scope_dir, _label, name, _glob, fields in candidates:
        if scope_dir != trusted_scope or not block_of(fields):
            continue
        result = read_rule_file(scope_dir, name)
        if result is None or not result[1]:
            continue
        return name, result[1]
    return None


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


def main():
    payload = json.load(sys.stdin)
    raw_path = extract_file_path(payload)
    if not raw_path:
        return
    cwd = payload.get("cwd") or os.getcwd()
    abs_path = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
    abs_path = os.path.normpath(abs_path).replace(os.sep, "/")
    # Resolved once for the whole call: resolving a path walks every component
    # of it, and both checks below need the same answer.
    real_abs = os.path.realpath(abs_path).replace(os.sep, "/")
    tool_name = payload.get("tool_name")
    if is_inside_rules_dir(abs_path, real_abs):
        # A rule file never injects — and, when the model is the one WRITING it,
        # the `verify:` it may now carry must not run before the next session
        # (see `record_rules_written`). Only a write pays for this: a read of a
        # rule file, and every path outside the rules directory, return exactly
        # as they did.
        if tool_name in WRITE_TOOL_NAMES:
            state_fd, state = open_state(state_file_for(payload.get("session_id")))
            try:
                record_rules_written(state, abs_path, real_abs)
                save_state(state_fd, state)
            finally:
                close_state(state_fd)
        return

    scopes = find_scopes(os.path.dirname(abs_path))
    if not scopes:
        return
    candidates, legacy_scopes = collect_candidates(abs_path, real_abs, scopes,
                                                   tool_name)

    # The denial is decided before any configuration is read, and its wording
    # is then resolved from the trusted layers alone. Both halves are the same
    # rule: whether the machine owner's block fires, and what it says, may not
    # depend on a file that arrived with the repository being blocked.
    denial = blocking_rule(tool_name, scopes, candidates)
    if denial is not None:
        name, body = denial
        # A project deliberately wins `language` everywhere else, so its rules
        # come out in its own language. The block reason is the one sentence the
        # plugin speaks on the owner's behalf AGAINST a repository, and that
        # repository does not choose the language it is refused in.
        owner = global_scope(scopes)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": build_block_reason(
                    name, body, messages_for_scopes([owner] if owner else [])),
            },
        }))
        return

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
                payload_out = json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": build_context(blocks, messages),
                    },
                    "suppressOutput": True,
                })
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


def session_notice():
    """SessionStart: say up front that the rules directory is the plugin's
    business, not the agent's.

    Without this the agent meets the directory the only way it can — by listing,
    reading or grepping it — and collects a permission denial for every attempt,
    in every session, because the recommended hardening deny-lists exactly those
    paths. A denial explains nothing, so the attempt repeats in the next session.
    Saying it once, before anything is tried, costs about eighty tokens and only
    in sessions that actually have a scope; the denials cost more than that and
    teach nothing."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    scopes = find_scopes(os.path.abspath(cwd))
    if not scopes:
        return  # no rules anywhere near this session: say nothing at all
    # This path did not read any configuration before the notice had a language
    # to be written in. The cost is one small file per layer, once per session,
    # and only in sessions that have a scope at all — and the notice still goes
    # out in English if that read turns out to be unusable, because a repository
    # silencing this warning is exactly the outcome it exists to prevent.
    messages = messages_for_scopes(scopes)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": messages[SESSION_NOTICE_KEY],
        },
    }))


def reset_session():
    """SessionStart (source compact|clear) mode: drop the session's state so
    rules are re-injected on the next touch — compaction may have summarized
    the injected text away, and /clear discards it entirely.

    A payload it cannot read leaves nothing to reset, which is not a failure
    worth reporting: `cli` would otherwise print it as an unexpected error, and
    a hook whose whole job is to be invisible does not shout on stderr over a
    session it has no state for."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    state_path = state_file_for(payload.get("session_id"))
    if state_path is None or not os.path.isfile(state_path):
        return
    # One key survives: the rule files this session wrote itself. The rest of
    # the state is about a context that no longer holds what was injected into
    # it; `rules_written` is about the SESSION, and a /clear must not be the way
    # to make a `verify:` the model authored here run now. A session that wrote
    # no rule file keeps nothing, so it still costs an unlinked file rather than
    # an empty one left behind.
    state_fd, state = open_state(state_path)
    try:
        rules_written = state.get("rules_written") or []
        if rules_written:
            save_state(state_fd, empty_state(rules_written))
            return
    finally:
        close_state(state_fd)
    try:
        os.unlink(state_path)
    except FileNotFoundError:
        pass


def cli(argv=None):
    """Dispatch by flag, and never exit non-zero: a hook that fails must leave
    the tool call alone."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if "--reset-session" in argv:
            reset_session()
        elif "--session-notice" in argv:
            session_notice()
        elif "--verify" in argv:
            verify_turn()
        else:
            main()
    except Exception as exc:  # never break the tool call because of this hook
        warn(f"unexpected error: {exc}")
    return 0
