"""The terminal line the user sees when a tool call injects rules
(rules_by_trigger.notice), and the `show_injections` config key that turns it
off — trusted in one direction only, the way `rule_size` is (see
rules_by_trigger.config.sanitize_show_injections)."""

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

EN = HOOK.messages_for(HOOK.DEFAULT_LANGUAGE)
PT_BR = HOOK.messages_for(HOOK.BRAZILIAN_PORTUGUESE)


def rule_block(name, repeat=False, superseded=False, scope_dir="/scope"):
    return {"name": name, "text": "body", "scope_dir": scope_dir,
           "glob": "**", "repeat": repeat, "superseded": superseded}


def legacy_block():
    """The legacy-format notice: it carries no `scope_dir`, which is exactly
    what tells `rule_blocks_of` it is not a rule."""
    return {"name": "legacy-format", "text": "migrate me"}


class NoticeLineTest(unittest.TestCase):
    """`build_notice_line` in isolation: no subprocess, no config layer."""

    def test_a_single_first_delivery_names_the_rule_with_no_suffix(self):
        line = HOOK.build_notice_line([rule_block("CONV_api.md")], EN, False)
        self.assertEqual(
            line, f"\n{HOOK.NOTICE_COLOUR} {HOOK.NOTICE_MARKER} CONV_api.md "
                  f"{HOOK.NOTICE_COLOUR_RESET}")

    def test_a_repeat_carries_the_repeat_suffix(self):
        line = HOOK.build_notice_line([rule_block("CONV_api.md", repeat=True)],
                                      EN, False)
        self.assertIn("CONV_api.md (repeat)", line)

    def test_a_superseded_block_carries_the_new_version_suffix(self):
        line = HOOK.build_notice_line(
            [rule_block("CONV_api.md", superseded=True)], EN, False)
        self.assertIn("CONV_api.md (new version)", line)

    def test_repeat_and_superseded_never_apply_to_the_same_block(self):
        # A block with both flags set (which build_blocks never produces) is
        # still resolved to exactly one suffix, deterministically.
        block = rule_block("CONV_api.md", repeat=True, superseded=True)
        line = HOOK.build_notice_line([block], EN, False)
        self.assertIn("(repeat)", line)
        self.assertNotIn("new version", line)

    def test_every_injected_rule_is_listed_in_block_order(self):
        line = HOOK.build_notice_line(
            [rule_block("CONV_api.md"), rule_block("SEC_auth.md")], EN, False)
        self.assertIn("CONV_api.md, SEC_auth.md", line)

    def test_a_subagent_marker_is_appended_once_to_the_whole_line(self):
        line = HOOK.build_notice_line([rule_block("CONV_api.md")], EN, True)
        self.assertIn("CONV_api.md (subagent)", line)
        self.assertEqual(line.count("(subagent)"), 1)

    def test_the_legacy_notice_alone_names_no_rule(self):
        self.assertIsNone(HOOK.build_notice_line([legacy_block()], EN, False))

    def test_no_blocks_at_all_is_also_nothing_to_show(self):
        self.assertIsNone(HOOK.build_notice_line([], EN, False))

    def test_a_legacy_notice_beside_a_rule_only_names_the_rule(self):
        line = HOOK.build_notice_line([legacy_block(), rule_block("CONV_api.md")],
                                      EN, False)
        self.assertIn("CONV_api.md", line)
        self.assertNotIn("legacy-format", line)

    def test_portuguese_labels(self):
        line = HOOK.build_notice_line([rule_block("CONV_api.md", repeat=True)],
                                      PT_BR, True)
        self.assertIn("(repetição)", line)
        self.assertIn("(subagente)", line)
        self.assertNotIn("(repeat)", line)


class PreToolUseOutputTest(unittest.TestCase):
    """`build_pretooluse_output`: additionalContext never depends on the
    setting, systemMessage does."""

    def test_show_injections_false_drops_the_notice_but_not_the_context(self):
        blocks = [rule_block("CONV_api.md")]
        on = HOOK.build_pretooluse_output(blocks, EN, False, True)
        off = HOOK.build_pretooluse_output(blocks, EN, False, False)
        self.assertIn("systemMessage", on)
        self.assertNotIn("systemMessage", off)
        self.assertEqual(on["hookSpecificOutput"]["additionalContext"],
                         off["hookSpecificOutput"]["additionalContext"])

    def test_no_suppress_output_and_hook_event_name_is_unchanged(self):
        """`suppressOutput` is documented by Claude Code as having no effect,
        so the payload does not carry it."""
        output = HOOK.build_pretooluse_output([rule_block("CONV_api.md")],
                                              EN, False, True)
        self.assertNotIn("suppressOutput", output)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"],
                         "PreToolUse")

    def test_only_a_legacy_notice_produces_no_system_message(self):
        output = HOOK.build_pretooluse_output([legacy_block()], EN, False, True)
        self.assertNotIn("systemMessage", output)


class ShowInjectionsConfigTest(util.SandboxTestCase):
    """The `show_injections` config key: a bool, trusted asymmetrically."""

    def load(self, trusted_count=1):
        return HOOK.load_config([self.home, self.proj], trusted_count)

    def test_the_shipped_default_is_true_with_no_config(self):
        self.assertTrue(HOOK.show_injections(HOOK.load_config()))

    def test_a_trusted_layer_may_turn_it_off(self):
        util.write_config(self.home, {"show_injections": False})
        self.assertFalse(HOOK.show_injections(self.load(trusted_count=1)))

    def test_an_untrusted_layer_may_turn_it_on_over_a_trusted_off(self):
        util.write_config(self.home, {"show_injections": False})
        util.write_config(self.proj, {"show_injections": True})
        self.assertTrue(HOOK.show_injections(self.load(trusted_count=1)))

    def test_an_untrusted_layer_may_not_turn_it_off(self):
        util.write_config(self.proj, {"show_injections": False})
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            config = self.load(trusted_count=0)
        self.assertTrue(HOOK.show_injections(config),
                        "an untrusted false is ignored, not honoured")
        self.assertIn("show_injections", stderr.getvalue())

    def test_a_non_bool_value_is_ignored_with_a_warning(self):
        for value in ("false", 0, 1, None, [], {}):
            with self.subTest(value=value):
                util.write_config(self.proj, {"show_injections": value})
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    config = self.load(trusted_count=0)
                self.assertTrue(HOOK.show_injections(config))
                self.assertIn("show_injections", stderr.getvalue())


class ShowInjectionsEndToEndTest(util.SandboxTestCase):
    """The full hook, run as a subprocess, the way Claude Code runs it."""

    PROJECT_SUBDIRS = ("src",)
    TOUCHED = "src/a.py"

    def setUp(self):
        super().setUp()
        util.write_rule(self.proj, "CONV_api.md", "src/**", "Rule text.",
                        extra_frontmatter=["remember_again_after: 1 calls"])

    def system_message(self, session="s1", agent_id=None, env=None):
        target = os.path.join(self.proj, self.TOUCHED)
        payload = util.read_payload("Read", target, session=session)
        if agent_id is not None:
            payload["agent_id"] = agent_id
        proc = util.run_hook(payload, self.home, env=env)
        return util.hook_output(proc), proc

    def test_first_injection_names_the_rule_and_carries_the_colour_codes(self):
        output, proc = self.system_message()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        message = output["systemMessage"]
        self.assertIn("CONV_api.md", message)
        self.assertIn(HOOK.NOTICE_COLOUR, message)
        self.assertIn(HOOK.NOTICE_COLOUR_RESET, message)
        self.assertNotIn("(repeat)", message)

    def test_a_reinjection_is_labelled_a_repeat(self):
        self.system_message(session="s2")
        output, _ = self.system_message(session="s2")
        self.assertIn("CONV_api.md (repeat)", output["systemMessage"])

    def test_an_edited_rule_is_labelled_a_new_version(self):
        self.system_message(session="s3")
        util.write_rule(self.proj, "CONV_api.md", "src/**", "Rule text, edited.",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        output, _ = self.system_message(session="s3")
        self.assertIn("CONV_api.md (new version)", output["systemMessage"])

    def test_a_subagent_call_carries_the_subagent_marker(self):
        output, _ = self.system_message(session="s4", agent_id="agent-1")
        self.assertIn("(subagent)", output["systemMessage"])

    def test_portuguese_config_selects_portuguese_labels(self):
        util.write_config(self.global_scope, {"language": "pt-BR"})
        self.system_message(session="s5")
        util.write_rule(self.proj, "CONV_api.md", "src/**", "Rule text, edited.",
                        extra_frontmatter=["remember_again_after: 1 calls"])
        output, _ = self.system_message(session="s5")
        self.assertIn("(nova versão)", output["systemMessage"])

    def test_nothing_injected_means_no_output_at_all(self):
        proc = self.hook_for("README.md", session="fresh")  # no glob matches
        self.assertEqual(proc.stdout.strip(), "")

    def test_additional_context_is_byte_identical_regardless_of_the_setting(self):
        on = self.inject(session="on")
        util.write_config(self.global_scope, {"show_injections": False})
        off = self.inject(session="off")
        self.assertEqual(on, off)
        self.assertNotIn("rules-by-trigger:", off)

    def test_global_false_hides_the_notice_but_keeps_the_injection(self):
        util.write_config(self.global_scope, {"show_injections": False})
        output, _ = self.system_message(session="s6")
        self.assertNotIn("systemMessage", output)
        self.assertIsNotNone(
            output["hookSpecificOutput"]["additionalContext"])

    def test_project_false_with_no_global_setting_is_still_shown_and_warned(self):
        util.write_config(self.scope, {"show_injections": False})
        output, proc = self.system_message(session="s8")
        self.assertIn("systemMessage", output)
        self.assertIn("show_injections", proc.stderr)

    def test_project_true_over_global_false_is_shown(self):
        util.write_config(self.global_scope, {"show_injections": False})
        util.write_config(self.scope, {"show_injections": True})
        output, _ = self.system_message(session="s9")
        self.assertIn("systemMessage", output)


if __name__ == "__main__":
    unittest.main()
