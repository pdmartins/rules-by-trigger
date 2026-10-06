"""What a turn's verifications come back as: the reason Claude reads when the
turn is held open, and the coloured block the user reads, held open or not.

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

from .constants import MAX_TOTAL_CHARS, NOTICE_MARKER, warn
from .context import neutralize
from .messages import (NOTICE_COLOUR, NOTICE_GLOBAL_KEY, VERIFY_DEFERRED_ICON,
                       VERIFY_ERROR_KEY, VERIFY_EXIT_CODE_KEY,
                       VERIFY_FAILED_COLOUR, VERIFY_FAILED_ICON,
                       VERIFY_FAILURE_KEY, VERIFY_ITEM_STATUS_SEPARATOR,
                       VERIFY_NO_OUTPUT_KEY, VERIFY_NOT_RUN_HEADER_KEY,
                       VERIFY_NOT_RUN_ICON, VERIFY_NOT_RUN_KEY,
                       VERIFY_NOT_STARTED_KEY, VERIFY_OUT_OF_TIME_KEY,
                       VERIFY_PASSED_COLOUR, VERIFY_PASSED_ICON,
                       VERIFY_PASSED_KEY, VERIFY_REPORT_CUT_KEY,
                       VERIFY_REPORT_HEADER_KEY, VERIFY_TIMED_OUT_KEY,
                       VERIFY_USER_DEFERRED_KEY, VERIFY_USER_FAILED_KEY,
                       VERIFY_USER_NONE_KEY, VERIFY_USER_PASSED_KEY)
from .notice import NOTICE_ITEM_INDENT, coloured_block
from .verifyrun import (DID_NOT_RUN_STATUSES, STATUS_ERROR, STATUS_FAILED,
                        STATUS_NOT_STARTED, STATUS_OUT_OF_TIME, STATUS_PASSED,
                        STATUS_TIMED_OUT)

# How bad a command's outcome is for its rule; the worst one is the rule's.
RANK_PASSED, RANK_NOT_RUN, RANK_FAILED = range(3)

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


def verify_item(icon, name, messages, status=None, is_global=False):
    """One rule's line in the user's block, before colouring: indent, icon, the
    tag that marks a rule from the global scope (same as the injection notice),
    the rule's name and, for anything that did not pass, what became of it.
    The name is neutralized like everything else that came from a rule file."""
    tag = f"{messages[NOTICE_GLOBAL_KEY]} " if is_global else ""
    line = f"{NOTICE_ITEM_INDENT}{icon} {tag}{neutralize(name)}"
    if status:
        line += f"{VERIFY_ITEM_STATUS_SEPARATOR}{status}"
    return line


def outcome_rank(result):
    """How bad one command's outcome is for the rule that asked for it: a
    command that RAN and did not pass outranks one that never ran, which
    outranks one that passed (the same three-way split as `split_results`)."""
    if result.status == STATUS_PASSED:
        return RANK_PASSED
    if result.status in DID_NOT_RUN_STATUSES:
        return RANK_NOT_RUN
    return RANK_FAILED


def merge_by_rule(results):
    """{(scope_dir, name): (rank, result)} — one entry per RULE, in the order
    the rules first appear in `results`.

    A rule can own several commands (two `verify:` entries) and a command can
    run more than once for it (a global rule in two directories), and the user
    reads one line per rule. The entry kept is the worst outcome among the
    rule's commands (`outcome_rank`), and among equals the first in results
    order, so a failing rule shows the status text of its first failure. A job
    built without rules (only tests do) is its own rule, identified by its
    `name`."""
    merged = {}
    for job, result in results:
        rank = outcome_rank(result)
        for identity in job.rules or [(None, job.name)]:
            if identity not in merged or rank > merged[identity][0]:
                merged[identity] = (rank, result)
    return merged


def build_system_message(results, messages, deferred=(), global_scope_dir=None):
    """The coloured block for the USER and not for Claude (spec Q15), or None
    when there is nothing to report — no result and no deferred rule.

    It has the layout of the injection notice (see `notice.coloured_block`): a
    header, then one indented line per rule, in this order: failed, passed, not
    run, deferred. It lists everything, passed ones included, whether or not the
    turn is held open: the model's reason (`build_report`) carries the failures'
    output, and this is the user's own summary of the turn, so what is in the
    one is not kept out of the other. The command is not shown, only the rule.

    Green when everything that ran passed, red when anything failed, and the
    notice's blue when nothing ran. Each rule has ONE line and is counted once
    (`merge_by_rule`): its worst outcome among its commands, failed above not
    run above passed. `ran` is the rules that failed or passed, so a rule whose
    commands never ran is in neither count and shows as not run. (The model's
    report counts commands.) Failed and not-run lines carry what became of the
    command (`status_line`); a passed one needs no status. A rule whose `verify:` this session's own
    writes put into its file is deferred to the next session (see
    `record_rules_written`): the only sign the user has that a rule they just
    wrote is not yet running.

    `global_scope_dir` is the machine owner's rules directory, as the injection
    knows it (see `discovery.global_scope`): a rule from it is tagged
    `[global]`. Deferred rules arrive as bare names, so they carry no tag."""
    if not results and not deferred:
        return None
    outcomes = merge_by_rule(results)

    def items_of(rank, icon, with_status):
        return [verify_item(
            icon, name, messages,
            status_line(result, messages) if with_status else None,
            global_scope_dir is not None and scope_dir == global_scope_dir)
            for (scope_dir, name), (item_rank, result) in outcomes.items()
            if item_rank == rank]

    failed_items = items_of(RANK_FAILED, VERIFY_FAILED_ICON, True)
    passed_items = items_of(RANK_PASSED, VERIFY_PASSED_ICON, False)
    ran = len(failed_items) + len(passed_items)
    items = (failed_items + passed_items
             + items_of(RANK_NOT_RUN, VERIFY_NOT_RUN_ICON, True)
             + [verify_item(VERIFY_DEFERRED_ICON, name, messages,
                            messages[VERIFY_USER_DEFERRED_KEY])
                for name in deferred])
    if failed_items:
        colour = VERIFY_FAILED_COLOUR
        summary = messages[VERIFY_USER_FAILED_KEY].format(
            failed=len(failed_items), ran=ran)
    elif passed_items:
        colour = VERIFY_PASSED_COLOUR
        summary = messages[VERIFY_USER_PASSED_KEY].format(
            passed=len(passed_items), ran=ran)
    else:
        colour = NOTICE_COLOUR
        summary = messages[VERIFY_USER_NONE_KEY]
    return coloured_block([f"{NOTICE_MARKER} {summary}"] + items, colour)
