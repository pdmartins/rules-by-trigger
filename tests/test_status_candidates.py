"""`status --json` improvement candidates (spec 0008, FR-013): prune, narrow
and repeat, their priority, and a disabled rule never being one."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402
from statsutil import HOOK  # noqa: E402

PROHIBITION = "Never log the request body."
CONVENTION = "Controllers end in Controller."
REMEMBER_NEVER = "remember_again_after: never"
REMEMBER_OFTEN = "remember_again_after: 5 calls"
HANDLERS = "src/api/handlers"


class CandidateTest(statsutil.RepoSandbox):
    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})  # setup is done
        self.repo = os.path.realpath(self.proj)
        self.usage = {}

    def project_rule(self, name, glob="src/**", body=CONVENTION, *extra):
        return util.write_rule(self.proj, name, glob, body, extra_frontmatter=extra)

    def fired(self, scope, name, this_repo, total=None, dirs=None):
        """Plant `this_repo` fires in this repo and `total` for the rule."""
        entry = HOOK.empty_entry()
        entry.update(injections=this_repo, first=1, last=2, sessions=1,
                     dirs=dirs if dirs is not None else {"src/web": this_repo})
        self.usage[self.key(scope, name)] = {
            "total": this_repo if total is None else total, "last": 2,
            "repos": {self.repo: entry} if this_repo else {}}
        self.plant({"version": 2, "since": 1, "rules": self.usage})

    def rule(self, name):
        proc = self.admin("status", "--root", self.proj, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [rule for rule in json.loads(proc.stdout)["rules"]
                if rule["name"] == name][0]

    def candidate(self, name):
        return self.rule(name)["candidate"]


class PruneTest(CandidateTest):
    def test_a_global_rule_with_zero_here_and_a_total_is_not_a_candidate(self):
        util.write_rule(self.home, "GLOB_all.md", "**/*.py", CONVENTION)
        self.fired(self.global_scope, "GLOB_all.md", 0, total=4)
        rule = self.rule("GLOB_all.md")
        self.assertEqual((rule["this_repo"], rule["total"]), (0, 4))
        self.assertIsNone(rule["candidate"])
        self.assertIsNone(rule["reason"])

    def test_a_global_rule_with_total_zero_is_a_prune_candidate(self):
        util.write_rule(self.home, "GLOB_all.md", "**/*.py", CONVENTION)
        rule = self.rule("GLOB_all.md")
        self.assertEqual(rule["candidate"], "prune")
        self.assertEqual(rule["reason"], "never fired in any repo")

    def test_a_project_rule_with_zero_here_is_a_prune_candidate_whatever_its_total(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 0, total=9)
        rule = self.rule("CONV_api.md")
        self.assertEqual((rule["candidate"], rule["reason"]),
                         ("prune", "never fired in this repo"))

    def test_a_project_rule_with_a_fire_here_is_not_a_prune_candidate(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 1)
        self.assertIsNone(self.candidate("CONV_api.md"))

    def test_prune_asks_for_no_minimum_age_or_use(self):
        """The usage file is a second old and holds nothing else."""
        self.project_rule("CONV_api.md")
        self.plant({"version": 2, "since": 2 ** 31 - 1, "rules": {}})
        self.assertEqual(self.candidate("CONV_api.md"), "prune")


class NarrowTest(CandidateTest):
    def test_five_fires_one_glob_and_one_deeper_folder_is_a_narrow_candidate(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 5, dirs={HANDLERS: 5})
        rule = self.rule("CONV_api.md")
        self.assertEqual(rule["candidate"], "narrow")
        self.assertIn("always under 'src/api/handlers/'", rule["reason"])

    def test_the_deepest_common_folder_of_all_recorded_folders_is_used(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 6,
                   dirs={"src/api/handlers/a": 3, "src/api/handlers/b": 3})
        self.assertIn("always under 'src/api/handlers/'",
                      self.rule("CONV_api.md")["reason"])

    def test_four_fires_are_not_enough(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 4, dirs={HANDLERS: 4})
        self.assertIsNone(self.candidate("CONV_api.md"))

    def test_two_globs_were_widened_on_purpose(self):
        util.write_rule(self.proj, "CONV_api.md", ["src/**", "lib/**"], CONVENTION)
        self.fired(self.scope, "CONV_api.md", 5, dirs={HANDLERS: 5})
        self.assertIsNone(self.candidate("CONV_api.md"))

    def test_folders_that_share_only_the_base_of_the_glob_do_not_narrow(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 6, dirs={HANDLERS: 3, "src/web": 3})
        self.assertIsNone(self.candidate("CONV_api.md"))

    def test_a_glob_with_no_place_in_it_cannot_be_narrowed(self):
        self.project_rule("CONV_api.md", "**/*.py")
        self.fired(self.scope, "CONV_api.md", 5, dirs={HANDLERS: 5})
        self.assertIsNone(self.candidate("CONV_api.md"))

    def test_only_this_repos_fires_count(self):
        self.project_rule("CONV_api.md")
        self.fired(self.scope, "CONV_api.md", 4, total=30, dirs={HANDLERS: 4})
        self.assertIsNone(self.candidate("CONV_api.md"))


class RepeatTest(CandidateTest):
    def test_a_prohibition_set_to_never_is_a_repeat_candidate(self):
        self.project_rule("CONV_api.md", "src/**", PROHIBITION, REMEMBER_NEVER)
        self.fired(self.scope, "CONV_api.md", 1)
        rule = self.rule("CONV_api.md")
        self.assertEqual(rule["candidate"], "repeat")
        self.assertIn("reads like a prohibition", rule["reason"])

    def test_a_convention_repeated_too_often_is_a_repeat_candidate(self):
        self.project_rule("CONV_api.md", "src/**", CONVENTION, REMEMBER_OFTEN)
        self.fired(self.scope, "CONV_api.md", 1)
        rule = self.rule("CONV_api.md")
        self.assertEqual(rule["candidate"], "repeat")
        self.assertIn("over-treatment", rule["reason"])

    def test_the_same_finding_is_what_validate_prints(self):
        self.project_rule("CONV_api.md", "src/**", PROHIBITION, REMEMBER_NEVER)
        self.fired(self.scope, "CONV_api.md", 1)
        self.assertIn(self.rule("CONV_api.md")["reason"],
                      self.admin("validate", "--root", self.proj).stdout)

    def test_a_rule_the_validator_does_not_flag_is_not_one(self):
        self.project_rule("CONV_api.md", "src/**", PROHIBITION, "remember_again_after: 30k")
        self.fired(self.scope, "CONV_api.md", 1)
        self.assertIsNone(self.candidate("CONV_api.md"))


class PriorityTest(CandidateTest):
    def test_prune_comes_before_repeat(self):
        self.project_rule("CONV_api.md", "src/**", PROHIBITION, REMEMBER_NEVER)
        rule = self.rule("CONV_api.md")
        self.assertEqual(rule["candidate"], "prune")
        self.assertNotIn("prohibition", rule["reason"], "the reason names only one")

    def test_narrow_comes_before_repeat(self):
        self.project_rule("CONV_api.md", "src/**", PROHIBITION, REMEMBER_NEVER)
        self.fired(self.scope, "CONV_api.md", 5, dirs={HANDLERS: 5})
        rule = self.rule("CONV_api.md")
        self.assertEqual(rule["candidate"], "narrow")
        self.assertNotIn("prohibition", rule["reason"])


class DisabledTest(CandidateTest):
    def test_a_disabled_rule_is_never_a_candidate(self):
        cases = {"CONV_never.md": (CONVENTION, ()),
                 "CONV_repeat.md": (PROHIBITION, (REMEMBER_NEVER,)),
                 "CONV_narrow.md": (CONVENTION, ())}
        for name, (body, extra) in cases.items():
            self.project_rule(name, "src/**", body, "enabled: false", *extra)
        self.fired(self.scope, "CONV_repeat.md", 1)
        self.fired(self.scope, "CONV_narrow.md", 5, dirs={HANDLERS: 5})
        for name in cases:
            rule = self.rule(name)
            self.assertEqual(rule["state"], "disabled", name)
            self.assertEqual((rule["candidate"], rule["reason"]), (None, None), name)

    def test_a_disabled_global_rule_with_total_zero_is_not_a_prune_candidate(self):
        util.write_rule(self.home, "GLOB_all.md", "**/*.py", CONVENTION,
                        extra_frontmatter=["enabled: false"])
        self.assertIsNone(self.candidate("GLOB_all.md"))


class JsonShapeTest(CandidateTest):
    def test_a_rule_carries_exactly_the_seven_keys(self):
        self.project_rule("CONV_api.md")
        self.assertEqual(set(self.rule("CONV_api.md")),
                         {"name", "scope", "state", "this_repo", "total",
                          "candidate", "reason"})

    def test_a_nested_project_is_named_by_its_folder(self):
        util.write_rule(os.path.join(self.proj, "packages", "web"), "WEB_one.md",
                        "src/**", CONVENTION)
        self.assertEqual(self.rule("WEB_one.md")["scope"], "project (packages/web)")

    def test_an_error_leaves_stdout_empty_and_the_code_non_zero(self):
        proc = self.admin("status", "--root", os.path.join(self.tmp.name, "nope"),
                          "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip())


if __name__ == "__main__":
    unittest.main()
