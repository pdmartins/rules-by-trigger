"""The `Stop` hook: which verifications this turn owes, and what comes back.

`Stop` is the only hook that closes a turn, and the only one told nothing about
files — so the writes it acts on come from the trail `PreToolUse` left in the
session state (see `written.py`), and the first thing this module does is take
that list and clear it. An empty list is the normal case and the fast path: a
turn that wrote nothing, or one whose verifications already ran, ends here
without reading a single rule. That is also what stops the correction loop
(spec Q10) — a re-run with no new write has nothing to check.

Selection is the injection's selection, minus one filter: `collect_candidates`
is called with no tool name, so a rule's `tool:` narrows what reaches the
model and never what runs, because a WRITE is the trigger either way (spec
Q14). `exclude:` still applies — it is the same matcher.

What the model gets back is `decision: block` with the failures, and nothing at
all when nothing that RAN failed: the user is told about a passing check — and
about a command that never ran at all — through `systemMessage`, and Claude's
context does not pay for good news (spec Q15).

Running a command is `verifyrun.py`'s business and writing the report is
`verifyreport.py`'s; this module owns the selection, the order, the
deduplication and the clock they all share.

One rule never runs, however it matches: one whose file THIS session wrote (see
`record_rules_written`). Claude Code protects its own hooks the same way, by
snapshotting `settings.json` at startup, and for the same reason — a model that
can write a hook and have it run in the same turn has written itself a shell."""

import collections
import json
import os
import sys
import time

from .constants import (VERIFY_COMMAND_TIMEOUT_SECONDS,
                        VERIFY_TOTAL_BUDGET_SECONDS, warn)
from .discovery import find_scopes, project_root_of
from .frontmatter import verify_of
from .matching import collect_candidates
from .state import close_state, open_state, save_state, state_file_for
from .stats import record_verifications
from .verifyreport import build_report, build_system_message, split_results
from .verifyrun import (DID_NOT_RUN_STATUSES, STATUS_OUT_OF_TIME, STATUS_PASSED,
                        STATUS_TIMED_OUT, not_started, run_command)
from .written import take_written

# One command to run, and who asked for it:
#   command    the shell command line, verbatim from the rule
#   cwd        where it runs (spec Q6)
#   name       the rule the report names — the first one that asked
#   rules      [(scope_dir, name)] every rule this one run answers for, so a
#              command two rules share is counted for both (spec Q16)
VerifyJob = collections.namedtuple("VerifyJob", "command cwd name rules")


def scope_order(base_dir):
    """The sort key that puts the global scope first and then project scopes
    from the outermost to the innermost (spec Q5).

    `find_scopes` already answers in that order for ONE file; a turn writes
    several, in several scopes, and the union has to be ordered again. Depth is
    what "outermost" means, and the sort is stable, so rules of the same scope
    keep the order `collect_candidates` yielded them in."""
    if base_dir is None:
        return (0, 0)  # the machine owner's scope, before anything a repo ships
    return (1, len([segment for segment in base_dir.split("/") if segment]))


def job_cwd(base_dir, project_root, session_cwd):
    """Where one rule's commands run (spec Q6).

    A project rule runs at the root of the project that owns it: that is where
    its `pytest.ini`, its `Makefile` and its own relative paths make sense. A
    global rule has no root of its own, so it borrows the root of the project
    the written file belongs to — the innermost directory above it holding a
    `.claude` (see `project_root_of`) — and falls back to the session's cwd
    when the file belongs to no project at all. The same global rule therefore
    runs once per repository it touched, which is the point: `pytest` means a
    different suite in each.

    What it borrows is deliberately NOT the innermost RULES scope: a repository
    that ships no `.claude/rules-by-path/` of its own is still a project, and
    running the user's global `pytest` at the session's cwd instead of at that
    repository's root is how a global rule ends up testing the wrong tree."""
    if base_dir is not None:
        return base_dir
    return project_root or session_cwd


def rule_file_written(scope_dir, name, session_rules):
    """True when this rule's own file is one this session wrote.

    The rule file is `scope_dir/name` — the same path `read_rule_file` opens.
    Both spellings are compared, the literal one and the resolved one, because
    the write recorded both and the scope walk may have reached the directory
    through either (a symlinked `.claude`, a monorepo alias)."""
    path = os.path.join(scope_dir, name).replace(os.sep, "/")
    if path in session_rules:
        return True
    return os.path.realpath(path).replace(os.sep, "/") in session_rules


def collect_jobs(written, session_cwd, deadline=None, rules_written=()):
    """(jobs, deferred rule names) — the commands this turn owes, in the order
    they run and deduplicated, and the rules whose commands are deliberately
    not among them.

    One job per distinct (command, cwd) pair: a global rule that covers two
    repositories runs once in each, and two rules of the same project asking
    for the same command run it once between them (spec §3). The rules that
    shared a job are all remembered on it — the command ran on behalf of every
    one of them, and the usage stats say so.

    `rules_written` is the session's own rule-file writes, and a rule whose
    file is in it contributes no job at all: its `verify:` may be one the model
    wrote itself minutes ago, and a hook the model can add and have run in the
    same turn is a shell it granted itself. It is deferred, not dropped — the
    next session reads the file with no such history and runs it, which is
    exactly what Claude Code's own `settings.json` snapshot does.

    A path whose tree holds no rules directory at all costs one `find_scopes`
    and nothing else. The scopes, the project root and each scope's index are
    memoised for the duration of this call: a turn that wrote forty files in
    one folder used to walk the ancestors and re-read every frontmatter of
    every scope forty times over, all to reach the same answer.

    `deadline` is a `time.monotonic()` value and it is the turn's, not this
    function's: selecting is walking scopes and matching globs once per written
    path, and a turn that wrote hundreds of files into a repository full of
    expensive globs could spend the whole budget here and be killed before one
    command ran — which reports nothing at all. Past it the remaining paths are
    left unverified, out loud."""
    entries = []
    deferred = []
    session_rules = set(rules_written)
    directory_cache = {}
    index_cache = {}
    for abs_path in written:
        if deadline is not None and time.monotonic() > deadline:
            warn(f"the turn's verification budget ran out while choosing what "
                 f"to verify; the writes after {abs_path!r} were not checked")
            break
        try:
            # The literal path is what the tool named and what the state kept;
            # the resolved one is computed here, exactly as `main()` does, so a
            # rule written against either spelling matches (see `path_targets`).
            real_abs = os.path.realpath(abs_path).replace(os.sep, "/")
            directory = os.path.dirname(abs_path)
            found = directory_cache.get(directory)
            if found is None:
                found = (find_scopes(directory), project_root_of(directory))
                directory_cache[directory] = found
            scopes, project_root = found
            if not scopes:
                continue
            candidates, _legacy = collect_candidates(abs_path, real_abs, scopes,
                                                     index_cache=index_cache)
        except Exception as exc:
            warn(f"skipping {abs_path} while collecting verifications: {exc}")
            continue
        base_dirs = {scope_dir: base_dir for base_dir, scope_dir, _label in scopes}
        for scope_dir, _label, name, _glob, fields in candidates:
            commands = verify_of(fields)
            if not commands:
                continue
            if session_rules and rule_file_written(scope_dir, name, session_rules):
                if (scope_dir, name) not in deferred:
                    deferred.append((scope_dir, name))
                    warn(f"the verify: in rule {name!r} ({scope_dir}) was "
                         f"written by this session, so it runs from the next "
                         f"one — as a hook added to settings.json does")
                continue
            base_dir = base_dirs.get(scope_dir)
            cwd = job_cwd(base_dir, project_root, session_cwd)
            for command in commands:
                entries.append((scope_order(base_dir),
                                (command, cwd, scope_dir, name)))
    entries.sort(key=lambda entry: entry[0])
    jobs = []
    by_pair = {}
    for _order, (command, cwd, scope_dir, name) in entries:
        job = by_pair.get((command, cwd))
        if job is None:
            job = VerifyJob(command, cwd, name, [])
            by_pair[(command, cwd)] = job
            jobs.append(job)
        if (scope_dir, name) not in job.rules:
            job.rules.append((scope_dir, name))
    return jobs, [name for _scope_dir, name in deferred]


def run_jobs(jobs, budget=VERIFY_TOTAL_BUDGET_SECONDS,
             command_timeout=VERIFY_COMMAND_TIMEOUT_SECONDS):
    """[(job, result)] — every job, in order, run under one shared clock.

    Every job is reported even after a failure: a turn that wrote in three
    folders learns about all three at once instead of one per turn (spec Q5).

    The budget is the turn's, not the command's, and it exists so the hook
    outlives its own commands: Claude Code kills a `Stop` hook that overruns
    and DISCARDS its output, which would end the turn silently with a failing
    verification nobody reported. So a command that starts near the end of the
    budget gets only what is left of it, and one that finds nothing left is not
    started at all. Neither is reported as the command's own timeout, because
    the command's own allowance is not what they hit — and the two are not
    reported as each other either: the one that was killed mid-run RAN and can
    block the turn, while the one that never started is not evidence about
    anything (see `split_results`).

    `budget` and `command_timeout` are parameters with the constants as
    defaults, so a test can prove the cutoff without waiting nine minutes."""
    results = []
    deadline = time.monotonic() + budget
    for job in jobs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append((job, not_started()))
            continue
        allowance = min(command_timeout, remaining)
        result = run_command(job.command, job.cwd, allowance)
        if result.status == STATUS_TIMED_OUT and allowance < command_timeout:
            result = result._replace(status=STATUS_OUT_OF_TIME)
        results.append((job, result))
    return results


def take_turn_writes(session_id):
    """(paths written since the last verification, rule files this session
    wrote) — the first list taken and cleared, the second only read.

    The two have different lifetimes on purpose. The writes are the turn's, and
    a verification that consumed them must not be handed them again. The rule
    files are the session's: they are what says a `verify:` was authored here
    and waits for the next session, so nothing clears them until the session
    itself is over (see `record_rules_written`).

    The lock is held for exactly this: the list is read, emptied and saved, and
    the state file is closed BEFORE the first command runs. A verification that
    takes two minutes must not be two minutes in which no tool call of this
    session can record anything.

    A session with no state file wrote nothing that could be recorded, so the
    file is looked for and never created — this runs at the end of every turn
    of every session, including the ones that have no rules anywhere near
    them, and leaving a directory behind for a user who has none is not what a
    hook that found nothing to do should cost."""
    state_path = state_file_for(session_id, create=False)
    if state_path is None or not os.path.isfile(state_path):
        return [], []
    state_fd, state = open_state(state_path)
    try:
        written = take_written(state)
        if written:
            save_state(state_fd, state)
        return written, state.get("rules_written") or []
    finally:
        close_state(state_fd)


def verify_turn():
    """Stop: run the verifications the rules declare for what this turn wrote.

    Never raises and never blocks a turn over a failure of its own: a payload
    it cannot read, a rule directory that vanished, a subprocess that would not
    start — each of those costs the verification and nothing more. The only
    thing that holds a turn open is a verification that actually failed."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Like `reset_session`: a payload nobody can read leaves nothing to
        # verify, and that is not worth a line on stderr every turn.
        return
    if not isinstance(payload, dict):
        return
    session_id = payload.get("session_id")
    written, rules_written = take_turn_writes(session_id)
    if not written:
        return
    # One clock for the whole hook: choosing what to verify and running it
    # answer to the same budget, because the harness's timeout does.
    deadline = time.monotonic() + VERIFY_TOTAL_BUDGET_SECONDS
    jobs, deferred = collect_jobs(written, payload.get("cwd") or os.getcwd(),
                                  deadline, rules_written)
    if not jobs and not deferred:
        return
    # Deferred: `main` imports this module for the `--verify` entry point, so
    # importing it back at module level would be a cycle. The language is the
    # one configured for the scopes of the first written path — a turn that
    # wrote in two projects reports in the language of the first.
    from .main import messages_for_scopes
    messages = messages_for_scopes(find_scopes(os.path.dirname(written[0])))
    results = run_jobs(jobs, budget=deadline - time.monotonic())
    # Recorded BEFORE the report is printed, unlike the injection's stats,
    # which are written after the payload is flushed. The reason is the same
    # one in reverse: there, bookkeeping must never delay a tool call. Here the
    # printed JSON asks the harness to act on the turn, and a process that has
    # just said "block" cannot assume it will be left running afterwards. A
    # locked write of one small file is what it costs; a command that ran and
    # was never counted is what it buys.
    #
    # Only the commands that RAN are counted. A command the budget never let
    # start, or one the environment refused to launch, says nothing about the
    # rule that asked for it: counting it would make `status` report a
    # verification that never happened, with a failure rate that is the
    # machine's and not the code's.
    record_verifications(session_id,
                         [(scope_dir, name, result.status == STATUS_PASSED)
                          for job, result in results
                          if result.status not in DID_NOT_RUN_STATUSES
                          for scope_dir, name in job.rules])
    report = build_report(results, messages)
    if report is None:
        output = {"systemMessage":
                  build_system_message(results, messages, deferred)}
    else:
        output = {"decision": "block", "reason": report}
        # A blocked turn still owes the user the lines that are theirs alone:
        # the one about a rule whose `verify:` was deferred, and one per
        # command that never ran — the budget was spent, or the environment
        # refused to launch it. Neither is shown to them any other way: stderr
        # is not shown to them, and both are appendices in the model's report,
        # not lines of their own (see `build_report`). A command that PASSED
        # stays out of `systemMessage` here on purpose: on a blocked turn it is
        # already one line inside the report the model reads, and repeating it
        # to the user would be the same news twice. `systemMessage` travels
        # beside the decision — the harness reads the common fields of every
        # hook output.
        _failures, did_not_run = split_results(results)
        deferred_lines = build_system_message(did_not_run, messages, deferred)
        if deferred_lines:
            output["systemMessage"] = deferred_lines
    print(json.dumps(output))
    sys.stdout.flush()
