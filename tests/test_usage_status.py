"""`status` reads the hook's usage stats: the fires of each rule, and the
narrow candidate when every injection sits under one subfolder of the glob."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402

HOOK = util.load_hook_module()


class UsageStatusTest(statsutil.RepoSandbox):
    """What a real session leaves in the usage file, as `status --json` reads
    it: the fires of this repo and the narrow candidate."""
    PROJECT_SUBDIRS = ("src/api/handlers", "src/web")

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})  # setup is done

    def touch(self, relative, session):
        proc = util.run_hook(util.read_payload("Read", os.path.join(self.proj, relative),
                                               session=session, cwd=self.proj),
                             self.home, env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def rules(self):
        proc = self.admin("status", "--root", self.proj, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return {rule["name"]: rule for rule in json.loads(proc.stdout)["rules"]}

    def test_no_stats_yet_means_every_project_rule_is_a_prune_candidate(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        rule = self.rules()["CONV_api.md"]
        self.assertEqual((rule["this_repo"], rule["total"], rule["candidate"]),
                         (0, 0, "prune"))

    def test_fires_are_counted_per_injection_and_the_unfired_rule_is_pruned(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        util.write_rule(self.proj, "CONV_dead.md", "docs/**", "DOCS")
        self.touch("src/web/a.py", "s1")
        self.touch("src/web/a.py", "s2")
        rules = self.rules()
        self.assertEqual((rules["CONV_api.md"]["this_repo"], rules["CONV_api.md"]["total"]),
                         (2, 2))
        self.assertIsNone(rules["CONV_api.md"]["candidate"])
        self.assertEqual(rules["CONV_dead.md"]["candidate"], "prune")
        self.assertEqual(rules["CONV_dead.md"]["reason"], "never fired in this repo")

    def test_narrow_when_every_injection_sits_under_one_subfolder(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        for index in range(5):
            self.touch("src/api/handlers/a.py", f"s{index}")
        rule = self.rules()["CONV_api.md"]
        self.assertEqual(rule["candidate"], "narrow")
        self.assertIn("always under 'src/api/handlers/'", rule["reason"])
        self.assertIn("--glob 'src/api/handlers/**'", rule["reason"])

    def test_no_narrow_when_injections_spread_across_the_glob(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        for index in range(5):
            self.touch("src/api/handlers/a.py", f"s{index}")
        self.touch("src/web/a.py", "other")
        self.assertIsNone(self.rules()["CONV_api.md"]["candidate"])

    def test_no_narrow_below_the_injection_threshold(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API")
        for index in range(4):
            self.touch("src/api/handlers/a.py", f"s{index}")
        self.assertIsNone(self.rules()["CONV_api.md"]["candidate"])


class NarrowingHelpersTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(util.PLUGIN_ROOT, "scripts"))
        from rules_by_trigger_admin import usage
        self.usage = usage

    def test_glob_base_stops_at_the_first_metacharacter(self):
        self.assertEqual(self.usage.glob_base_segments("src/api/**"), ["src", "api"])
        self.assertEqual(self.usage.glob_base_segments("**/docs/**"), [])
        self.assertEqual(self.usage.glob_base_segments("/opt/x/*.md"), ["opt", "x"])

    def test_common_prefix_of_recorded_directories(self):
        self.assertEqual(self.usage.common_prefix(["src/api/a", "src/api/b"]), ["src", "api"])
        self.assertEqual(self.usage.common_prefix(["src/api", "."]), [])
        self.assertEqual(self.usage.common_prefix([]), [])

    def test_absolute_globs_keep_their_leading_slash_in_the_suggestion(self):
        entry = {"injections": 9, "dirs": {"/opt/x/deep/er": 9}}
        note = self.usage.narrowing_note("OTHR_x.md", ["/opt/x/**"], entry)
        self.assertIn("--glob '/opt/x/deep/er/**'", note)

    def test_multi_glob_rules_are_never_asked_to_narrow(self):
        entry = {"injections": 9, "dirs": {"src/api/deep": 9}}
        self.assertIsNone(self.usage.narrowing_note("x.md", ["src/**", "lib/**"], entry))


if __name__ == "__main__":
    unittest.main()
