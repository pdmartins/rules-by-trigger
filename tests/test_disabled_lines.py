"""The `Edit(<glob>)` lines of a project rule with `block: true` follow the
rule's state: `remove` takes them out, `update --enable` gives them back,
`block --sync` corrects a hand edit — each in a fixed write order, so a failed
run is finished by running it again (spec 0008, FR-005)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from disabledutil import BODY, DISABLED, NOT_ROOT, DisabledSandbox, line_for  # noqa: E402

PROD = "infra/prod/**"
OTHER_LINE = "Bash(rm:*)"


class RemoveLinesTest(DisabledSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule("BUSN_prod.md", PROD)
        self.write_settings([line_for(PROD), OTHER_LINE])

    def test_disabling_takes_its_lines_out_and_keeps_the_others(self):
        proc = self.remove("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])
        self.assertIn(DISABLED, self.read_rule("BUSN_prod.md"))

    def test_a_line_another_active_block_rule_uses_is_kept(self):
        self.block_rule("BUSN_other.md", PROD)
        self.block_rule("BUSN_own.md", "infra/own/**")
        self.write_settings([line_for(PROD), line_for("infra/own/**")])
        self.remove("BUSN_prod.md")
        self.assertEqual(self.deny_lines(), [line_for(PROD), line_for("infra/own/**")])
        self.remove("BUSN_own.md")
        self.assertEqual(self.deny_lines(), [line_for(PROD)])

    def test_a_line_only_a_disabled_rule_uses_is_not_kept_alive_by_it(self):
        self.block_rule("BUSN_other.md", PROD, DISABLED)
        self.remove("BUSN_prod.md")
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_a_line_already_removed_by_hand_is_not_an_error(self):
        self.write_settings([OTHER_LINE])
        proc = self.remove("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    def test_a_project_with_no_settings_file_gets_none_created(self):
        os.remove(os.path.join(self.proj, ".claude", "settings.json"))
        proc = self.remove("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(self.deny_lines())

    def test_the_message_names_the_settings_file(self):
        proc = self.remove("BUSN_prod.md")
        settings = os.path.join(self.proj, ".claude", "settings.json")
        self.assertIn(f"removed from {settings}", proc.stdout)

    def test_a_rule_without_block_touches_no_settings(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        self.write_settings([line_for("src/**")])
        self.remove("CONV_a.md")
        self.assertEqual(self.deny_lines(), [line_for("src/**")])

    def test_delete_takes_the_lines_out_too(self):
        proc = self.remove("BUSN_prod.md", "--delete")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    @NOT_ROOT
    def test_a_failed_settings_write_exits_non_zero_and_a_rerun_finishes(self):
        self.make_dot_claude_read_only()
        proc = self.remove("BUSN_prod.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(DISABLED, self.read_rule("BUSN_prod.md"),
                      "the rule file goes first, so it is already disabled")
        self.assertEqual(self.deny_lines(), [line_for(PROD), OTHER_LINE])
        self.make_dot_claude_writable()
        proc = self.remove("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])
        self.assertEqual(self.read_rule("BUSN_prod.md").count(DISABLED), 1)

    @NOT_ROOT
    def test_a_failed_settings_write_on_delete_keeps_the_rule_and_a_rerun_deletes(self):
        self.make_dot_claude_read_only()
        proc = self.remove("BUSN_prod.md", "--delete")
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.rule_exists("BUSN_prod.md"),
                        "the file is the last thing to go, so the globs survive")
        self.make_dot_claude_writable()
        proc = self.remove("BUSN_prod.md", "--delete")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists("BUSN_prod.md"))
        self.assertEqual(self.deny_lines(), [OTHER_LINE])


class EnableLinesTest(DisabledSandbox):
    def setUp(self):
        super().setUp()
        self.block_rule("BUSN_prod.md", PROD)
        self.write_settings([OTHER_LINE])
        self.remove("BUSN_prod.md")

    def test_enable_drops_the_flag_and_restores_the_lines(self):
        proc = self.enable("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.read_rule("BUSN_prod.md")
        self.assertNotIn("enabled", text)
        self.assertIn(BODY, text)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(PROD)])

    def test_a_rule_enabled_again_is_injected_again(self):
        self.enable("BUSN_prod.md")
        self.assertIn(BODY, self.inject("infra/prod/x.tf"))

    def test_enable_on_an_active_rule_is_a_no_op_that_still_ensures_the_lines(self):
        self.enable("BUSN_prod.md")
        text = self.read_rule("BUSN_prod.md")
        self.write_settings([OTHER_LINE])  # a hand edit took the line out
        proc = self.enable("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(PROD)])
        self.assertEqual(self.read_rule("BUSN_prod.md"), text)

    def test_enable_after_the_line_was_removed_by_hand_gives_it_back_without_error(self):
        self.write_settings([])
        proc = self.enable("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [line_for(PROD)])

    def test_enable_creates_the_settings_file_when_there_is_none(self):
        os.remove(os.path.join(self.proj, ".claude", "settings.json"))
        proc = self.enable("BUSN_prod.md")
        self.assertIn(f"written to {os.path.join(self.proj, '.claude', 'settings.json')}",
                      proc.stdout)
        self.assertEqual(self.deny_lines(), [line_for(PROD)])

    def test_a_line_already_there_is_not_duplicated(self):
        self.write_settings([line_for(PROD)])
        self.enable("BUSN_prod.md")
        self.assertEqual(self.deny_lines(), [line_for(PROD)])

    def test_enable_never_writes_lines_for_a_rule_without_block(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[DISABLED])
        self.enable("CONV_a.md")
        self.assertEqual(self.deny_lines(), [OTHER_LINE])

    @NOT_ROOT
    def test_a_failed_settings_write_exits_non_zero_and_a_rerun_finishes(self):
        self.make_dot_claude_read_only()
        proc = self.enable("BUSN_prod.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("enabled", self.read_rule("BUSN_prod.md"))
        self.make_dot_claude_writable()
        proc = self.enable("BUSN_prod.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(PROD)])

    def test_enable_with_no_body_refuses_the_flags_that_change_the_rule(self):
        proc = self.admin("update", "--root", self.proj, "--rule", "BUSN_prod.md",
                          "--enable", "--glob", "other/**")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(DISABLED, self.read_rule("BUSN_prod.md"))

    def test_update_of_a_disabled_rule_keeps_it_disabled_and_writes_no_lines(self):
        proc = self.admin("update", "--root", self.proj, "--rule", "BUSN_prod.md",
                          "--glob", "infra/new/**", stdin="NEW BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.read_rule("BUSN_prod.md")
        self.assertIn(DISABLED, text)
        self.assertIn("infra/new/**", text)
        self.assertIn("block: true", text)
        self.assertEqual(self.deny_lines(), [OTHER_LINE])
        self.enable("BUSN_prod.md")
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for("infra/new/**")],
                         "the lines come back with the globs the rule has now")

    def test_update_with_a_body_and_enable_does_both(self):
        proc = self.admin("update", "--root", self.proj, "--rule", "BUSN_prod.md",
                          "--enable", stdin="NEW BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.read_rule("BUSN_prod.md")
        self.assertNotIn("enabled", text)
        self.assertIn("NEW BODY", text)
        self.assertEqual(self.deny_lines(), [OTHER_LINE, line_for(PROD)])

    def test_show_edit_update_round_trip_of_a_disabled_rule_keeps_it_disabled(self):
        shown = self.admin("show", "--root", self.proj, "--rule", "BUSN_prod.md")
        self.assertIn(DISABLED, shown.stdout)
        proc = self.admin("update", "--root", self.proj, "--rule", "BUSN_prod.md",
                          stdin=shown.stdout.replace(BODY, "EDITED BODY"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.read_rule("BUSN_prod.md")
        self.assertEqual(text.count(DISABLED), 1)
        self.assertIn("EDITED BODY", text)
        self.assertEqual(self.deny_lines(), [OTHER_LINE], "no lines written")

    def test_update_refuses_enabled_true_on_a_disabled_rule(self):
        before = self.read_rule("BUSN_prod.md")
        stdin = f"---\nglob: {PROD}\nenabled: true\n---\nNEW BODY\n"
        proc = self.admin("update", "--root", self.proj, "--rule",
                          "BUSN_prod.md", stdin=stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("remove", proc.stderr)
        self.assertIn("--enable", proc.stderr)
        self.assertEqual(self.read_rule("BUSN_prod.md"), before)

    def test_update_refuses_enabled_false_on_an_active_rule(self):
        self.enable("BUSN_prod.md")
        before = self.read_rule("BUSN_prod.md")
        stdin = f"---\nglob: {PROD}\n{DISABLED}\n---\nNEW BODY\n"
        proc = self.admin("update", "--root", self.proj, "--rule",
                          "BUSN_prod.md", stdin=stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("remove", proc.stderr)
        self.assertEqual(self.read_rule("BUSN_prod.md"), before)

    def test_update_accepts_enabled_true_on_an_active_rule_and_keeps_it_active(self):
        self.enable("BUSN_prod.md")
        stdin = f"---\nglob: {PROD}\nenabled: true\n---\nNEW BODY\n"
        proc = self.admin("update", "--root", self.proj, "--rule",
                          "BUSN_prod.md", stdin=stdin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("NEW BODY", self.inject("infra/prod/x.tf"))

    def test_update_enable_with_enabled_false_on_stdin_is_a_contradiction(self):
        stdin = f"---\nglob: {PROD}\n{DISABLED}\n---\nNEW BODY\n"
        proc = self.admin("update", "--root", self.proj, "--rule", "BUSN_prod.md",
                          "--enable", stdin=stdin)
        self.assertNotEqual(proc.returncode, 0)

    def test_add_refuses_an_enabled_key_submitted_on_stdin_too(self):
        stdin = f"---\nglob: src/**\n{DISABLED}\n---\nBODY\n"
        proc = self.admin("add", "--root", self.proj, "--type", "OTHR",
                          "--glob", "src/**", stdin=stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("new rule", proc.stderr)


class BlockSyncTest(DisabledSandbox):
    def test_sync_removes_the_lines_of_globs_only_disabled_rules_use(self):
        self.block_rule("BUSN_a.md", "infra/a/**", DISABLED)  # hand-edited off
        self.block_rule("BUSN_b.md", "infra/b/**")
        self.block_rule("BUSN_c.md", "infra/shared/**", DISABLED)
        self.block_rule("BUSN_d.md", "infra/shared/**")
        self.write_settings([line_for("infra/a/**"), line_for("infra/shared/**"),
                             OTHER_LINE])
        proc = self.admin("block", "--root", self.proj, "--sync")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [line_for("infra/shared/**"), OTHER_LINE,
                                             line_for("infra/b/**")])

    def test_sync_removes_even_when_no_active_block_rule_is_left(self):
        self.block_rule("BUSN_a.md", "infra/a/**", DISABLED)
        self.write_settings([line_for("infra/a/**")])
        proc = self.admin("block", "--root", self.proj, "--sync")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.deny_lines(), [])

    def test_sync_says_what_it_removed_not_that_everything_was_there(self):
        self.block_rule("BUSN_a.md", "infra/a/**", DISABLED)
        self.write_settings([line_for("infra/a/**")])
        proc = self.admin("block", "--root", self.proj, "--sync")
        self.assertIn("removed", proc.stdout)
        self.assertNotIn("already has every", proc.stdout)

    def test_sync_with_only_stale_rules_and_no_settings_file_changes_nothing(self):
        self.block_rule("BUSN_a.md", "infra/a/**", DISABLED)
        proc = self.admin("block", "--root", self.proj, "--sync")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to change", proc.stdout)
        self.assertNotIn("already has every", proc.stdout)

    def test_sync_never_writes_lines_for_a_disabled_rule(self):
        self.block_rule("BUSN_a.md", "infra/a/**", DISABLED)
        self.admin("block", "--root", self.proj, "--sync")
        self.assertIsNone(self.deny_lines())

    def test_list_does_not_offer_a_disabled_rule_for_sync(self):
        self.block_rule("BUSN_a.md", "infra/a/**", DISABLED)
        proc = self.admin("block", "--root", self.proj, "--list")
        self.assertNotIn("NOT synced", proc.stdout)


if __name__ == "__main__":
    unittest.main()
