"""`split <rule>`: one rule becomes several, in the same scope, and the history
stays with the piece that keeps the original name.

The pieces arrive as JSON on stdin (see SPLIT_HELP). The piece named like the
original rewrites the original file — it is written last, so it still names
the old globs until nothing needs them — and keeps the usage history; every
other piece is a new file that starts from zero. When no piece keeps the name,
the original is deleted with its history.

A piece declares what `add` takes (name with its type prefix, globs and/or
calls, filters, `verify`, schedule, body). What a rule is rather than says —
`enabled`, `block`, `description` and any other key this tool does not own — is
carried over from the original, so splitting a disabled rule yields disabled
pieces and writes no settings line."""

import json
import os
import sys

from .carry import (DEST_NEW, DEST_OVERWRITE, DEST_SAME, clear_overwritten,
                    discard_history, plan_destination)
from .common import (HOOK, MAX_ECHOED_NAME_CHARS, atomic_write,
                     existing_rule_path, fail, preserved_fields,
                     read_regular_file, rule_path, warn_if_long, scope_for)
from .config import check_remember_again_after, config_for, resolve_type
from .lifecycle import holder, sync_lines
from .rules import render_rule
from .validate import validate_scope

MIN_PIECES = 2
PIECE_NAME = "name"
PIECE_BODY = "body"
PIECE_GLOB = "glob"
PIECE_CALL = "call"
PIECE_EXCLUDE = "exclude"
PIECE_TOOL = "tool"
PIECE_VERIFY = "verify"
PIECE_INTERVAL = "remember_again_after"
PIECE_KEYS = (PIECE_NAME, PIECE_BODY, PIECE_GLOB, PIECE_CALL, PIECE_EXCLUDE,
              PIECE_TOOL, PIECE_VERIFY, PIECE_INTERVAL)

# ---- user-visible text ------------------------------------------------------
SPLIT_HELP = """\
split <rule>  reads the pieces as JSON on stdin: an array of at least two
objects, each one rule that stays in the original's scope.
  name                  required; the file name with its type prefix, e.g. CONV_api-errors.md
  body                  required; the rule text
  glob, call            a string or an array of strings; at least one of the two
  exclude, tool, verify strings or arrays, as `add` takes them
  remember_again_after  a string, as `add` takes it; the type's default otherwise
The piece named like the original keeps its usage history and rewrites it; the
others start from zero. With no piece named like it, the original is deleted
with its history. A piece declares everything it needs (nothing but the
original's enabled, block, description and custom keys is inherited). With
--force, a piece may replace a rule of that name, whose history is discarded.
Example: [{"name": "CONV_api.md", "glob": "src/api/**", "body": "..."},
          {"name": "CONV_web.md", "glob": "src/web/**", "body": "..."}]"""
ERROR_NO_INPUT = "split needs the pieces as JSON on stdin; see `--help`"
ERROR_NOT_JSON = "the pieces are not valid JSON ({why}); see `--help`"
ERROR_NOT_AN_ARRAY = "the pieces must be a JSON array of at least {minimum} objects"
ERROR_NOT_AN_OBJECT = "piece {number} is not a JSON object"
ERROR_UNKNOWN_KEY = "piece {number} has keys split does not know: {keys}; known: {known}"
ERROR_MISSING = "piece {number} has no {key}"
ERROR_NOT_TEXT = "piece {number}: {key} must be {what}"
ERROR_NO_TRIGGER = "piece {name} has neither a glob nor a call"
ERROR_BAD_TOOL = "piece {name}: tool must be one of {allowed}"
ERROR_DUPLICATE = "two pieces are named {name}"
ERROR_NOT_A_RULE = "{name} is not a rule (no frontmatter); nothing to split"
MESSAGE_SPLIT = "ok: split {name} into {names}"
MESSAGE_KEPT = "    {original} keeps its history"
MESSAGE_DELETED = "    {original} is gone, with its history"
WHAT_STRINGS = "a string or an array of strings"
WHAT_STRING = "a string"
# -----------------------------------------------------------------------------


def strings_of(piece, key, number):
    """The list of strings a piece declares under `key`: a string is one."""
    value = piece.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not (isinstance(value, list)
            and all(isinstance(item, str) for item in value)):
        fail(ERROR_NOT_TEXT.format(number=number, key=key, what=WHAT_STRINGS))
    return [item.strip() for item in value if item.strip()]


def text_of(piece, key, number, required=False):
    value = piece.get(key)
    if value is None and required:
        fail(ERROR_MISSING.format(number=number, key=key))
    if value is not None and not isinstance(value, str):
        fail(ERROR_NOT_TEXT.format(number=number, key=key, what=WHAT_STRING))
    return value


def read_pieces(raw):
    """The JSON array of pieces, checked for shape."""
    if not raw.strip():
        fail(ERROR_NO_INPUT)
    try:
        pieces = json.loads(raw)
    except ValueError as exc:
        fail(ERROR_NOT_JSON.format(why=exc))
    if not isinstance(pieces, list) or len(pieces) < MIN_PIECES:
        fail(ERROR_NOT_AN_ARRAY.format(minimum=MIN_PIECES))
    for number, piece in enumerate(pieces, start=1):
        if not isinstance(piece, dict):
            fail(ERROR_NOT_AN_OBJECT.format(number=number))
        unknown = sorted(set(piece) - set(PIECE_KEYS))
        if unknown:
            fail(ERROR_UNKNOWN_KEY.format(number=number, keys=", ".join(unknown),
                                          known=", ".join(PIECE_KEYS)))
    return pieces


def render_piece(piece, number, original, config, scope_dir):
    """(file name, rule text, globs) of one piece, refused here — before
    anything is written — when it is not a rule the hook would run."""
    prefix, name = resolve_type(config, None, text_of(piece, PIECE_NAME, number, True))
    rule_path(scope_dir, name)  # refuses a name the hook would not read
    body = (text_of(piece, PIECE_BODY, number, True) or "").strip()
    if not body:
        fail(ERROR_MISSING.format(number=number, key=PIECE_BODY))
    globs, calls = strings_of(piece, PIECE_GLOB, number), strings_of(piece, PIECE_CALL, number)
    if not globs and not calls:
        fail(ERROR_NO_TRIGGER.format(name=name[:MAX_ECHOED_NAME_CHARS]))
    tool = strings_of(piece, PIECE_TOOL, number)
    if any(value not in HOOK.TOOL_KINDS for value in tool):
        fail(ERROR_BAD_TOOL.format(name=name, allowed=", ".join(HOOK.TOOL_KINDS)))
    interval = (text_of(piece, PIECE_INTERVAL, number)
                or HOOK.remember_again_after_for_type(config, prefix))
    check_remember_again_after(interval)
    text = render_rule(globs, body, interval,
                       preserved_fields(original, owned_last=True),
                       excludes=strings_of(piece, PIECE_EXCLUDE, number), tool=tool,
                       verify=strings_of(piece, PIECE_VERIFY, number), calls=calls)
    warn_if_long(name, body, config)
    return name, text, globs


def cmd_split(args):
    scope_dir, anchor = scope_for(args)
    config = config_for(args)
    original_name = args.rule
    source_path = existing_rule_path(scope_dir, original_name)
    original_text = read_regular_file(source_path, os.path.getsize(source_path))
    original, _body = HOOK.parse_frontmatter(original_text)
    if not original:
        fail(ERROR_NOT_A_RULE.format(name=original_name))
    pieces = [render_piece(piece, number, original, config, scope_dir)
              for number, piece in enumerate(read_pieces(sys.stdin.read()), start=1)]
    names = [name for name, _text, _globs in pieces]
    for name in names:
        if names.count(name) > 1:
            fail(ERROR_DUPLICATE.format(name=name))

    # Every refusal comes before the first write: a collision leaves no trace.
    new_pieces = [piece for piece in pieces if piece[0] != original_name]
    states = {name: plan_destination(rule_path(scope_dir, name), text, name, "this",
                                     args.force)
              for name, text, _globs in new_pieces}
    # A new piece starts from zero: what an earlier rule of that name left
    # behind is discarded when the file is created, never on a resume.
    for name, state in states.items():
        if state == DEST_OVERWRITE:
            clear_overwritten(args.use_global, scope_dir, anchor, name)
        elif state == DEST_NEW:
            discard_history(scope_dir, name)
    for name, text, _globs in new_pieces:
        if states[name] != DEST_SAME:
            atomic_write(rule_path(scope_dir, name), text)
    sync_lines(args.use_global, scope_dir, anchor, [holder(original_name, original)],
               [holder(name, original, globs) for name, _text, globs in pieces])
    kept = next((text for name, text, _globs in pieces if name == original_name), None)
    if kept is None:
        discard_history(scope_dir, original_name)
        os.unlink(source_path)
    else:
        atomic_write(source_path, kept)

    print(MESSAGE_SPLIT.format(name=original_name, names=", ".join(names)))
    print(MESSAGE_DELETED.format(original=original_name) if kept is None
          else MESSAGE_KEPT.format(original=original_name))
    validate_scope(scope_dir, anchor, quiet=True, config=config,
                   is_global=args.use_global)
