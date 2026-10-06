"""The usage file of the first format converted to the per-repo one (FR-018),
a file the plugin cannot use set aside, and what `status` shows of it."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402
from statsutil import HAS_GIT, HOOK, git, v1_entry  # noqa: E402

SINCE = 1_700_000_000


class MigrationTest(statsutil.RepoSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_api.md", "src/**", "PROJECT")
        util.write_rule(self.home, "GLOB_all.md", "**/*.py", "GLOBAL")
        self.repo = os.path.realpath(self.proj)

    def plant_v1(self, **extra_rules):
        rules = {self.key(self.scope, "CONV_api.md"): v1_entry(3, last=50),
                 self.key(self.global_scope, "GLOB_all.md"): v1_entry(7, last=60),
                 self.key(self.scope, "CONV_gone.md"): v1_entry(9, last=70)}
        rules.update(extra_rules)
        self.plant_legacy({"version": 1, "since": SINCE, "rules": rules})

    def test_status_converts_once_and_shows_the_same_numbers_twice(self):
        self.plant_v1()
        first = self.status_json()
        converted = self.stats_bytes()
        second = self.status_json()
        self.assertEqual(self.stats_bytes(), converted, "the second run rewrites nothing")
        self.assertEqual(first, second)
        stats = json.loads(converted)
        self.assertEqual(stats["version"], 2)
        self.assertEqual(stats["since"], SINCE)
        api = self.rule_of(first, "CONV_api.md")
        self.assertEqual((api["this_repo"], api["total"]), (3, 3))
        converted_rule = stats["rules"][self.key(self.scope, "CONV_api.md")]
        self.assertEqual(converted_rule["repos"][self.repo]["dirs"], {"src": 3})

    def test_the_old_file_is_left_exactly_as_it_was(self):
        self.plant_v1()
        before = self.legacy_bytes()
        self.status_json()
        self.fire("src/web/a.py")
        self.assertEqual(self.legacy_bytes(), before)
        self.assertTrue(os.path.exists(self.stats_file()))

    def test_what_an_old_session_writes_there_after_the_conversion_is_ignored(self):
        self.plant_v1()
        self.status_json()
        self.plant_legacy({"version": 1, "since": 1, "rules": {
            self.key(self.scope, "CONV_api.md"): v1_entry(90, last=99)}})
        report = self.status_json()
        self.assertEqual(self.rule_of(report, "CONV_api.md")["total"], 3)
        self.fire("src/web/a.py")
        self.assertEqual(self.stats()["rules"][self.key(self.scope, "CONV_api.md")]["total"], 4)

    def test_an_existing_new_file_is_never_overwritten_by_the_old_one(self):
        entry = HOOK.empty_entry()
        entry.update(injections=2, last=5)
        self.plant({"version": 2, "since": 1, "rules": {
            self.key(self.scope, "CONV_api.md"): {"total": 2, "last": 5,
                                                  "repos": {self.repo: entry}}}})
        self.plant_v1()
        report = self.status_json()
        self.assertEqual(self.rule_of(report, "CONV_api.md")["total"], 2)
        self.assertNotIn(self.key(self.global_scope, "GLOB_all.md"), self.stats()["rules"])

    def test_an_unusable_old_file_starts_the_new_one_from_zero_and_stays_put(self):
        util.write_file(self.legacy_file(), "{not json")
        self.status_json()
        self.assertEqual(self.stats()["rules"], {})
        self.assertEqual(self.legacy_bytes(), b"{not json")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_an_old_file_that_cannot_be_read_is_retried_not_replaced_by_zero(self):
        self.plant_v1()
        os.chmod(self.legacy_file(), 0)
        self.addCleanup(os.chmod, self.legacy_file(), 0o600)
        proc = self.admin("status", "--root", self.proj, "--json",
                          env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("unreadable", proc.stderr)
        self.assertFalse(os.path.exists(self.stats_file()))
        fired = self.fire("src/web/a.py")
        self.assertIsNotNone(util.injected_text(fired), "the call still injects")
        self.assertIn("unreadable", fired.stderr)
        self.assertFalse(os.path.exists(self.stats_file()))
        os.chmod(self.legacy_file(), 0o600)
        report = self.status_json()
        api = self.rule_of(report, "CONV_api.md")
        self.assertEqual((api["this_repo"], api["total"]), (3, 3))
        self.assertEqual(self.stats()["rules"][self.key(self.scope, "CONV_api.md")]["total"], 3)

    def test_a_project_rule_goes_to_its_repo_and_a_global_one_only_to_its_total(self):
        self.plant_v1()
        self.status_json()
        rules = self.stats()["rules"]
        project = rules[self.key(self.scope, "CONV_api.md")]
        self.assertEqual((project["total"], project["last"]), (3, 50))
        self.assertEqual(list(project["repos"]), [self.repo])
        self.assertEqual(project["repos"][self.repo]["sessions"], 1)
        self.assertEqual(project["repos"][self.repo]["recent_sessions"], ["old"])
        glob = rules[self.key(self.global_scope, "GLOB_all.md")]
        self.assertEqual((glob["total"], glob["last"], glob["repos"]), (7, 60, {}))
        report = self.status_json()
        shown = self.rule_of(report, "GLOB_all.md")
        self.assertEqual((shown["this_repo"], shown["total"]), (0, 7))

    def test_an_entry_of_a_rule_that_no_longer_exists_is_dropped(self):
        self.plant_v1()
        self.status_json()
        self.assertNotIn(self.key(self.scope, "CONV_gone.md"), self.stats()["rules"])
        self.assertEqual(len(self.stats()["rules"]), 2)

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_a_project_rule_goes_to_the_git_root_of_its_own_folder(self):
        package = os.path.join(self.tmp.name, "mono", "pkg")
        util.write_rule(package, "PKG_one.md", "**", "PKG")
        git("init", "-q", cwd=os.path.join(self.tmp.name, "mono"))
        self.plant_legacy({"version": 1, "since": SINCE, "rules": {
            self.key(util.scope_dir(package), "PKG_one.md"): v1_entry(4)}})
        self.status_json()
        rule = self.stats()["rules"][self.key(util.scope_dir(package), "PKG_one.md")]
        self.assertEqual(list(rule["repos"]),
                         [os.path.realpath(os.path.join(self.tmp.name, "mono"))])

    def test_when_the_hook_reads_first_nothing_is_counted_twice(self):
        self.plant_v1()
        self.fire("src/web/a.py")
        self.status_json()
        self.status_json()
        rules = self.stats()["rules"]
        project = rules[self.key(self.scope, "CONV_api.md")]
        self.assertEqual(project["total"], 4)
        self.assertEqual(project["repos"][self.repo]["injections"], 4)
        self.assertEqual(rules[self.key(self.global_scope, "GLOB_all.md")]["total"], 8)

    def test_a_firing_after_the_conversion_adds_one(self):
        self.plant_v1()
        self.status_json()
        self.fire("src/web/a.py")
        project = self.stats()["rules"][self.key(self.scope, "CONV_api.md")]
        self.assertEqual(project["total"], 4)
        self.assertEqual(project["repos"][self.repo]["injections"], 4)


class RejectedFileTest(statsutil.RepoSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_api.md", "src/**", "PROJECT")

    def assert_set_aside_and_zero(self, original):
        aside = self.aside_files()
        self.assertEqual(len(aside), 1)
        with open(os.path.join(util.state_dir(self.home), aside[0]), "rb") as handle:
            self.assertEqual(handle.read(), original)
        stats = self.stats()
        self.assertEqual((stats["version"], stats["rules"]), (2, {}))

    def test_a_corrupt_file_is_kept_beside_and_the_count_starts_over(self):
        util.write_file(self.stats_file(), "{not json")
        report = self.status_json()
        self.assert_set_aside_and_zero(b"{not json")
        api = self.rule_of(report, "CONV_api.md")
        self.assertEqual((api["this_repo"], api["total"]), (0, 0))

    def test_a_file_of_an_unknown_version_is_kept_beside_too(self):
        original = json.dumps({"version": 99, "since": 1, "rules": {"x::y.md": {}}})
        util.write_file(self.stats_file(), original)
        self.status_json()
        self.assert_set_aside_and_zero(original.encode("utf-8"))

    def test_the_hook_sets_a_corrupt_file_aside_and_counts_from_zero(self):
        util.write_file(self.stats_file(), "[1, 2]")
        proc = self.fire("src/web/a.py")
        self.assertIsNotNone(util.injected_text(proc))
        self.assertEqual(len(self.aside_files()), 1)
        project = self.stats()["rules"][self.key(self.scope, "CONV_api.md")]
        self.assertEqual(project["total"], 1)

    def test_the_stale_sweep_keeps_the_record_and_clears_dead_writes(self):
        util.write_file(self.stats_file(), "{not json")
        self.fire("src/web/a.py")
        directory = util.state_dir(self.home)
        util.write_file(self.legacy_file(), "{}")
        leftover = util.write_file(self.stats_file() + ".tmp-4242", "half a write")
        old = time.time() - HOOK.STATE_MAX_AGE_SECONDS - 10
        for name in os.listdir(directory):
            os.utime(os.path.join(directory, name), (old, old))
        self.fire("src/web/a.py", session="fresh")  # call 1 runs the sweep
        self.assertEqual(len(self.aside_files()), 1)
        self.assertTrue(os.path.exists(self.stats_file() + ".lock"))
        self.assertTrue(os.path.exists(self.stats_file()))
        self.assertTrue(os.path.exists(self.legacy_file()))
        self.assertFalse(os.path.exists(leftover), "a dead write ages out")


class ThisRepoViewTest(statsutil.RepoSandbox):
    """`narrow` and `prune` look at this repo's entry and the rule's total,
    whichever repository `status` is run from."""

    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_api.md", "src/**", "PROJECT")
        util.write_rule(self.home, "GLOB_all.md", "src/**", "GLOBAL")
        self.here = os.path.realpath(self.proj)
        self.elsewhere = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(self.elsewhere)

    def entry(self, injections, dirs):
        entry = HOOK.empty_entry()
        entry.update(injections=injections, first=1, last=2, dirs=dirs)
        return entry

    def rule_from(self, name, project_dir):
        """`name` as `status --json` shows it when run from `project_dir`."""
        proc = self.admin("status", "--json",
                          env={"CLAUDE_PROJECT_DIR": project_dir})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return self.rule_of(json.loads(proc.stdout), name)

    def test_the_narrow_candidate_uses_this_repos_directories(self):
        narrow = self.entry(5, {"src/api/handlers": 5})
        wide = self.entry(5, {"src/api/handlers": 3, "src/web": 2})
        self.plant({"version": 2, "since": 1, "rules": {
            self.key(self.global_scope, "GLOB_all.md"): {
                "total": 10, "last": 2,
                "repos": {self.here: narrow, os.path.realpath(self.elsewhere): wide}}}})
        here = self.rule_from("GLOB_all.md", self.proj)
        self.assertEqual(here["candidate"], "narrow")
        self.assertIn("always under 'src/api/handlers/'", here["reason"])
        self.assertIsNone(self.rule_from("GLOB_all.md", self.elsewhere)["candidate"])

    def test_a_rule_fired_only_elsewhere_is_a_prune_candidate_here_with_its_total(self):
        self.plant({"version": 2, "since": 1, "rules": {
            self.key(self.scope, "CONV_api.md"): {
                "total": 4, "last": 2,
                "repos": {os.path.realpath(self.elsewhere): self.entry(4, {"src": 4})}}}})
        rule = self.rule_from("CONV_api.md", self.proj)
        self.assertEqual((rule["this_repo"], rule["total"]), (0, 4))
        self.assertEqual(rule["candidate"], "prune")


if __name__ == "__main__":
    unittest.main()
