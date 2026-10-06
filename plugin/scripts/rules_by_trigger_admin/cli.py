"""Argument parsing and dispatch: every subcommand this CLI accepts is one
entry of COMMANDS, so the list a user sees and the function that runs cannot
drift apart."""

import argparse
import sys

from .common import HOOK, AdminError, fail, warn
from .configargs import (check_config_operands, check_setup_flags,
                         uses_setup_flags)
from .configcmd import cmd_config
from .digest import cmd_digest
from .doctor import cmd_doctor
from .block import cmd_block
from .migrate import cmd_migrate
from .move import ANCHOR_CHOICES, cmd_move
from .lifecycle import cmd_remove
from .rename import cmd_rename
from .rules import cmd_add, cmd_init, cmd_list, cmd_show, cmd_update
from .setup import is_set_up, setup_notice
from .split import SPLIT_HELP, cmd_split
from .status import cmd_status
from .validate import cmd_validate
from .which import cmd_which

# Declaration order is what `--help` and the "invalid choice" error list.
COMMANDS = {"init": cmd_init, "list": cmd_list, "show": cmd_show,
            "which": cmd_which, "add": cmd_add, "update": cmd_update,
            "remove": cmd_remove, "validate": cmd_validate,
            "config": cmd_config, "migrate": cmd_migrate,
            "block": cmd_block, "status": cmd_status,
            "doctor": cmd_doctor, "move": cmd_move, "digest": cmd_digest,
            "rename": cmd_rename, "split": cmd_split}

# The commands that take their rule as an argument after the command name, and
# what each argument is called: `rename <rule> <new-name>`, `split <rule>`.
OPERANDS = {"rename": ("rule", "new_name"), "split": ("rule",)}
OPERANDS_USAGE = {"rename": "<rule> <new-name>", "split": "<rule>"}
EPILOG = SPLIT_HELP

# Commands that never carry the setup notice. `config` and `doctor` are where
# the notice is answered, so a reminder on top of them would be noise. `show`
# prints a rule document that the documented `show -> edit -> update` round
# trip feeds back into `update`: a line above its `---` would end up inside
# the rule's body. `status --json` is exempt too, through `args.json`.
SETUP_NOTICE_EXEMPT_COMMANDS = ("config", "doctor", "show")

# Commands that run without `--root`/`--global`; a setup flag does too, since it
# acts on this machine and not on a scope.
COMMANDS_WITHOUT_SCOPE = ("status",)

# `block` answered to `enforce` until 0.7.0, alongside the frontmatter key of
# the same name. Kept as an alias — and out of COMMANDS, so `--help` teaches
# only the current name — because the old one is written into scripts and into
# skill instructions that were shipped, and failing them on "invalid choice"
# helps nobody. It warns rather than dying, and is resolved before validation
# so every message below names the command the user actually typed.
COMMAND_ALIASES = {"enforce": "block"}


def main():
    parser = argparse.ArgumentParser(
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=list(COMMANDS) + list(COMMAND_ALIASES))
    parser.add_argument("operands", nargs="*", metavar="argument",
                        help="rename: <rule> <new-name>; split: <rule>; "
                             "config: <key> <value>")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--root", help="project root (the folder containing "
                                      ".claude/); on status, the folder it "
                                      "starts from")
    scope.add_argument("--global", dest="use_global", action="store_true",
                       help="global scope (~/.claude/rules-by-trigger)")
    parser.add_argument("--glob", action="append", default=[],
                        help="glob the rule applies to; repeat for several")
    parser.add_argument("--call", action="append", default=[],
                        help="call trigger the rule fires on, written "
                             "Tool(field=value); repeat for several. "
                             "'none' clears them on `update`; on `which`, "
                             "the one call to test, in place of --path")
    parser.add_argument("--exclude", action="append", default=[],
                        help="glob the rule must NOT apply to, even when a "
                             "--glob covers it; repeat for several")
    parser.add_argument("--tool", choices=[*HOOK.TOOL_KINDS, HOOK.TOOL_KIND_ANY],
                        help="restrict the rule to write tool calls "
                             "(Write/Edit/MultiEdit/NotebookEdit) or to reads; "
                             f"'{HOOK.TOOL_KIND_ANY}' clears the restriction. "
                             "On `which`, asks what fires for that kind of call")
    parser.add_argument("--verify", action="append", default=[],
                        help="command to run at the end of a turn in which a "
                             "file this rule covers was written; repeat for "
                             f"several, '{HOOK.VERIFY_NONE}' clears them")
    parser.add_argument("--rule", help="rule file name")
    parser.add_argument("--type", dest="type",
                        help="rule type prefix (see `config` for the configured "
                             "ones); required by `add`")
    parser.add_argument("--remember-again-after", dest="remember_again_after",
                        help="how far the context may move before the rule is "
                             "sent again: '30k' (tokens), '25 calls', or 'never'. "
                             "Defaults to what the rule's type declares")
    parser.add_argument("--force", action="store_true", help="overwrite an existing rule")
    parser.add_argument("--allow-duplicate", dest="allow_duplicate",
                        action="store_true",
                        help="add: create the rule even though a disabled rule "
                             "of this scope declares the same glob or call "
                             "(--force only overwrites a rule of the same name)")
    parser.add_argument("--delete", action="store_true",
                        help="remove: delete the rule file and its usage "
                             "history instead of disabling the rule")
    parser.add_argument("--enable", action="store_true",
                        help="update: switch a disabled rule back on (with no "
                             "body on stdin, nothing else changes)")
    parser.add_argument("--path", help="file/folder to resolve (which; optional "
                                       "on status, where relative paths start "
                                       "from the folder status starts from)")
    parser.add_argument("--json", action="store_true",
                        help="status: print the rules and their improvement "
                             "candidates as JSON")
    parser.add_argument("--to-global", dest="to_global", action="store_true",
                        help="move: destination is the global scope")
    parser.add_argument("--to-root", dest="to_root",
                        help="move: destination is this project root")
    parser.add_argument("--anchor", choices=list(ANCHOR_CHOICES),
                        help="move --to-global: what a root-anchored glob "
                             "should mean there — in any project (**/glob) or "
                             "only in this one (absolute path)")
    parser.add_argument("--sessions", type=int,
                        help="digest: how many recent sessions to distill")
    parser.add_argument("--max-chars", dest="max_chars", type=int,
                        help="digest: overall size budget of the output")
    parser.add_argument("--fix", action="store_true",
                        help="doctor: apply the deterministic fixes (migration) "
                             "and re-check")
    parser.add_argument("--uninstall", action="store_true",
                        help="doctor: remove the deny entries and cached state "
                             "the plugin left behind; rule directories are kept")
    parser.add_argument("--setup", action="store_true",
                        help="config: record this machine's setup consent in "
                             "~/.claude/rules-by-trigger/config.json; needs "
                             "--language and --harden/--no-harden, or "
                             "--decline")
    parser.add_argument("--language",
                        help="config --setup: language for rule bodies and the "
                             "text the hook injects around them")
    harden_group = parser.add_mutually_exclusive_group()
    harden_group.add_argument("--harden", dest="harden", action="store_true",
                              default=None,
                              help="config: apply the recommended permission "
                                   "hardening to ~/.claude/settings.json "
                                   "(edits the user's own file — ask first)")
    harden_group.add_argument("--no-harden", dest="harden", action="store_false",
                              help="config --setup: skip the hardening")
    parser.add_argument("--decline", action="store_true",
                        help="config --setup: record the setup decision "
                             "without hardening or a language")
    parser.add_argument("--list", action="store_true",
                        help="block: show block: true rules and their native "
                             "deny equivalents")
    parser.add_argument("--sync", action="store_true",
                        help="block: write the native deny entries a project's "
                             "block: true rules need into its settings.json")
    args = parser.parse_intermixed_args()

    # `status` alone may leave the scope out: it then starts from
    # CLAUDE_PROJECT_DIR or the current folder. So does a setup flag: the setup
    # is about this machine, not about a scope, and on any command but
    # `config` it is refused below by name rather than as a missing scope.
    scope_free = (args.command in COMMANDS_WITHOUT_SCOPE
                  or uses_setup_flags(args))
    if not scope_free and not (args.root or args.use_global):
        parser.error("one of the arguments --root --global is required")

    if args.command in OPERANDS:
        names = OPERANDS[args.command]
        if (len(args.operands) != len(names) or args.rule or args.glob
                or args.type or args.remember_again_after):
            fail(f"'{args.command}' takes {OPERANDS_USAGE[args.command]} and no "
                 f"--rule/--glob/--type/--remember-again-after")
        for attribute, value in zip(names, args.operands):
            setattr(args, attribute, value)
    elif args.command == "config":
        check_config_operands(args)
    elif args.operands:
        fail(f"'{args.command}' takes no positional arguments "
             f"({' '.join(args.operands)!r}); they belong to `rename`, `split` "
             f"and `config`")

    if args.command in COMMAND_ALIASES:
        current = COMMAND_ALIASES[args.command]
        warn(f"{args.command!r} is the name {current!r} carried until 0.7.0; "
             f"use {current!r}")
        args.command = current

    if args.command == "add" and not (args.glob or args.call):
        fail("'add' requires --glob or --call")
    for flag, owner in (("allow_duplicate", "add"), ("delete", "remove"),
                        ("enable", "update")):
        if getattr(args, flag) and args.command != owner:
            fail(f"'{args.command}' takes no --{flag.replace('_', '-')}; it "
                 f"belongs to `{owner}`")
    if args.command in ("show", "update") and not args.rule:
        fail(f"'{args.command}' requires --rule")
    if args.command == "remove":
        if not (args.rule or args.glob):
            fail("'remove' requires --rule or --glob")
        if args.rule and args.glob:
            fail("'remove' takes --rule OR --glob, not both")
        if args.glob:
            args.glob = args.glob[0]
    if args.command == "which":
        if not (args.path or args.call):
            fail("'which' requires --path or --call")
        if args.path and args.call:
            fail("'which' takes --path OR --call, not both")
        if len(args.call) > 1:
            fail("'which --call' takes one call trigger; run it again for another")
    if args.command == "move":
        if not args.rule:
            fail("'move' requires --rule")
        if bool(args.to_global) == bool(args.to_root):
            fail("'move' requires --to-global OR --to-root <project-root>")
        if args.anchor and not args.to_global:
            fail("--anchor only means something with --to-global")
    elif args.to_global or args.to_root or args.anchor:
        fail(f"'{args.command}' takes no --to-global/--to-root/--anchor; they "
             f"belong to `move`")
    # A filter only means something on a file the command actually writes or
    # resolves. Accepting it silently elsewhere would read as "this rule now
    # excludes X" when nothing was written at all.
    if args.command not in ("add", "update", "which", "status"):
        if args.exclude or args.tool:
            fail(f"'{args.command}' takes no --exclude/--tool; they belong to "
                 f"`add`, `update` and `which`")
    if args.command == "status" and args.exclude:
        fail("'status' takes no --exclude")
    # `--call` is narrower still: `status` never took `--exclude` either, and a
    # call trigger is never something a command merely resolves against — only
    # the two that WRITE a rule, plus `which`'s own alternative to --path.
    if args.call and args.command not in ("add", "update", "which"):
        fail(f"'{args.command}' takes no --call; it belongs to `add`, "
             f"`update` and `which`")
    # `--verify` is narrower still: it does not describe a rule, it declares
    # what one runs, so only the two commands that WRITE a rule accept it.
    if args.verify and args.command not in ("add", "update"):
        fail(f"'{args.command}' takes no --verify; it belongs to `add` and "
             f"`update`")
    if args.json and args.command != "status":
        fail(f"'{args.command}' takes no --json; it belongs to `status`")
    if (args.fix or args.uninstall) and args.command != "doctor":
        fail(f"'{args.command}' takes no --fix/--uninstall; they belong to `doctor`")
    if args.fix and args.uninstall:
        fail("'doctor' takes --fix OR --uninstall, not both")
    if (args.sessions or args.max_chars) and args.command != "digest":
        fail(f"'{args.command}' takes no --sessions/--max-chars; they belong to `digest`")
    if args.command == "block":
        if not (args.list or args.sync):
            fail("'block' requires --list or --sync")
        if args.list and args.sync:
            fail("'block' takes --list OR --sync, not both")
    check_setup_flags(args)

    if (args.command not in SETUP_NOTICE_EXEMPT_COMMANDS and not args.json
            and not is_set_up()):
        print(setup_notice())

    COMMANDS[args.command](args)


def run():
    """Every failure leaves as one line on stderr and exit 1 — including an
    unexpected one. A traceback tells the model driving this CLI nothing it can
    act on, and `show` used to emit one for a rule saved in cp1252."""
    try:
        main()
    except AdminError as error:
        print(f"rules-by-trigger-admin: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001 - deliberate last resort
        print(f"rules-by-trigger-admin: unexpected error: {error!r}", file=sys.stderr)
        return 1
    return 0
