"""Tests for the `call:` trigger: a rule fired by a tool call — today, only a
`Skill` load — instead of a touched file path. The path trigger's own tests
are untouched (see test_frontmatter.py and test_hook.py); this file covers
only what frontmatter.py, matching.py, rules.py, main.py, injection.py and
stats.py add for it."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

SKILL_TOOL = "Skill"
WORKFLOW_SKILL = "workflow-authoring"
WORKFLOW_CALL = f"Skill(skill={WORKFLOW_SKILL})"
OTHER_SKILL = "some-other-skill"


def call_payload(skill, session="s1", cwd="/tmp", agent_id=None, args=""):
    payload = {
        "session_id": session,
        "cwd": cwd,
        "tool_name": SKILL_TOOL,
        "tool_input": {"skill": skill, "args": args},
        "hook_event_name": "PreToolUse",
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def write_call_rule(scope_dir, name, call_value, body, extra_frontmatter=()):
    """A call-only rule: no `glob:` line at all. `util.write_rule` always
    writes one, so a rule with a `call:` and no `glob:` is built directly."""
    lines = ["---", f"call: {call_value}"]
    lines.extend(extra_frontmatter)
    lines.append("---")
    lines.append("")
    return util.write_file(os.path.join(scope_dir, name),
                           "\n".join(lines) + body.strip() + "\n")


class ParseCallTriggerTest(unittest.TestCase):
    def test_valid_triggers(self):
        cases = {
            "Skill(skill=workflow-authoring)":
                ("Skill", "skill", "workflow-authoring"),
            "  Skill ( skill = workflow-authoring )  ":
                ("Skill", "skill", "workflow-authoring"),
            "Skill(skill=plugin:name)": ("Skill", "skill", "plugin:name"),
            "Skill(skill=a(b))": ("Skill", "skill", "a(b)"),  # value holds a ')'
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(HOOK.parse_call_trigger(text), expected)

    def test_invalid_triggers(self):
        for text in ("Skill", "Skill()", "Skill(skill=)", "Skill(=x)",
                     "skill=x", "1Skill(skill=x)"):
            with self.subTest(text=text):
                self.assertIsNone(HOOK.parse_call_trigger(text))


class CallsOfTest(unittest.TestCase):
    def test_singular_key(self):
        self.assertEqual(HOOK.calls_of({"call": "Skill(skill=x)"}),
                         [("Skill(skill=x)", "Skill", "skill", "x")])

    def test_plural_key_with_one_value(self):
        self.assertEqual(HOOK.calls_of({"calls": "Skill(skill=x)"}),
                         [("Skill(skill=x)", "Skill", "skill", "x")])

    def test_plural_key_with_a_list(self):
        self.assertEqual(
            HOOK.calls_of({"calls": ["Skill(skill=x)", "Skill(skill=y)"]}),
            [("Skill(skill=x)", "Skill", "skill", "x"),
             ("Skill(skill=y)", "Skill", "skill", "y")])

    def test_unparseable_entries_are_skipped_silently(self):
        fields = {"call": ["Skill(skill=x)", "not a trigger", "Skill(skill=y)"]}
        self.assertEqual([text for text, *_rest in HOOK.calls_of(fields)],
                         ["Skill(skill=x)", "Skill(skill=y)"])


class CallTriggerOfTest(unittest.TestCase):
    def test_a_non_string_input_value_never_matches(self):
        fields = {"call": WORKFLOW_CALL}
        self.assertIsNone(
            HOOK.call_trigger_of(fields, SKILL_TOOL, {"skill": 123}))

    def test_the_input_value_is_stripped_before_comparing(self):
        fields = {"call": WORKFLOW_CALL}
        self.assertEqual(
            HOOK.call_trigger_of(fields, SKILL_TOOL,
                                 {"skill": f"  {WORKFLOW_SKILL}  "}),
            WORKFLOW_CALL)


class CallTriggerEndToEndTest(util.SandboxTestCase):
    def test_global_scope_call_rule_fires_on_matching_skill(self):
        write_call_rule(self.global_scope, "workflow.md", WORKFLOW_CALL,
                        "USE THE SCRIPT API")
        proc = util.run_hook(call_payload(WORKFLOW_SKILL, cwd=self.proj), self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("USE THE SCRIPT API", util.injected_text(proc) or "")

    def test_same_payload_again_same_session_is_deduplicated(self):
        write_call_rule(self.global_scope, "workflow.md", WORKFLOW_CALL,
                        "USE THE SCRIPT API")
        payload = call_payload(WORKFLOW_SKILL, cwd=self.proj)
        self.assertIsNotNone(util.injected_text(util.run_hook(payload, self.home)))
        self.assertIsNone(util.injected_text(util.run_hook(payload, self.home)),
                          "the same rule must not be sent twice in one session")

    def test_non_matching_skill_no_output_and_state_file_not_advanced(self):
        write_call_rule(self.global_scope, "workflow.md", WORKFLOW_CALL,
                        "USE THE SCRIPT API")
        session = "s-untouched"
        proc = util.run_hook(call_payload(OTHER_SKILL, session=session, cwd=self.proj),
                             self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(util.injected_text(proc))
        self.assertFalse(os.path.isfile(util.state_path(self.home, session)),
                         "a call with nothing to deliver must not open the state file")

    def test_non_matching_skill_does_not_advance_an_existing_call_counter(self):
        """A rule's `remember_again_after: N calls` measures distance in
        calls — a Skill call that matched nothing must not be one of them,
        even once the session already has a state file for another reason."""
        write_call_rule(self.global_scope, "workflow.md", WORKFLOW_CALL,
                        "USE THE SCRIPT API")
        session = "s-existing"
        target = os.path.join(self.proj, "unrelated.py")
        util.write_file(target, "print(1)\n")
        util.run_hook(util.read_payload("Read", target, session=session, cwd=self.proj),
                      self.home)
        self.assertEqual(util.read_state(self.home, session)["calls"], 1)
        proc = util.run_hook(call_payload(OTHER_SKILL, session=session, cwd=self.proj),
                             self.home)
        self.assertIsNone(util.injected_text(proc))
        self.assertEqual(util.read_state(self.home, session)["calls"], 1,
                         "a call matching nothing must not advance the counter")

    def test_project_scope_call_rule_is_found_from_cwd(self):
        write_call_rule(self.scope, "workflow.md", WORKFLOW_CALL, "PROJECT RULE")
        proc = util.run_hook(call_payload(WORKFLOW_SKILL, cwd=self.proj), self.home)
        self.assertIn("PROJECT RULE", util.injected_text(proc) or "")

    def test_call_naming_an_unregistered_tool_never_fires(self):
        write_call_rule(self.global_scope, "bash.md", "Bash(command=ls)", "NEVER")
        proc = util.run_hook(call_payload(WORKFLOW_SKILL, cwd=self.proj), self.home)
        self.assertIsNone(util.injected_text(proc))

    def test_glob_only_rule_never_fires_on_a_skill_call(self):
        util.write_rule(self.proj, "glob-only.md", "**", "PATH ONLY")
        proc = util.run_hook(call_payload(WORKFLOW_SKILL, cwd=self.proj), self.home)
        self.assertIsNone(util.injected_text(proc))

    def test_non_dict_tool_input_no_output_no_error(self):
        payload = {"session_id": "s1", "cwd": self.proj, "tool_name": SKILL_TOOL,
                  "tool_input": "not-a-dict", "hook_event_name": "PreToolUse"}
        proc = util.run_hook(payload, self.home)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(util.injected_text(proc))
        self.assertEqual(proc.stderr, "")

    def test_glob_and_call_rule_shares_the_dedup_with_a_later_file_touch(self):
        util.write_rule(self.proj, "both.md", "src/**", "SHARED RULE",
                        extra_frontmatter=[f"call: {WORKFLOW_CALL}"])
        session = "s-shared"
        proc = util.run_hook(call_payload(WORKFLOW_SKILL, session=session, cwd=self.proj),
                             self.home)
        self.assertIn("SHARED RULE", util.injected_text(proc) or "")
        target = os.path.join(self.proj, "src", "a.py")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        read_proc = util.run_hook(
            util.read_payload("Read", target, session=session, cwd=self.proj), self.home)
        self.assertIsNone(util.injected_text(read_proc),
                          "the file touch must not re-send what the call already delivered")

    def test_agent_id_gets_the_delivery_again_inside_the_subagent(self):
        write_call_rule(self.global_scope, "workflow.md", WORKFLOW_CALL,
                        "USE THE SCRIPT API")
        session = "s-agent"
        main_proc = util.run_hook(
            call_payload(WORKFLOW_SKILL, session=session, cwd=self.proj), self.home)
        self.assertIsNotNone(util.injected_text(main_proc))
        payload = call_payload(WORKFLOW_SKILL, session=session, cwd=self.proj,
                               agent_id="sub-1")
        sub_proc = util.run_hook(payload, self.home)
        self.assertIsNotNone(util.injected_text(sub_proc),
                             "a subagent starts from an empty context of its own")

    def test_stats_record_the_trigger_text_and_no_directory(self):
        write_call_rule(self.global_scope, "workflow.md", WORKFLOW_CALL,
                        "USE THE SCRIPT API")
        util.run_hook(call_payload(WORKFLOW_SKILL, cwd=self.proj), self.home)
        stats_file = os.path.join(util.state_dir(self.home), HOOK.STATS_FILE_NAME)
        with open(stats_file, encoding="utf-8") as handle:
            stats = json.load(handle)
        entry = stats["rules"][f"{os.path.realpath(self.global_scope)}::workflow.md"]
        self.assertEqual(entry["injections"], 1)
        self.assertEqual(entry["globs"], {WORKFLOW_CALL: 1})
        self.assertEqual(entry["dirs"], {})


class WiringTest(unittest.TestCase):
    """`hooks/hooks.json` mirrors `constants.py` by hand — JSON cannot import
    a Python constant — so a test has to be the thing that keeps them equal."""

    def test_matcher_is_the_five_file_tools_plus_the_call_trigger_tools(self):
        with open(os.path.join(util.PLUGIN_ROOT, "hooks", "hooks.json"),
                  encoding="utf-8") as handle:
            hooks = json.load(handle)["hooks"]
        matcher = hooks["PreToolUse"][0]["matcher"]
        self.assertEqual(set(matcher.split("|")),
                         {"Read", *HOOK.WRITE_TOOL_NAMES, *HOOK.CALL_TRIGGER_TOOLS})


if __name__ == "__main__":
    unittest.main()
