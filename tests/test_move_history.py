"""`move` and the history, the settings lines and the interrupted runs that go
with it (spec 0008, FR-004 and FR-005): a rule keeps what it fired, the
`Edit(<glob>)` lines of its globs follow it, and a run that stopped half way is
finished by running the same command again."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from operationsutil import (BODY, DISABLED, KIND_GLOBAL, NOT_ROOT,  # noqa: E402
                            OperationsSandbox, THIS_REPO_COUNT, TOTAL_COUNT,
                            line_for)

NAME = "BUSN_prod.md"
PROJECT_GLOB = "infra/prod/**"
GLOBAL_GLOB = "**/infra/prod/**"
OTHER_LINE = "Bash(rm:*)"


class MoveHistoryTest(OperationsSandbox):
    def test_project_to_global_shows_the_same_counters_in_status(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.history(self.scope, "CONV_a.md")
        proc = self.move_to_global("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts("CONV_a.md", KIND_GLOBAL),
                         (THIS_REPO_COUNT, TOTAL_COUNT))
        rules = self.stats()["rules"]
        self.assertNotIn(self.key(self.scope, "CONV_a.md"), rules)
        self.assertEqual(len(rules[self.key(self.global_scope, "CONV_a.md")]["repos"]), 2)

    def test_global_to_project_keeps_only_this_repo_and_the_total_follows(self):
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.global_scope, "CONV_a.md")
        proc = self.move_to_project("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts("CONV_a.md"), (THIS_REPO_COUNT, THIS_REPO_COUNT))
        rule = self.stats()["rules"][self.key(self.scope, "CONV_a.md")]
        self.assertEqual(list(rule["repos"]), [os.path.realpath(self.proj)])
        self.assertNotIn(self.key(self.global_scope, "CONV_a.md"), self.stats()["rules"])

    def test_global_to_project_of_a_rule_this_repo_never_fired_starts_at_zero(self):
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.global_scope, "CONV_a.md")
        stats = self.stats()
        del stats["rules"][self.key(self.global_scope, "CONV_a.md")]["repos"][
            os.path.realpath(self.proj)]
        self.plant(stats)
        self.assertEqual(self.move_to_project("CONV_a.md").returncode, 0)
        self.assertEqual(self.counts("CONV_a.md"), (0, 0))

    def test_history_left_under_a_new_global_name_is_gone_when_the_moved_rule_has_none(self):
        """(d)"""
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.history(self.global_scope, "CONV_a.md")
        self.assertFalse(self.global_exists("CONV_a.md"))
        proc = self.move_to_global("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts("CONV_a.md", KIND_GLOBAL), (0, 0))
        self.assertNotIn(self.key(self.global_scope, "CONV_a.md"), self.stats_keys())

    def test_history_left_under_a_new_project_name_is_gone_when_the_moved_rule_has_none(self):
        """(d), the other direction"""
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.scope, "CONV_a.md")
        self.assertFalse(self.rule_exists("CONV_a.md"))
        proc = self.move_to_project("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts("CONV_a.md"), (0, 0))
        self.assertNotIn(self.key(self.scope, "CONV_a.md"), self.stats_keys())

    def test_history_left_under_a_new_name_gives_way_to_the_moved_rules(self):
        """(e) Both directions: the new name ends with the moved rule's history,
        a larger leftover included."""
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.history(self.scope, "CONV_a.md")
        self.history(self.global_scope, "CONV_a.md", this_repo=50, other_repo=0, total=50)
        before = self.stats()["rules"][self.key(self.scope, "CONV_a.md")]
        self.assertEqual(self.move_to_global("CONV_a.md").returncode, 0)
        self.assertEqual(self.stats()["rules"][self.key(self.global_scope, "CONV_a.md")],
                         before)
        self.global_rule("CONV_b.md", "src/**")
        self.history(self.global_scope, "CONV_b.md")
        self.history(self.scope, "CONV_b.md", this_repo=50, other_repo=0, total=50)
        self.assertEqual(self.move_to_project("CONV_b.md").returncode, 0)
        self.assertEqual(self.counts("CONV_b.md"), (THIS_REPO_COUNT, THIS_REPO_COUNT))

    def test_a_taken_name_is_refused_and_nothing_is_touched(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "PROJECT VERSION")
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.scope, "CONV_a.md")
        self.history(self.global_scope, "CONV_a.md", this_repo=1, other_repo=1, total=2)
        self.write_settings([OTHER_LINE])
        before = self.snapshot()
        proc = self.move_to_global("CONV_a.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_force_discards_the_history_of_the_rule_it_overwrites(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "PROJECT VERSION")
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.scope, "CONV_a.md", this_repo=2, other_repo=0, total=2)
        self.history(self.global_scope, "CONV_a.md")
        proc = self.move_to_global("CONV_a.md", "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts("CONV_a.md", KIND_GLOBAL), (2, 2))

    def test_force_over_a_rule_with_history_when_the_moved_one_has_none(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "PROJECT VERSION")
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.global_scope, "CONV_a.md")
        self.assertEqual(self.move_to_global("CONV_a.md", "--force").returncode, 0)
        self.assertEqual(self.counts("CONV_a.md", KIND_GLOBAL), (0, 0))

    def test_between_two_projects_the_whole_history_moves(self):
        other = os.path.join(self.tmp.name, "other-project")
        os.makedirs(other)
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.history(self.scope, "CONV_a.md")
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_a.md",
                          "--to-root", other)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rule = self.stats()["rules"][self.key(util.scope_dir(other), "CONV_a.md")]
        self.assertEqual((rule["total"], len(rule["repos"])), (TOTAL_COUNT, 2))


class MoveLinesTest(OperationsSandbox):
    def test_project_to_global_takes_the_lines_out_of_the_project(self):
        self.block_rule(NAME, PROJECT_GLOB)
        self.write_settings([line_for(PROJECT_GLOB), OTHER_LINE])
        proc = self.move_to_global(NAME)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])
        self.assertIn(f"glob: {GLOBAL_GLOB}", self.read_global_rule(NAME))

    def test_a_line_another_active_block_rule_uses_stays(self):
        self.block_rule(NAME, PROJECT_GLOB)
        self.block_rule("BUSN_twin.md", PROJECT_GLOB)
        self.write_settings([line_for(PROJECT_GLOB)])
        self.move_to_global(NAME)
        self.assertEqual(self.deny_lines(), [line_for(PROJECT_GLOB)])

    def test_global_to_project_puts_the_lines_in_the_project(self):
        self.global_rule(NAME, GLOBAL_GLOB, "block: true")
        self.write_settings([OTHER_LINE])
        proc = self.move_to_project(NAME)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(GLOBAL_GLOB)])
        self.assertEqual(self.move_to_global(NAME).returncode, 0)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_a_project_without_a_settings_file_gets_one(self):
        self.global_rule(NAME, GLOBAL_GLOB, "block: true")
        self.assertEqual(self.move_to_project(NAME).returncode, 0)
        self.assertEqual(self.deny_lines(), [line_for(GLOBAL_GLOB)])

    def test_a_rule_without_block_touches_no_settings(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.write_settings([OTHER_LINE])
        self.move_to_global("CONV_a.md")
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_force_removes_the_lines_of_the_project_rule_it_overwrites(self):
        self.global_rule(NAME, "**/docs/**")
        self.block_rule(NAME, "old/**")
        self.write_settings([line_for("old/**"), OTHER_LINE])
        # the rule moved is the global one, replacing the project's
        proc = self.move_to_project(NAME, "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_a_disabled_rule_stays_disabled_with_no_lines_and_enable_adds_them(self):
        self.block_rule(NAME, PROJECT_GLOB)
        self.write_settings([line_for(PROJECT_GLOB)])
        self.remove(NAME)
        self.assertEqual(self.deny_lines(), [])
        self.assertEqual(self.move_to_global(NAME).returncode, 0)
        self.assertIn(DISABLED, self.read_global_rule(NAME))
        self.assertEqual(self.move_to_project(NAME).returncode, 0)
        self.assertIn(DISABLED, self.read_rule(NAME))
        self.assertEqual(self.deny_lines(), [])
        self.assertEqual(self.enable(NAME).returncode, 0)
        self.assertEqual(self.deny_lines(), [line_for(GLOBAL_GLOB)])


@NOT_ROOT
class InterruptedMoveTest(OperationsSandbox):
    def test_project_to_global_stopped_at_the_settings_is_finished_by_a_rerun(self):
        self.block_rule(NAME, PROJECT_GLOB)
        self.write_settings([line_for(PROJECT_GLOB)])
        self.history(self.scope, NAME)
        self.make_dot_claude_read_only()
        proc = self.move_to_global(NAME)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.global_exists(NAME), "the destination goes first")
        self.assertTrue(self.rule_exists(NAME), "the source goes last")
        self.assertEqual(self.deny_lines(), [line_for(PROJECT_GLOB)])
        self.make_dot_claude_writable()
        proc = self.move_to_global(NAME)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists(NAME))
        self.assertEqual(self.deny_lines(), [])
        self.assertEqual(self.counts(NAME, KIND_GLOBAL), (THIS_REPO_COUNT, TOTAL_COUNT))

    def leave_the_project_copy(self, name):
        """What an earlier run that wrote the project file left behind: the
        project copy, the global rule still there, its history back under the
        global key. The next run goes on from there and reaches the history
        step, which a run that finds nothing at the destination no longer does
        before it has cleared that name's history."""
        self.assertEqual(self.move_to_project(name).returncode, 0)
        util.write_file(os.path.join(self.global_scope, name), self.read_rule(name))
        stats = self.stats()
        del stats["rules"][self.key(self.scope, name)]
        self.plant(stats)
        self.history(self.global_scope, name)

    def test_global_to_project_stopped_at_the_history_is_finished_by_a_rerun(self):
        self.global_rule(NAME, GLOBAL_GLOB, "block: true")
        self.history(self.global_scope, NAME)
        self.leave_the_project_copy(NAME)
        lines = self.deny_lines()
        self.break_stats()
        proc = self.move_to_project(NAME)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.global_exists(NAME))
        self.assertEqual(self.deny_lines(), lines)
        self.repair_stats()
        proc = self.move_to_project(NAME)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.global_exists(NAME))
        self.assertEqual(self.counts(NAME), (THIS_REPO_COUNT, THIS_REPO_COUNT))

    def test_an_unusable_usage_file_stops_a_move_before_anything_is_written(self):
        self.global_rule(NAME, GLOBAL_GLOB, "block: true")
        self.history(self.global_scope, NAME)
        self.break_stats()
        before = self.snapshot()
        proc = self.move_to_project(NAME)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.rule_exists(NAME))
        self.assertEqual(self.snapshot(), before)
        self.repair_stats()
        self.assertEqual(self.move_to_project(NAME).returncode, 0)
        self.assertEqual(self.counts(NAME), (THIS_REPO_COUNT, THIS_REPO_COUNT))

    def test_a_rerun_after_the_history_moved_and_the_rule_fired_keeps_the_moved_history(self):
        """(c, first window) The history step succeeded, removing the global
        file failed, and the rule fired once before the re-run: both keys are
        one firing up, and the project rule ends with what moved, plus one."""
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.global_scope, "CONV_a.md")
        self.assertEqual(self.move_to_project("CONV_a.md").returncode, 0)
        util.write_file(os.path.join(self.global_scope, "CONV_a.md"),
                        self.read_rule("CONV_a.md"))
        self.add_firing(self.global_scope, "CONV_a.md")
        self.add_firing(self.scope, "CONV_a.md")
        proc = self.move_to_project("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.global_exists("CONV_a.md"))
        self.assertEqual(self.counts("CONV_a.md"),
                         (THIS_REPO_COUNT + 1, THIS_REPO_COUNT + 1))

    def test_a_rerun_after_a_failed_history_step_and_a_firing_keeps_the_global_history(self):
        """(c, second window) The history step failed and the rule fired once
        before the re-run: the global rule kept counting, the project one
        started at one, and the project rule ends with this repo's count of
        the global one."""
        self.global_rule("CONV_a.md", "src/**")
        self.history(self.global_scope, "CONV_a.md")
        self.leave_the_project_copy("CONV_a.md")
        self.break_stats()
        self.assertNotEqual(self.move_to_project("CONV_a.md").returncode, 0)
        self.repair_stats()
        self.add_firing(self.global_scope, "CONV_a.md")
        self.add_firing(self.scope, "CONV_a.md")
        proc = self.move_to_project("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.global_exists("CONV_a.md"))
        self.assertEqual(self.counts("CONV_a.md"),
                         (THIS_REPO_COUNT + 1, THIS_REPO_COUNT + 1))

    def test_a_forced_run_finished_by_a_rerun_keeps_the_history_that_moved(self):
        self.block_rule(NAME, PROJECT_GLOB)
        self.global_rule(NAME, "**/docs/**")
        self.write_settings([line_for(PROJECT_GLOB)])
        self.history(self.scope, NAME)
        self.history(self.global_scope, NAME, this_repo=1, other_repo=1, total=2)
        self.make_dot_claude_read_only()
        self.assertNotEqual(self.move_to_global(NAME, "--force").returncode, 0)
        self.make_dot_claude_writable()
        proc = self.move_to_global(NAME, "--force")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.counts(NAME, KIND_GLOBAL), (THIS_REPO_COUNT, TOTAL_COUNT))

    def test_a_destination_with_other_content_is_still_a_collision_on_a_rerun(self):
        self.block_rule(NAME, PROJECT_GLOB)
        self.write_settings([line_for(PROJECT_GLOB)])
        self.make_dot_claude_read_only()
        self.assertNotEqual(self.move_to_global(NAME).returncode, 0)
        self.make_dot_claude_writable()
        util.write_file(os.path.join(self.global_scope, NAME),
                        self.read_global_rule(NAME) + "\nEDITED SINCE\n")
        proc = self.move_to_global(NAME)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("already exists", proc.stderr)
        self.assertTrue(self.rule_exists(NAME))
        self.assertEqual(self.deny_lines(), [line_for(PROJECT_GLOB)])


if __name__ == "__main__":
    unittest.main()
