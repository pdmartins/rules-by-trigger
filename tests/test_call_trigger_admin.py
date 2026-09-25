"""The admin CLI's half of the `call:` trigger: writing a call-only rule,
carrying calls through `update`/`move`, reporting them in `list`/`which`, and
the errors `validate` raises for a bad one. The hook's half is in
test_call_trigger.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

BODY = "Load the workflow-authoring skill before writing a Workflow script."
CALL = "Skill(skill=workflow-authoring)"
OTHER_CALL = "Skill(skill=some-other-skill)"


class CallTriggerAdminTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def add_call_only(self, *args, stdin=BODY, call=CALL):
        return self.admin("add", "--root", self.proj, "--call", call,
                          "--type", "OTHR", *args, stdin=stdin)

    # ---- add ----------------------------------------------------------

    def test_add_call_only_derives_a_name_and_writes_no_bare_glob(self):
        proc = self.add_call_only()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OTHR_skill-workflow-authoring.md", proc.stdout)
        content = self.read_rule("OTHR_skill-workflow-authoring.md")
        self.assertIn(f"call: {CALL}", content)
        self.assertNotIn("glob:", content)
        self.assertIn(BODY, content)

    def test_add_with_neither_glob_nor_call_fails(self):
        proc = self.admin("add", "--root", self.proj, "--type", "OTHR",
                          "--rule", "OTHR_nothing.md", stdin=BODY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--glob or --call", proc.stderr)

    def test_add_with_bad_grammar_fails(self):
        proc = self.add_call_only(call="Skill")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Tool(field=value)", proc.stderr)

    def test_add_with_an_unregistered_tool_fails_naming_skill(self):
        proc = self.add_call_only(call="Bash(command=ls)")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Skill", proc.stderr)

    # ---- show -> update round trip ------------------------------------

    def test_show_update_round_trip_keeps_frontmatter_out_of_the_body(self):
        self.add_call_only()
        shown = self.admin("show", "--root", self.proj,
                           "--rule", "OTHR_skill-workflow-authoring.md").stdout
        proc = self.admin("update", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md",
                          stdin=shown.replace(BODY, "NEW BODY"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("OTHR_skill-workflow-authoring.md")
        self.assertEqual(content.count("---"), 2)
        self.assertIn(f"call: {CALL}", content)
        self.assertIn("NEW BODY", content)

    def test_update_without_flags_keeps_the_call(self):
        self.add_call_only()
        proc = self.admin("update", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md",
                          stdin="UPDATED BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("OTHR_skill-workflow-authoring.md")
        self.assertIn(f"call: {CALL}", content)
        self.assertIn("UPDATED BODY", content)

    def test_update_call_replaces_it(self):
        self.add_call_only()
        proc = self.admin("update", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md",
                          "--call", OTHER_CALL, stdin=BODY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("OTHR_skill-workflow-authoring.md")
        self.assertIn(f"call: {OTHER_CALL}", content)
        self.assertNotIn(CALL, content)

    def test_update_call_none_drops_calls_when_a_glob_remains(self):
        self.admin("add", "--root", self.proj, "--glob", "src/**",
                  "--call", CALL, "--rule", "OTHR_both.md", stdin=BODY)
        proc = self.admin("update", "--root", self.proj, "--rule", "OTHR_both.md",
                          "--call", "none", stdin=BODY)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("OTHR_both.md")
        self.assertNotIn("call:", content)
        self.assertIn("glob: src/**", content)

    def test_update_call_none_refused_on_a_call_only_rule(self):
        self.add_call_only()
        proc = self.admin("update", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md",
                          "--call", "none", stdin=BODY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no glob and no call", proc.stderr)

    # ---- list -----------------------------------------------------------

    def test_list_shows_the_call(self):
        self.add_call_only()
        out = self.admin("list", "--root", self.proj).stdout
        self.assertIn("OTHR_skill-workflow-authoring.md  <-  " + CALL, out)

    # ---- which ------------------------------------------------------------

    def test_which_call_matches(self):
        self.add_call_only()
        proc = self.admin("which", "--root", self.proj, "--call", CALL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("match: rule OTHR_skill-workflow-authoring.md", proc.stdout)

    def test_which_call_no_match(self):
        self.add_call_only()
        proc = self.admin("which", "--root", self.proj, "--call", OTHER_CALL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no rule injects for call", proc.stdout)

    # ---- validate ---------------------------------------------------------

    def test_validate_call_only_rule_has_no_error(self):
        self.add_call_only()
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_validate_bad_grammar_is_an_error(self):
        util.write_file(os.path.join(self.scope, "OTHR_bad.md"),
                        "---\ncall: Skill\n---\n" + BODY + "\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Tool(field=value)", proc.stderr)

    def test_validate_unregistered_tool_is_an_error(self):
        util.write_file(os.path.join(self.scope, "OTHR_bash.md"),
                        "---\ncall: Bash(command=ls)\n---\n" + BODY + "\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Skill", proc.stderr)

    def test_validate_notes_exclude_on_a_call_only_rule(self):
        util.write_file(os.path.join(self.scope, "OTHR_excl.md"),
                        f"---\ncall: {CALL}\nexclude: src/**\n---\n" + BODY + "\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no effect on this call-only rule", proc.stdout)

    def test_validate_neither_glob_nor_call_is_an_error(self):
        util.write_rule(self.proj, "OTHR_orphan.md", [], BODY)
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no glob and no call", proc.stderr)

    # ---- move ---------------------------------------------------------

    def test_move_keeps_the_call(self):
        self.add_call_only()
        proc = self.admin("move", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md", "--to-global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(self.global_scope,
                               "OTHR_skill-workflow-authoring.md"),
                 encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn(f"call: {CALL}", content)
