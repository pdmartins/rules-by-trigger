"""The two caps on the usage file and the three operations later commands use
when a rule is deleted, renamed, moved or made global or project."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402
from statsutil import HOOK  # noqa: E402


def entry(injections, last):
    value = HOOK.empty_entry()
    value.update(injections=injections, first=1, last=last)
    return value


class CapsTest(statsutil.StatsSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_api.md", "src/**", "PROJECT")

    def test_past_512_rules_the_one_fired_longest_ago_goes(self):
        rules = {f"/planted/scope::r{index}.md": {"total": 1, "last": 1000 + index,
                                                  "repos": {}}
                 for index in range(HOOK.MAX_STATS_RULES)}
        self.plant({"version": 2, "since": 1, "rules": rules})
        self.fire("src/web/a.py")
        kept = self.stats()["rules"]
        self.assertEqual(len(kept), HOOK.MAX_STATS_RULES)
        self.assertNotIn("/planted/scope::r0.md", kept)
        self.assertIn("/planted/scope::r1.md", kept)
        self.assertIn(self.key(self.scope, "CONV_api.md"), kept)

    def test_past_64_repos_the_oldest_goes_and_the_total_stays(self):
        repos = {f"/planted/repo{index}": entry(2, 1000 + index)
                 for index in range(HOOK.MAX_STATS_REPOS_PER_RULE)}
        self.plant({"version": 2, "since": 1, "rules": {
            self.key(self.scope, "CONV_api.md"): {"total": 128, "last": 2000,
                                                  "repos": repos}}})
        self.fire("src/web/a.py")
        rule = self.stats()["rules"][self.key(self.scope, "CONV_api.md")]
        self.assertEqual(len(rule["repos"]), HOOK.MAX_STATS_REPOS_PER_RULE)
        self.assertNotIn("/planted/repo0", rule["repos"])
        self.assertIn(os.path.realpath(self.proj), rule["repos"])
        self.assertEqual(rule["total"], 129, "the evicted repo's firings still count")


class SizeCeilingTest(unittest.TestCase):
    def test_a_write_over_the_ceiling_drops_the_oldest_repos_and_stays_readable(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"CLAUDE_PLUGIN_DATA": tmp, "HOME": tmp}), \
                mock.patch.object(HOOK.stats, "STATS_READ_LIMIT_BYTES", 6000):
            key = "/p/.claude/rules-by-trigger::r.md"
            repos = {f"/repo/{index}": entry(1, 100 + index) for index in range(40)}
            HOOK.update_stats(lambda stats: stats["rules"].update(
                {key: {"total": 40, "last": 139, "repos": repos}}))
            with open(HOOK.stats_path(), "rb") as handle:
                self.assertLessEqual(len(handle.read()), 6000)
            rule = HOOK.load_stats()["rules"][key]
            self.assertEqual(rule["total"], 40)
            self.assertIn("/repo/39", rule["repos"])
            self.assertNotIn("/repo/0", rule["repos"])
            kept = os.listdir(os.path.dirname(HOOK.stats_path()))
            self.assertEqual([n for n in kept if ".corrupt-" in n], [])


class OperationsTest(unittest.TestCase):
    """The pure operations on a parsed stats structure, then the same three
    through the file, as a later command will call them."""

    OLD = "/p/.claude/rules-by-trigger::old.md"
    NEW = "/p/.claude/rules-by-trigger::new.md"

    def stats(self):
        return {"version": 2, "since": 1, "rules": {
            self.OLD: {"total": 10, "last": 9,
                       "repos": {"/repo/a": entry(4, 9), "/repo/b": entry(3, 8)}},
            "/other::keep.md": {"total": 1, "last": 1, "repos": {}}}}

    def test_drop_removes_every_entry_of_the_rule_and_only_that_rule(self):
        stats = self.stats()
        HOOK.drop_rule(stats, self.OLD)
        self.assertEqual(list(stats["rules"]), ["/other::keep.md"])
        HOOK.drop_rule(stats, self.OLD)  # nothing left: not an error

    def test_move_carries_the_total_and_every_repo(self):
        stats = self.stats()
        before = stats["rules"][self.OLD]
        HOOK.move_rule(stats, self.OLD, self.NEW)
        self.assertNotIn(self.OLD, stats["rules"])
        self.assertEqual(stats["rules"][self.NEW], before)
        self.assertEqual(stats["rules"][self.NEW]["total"], 10)
        self.assertEqual(set(stats["rules"][self.NEW]["repos"]), {"/repo/a", "/repo/b"})

    def test_move_onto_a_key_with_history_keeps_the_larger_total(self):
        for destination_total, winner in ((3, "source"), (10, "destination"),
                                          (30, "destination")):
            with self.subTest(destination_total=destination_total):
                stats = self.stats()
                stats["rules"][self.NEW] = {"total": destination_total,
                                            "last": 1, "repos": {}}
                source = stats["rules"][self.OLD]
                destination = stats["rules"][self.NEW]
                HOOK.move_rule(stats, self.OLD, self.NEW)
                self.assertNotIn(self.OLD, stats["rules"])
                self.assertIs(stats["rules"][self.NEW],
                              source if winner == "source" else destination)

    def test_move_onto_a_key_with_only_history_there_leaves_it(self):
        stats = self.stats()
        del stats["rules"][self.OLD]
        stats["rules"][self.NEW] = {"total": 2, "last": 1, "repos": {}}
        before = {key: dict(rule) for key, rule in stats["rules"].items()}
        HOOK.move_rule(stats, self.OLD, self.NEW)
        self.assertEqual(stats["rules"], before)

    def test_move_of_a_rule_with_no_history_changes_nothing(self):
        stats = self.stats()
        HOOK.move_rule(stats, "/nothing::here.md", self.NEW)
        self.assertEqual(stats, self.stats())

    def test_keep_one_repo_sets_the_total_to_its_count_and_drops_the_rest(self):
        stats = self.stats()
        HOOK.keep_one_repo(stats, self.OLD, "/repo/b")
        rule = stats["rules"][self.OLD]
        self.assertEqual(list(rule["repos"]), ["/repo/b"])
        self.assertEqual((rule["total"], rule["last"]), (3, 8))

    def test_keep_one_repo_of_a_repo_that_never_fired_it_leaves_nothing(self):
        stats = self.stats()
        HOOK.keep_one_repo(stats, self.OLD, "/repo/never")
        self.assertNotIn(self.OLD, stats["rules"])

    def test_the_three_operations_write_the_usage_file(self):
        scope = "/p/.claude/rules-by-trigger"
        key = lambda name: os.path.realpath(scope) + "::" + name  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"CLAUDE_PLUGIN_DATA": tmp, "HOME": tmp}):
            HOOK.update_stats(lambda stats: stats["rules"].update(self.stats()["rules"]))
            HOOK.move_rule_usage(scope, "old.md", scope, "new.md")
            rules = HOOK.load_stats()["rules"]
            self.assertIn(key("new.md"), rules)
            self.assertNotIn(key("old.md"), rules)
            HOOK.keep_one_repo_usage(scope, "new.md", "/repo/a")
            rule = HOOK.load_stats()["rules"][key("new.md")]
            self.assertEqual((rule["total"], list(rule["repos"])), (4, ["/repo/a"]))
            HOOK.drop_rule_usage(scope, "new.md")
            self.assertEqual(list(HOOK.load_stats()["rules"]), ["/other::keep.md"])


if __name__ == "__main__":
    unittest.main()
