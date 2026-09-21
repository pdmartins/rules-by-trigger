"""Edge-case behaviour of `verify:`: a rule file this session wrote hands it
no shell, a command that never ran is not a failing check, what reaches the
model is bounded in bytes as well as in lines, and a global rule runs at the
root of the project the written file belongs to."""

import json
import os
import shlex
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

PYTHON = shlex.quote(sys.executable)
MESSAGES = HOOK.messages_for(HOOK.DEFAULT_LANGUAGE)


def python_command(source):
    """Every command a test runs is this interpreter: it exists wherever the
    suite runs, and nothing else can be assumed to."""
    return f"{PYTHON} -c {shlex.quote(source)}"


MARKER = python_command('open("marker.txt", "a").write("x")')
CLI_MARKER = python_command('open("cli.txt", "a").write("x")')
PRINT_CWD = python_command("import os, sys; print(os.getcwd()); sys.exit(1)")
FAIL = python_command("import sys; sys.exit(1)")


def stop_payload(session, cwd):
    return {"session_id": session, "cwd": cwd, "hook_event_name": "Stop",
            "stop_hook_active": False}


class VerifyEdgeCaseTestCase(util.SandboxTestCase):
    """A sandbox with one session: the writes a turn leaves behind, the Stop
    hook run over them, and the usage file it may or may not have touched."""

    PROJECT_SUBDIRS = ("src",)
    SESSION = "s1"

    def source(self, root=None, rel="src/a.py"):
        return os.path.join(root or self.proj, rel).replace(os.sep, "/")

    def wrote(self, *paths, session=None):
        """Plant the state a turn's writes leave behind (see `written.py`)."""
        util.write_state(self.home, session or self.SESSION,
                         json.dumps({"calls": 1, "injected_rules": {},
                                     "unverified_writes": list(paths)}))

    def verify(self, cwd=None, session=None):
        """The Stop hook's answer, as JSON — {} when it stayed silent."""
        proc = util.run_hook(stop_payload(session or self.SESSION,
                                          cwd or self.proj),
                             self.home, args=("--verify",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return util.hook_output(proc) or {}

    def ran_in(self, root, name="marker.txt"):
        return os.path.isfile(os.path.join(root, name))

    def verifications(self, scope, name):
        """How often the usage file counted a verification for a rule — zero
        when nothing was ever recorded for it, file included."""
        path = os.path.join(util.state_dir(self.home), HOOK.STATS_FILE_NAME)
        if not os.path.isfile(path):
            return 0
        with open(path, encoding="utf-8") as handle:
            rules = json.load(handle)["rules"]
        entry = rules.get(f"{os.path.realpath(scope)}::{name}") or {}
        return entry.get("verifications", 0)


class RuleWrittenThisSessionTest(VerifyEdgeCaseTestCase):
    """A `verify:` the session's own `Write`/`Edit` put into a rule file
    waits for the next session, the way a hook added to `settings.json` does."""

    RULE = "CONV_src.md"

    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, self.RULE, "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {MARKER}"])
        self.rule_path = os.path.join(self.scope, self.RULE).replace(os.sep, "/")

    def write_tool(self, path, session=None):
        """One PreToolUse `Write` on an absolute path, as Claude Code runs it."""
        payload = util.read_payload("Write", path,
                                    session=session or self.SESSION, cwd=self.proj)
        proc = util.run_hook(payload, self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def state(self, session=None):
        return util.read_state(self.home, session or self.SESSION)

    def test_a_write_into_the_rules_directory_is_recorded(self):
        self.write_tool(self.rule_path)
        self.assertIn(self.rule_path, self.state()["rules_written"])
        self.assertEqual(self.state()["unverified_writes"], [],
                         "a rule file is not a write anything verifies")

    def test_the_verify_it_carries_does_not_run_this_session(self):
        self.write_tool(self.rule_path)
        self.write_tool(self.source())
        output = self.verify()
        self.assertNotIn("decision", output, "nothing is held open")
        self.assertIn("written by this session", output["systemMessage"])
        self.assertIn(self.RULE, output["systemMessage"])
        self.assertFalse(self.ran_in(self.proj), "the command never ran")
        self.assertEqual(self.verifications(self.scope, self.RULE), 0,
                         "and nothing was counted for the rule")

    def test_a_blocked_turn_still_tells_the_user_about_the_deferral(self):
        """The deferred rule's line rides beside the block decision: the model
        reads the reason, the user reads `systemMessage`, and stderr is
        neither's."""
        util.write_rule(self.proj, "CONV_fails.md", "src/**", "Fails.",
                        extra_frontmatter=[f"verify: {PRINT_CWD}"])
        self.write_tool(self.rule_path)
        self.write_tool(self.source())
        output = self.verify()
        self.assertEqual(output.get("decision"), "block", output)
        self.assertNotIn(self.RULE, output["reason"],
                         "the deferred rule is not the model's business")
        self.assertIn(self.RULE, output["systemMessage"])
        self.assertIn("written by this session", output["systemMessage"])

    def test_the_order_of_the_two_writes_does_not_matter(self):
        """Editing the code first and the rule afterwards is the likelier
        sequence, and both writes pass through the same state file."""
        self.write_tool(self.source())
        self.write_tool(self.rule_path)
        output = self.verify()
        self.assertIn("written by this session", output["systemMessage"])
        self.assertFalse(self.ran_in(self.proj))

    def test_a_reset_keeps_the_rule_writes_and_clears_the_turn(self):
        """/clear and a compaction drop the session's state, but not this: the
        gate is about the SESSION, and a reset must not be the way past it."""
        self.write_tool(self.rule_path)
        self.write_tool(self.source())
        util.run_hook({"session_id": self.SESSION}, self.home,
                      args=("--reset-session",))
        state = self.state()
        self.assertIn(self.rule_path, state["rules_written"])
        self.assertEqual(state["unverified_writes"], [],
                         "the turn's writes are gone")
        self.assertEqual(state["injected_rules"], {}, "and so is the dedup")
        self.write_tool(self.source())
        self.assertIn("written by this session", self.verify()["systemMessage"])

    def test_the_next_session_runs_it(self):
        """A new session id reads the file with no history of its own — the
        native 'takes effect after a restart', with nothing to clean up."""
        self.write_tool(self.rule_path)
        self.write_tool(self.source(), session="s2")
        output = self.verify(session="s2")
        self.assertTrue(self.ran_in(self.proj))
        self.assertIn("verified", output["systemMessage"])

    def test_a_rule_added_through_the_cli_runs_at_once(self):
        """The manage skill's own path goes through Bash, which is already a
        shell: gating it would break the flow that writes rules for a living."""
        proc = self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--rule", "CONV_cli.md", "--verify", CLI_MARKER,
                          stdin="Body.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.write_tool(self.source())
        self.verify()
        self.assertTrue(self.ran_in(self.proj, "cli.txt"))


class DidNotRunTest(VerifyEdgeCaseTestCase):
    """A command the budget never started, or one the environment refused,
    did not verify anything — and did not fail anything either."""

    def test_a_command_that_could_not_be_started_blocks_nothing(self):
        """The Popen failure end to end: the directory the command would have
        run in is gone by the time the turn ends."""
        loose = os.path.join(self.tmp.name, "loose")
        gone = os.path.join(self.tmp.name, "gone")
        os.makedirs(loose)
        os.makedirs(gone)
        util.write_rule(self.home, "BUSN_all.md",
                        f"{loose}/**".replace(os.sep, "/"), "Global.",
                        extra_frontmatter=[f"verify: {MARKER}"])
        self.wrote(self.source(root=loose, rel="a.py"))
        os.rmdir(gone)
        output = self.verify(cwd=gone)
        self.assertNotIn("decision", output, "the environment is not the code")
        self.assertIn("could not be started", output["systemMessage"])
        self.assertEqual(self.verifications(self.global_scope, "BUSN_all.md"), 0)

    def test_a_command_the_budget_never_started_blocks_nothing(self):
        job = HOOK.VerifyJob(MARKER, self.proj, "CONV_a.md", [])
        results = HOOK.run_jobs([job], budget=0)
        self.assertEqual(results[0][1].status, HOOK.STATUS_NOT_STARTED)
        self.assertIsNone(HOOK.build_report(results, MESSAGES))
        self.assertIn("budget was already spent",
                      HOOK.build_system_message(results, MESSAGES))
        self.assertFalse(self.ran_in(self.proj))

    def test_a_failure_beside_it_carries_it_into_the_report(self):
        """The model may not act on a check that never ran, but it must not
        read a partial verification as a complete one either."""
        failed = (HOOK.VerifyJob("run-me", "/proj", "CONV_a.md", []),
                  HOOK.CommandResult(HOOK.STATUS_FAILED, 3, 120, "boom"))
        skipped = (HOOK.VerifyJob("never-ran", "/proj", "CONV_b.md", []),
                   HOOK.not_started())
        report = HOOK.build_report([failed, skipped], MESSAGES)
        self.assertIn("boom", report)
        self.assertIn("1 of 1", report, "the count is of what ran")
        self.assertIn("never ran", report, "the appendix says what did not")
        self.assertIn("never-ran", report)
        self.assertIn("CONV_b.md", report)
        self.assertLess(report.index("boom"), report.index("never-ran"),
                        "the failure comes first")

    def test_a_command_the_budget_killed_mid_run_still_blocks(self):
        """The other half of the split: it ran, so it did not pass."""
        job = HOOK.VerifyJob("slow", "/proj", "CONV_a.md", [])
        killed = HOOK.CommandResult(HOOK.STATUS_OUT_OF_TIME, None, 120, "half")
        self.assertIsNotNone(HOOK.build_report([(job, killed)], MESSAGES))

    def test_a_blocked_turn_reports_the_never_ran_line_and_not_the_passed_one(self):
        """On a blocked turn the user's `systemMessage` carries the appendix
        lines that are theirs alone — a command that never ran — and none of
        the lines already sitting inside the model's report, such as a command
        that passed (spec Q15; README's "one line each")."""
        loose = os.path.join(self.tmp.name, "loose")
        gone = os.path.join(self.tmp.name, "gone")
        os.makedirs(loose)
        os.makedirs(gone)
        os.rmdir(gone)
        util.write_rule(self.proj, "CONV_fail.md", "src/a.py", "Fails.",
                        extra_frontmatter=[f"verify: {FAIL}"])
        util.write_rule(self.proj, "CONV_pass.md", "src/b.py", "Passes.",
                        extra_frontmatter=[f"verify: {MARKER}"])
        util.write_rule(self.home, "BUSN_never.md",
                        f"{loose}/**".replace(os.sep, "/"), "Global.",
                        extra_frontmatter=[f"verify: {MARKER}"])
        self.wrote(self.source(), self.source(rel="src/b.py"),
                  self.source(root=loose, rel="a.py"))
        output = self.verify(cwd=gone)
        self.assertEqual(output.get("decision"), "block", output)
        message = output.get("systemMessage", "")
        self.assertIn("not run", message, "the never-ran line reaches the user")
        self.assertIn("could not be started", message)
        self.assertNotIn("verified", message,
                         "the passed command stays inside the model's report")


class OutputCeilingTest(VerifyEdgeCaseTestCase):
    """Lines are not a bound on size."""

    def test_one_enormous_line_is_cut_to_its_end(self):
        # The sentinels are built with chr() so that the command line, which
        # the report quotes back, does not carry them itself.
        command = python_command(
            "import sys; sys.stdout.write(chr(72) * 8 + 'x' * 200000 "
            "+ chr(90) * 8); sys.exit(1)")
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {command}"])
        self.wrote(self.source())
        output = self.verify()
        reason = output["reason"]
        self.assertEqual(output.get("decision"), "block", output)
        self.assertLess(len(reason), HOOK.MAX_TOTAL_CHARS)
        self.assertIn("Z" * 8, reason, "the end of the output is kept")
        self.assertNotIn("H" * 8, reason, "and the beginning is not")
        self.assertIn(HOOK.VERIFY_OUTPUT_CUT_MARKER.strip(), reason)

    def test_a_report_past_the_ceiling_keeps_its_beginning(self):
        job = HOOK.VerifyJob("run-me", "/proj", "CONV_a.md", [])
        huge = HOOK.CommandResult(HOOK.STATUS_FAILED, 1, 120,
                                  "x" * (HOOK.MAX_TOTAL_CHARS + 1_000))
        report = HOOK.build_report([(job, huge)], MESSAGES)
        self.assertLessEqual(len(report), HOOK.MAX_TOTAL_CHARS)
        self.assertIn("run-me", report, "the failure it is held open for")
        self.assertTrue(report.endswith(MESSAGES[HOOK.VERIFY_REPORT_CUT_KEY]))


class GlobalRuleDirectoryTest(VerifyEdgeCaseTestCase):
    """A global rule borrows the root of the project the written file belongs
    to (spec Q6) — the innermost ancestor with a `.claude`, which is not the
    same as the innermost with rules of its own."""

    def repo(self, name, claude=True):
        root = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(root, "src"))
        if claude:
            util.write_file(os.path.join(root, ".claude", "settings.json"), "{}")
        return root

    def global_rule(self, command, *globs):
        util.write_rule(self.home, "BUSN_all.md",
                        [glob.replace(os.sep, "/") for glob in globs],
                        "Global.", extra_frontmatter=[f"verify: {command}"])

    def test_it_runs_at_a_repo_root_that_ships_no_rules_of_its_own(self):
        repo = self.repo("repo")
        self.global_rule(PRINT_CWD, f"{repo}/src/**")
        self.wrote(self.source(root=repo))
        reason = self.verify(cwd=self.proj)["reason"]
        self.assertIn(os.path.realpath(repo), reason)
        self.assertNotIn(os.path.realpath(self.proj), reason,
                         "the session's cwd is the fallback, not the answer")

    def test_with_no_claude_anywhere_it_runs_at_the_sessions_cwd(self):
        loose = self.repo("loose", claude=False)
        self.global_rule(PRINT_CWD, f"{loose}/src/**")
        self.wrote(self.source(root=loose))
        self.assertIn(os.path.realpath(self.proj),
                      self.verify(cwd=self.proj)["reason"])

    def test_one_rule_over_two_repositories_runs_once_in_each(self):
        first, second = self.repo("repo_a"), self.repo("repo_b")
        self.global_rule(MARKER, f"{first}/src/**", f"{second}/src/**")
        self.wrote(self.source(root=first), self.source(root=second))
        self.verify(cwd=self.proj)
        self.assertTrue(self.ran_in(first))
        self.assertTrue(self.ran_in(second))
        self.assertFalse(self.ran_in(self.proj), "and not at the session's cwd")

    def test_a_file_directly_under_home_falls_back_to_the_sessions_cwd(self):
        """`~/.claude` is the GLOBAL scope, not a project: a file written
        straight under home, with no project `.claude` between it and home,
        must not resolve to home itself — it has none of its own, so the
        session's cwd is what the global rule's command borrows."""
        self.global_rule(PRINT_CWD, f"{self.home}/loose/**")
        self.wrote(self.source(root=self.home, rel="loose/a.py"))
        reason = self.verify(cwd=self.proj)["reason"]
        self.assertIn(os.path.realpath(self.proj), reason)
        self.assertNotIn(os.path.realpath(self.home), reason,
                         "home's own `.claude` is the global scope, not a project")

    def test_a_project_nested_under_home_still_resolves_to_it(self):
        """The opposite case: a REAL project — its own `.claude`, not home's —
        happens to sit inside the user's home directory. Only home itself is
        skipped by the fix above; an ancestor (or here, a descendant carrying
        its own `.claude`) still counts as a project."""
        nested = os.path.join(self.home, "nested-repo")
        os.makedirs(os.path.join(nested, "src"))
        util.write_file(os.path.join(nested, ".claude", "settings.json"), "{}")
        self.global_rule(PRINT_CWD, f"{nested}/src/**")
        self.wrote(self.source(root=nested))
        reason = self.verify(cwd=self.proj)["reason"]
        self.assertIn(os.path.realpath(nested), reason)
        self.assertNotIn(os.path.realpath(self.proj), reason,
                         "the nested project's own root is the answer, not the "
                         "session's cwd")


class VerifyInvariantsTest(VerifyEdgeCaseTestCase):
    """Two invariants assumed elsewhere in this suite."""

    def test_a_missing_command_is_a_failure_that_holds_the_turn_open(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=["verify: rules-by-trigger-no-such-cmd"])
        self.wrote(self.source())
        output = self.verify()
        self.assertEqual(output.get("decision"), "block", output)
        self.assertIn("exit code 127", output["reason"],
                      "the shell ran and said the command does not exist")

    def test_verification_is_wired_to_stop_and_to_nothing_else(self):
        with open(os.path.join(util.PLUGIN_ROOT, "hooks", "hooks.json"),
                  encoding="utf-8") as handle:
            hooks = json.load(handle)["hooks"]
        self.assertIn("Stop", hooks)
        self.assertNotIn("SubagentStop", hooks,
                         "spec §3: verification is the main turn's only")


if __name__ == "__main__":
    unittest.main()
