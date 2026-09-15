"""`verify:` as the frontmatter parser reads it. The admin CLI's half is in
test_verify_admin.py.

The property asserted throughout is the same one the other keys answer to: a
command the parser cannot honour is DROPPED and said out loud, never truncated
and never half-run — and an absent key costs nothing, because this runs on the
hook's hot path.
"""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

COMMAND = "make test"
OTHER_COMMAND = "ruff check src"


def verify_of(fields):
    """`verify_of` plus whatever it warned about, so every test can assert on
    both halves of one call."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        commands = HOOK.verify_of(fields)
    return commands, stderr.getvalue()


class VerifyParsingTest(unittest.TestCase):
    def test_a_scalar_and_a_list_read_the_same_way(self):
        self.assertEqual(verify_of({"verify": COMMAND})[0], [COMMAND])
        self.assertEqual(verify_of({"verify": [COMMAND, OTHER_COMMAND]})[0],
                         [COMMAND, OTHER_COMMAND])

    def test_both_shapes_survive_the_real_parser(self):
        """The two spellings `glob` and `exclude` accept, on the same key."""
        fields, _ = HOOK.parse_frontmatter(
            f"---\nglob: src/**\nverify: {COMMAND}\n---\nbody")
        self.assertEqual(HOOK.verify_of(fields), [COMMAND])
        fields, _ = HOOK.parse_frontmatter(
            f"---\nglob: src/**\nverify:\n  - {COMMAND}\n"
            f"  - {OTHER_COMMAND}\n---\nbody")
        self.assertEqual(HOOK.verify_of(fields), [COMMAND, OTHER_COMMAND])

    def test_a_quoted_command_keeps_its_shell_syntax(self):
        fields, _ = HOOK.parse_frontmatter(
            '---\nglob: src/**\nverify: "pytest -q tests/ --maxfail=1"\n---\nx')
        self.assertEqual(HOOK.verify_of(fields), ["pytest -q tests/ --maxfail=1"])

    def test_an_absent_key_is_no_command_and_no_warning(self):
        """Every write of every turn reaches this function; a rule declaring no
        verification is the normal case, not something to complain about."""
        commands, warnings = verify_of({"glob": "src/**"})
        self.assertEqual(commands, [])
        self.assertEqual(warnings, "")

    def test_a_key_with_nothing_under_it_reads_as_no_command(self):
        fields, _ = HOOK.parse_frontmatter("---\nglob: src/**\nverify:\n---\nx")
        self.assertEqual(HOOK.verify_of(fields), [])

    def test_blank_items_are_dropped(self):
        self.assertEqual(verify_of({"verify": ["", "   ", COMMAND]})[0], [COMMAND])

    def test_the_off_word_turns_the_key_off_rather_than_naming_a_command(self):
        """`--verify none` is how the CLI clears the key, so a rule carrying
        the word must mean 'no verification' — not a command called `none`
        that fails and holds the turn open."""
        self.assertEqual(verify_of({"verify": HOOK.VERIFY_NONE})[0], [])
        self.assertEqual(verify_of({"verify": HOOK.VERIFY_NONE.upper()})[0], [])

    def test_commands_past_the_limit_are_dropped_with_a_warning(self):
        declared = [f"cmd{index}" for index in range(HOOK.MAX_VERIFY_COMMANDS + 3)]
        commands, warnings = verify_of({"verify": declared})
        self.assertEqual(commands, declared[:HOOK.MAX_VERIFY_COMMANDS])
        self.assertIn("3 ignored", warnings)

    def test_a_command_over_the_size_limit_is_dropped_not_truncated(self):
        """Half a command line is not a command, so it is refused whole."""
        oversized = "x" * (HOOK.MAX_VERIFY_COMMAND_CHARS + 1)
        commands, warnings = verify_of({"verify": [oversized, COMMAND]})
        self.assertEqual(commands, [COMMAND])
        self.assertIn(str(HOOK.MAX_VERIFY_COMMAND_CHARS), warnings)

    def test_a_command_at_the_size_limit_is_kept(self):
        exact = "x" * HOOK.MAX_VERIFY_COMMAND_CHARS
        self.assertEqual(verify_of({"verify": exact})[0], [exact])

    def test_the_key_is_the_one_the_constants_declare(self):
        """The hook, the admin CLI and `validate` all address this key through
        the same constant; a private copy is how they drift apart."""
        self.assertEqual(HOOK.VERIFY_KEY, "verify")
        self.assertEqual(verify_of({HOOK.VERIFY_KEY: COMMAND})[0], [COMMAND])


if __name__ == "__main__":
    unittest.main()
