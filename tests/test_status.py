"""`status`: one read-only call that shows how much each rule is used. The
table's exact bytes are in test_status_table.py and the improvement candidates
in test_status_candidates.py; here, what the command is and is not."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402

REPEAT_UNIT_LINES = ("repeat distance", "repeat override", "measured in")
ENVIRONMENT_LINES = ("python:", "hook:", "rules-by-trigger 0.")


class StatusTest(statsutil.RepoSandbox):
    PROJECT_SUBDIRS = ("src/api",)

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})  # setup is done

    def status(self, *extra):
        proc = self.admin("status", "--root", self.proj, *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_a_project_with_no_rules_says_so_in_one_line(self):
        self.assertEqual(self.status().stdout, "No rules.\n")

    def test_lists_the_global_and_the_project_rules(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        util.write_rule(self.home, "ARCH_global.md", "**/docs/**", "GLOBAL RULE")
        lines = self.status().stdout.splitlines()
        by_name = {line.split()[0]: line for line in lines[2:]}
        self.assertIn("project", by_name["CONV_api.md"])
        self.assertIn("global", by_name["ARCH_global.md"])

    def test_global_only_lists_the_global_rules(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        util.write_rule(self.home, "ARCH_global.md", "**/docs/**", "GLOBAL RULE")
        proc = self.admin("status", "--global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ARCH_global.md", proc.stdout)
        self.assertNotIn("CONV_api.md", proc.stdout)

    def test_the_validator_findings_are_validates_to_print_not_status(self):
        util.write_rule(self.proj, "CONV_dead.md", [], "NEVER FIRES")
        out = self.status().stdout
        self.assertIn("CONV_dead.md", out)
        self.assertNotIn("ERROR", out)
        self.assertNotIn("no glob and no call", out)

    def test_json_has_the_repo_and_the_rules_and_nothing_of_the_old_report(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        report = json.loads(self.status("--json").stdout)
        self.assertEqual(set(report), {"repo", "rules"})
        self.assertEqual(report["repo"], os.path.realpath(self.proj))
        self.assertEqual(
            report["rules"],
            [{"name": "CONV_api.md", "scope": "project", "state": "active",
              "this_repo": 0, "total": 0, "candidate": "prune",
              "reason": "never fired in this repo"}])

    def test_json_covering_only_comes_with_path(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        report = json.loads(self.status("--json", "--path", "src/api/x.py").stdout)
        self.assertEqual(report["covering"],
                         [{"name": "CONV_api.md", "scope": "project"}])

    def test_json_belongs_to_status_only(self):
        proc = self.admin("list", "--root", self.proj, "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--json", proc.stderr)

    def test_a_command_other_than_status_still_needs_root_or_global(self):
        proc = self.admin("list")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--root", proc.stderr)

    def test_without_root_it_starts_from_the_project_dir(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        proc = self.admin("status", "--json", env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["repo"], os.path.realpath(self.proj))
        self.assertEqual([rule["name"] for rule in report["rules"]], ["CONV_api.md"])

    def test_without_root_or_project_dir_it_starts_from_the_current_folder(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        proc = self.admin("status", "--json", cwd=self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["repo"], os.path.realpath(self.proj))

    def test_a_root_that_does_not_exist_is_an_error_on_stderr_only(self):
        for extra in ((), ("--json",)):
            proc = self.admin("status", "--root", os.path.join(self.tmp.name, "nope"),
                              *extra)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")
            self.assertIn("does not exist", proc.stderr)

    def test_two_runs_in_a_row_print_the_same_bytes(self):
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        util.write_rule(self.home, "ARCH_global.md", "**/docs/**", "GLOBAL RULE")
        for extra in ((), ("--json",), ("--path", "src/api/x.py")):
            self.assertEqual(self.status(*extra).stdout, self.status(*extra).stdout)

    def test_the_environment_and_the_repeat_distance_are_no_longer_shown(self):
        util.write_state(self.home, "s", json.dumps(
            {"calls": 3, "injected_rules": {"k": [1, 45000, 0]}}))
        proc = util.run_admin(["status", "--root", self.proj], self.home,
                              env={"RULES_BY_TRIGGER_REMEMBER_AGAIN_AFTER": "50k"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for text in ENVIRONMENT_LINES + REPEAT_UNIT_LINES:
            self.assertNotIn(text, proc.stdout)
        self.assertNotIn("rule types", proc.stdout)

    def test_the_environment_is_the_doctors(self):
        out = self.admin("doctor", "--root", self.proj).stdout
        self.assertIn("rules-by-trigger 0.", out)
        self.assertIn("python ", out)
        self.assertIn("hook launcher present", out)

    def test_the_config_in_force_is_the_configs(self):
        proc = self.admin("config", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rule types:", proc.stdout)
        self.assertIn("language", proc.stdout)


if __name__ == "__main__":
    unittest.main()
