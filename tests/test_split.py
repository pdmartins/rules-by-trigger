"""`split <rule>` (spec 0008, FR-004): one rule becomes several in its own
scope, the history stays with the piece that keeps the name, the settings lines
follow the globs, and a run that stopped half way is finished by running it
again."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from operationsutil import (DISABLED, NOT_ROOT, OperationsSandbox,  # noqa: E402
                            THIS_REPO_COUNT, TOTAL_COUNT, line_for)

NAME = "BUSN_infra.md"
PROD_GLOB = "infra/prod/**"
DEV_GLOB = "infra/dev/**"
PROD = "BUSN_prod.md"
DEV = "BUSN_dev.md"
OTHER_LINE = "Bash(rm:*)"


def piece(name, glob, body="PIECE TEXT", **more):
    return {"name": name, "glob": glob, "body": body, **more}


class SplitTest(OperationsSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule(NAME, PROD_GLOB)
        self.history(self.scope, NAME)

    def split_in_two(self, *flags):
        return self.split(NAME, self.pieces(piece(NAME, PROD_GLOB, "PROD TEXT"),
                                            piece(DEV, DEV_GLOB, "DEV TEXT")), *flags)

    def test_the_piece_with_the_original_name_keeps_the_history_the_others_start_at_zero(self):
        proc = self.split_in_two()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PROD TEXT", self.read_rule(NAME))
        self.assertIn("DEV TEXT", self.read_rule(DEV))
        self.assertEqual(self.counts(NAME), (THIS_REPO_COUNT, TOTAL_COUNT))
        self.assertEqual(self.counts(DEV), (0, 0))

    def test_with_no_piece_keeping_the_name_the_original_and_its_history_go(self):
        proc = self.split(NAME, self.pieces(piece(PROD, PROD_GLOB), piece(DEV, DEV_GLOB)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(NAME))
        self.assertEqual(self.counts(PROD), (0, 0))
        self.assertEqual(self.counts(DEV), (0, 0))
        self.assertNotIn(self.key(self.scope, NAME), self.stats_keys())

    def test_a_piece_declares_what_add_takes_and_nothing_else_is_inherited_but_state(self):
        util.write_rule(self.proj, "CONV_old.md", "src/**", "OLD",
                        extra_frontmatter=["tool: write", "description: kept", "block: true"])
        proc = self.split("CONV_old.md", self.pieces(
            piece("CONV_old.md", "src/a/**", exclude="src/a/x/**", verify=["true"],
                  remember_again_after="20k"),
            piece("CONV_new.md", ["src/b/**", "src/c/**"])))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        first, second = self.read_rule("CONV_old.md"), self.read_rule("CONV_new.md")
        for text in (first, second):
            self.assertIn("description: kept", text)
            self.assertIn("block: true", text)
        self.assertIn("exclude: src/a/x/**", first)
        self.assertIn("remember_again_after: 20k", first)
        self.assertNotIn("tool: write", first + second)
        self.assertIn("  - src/c/**", second)

    def test_a_name_taken_by_another_rule_is_refused_and_nothing_is_touched(self):
        util.write_rule(self.proj, DEV, "other/**", "ANOTHER RULE")
        self.history(self.scope, DEV, this_repo=1, other_repo=1, total=2)
        self.write_settings([line_for(PROD_GLOB), OTHER_LINE])
        before = self.snapshot()
        proc = self.split_in_two()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_force_replaces_it_and_discards_the_history_it_had(self):
        util.write_rule(self.proj, DEV, "other/**", "ANOTHER RULE")
        self.history(self.scope, DEV, this_repo=1, other_repo=1, total=2)
        proc = self.split_in_two("--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("DEV TEXT", self.read_rule(DEV))
        self.assertEqual(self.counts(DEV), (0, 0))
        self.assertEqual(self.counts(NAME), (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_a_new_piece_does_not_inherit_the_history_a_deleted_rule_of_that_name_left(self):
        self.history(self.scope, DEV, this_repo=6, other_repo=0, total=6)
        self.assertFalse(self.rule_exists(DEV))
        proc = self.split_in_two()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts(DEV), (0, 0))
        self.assertEqual(self.counts(NAME), (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_bad_pieces_are_refused_before_anything_is_written(self):
        before = self.snapshot()
        cases = ["", "not json", "{}", json.dumps([piece(NAME, PROD_GLOB)]),
                 self.pieces(piece(NAME, PROD_GLOB), piece(NAME, DEV_GLOB)),
                 self.pieces(piece(NAME, PROD_GLOB), {"name": DEV, "body": "x"}),
                 self.pieces(piece(NAME, PROD_GLOB), piece("dev.md", DEV_GLOB)),
                 self.pieces(piece(NAME, PROD_GLOB), piece(DEV, DEV_GLOB, color="red")),
                 self.pieces(piece(NAME, PROD_GLOB), piece(DEV, DEV_GLOB, tool="sideways"))]
        for stdin in cases:
            proc = self.split(NAME, stdin)
            self.assertNotEqual(proc.returncode, 0, stdin)
            self.assertEqual(self.snapshot(), before, stdin)

    def test_help_documents_the_pieces(self):
        proc = self.admin("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("split <rule>", proc.stdout)
        self.assertIn("remember_again_after", proc.stdout)


class SplitLinesTest(OperationsSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule(NAME, PROD_GLOB)
        self.write_settings([line_for(PROD_GLOB), OTHER_LINE])

    def test_the_lines_of_the_new_globs_come_and_the_unused_ones_go(self):
        proc = self.split(NAME, self.pieces(piece(PROD, DEV_GLOB), piece(DEV, "infra/qa/**")))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(),
                         [OTHER_LINE, line_for(DEV_GLOB), line_for("infra/qa/**")])

    def test_a_glob_a_piece_keeps_keeps_its_line(self):
        self.split(NAME, self.pieces(piece(NAME, PROD_GLOB), piece(DEV, DEV_GLOB)))
        self.assertEqual(self.deny_lines(),
                         [line_for(PROD_GLOB), OTHER_LINE, line_for(DEV_GLOB)])

    def test_a_disabled_rule_splits_into_disabled_pieces_and_no_lines(self):
        self.remove(NAME)
        proc = self.split(NAME, self.pieces(piece(NAME, PROD_GLOB), piece(DEV, DEV_GLOB)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in (NAME, DEV):
            self.assertIn(DISABLED, self.read_rule(name))
        self.assertEqual(self.deny_lines(), [OTHER_LINE])
        self.assertEqual(self.enable(DEV).returncode, 0)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(DEV_GLOB)])


@NOT_ROOT
class InterruptedSplitTest(OperationsSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule(NAME, PROD_GLOB)
        self.write_settings([line_for(PROD_GLOB)])
        self.history(self.scope, NAME)
        self.stdin = self.pieces(piece(NAME, "infra/new/**", "KEPT TEXT"),
                                 piece(DEV, DEV_GLOB, "DEV TEXT"))

    def test_stopped_at_the_settings_a_rerun_finishes_without_force(self):
        self.make_dot_claude_read_only()
        proc = self.split(NAME, self.stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DEV TEXT", self.read_rule(DEV), "the new pieces go first")
        self.assertNotIn("KEPT TEXT", self.read_rule(NAME), "the original goes last")
        self.make_dot_claude_writable()
        proc = self.split(NAME, self.stdin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("KEPT TEXT", self.read_rule(NAME))
        self.assertEqual(self.deny_lines(), [line_for("infra/new/**"), line_for(DEV_GLOB)])
        self.assertEqual(self.counts(NAME), (THIS_REPO_COUNT, TOTAL_COUNT))
        self.assertEqual(self.counts(DEV), (0, 0))

    def test_stopped_at_the_history_a_rerun_finishes_and_drops_it(self):
        self.break_stats()
        stdin = self.pieces(piece(PROD, PROD_GLOB), piece(DEV, DEV_GLOB))
        proc = self.split(NAME, stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.rule_exists(NAME), "the original goes last")
        self.repair_stats()
        proc = self.split(NAME, stdin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(NAME))
        self.assertNotIn(self.key(self.scope, NAME), self.stats_keys())

    def test_a_piece_with_other_content_is_still_a_collision_on_a_rerun(self):
        self.make_dot_claude_read_only()
        self.assertNotEqual(self.split(NAME, self.stdin).returncode, 0)
        self.make_dot_claude_writable()
        util.write_file(os.path.join(self.scope, DEV), "---\nglob: x/**\n---\nEDITED SINCE\n")
        proc = self.split(NAME, self.stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertNotIn("KEPT TEXT", self.read_rule(NAME))


if __name__ == "__main__":
    unittest.main()
