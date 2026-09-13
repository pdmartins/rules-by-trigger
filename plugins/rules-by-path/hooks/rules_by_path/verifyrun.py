"""Running ONE verification command: the subprocess, its clock, and the tail
of what it printed.

Split from `verify.py`, which decides which commands run and turns the answers
into the report Claude reads. The two halves are tested apart because they fail
apart: selection can be wrong with no process involved, and a process can hang
with the selection perfect.

Nothing here decides anything about a turn. It answers one question — what
happened to this command line, in this directory — and leaves the sentence
that describes it to the caller, which is the only side that knows the
reader's language."""

import collections
import os
import signal
import subprocess

from .constants import (VERIFY_COMMAND_TIMEOUT_SECONDS,
                        VERIFY_KILL_DRAIN_SECONDS, VERIFY_OUTPUT_CUT_MARKER,
                        VERIFY_OUTPUT_TAIL_LINES, VERIFY_OUTPUT_TAIL_MAX_CHARS,
                        warn)

# What became of one command. Named rather than spelled out at each site: the
# report picks one sentence per status, and a misspelled bare string would
# silently print nothing instead of failing.
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"          # ran to the end, exit code not zero
STATUS_TIMED_OUT = "timed out"    # killed by its own per-command allowance
STATUS_OUT_OF_TIME = "out of time"  # started, killed by the TURN's budget
STATUS_NOT_STARTED = "not started"  # the TURN's budget was already spent
STATUS_ERROR = "error"            # never started: no such directory, no shell

# The two statuses that mean the command NEVER RAN, as opposed to ran and did
# not pass. The distinction decides three things at once (see `verify.py`): a
# command nobody let start cannot be evidence that anything is wrong, so it
# never blocks the turn on its own, it is not counted as a verification of the
# rule that asked for it, and it is reported as incomplete rather than as a
# failure. A command killed MID-RUN by the turn's budget is not here: it ran.
DID_NOT_RUN_STATUSES = (STATUS_NOT_STARTED, STATUS_ERROR)

# The outcome of a command, whatever became of it:
#   status     one of the constants above
#   exit_code  the process's own code, or None when there never was one
#   seconds    the allowance that applied, for the timeout wording
#   output     stdout and stderr interleaved, already tailed
CommandResult = collections.namedtuple("CommandResult",
                                       "status exit_code seconds output")

# POSIX only. `start_new_session` puts the command in a process group of its
# own, which is what makes killing its whole tree possible below; on Windows
# there is no such call, and passing it would be a lie in the signature.
NEW_SESSION_KWARGS = {"start_new_session": True} if os.name != "nt" else {}


def not_started():
    """The result of a command the turn's budget never let start.

    Named for what happened rather than for the clock that caused it: this is
    the only outcome in which no process ever existed, and the whole of
    `DID_NOT_RUN_STATUSES` hangs on that difference."""
    return CommandResult(STATUS_NOT_STARTED, None, 0, "")


def tail(output, lines=VERIFY_OUTPUT_TAIL_LINES,
         max_chars=VERIFY_OUTPUT_TAIL_MAX_CHARS):
    """The last `lines` lines of what a command printed, and at most
    `max_chars` of them.

    The END of the output, not the beginning: a test runner puts its summary
    and the failing assertion last, and a build log's first lines are the same
    banner every time. Trailing blank lines go, because they would be spent as
    context for nothing.

    Lines alone are not a bound on size. A minified bundle, a base64 payload or
    a progress bar that rewrites itself with `\r` is ONE line of any length, so
    the line tail would hand the model the entire thing — which is why the byte
    ceiling is applied after it, keeping the end for the same reason, with a
    marker saying the beginning is gone."""
    text = "\n".join(output.splitlines()[-lines:]).strip()
    if len(text) <= max_chars:
        return text
    return VERIFY_OUTPUT_CUT_MARKER + text[len(text) - max_chars:]


def stop_process_tree(process):
    """Kill the command and everything it started.

    `shell=True` means the process that was started is a shell, so killing only
    it leaves the test runner it spawned alive — still holding the pipe this
    hook is reading. The hook would then wait for a command it believes it has
    already killed, past the Stop hook's own timeout, and the turn would end
    with no report at all. Signalling the process group is what avoids that;
    where there is no group to signal, killing the shell is all there is."""
    try:
        if NEW_SESSION_KWARGS and hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
    except Exception as exc:
        warn(f"could not kill the verification command's process group: {exc}")
    try:
        process.kill()
    except Exception as exc:
        warn(f"could not kill the verification command: {exc}")


def run_command(command, cwd, timeout=VERIFY_COMMAND_TIMEOUT_SECONDS):
    """Run one verification command in `cwd` and report what became of it.

    Through the system shell, because a shell command line is what a rule
    writes in `verify:` — `pytest -q && ruff check .` is one verification, not
    two, and the spec (§3) says so. What that trusts is stated in the ADR: a
    project's `verify:` runs, because a repository's own `.claude/settings.json`
    hooks already do.

    stdout and stderr arrive interleaved in one stream, in the order a human
    reading the terminal would have seen them; the bytes are decoded with
    `errors="replace"`, since a command that prints half a UTF-8 sequence must
    cost a mojibake character and not the whole report.

    stdin is /dev/null so that a command which stops to ask a question fails at
    once instead of spending the turn's entire budget waiting for an answer
    nobody is there to type. Without it the child would inherit this hook's own
    stdin — the pipe the Stop payload arrived through.

    An exception's own text goes through `tail` like a command's output does:
    it is a string this process did not write either, and a `PermissionError`
    naming a path of any length must not become the whole report.

    The one bound not enforced here is on how MUCH a command may print: the
    output is read into memory whole and only then tailed. The per-command
    timeout is what bounds it in practice, and the command is one the user's
    own rule asked for."""
    try:
        process = subprocess.Popen(
            command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            **NEW_SESSION_KWARGS)
    except Exception as exc:
        # A scope whose directory has been deleted since the write, a shell
        # that is not there: the verification fails, the hook does not.
        warn(f"verification command could not be started ({command[:64]!r}): {exc}")
        return CommandResult(STATUS_ERROR, None, timeout, tail(str(exc)))
    try:
        raw, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process_tree(process)
        raw = drain(process)
        return CommandResult(STATUS_TIMED_OUT, None, timeout, decode(raw))
    except Exception as exc:
        stop_process_tree(process)
        warn(f"verification command failed to run ({command[:64]!r}): {exc}")
        return CommandResult(STATUS_ERROR, None, timeout, tail(str(exc)))
    status = STATUS_PASSED if process.returncode == 0 else STATUS_FAILED
    return CommandResult(status, process.returncode, timeout, decode(raw))


def drain(process):
    """What a killed command had printed before it was killed.

    Bounded by its own small clock: the kill went to the process group, so this
    returns immediately in the normal case, and the ceiling is there for the
    platform where the group could not be signalled and something still holds
    the pipe. Losing the output of a command that would not die is a worse
    report; losing the turn is worse than that."""
    try:
        raw, _ = process.communicate(timeout=VERIFY_KILL_DRAIN_SECONDS)
        return raw
    except Exception as exc:
        warn(f"killed verification command left its output unreadable: {exc}")
        return b""


def decode(raw):
    """The tail of a command's output as text, however it was encoded."""
    if not raw:
        return ""
    return tail(raw.decode("utf-8", "replace"))
