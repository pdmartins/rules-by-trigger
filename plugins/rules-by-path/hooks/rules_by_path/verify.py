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
all when everything passed: the user is told about a passing check through
`systemMessage`, and Claude's context does not pay for good news (spec Q15).

Running a command is `verifyrun.py`'s business, and the sentences are the
translation table's; this module owns the order, the deduplication, the clock
they all share, and the report."""

import collections
import json
import os
import sys
import time

from .constants import (VERIFY_COMMAND_TIMEOUT_SECONDS,
                        VERIFY_TOTAL_BUDGET_SECONDS, warn)
from .context import neutralize
from .discovery import find_scopes
from .frontmatter import verify_of
from .matching import collect_candidates
from .messages import (VERIFY_ERROR_KEY, VERIFY_EXIT_CODE_KEY,
                       VERIFY_FAILURE_KEY, VERIFY_NO_OUTPUT_KEY,
                       VERIFY_OUT_OF_TIME_KEY, VERIFY_PASSED_KEY,
                       VERIFY_REPORT_HEADER_KEY, VERIFY_SYSTEM_MESSAGE_KEY,
                       VERIFY_TIMED_OUT_KEY)
from .state import close_state, open_state, save_state, state_file_for
from .stats import record_verifications
from .verifyrun import (STATUS_ERROR, STATUS_FAILED, STATUS_OUT_OF_TIME,
                        STATUS_PASSED, STATUS_TIMED_OUT, out_of_time,
                        run_command)
from .written import take_written

# One command to run, and who asked for it:
#   command    the shell command line, verbatim from the rule
#   cwd        where it runs (spec Q6)
#   name       the rule the report names — the first one that asked
#   rules      [(scope_dir, name)] every rule this one run answers for, so a
#              command two rules share is counted for both (spec Q16)
VerifyJob = collections.namedtuple("VerifyJob", "command cwd name rules")

# Which sentence describes a status. PASSED is absent on purpose: a passing
# command has a summary line and no status line of its own.
STATUS_MESSAGE_KEYS = {
    STATUS_FAILED: VERIFY_EXIT_CODE_KEY,
    STATUS_TIMED_OUT: VERIFY_TIMED_OUT_KEY,
    STATUS_OUT_OF_TIME: VERIFY_OUT_OF_TIME_KEY,
    STATUS_ERROR: VERIFY_ERROR_KEY,
}


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


def job_cwd(base_dir, scopes, session_cwd):
    """Where one rule's commands run (spec Q6).

    A project rule runs at the root of the project that owns it: that is where
    its `pytest.ini`, its `Makefile` and its own relative paths make sense. A
    global rule has no root of its own, so it borrows the one of the file that
    triggered it — the innermost project scope found for that path — and falls
    back to the session's cwd when the file belongs to no project at all. The
    same global rule therefore runs once per repository it touched, which is
    the point: `pytest` means a different suite in each."""
    if base_dir is not None:
        return base_dir
    innermost_project = scopes[-1][0] if scopes else None
    return innermost_project or session_cwd


def collect_jobs(written, session_cwd, deadline=None):
    """The commands this turn owes, in the order they run and deduplicated.

    One job per distinct (command, cwd) pair: a global rule that covers two
    repositories runs once in each, and two rules of the same project asking
    for the same command run it once between them (spec §3). The rules that
    shared a job are all remembered on it — the command ran on behalf of every
    one of them, and the usage stats say so.

    A path whose tree holds no rules directory at all costs one `find_scopes`
    and nothing else.

    `deadline` is a `time.monotonic()` value and it is the turn's, not this
    function's: selecting is walking scopes and matching globs once per written
    path, and a turn that wrote hundreds of files into a repository full of
    expensive globs could spend the whole budget here and be killed before one
    command ran — which reports nothing at all. Past it the remaining paths are
    left unverified, out loud."""
    entries = []
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
            scopes = find_scopes(os.path.dirname(abs_path))
            if not scopes:
                continue
            candidates, _legacy = collect_candidates(abs_path, real_abs, scopes)
        except Exception as exc:
            warn(f"skipping {abs_path} while collecting verifications: {exc}")
            continue
        base_dirs = {scope_dir: base_dir for base_dir, scope_dir, _label in scopes}
        for scope_dir, _label, name, _glob, fields in candidates:
            base_dir = base_dirs.get(scope_dir)
            cwd = job_cwd(base_dir, scopes, session_cwd)
            for command in verify_of(fields):
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
    return jobs


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
    started at all — both are reported as "the turn's time ran out", never as
    the command's own timeout, because the command's own allowance is not what
    they hit.

    `budget` and `command_timeout` are parameters with the constants as
    defaults, so a test can prove the cutoff without waiting nine minutes."""
    results = []
    deadline = time.monotonic() + budget
    for job in jobs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append((job, out_of_time()))
            continue
        allowance = min(command_timeout, remaining)
        result = run_command(job.command, job.cwd, allowance)
        if result.status == STATUS_TIMED_OUT and allowance < command_timeout:
            result = result._replace(status=STATUS_OUT_OF_TIME)
        results.append((job, result))
    return results


def status_line(result, messages):
    """The one line that says what became of a failed command."""
    key = STATUS_MESSAGE_KEYS.get(result.status, VERIFY_ERROR_KEY)
    return messages[key].format(code=result.exit_code,
                                seconds=round(result.seconds or 0))


def build_report(results, messages):
    """The reason a failed turn holds on, or None when nothing failed.

    Per failure: the rule that asked for the command, the command, what became
    of it, and the tail of what it printed (spec Q12) — the rule's own body is
    NOT here, because it was injected when the file was touched and paying for
    it twice buys nothing. The passing commands are one line each at the end,
    so the model reads what ran as well as what broke.

    Everything that came from a rule file or from a command's output is
    neutralized on the way in: the report is text the model reads with the
    harness's own authority, and command output is the least trusted string in
    this plugin — it is whatever the repository's test suite chose to print."""
    failures = [(job, result) for job, result in results
                if result.status != STATUS_PASSED]
    if not failures:
        return None
    blocks = [messages[VERIFY_REPORT_HEADER_KEY].format(failed=len(failures),
                                                        total=len(results))]
    for job, result in failures:
        blocks.append(messages[VERIFY_FAILURE_KEY].format(
            name=neutralize(job.name), command=neutralize(job.command),
            status=status_line(result, messages),
            output=neutralize(result.output) or messages[VERIFY_NO_OUTPUT_KEY]))
    passed = [messages[VERIFY_PASSED_KEY].format(command=neutralize(job.command),
                                                 name=neutralize(job.name))
              for job, result in results if result.status == STATUS_PASSED]
    if passed:
        blocks.append("\n".join(passed))
    return "\n\n".join(blocks)


def build_system_message(results, messages):
    """One line per command, for the USER and not for Claude (spec Q15).

    A verification that passed is not news the model has to spend context on;
    it is the user who asked for the check and wants to see it ran."""
    return "\n".join(
        messages[VERIFY_SYSTEM_MESSAGE_KEY].format(
            command=neutralize(job.command), name=neutralize(job.name))
        for job, _result in results)


def take_turn_writes(session_id):
    """The paths written since the last verification, taken and cleared.

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
        return []
    state_fd, state = open_state(state_path)
    try:
        written = take_written(state)
        if written:
            save_state(state_fd, state)
        return written
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
    written = take_turn_writes(session_id)
    if not written:
        return
    # One clock for the whole hook: choosing what to verify and running it
    # answer to the same budget, because the harness's timeout does.
    deadline = time.monotonic() + VERIFY_TOTAL_BUDGET_SECONDS
    jobs = collect_jobs(written, payload.get("cwd") or os.getcwd(), deadline)
    if not jobs:
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
    record_verifications(session_id,
                         [(scope_dir, name, result.status == STATUS_PASSED)
                          for job, result in results
                          for scope_dir, name in job.rules])
    report = build_report(results, messages)
    if report is not None:
        print(json.dumps({"decision": "block", "reason": report}))
    else:
        print(json.dumps({"systemMessage":
                          build_system_message(results, messages)}))
    sys.stdout.flush()
