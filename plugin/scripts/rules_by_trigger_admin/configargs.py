"""The command line of `config`, checked before it runs: the operands that
name a key and a value, and the setup flags that belong to this command alone.

Kept out of `cli.py`, which holds the rest of the checks, so that module stays
the list of commands and their options."""

from .common import HOOK, fail

CONFIG_OPERAND_COUNTS = (0, 2)

# ---- user-visible text ------------------------------------------------------
SETUP_FLAGS = "--setup/--language/--decline/--harden/--no-harden"
ERROR_OPERANDS = ("'config' takes no arguments (it shows the config in force) "
                  "or <key> <value> (it writes one key), not {given!r}")
ERROR_FLAGS_ELSEWHERE = "'{command}' takes no {flags}; they belong to `config`"
ERROR_KEY_AND_FLAGS = ("'config' takes <key> <value> OR the setup flags "
                       f"({SETUP_FLAGS}), not both")
ERROR_SETUP_WITH_ROOT = ("the setup flags act on this machine's own files, not "
                         "on a project: leave --root out (--global is accepted)")
ERROR_ONLY_WITH_SETUP = "'{flag}' only means something with `config --setup`"
ERROR_LANGUAGE_UNUSABLE = "--language {language!r} is not usable — see stderr"
ERROR_DECLINE_EXTRAS = ("'config --setup --decline' takes no --language/"
                        "--harden/--no-harden")
ERROR_SETUP_INCOMPLETE = ("'config --setup' requires --language and one of "
                          "--harden/--no-harden (or --decline to skip both)")
# -----------------------------------------------------------------------------


def uses_setup_flags(args):
    return bool(args.setup or args.decline or args.language is not None
                or args.harden is not None)


def check_config_operands(args):
    """`config` alone, or `config <key> <value>`: sets `args.key` and
    `args.value` (both None when nothing was given)."""
    if len(args.operands) not in CONFIG_OPERAND_COUNTS:
        fail(ERROR_OPERANDS.format(given=" ".join(args.operands)[:40]))
    args.key, args.value = args.operands or (None, None)


def check_setup_flags(args):
    """The setup flags go with `config` and with nothing else; with `config`
    they never go with a key, a project root, or each other the wrong way."""
    if not uses_setup_flags(args):
        return
    if args.command != "config":
        fail(ERROR_FLAGS_ELSEWHERE.format(command=args.command, flags=SETUP_FLAGS))
    if args.key:
        fail(ERROR_KEY_AND_FLAGS)
    if args.root:
        fail(ERROR_SETUP_WITH_ROOT)
    if args.decline and not args.setup:
        fail(ERROR_ONLY_WITH_SETUP.format(flag="--decline"))
    if args.language is not None and not args.setup:
        fail(ERROR_ONLY_WITH_SETUP.format(flag="--language"))
    if args.harden is False and not args.setup:
        fail(ERROR_ONLY_WITH_SETUP.format(flag="--no-harden"))
    if args.language is not None:
        sanitized = HOOK.sanitize_language(args.language, "--language")
        if sanitized is None:
            fail(ERROR_LANGUAGE_UNUSABLE.format(language=args.language[:40]))
        args.language = sanitized
    if not args.setup:
        return
    if args.decline:
        if args.language is not None or args.harden is not None:
            fail(ERROR_DECLINE_EXTRAS)
    elif args.language is None or args.harden is None:
        fail(ERROR_SETUP_INCOMPLETE)
