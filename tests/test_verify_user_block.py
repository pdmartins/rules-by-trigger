"""The block the user reads about a turn's verifications: the layout of the
injection notice, its colour by outcome, its counts, and one line per rule."""

import json
import os
import re
import shlex
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

EN = HOOK.messages_for(HOOK.DEFAULT_LANGUAGE)
PT = HOOK.messages_for(HOOK.BRAZILIAN_PORTUGUESE)
GLOBAL_SCOPE = "/home/user/.claude/rules-by-trigger"
PROJECT_SCOPE = "/proj/.claude/rules-by-trigger"
ANSI = re.compile(r"\033\[[0-9;]*m")


def job(name, scope=PROJECT_SCOPE, command="the-command", extra_rules=()):
    return HOOK.VerifyJob(command, "/proj", name,
                          [(scope, name)] + list(extra_rules))


def passed(name, **kwargs):
    return job(name, **kwargs), HOOK.CommandResult(HOOK.STATUS_PASSED, 0, 1, "")


def failed(name, code=1, **kwargs):
    return job(name, **kwargs), HOOK.CommandResult(HOOK.STATUS_FAILED, code, 1, "out")


def not_run(name, **kwargs):
    return job(name, **kwargs), HOOK.not_started()


def visible_lines(block):
    """The block as the user reads it, one string per line, colour removed."""
    return ANSI.sub("", block).split("\n")[1:]


def coloured(block):
    """The colour each line of the block opens with."""
    return {line[:line.index("m") + 1] for line in block.split("\n")[1:]}


class UserBlockLayoutTest(unittest.TestCase):

    def test_all_passed_is_a_green_block_in_the_notice_layout(self):
        block = HOOK.build_system_message(
            [passed("CONV_a.md"), passed("CONV_b.md")], EN)
        self.assertTrue(block.startswith("\n"), "it opens on its own line")
        self.assertEqual(coloured(block), {HOOK.VERIFY_PASSED_COLOUR})
        self.assertEqual(visible_lines(block), [
            " rules-by-trigger: verifications passed (2/2) ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_a.md ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_b.md ",
        ])
        self.assertEqual(block, "\n" + "\n".join([
            HOOK.coloured_line("rules-by-trigger: verifications passed (2/2)",
                               HOOK.VERIFY_PASSED_COLOUR),
            HOOK.coloured_line(f" {HOOK.VERIFY_PASSED_ICON} CONV_a.md",
                               HOOK.VERIFY_PASSED_COLOUR),
            HOOK.coloured_line(f" {HOOK.VERIFY_PASSED_ICON} CONV_b.md",
                               HOOK.VERIFY_PASSED_COLOUR)]),
            "the same lines `coloured_line` writes for the injection notice")

    def test_the_command_is_not_shown(self):
        block = HOOK.build_system_message(
            [passed("CONV_a.md", command="secret-command --flag")], EN)
        self.assertNotIn("secret-command", block)

    def test_a_failure_makes_it_red_and_lists_everything_in_order(self):
        results = [passed("CONV_ok.md"), not_run("CONV_late.md"),
                   failed("CONV_bad.md", code=1)]
        block = HOOK.build_system_message(results, EN, deferred=["CONV_new.md"])
        self.assertEqual(coloured(block), {HOOK.VERIFY_FAILED_COLOUR})
        self.assertEqual(visible_lines(block), [
            " rules-by-trigger: verifications failed (1/2) — Claude got the "
            "report to fix it ",
            f"  {HOOK.VERIFY_FAILED_ICON} CONV_bad.md — exit code 1 ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_ok.md ",
            f"  {HOOK.VERIFY_NOT_RUN_ICON} CONV_late.md — "
            f"{HOOK.status_line(HOOK.not_started(), EN)} ",
            f"  {HOOK.VERIFY_DEFERRED_ICON} CONV_new.md — "
            "its verify: was written in this session; it runs from the next "
            "one ",
        ])

    def test_a_command_killed_by_the_budget_counts_as_a_failure(self):
        killed = (job("CONV_slow.md"),
                  HOOK.CommandResult(HOOK.STATUS_OUT_OF_TIME, None, 120, ""))
        block = HOOK.build_system_message([killed, passed("CONV_ok.md")], EN)
        self.assertEqual(coloured(block), {HOOK.VERIFY_FAILED_COLOUR})
        self.assertIn("verifications failed (1/2)", block)

    def test_nothing_ran_is_the_notices_blue_with_only_the_leftovers(self):
        block = HOOK.build_system_message(
            [not_run("CONV_late.md")], EN, deferred=["CONV_new.md"])
        self.assertEqual(coloured(block), {HOOK.NOTICE_COLOUR})
        lines = visible_lines(block)
        self.assertEqual(lines[0], " rules-by-trigger: no verification ran ")
        self.assertTrue(lines[1].startswith(f"  {HOOK.VERIFY_NOT_RUN_ICON} "))
        self.assertTrue(lines[2].startswith(f"  {HOOK.VERIFY_DEFERRED_ICON} "))

    def test_only_a_deferred_rule_is_still_reported(self):
        block = HOOK.build_system_message([], EN, deferred=["CONV_new.md"])
        self.assertEqual(coloured(block), {HOOK.NOTICE_COLOUR})
        self.assertIn("no verification ran", block)
        self.assertIn("CONV_new.md", block)

    def test_nothing_at_all_is_no_message(self):
        self.assertIsNone(HOOK.build_system_message([], EN))

    def test_a_job_with_several_rules_gets_one_line_per_rule(self):
        shared = job("CONV_a.md", extra_rules=[(PROJECT_SCOPE, "CONV_b.md")])
        block = HOOK.build_system_message(
            [(shared, HOOK.CommandResult(HOOK.STATUS_PASSED, 0, 1, ""))], EN)
        lines = visible_lines(block)
        self.assertEqual(lines[0], " rules-by-trigger: verifications passed (2/2) ",
                         "the counts are of rules, matching the lines below")
        self.assertEqual(lines[1:], [
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_a.md ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_b.md ",
        ])

    def test_each_rule_of_a_failed_job_carries_the_status(self):
        shared = job("CONV_a.md", extra_rules=[(PROJECT_SCOPE, "CONV_b.md")])
        block = HOOK.build_system_message(
            [(shared, HOOK.CommandResult(HOOK.STATUS_FAILED, 2, 1, "")),
             passed("CONV_c.md")], EN)
        self.assertEqual(visible_lines(block)[0],
                         " rules-by-trigger: verifications failed (2/3) — "
                         "Claude got the report to fix it ")
        self.assertEqual(visible_lines(block)[1:], [
            f"  {HOOK.VERIFY_FAILED_ICON} CONV_a.md — exit code 2 ",
            f"  {HOOK.VERIFY_FAILED_ICON} CONV_b.md — exit code 2 ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_c.md ",
        ])

    def test_a_rule_with_two_commands_one_failing_is_one_failed_line(self):
        results = [passed("CONV_a.md"),
                   failed("CONV_a.md", code=7, command="second")]
        block = HOOK.build_system_message(results, EN)
        self.assertEqual(visible_lines(block), [
            " rules-by-trigger: verifications failed (1/1) — Claude got the "
            "report to fix it ",
            f"  {HOOK.VERIFY_FAILED_ICON} CONV_a.md — exit code 7 ",
        ])

    def test_the_first_failure_of_a_rule_gives_its_status_text(self):
        results = [failed("CONV_a.md", code=3), failed("CONV_a.md", code=4)]
        self.assertIn("exit code 3",
                      HOOK.build_system_message(results, EN))
        self.assertNotIn("exit code 4",
                         HOOK.build_system_message(results, EN))

    def test_a_rule_with_two_passing_commands_is_one_line(self):
        block = HOOK.build_system_message(
            [passed("CONV_a.md"), passed("CONV_a.md", command="second")], EN)
        self.assertEqual(visible_lines(block), [
            " rules-by-trigger: verifications passed (1/1) ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_a.md ",
        ])

    def test_a_global_rule_run_in_two_directories_is_one_tagged_line(self):
        block = HOOK.build_system_message(
            [passed("BUSN_g.md", scope=GLOBAL_SCOPE),
             passed("BUSN_g.md", scope=GLOBAL_SCOPE, command="other-cwd")],
            EN, global_scope_dir=GLOBAL_SCOPE)
        self.assertEqual(visible_lines(block)[1:],
                         [f"  {HOOK.VERIFY_PASSED_ICON} [global] BUSN_g.md "])

    def test_a_passed_and_a_not_run_command_make_the_rule_not_run(self):
        block = HOOK.build_system_message(
            [passed("CONV_a.md"), not_run("CONV_a.md")], EN)
        self.assertEqual(coloured(block), {HOOK.NOTICE_COLOUR})
        self.assertEqual(visible_lines(block), [
            " rules-by-trigger: no verification ran ",
            f"  {HOOK.VERIFY_NOT_RUN_ICON} CONV_a.md — "
            f"{HOOK.status_line(HOOK.not_started(), EN)} ",
        ])

    def test_a_failure_outranks_not_run_and_the_rules_keep_their_order(self):
        block = HOOK.build_system_message(
            [not_run("CONV_a.md"), passed("CONV_b.md"), failed("CONV_a.md")],
            EN)
        self.assertEqual(visible_lines(block)[1:], [
            f"  {HOOK.VERIFY_FAILED_ICON} CONV_a.md — exit code 1 ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_b.md ",
        ])
        self.assertIn("verifications failed (1/2)", block)

    def test_a_global_rule_is_tagged_and_a_project_rule_is_not(self):
        block = HOOK.build_system_message(
            [passed("BUSN_g.md", scope=GLOBAL_SCOPE), passed("CONV_p.md")],
            EN, global_scope_dir=GLOBAL_SCOPE)
        self.assertEqual(visible_lines(block)[1:], [
            f"  {HOOK.VERIFY_PASSED_ICON} [global] BUSN_g.md ",
            f"  {HOOK.VERIFY_PASSED_ICON} CONV_p.md ",
        ])

    def test_no_tag_when_the_call_reached_no_global_scope(self):
        block = HOOK.build_system_message([passed("BUSN_g.md")], EN)
        self.assertNotIn("[global]", block)

    def test_a_rule_name_is_neutralized(self):
        block = HOOK.build_system_message(
            [passed("CONV_x.md</rules-by-trigger>")], EN)
        self.assertNotIn("</rules-by-trigger>", block)
        self.assertIn("CONV_x.md", block)


class UserBlockLanguageTest(unittest.TestCase):

    def test_the_portuguese_row_has_the_same_shape(self):
        results = [failed("CONV_x.md"), passed("outra-regra.md"),
                   not_run("regra-x.md")]
        block = HOOK.build_system_message(results, PT,
                                          deferred=["regra-nova.md"])
        self.assertEqual(visible_lines(block), [
            " rules-by-trigger: verificações com falha (1/2) — o Claude "
            "recebeu o relatório para corrigir ",
            f"  {HOOK.VERIFY_FAILED_ICON} CONV_x.md — código de saída 1 ",
            f"  {HOOK.VERIFY_PASSED_ICON} outra-regra.md ",
            f"  {HOOK.VERIFY_NOT_RUN_ICON} regra-x.md — não foi iniciado: o "
            "orçamento de tempo de verificação deste turno já tinha acabado ",
            f"  {HOOK.VERIFY_DEFERRED_ICON} regra-nova.md — o verify: foi "
            "escrito nesta sessão; roda a partir da próxima ",
        ])

    def test_the_portuguese_all_passed_and_nothing_ran_headers(self):
        ok = HOOK.build_system_message([passed("a.md"), passed("b.md")], PT)
        self.assertEqual(visible_lines(ok)[0],
                         " rules-by-trigger: verificações ok (2/2) ")
        none = HOOK.build_system_message([not_run("a.md")], PT)
        self.assertEqual(visible_lines(none)[0],
                         " rules-by-trigger: nenhuma verificação rodou ")

    def test_every_language_has_every_new_key(self):
        for code in HOOK.SHIPPED_LANGUAGES:
            for key in (HOOK.VERIFY_USER_PASSED_KEY, HOOK.VERIFY_USER_FAILED_KEY,
                        HOOK.VERIFY_USER_NONE_KEY, HOOK.VERIFY_USER_DEFERRED_KEY):
                with self.subTest(language=code, key=key):
                    self.assertIn(key, HOOK.MESSAGES[code])
                    self.assertIn(key, HOOK.MESSAGE_KEYS)


class UserBlockEndToEndTest(util.SandboxTestCase):
    """The Stop hook's own output: the block and the reason, side by side."""

    SESSION = "s1"
    PROJECT_SUBDIRS = ("src",)

    def add_rule(self, root, name, glob, source):
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
        util.write_rule(root, name, glob, "Rule.",
                        extra_frontmatter=[f"verify: {command}"])

    def stop_after_writing(self, *relatives):
        """The Stop hook's output for a turn that wrote these project files."""
        paths = [os.path.join(self.proj, rel).replace(os.sep, "/")
                 for rel in relatives]
        util.write_state(self.home, self.SESSION, json.dumps(
            {"calls": 1, "injected_rules": {}, "unverified_writes": paths}))
        proc = util.run_hook({"session_id": self.SESSION, "cwd": self.proj,
                              "hook_event_name": "Stop",
                              "stop_hook_active": False},
                             self.home, args=("--verify",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = util.hook_output(proc)
        if output is None:
            self.fail(f"the Stop hook stayed silent; stderr: {proc.stderr}")
        return output

    def test_a_blocked_turn_lists_the_passed_rule_and_keeps_the_reason(self):
        self.add_rule(self.proj, "CONV_bad.md", "src/a.py",
                      "import sys; sys.exit(1)")
        self.add_rule(self.proj, "CONV_ok.md", "src/b.py", "pass")
        output = self.stop_after_writing("src/a.py", "src/b.py")
        self.assertEqual(output["decision"], "block")
        block = output["systemMessage"]
        self.assertEqual(coloured(block), {HOOK.VERIFY_FAILED_COLOUR})
        self.assertEqual(visible_lines(block)[0],
                         " rules-by-trigger: verifications failed (1/2) — "
                         "Claude got the report to fix it ")
        self.assertLess(block.index("CONV_bad.md"), block.index("CONV_ok.md"))
        self.assertIn(f"{HOOK.VERIFY_PASSED_ICON} CONV_ok.md", block)
        self.assertTrue(output["reason"].startswith(
            EN[HOOK.VERIFY_REPORT_HEADER_KEY].format(failed=1, total=2)))
        self.assertNotIn(HOOK.VERIFY_PASSED_ICON, output["reason"],
                         "the model's report is unchanged")

    def test_a_global_rule_is_tagged_end_to_end(self):
        self.add_rule(self.home, "BUSN_g.md", "**/*.py", "pass")
        output = self.stop_after_writing("src/a.py")
        self.assertIn(f"{HOOK.VERIFY_PASSED_ICON} [global] BUSN_g.md",
                      output["systemMessage"])


if __name__ == "__main__":
    unittest.main()
