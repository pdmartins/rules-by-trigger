"""The setup notice printed by every admin subcommand until the machine has
its own `~/.claude/rules-by-trigger/config.json`, and `doctor --setup` / `doctor
--harden`, the only ways that file (and the recommended hardening) get
written."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()
SETTINGS_RELPATH = os.path.join(".claude", "settings.json")
HARDENING_ENTRIES = [
    "Read(**/.claude/rules-by-trigger/**)",
    "Edit(**/.claude/rules-by-trigger/**)",
    "Read(~/.claude/rules-by-trigger/**)",
    "Edit(~/.claude/rules-by-trigger/**)",
]
NOTICE_TEXT = HOOK.MESSAGES[HOOK.DEFAULT_LANGUAGE][HOOK.SETUP_NOTICE_KEY]


class SetupTestCase(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def settings_path(self):
        return os.path.join(self.home, SETTINGS_RELPATH)

    def write_settings(self, data):
        util.write_file(self.settings_path(), json.dumps(data))

    def settings(self):
        with open(self.settings_path(), encoding="utf-8") as handle:
            return json.load(handle)

    def config_path(self):
        return os.path.join(self.global_scope, "config.json")

    def config(self):
        with open(self.config_path(), encoding="utf-8") as handle:
            return json.load(handle)

    def doctor(self, *extra):
        return self.admin("doctor", "--root", self.proj, *extra)


class NoticeTest(SetupTestCase):

    def test_notice_is_the_first_stdout_line_without_a_config(self):
        proc = self.admin("list", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split("\n", 1)[0], NOTICE_TEXT)

    def test_notice_absent_once_the_machine_is_set_up(self):
        util.write_config(self.global_scope, {})
        proc = self.admin("list", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(NOTICE_TEXT, proc.stdout)

    def test_notice_absent_on_doctor(self):
        proc = self.doctor()
        self.assertNotIn(NOTICE_TEXT, proc.stdout)

    def test_show_without_config_prints_only_the_rule_document(self):
        """`show` feeds the documented show -> edit -> update round trip, so
        its stdout must open with the rule's own `---`, not with the notice."""
        util.write_rule(self.proj, "CONV_a.md", "src/**", "Validate the DTOs.")
        proc = self.admin("show", "--root", self.proj, "--rule", "CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(NOTICE_TEXT, proc.stdout)
        self.assertTrue(proc.stdout.startswith("---"), proc.stdout[:200])

    def test_status_json_without_config_still_parses(self):
        proc = self.admin("status", "--root", self.proj, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)  # would raise if the notice leaked in
        self.assertIn("scopes", report)

    def test_a_project_language_does_not_switch_the_notice(self):
        """The notice is the plugin's own config layer only — no scope dirs —
        so a project layer that arrived with a cloned repository cannot pick
        the words that ask the machine owner for consent."""
        util.write_config(self.scope, {"language": "pt-BR"})
        proc = self.admin("list", "--root", self.proj)
        self.assertEqual(proc.stdout.split("\n", 1)[0], NOTICE_TEXT)


class SetupAcceptDeclineTest(SetupTestCase):

    def test_setup_with_language_and_harden(self):
        proc = self.doctor("--setup", "--language", "pt-BR", "--harden")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.config(), {"language": "pt-BR"})
        self.assertEqual(self.settings()["permissions"]["deny"], HARDENING_ENTRIES)
        proc = self.admin("list", "--root", self.proj)
        self.assertNotIn(NOTICE_TEXT, proc.stdout)

    def test_setup_with_language_and_no_harden(self):
        proc = self.doctor("--setup", "--language", "en", "--no-harden")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.config(), {"language": "en"})
        self.assertFalse(os.path.isfile(self.settings_path()))

    def test_setup_decline(self):
        proc = self.doctor("--setup", "--decline")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.config(), {})
        self.assertFalse(os.path.isfile(self.settings_path()))
        proc = self.admin("list", "--root", self.proj)
        self.assertNotIn(NOTICE_TEXT, proc.stdout)

    def test_rerunning_setup_preserves_other_keys(self):
        util.write_config(self.global_scope, {"rule_size": {"max_chars": 1000}})
        proc = self.doctor("--setup", "--language", "en", "--no-harden")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.config(), {"rule_size": {"max_chars": 1000},
                                         "language": "en"})

    def test_unreadable_existing_config_makes_setup_fail_and_stay_untouched(self):
        util.write_config(self.global_scope, "{not json at all")
        with open(self.config_path(), encoding="utf-8") as handle:
            before = handle.read()
        proc = self.doctor("--setup", "--decline")
        self.assertNotEqual(proc.returncode, 0)
        with open(self.config_path(), encoding="utf-8") as handle:
            after = handle.read()
        self.assertEqual(before, after)


class FlagValidationTest(SetupTestCase):

    def test_setup_alone_fails(self):
        proc = self.doctor("--setup")
        self.assertNotEqual(proc.returncode, 0)

    def test_setup_with_language_but_no_harden_choice_fails(self):
        proc = self.doctor("--setup", "--language", "en")
        self.assertNotEqual(proc.returncode, 0)

    def test_decline_with_language_fails(self):
        proc = self.doctor("--decline", "--language", "en")
        self.assertNotEqual(proc.returncode, 0)

    def test_language_without_setup_fails(self):
        proc = self.doctor("--language", "en")
        self.assertNotEqual(proc.returncode, 0)

    def test_setup_with_fix_fails(self):
        proc = self.doctor("--setup", "--decline", "--fix")
        self.assertNotEqual(proc.returncode, 0)

    def test_harden_belongs_to_doctor_only(self):
        proc = self.admin("list", "--root", self.proj, "--harden")
        self.assertNotEqual(proc.returncode, 0)

    def test_invalid_language_fails(self):
        proc = self.doctor("--setup", "--harden", "--language", "en\nx")
        self.assertNotEqual(proc.returncode, 0)


class AbsoluteRootEquivalenceTest(SetupTestCase):

    def test_absolute_root_read_entry_covers_both_read_pairs(self):
        self.write_settings({"permissions": {
            "deny": ["Read(//**/.claude/rules-by-trigger/**)"]}})
        proc = self.doctor()
        self.assertIn("2 of 4 deny entries missing", proc.stdout)
        proc = self.doctor("--harden")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.settings()["permissions"]["deny"],
                         ["Read(//**/.claude/rules-by-trigger/**)",
                          "Edit(**/.claude/rules-by-trigger/**)",
                          "Edit(~/.claude/rules-by-trigger/**)"])


if __name__ == "__main__":
    unittest.main()
