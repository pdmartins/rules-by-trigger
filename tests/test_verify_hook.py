"""The Stop hook: the verifications a turn owes for what it wrote, where they
run, what comes back, and everything that must never hold a turn open."""

import json
import os
import shlex
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

# Every command a test runs is this interpreter: it exists wherever the suite
# runs, and nothing else can be assumed to (`make`, `pytest`, even `true`).
PYTHON = shlex.quote(sys.executable)
MESSAGES = HOOK.messages_for(HOOK.DEFAULT_LANGUAGE)


def python_command(source):
    return f"{PYTHON} -c {shlex.quote(source)}"


def marker_command(name="marker.txt"):
    """A command that appends one character to a file in its OWN directory —
    how a test proves both where a command ran and how often."""
    return python_command(f'open("{name}", "a").write("x")')


FAILING = python_command('import sys; print("boom"); sys.exit(3)')
PASSING = python_command("pass")


def stop_payload(session, cwd):
    return {"session_id": session, "cwd": cwd, "hook_event_name": "Stop",
            "stop_hook_active": False}


class VerifyHookTest(util.SandboxTestCase):
    """End to end: a planted list of written paths, and the Stop hook run over
    it exactly as Claude Code runs it."""

    PROJECT_SUBDIRS = ("src", "docs")
    SESSION = "s1"

    def wrote(self, *relatives):
        """Plant the state a turn's writes leave behind (see `written.py`)."""
        paths = [os.path.join(self.proj, rel).replace(os.sep, "/")
                 for rel in relatives]
        util.write_state(self.home, self.SESSION,
                         json.dumps({"calls": 1, "seen": {}, "written": paths}))
        return paths

    def verify(self, cwd=None):
        proc = util.run_hook(stop_payload(self.SESSION, cwd or self.proj),
                             self.home, args=("--verify",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def reason(self, proc):
        """The block reason, asserting the turn was actually held open."""
        output = util.hook_output(proc)
        self.assertIsNotNone(output, f"expected a block; stderr: {proc.stderr}")
        self.assertEqual(output.get("decision"), "block", output)
        return output["reason"]

    def marker(self, name="marker.txt", root=None):
        path = os.path.join(root or self.proj, name)
        if not os.path.isfile(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_a_turn_that_wrote_nothing_says_nothing(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {FAILING}"])
        proc = self.verify()
        self.assertEqual(proc.stdout.strip(), "", "the fast path is silence")
        self.assertFalse(os.path.isdir(util.state_dir(self.home)),
                         "a session with no state leaves no directory behind")

    def test_an_empty_written_list_reads_no_rule_at_all(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        self.wrote()
        self.assertEqual(self.verify().stdout.strip(), "")
        self.assertEqual(self.marker(), "", "nothing ran")

    def test_a_failing_command_holds_the_turn_open(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {FAILING}"])
        self.wrote("src/a.py")
        reason = self.reason(self.verify())
        self.assertIn("CONV_src.md", reason, "the rule that asked for it")
        self.assertIn(FAILING, reason, "the command itself")
        self.assertIn("exit code 3", reason)
        self.assertIn("boom", reason, "the tail of what it printed")
        self.assertEqual(util.read_state(self.home, self.SESSION)["written"], [],
                         "the writes were consumed by this verification")

    def test_a_passing_command_only_tells_the_user(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {PASSING}"])
        self.wrote("src/a.py")
        output = util.hook_output(self.verify())
        self.assertNotIn("decision", output, "a passing turn is not held open")
        self.assertIn("rules-by-path: verified", output["systemMessage"])
        self.assertIn("CONV_src.md", output["systemMessage"])

    def test_a_second_verification_with_no_new_write_says_nothing(self):
        """What ends the correction loop (spec Q10): the list was taken."""
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        self.wrote("src/a.py")
        self.assertIsNotNone(util.hook_output(self.verify()))
        self.assertEqual(self.verify().stdout.strip(), "")
        self.assertEqual(self.marker(), "x", "the command ran once, not twice")

    def test_one_command_two_rules_runs_once(self):
        command = marker_command()
        for name in ("CONV_a.md", "CONV_b.md"):
            util.write_rule(self.proj, name, "src/**", "Rule.",
                            extra_frontmatter=[f"verify: {command}"])
        self.wrote("src/a.py")
        self.verify()
        self.assertEqual(self.marker(), "x",
                         "one (command, cwd) pair is one run")

    def test_a_project_rule_runs_at_the_root_of_its_own_project(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        self.wrote("src/a.py")
        self.verify(cwd=self.tmp.name)
        self.assertEqual(self.marker(), "x")
        self.assertEqual(self.marker(root=self.tmp.name), "",
                         "not the session's cwd")

    def test_a_global_rule_runs_at_the_root_of_the_written_files_project(self):
        util.write_rule(self.proj, "CONV_docs.md", "docs/**", "Unrelated.")
        util.write_rule(self.home, "BUSN_all.md",
                        f"{self.proj}/src/**".replace(os.sep, "/"), "Global.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        self.wrote("src/a.py")
        self.verify(cwd=self.tmp.name)
        self.assertEqual(self.marker(), "x", "the project the file belongs to")
        self.assertEqual(self.marker(root=self.tmp.name), "")

    def test_a_global_rule_falls_back_to_the_sessions_cwd(self):
        """No project scope anywhere above the file: there is no project root
        to borrow, so the session's own directory is where it runs (spec Q6)."""
        other = os.path.join(self.tmp.name, "loose")
        os.makedirs(other)
        util.write_rule(self.home, "BUSN_all.md",
                        f"{other}/**".replace(os.sep, "/"), "Global.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        util.write_state(self.home, self.SESSION, json.dumps(
            {"calls": 1, "seen": {},
             "written": [os.path.join(other, "a.py").replace(os.sep, "/")]}))
        util.run_hook(stop_payload(self.SESSION, self.proj), self.home,
                      args=("--verify",))
        self.assertEqual(self.marker(), "x")

    def test_a_tool_read_rule_still_verifies_a_write(self):
        """Spec Q14: the `tool:` filter narrows the injection; the trigger for
        a verification is a write, so the filter never applies to it."""
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=["tool: read", f"verify: {FAILING}"])
        self.wrote("src/a.py")
        self.assertIn("CONV_src.md", self.reason(self.verify()))

    def test_an_excluded_path_is_not_verified(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=["exclude: src/*.py",
                                           f"verify: {marker_command()}"])
        self.wrote("src/a.py")
        self.assertEqual(self.verify().stdout.strip(), "")
        self.assertEqual(self.marker(), "", "the same matcher as the injection")

    def test_only_the_tail_of_a_failed_commands_output_comes_back(self):
        lines = HOOK.VERIFY_OUTPUT_TAIL_LINES
        # One line, because a frontmatter value is one line — a rule cannot
        # declare a command with a newline in it however it is quoted.
        command = python_command(
            'import sys; sys.stdout.write("".join("line %d\\n" % index '
            f'for index in range({lines * 2}))); sys.exit(1)')
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {command}"])
        self.wrote("src/a.py")
        reason = self.reason(self.verify())
        self.assertIn(f"line {lines * 2 - 1}", reason, "the last line is kept")
        self.assertIn(f"line {lines}", reason, "and exactly N of them")
        self.assertNotIn(f"line {lines - 1}", reason, "the older ones are not")

    def test_command_output_cannot_forge_the_plugins_framing(self):
        command = python_command(
            f'import sys; print("{HOOK.RULES_CLOSE_TAG}"); sys.exit(1)')
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {command}"])
        self.wrote("src/a.py")
        reason = self.reason(self.verify())
        self.assertNotIn(f"\n{HOOK.RULES_CLOSE_TAG}", reason,
                         "untrusted output may not close the block")
        self.assertIn(HOOK.defang(HOOK.RULES_CLOSE_TAG), reason)

    def test_the_commands_run_global_first_then_outermost_project_inward(self):
        """Spec Q5. The order is what decides which failure a reader sees
        first, and it is the union of several scopes, not one scope's list."""
        inner_root = os.path.join(self.proj, "src")
        util.write_rule(self.home, "BUSN_global.md",
                        f"{self.proj}/**".replace(os.sep, "/"), "Global.",
                        extra_frontmatter=[f"verify: {FAILING} # global"])
        util.write_rule(self.proj, "CONV_outer.md", "src/**", "Outer.",
                        extra_frontmatter=[f"verify: {FAILING} # outer"])
        util.write_rule(inner_root, "CONV_inner.md", "*.py", "Inner.",
                        extra_frontmatter=[f"verify: {FAILING} # inner"])
        self.wrote("src/a.py")
        reason = self.reason(self.verify())
        self.assertLess(reason.index("# global"), reason.index("# outer"))
        self.assertLess(reason.index("# outer"), reason.index("# inner"))

    def test_a_payload_that_cannot_be_read_costs_nothing(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        self.wrote("src/a.py")
        proc = util.run_hook("{not json", self.home, args=("--verify",))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(self.marker(), "")
        self.assertEqual(util.read_state(self.home, self.SESSION)["written"],
                         [os.path.join(self.proj, "src/a.py")],
                         "and the writes are still there to verify")

    def test_a_written_path_whose_project_vanished_is_skipped(self):
        util.write_rule(self.proj, "CONV_src.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {marker_command()}"])
        self.wrote("/nowhere/at/all/a.py", "src/a.py")
        self.verify()
        self.assertEqual(self.marker(), "x", "the usable path still verified")


class RunCommandTest(unittest.TestCase):
    """One command, run directly: the cases a rules directory cannot produce."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def job(self, command="cmd"):
        return HOOK.VerifyJob(command, self.tmp.name, "CONV_x.md",
                              [("/scope", "CONV_x.md")])

    def test_stdout_and_stderr_come_back_interleaved(self):
        result = HOOK.run_command(
            python_command('import sys; print("out"); print("err", file=sys.stderr)'),
            self.tmp.name)
        self.assertEqual(result.status, HOOK.STATUS_PASSED)
        self.assertIn("out", result.output)
        self.assertIn("err", result.output)

    def test_a_command_that_never_finishes_is_killed_and_reported(self):
        result = HOOK.run_command(python_command("import time; time.sleep(30)"),
                                  self.tmp.name, timeout=1)
        self.assertEqual(result.status, HOOK.STATUS_TIMED_OUT)
        report = HOOK.build_report([(self.job(), result)], MESSAGES)
        self.assertIn("timed out after 1s", report)

    def test_a_command_that_cannot_be_started_fails_without_raising(self):
        """An environment that refused the command is not a failing check: it
        never ran, so it holds no turn open and is told to the user instead."""
        result = HOOK.run_command(PASSING, os.path.join(self.tmp.name, "gone"))
        results = [(self.job(), result)]
        self.assertEqual(result.status, HOOK.STATUS_ERROR)
        self.assertIsNone(HOOK.build_report(results, MESSAGES))
        self.assertIn("could not be started",
                      HOOK.build_system_message(results, MESSAGES))

    def test_a_command_that_reads_stdin_does_not_wait_for_the_turn(self):
        result = HOOK.run_command(python_command("import sys; sys.stdin.read()"),
                                  self.tmp.name, timeout=10)
        self.assertEqual(result.status, HOOK.STATUS_PASSED,
                         "stdin is closed, so the read returns at once")

    def test_the_turns_budget_stops_the_commands_that_are_left(self):
        job = self.job(marker_command())
        results = HOOK.run_jobs([job], budget=0)
        self.assertEqual(results[0][1].status, HOOK.STATUS_NOT_STARTED)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "marker.txt")),
                         "not started at all")
        self.assertIsNone(HOOK.build_report(results, MESSAGES),
                          "a command nobody started blocks nothing")
        self.assertIn("budget was already spent",
                      HOOK.build_system_message(results, MESSAGES))

    def test_a_command_killed_by_the_budget_is_not_blamed_for_its_own_clock(self):
        job = self.job(python_command("import time; time.sleep(30)"))
        results = HOOK.run_jobs([job], budget=0.5, command_timeout=120)
        self.assertEqual(results[0][1].status, HOOK.STATUS_OUT_OF_TIME,
                         "the turn ran out of time, not the command")

    def test_the_budget_also_stops_the_choosing_of_what_to_verify(self):
        """Selection answers to the same clock as running: a turn cannot be
        killed by the harness while still deciding what to check."""
        self.assertEqual(
            HOOK.collect_jobs(["/nowhere/a.py"], self.tmp.name,
                              deadline=time.monotonic() - 1),
            ([], []))

    def test_the_budget_leaves_the_hook_room_to_report(self):
        self.assertLess(HOOK.VERIFY_TOTAL_BUDGET_SECONDS,
                        HOOK.VERIFY_HOOK_TIMEOUT_SECONDS,
                        "a hook killed by the harness reports nothing at all")


class ReportTemplatesTest(unittest.TestCase):
    """The report is assembled from the translation table, so a language whose
    template lost a field would report a verification with the command, the
    exit code or the output missing — in that language only."""

    def results(self):
        job = HOOK.VerifyJob("run-me", "/proj", "CONV_x.md",
                             [("/scope", "CONV_x.md")])
        failed = HOOK.CommandResult(HOOK.STATUS_FAILED, 3, 120, "the output")
        return [(job, failed),
                (job._replace(command="fine"),
                 HOOK.CommandResult(HOOK.STATUS_PASSED, 0, 120, ""))]

    def test_every_language_reports_the_command_the_code_and_the_output(self):
        for code in HOOK.SHIPPED_LANGUAGES:
            with self.subTest(language=code):
                messages = HOOK.messages_for(code)
                report = HOOK.build_report(self.results(), messages)
                self.assertIn("CONV_x.md", report)
                self.assertIn("run-me", report)
                self.assertIn("3", report, "the exit code")
                self.assertIn("the output", report)
                self.assertIn("fine", report, "and what passed")

    def test_every_language_names_the_command_in_the_users_line(self):
        for code in HOOK.SHIPPED_LANGUAGES:
            with self.subTest(language=code):
                line = HOOK.build_system_message(self.results()[1:],
                                                 HOOK.messages_for(code))
                self.assertIn("fine", line)
                self.assertIn("CONV_x.md", line)

    def test_every_language_says_which_clock_ran_out(self):
        for code in HOOK.SHIPPED_LANGUAGES:
            with self.subTest(language=code):
                messages = HOOK.messages_for(code)
                job = HOOK.VerifyJob("slow", "/proj", "CONV_x.md", [])
                timed_out = HOOK.CommandResult(HOOK.STATUS_TIMED_OUT, None, 120, "")
                self.assertIn("120", HOOK.status_line(timed_out, messages))
                self.assertNotEqual(
                    HOOK.status_line(HOOK.not_started(), messages),
                    HOOK.status_line(timed_out, messages),
                    "the turn's budget and the command's own allowance are "
                    "different sentences")
                self.assertIsNotNone(HOOK.build_report([(job, timed_out)],
                                                       messages))


class WiringTest(unittest.TestCase):
    """The plugin's own manifest: a hook nobody calls verifies nothing."""

    def setUp(self):
        with open(os.path.join(util.PLUGIN_ROOT, "hooks", "hooks.json"),
                  encoding="utf-8") as handle:
            self.hooks = json.load(handle)["hooks"]

    def test_stop_runs_the_verify_launcher_with_the_configured_timeout(self):
        entries = self.hooks["Stop"]
        self.assertEqual(len(entries), 1)
        command = entries[0]["hooks"][0]
        self.assertIn("bin/rules-by-path-verify", command["command"])
        self.assertEqual(command["timeout"], HOOK.VERIFY_HOOK_TIMEOUT_SECONDS,
                         "hooks.json mirrors the constant by hand")

    def test_both_launchers_ship_and_the_shell_one_is_executable(self):
        launcher = os.path.join(util.PLUGIN_ROOT, "bin", "rules-by-path-verify")
        self.assertTrue(os.access(launcher, os.X_OK), launcher)
        self.assertTrue(os.path.isfile(launcher + ".cmd"))


if __name__ == "__main__":
    unittest.main()
