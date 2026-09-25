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

    def test_update_call_empty_string_is_refused_not_silently_cleared(self):
        """W3: `--call ''` used to be filtered out exactly like `--call none`,
        silently wiping the calls of a rule the caller never asked to clear."""
        self.add_call_only()
        proc = self.admin("update", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md",
                          "--call", "", stdin=BODY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--call", proc.stderr)
        self.assertIn("none", proc.stderr)
        content = self.read_rule("OTHR_skill-workflow-authoring.md")
        self.assertIn(f"call: {CALL}", content)

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

    def test_validate_does_not_suggest_block_sync_for_a_call_only_rule(self):
        """W4: `block --sync` writes one native deny per glob; a call-only
        rule has none, so that advice would do nothing — the irrelevant-key
        note already says `block:` has no effect here."""
        util.write_file(os.path.join(self.scope, "OTHR_block.md"),
                        f"---\ncall: {CALL}\nblock: true\n---\n" + BODY + "\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("block --sync", proc.stdout)
        self.assertIn("no effect on this call-only rule", proc.stdout)

    def test_validate_neither_glob_nor_call_is_an_error(self):
        util.write_rule(self.proj, "OTHR_orphan.md", [], BODY)
        proc = self.admin("validate", "--root", self.proj)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no glob and no call", proc.stderr)

    def test_add_call_only_with_exclude_matching_everything_is_not_refused(self):
        """A call-only rule fires on its call no matter what `exclude:` says
        about paths — E1: `exclude: '**'` must not be treated as "can never
        inject" here, only noted as inert."""
        proc = self.add_call_only("--exclude", "**")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("OTHR_skill-workflow-authoring.md")
        self.assertIn(f"call: {CALL}", content)
        self.assertIn("exclude: **", content)

    def test_validate_call_only_with_exclude_everything_is_not_an_error(self):
        util.write_file(os.path.join(self.scope, "OTHR_excl_all.md"),
                        f"---\ncall: {CALL}\nexclude: '**'\n---\n" + BODY + "\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no effect on this call-only rule", proc.stdout)

    def test_validate_glob_and_call_with_every_glob_excluded_is_a_note(self):
        """A glob+call rule whose globs are all excluded still fires on its
        call, so this is advice, not an error."""
        util.write_file(os.path.join(self.scope, "OTHR_dead_glob.md"),
                        f"---\nglob: src/**\ncall: {CALL}\n"
                        f"exclude: src/**\n---\n" + BODY + "\n")
        proc = self.admin("validate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("only fires on its call", proc.stdout)
        self.assertNotIn("can never inject", proc.stdout + proc.stderr)

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

    def test_move_of_a_call_only_rule_suggests_which_call_not_which_path(self):
        """W4: a call-only rule has no glob for `which --path` to probe; the
        reach check printed after the move must exercise the call instead."""
        self.add_call_only()
        proc = self.admin("move", "--root", self.proj,
                          "--rule", "OTHR_skill-workflow-authoring.md", "--to-global")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"which --global --call '{CALL}'", proc.stdout)
        self.assertNotIn("--path", proc.stdout)
