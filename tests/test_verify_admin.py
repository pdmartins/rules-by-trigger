"""The admin CLI's half of `verify:`: writing the key, carrying it through a
rewrite, clearing it, reporting it in an inventory, and the notes `validate`
raises about it. The parser's half is in test_verify_frontmatter.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

BODY = "RULE BODY"
COMMAND = "make test"
OTHER_COMMAND = "ruff check src"


class AdminVerifyTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api",)

    def add(self, *args, stdin=BODY):
        return self.admin("add", "--root", self.proj, "--glob", "src/**",
                          "--rule", "CONV_a.md", *args, stdin=stdin)

    def update(self, *args, stdin=BODY):
        return self.admin("update", "--root", self.proj, "--rule", "CONV_a.md",
                          *args, stdin=stdin)

    def test_add_writes_one_command_inline(self):
        proc = self.add("--verify", COMMAND)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"verify: {COMMAND}", self.read_rule("CONV_a.md"))
        self.assertIn(f"verify: {COMMAND}", proc.stdout)

    def test_add_writes_several_commands_as_a_list(self):
        proc = self.add("--verify", COMMAND, "--verify", OTHER_COMMAND)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"verify:\n  - {COMMAND}\n  - {OTHER_COMMAND}",
                      self.read_rule("CONV_a.md"))

    def test_what_add_writes_is_what_the_hook_reads_back(self):
        self.add("--verify", COMMAND, "--verify", OTHER_COMMAND)
        fields = HOOK.read_rule_file(self.scope, "CONV_a.md")[0]
        self.assertEqual(HOOK.verify_of(fields), [COMMAND, OTHER_COMMAND])

    def test_update_without_the_flag_keeps_the_commands(self):
        self.add("--verify", COMMAND)
        proc = self.update(stdin="NEW BODY")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn(f"verify: {COMMAND}", content)
        self.assertIn("NEW BODY", content)

    def test_update_with_the_flag_replaces_the_commands(self):
        self.add("--verify", COMMAND)
        proc = self.update("--verify", OTHER_COMMAND)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn(f"verify: {OTHER_COMMAND}", content)
        self.assertNotIn(COMMAND, content)

    def test_verify_none_clears_the_key(self):
        self.add("--verify", COMMAND)
        proc = self.update("--verify", HOOK.VERIFY_NONE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("verify", self.read_rule("CONV_a.md"))

    def test_show_update_round_trip_keeps_the_commands(self):
        """The documented way to edit a rule: `show`, edit the body, `update`."""
        self.add("--verify", COMMAND, "--verify", OTHER_COMMAND)
        shown = self.admin("show", "--root", self.proj, "--rule",
                           "CONV_a.md").stdout
        self.assertIn(f"  - {COMMAND}", shown)
        proc = self.update(stdin=shown.replace(BODY, "NEW BODY"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn(f"  - {COMMAND}", content)
        self.assertIn(f"  - {OTHER_COMMAND}", content)

    def test_deleting_the_key_from_the_submitted_frontmatter_removes_it(self):
        self.add("--verify", COMMAND)
        shown = self.admin("show", "--root", self.proj, "--rule",
                           "CONV_a.md").stdout
        stripped = "\n".join(line for line in shown.split("\n")
                             if not line.startswith("verify:"))
        proc = self.update(stdin=stripped)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("verify", self.read_rule("CONV_a.md"))

    def test_list_reports_how_many_commands_not_which(self):
        """An inventory is one line per rule, and a command can be long."""
        self.add("--verify", COMMAND, "--verify", OTHER_COMMAND)
        listing = self.admin("list", "--root", self.proj).stdout
        self.assertIn("verify: 2 cmds", listing)
        self.assertNotIn(COMMAND, listing)

    def test_list_says_one_command_in_the_singular(self):
        self.add("--verify", COMMAND)
        self.assertIn("verify: 1 cmd]", self.admin("list", "--root",
                                                   self.proj).stdout)

    def test_a_command_with_a_newline_is_refused(self):
        """It would smuggle a second frontmatter line past what the command
        confirmed — the same reason a glob may not carry one."""
        proc = self.add("--verify", "make test\nglob: **")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.scope, "CONV_a.md")))

    def test_more_commands_than_the_hook_runs_are_refused(self):
        args = []
        for index in range(HOOK.MAX_VERIFY_COMMANDS + 1):
            args += ["--verify", f"make test{index}"]
        proc = self.add(*args)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("at most", proc.stderr)

    def test_a_command_the_hook_would_drop_is_refused(self):
        proc = self.add("--verify", "x" * (HOOK.MAX_VERIFY_COMMAND_CHARS + 1))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(str(HOOK.MAX_VERIFY_COMMAND_CHARS), proc.stderr)

    def test_a_frontmatter_over_the_window_the_hook_reads_is_refused(self):
        """Globs and commands share one budget: past it the closing `---` is
        outside the window the hook reads, and the rule stops existing."""
        args = []
        for index in range(HOOK.MAX_GLOBS_PER_RULE):
            args += ["--glob", f"src/p{index}/" + "d" * (HOOK.MAX_GLOB_CHARS - 12)]
        for index in range(HOOK.MAX_VERIFY_COMMANDS):
            args += ["--verify",
                     f"cmd{index} " + "a" * (HOOK.MAX_VERIFY_COMMAND_CHARS - 8)]
        proc = self.admin("add", "--root", self.proj, "--rule", "CONV_b.md",
                          *args, stdin=BODY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("window the hook reads", proc.stderr)

    def test_the_flag_is_refused_where_it_would_do_nothing(self):
        proc = self.admin("list", "--root", self.proj, "--verify", COMMAND)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--verify", proc.stderr)


class ValidateVerifyTest(util.SandboxTestCase):
    def validate(self):
        return self.admin("validate", "--root", self.proj)

    def rule(self, *extra, scope=None, name="CONV_a.md", glob="src/**"):
        util.write_rule(scope or self.proj, name, glob, BODY,
                        extra_frontmatter=list(extra))

    def test_a_rule_with_a_verification_validates_cleanly(self):
        self.rule(f"verify: {COMMAND}")
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("verify", proc.stdout)

    def test_the_key_is_not_reported_as_unknown(self):
        self.rule(f"verify: {COMMAND}")
        self.assertNotIn("unknown frontmatter key", self.validate().stdout)

    def test_a_key_with_no_command_is_reported(self):
        """`verify_of` cannot tell this from an absent key, which is exactly
        why `validate` reads the raw field."""
        self.rule("verify:")
        self.assertIn("no command", self.validate().stdout)

    def test_a_blank_command_is_reported_rather_than_silently_dropped(self):
        """A quoted blank survives the parser — `unquote` hands back the
        spaces — so the key looks declared while the hook runs nothing."""
        self.rule('verify: "  "')
        self.assertIn("no command", self.validate().stdout)

    def test_a_blank_among_real_commands_is_reported(self):
        self.rule("verify:", '  - "  "', f"  - {COMMAND}")
        self.assertIn("blank command(s)", self.validate().stdout)

    def test_a_command_the_hook_would_drop_is_reported(self):
        self.rule("verify: " + "x" * (HOOK.MAX_VERIFY_COMMAND_CHARS + 1))
        self.assertIn("are ignored", self.validate().stdout)

    def test_more_commands_than_the_hook_runs_are_reported(self):
        self.rule("verify:", *[f"  - cmd{index}"
                               for index in range(HOOK.MAX_VERIFY_COMMANDS + 2)])
        self.assertIn(f"only the first {HOOK.MAX_VERIFY_COMMANDS}",
                      self.validate().stdout)

    def test_the_off_word_is_not_reported_as_a_bad_command(self):
        self.rule(f"verify: {HOOK.VERIFY_NONE}")
        proc = self.validate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("verify", proc.stdout)

    def test_a_read_only_rule_is_told_its_text_never_arrives(self):
        """The hook ignores `tool:` when it verifies — a write is the trigger —
        so the command still runs; what the filter cancels is the guidance."""
        self.rule(f"verify: {COMMAND}", "tool: read")
        note = self.validate().stdout
        self.assertIn("still runs", note)
        self.assertIn("guidance unread", note)

    def test_a_block_and_a_verification_are_reported_as_pulling_apart(self):
        self.rule(f"verify: {COMMAND}", "block: true",
                  scope=self.home, name="BUSN_a.md", glob="/tmp/**")
        self.assertIn("pull against each other",
                      self.admin("validate", "--global").stdout)

    def test_an_inert_project_block_is_not_claimed_to_stop_the_write(self):
        """A project `block:` never binds (see the ADR), so the verification
        runs — saying otherwise would be a note that is simply false."""
        self.rule(f"verify: {COMMAND}", "block: true")
        self.assertNotIn("pull against each other", self.validate().stdout)


class RewriteVerifyTest(util.SandboxTestCase):
    """`migrate` and `move` re-render a rule whole, so every key `render_rule`
    writes from an argument has to be handed back to it."""

    def test_migrate_keeps_the_key_while_renaming_another(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[f"verify: {COMMAND}",
                                           "remember_after: 30k"])
        proc = self.admin("migrate", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = self.read_rule("CONV_a.md")
        self.assertIn("remember_again_after: 30k", content)
        self.assertIn(f"verify: {COMMAND}", content)

    def test_move_keeps_the_key(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", BODY,
                        extra_frontmatter=[f"verify: {COMMAND}"])
        proc = self.admin("move", "--root", self.proj, "--rule", "CONV_a.md",
                          "--to-global", "--anchor", "any-project")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(self.global_scope, "CONV_a.md"),
                  encoding="utf-8") as handle:
            self.assertIn(f"verify: {COMMAND}", handle.read())


if __name__ == "__main__":
    unittest.main()
