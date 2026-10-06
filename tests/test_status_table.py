"""`status` table (spec 0008, FR-011): exact bytes, widths, cuts, alignment,
order, labels by language, which rules get a row and the `--path` block."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402
from statsutil import HAS_GIT, HOOK, git  # noqa: E402

LONG_NAME = "CONV_" + "x" * 50 + ".md"
NESTED_WEB = "packages/web"
NESTED_DEEP = "deep/very/long/folder/name/pkg"
NESTED_ONE_SEGMENT = "averyveryveryverylongfoldername"
NESTED_SHORT = "pkg/a"

HEADER_EN = ("Rule                                      Scope               "
             "State          This repo    Total")
HEADER_PT = ("Regra                                     Escopo              "
             "Estado        Neste repo    Total")
RULER = ("----------------------------------------  ------------------  "
         "------------  ----------  -------")

# What the fixture below must print, in English: this repo (most first), total
# (most first), name (uppercase before lowercase), project before global, then
# the folder; the long name and the long folders cut as FR-011 says.
EXPECTED_EN = "\n".join([
    HEADER_EN,
    RULER,
    "CONV_api.md                               project             active                 7      120",
    "GLOB_all.md                               global              active                 3       12",
    "CONV_B.md                                 project             active                 3        3",
    "CONV_a.md                                 project             active                 3        3",
    "WEB_one.md                                project (…/web)     active                 2        2",
    "CONV_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…  project             active                 1        1",
    "DEEP_one.md                               project (…/pkg)     active                 1        1",
    "ONE_seg.md                                project (…dername)  active                 1        1",
    "SHRT_one.md                               project (pkg/a)     active                 1        1",
    "CONV_off.md                               project             disabled               0        5",
    "SAME_name.md                              project             active                 0        0",
    "SAME_name.md                              project (pkg/a)     active                 0        0",
    "SAME_name.md                              global              active                 0        0",
]) + "\n"

EXPECTED_PT = EXPECTED_EN.replace(HEADER_EN, HEADER_PT)
for english, portuguese in (("project (", "projeto ("), ("project   ", "projeto   "),
                            ("active  ", "ativa   "), ("disabled    ", "desabilitada")):
    EXPECTED_PT = EXPECTED_PT.replace(english, portuguese)


class TableFixture(statsutil.RepoSandbox):
    """A repository with project rules at its root and in nested projects, a
    global scope, and usage planted for each rule."""

    def setUp(self):
        super().setUp()
        self.repo = os.path.realpath(self.proj)
        self.nested = {}
        util.write_config(self.global_scope, {"language": "en"})
        self.rule(self.proj, "CONV_api.md", 7, 7)
        self.rule(self.proj, "CONV_a.md", 3, 3)
        self.rule(self.proj, "CONV_B.md", 3, 3)
        self.rule(self.proj, "CONV_off.md", 0, 5, "enabled: false")
        self.rule(self.proj, LONG_NAME, 1, 1)
        self.rule(self.proj, "SAME_name.md", 0, 0)
        self.rule(self.home, "GLOB_all.md", 3, 12)
        self.rule(self.home, "SAME_name.md", 0, 0)
        self.nested_rule(NESTED_WEB, "WEB_one.md", 2, 2)
        self.nested_rule(NESTED_DEEP, "DEEP_one.md", 1, 1)
        self.nested_rule(NESTED_ONE_SEGMENT, "ONE_seg.md", 1, 1)
        self.nested_rule(NESTED_SHORT, "SHRT_one.md", 1, 1)
        self.nested_rule(NESTED_SHORT, "SAME_name.md", 0, 0)
        # The total of a rule with no history of its own is lifted to what the
        # global rule says; the fixture plants the one big total here.
        self.plant_all()

    def rule(self, base, name, this_repo, total, *extra):
        util.write_rule(base, name, "src/**", "Rule.", extra_frontmatter=extra)
        scope = util.scope_dir(base)
        self.usage = getattr(self, "usage", {})
        if this_repo or total:
            entry = HOOK.empty_entry()
            entry.update(injections=this_repo, first=1, last=2, sessions=1)
            self.usage[self.key(scope, name)] = {
                "total": 120 if name == "CONV_api.md" else total, "last": 2,
                "repos": {self.repo: entry} if this_repo else {}}

    def nested_rule(self, subdir, name, this_repo, total):
        self.rule(os.path.join(self.proj, *subdir.split("/")), name, this_repo, total)

    def plant_all(self):
        self.plant({"version": 2, "since": 1, "rules": self.usage})

    def status(self, *extra, env=None, cwd=None):
        proc = self.admin("status", "--root", self.proj, *extra, env=env, cwd=cwd)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout


class TableTest(TableFixture):
    def test_the_table_is_exactly_this_in_english(self):
        self.assertEqual(self.status(), EXPECTED_EN)

    def test_every_line_has_the_fixed_widths_and_no_trailing_space(self):
        for line in self.status().splitlines():
            self.assertEqual(len(line), 40 + 18 + 12 + 10 + 7 + 2 * 4, line)
            self.assertEqual(line, line.rstrip())

    def test_the_labels_are_portuguese_when_the_language_is_pt_br(self):
        util.write_config(self.global_scope, {"language": "pt-BR"})
        self.assertEqual(self.status(), EXPECTED_PT)

    def test_the_language_is_read_like_the_hook_reads_it(self):
        util.write_config(self.global_scope, {"language": "pt_br"})
        self.assertEqual(self.status().splitlines()[0], HEADER_PT)

    def test_any_other_language_gets_english_labels(self):
        util.write_config(self.global_scope, {"language": "fr"})
        self.assertEqual(self.status(), EXPECTED_EN)

    def test_the_project_language_is_used_too(self):
        util.write_config(self.global_scope, {})
        util.write_config(self.scope, {"language": "pt-BR"})
        self.assertEqual(self.status().splitlines()[0], HEADER_PT)

    def test_two_runs_in_a_row_print_identical_output(self):
        self.assertEqual(self.status(), self.status())
        self.assertEqual(self.status("--json"), self.status("--json"))

    def test_the_json_scope_names_are_english_whatever_the_language(self):
        util.write_config(self.global_scope, {"language": "pt-BR"})
        rules = json.loads(self.status("--json"))["rules"]
        self.assertEqual({rule["scope"] for rule in rules},
                         {"project", "global", f"project ({NESTED_WEB})",
                          f"project ({NESTED_DEEP})",
                          f"project ({NESTED_ONE_SEGMENT})",
                          f"project ({NESTED_SHORT})"})
        self.assertEqual({rule["state"] for rule in rules}, {"active", "disabled"})

    def test_a_rule_with_history_but_no_file_is_left_out(self):
        gone = HOOK.empty_entry()
        gone.update(injections=50, first=1, last=2, sessions=1)
        self.usage[self.key(self.scope, "CONV_gone.md")] = {
            "total": 50, "last": 2, "repos": {self.repo: gone}}
        self.plant_all()
        self.assertNotIn("CONV_gone.md", self.status())

    def test_the_nested_project_folder_has_slashes_whatever_the_depth(self):
        rules = json.loads(self.status("--json"))["rules"]
        scopes = {rule["scope"] for rule in rules if rule["name"] == "DEEP_one.md"}
        self.assertEqual(scopes, {f"project ({NESTED_DEEP})"})

    def test_a_name_over_the_width_is_cut_with_the_ellipsis_inside_it(self):
        line = [line for line in self.status().splitlines()
                if line.startswith("CONV_xxx")][0]
        self.assertEqual(line[:40], "CONV_" + "x" * 34 + "…")

    def test_the_global_only_view_lists_only_global_rules(self):
        out = self.admin("status", "--global").stdout
        names = [line.split()[0] for line in out.splitlines()[2:]]
        self.assertEqual(names, ["GLOB_all.md", "SAME_name.md"])

    def test_the_folders_git_does_not_hold_projects_are_not_entered(self):
        self.rule(os.path.join(self.proj, ".git", "hooks"), "GIT_inside.md", 9, 9)
        self.plant_all()
        self.assertNotIn("GIT_inside.md", self.status())


class EmptyAndSetupTest(statsutil.RepoSandbox):
    def test_no_rules_is_one_line(self):
        util.write_config(self.global_scope, {"language": "en"})
        proc = self.admin("status", "--root", self.proj)
        self.assertEqual((proc.returncode, proc.stdout), (0, "No rules.\n"))

    def test_no_rules_in_portuguese(self):
        util.write_config(self.global_scope, {"language": "pt-BR"})
        proc = self.admin("status", "--root", self.proj)
        self.assertEqual((proc.returncode, proc.stdout), (0, "Nenhuma regra.\n"))

    def test_no_rules_in_json_is_an_empty_list_and_exit_zero(self):
        proc = self.admin("status", "--root", self.proj, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["rules"], [])

    def test_the_setup_notice_comes_before_the_table_and_never_before_json(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "Rule.")
        lines = self.admin("status", "--root", self.proj).stdout.splitlines()
        self.assertIn("not set up", lines[0])
        self.assertTrue(lines[1].startswith("Rule "), lines[1])
        out = self.admin("status", "--root", self.proj, "--json").stdout
        self.assertTrue(out.startswith("{"), out[:80])


class WhichRulesTest(statsutil.RepoSandbox):
    """The scopes the table lists: nothing above the git root."""

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_a_project_rule_above_the_git_root_is_not_in_the_table(self):
        repo = os.path.join(self.proj, "inner")
        os.makedirs(repo)
        git("init", "-q", cwd=repo)
        util.write_rule(self.proj, "CONV_above.md", "**", "Above the repo.")
        util.write_rule(repo, "CONV_inside.md", "**", "Inside the repo.")
        out = self.admin("status", "--root", repo).stdout
        self.assertIn("CONV_inside.md", out)
        self.assertNotIn("CONV_above.md", out)

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_from_a_subfolder_without_the_project_dir_the_counts_are_the_roots(self):
        """Amended FR-009: by hand, the repo is the git root of the current
        folder, so a run from `src/web` shows what the hook recorded at the
        root."""
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API.")
        self.fire("src/web/a.py", project_dir=self.proj)
        subfolder = os.path.join(self.proj, "src", "web")
        os.makedirs(subfolder, exist_ok=True)
        proc = self.admin("status", "--json", cwd=subfolder)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["repo"], os.path.realpath(self.proj))
        self.assertEqual((report["rules"][0]["name"], report["rules"][0]["this_repo"]),
                         ("CONV_api.md", 1))

    @unittest.skipUnless(HAS_GIT, "git is not installed")
    def test_the_hook_without_the_project_dir_records_under_the_git_root(self):
        util.write_rule(self.proj, "CONV_api.md", "src/**", "API.")
        subfolder = os.path.join(self.proj, "src", "web")
        os.makedirs(subfolder, exist_ok=True)
        self.fire("src/web/a.py", project_dir="", cwd=subfolder)
        repos = self.stats()["rules"][self.key(self.scope, "CONV_api.md")]["repos"]
        self.assertEqual(list(repos), [os.path.realpath(self.proj)])


class SymlinkTest(statsutil.RepoSandbox):
    """`--path` through a symlink lists what the hook injects for that file:
    the repository root is a resolved folder, the path may not be."""
    PROJECT_SUBDIRS = ("src/deep/x", "lib")
    TARGET = "src/deep/x/a.py"
    BODIES = {"CONV_api.md": "API BODY", "CONV_deep.md": "DEEP BODY",
              "CONV_lib.md": "LIB BODY", "GLOB_all.md": "ALL BODY"}

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})
        util.write_rule(self.proj, "CONV_api.md", "src/**", self.BODIES["CONV_api.md"])
        util.write_rule(self.proj, "CONV_deep.md", "src/deep/**",
                        self.BODIES["CONV_deep.md"])
        util.write_rule(self.proj, "CONV_lib.md", "lib/**", self.BODIES["CONV_lib.md"])
        util.write_rule(self.home, "GLOB_all.md", "**/*.py", self.BODIES["GLOB_all.md"])
        self.link = os.path.join(self.tmp.name, "link")
        os.symlink(self.proj, self.link)

    def injected_by_the_hook(self):
        """The rules the hook injects when `<link>/src/deep/x/a.py` is read."""
        target = os.path.join(self.link, self.TARGET)
        proc = util.run_hook(util.read_payload("Read", target, cwd=self.link),
                             self.home, env={"CLAUDE_PROJECT_DIR": self.link})
        text = util.injected_text(proc) or ""
        return {name for name, body in self.BODIES.items() if body in text}

    def covering(self, *args, env=None):
        proc = self.admin("status", "--json", *args, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return {rule["name"] for rule in json.loads(proc.stdout)["covering"]}

    def test_the_fixture_is_what_the_hook_injects(self):
        self.assertEqual(self.injected_by_the_hook(),
                         {"CONV_api.md", "CONV_deep.md", "GLOB_all.md"})

    def test_a_root_that_is_a_symlink_lists_what_the_hook_injects(self):
        self.assertEqual(self.covering("--root", self.link, "--path", self.TARGET),
                         self.injected_by_the_hook())

    def test_an_absolute_path_through_a_symlink_lists_what_the_hook_injects(self):
        through_link = os.path.join(self.link, self.TARGET)
        self.assertEqual(self.covering("--root", self.proj, "--path", through_link),
                         self.injected_by_the_hook())

    def test_a_project_dir_that_is_a_symlink_lists_what_the_hook_injects(self):
        self.assertEqual(self.covering("--path", self.TARGET,
                                       env={"CLAUDE_PROJECT_DIR": self.link}),
                         self.injected_by_the_hook())

    def test_a_folder_through_a_symlink_keeps_its_trailing_slash_meaning(self):
        through_link = os.path.join(self.link, "src", "deep", "new") + "/"
        self.assertIn("CONV_deep.md",
                      self.covering("--root", self.proj, "--path", through_link))


class CoveringTest(TableFixture):
    """`--path`: the rules the hook would inject at that path, and no other."""

    def setUp(self):
        super().setUp()
        self.rule(self.proj, "CONV_excluded.md", 1, 1, "exclude: src/api/**")
        self.rule(self.proj, "CONV_skill.md", 1, 1)
        util.write_rule(self.proj, "CONV_skill.md", [], "Skill rule.",
                        extra_frontmatter=["call: Skill(skill=writer)"])
        # A global rule matches a path through a glob that does not start at a
        # project root.
        for name in ("GLOB_all.md", "SAME_name.md"):
            util.write_rule(self.home, name, "**/*.py", "Global rule.")
        self.plant_all()

    def covering(self, path):
        out = self.status("--path", path)
        return out.partition("\n\n")[2].splitlines()

    def test_the_block_lists_active_rules_the_hook_would_inject(self):
        block = self.covering("src/api/x.py")
        self.assertEqual(block, [
            "Covering src/api/x.py:",
            "  CONV_api.md  project",
            "  GLOB_all.md  global",
            "  CONV_B.md  project",
            "  CONV_a.md  project",
            f"  {LONG_NAME}  project",
            "  SAME_name.md  project",
            "  SAME_name.md  global",
        ])

    def test_disabled_excluded_and_skill_rules_are_out_of_the_block(self):
        block = "\n".join(self.covering("src/api/x.py"))
        for name in ("CONV_off.md", "CONV_excluded.md", "CONV_skill.md"):
            self.assertNotIn(name, block)

    def test_a_nested_project_only_covers_paths_inside_it(self):
        inside = "\n".join(self.covering(f"{NESTED_WEB}/src/x.py"))
        outside = "\n".join(self.covering("src/x.py"))
        self.assertIn(f"  WEB_one.md  project ({NESTED_WEB})", inside)
        self.assertNotIn("WEB_one.md", outside)

    def test_a_file_inside_a_rules_folder_is_covered_by_nothing(self):
        path = os.path.join(self.scope, "CONV_api.md")
        self.assertEqual(self.covering(path), [f"Covering {path}:"])

    def test_the_block_is_in_portuguese_with_the_language(self):
        util.write_config(self.global_scope, {"language": "pt-BR"})
        self.assertEqual(self.covering("src/api/x.py")[:2],
                         ["Cobrem src/api/x.py:", "  CONV_api.md  projeto"])

    def test_the_table_comes_first_and_is_unchanged_by_path(self):
        with_path = self.status("--path", "src/api/x.py").split("\n\n")[0] + "\n"
        self.assertEqual(with_path, self.status())

    def test_a_relative_path_starts_from_the_folder_status_starts_from(self):
        out = self.admin("status", "--path", "x.py", "--json",
                         cwd=os.path.join(self.proj, "src", "api"),
                         env={"CLAUDE_PROJECT_DIR": self.proj})
        names = [rule["name"] for rule in json.loads(out.stdout)["covering"]]
        self.assertNotIn("CONV_api.md", names, "x.py is at the project root")
        out = self.admin("status", "--path", "src/api/x.py", "--json",
                         env={"CLAUDE_PROJECT_DIR": self.proj})
        names = [rule["name"] for rule in json.loads(out.stdout)["covering"]]
        self.assertIn("CONV_api.md", names)


if __name__ == "__main__":
    unittest.main()
