"""`config <key> <value>`: write one key of one config layer.

The value goes through the very functions the hook validates that layer with
when it reads it (`HOOK.sanitize_config`, so the floors and ceilings of a
project layer are the reader's, not a copy of them). The two differ in what
they do with a bad value: the reader warns and ignores it, because nothing may
stop an injection; the writer refuses, because there is a human to tell. A
value the reader would clamp, drop or warn about is therefore refused, with
the reader's own sentence as the reason, and the file is left as it was.

The key is a path in dots. `rule_types` replaces its whole list in the layer
that declares it, so a path inside it writes the list in force with that one
change in it, into the layer being written."""

import contextlib
import io
import json
import os

from .common import HOOK, atomic_write, fail, scope_for
from .config import REINJECT_BUDGET_KEY, config_for
from .setup import is_set_up

KEY_SEPARATOR = "."
# What the reader's warnings call the layer they are about, so that its own
# prefix can be cut off the sentence the refusal quotes.
SOURCE_LABEL = "this value"
INTERVAL_KEY = "remember_again_after"
RULE_SIZE_KEY = "rule_size"
RULE_TYPES_KEY = "rule_types"
# Every key `config` writes, in the order the error lists them. The one the
# reader also knows, `legacy_type_prefixes`, is `migrate`'s and not written here.
WRITABLE_KEYS = (HOOK.LANGUAGE_KEY, RULE_TYPES_KEY, INTERVAL_KEY, RULE_SIZE_KEY,
                 REINJECT_BUDGET_KEY, HOOK.SHOW_INJECTIONS_KEY)
# The keys that hold a small object, and what may be written inside each.
SUBKEYS = {INTERVAL_KEY: HOOK.REMEMBER_UNITS, RULE_SIZE_KEY: HOOK.RULE_SIZE_KEYS}
# What `rule_types.<TYPE>.<field>` may write; a prefix changes through the
# JSON form of `rule_types.<TYPE>`, which is how a type is renamed.
TYPE_FIELDS = ("name", "purpose", INTERVAL_KEY)
# Written as whole numbers: the reader would truncate `3000.7` and read `true`
# as 1, which a writer must not do in silence.
NUMBER_KEYS = (RULE_SIZE_KEY, REINJECT_BUDGET_KEY)

# ---- user-visible text ------------------------------------------------------
ERROR_NOT_SET_UP = ("the setup is not done, so the global config is not "
                    "written outside it; do the setup first: ask the user to "
                    "type /rules-by-trigger:config (or run `config --setup`)")
ERROR_UNKNOWN_KEY = "unknown config key {key!r}; the keys are: {keys}"
ERROR_BAD_PATH = ("{key!r} is not a key `config` writes; inside {top} the "
                  "paths are: {paths}")
ERROR_UNKNOWN_TYPE = ("there is no rule type {prefix!r} in force to change; "
                      "create it by writing rule_types.{prefix} with a JSON "
                      "object (prefix, name, purpose)")
ERROR_TYPE_NOT_OBJECT = ("{key} takes a JSON object with name, purpose and "
                         "optionally remember_again_after, not {value!r}")
ERROR_UNREADABLE = ("{path} exists but could not be read as a JSON object; "
                    "fix it by hand, then run the same command again")
ERROR_NOT_NUMBER = "{key} must be a whole number, not {value!r}"
ERROR_REFUSED = "{key} {value} was refused: {reason}; the file is unchanged"
ERROR_TOO_BIG = ("{path} would be {size} bytes, over the {limit} the reader "
                 "accepts; it is unchanged")
REASON_CHECK_FAILED = "the reader's own check failed ({error!r})"
REASON_NOT_USABLE = "it is not a usable value for this key"
REASON_ADJUSTED = ("the reader would change it to {stored} (a soft limit above "
                   "the hard cut is lowered to it)")
REASON_NOT_KEPT = "the reader would drop it, so it would not take effect"
REASON_PARTS_DROPPED = ("part of it is not usable ({kept} of {given} kept): "
                        "rule types need a prefix, a name and a purpose")
PATHS_OF_TYPES = (f"{RULE_TYPES_KEY}, {RULE_TYPES_KEY}.<TYPE>, "
                  f"{RULE_TYPES_KEY}.<TYPE>.<{'|'.join(TYPE_FIELDS)}>")
PATH_OF_SUBKEY = "{top}.{sub}"
PATHS_SEPARATOR = ", "
MESSAGE_WRITTEN = "ok: wrote {key} in {path}"
# -----------------------------------------------------------------------------


def parse_value(text):
    """The value as JSON when it parses as JSON (`3000`, `true`, a list, an
    object), else as the plain string it was (`pt-BR`, `30k`)."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return text


def read_layer(path):
    """The layer's own JSON object, `{}` when there is no file. A file that is
    there and cannot be read is never replaced: that would erase whatever the
    user kept in it."""
    data = HOOK.read_config_file(path)
    if data is None:
        if os.path.lexists(path):
            fail(ERROR_UNREADABLE.format(path=path))
        return {}
    return data


def sanitize(key, candidate, trusted):
    """(the layer's value for `key`, what the reader said about it, as one
    sentence)."""
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            layer = HOOK.sanitize_config({key: candidate}, SOURCE_LABEL, trusted)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        fail(ERROR_REFUSED.format(key=key, value=json.dumps(candidate),
                                  reason=REASON_CHECK_FAILED.format(error=exc)))
    said = [line.partition(f"{SOURCE_LABEL}: ")[2] or line
            for line in captured.getvalue().splitlines()]
    return layer.get(key), "; ".join(said)


def layer_as_read(layer, key, trusted):
    """What the reader makes of the key as the layer holds it now, with its
    warnings left out: a new sub-key is added to that, not to a damaged one."""
    if key not in layer:
        return {}
    return sanitize(key, layer[key], trusted)[0] or {}


def type_candidate(parts, value, config):
    """The `rule_types` list to validate: the one in force, with the type
    `parts` names replaced (or added), or `value` itself for the whole list."""
    if len(parts) == 1:
        return value
    types = [dict(entry) for entry in HOOK.rule_types(config)]
    prefix = parts[1]
    wanted = prefix.strip().upper()
    index = next((i for i, entry in enumerate(types)
                  if entry["prefix"] == wanted), None)
    if len(parts) == 2:
        if not isinstance(value, dict):
            fail(ERROR_TYPE_NOT_OBJECT.format(
                key=KEY_SEPARATOR.join(parts), value=value))
        entry = {"prefix": prefix, **value}
    elif len(parts) == 3 and parts[2] in TYPE_FIELDS:
        if index is None:
            fail(ERROR_UNKNOWN_TYPE.format(prefix=prefix))
        entry = {**types[index], parts[2]: value}
    else:
        fail(ERROR_BAD_PATH.format(
            key=KEY_SEPARATOR.join(parts), top=RULE_TYPES_KEY,
            paths=PATHS_OF_TYPES))
    if index is None:
        types.append(entry)
    else:
        types[index] = entry
    return types


def candidate_for(parts, value, config, layer, trusted):
    """The value the layer would hold for `parts[0]` once `value` is written
    at the path `parts`."""
    top = parts[0]
    if top == RULE_TYPES_KEY:
        return type_candidate(parts, value, config)
    if len(parts) == 1:
        return value
    allowed = SUBKEYS.get(top)
    if allowed is None or len(parts) != 2 or parts[1] not in allowed:
        paths = [top] + [PATH_OF_SUBKEY.format(top=top, sub=sub)
                         for sub in allowed or ()]
        fail(ERROR_BAD_PATH.format(key=KEY_SEPARATOR.join(parts), top=top,
                                   paths=PATHS_SEPARATOR.join(paths)))
    return {**layer_as_read(layer, top, trusted), parts[1]: value}


def require_whole_numbers(key, top, candidate):
    """`candidate` holds only whole numbers when `top` is one of the numeric
    keys, a JSON `true` or `3000.7` or `"3000"` being what the reader would
    quietly turn into a different number."""
    if top not in NUMBER_KEYS:
        return
    numbers = candidate.values() if isinstance(candidate, dict) else [candidate]
    for number in numbers:
        if isinstance(number, bool) or not isinstance(number, int):
            fail(ERROR_NOT_NUMBER.format(key=key, value=number))


def is_kept(parts, stored):
    """Whether the value written at the path `parts` is in what the reader
    kept: the reader drops, without a word, a `null` where a value was due."""
    if len(parts) == 2 and parts[0] in SUBKEYS:
        return parts[1] in stored
    if len(parts) == 3:
        wanted = parts[1].strip().upper()
        return any(entry["prefix"] == wanted and parts[2] in entry
                   for entry in stored)
    return True


def refuse_unless_kept(parts, value, candidate, stored, warnings):
    """Refuse a value the reader did not take whole: it warned about it, or
    kept nothing, or dropped some of its parts without a word."""
    key = KEY_SEPARATOR.join(parts)
    reason = warnings
    if not reason and stored is None:
        reason = REASON_NOT_USABLE
    if not reason and isinstance(candidate, (dict, list)) \
            and len(stored) != len(candidate):
        reason = REASON_PARTS_DROPPED.format(kept=len(stored),
                                             given=len(candidate))
    if not reason and parts[0] == RULE_SIZE_KEY and stored != candidate:
        reason = REASON_ADJUSTED.format(stored=json.dumps(stored))
    if not reason and not is_kept(parts, stored):
        reason = REASON_NOT_KEPT
    if reason:
        fail(ERROR_REFUSED.format(key=key, value=json.dumps(value),
                                  reason=reason))


def write_key(args):
    """Validate `args.value` for `args.key` and write it into the layer this
    run targets (`--global` or `--root`), keeping every other key of the file.

    Nothing is written unless the whole value is accepted, and the write is one
    atomic replace, so a run that fails leaves the file as it was and running
    the same command again does the whole job."""
    if args.use_global and not is_set_up():
        fail(ERROR_NOT_SET_UP)
    parts = args.key.strip().split(KEY_SEPARATOR)
    top = parts[0]
    if top not in WRITABLE_KEYS:
        fail(ERROR_UNKNOWN_KEY.format(key=args.key[:40],
                                      keys=PATHS_SEPARATOR.join(WRITABLE_KEYS)))
    scope_dir, _anchor = scope_for(args)
    path = HOOK.config_path_for(scope_dir)
    trusted = args.use_global
    value = parse_value(args.value)
    layer = read_layer(path)
    candidate = candidate_for(parts, value, config_for(args), layer, trusted)
    require_whole_numbers(args.key, top, candidate)
    stored, warnings = sanitize(top, candidate, trusted)
    refuse_unless_kept(parts, value, candidate, stored, warnings)

    layer[top] = stored
    text = json.dumps(layer, indent=2) + "\n"
    size = len(text.encode("utf-8"))
    if size > HOOK.MAX_CONFIG_BYTES:
        fail(ERROR_TOO_BIG.format(path=path, size=size,
                                  limit=HOOK.MAX_CONFIG_BYTES))
    atomic_write(path, text)
    print(MESSAGE_WRITTEN.format(key=args.key, path=path))
