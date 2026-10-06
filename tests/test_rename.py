"""`rename <rule> <new-name>` (spec 0008, FR-004): the rule under another name,
its history with it; a taken name is refused and `--force` discards that
rule's history; a run that stopped half way is finished by running it again."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from operationsutil import (DISABLED, NOT_ROOT, OperationsSandbox,  # noqa: E402
                            THIS_REPO_COUNT, TOTAL_COUNT, line_for)

OLD = "BUSN_prod.md"
NEW = "BUSN_production.md"
PROD = "infra/prod/**"


class RenameTest(OperationsSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule(OLD, PROD)
        self.write_settings([line_for(PROD)])
        self.history(self.scope, OLD)

    def test_the_rule_and_its_whole_history_move_to_the_new_name(self):
        before = self.read_rule(OLD)
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_rule(NEW), before)
        self.assertFalse(self.rule_exists(OLD))
        self.assertEqual(self.counts(NEW), (THIS_REPO_COUNT, TOTAL_COUNT))
        self.assertNotIn(self.key(self.scope, OLD), self.stats_keys())
        self.assertEqual(len(self.stats()["rules"][self.key(self.scope, NEW)]["repos"]), 2)

    def test_the_settings_lines_stay_as_they_are(self):
        self.write_settings([line_for(PROD), "Bash(rm:*)"])
        self.rename(OLD, NEW)
        self.assertEqual(self.deny_lines(), [line_for(PROD), "Bash(rm:*)"])

    def test_a_disabled_rule_stays_disabled_and_gets_no_line(self):
        self.remove(OLD)
        self.assertEqual(self.deny_lines(), [])
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(DISABLED, self.read_rule(NEW))
        self.assertEqual(self.deny_lines(), [])

    def test_a_taken_name_is_refused_and_nothing_is_touched(self):
        util.write_rule(self.proj, NEW, "other/**", "ANOTHER RULE")
        self.history(self.scope, NEW, this_repo=1, other_repo=1, total=2)
        before = self.snapshot()
        proc = self.rename(OLD, NEW)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_force_replaces_it_and_discards_the_history_it_had(self):
        util.write_rule(self.proj, NEW, "other/**", "ANOTHER RULE")
        self.history(self.scope, NEW, this_repo=1, other_repo=1, total=2)
        proc = self.rename(OLD, NEW, "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("ANOTHER RULE", self.read_rule(NEW))
        self.assertEqual(self.counts(NEW), (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_force_over_a_rule_with_history_when_the_renamed_one_has_none(self):
        util.write_rule(self.proj, NEW, "other/**", "ANOTHER RULE")
        self.history(self.scope, NEW, this_repo=7, other_repo=0, total=7)
        stats = self.stats()
        del stats["rules"][self.key(self.scope, OLD)]
        self.plant(stats)
        proc = self.rename(OLD, NEW, "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts(NEW), (0, 0))

    def test_a_name_without_a_known_type_is_refused(self):
        proc = self.rename(OLD, "prod.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.rule_exists(OLD))

    def test_the_same_name_and_a_missing_rule_are_refused(self):
        self.assertNotEqual(self.rename(OLD, OLD).returncode, 0)
        self.assertNotEqual(self.rename("BUSN_none.md", NEW).returncode, 0)

    def test_a_global_rule_is_renamed_in_the_global_scope(self):
        self.global_rule("CONV_cs.md", "*.cs")
        self.history(self.global_scope, "CONV_cs.md")
        proc = self.admin("rename", "--global", "CONV_cs.md", "CONV_csharp.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.global_exists("CONV_cs.md"))
        self.assertEqual(self.counts("CONV_csharp.md", "global"),
                         (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_the_arguments_are_checked(self):
        self.assertIn("takes <rule> <new-name>",
                      self.admin("rename", "--root", self.proj, OLD).stderr)
        self.assertIn("takes no positional arguments",
                      self.admin("list", "--root", self.proj, OLD).stderr)

    def test_flags_that_mean_nothing_to_a_rename_or_a_split_are_refused(self):
        for flag in (("--type", "BUSN"), ("--remember-again-after", "20k"),
                     ("--glob", "x/**"), ("--rule", OLD)):
            proc = self.rename(OLD, NEW, *flag)
            self.assertNotEqual(proc.returncode, 0, flag)
            self.assertIn("takes <rule> <new-name>", proc.stderr)
            self.assertTrue(self.rule_exists(OLD))
            proc = self.admin("split", "--root", self.proj, OLD, *flag, stdin="[]")
            self.assertNotEqual(proc.returncode, 0, flag)
            self.assertIn("takes <rule>", proc.stderr)

    @NOT_ROOT
    def test_a_failed_history_update_exits_non_zero_and_a_rerun_finishes(self):
        self.break_stats()
        proc = self.rename(OLD, NEW)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.rule_exists(OLD), "the old file goes last")
        self.assertTrue(self.rule_exists(NEW))
        self.repair_stats()
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(OLD))
        self.assertEqual(self.counts(NEW), (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_a_rerun_after_the_history_moved_counts_nothing_twice(self):
        """The run that died after the history step and before the old file
        went: the old file is still there, the history is not under its key."""
        self.rename(OLD, NEW)
        util.write_file(os.path.join(self.scope, OLD), self.read_rule(NEW))
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(OLD))
        self.assertEqual(self.counts(NEW), (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_a_new_name_with_other_content_is_still_a_collision_on_a_rerun(self):
        self.rename(OLD, NEW)
        util.write_rule(self.proj, OLD, PROD, "A DIFFERENT TEXT",
                        extra_frontmatter=["block: true"])
        proc = self.rename(OLD, NEW)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertTrue(self.rule_exists(OLD))


if __name__ == "__main__":
    unittest.main()
