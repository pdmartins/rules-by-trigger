"""What a turn's verifications come back as: the reason Claude reads when the
turn is held open, and the line the user reads when it is not.

Split from `verify.py`, which owns which commands run, in what order and under
which clock. The two halves are separate because they answer to different
readers: everything here is text someone reads, in the reader's own language,
assembled from the translation table and from strings this plugin does not
control — a rule's name, a command line, whatever a repository's test suite
chose to print. Nothing here decides anything about a turn.

Neutralization is the one invariant that must not move: every value that came
from a rule file or from a command's output passes through `neutralize` on the
way in, because the report is text the model reads with the harness's own
authority."""

from .constants import MAX_TOTAL_CHARS, warn
from .context import neutralize
from .messages import (VERIFY_ERROR_KEY, VERIFY_EXIT_CODE_KEY,
                       VERIFY_FAILURE_KEY, VERIFY_NO_OUTPUT_KEY,
                       VERIFY_NOT_RUN_HEADER_KEY, VERIFY_NOT_RUN_KEY,
                       VERIFY_NOT_STARTED_KEY, VERIFY_OUT_OF_TIME_KEY,
                       VERIFY_PASSED_KEY, VERIFY_REPORT_CUT_KEY,
                       VERIFY_REPORT_HEADER_KEY, VERIFY_SYSTEM_MESSAGE_KEY,
                       VERIFY_SYSTEM_NOT_RUN_KEY,
                       VERIFY_SYSTEM_RULE_WRITTEN_KEY, VERIFY_TIMED_OUT_KEY)
from .verifyrun import (DID_NOT_RUN_STATUSES, STATUS_ERROR, STATUS_FAILED,
                        STATUS_NOT_STARTED, STATUS_OUT_OF_TIME, STATUS_PASSED,
                        STATUS_TIMED_OUT)

# Which sentence describes a status. PASSED is absent on purpose: a passing
# command has a summary line and no status line of its own.
STATUS_MESSAGE_KEYS = {
    STATUS_FAILED: VERIFY_EXIT_CODE_KEY,
    STATUS_TIMED_OUT: VERIFY_TIMED_OUT_KEY,
    STATUS_OUT_OF_TIME: VERIFY_OUT_OF_TIME_KEY,
    STATUS_NOT_STARTED: VERIFY_NOT_STARTED_KEY,
    STATUS_ERROR: VERIFY_ERROR_KEY,
}


def status_line(result, messages):
    """The one line that says what became of a command that did not pass."""
    key = STATUS_MESSAGE_KEYS.get(result.status, VERIFY_ERROR_KEY)
    return messages[key].format(code=result.exit_code,
                                seconds=round(result.seconds or 0))


def split_results(results):
    """(failures, did not run) — the two ways a command can fail to pass, which
    are not the same thing and must not be reported as if they were.

    A failure is a command that RAN and did not pass: an exit code, its own
    timeout, or the turn's budget killing it mid-run. "Did not run" is the
    command nobody ever started — the budget was already spent, or the
    subprocess could not be launched at all. Only the first is evidence about
    the code, so only the first holds a turn open and only the first is counted
    as a verification of the rule that asked for it."""
    failures, did_not_run = [], []
    for job, result in results:
        if result.status in DID_NOT_RUN_STATUSES:
            did_not_run.append((job, result))
        elif result.status != STATUS_PASSED:
            failures.append((job, result))
    return failures, did_not_run


def cut_to_budget(report, messages):
    """The report, never longer than the ceiling one injection answers to.

    The BEGINNING is what survives, unlike a command's own output: the failures
    come first and the summary lines last, so what a cut costs is the list of
    what passed, not the failure the turn is being held open for. A marker says
    so, and it is counted against the ceiling rather than added on top of it."""
    if len(report) <= MAX_TOTAL_CHARS:
        return report
    marker = messages[VERIFY_REPORT_CUT_KEY]
    warn(f"the verification report exceeded {MAX_TOTAL_CHARS} chars and was "
         f"cut; the failures come first, so what was dropped is the tail")
    return report[:MAX_TOTAL_CHARS - len(marker)] + marker


def build_report(results, messages):
    """The reason a failed turn holds on, or None when nothing that ran failed.

    Per failure: the rule that asked for the command, the command, what became
    of it, and the tail of what it printed (spec Q12) — the rule's own body is
    NOT here, because it was injected when the file was touched and paying for
    it twice buys nothing. The passing commands are one line each at the end,
    so the model reads what ran as well as what broke.

    The commands that never ran are a section of their own, and only ever an
    appendix to a report that was being printed anyway: they hold no turn open,
    because a command nobody started is not evidence that anything is wrong,
    and the model can do nothing about a budget that ran out. It is still told,
    so it does not read a partial verification as a complete one.

    Everything that came from a rule file or from a command's output is
    neutralized on the way in: the report is text the model reads with the
    harness's own authority, and command output is the least trusted string in
    this plugin — it is whatever the repository's test suite chose to print."""
    failures, did_not_run = split_results(results)
    if not failures:
        return None
    blocks = [messages[VERIFY_REPORT_HEADER_KEY].format(
        failed=len(failures), total=len(results) - len(did_not_run))]
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
    if did_not_run:
        blocks.append("\n".join([messages[VERIFY_NOT_RUN_HEADER_KEY]]
                                + [not_run_line(job, result, messages)
                                   for job, result in did_not_run]))
    return cut_to_budget("\n\n".join(blocks), messages)


def not_run_line(job, result, messages):
    """One line of the report's appendix: the command, the rule, the reason."""
    return messages[VERIFY_NOT_RUN_KEY].format(
        command=neutralize(job.command), name=neutralize(job.name),
        status=status_line(result, messages))


def build_system_message(results, messages, deferred=()):
    """The lines for the USER and not for Claude (spec Q15).

    Three things belong to the user alone. A verification that passed is not
    news the model has to spend context on; it is the user who asked for the
    check and wants to see it ran. A verification that never ran is not
    something the model can act on either, but the user can — it is their
    budget and their environment. And a `verify:` this session's own writes put
    into a rule file is deferred to the next session (see
    `record_rules_written`), which the user must be told about, since it is the
    only sign that a rule they just wrote is not yet running.

    A command that ran and failed gets no line here: it is in the report the
    model reads, and this message is printed when there is no report."""
    lines = []
    for job, result in results:
        if result.status in DID_NOT_RUN_STATUSES:
            lines.append(messages[VERIFY_SYSTEM_NOT_RUN_KEY].format(
                command=neutralize(job.command), name=neutralize(job.name),
                status=status_line(result, messages)))
        elif result.status == STATUS_PASSED:
            lines.append(messages[VERIFY_SYSTEM_MESSAGE_KEY].format(
                command=neutralize(job.command), name=neutralize(job.name)))
    lines.extend(messages[VERIFY_SYSTEM_RULE_WRITTEN_KEY].format(
        name=neutralize(name)) for name in deferred)
    return "\n".join(lines)
