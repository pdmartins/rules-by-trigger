"""Whether this machine has consented to the plugin's setup, and the notice
told to the model everywhere else until it has.

"Set up" means exactly one file: `~/.claude/rules-by-trigger/config.json`. Its
mere existence is the signal, checked with `os.path.isfile` and nothing more —
an empty `{}` counts, because writing it is itself the record that someone was
asked and answered. The project scope carries no such state of its own:
`config.json` there is repository data like every other project layer, and
asking a project for consent about the machine it happens to be checked out on
would make no sense.

`doctor --setup` is the only writer, and it writes the file LAST (see
`run_setup`): a decline is a legitimate answer that still ends the notice, and
either answer commits by writing the file — nothing here half-applies a
choice."""

import json
import os

from .common import (HOOK, LEVEL_OK, LEVEL_WARN, atomic_write, fail, finding)
from .hardening import apply_hardening

# ---- user-visible text, in display order -----------------------------------
SETUP_APPLYING_HARDEN = "\napplying: the recommended hardening"
SETUP_LANGUAGE_FALLBACK = ("note: {language!r} ships no translation of the "
                           "text the hook injects, which falls back to "
                           "{fallback} — translations shipped: {shipped}")
SETUP_ACCEPTED = ("setup: wrote {path} (language={language}, hardening="
                  "{harden})")
SETUP_DECLINED = "setup: declined — {path} written, settings untouched"
CHECK_NOT_DONE = "setup: not done — {path} does not exist"
CHECK_NOT_DONE_HINT = ("ask the user the language (shipped translations: "
                       "{shipped}) and whether to apply the recommended "
                       "hardening, then `doctor --setup --language <code> "
                       "--harden|--no-harden`; if they decline the setup, "
                       "`doctor --setup --decline`")
CHECK_DONE = "setup: done ({path})"
# -----------------------------------------------------------------------------


def user_config_path():
    """The file whose existence marks this machine as set up — always the
    user's own scope, since the project scope has no "set up" state of its
    own."""
    home_scope = os.path.join(os.path.expanduser("~"), HOOK.RULES_DIR_RELPATH)
    return HOOK.config_path_for(home_scope)


def is_set_up():
    return os.path.isfile(user_config_path())


def setup_notice():
    """The setup notice, in the language of the plugin's OWN config layer —
    never a user or project layer: the user layer does not exist by
    definition, and letting a project layer that arrived with a cloned
    repository choose the wording that asks the machine owner for consent
    would hand it exactly the authority it must not have."""
    config = HOOK.load_config()
    return HOOK.messages_for(HOOK.language(config))[HOOK.SETUP_NOTICE_KEY]


def write_user_config(language):
    """Write (or update) the user's own config.json with the setup decision.

    `language` is the sanitized value to store, or None to leave `language`
    untouched — a `--decline`, which still writes the file so the notice
    stops. An existing file that fails to parse is never clobbered: silently
    replacing it would erase whatever the user had there, so this fails loudly
    and asks for a manual fix instead. An absent file starts from `{}`. Every
    other key already in the file rides through unchanged."""
    path = user_config_path()
    existed = os.path.isfile(path)
    data = HOOK.read_config_file(path)
    if data is None:
        if existed:
            fail(f"{path} exists but could not be read as JSON; fix it by "
                 f"hand, then re-run `doctor --setup`")
        data = {}
    if language is not None:
        data[HOOK.LANGUAGE_KEY] = language
    atomic_write(path, json.dumps(data, indent=2) + "\n")
    return path


def run_setup(args):
    """`doctor --setup`'s own effect, run before the normal report.

    Order matters: the config file is written LAST, so a failure above it
    (an existing file that cannot be parsed, a hardening write that cannot be
    made) leaves the setup notice in place instead of a half-applied setup."""
    if args.decline:
        path = write_user_config(None)
        print(SETUP_DECLINED.format(path=path))
        return
    if not HOOK.has_translation(args.language):
        print(SETUP_LANGUAGE_FALLBACK.format(
            language=args.language, fallback=HOOK.DEFAULT_LANGUAGE,
            shipped=", ".join(HOOK.SHIPPED_LANGUAGES)))
    if args.harden:
        print(SETUP_APPLYING_HARDEN)
        added, removed = apply_hardening()
        for item in added:
            print(f"  + {item}")
        for item in removed:
            print(f"  - {item}")
    path = write_user_config(args.language)
    print(SETUP_ACCEPTED.format(path=path, language=args.language,
                                harden="applied" if args.harden else "skipped"))


def check_setup():
    """The report's own line about consent — WARN, never ERROR: a machine
    that never ran `--setup` still gets everything injected, so this must not
    flip `doctor`'s exit code the way a broken installation does."""
    path = user_config_path()
    if is_set_up():
        return [finding(LEVEL_OK, CHECK_DONE.format(path=path))]
    hint = CHECK_NOT_DONE_HINT.format(shipped=", ".join(HOOK.SHIPPED_LANGUAGES))
    return [finding(LEVEL_WARN, CHECK_NOT_DONE.format(path=path), hint)]
