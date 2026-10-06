"""A rule that says `enabled: false` is left alone by everything that acts on a
rule: injection by glob and by skill, `verify:`, the global block, `which`; and
the validator reports a value that is not a boolean (spec 0008, FR-005)."""

import json
import os
import shlex
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from disabledutil import BODY, DISABLED, SKILL, SKILL_CALL, DisabledSandbox  # noqa: E402


def verify_marker_command():
    source = 'open("marker.txt", "a").write("x")'
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def skill_payload():
    return {"session_id": "s1", "cwd": "/tmp", "tool_name": "Skill",
            "tool_input": {"skill": SKILL}, "hook_event_name": "PreToolUse"}


class HookIgnoresDisabledRulesTest(DisabledSandbox):
    def test_a_disabled_rule_is_not_injected_by_glob(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[DISABLED])
        self.assertIsNone(self.inject("src/a.py"))

    def test_the_same_rule_active_is_injected(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["enabled: true"])
        self.assertIn(BODY, self.inject("src/a.py"))

    def test_a_disabled_rule_is_not_injected_by_skill(self):
        util.write_file(os.path.join(self.scope, "CONV_skill.md"),
                        f"---\ncall: {SKILL_CALL}\n{DISABLED}\n---\n{BODY}\n")
        payload = dict(skill_payload(), cwd=self.proj)
        self.assertIsNone(util.injected_text(util.run_hook(payload, self.home)))
        self.admin("update", "--root", self.proj, "--rule", "CONV_skill.md",
                   "--enable")
        self.assertIn(BODY, util.injected_text(util.run_hook(payload, self.home)))

    def test_a_disabled_rule_runs_no_verify(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[DISABLED,
                                           f"verify: {verify_marker_command()}"])
        util.write_state(self.home, "s1", json.dumps({
            "calls": 1, "injected_rules": {},
            "unverified_writes": [os.path.join(self.proj, "src", "a.py")]}))
        payload = {"session_id": "s1", "cwd": self.proj, "hook_event_name": "Stop",
                   "stop_hook_active": False}
        proc = util.run_hook(payload, self.home, args=("--verify",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.proj, "marker.txt")))

    def test_a_disabled_global_block_rule_stops_blocking(self):
        glob = f"{self.proj}/src/**".replace(os.sep, "/")
        util.write_rule(self.home, "BUSN_no.md", glob, BODY,
                        extra_frontmatter=["block: true"])
        blocked = self.hook_for("src/a.py", tool="Write")
        self.assertEqual(util.hook_specific_output(blocked)["permissionDecision"],
                         "deny")
        util.write_rule(self.home, "BUSN_no.md", glob, BODY,
                        extra_frontmatter=["block: true", DISABLED])
        proc = self.hook_for("src/a.py", session="s2", tool="Write")
        self.assertNotIn("permissionDecision", util.hook_specific_output(proc))

    def test_only_the_word_false_disables(self):
        for index, value in enumerate(("maybe", "no", "0", "", "False ")):
            util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                            extra_frontmatter=[f"enabled: {value}"])
            text = self.inject("src/a.py", session=f"s{index}")
            if value.strip().lower() == "false":
                self.assertIsNone(text, repr(value))
            else:
                self.assertIn(BODY, text, repr(value))

    def test_a_list_value_leaves_the_rule_active(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["enabled:", "  - false"])
        self.assertIn(BODY, self.inject("src/a.py"))


class ValidatorAndWhichTest(DisabledSandbox):
    def test_the_validator_flags_a_non_boolean_enabled(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["enabled: maybe"])
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("enabled", proc.stderr)
        self.assertIn("boolean", proc.stderr)

    def test_the_validator_flags_a_list_value(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=["enabled:", "  - false"])
        self.assertNotEqual(self.admin("validate", "--root", self.proj).returncode, 0)

    def test_the_validator_accepts_true_and_false(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[DISABLED])
        util.write_rule(self.proj, "CONV_b.md", "lib/**", BODY,
                        extra_frontmatter=["enabled: true"])
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_which_never_reports_a_disabled_rule(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[DISABLED])
        path = os.path.join(self.proj, "src", "a.py")
        proc = self.admin("which", "--root", self.proj, "--path", path)
        self.assertNotIn("CONV_a.md", proc.stdout)
        self.admin("update", "--root", self.proj, "--rule", "CONV_a.md", "--enable")
        proc = self.admin("which", "--root", self.proj, "--path", path)
        self.assertIn("CONV_a.md", proc.stdout)


if __name__ == "__main__":
    unittest.main()
