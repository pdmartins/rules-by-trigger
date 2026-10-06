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

    def leave_the_new_file(self):
        """What an earlier run that got as far as writing the new file left:
        the next run goes on from there and reaches the history step."""
        util.write_file(os.path.join(self.scope, NEW), self.read_rule(OLD))

    @NOT_ROOT
    def test_a_failed_history_update_exits_non_zero_and_a_rerun_finishes(self):
        self.leave_the_new_file()
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

    @NOT_ROOT
    def test_an_unusable_usage_file_stops_a_new_name_before_anything_is_written(self):
        """The leftover under a new name is discarded before the file is
        written, so a history that cannot be read or changed stops the command
        there: nothing is written, and the re-run finds a clean start."""
        self.break_stats()
        before = self.snapshot()
        proc = self.rename(OLD, NEW)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.rule_exists(NEW))
        self.assertTrue(self.rule_exists(OLD))
        self.assertEqual(self.snapshot(), before)

    def test_a_usage_file_that_is_a_symlink_hides_no_leftover(self):
        """The usage file cannot be read through a link, so whether a leftover
        sits under the new name is unknowable: the command stops before the
        write instead of taking silence for 'nothing there'."""
        self.history(self.scope, NEW, this_repo=50, other_repo=0, total=50)
        kept = self.stats_bytes()
        replacement = self.stats_file() + ".real"
        os.replace(self.stats_file(), replacement)
        os.symlink(replacement, self.stats_file())
        before = self.snapshot()
        proc = self.rename(OLD, NEW)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.rule_exists(NEW))
        self.assertEqual(self.snapshot(), before)
        os.unlink(self.stats_file())
        util.write_file(self.stats_file(), kept.decode("utf-8"))
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
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

    def test_a_rerun_after_the_history_moved_and_the_rule_fired_keeps_the_moved_history(self):
        """(a) The history step succeeded, removing the old file failed, and
        the rule fired once before the re-run: both files count, so both keys
        are one firing up. The new name ends with what moved, plus that one."""
        self.rename(OLD, NEW)
        util.write_file(os.path.join(self.scope, OLD), self.read_rule(NEW))
        self.add_firing(self.scope, OLD)
        self.add_firing(self.scope, NEW)
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(OLD))
        self.assertEqual(self.counts(NEW), (THIS_REPO_COUNT + 1, TOTAL_COUNT + 1))
        self.assertNotIn(self.key(self.scope, OLD), self.stats_keys())

    @NOT_ROOT
    def test_a_rerun_after_a_failed_history_step_and_a_firing_keeps_the_old_history(self):
        """(b) The history step failed and the rule fired once before the
        re-run: the old name kept counting, the new one started from zero."""
        self.leave_the_new_file()
        self.break_stats()
        self.assertNotEqual(self.rename(OLD, NEW).returncode, 0)
        self.repair_stats()
        self.add_firing(self.scope, OLD)
        self.add_firing(self.scope, NEW)
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(OLD))
        self.assertEqual(self.counts(NEW), (THIS_REPO_COUNT + 1, TOTAL_COUNT + 1))

    def test_history_left_under_a_new_name_is_gone_when_the_renamed_rule_has_none(self):
        """(d) A rule file deleted by hand leaves its history under its name;
        it must not outlive the creation of a new rule with that name."""
        self.forget_history(self.scope, OLD)
        self.history(self.scope, NEW)
        self.assertFalse(self.rule_exists(NEW))
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts(NEW), (0, 0))
        self.assertNotIn(self.key(self.scope, NEW), self.stats_keys())

    def test_history_left_under_a_new_name_gives_way_to_the_renamed_rules(self):
        """(e) The same, with a renamed rule that has history: the new name ends
        with exactly that history, the larger leftover included."""
        self.history(self.scope, NEW, this_repo=50, other_repo=0, total=50)
        before = self.stats()["rules"][self.key(self.scope, OLD)]
        proc = self.rename(OLD, NEW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.stats()["rules"][self.key(self.scope, NEW)], before)
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
