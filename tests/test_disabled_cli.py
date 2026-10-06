"""`remove` disables by default and deletes with `--delete`; `update --enable`
switches a rule back on; `add` refuses a twin of a disabled rule (spec 0008,
FR-006 and FR-003, CLI part)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from disabledutil import (BODY, DISABLED, SETTINGS_RELPATH, SKILL, SKILL_CALL,  # noqa: E402
                          DisabledSandbox)

SHOWS_DISABLED = "DISABLED"


class RemoveDisablesTest(DisabledSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)

    def test_remove_without_delete_writes_enabled_false_and_keeps_the_file(self):
        before = self.read_rule("CONV_a.md")
        proc = self.remove("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after = self.read_rule("CONV_a.md")
        self.assertIn(DISABLED, after)
        self.assertEqual(after.replace(f"{DISABLED}\n", "", 1), before,
                         "only the state changed: the body and the rest stay")

    def test_remove_by_glob_disables_too(self):
        proc = self.admin("remove", "--root", self.proj, "--glob", "src/**")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(DISABLED, self.read_rule("CONV_a.md"))

    def test_the_disabled_rule_is_not_injected_any_more(self):
        self.assertIn(BODY, self.inject("src/a.py"))
        self.remove("CONV_a.md")
        self.assertIsNone(self.inject("src/a.py", session="s2"))

    def test_running_it_again_is_not_an_error_and_writes_one_line(self):
        self.remove("CONV_a.md")
        proc = self.remove("CONV_a.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_rule("CONV_a.md").count(DISABLED), 1)

    def test_the_disabled_rule_stays_in_list_and_status_marked_as_disabled(self):
        self.fire("src/a.py")
        before = self.usage_of(self.status_json(), "CONV_a.md")
        self.assertEqual(before["injections"], 1)
        self.remove("CONV_a.md")
        listing = self.admin("list", "--root", self.proj)
        self.assertIn("CONV_a.md", listing.stdout)
        self.assertIn(SHOWS_DISABLED, listing.stdout)
        rule = [r for scope in self.status_json()["scopes"]
                for r in scope["rules"] if r["name"] == "CONV_a.md"][0]
        self.assertFalse(rule["enabled"])
        self.assertEqual(rule["usage"], before, "the history is kept")
        text = self.admin("status", "--root", self.proj,
                          env={"CLAUDE_PROJECT_DIR": self.proj}).stdout
        self.assertIn(SHOWS_DISABLED, text)

    def test_a_file_that_is_not_a_rule_cannot_be_disabled(self):
        util.write_file(os.path.join(self.scope, "notes.md"), "just notes\n")
        proc = self.remove("notes.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.read_rule("notes.md"), "just notes\n")

    def test_a_global_rule_is_disabled_with_no_settings_written(self):
        util.write_rule(self.home, "BUSN_g.md", "**/*.py", BODY,
                        extra_frontmatter=["block: true"])
        proc = self.admin("remove", "--global", "--rule", "BUSN_g.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(self.global_scope, "BUSN_g.md"),
                  encoding="utf-8") as handle:
            self.assertIn(DISABLED, handle.read())
        self.assertFalse(os.path.exists(os.path.join(self.home, SETTINGS_RELPATH)))

    def test_a_file_that_is_not_a_rule_cannot_be_enabled_either(self):
        util.write_file(os.path.join(self.scope, "notes.md"), "just notes\n")
        proc = self.enable("notes.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("ok: enabled", proc.stdout)
        self.assertIn("not a rule", proc.stderr)

    def test_add_with_an_enabled_line_says_a_new_rule_cannot_carry_one(self):
        stdin = f"---\nglob: src/**\n{DISABLED}\n---\nBODY\n"
        proc = self.admin("add", "--root", self.proj, "--type", "OTHR",
                          "--glob", "src/**", stdin=stdin)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("new rule", proc.stderr)
        self.assertIn("remove", proc.stderr)
        self.assertNotIn("would change", proc.stderr)

    def test_a_crlf_rule_keeps_its_line_endings_through_disable_and_enable(self):
        text = f"---\r\nglob: src/**\r\n---\r\n{BODY}\r\nSecond line.\r\n"
        path = os.path.join(self.scope, "CRLF_a.md")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

        def raw():
            with open(path, "rb") as handle:
                return handle.read().decode("utf-8")

        self.assertEqual(self.remove("CRLF_a.md").returncode, 0)
        self.assertEqual(raw(), text.replace("---\r\n" + BODY,
                                             f"{DISABLED}\r\n---\r\n" + BODY))
        self.assertEqual(self.enable("CRLF_a.md").returncode, 0)
        self.assertEqual(raw(), text)

    def test_the_delete_flag_is_the_only_way_to_delete(self):
        self.remove("CONV_a.md")
        self.assertTrue(self.rule_exists("CONV_a.md"))
        self.assertEqual(self.remove("CONV_a.md", "--delete").returncode, 0)
        self.assertFalse(self.rule_exists("CONV_a.md"))


class RemoveDeleteTest(DisabledSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY)
        util.write_rule(self.proj, "CONV_b.md", "lib/**", BODY)

    def test_delete_removes_the_file_the_status_entry_and_the_history(self):
        self.fire("src/a.py")
        self.fire("src/a.py")
        key = self.key(self.scope, "CONV_a.md")
        self.assertIn(key, self.stats()["rules"])
        proc = self.remove("CONV_a.md", "--delete")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists("CONV_a.md"))
        names = [r["name"] for scope in self.status_json()["scopes"]
                 for r in scope["rules"]]
        self.assertNotIn("CONV_a.md", names)
        self.assertNotIn(key, self.stats()["rules"])

    def test_a_failed_stats_drop_exits_non_zero_with_the_file_in_place(self):
        self.fire("src/a.py")
        lock = self.stats_file() + ".lock"
        if os.path.isfile(lock):
            os.remove(lock)
        os.makedirs(lock)  # the usage file can no longer be locked
        proc = self.remove("CONV_a.md", "--delete")
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.rule_exists("CONV_a.md"))
        self.assertIn(self.key(self.scope, "CONV_a.md"), self.stats()["rules"])
        os.rmdir(lock)
        proc = self.remove("CONV_a.md", "--delete")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.rule_exists("CONV_a.md"))
        self.assertNotIn(self.key(self.scope, "CONV_a.md"), self.stats()["rules"])


class AddDuplicateTest(DisabledSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_old.md", ["src/**", "lib/**"], BODY,
                        extra_frontmatter=[DISABLED])

    def add(self, *flags, stdin=BODY):
        return self.admin("add", "--root", self.proj, "--type", "OTHR", *flags,
                          stdin=stdin)

    def test_the_same_glob_as_a_disabled_rule_is_refused_and_names_it(self):
        proc = self.add("--glob", "src/**", "--rule", "OTHR_new.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONV_old.md", proc.stderr)
        self.assertFalse(self.rule_exists("OTHR_new.md"))

    def test_one_glob_in_common_is_enough(self):
        proc = self.add("--glob", "docs/**", "--glob", "lib/**",
                        "--rule", "OTHR_new.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONV_old.md", proc.stderr)

    def test_allow_duplicate_creates_it(self):
        proc = self.add("--glob", "src/**", "--rule", "OTHR_new.md",
                        "--allow-duplicate")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.rule_exists("OTHR_new.md"))

    def test_force_does_not_bypass_it(self):
        util.write_rule(self.proj, "OTHR_new.md", "docs/**", BODY)
        proc = self.add("--glob", "src/**", "--rule", "OTHR_new.md", "--force")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONV_old.md", proc.stderr)

    def test_force_over_the_disabled_rule_itself_is_not_a_duplicate_of_it(self):
        proc = self.admin("add", "--root", self.proj, "--type", "CONV",
                          "--glob", "src/**", "--rule", "CONV_old.md", "--force",
                          stdin=BODY)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_glob_identity_is_by_text(self):
        proc = self.add("--glob", "src/*", "--rule", "OTHR_new.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_an_active_rule_with_the_same_glob_is_not_a_duplicate(self):
        util.write_rule(self.proj, "CONV_live.md", "app/**", BODY)
        proc = self.add("--glob", "app/**", "--rule", "OTHR_new.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_same_skill_trigger_is_refused_too(self):
        util.write_file(os.path.join(self.scope, "CONV_skill.md"),
                        f"---\ncall: {SKILL_CALL}\n{DISABLED}\n---\n{BODY}\n")
        proc = self.add("--call", f" Skill ( skill = {SKILL} ) ",
                        "--rule", "OTHR_skill.md")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONV_skill.md", proc.stderr)
        proc = self.add("--call", SKILL_CALL, "--rule", "OTHR_skill.md",
                        "--allow-duplicate")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_a_disabled_rule_of_another_scope_is_not_checked(self):
        util.write_rule(self.home, "CONV_global.md", "app/**", BODY,
                        extra_frontmatter=[DISABLED])
        proc = self.add("--glob", "app/**", "--rule", "OTHR_new.md")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.admin("add", "--global", "--type", "OTHR", "--glob", "app/**",
                          "--rule", "OTHR_g.md", stdin=BODY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("CONV_global.md", proc.stderr)

    def test_the_first_add_into_a_missing_scope_is_silent(self):
        fresh = os.path.join(self.tmp.name, "fresh")
        os.makedirs(fresh)
        for flags, folder in ((["--root", fresh], util.scope_dir(fresh)),
                              (["--global"], self.global_scope)):
            self.assertFalse(os.path.exists(folder))
            proc = self.admin("add", *flags, "--type", "OTHR", "--glob", "src/**",
                              "--rule", "OTHR_first.md", stdin=BODY)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stderr, "")
            self.assertTrue(os.path.isfile(os.path.join(folder, "OTHR_first.md")))

    def test_the_flags_belong_to_their_own_command(self):
        for command, extra in (("add", ["--delete", "--glob", "src/**"]),
                               ("update", ["--allow-duplicate"]),
                               ("remove", ["--enable"]), ("list", ["--enable"])):
            proc = self.admin(command, "--root", self.proj, "--rule", "x.md", *extra)
            self.assertNotEqual(proc.returncode, 0, command)
            self.assertIn("belongs to", proc.stderr)


if __name__ == "__main__":
    unittest.main()
