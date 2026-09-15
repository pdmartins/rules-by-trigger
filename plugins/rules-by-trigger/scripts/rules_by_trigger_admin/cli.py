"""Argument parsing and dispatch: every subcommand this CLI accepts is one
entry of COMMANDS, so the list a user sees and the function that runs cannot
drift apart."""

import argparse
import sys

from .common import HOOK, AdminError, fail, warn
from .config import cmd_config
from .digest import cmd_digest
from .doctor import cmd_doctor
from .block import cmd_block
from .migrate import cmd_migrate
from .move import ANCHOR_CHOICES, cmd_move
from .rules import (cmd_add, cmd_init, cmd_list, cmd_remove, cmd_show,
                    cmd_update)
from .setup import is_set_up, setup_notice
from .status import cmd_status
from .validate import cmd_validate
from .which import cmd_which

# Declaration order is what `--help` and the "invalid choice" error list.
COMMANDS = {"init": cmd_init, "list": cmd_list, "show": cmd_show,
            "which": cmd_which, "add": cmd_add, "update": cmd_update,
            "remove": cmd_remove, "validate": cmd_validate,
            "config": cmd_config, "migrate": cmd_migrate,
            "block": cmd_block, "status": cmd_status,
            "doctor": cmd_doctor, "move": cmd_move, "digest": cmd_digest}

# Commands that never carry the setup notice. `doctor` is where the notice is
# answered (`--setup`), so a reminder on top of it would be noise. `show`
# prints a rule document that the documented `show -> edit -> update` round
# trip feeds back into `update`: a line above its `---` would end up inside
# the rule's body. `status --json` is exempt too, through `args.json`.
SETUP_NOTICE_EXEMPT_COMMANDS = ("doctor", "show")

# `block` answered to `enforce` until 0.7.0, alongside the frontmatter key of
# the same name. Kept as an alias — and out of COMMANDS, so `--help` teaches
# only the current name — because the old one is written into scripts and into
# skill instructions that were shipped, and failing them on "invalid choice"
# helps nobody. It warns rather than dying, and is resolved before validation
# so every message below names the command the user actually typed.
COMMAND_ALIASES = {"enforce": "block"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=list(COMMANDS) + list(COMMAND_ALIASES))
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--root", help="project root (the folder containing .claude/)")
    scope.add_argument("--global", dest="use_global", action="store_true",
                       help="global scope (~/.claude/rules-by-trigger)")
    parser.add_argument("--glob", action="append", default=[],
                        help="glob the rule applies to; repeat for several")
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
    parser.add_argument("--path", help="file/folder to resolve (which; optional "
                                       "on status)")
    parser.add_argument("--json", action="store_true",
                        help="status: print the report as JSON")
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
                        help="doctor: record this machine's setup consent in "
                             "~/.claude/rules-by-trigger/config.json, then run "
                             "the normal report; needs --language and "
                             "--harden/--no-harden, or --decline")
    parser.add_argument("--language",
                        help="doctor --setup: language for rule bodies and the "
                             "text the hook injects around them")
    harden_group = parser.add_mutually_exclusive_group()
    harden_group.add_argument("--harden", dest="harden", action="store_true",
                              default=None,
                              help="doctor: apply the recommended permission "
                                   "hardening to ~/.claude/settings.json "
                                   "(edits the user's own file — ask first)")
    harden_group.add_argument("--no-harden", dest="harden", action="store_false",
                              help="doctor --setup: skip the hardening")
    parser.add_argument("--decline", action="store_true",
                        help="doctor --setup: record the setup decision "
                             "without hardening or a language")
    parser.add_argument("--list", action="store_true",
                        help="block: show block: true rules and their native "
                             "deny equivalents")
    parser.add_argument("--sync", action="store_true",
                        help="block: write the native deny entries a project's "
                             "block: true rules need into its settings.json")
    args = parser.parse_args()

    if args.command in COMMAND_ALIASES:
        current = COMMAND_ALIASES[args.command]
        warn(f"{args.command!r} is the name {current!r} carried until 0.7.0; "
             f"use {current!r}")
        args.command = current

    if args.command == "add" and not args.glob:
        fail("'add' requires --glob")
    if args.command in ("show", "update") and not args.rule:
        fail(f"'{args.command}' requires --rule")
    if args.command == "remove":
        if not (args.rule or args.glob):
            fail("'remove' requires --rule or --glob")
        if args.rule and args.glob:
            fail("'remove' takes --rule OR --glob, not both")
        if args.glob:
            args.glob = args.glob[0]
    if args.command == "which" and not args.path:
        fail("'which' requires --path")
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
    # `--setup` and its own sub-flags belong to `doctor` alone, the same way
    # `--fix`/`--uninstall` do above.
    if (args.setup or args.language is not None or args.decline) and args.command != "doctor":
        fail(f"'{args.command}' takes no --setup/--language/--decline; they "
             f"belong to `doctor`")
    if args.harden is not None and args.command != "doctor":
        fail(f"'{args.command}' takes no --harden/--no-harden; they belong to `doctor`")
    if args.decline and not args.setup:
        fail("'--decline' only means something with `doctor --setup`")
    if args.language is not None and not args.setup:
        fail("'--language' only means something with `doctor --setup`")
    if args.harden is False and not args.setup:
        fail("'--no-harden' only means something with `doctor --setup`")
    if args.harden is not None and args.uninstall:
        fail("'doctor' takes --harden/--no-harden OR --uninstall, not both")
    if args.language is not None:
        sanitized = HOOK.sanitize_language(args.language, "--language")
        if sanitized is None:
            fail(f"--language {args.language[:40]!r} is not usable — see stderr")
        args.language = sanitized
    if args.setup:
        if args.fix or args.uninstall:
            fail("'doctor --setup' takes no --fix/--uninstall")
        if args.decline:
            if args.language is not None or args.harden is not None:
                fail("'doctor --setup --decline' takes no --language/--harden/"
                     "--no-harden")
        elif args.language is None or args.harden is None:
            fail("'doctor --setup' requires --language and one of "
                 "--harden/--no-harden (or --decline to skip both)")

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
