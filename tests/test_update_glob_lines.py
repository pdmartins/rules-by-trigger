"""A glob change by `update` swaps the `Edit(<glob>)` lines of an active
project rule with `block: true` in the same command (spec 0008, FR-004 and
FR-005). The rule file is written last — it names the old globs until their
lines are gone — so a failed run is finished by running it again."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from operationsutil import (BODY, DISABLED, NOT_ROOT, OperationsSandbox,  # noqa: E402
                            line_for)

NAME = "BUSN_prod.md"
OLD_GLOB = "infra/prod/**"
NEW_GLOB = "infra/live/**"
OTHER_LINE = "Bash(rm:*)"


class GlobChangeLinesTest(OperationsSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule(NAME, OLD_GLOB)
        self.write_settings([line_for(OLD_GLOB), OTHER_LINE])

    def update_glob(self, glob, name=NAME, body="NEW BODY"):
        return self.admin("update", "--root", self.proj, "--rule", name,
                          "--glob", glob, stdin=body)

    def update_with_frontmatter(self, glob, *extra):
        edited = "\n".join(["---", f"glob: {glob}", *extra, "---", "NEW BODY"])
        return self.admin("update", "--root", self.proj, "--rule", NAME, stdin=edited)

    def test_the_line_of_the_old_glob_goes_and_the_one_of_the_new_comes(self):
        proc = self.update_glob(NEW_GLOB)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(NEW_GLOB)])
        self.assertIn(f"glob: {NEW_GLOB}", self.read_rule(NAME))

    def test_a_line_another_active_block_rule_uses_stays(self):
        self.block_rule("BUSN_twin.md", OLD_GLOB)
        self.update_glob(NEW_GLOB)
        self.assertEqual(self.deny_lines(),
                         [line_for(OLD_GLOB), OTHER_LINE, line_for(NEW_GLOB)])

    def test_a_line_that_is_already_there_is_not_written_twice(self):
        self.write_settings([line_for(OLD_GLOB), line_for(NEW_GLOB)])
        self.update_glob(NEW_GLOB)
        self.assertEqual(self.deny_lines(), [line_for(NEW_GLOB)])

    def test_a_glob_changed_through_the_edited_rule_on_stdin_swaps_the_lines_too(self):
        edited = self.read_rule(NAME).replace(OLD_GLOB, NEW_GLOB)
        proc = self.admin("update", "--root", self.proj, "--rule", NAME, stdin=edited)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(NEW_GLOB)])

    def test_an_update_that_keeps_the_globs_touches_no_settings(self):
        self.write_settings([OTHER_LINE])
        proc = self.admin("update", "--root", self.proj, "--rule", NAME, stdin="ONLY THE BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_a_rule_without_block_touches_no_settings(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.update_glob("lib/**", "CONV_a.md")
        self.assertEqual(self.deny_lines(), [line_for(OLD_GLOB), OTHER_LINE])

    def test_a_disabled_rule_stays_disabled_with_no_lines_and_enable_adds_the_current_ones(self):
        self.remove(NAME)
        proc = self.update_glob(NEW_GLOB)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(DISABLED, self.read_rule(NAME))
        self.assertEqual(self.deny_lines(), [OTHER_LINE])
        self.assertEqual(self.enable(NAME).returncode, 0)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(NEW_GLOB)])

    def test_a_glob_change_with_enable_on_an_active_rule_swaps_the_lines(self):
        proc = self.admin("update", "--root", self.proj, "--rule", NAME,
                          "--glob", NEW_GLOB, "--enable", stdin="NEW BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(NEW_GLOB)])

    def test_enable_with_a_body_on_a_disabled_rule_writes_the_lines_of_its_new_globs(self):
        self.remove(NAME)
        proc = self.admin("update", "--root", self.proj, "--rule", NAME,
                          "--glob", NEW_GLOB, "--enable", stdin="NEW BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(DISABLED, self.read_rule(NAME))
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(NEW_GLOB)])

    def test_a_glob_change_that_also_turns_block_off_writes_no_line_for_the_new_glob(self):
        proc = self.update_with_frontmatter(NEW_GLOB, "block: false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_turning_block_off_alone_removes_the_lines_no_other_rule_uses(self):
        self.block_rule("BUSN_twin.md", "infra/own/**")
        self.write_settings([line_for(OLD_GLOB), line_for("infra/own/**")])
        proc = self.update_with_frontmatter(OLD_GLOB, "block: false")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [line_for("infra/own/**")])

    def test_turning_block_off_keeps_a_line_another_active_block_rule_uses(self):
        self.block_rule("BUSN_twin.md", OLD_GLOB)
        self.update_with_frontmatter(OLD_GLOB, "block: false")
        self.assertEqual(self.deny_lines(), [line_for(OLD_GLOB), OTHER_LINE])

    def test_turning_block_on_alone_adds_the_lines(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        proc = self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          stdin="---\nglob: src/**\nblock: true\n---\nBODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(),
                         [line_for(OLD_GLOB), OTHER_LINE, line_for("src/**")])

    @NOT_ROOT
    def test_a_failed_settings_write_leaves_the_rule_alone_and_a_rerun_finishes(self):
        self.make_dot_claude_read_only()
        proc = self.update_glob(NEW_GLOB)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(f"glob: {OLD_GLOB}", self.read_rule(NAME))
        self.assertEqual(self.deny_lines(), [line_for(OLD_GLOB), OTHER_LINE])
        self.make_dot_claude_writable()
        proc = self.update_glob(NEW_GLOB)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"glob: {NEW_GLOB}", self.read_rule(NAME))
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(NEW_GLOB)])


if __name__ == "__main__":
    unittest.main()
