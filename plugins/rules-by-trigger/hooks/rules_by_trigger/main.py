"""The four entry points Claude Code calls: the PreToolUse injection (`main`),
the SessionStart notice, the state reset on compact/clear, and the Stop
verification — which lives in `verify.py` and is only dispatched here.

`main()` has two branches past reading `tool_name`: the path branch, for the
five file tools, matches a touched file against every rule's `glob:`; the
call branch, `inject_for_call`, matches the tool call itself against every
rule's `call:` (see `matching.collect_call_candidates`). Both hand off to
the same delivery, `injection.deliver`, once their candidates are known."""

import json
import os
import sys

from .constants import CALL_TRIGGER_TOOLS, DEFAULT_LANGUAGE, WRITE_TOOL_NAMES, warn
from .config import language
from .context import build_block_reason
from .messages import SESSION_NOTICE_KEY, messages_for
from .discovery import find_scopes, global_scope
from .frontmatter import block_of
from .injection import config_for_scopes, deliver
from .matching import (collect_call_candidates, collect_candidates,
                       extract_file_path, is_inside_rules_dir)
from .rules import read_rule_file
from .state import close_state, empty_state, open_state, save_state, state_file_for
from .verify import verify_turn
from .written import record_rules_written


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


def inject_for_call(payload, tool_name):
    """The call branch of `main()`: a call-trigger tool (today, only `Skill`)
    reaches the same delivery a matching file touch does, through a door with
    no path in it at all — no `is_inside_rules_dir`, no `block:` (see
    `blocking_rule`'s WRITE_TOOL_NAMES gate, which a call-trigger tool never
    passes), no `record_written`.

    Scopes are anchored on the session's cwd, exactly like `session_notice`:
    a tool call names no file, so there is no touched directory to walk up
    from. When there is nothing to deliver — no scope at all, or scopes but
    no matching candidate and no legacy scope — this returns before the state
    file is even opened, so a Skill call that matches nothing does not open,
    lock or advance the session state: `remember_again_after: N calls` on
    every rule in play measures its distance in calls, and a call that had
    nothing to say must not count as one."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    cwd = payload.get("cwd") or os.getcwd()
    scopes = find_scopes(os.path.abspath(cwd))
    if not scopes:
        return
    candidates, legacy_scopes = collect_call_candidates(tool_name, tool_input, scopes)
    if not candidates and not legacy_scopes:
        return
    deliver(payload, tool_name, scopes, candidates, legacy_scopes, None)


def main():
    payload = json.load(sys.stdin)
    # Read first, before anything else about this call is even looked at: a
    # call-trigger tool reaches an entirely different branch, one with no
    # path to resolve at all.
    tool_name = payload.get("tool_name")
    if tool_name in CALL_TRIGGER_TOOLS:
        inject_for_call(payload, tool_name)
        return
    raw_path = extract_file_path(payload)
    if not raw_path:
        return
    cwd = payload.get("cwd") or os.getcwd()
    abs_path = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
    abs_path = os.path.normpath(abs_path).replace(os.sep, "/")
    # Resolved once for the whole call: resolving a path walks every component
    # of it, and both checks below need the same answer.
    real_abs = os.path.realpath(abs_path).replace(os.sep, "/")
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

    deliver(payload, tool_name, scopes, candidates, legacy_scopes, abs_path)


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
