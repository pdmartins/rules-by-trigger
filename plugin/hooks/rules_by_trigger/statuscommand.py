"""UserPromptExpansion: show the `status` table when the user types
`/rules-by-trigger:status`, with no answer from the model.

The hook runs the admin CLI and hands its stdout, unchanged, back as the reason
of a block. A block's reason goes to the user and never into the model's
context, which is also why nothing but that one JSON object may be printed on
stdout here: plain stdout from this event IS added to the model's context.

Whatever goes wrong — arguments it does not know, the CLI failing or taking too
long, any exception — the hook prints nothing on stdout, says one line on
stderr and exits 0. The command then expands as usual and its body, which runs
the same CLI, takes over."""

import json
import os
import shlex
import subprocess
import sys

from .constants import warn

# ---- user-visible text -------------------------------------------------------
FALLBACK_NOTICE = ("/rules-by-trigger:status not shown by the hook ({reason}); "
                   "the command runs as usual")
REASON_BAD_ARGUMENTS = "it takes nothing or --path <file>, got {arguments!r}"
REASON_CLI_FAILED = "the CLI exited {code}: {error}"
REASON_CLI_TIMEOUT = "the CLI took more than {seconds} s"
REASON_CLI_SILENT = "the CLI printed nothing"
# -----------------------------------------------------------------------------

COMMAND_NAME = "rules-by-trigger:status"
PATH_FLAG = "--path"
CLI_SUBCOMMAND = "status"
# Below the 10 s the hook is registered with, so a slow CLI makes the hook fall
# back cleanly instead of being killed by the harness.
CLI_TIMEOUT_SECONDS = 8
ERROR_EXCERPT_CHARS = 200
QUOTES = "\"'"
# Python writes a piped stdout in the console's code page on Windows, which
# cannot hold what the table prints.
CLI_ENCODING_ENV = {"PYTHONIOENCODING": "utf-8"}
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI_LAUNCHER = os.path.join(PLUGIN_ROOT, "bin",
                            "rules-by-trigger.cmd" if os.name == "nt"
                            else "rules-by-trigger")


class NotShown(Exception):
    """The table cannot be shown by the hook; the message says why."""


def unquoted(word):
    """`word` without one pair of matching quotes around it. The split keeps
    backslashes as they are, which a Windows path needs, and leaves the quotes."""
    if len(word) >= 2 and word[0] == word[-1] and word[0] in QUOTES:
        return word[1:-1]
    return word


def cli_arguments(command_args):
    """The arguments to give the CLI after `status`: none, or `--path <file>`.
    Anything else is not this hook's to interpret."""
    words = [unquoted(word) for word in shlex.split(command_args or "", posix=False)]
    if not words:
        return []
    if len(words) == 2 and words[0] == PATH_FLAG and words[1]:
        return words
    raise NotShown(REASON_BAD_ARGUMENTS.format(arguments=command_args))


def table_of(extra_arguments, launcher, timeout):
    """The CLI's stdout for `status`, exactly as it printed it."""
    try:
        done = subprocess.run([launcher, CLI_SUBCOMMAND, *extra_arguments],
                              capture_output=True, stdin=subprocess.DEVNULL,
                              env={**os.environ, **CLI_ENCODING_ENV},
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NotShown(REASON_CLI_TIMEOUT.format(seconds=timeout))
    if done.returncode != 0:
        error = " ".join(done.stderr.decode("utf-8", "replace").split())
        raise NotShown(REASON_CLI_FAILED.format(
            code=done.returncode, error=error[:ERROR_EXCERPT_CHARS]))
    table = done.stdout.decode("utf-8")
    if not table:
        raise NotShown(REASON_CLI_SILENT)
    return table


def expand_status_command(payload, launcher=CLI_LAUNCHER,
                          timeout=CLI_TIMEOUT_SECONDS):
    """Print the block for a `/rules-by-trigger:status` expansion; for any other
    command, or when the table cannot be had, print nothing."""
    if payload.get("command_name") != COMMAND_NAME:
        return
    try:
        extra = cli_arguments(payload.get("command_args"))
        table = table_of(extra, launcher, timeout)
    except NotShown as exc:
        warn(FALLBACK_NOTICE.format(reason=exc))
        return
    except (OSError, ValueError) as exc:
        warn(FALLBACK_NOTICE.format(reason=f"{type(exc).__name__}: {exc}"))
        return
    print(json.dumps({"decision": "block", "reason": table}))


def status_command():
    """Hook entry: read the event from stdin and expand it."""
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        warn(FALLBACK_NOTICE.format(reason=f"event unreadable: {exc}"))
        return
    if isinstance(payload, dict):
        expand_status_command(payload)
