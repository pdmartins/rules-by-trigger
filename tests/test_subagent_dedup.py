"""A subagent starts from an empty context but shares the main conversation's
`session_id`. These tests pin that a rule the main conversation already
received still reaches a subagent touching the same file, once per subagent
(rules_by_trigger.due.agent_key_prefix and its wiring in rules_by_trigger.main).

Reproduced on Claude Code 2.1.277: the main conversation read `src/a.ts`, a
subagent then read `src/b.ts` and `src/a.ts`, and it received only the rule for
`src/b.ts`."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

SESSION = "s1"
FIRST_AGENT = "a1"
SECOND_AGENT = "a2"
SUBAGENT_TYPE = "general-purpose"
RULE_TEXT = "API RULE CONTENT"
LEGACY_MAP = 'rules:\n  - glob: "src/**"\n'
LEGACY_NOTICE_WORD = "migrate"


class AgentKeyPrefixTest(unittest.TestCase):
    def test_the_main_conversation_has_no_prefix(self):
        self.assertEqual(HOOK.agent_key_prefix({"session_id": SESSION}), "")

    def test_a_subagent_is_keyed_by_its_agent_id(self):
        self.assertEqual(HOOK.agent_key_prefix({"agent_id": FIRST_AGENT}),
                         f"{HOOK.AGENT_KEY_PREFIX}{FIRST_AGENT}::")

    def test_an_unusable_agent_id_counts_as_the_main_conversation(self):
        """`agent_id` arrives as JSON from another process."""
        for value in (None, "", "   ", 7, ["a1"], {"id": "a1"}):
            with self.subTest(value=value):
                self.assertEqual(HOOK.agent_key_prefix({"agent_id": value}), "")


class SubagentDedupEndToEndTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)
    TOUCHED = "src/api/users.py"

    def payload(self, agent_id, tool, rel):
        target = os.path.join(self.proj, rel if rel is not None else self.TOUCHED)
        payload = util.read_payload(tool, target, session=SESSION)
        if agent_id is not None:
            payload["agent_id"] = agent_id
            payload["agent_type"] = SUBAGENT_TYPE
        return payload

    def inject_as(self, agent_id=None, tool="Read", rel=None):
        """The context the hook injects for a tool call made by the main
        conversation (agent_id None) or by the subagent `agent_id`; "" when
        it stays silent, so `assertIn` fails instead of raising."""
        proc = util.run_hook(self.payload(agent_id, tool, rel), self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return util.injected_text(proc) or ""

    def write_api_rule(self, body=RULE_TEXT):
        util.write_rule(self.proj, "src--api.md", "src/api/**", body)

    def test_a_rule_the_main_conversation_received_still_reaches_a_subagent(self):
        self.write_api_rule()
        self.assertIn(RULE_TEXT, self.inject_as())
        self.assertIn(RULE_TEXT, self.inject_as(FIRST_AGENT),
                      "the subagent's context never held the rule")

    def test_each_context_receives_the_rule_once(self):
        self.write_api_rule()
        self.inject_as()
        self.inject_as(FIRST_AGENT)
        self.assertEqual(self.inject_as(FIRST_AGENT), "", "the subagent already has it")
        self.assertEqual(self.inject_as(), "", "the main conversation already has it")
        self.assertIn(RULE_TEXT, self.inject_as(SECOND_AGENT),
                      "another subagent is another empty context")

    def test_a_subagent_first_does_not_silence_the_main_conversation(self):
        self.write_api_rule()
        self.assertIn(RULE_TEXT, self.inject_as(FIRST_AGENT))
        self.assertIn(RULE_TEXT, self.inject_as())

    def test_an_edited_rule_supersedes_only_where_the_old_text_was(self):
        """The subagent never held the old text, so its delivery supersedes
        nothing; the main conversation did, and its next delivery says so."""
        self.write_api_rule("VERSION ONE")
        self.inject_as()
        self.write_api_rule("VERSION TWO")
        to_subagent = self.inject_as(FIRST_AGENT)
        self.assertIn("VERSION TWO", to_subagent)
        self.assertNotIn(HOOK.SUPERSEDE_NOTICE, to_subagent)
        to_main = self.inject_as()
        self.assertIn("VERSION TWO", to_main)
        self.assertIn(HOOK.SUPERSEDE_NOTICE, to_main)

    def test_the_legacy_notice_is_told_once_per_context(self):
        util.write_file(os.path.join(self.scope, HOOK.LEGACY_MAP_NAME), LEGACY_MAP)
        self.assertIn(LEGACY_NOTICE_WORD, self.inject_as())
        self.assertIn(LEGACY_NOTICE_WORD, self.inject_as(FIRST_AGENT))
        self.assertEqual(self.inject_as(FIRST_AGENT), "")

    def test_a_subagents_write_is_left_for_the_main_conversations_stop(self):
        """Only the main conversation's Stop hook verifies writes, and it reads
        them from the session's one state file. A scope must exist: with no
        rules anywhere the hook returns before recording anything."""
        self.write_api_rule()
        self.inject_as(FIRST_AGENT, tool="Write")
        target = os.path.normpath(os.path.join(self.proj, self.TOUCHED))
        written = util.read_state(self.home, SESSION)["written"]
        self.assertEqual(written, [target.replace(os.sep, "/")])


if __name__ == "__main__":
    unittest.main()
