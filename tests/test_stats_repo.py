"""Usage counted per repository and rule (FR-009): which repository a firing
belongs to, and which firings are not counted."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402
from statsutil import HAS_GIT, git  # noqa: E402


class PerRepoCountingTest(statsutil.StatsSandbox):
    def setUp(self):
        super().setUp()
        util.write_rule(self.home, "GLOB_all.md", "**/*.py", "GLOBAL")
        util.write_rule(self.proj, "CONV_api.md", "src/**", "PROJECT")

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_a_global_rule_fired_from_the_root_and_from_a_subfolder_adds_up(self):
        git("init", "-q", cwd=self.proj)
        self.fire("src/web/a.py", project_dir=self.proj)
        self.fire("src/web/a.py", project_dir=os.path.join(self.proj, "src", "web"))
        rule = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]
        self.assertEqual(list(rule["repos"]), [os.path.realpath(self.proj)])
        self.assertEqual(rule["repos"][os.path.realpath(self.proj)]["injections"], 2)
        self.assertEqual(rule["total"], 2)

    def test_same_name_in_the_global_and_a_project_scope_count_apart(self):
        util.write_rule(self.proj, "GLOB_all.md", "**/*.py", "PROJECT TWIN")
        self.fire("src/web/a.py")
        self.fire("src/web/a.py")  # a new session each time: two firings each
        rules = self.stats()["rules"]
        repo = os.path.realpath(self.proj)
        for scope in (self.global_scope, self.scope):
            rule = rules[self.key(scope, "GLOB_all.md")]
            self.assertEqual(rule["total"], 2, scope)
            self.assertEqual(rule["repos"][repo]["injections"], 2, scope)

    def test_the_same_rule_in_two_repos_keeps_a_counter_for_each(self):
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)
        self.fire("src/web/a.py", project_dir=self.proj)
        self.fire("src/web/a.py", project_dir=other)
        self.fire("src/web/a.py", project_dir=other)
        rule = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]
        self.assertEqual(rule["total"], 3)
        self.assertEqual(rule["repos"][os.path.realpath(self.proj)]["injections"], 1)
        self.assertEqual(rule["repos"][os.path.realpath(other)]["injections"], 2)

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_each_worktree_counts_apart(self):
        git("init", "-q", cwd=self.proj)
        git("commit", "-q", "--allow-empty", "-m", "x", cwd=self.proj)
        worktree = os.path.join(self.tmp.name, "worktree")
        git("worktree", "add", "-q", worktree, cwd=self.proj)
        self.fire("src/web/a.py", project_dir=self.proj)
        self.fire("src/web/a.py", project_dir=worktree)
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(set(repos), {os.path.realpath(self.proj),
                                      os.path.realpath(worktree)})

    def test_outside_git_the_folder_itself_is_the_repo(self):
        folder = os.path.join(self.proj, "src", "web")
        self.fire("src/web/a.py", project_dir=folder)
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(folder)])

    def test_a_symlinked_folder_is_counted_under_its_real_path(self):
        link = os.path.join(self.tmp.name, "link")
        os.symlink(self.proj, link)
        self.fire("src/web/a.py", project_dir=link)
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(self.proj)])

    def test_without_the_project_dir_the_current_folder_stands_in(self):
        folder = os.path.join(self.proj, "src", "web")
        self.fire("src/web/a.py", project_dir="", cwd=folder)
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(folder)])

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_when_git_cannot_run_the_current_folder_stands_in(self):
        git("init", "-q", cwd=self.proj)
        current = os.path.join(self.proj, "src", "api")
        opened = os.path.join(self.proj, "src", "web")
        self.fire("src/web/a.py", project_dir=opened, cwd=current, env={"PATH": ""})
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(current)])

    def test_when_the_project_dir_cannot_be_entered_the_current_folder_stands_in(self):
        current = os.path.join(self.proj, "src", "api")
        self.fire("src/web/a.py", project_dir=os.path.join(self.tmp.name, "gone"),
                  cwd=current)
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(current)])

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_when_git_fails_inside_a_repository_the_current_folder_stands_in(self):
        git("init", "-q", cwd=self.proj)
        bin_dir = os.path.join(self.tmp.name, "bin")
        wrapper = util.write_file(os.path.join(bin_dir, "git"),
                                  "#!/bin/sh\necho 'fatal: dubious ownership' >&2\nexit 128\n")
        os.chmod(wrapper, 0o755)
        current = os.path.join(self.proj, "src", "api")
        proc = self.fire("src/web/a.py", project_dir=os.path.join(self.proj, "src", "web"),
                         cwd=current, env={"PATH": bin_dir})
        repos = self.stats()["rules"][self.key(self.global_scope, "GLOB_all.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(current)])
        self.assertIn("dubious ownership", proc.stderr, "the failure is reported")
        self.assertIn("GLOBAL", util.injected_text(proc), "the injection is untouched")

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_a_project_rule_above_the_git_root_is_not_counted(self):
        inner = os.path.join(self.proj, "src", "web")
        git("init", "-q", cwd=inner)  # the rule lives in <proj>, above this root
        self.fire("src/web/a.py", project_dir=inner)
        rules = self.stats()["rules"]
        self.assertNotIn(self.key(self.scope, "CONV_api.md"), rules)
        self.assertIn(self.key(self.global_scope, "GLOB_all.md"), rules,
                      "the global rule fired in the same call still counts")

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_a_project_rule_inside_the_repo_is_counted_for_it(self):
        git("init", "-q", cwd=self.proj)
        self.fire("src/web/a.py", project_dir=os.path.join(self.proj, "src"))
        rule = self.stats()["rules"][self.key(self.scope, "CONV_api.md")]
        self.assertEqual(list(rule["repos"]), [os.path.realpath(self.proj)])

    def test_the_repo_is_worked_out_once_and_kept_in_the_session_state(self):
        self.fire("src/web/a.py", session="kept")
        self.assertEqual(util.read_state(self.home, "kept")["repo"],
                         os.path.realpath(self.proj))

    def test_no_temporary_file_is_left_beside_the_stats(self):
        self.fire("src/web/a.py")
        leftovers = [name for name in os.listdir(util.state_dir(self.home))
                     if ".tmp-" in name]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
