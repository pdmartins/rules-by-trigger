"""`/rules-by-trigger:status` typed by the user (FR-010): the UserPromptExpansion
hook blocks the expansion with the CLI's table as the reason, so the table is
shown and the model does not answer. Anything it cannot do, it leaves to the
command body: no stdout, one line on stderr, exit 0."""

import contextlib
import io
import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402

HOOK = statsutil.HOOK
STATUS_COMMAND_FLAG = "--status-command"
COMMAND_NAME = "rules-by-trigger:status"
EVENT = "UserPromptExpansion"
HOOKS_JSON = os.path.join(util.PLUGIN_ROOT, "hooks", "hooks.json")
HOOK_TIMEOUT_SECONDS = 10
NESTED_RULE = "CONV_nested.md"
ROOT_RULE = "CONV_root.md"
SHORT_TIMEOUT_SECONDS = 0.5


def event(command_args="", command_name=COMMAND_NAME):
    prompt = f"/{command_name} {command_args}".strip()
    return {"hook_event_name": EVENT, "session_id": "s1", "cwd": "/",
            "expansion_type": "slash_command", "command_name": command_name,
            "command_args": command_args, "command_source": "plugin",
            "prompt": prompt}


class StatusCommandTest(statsutil.RepoSandbox):
    PROJECT_SUBDIRS = ("src/api",)

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})  # setup is done
        util.write_rule(self.proj, "CONV_api.md", "src/api/**", "API RULE")
        util.write_rule(self.home, "ARCH_global.md", "**/*.py", "GLOBAL RULE")
        self.env = {"CLAUDE_PROJECT_DIR": self.proj}

    def expand(self, payload, env=None):
        return util.run_hook(payload, self.home, args=(STATUS_COMMAND_FLAG,),
                             env=env if env is not None else self.env,
                             cwd=self.proj)

    def cli(self, *args, env=None):
        proc = self.admin("status", *args,
                          env=env if env is not None else self.env, cwd=self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def assert_blocks_with(self, proc, table):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        block = json.loads(proc.stdout)
        self.assertEqual(set(block), {"decision", "reason"})
        self.assertEqual(block["decision"], "block")
        self.assertEqual(block["reason"], table)

    def assert_leaves_it_to_the_command(self, proc, stderr_lines):
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(proc.stderr.splitlines()), stderr_lines, proc.stderr)

    def test_the_reason_is_the_cli_table_byte_for_byte(self):
        proc = self.expand(event())
        self.assert_blocks_with(proc, self.cli())
        self.assertIn("CONV_api.md", json.loads(proc.stdout)["reason"])

    def test_with_a_path_the_reason_is_the_cli_output_for_that_path(self):
        proc = self.expand(event("--path src/api/a.py"))
        table = self.cli("--path", "src/api/a.py")
        self.assert_blocks_with(proc, table)
        self.assertIn("CONV_api.md", table.split("\n\n")[-1])

    def test_a_quoted_path_with_spaces_reaches_the_cli_as_one_argument(self):
        proc = self.expand(event('--path "src/my dir/a.py"'))
        self.assert_blocks_with(proc, self.cli("--path", "src/my dir/a.py"))

    def test_a_path_keeps_its_backslashes_and_loses_only_its_quotes(self):
        for typed, given in ((r"--path src\api\a.py", r"src\api\a.py"),
                             (r'--path "C:\Users\me\a b.py"', r"C:\Users\me\a b.py"),
                             (r"--path 'src/my dir/a.py'", "src/my dir/a.py")):
            with self.subTest(typed=typed):
                table = self.cli("--path", given)
                self.assert_blocks_with(self.expand(event(typed)), table)
                self.assertIn(given, table)

    def test_a_name_cut_with_an_ellipsis_survives_a_narrow_console_encoding(self):
        util.write_rule(self.proj, "CONV_" + "long" * 12 + ".md", "src/api/**", "LONG")
        env = {**self.env, "PYTHONIOENCODING": "cp1252"}
        table = self.cli(env={**self.env, "PYTHONIOENCODING": "utf-8"})
        self.assertIn("\u2026", table)
        self.assert_blocks_with(self.expand(event(), env=env), table)

    def test_two_runs_with_no_fire_between_are_identical(self):
        self.assertEqual(self.expand(event()).stdout, self.expand(event()).stdout)

    def test_without_the_project_dir_it_starts_from_the_current_folder(self):
        proc = self.expand(event(), env={})
        self.assert_blocks_with(proc, self.cli(env={}))

    def test_without_setup_the_notice_line_comes_first_as_in_the_cli(self):
        os.remove(os.path.join(self.global_scope, "config.json"))
        proc = self.expand(event())
        table = self.cli()
        self.assert_blocks_with(proc, table)
        notice = HOOK.messages_for(HOOK.DEFAULT_LANGUAGE)[HOOK.SETUP_NOTICE_KEY]
        self.assertTrue(table.startswith(notice + "\n"))

    def test_another_command_is_left_alone(self):
        for name in ("other:status", "status", "rules-by-trigger:doctor",
                     "rules-by-trigger:status-more", ""):
            with self.subTest(name=name):
                self.assert_leaves_it_to_the_command(
                    self.expand(event(command_name=name)), 0)

    def test_arguments_it_does_not_know_are_left_to_the_command(self):
        for args in ("--json", "--root /tmp", "--path", "--path a b",
                     "--path a --json", "src/a.py", '--path "unclosed',
                     '--path ""', "--path=src/a.py"):
            with self.subTest(args=args):
                self.assert_leaves_it_to_the_command(
                    self.expand(event(args)), 1)

    def test_a_cli_that_fails_is_left_to_the_command(self):
        gone = os.path.join(self.tmp.name, "gone")
        proc = self.expand(event(), env={"CLAUDE_PROJECT_DIR": gone})
        self.assert_leaves_it_to_the_command(proc, 1)
        self.assertIn("exited 1", proc.stderr)

    def test_an_unreadable_event_is_left_to_the_command(self):
        proc = util.run_hook("not json", self.home, args=(STATUS_COMMAND_FLAG,),
                             env=self.env, cwd=self.proj)
        self.assert_leaves_it_to_the_command(proc, 1)

    def fake_cli(self, body):
        path = util.write_file(os.path.join(self.tmp.name, "fake-cli"),
                               "#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def expand_in_process(self, launcher, timeout):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            HOOK.expand_status_command(event(), launcher=launcher, timeout=timeout)
        return out.getvalue(), err.getvalue()

    @unittest.skipIf(os.name == "nt", "the fake CLI is a shell script")
    def test_a_cli_that_takes_too_long_is_left_to_the_command(self):
        out, err = self.expand_in_process(self.fake_cli("sleep 30\n"),
                                          SHORT_TIMEOUT_SECONDS)
        self.assertEqual(out, "")
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn("took more than", err)

    @unittest.skipIf(os.name == "nt", "the fake CLI is a shell script")
    def test_a_cli_that_prints_nothing_or_cannot_start_is_left_to_the_command(self):
        for launcher in (self.fake_cli("exit 0\n"),
                         os.path.join(self.tmp.name, "missing")):
            with self.subTest(launcher=launcher):
                out, err = self.expand_in_process(launcher, SHORT_TIMEOUT_SECONDS)
                self.assertEqual(out, "")
                self.assertEqual(len(err.splitlines()), 1, err)

    def test_the_plugin_registers_the_event_with_its_matcher_and_timeout(self):
        with open(HOOKS_JSON, encoding="utf-8") as handle:
            registered = json.load(handle)["hooks"][EVENT]
        self.assertEqual([entry["matcher"] for entry in registered], [COMMAND_NAME])
        (hook,) = registered[0]["hooks"]
        self.assertEqual(hook["timeout"], HOOK_TIMEOUT_SECONDS)
        self.assertTrue(hook["command"].endswith(
            f"bin/rules-by-trigger-hook\" {STATUS_COMMAND_FLAG}"))
        self.assertLess(HOOK.statuscommand.CLI_TIMEOUT_SECONDS, hook["timeout"])


class StatusGitFailureTest(statsutil.RepoSandbox):
    """Git runs once per `status`, so what it fails with is said once."""

    @unittest.skipIf(os.name == "nt", "the fake git is a /bin/sh script")
    def test_a_git_that_fails_is_reported_once(self):
        util.write_config(self.global_scope, {"language": "en"})
        bin_dir = os.path.join(self.tmp.name, "bin")
        wrapper = util.write_file(os.path.join(bin_dir, "git"),
                                  "#!/bin/sh\necho 'fatal: dubious ownership' >&2\nexit 128\n")
        os.chmod(wrapper, 0o755)
        for args, env in ((("--root", self.proj), {}),
                          ((), {"CLAUDE_PROJECT_DIR": self.proj})):
            with self.subTest(args=args):
                proc = self.admin("status", *args, env={**env, "PATH": bin_dir},
                                  cwd=self.proj)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stderr.count("git root of"), 1, proc.stderr)


class StatusOutsideGitTest(statsutil.RepoSandbox):
    """Outside git there is no search: only the folder's own project is listed."""

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {"language": "en"})
        self.plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(self.plain)
        for root in (self.proj, self.plain):
            util.write_rule(root, ROOT_RULE, "src/**", "ROOT RULE")
            util.write_rule(os.path.join(root, "pkg"), NESTED_RULE, "pkg/**",
                            "NESTED RULE")

    def names_from(self, root, env=None):
        env = {"GIT_CEILING_DIRECTORIES": self.tmp.name, **(env or {})}
        proc = self.admin("status", "--root", root, "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [rule["name"] for rule in json.loads(proc.stdout)["rules"]]

    def test_a_nested_project_below_a_folder_outside_git_is_not_listed(self):
        self.assertEqual(self.names_from(self.plain), [ROOT_RULE])

    def test_inside_git_the_nested_project_is_listed(self):
        self.assertEqual(sorted(self.names_from(self.proj)),
                         sorted([ROOT_RULE, NESTED_RULE]))


if __name__ == "__main__":
    unittest.main()
