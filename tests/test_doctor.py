"""`doctor`: the setup checks as one command, `--fix` for the deterministic
repairs, `--uninstall` for what the plugin leaves behind."""

import json
import os
import re
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

SETTINGS_RELPATH = os.path.join(".claude", "settings.json")
HARDENING_ENTRIES = [
    "Read(**/.claude/rules-by-trigger/**)",
    "Edit(**/.claude/rules-by-trigger/**)",
    "Read(~/.claude/rules-by-trigger/**)",
    "Edit(~/.claude/rules-by-trigger/**)",
]


class DoctorTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def settings_path(self):
        return os.path.join(self.home, SETTINGS_RELPATH)

    def write_settings(self, data):
        util.write_file(self.settings_path(), json.dumps(data))

    def settings(self):
        with open(self.settings_path(), encoding="utf-8") as handle:
            return json.load(handle)

    def doctor(self, *extra):
        return self.admin("doctor", "--root", self.proj, *extra)

    def test_clean_install_reports_ok_and_the_missing_hardening(self):
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1, "WARN lines are problems")
        self.assertIn("ok    hook smoke test: exit 0, silent", proc.stdout)
        self.assertIn("ok    session notice:", proc.stdout)
        self.assertIn("info  project scope: not created yet", proc.stdout)
        self.assertIn("WARN  hardening: 4 of 4 deny entries missing", proc.stdout)
        self.assertIn("ok    no pre-plugin manual installation", proc.stdout)
        self.assertIn("WARN  setup: not done", proc.stdout)
        self.assertIn("does not exist — fix: ask the user to type "
                      "/rules-by-trigger:config", proc.stdout)
        self.assertIn("1 finding(s) need a human.", proc.stdout)
        self.assertIn("finding(s) need `config --harden`, which edits "
                      "~/.claude/settings.json — ask the user first.", proc.stdout)
        self.assertFalse(os.path.exists(util.state_path(self.home, "rbt-doctor-probe")))

    def test_legacy_map_is_an_error_that_fix_migrates(self):
        util.write_file(os.path.join(self.scope, "rules-map.yml"),
                        "- glob: src/**\n  rule: legacy.md\n")
        util.write_file(os.path.join(self.scope, "rules", "legacy.md"), "LEGACY BODY")
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR project scope: legacy rules-map.yml present", proc.stdout)
        self.assertIn(f"fix: migrate --root '{self.proj}' [--fix applies it]", proc.stdout)
        proc = self.doctor("--fix")
        self.assertIn("applying: migrate --root", proc.stdout)
        self.assertIn("--- after fixes ---", proc.stdout)
        self.assertNotIn("legacy rules-map.yml present", proc.stdout.split("after fixes")[1])
        self.assertFalse(os.path.exists(os.path.join(self.scope, "rules-map.yml")))

    def test_pre_0_4_prefixes_are_a_warning_that_fix_renames(self):
        util.write_rule(self.proj, "Business_no-refunds.md", "src/**", "NO REFUNDS")
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1, "a WARN is a problem")
        self.assertIn("WARN  project scope: 1 rule(s) with a pre-0.4.0 type prefix: "
                      "Business_no-refunds.md", proc.stdout)
        self.doctor("--fix")
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "BUSN_no-refunds.md")))
        proc = self.doctor()
        self.assertIn("ok    project scope: 1 rule(s), current format", proc.stdout)

    def test_untyped_rule_needs_a_human(self):
        # A set-up machine, so the setup finding is not a second manual one.
        util.write_config(self.global_scope, {})
        util.write_rule(self.proj, "no-refunds.md", "src/**", "NO REFUNDS")
        proc = self.doctor()
        self.assertIn("WARN  project scope: no type prefix on no-refunds.md", proc.stdout)
        self.assertIn("[manual]", proc.stdout)
        self.assertIn("1 finding(s) need a human.", proc.stdout)

    def test_fix_leaves_hardening_untouched_and_harden_writes_it(self):
        # A set-up machine, so "nothing to fix." is reachable once hardened.
        util.write_config(self.global_scope, {})
        self.write_settings({"permissions": {"deny": ["Read(**/.env)",
                                                      "Grep(**/.claude/rules-by-trigger/**)"]},
                             "model": "opus"})
        proc = self.doctor()
        self.assertIn("obsolete deny entries", proc.stdout)
        proc = self.doctor("--fix")
        self.assertEqual(proc.returncode, 1, "the obsolete entry is still a WARN")
        data = self.settings()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["permissions"]["deny"],
                         ["Read(**/.env)", "Grep(**/.claude/rules-by-trigger/**)"],
                         "--fix no longer touches the hardening")
        self.assertIn("WARN  hardening: obsolete deny entries", proc.stdout)
        self.assertNotEqual(self.doctor("--harden").returncode, 0)
        proc = self.admin("config", "--harden")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self.settings()
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["permissions"]["deny"],
                         ["Read(**/.env)"] + HARDENING_ENTRIES)
        proc = self.doctor()
        self.assertIn("ok    hardening: all 4 deny entries present", proc.stdout)
        self.assertIn("nothing to fix.", proc.stdout)

    def test_pre_plugin_installation_is_reported(self):
        util.write_file(os.path.join(self.home, ".claude", "hooks", "rules-by-trigger.py"), "#")
        self.write_settings({"hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "command": "python3 ~/.claude/hooks/rules-by-trigger.py"}]}]}})
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ERROR pre-plugin hook still registered", proc.stdout)
        self.assertIn("inject TWICE", proc.stdout)
        self.assertIn("WARN  pre-plugin manual installation left behind", proc.stdout)

    def test_uninstall_removes_deny_entries_and_state_but_keeps_rules(self):
        self.write_settings({"permissions": {"deny": ["Read(**/.env)"] + HARDENING_ENTRIES}})
        util.write_rule(self.proj, "CONV_x.md", "src/**", "KEEP ME")
        util.write_state(self.home, "s1", '{"calls": 1, "injected_rules": {}}')
        proc = self.doctor("--uninstall")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.settings()["permissions"]["deny"], ["Read(**/.env)"])
        self.assertFalse(os.path.exists(util.state_dir(self.home)))
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "CONV_x.md")))
        self.assertIn(f"kept (your rules, 1 file(s)): {self.scope}", proc.stdout)
        self.assertIn("/plugin uninstall rules-by-trigger@pdmartins", proc.stdout)

    def test_fix_and_uninstall_belong_to_doctor(self):
        proc = self.admin("list", "--root", self.proj, "--fix")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--fix", proc.stderr)
        proc = self.doctor("--fix", "--uninstall")
        self.assertNotEqual(proc.returncode, 0)


class DoctorHealthTest(util.SandboxTestCase):
    """FR-015: one `ok` line per check, exit 0 when all is well, non-zero and
    the rule named when the validator finds an error."""

    PROJECT_SUBDIRS = ("src",)

    def doctor(self, *extra):
        return self.admin("doctor", "--root", self.proj, *extra)

    def harden_a_set_up_machine(self):
        util.write_config(self.global_scope, {})
        util.write_file(os.path.join(self.home, SETTINGS_RELPATH),
                        json.dumps({"permissions": {"deny": HARDENING_ENTRIES}}))

    def test_healthy_setup_has_an_ok_line_per_check_and_exits_zero(self):
        self.harden_a_set_up_machine()
        util.write_rule(self.proj, "CONV_x.md", "src/**", "X")
        proc = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for check in ("python ", "plugin version ", "hook launcher present",
                      "hook smoke test", "session notice", "global scope:",
                      "project scope: 1 rule(s), current format",
                      "hardening: all 4 deny entries present",
                      "no pre-plugin manual installation", "setup: done"):
            self.assertRegex(proc.stdout, rf"(?m)^ok    {re.escape(check)}", check)
        self.assertNotRegex(proc.stdout, r"(?m)^(WARN|ERROR)")
        self.assertIn("nothing to fix.", proc.stdout)

    def write_deny(self, entries):
        util.write_file(os.path.join(self.home, SETTINGS_RELPATH),
                        json.dumps({"permissions": {"deny": entries}}))

    def test_hardening_none_present_on_a_set_up_machine_is_info_and_exits_zero(self):
        util.write_config(self.global_scope, {})
        proc = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^info  hardening: not applied in .*"
                         r"\(declined at setup or never applied\); `config --harden` applies it")
        self.assertNotRegex(proc.stdout, r"(?m)^(WARN|ERROR)")

    def test_invalid_settings_json_is_a_warn_and_the_preview_says_it_is_blocked(self):
        util.write_config(self.global_scope, {})
        util.write_file(os.path.join(self.home, SETTINGS_RELPATH), '{"permissions": [')
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^WARN  hardening: .*it is not valid JSON\), "
                         r"so the hardening state is unknown")
        self.assertNotRegex(proc.stdout, r"(?m)^info  hardening")
        self.assertRegex(proc.stdout, r"cannot remove lines from .*settings\.json: "
                         r"it is not valid JSON; fix it by hand first")

    def test_the_state_line_counts_the_folders_uninstall_removes(self):
        self.harden_a_set_up_machine()
        util.write_state(self.home, "s1", "{}")
        temp = os.path.join(self.tmp.name, "tmp", "rules-by-trigger-state-" + str(os.getuid()))
        util.write_file(os.path.join(temp, "a.json"), "{}")
        util.write_file(os.path.join(temp, "b.json"), "{}")
        plugin_data = os.path.join(self.tmp.name, "plugin-data")
        util.write_file(os.path.join(plugin_data, "state", "c.json"), "{}")
        proc = self.admin("doctor", "--root", self.proj, env={
            "TMPDIR": os.path.dirname(temp), "CLAUDE_PLUGIN_DATA": plugin_data})
        line = [row for row in proc.stdout.splitlines() if "cached session state" in row][0]
        self.assertIn(f"{util.state_dir(self.home)}, {temp}", line)
        self.assertNotIn(plugin_data, line)
        self.assertIn("3 file(s)", line)

    def test_a_symlinked_state_folder_is_shown_as_refused_and_not_counted(self):
        self.harden_a_set_up_machine()
        elsewhere = os.path.join(self.tmp.name, "elsewhere")
        util.write_file(os.path.join(elsewhere, "mine.txt"), "x")
        util.write_file(os.path.join(elsewhere, "also.txt"), "x")
        temp_root = os.path.join(self.tmp.name, "tmp")
        os.makedirs(temp_root)
        link = os.path.join(temp_root, "rules-by-trigger-state-" + str(os.getuid()))
        os.symlink(elsewhere, link)
        proc = self.admin("doctor", "--root", self.proj, env={"TMPDIR": temp_root})
        line = [row for row in proc.stdout.splitlines() if "cached session state" in row][0]
        self.assertIn("0 file(s) in", line)
        self.assertIn(f"not counted, `--uninstall` refuses {link}: it is a symlink", line)
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_a_symlinked_settings_file_gets_a_hint_that_fits(self):
        util.write_config(self.global_scope, {})
        target = os.path.join(self.tmp.name, "real-settings.json")
        util.write_file(target, "{}")
        os.makedirs(os.path.join(self.home, ".claude"), exist_ok=True)
        os.symlink(target, os.path.join(self.home, SETTINGS_RELPATH))
        proc = self.doctor()
        self.assertRegex(proc.stdout, r"(?m)^WARN  hardening: .*it is a symlink\), "
                         r"so the hardening state is unknown — fix: repair or replace the file")
        self.assertNotIn("fix the JSON", proc.stdout)

    def test_hardening_all_present_is_ok(self):
        self.harden_a_set_up_machine()
        proc = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^ok    hardening: all 4 deny entries present")

    def test_hardening_partly_present_is_a_warn_and_exits_non_zero(self):
        util.write_config(self.global_scope, {})
        self.write_deny(HARDENING_ENTRIES[:2])
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^WARN  hardening: 2 of 4 deny entries missing")

    def test_hardening_missing_without_the_setup_stays_a_warn(self):
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^WARN  hardening: 4 of 4 deny entries missing")
        self.assertRegex(proc.stdout, r"(?m)^WARN  setup: not done")

    def test_any_warn_makes_the_exit_non_zero(self):
        self.harden_a_set_up_machine()
        util.write_rule(self.proj, "no-type.md", "src/**", "X")
        proc = self.doctor()
        self.assertRegex(proc.stdout, r"(?m)^WARN  project scope: no type prefix")
        self.assertNotRegex(proc.stdout, r"(?m)^ERROR")
        self.assertEqual(proc.returncode, 1)

    def test_a_rule_with_a_validator_error_is_listed_and_the_exit_is_not_zero(self):
        self.harden_a_set_up_machine()
        util.write_file(os.path.join(self.scope, "CONV_bad.md"),
                        "---\ntype: note\n---\nBODY\n")
        proc = self.doctor()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertRegex(proc.stdout, r"(?m)^ERROR project scope: .*CONV_bad\.md")

    def test_plain_doctor_lists_what_uninstall_would_do_without_doing_it(self):
        self.harden_a_set_up_machine()
        util.write_state(self.home, "s1", "{}")
        util.write_rule(self.proj, "CONV_x.md", "src/**", "KEEP",
                        extra_frontmatter=["block: true"])
        self.assertEqual(self.admin("block", "--sync", "--root", self.proj).returncode, 0)
        before = self.snapshot()
        proc = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        listing = proc.stdout.split("`doctor --uninstall` would remove")[1]
        self.assertIn("4 deny line(s) in " + os.path.join(self.home, SETTINGS_RELPATH), listing)
        self.assertIn("state folder: " + util.state_dir(self.home), listing)
        self.assertIn("--- and would keep ---", listing)
        self.assertIn(f"{self.scope} (1 file(s))", listing)
        self.assertIn("  Edit(src/**)", listing)
        self.assertIn("then run /plugin uninstall rules-by-trigger@pdmartins", listing)
        self.assertEqual(self.snapshot(), before)

    def snapshot(self):
        """Every file under HOME and the project, with its bytes."""
        found = {}
        for base in (self.home, self.proj):
            for folder, _dirs, names in os.walk(base):
                for name in names:
                    path = os.path.join(folder, name)
                    with open(path, "rb") as handle:
                        found[path] = handle.read()
        return found


class UninstallTest(util.SandboxTestCase):
    """FR-016: `doctor --uninstall` clears what is the user's and keeps the
    rules and the project's `block --sync` lines."""

    PROJECT_SUBDIRS = ("src",)

    def setUp(self):
        super().setUp()
        self.temp_state = os.path.join(self.tmp.name, "tmp", "rules-by-trigger-state-" + str(os.getuid()))
        self.settings_path = os.path.join(self.home, SETTINGS_RELPATH)
        self.project_settings = os.path.join(self.proj, SETTINGS_RELPATH)

    def uninstall(self):
        return self.admin("doctor", "--root", self.proj, "--uninstall",
                          env={"TMPDIR": os.path.dirname(self.temp_state)})

    def read_bytes(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def test_removes_lines_and_state_folders_and_lists_the_project_lines(self):
        util.write_file(self.settings_path, json.dumps(
            {"model": "opus", "permissions": {"deny": ["Read(**/.env)"] + HARDENING_ENTRIES}}))
        util.write_state(self.home, "s1", "{}")
        util.write_file(os.path.join(self.temp_state, "s2.json"), "{}")
        util.write_rule(self.proj, "CONV_x.md", "src/**", "KEEP",
                        extra_frontmatter=["block: true"])
        self.assertEqual(self.admin("block", "--sync", "--root", self.proj).returncode, 0)
        project_before = self.read_bytes(self.project_settings)
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(self.settings_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data, {"model": "opus", "permissions": {"deny": ["Read(**/.env)"]}})
        self.assertFalse(os.path.exists(util.state_dir(self.home)))
        self.assertFalse(os.path.exists(self.temp_state))
        self.assertIn("  Edit(src/**)", proc.stdout)
        self.assertIn("a line identical to one of them added by hand", proc.stdout)
        self.assertIn("/plugin uninstall rules-by-trigger@pdmartins", proc.stdout)
        self.assertEqual(self.read_bytes(self.project_settings), project_before)
        self.assertTrue(os.path.isfile(os.path.join(self.scope, "CONV_x.md")))

    def test_the_four_lines_leave_and_the_hand_written_equivalents_stay_listed(self):
        keep = ["Read(**/.env)", "Grep(**/secrets/**)",
                "Grep(**/.claude/rules-by-trigger/**)",
                "Write(**/.claude/rules-by-trigger/**)"]
        equivalents = ["Read(//**/.claude/rules-by-trigger/**)",
                       "Edit(//**/.claude/rules-by-trigger/**)"]
        deny = keep[:1] + HARDENING_ENTRIES + keep[1:2] + equivalents + keep[2:]
        util.write_file(self.settings_path, json.dumps({"permissions": {"deny": deny}}))
        env = {"TMPDIR": os.path.dirname(self.temp_state)}
        preview = self.admin("doctor", "--root", self.proj, env=env).stdout
        preview = preview.split("`doctor --uninstall` would remove")[1]
        self.assertIn("4 deny line(s)", preview)
        self.assertIn("written by hand, recognised as protection", preview)
        for entry in equivalents:
            self.assertIn("  " + entry, preview)
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("kept (written by hand, recognised as protection", proc.stdout)
        for entry in equivalents:
            self.assertIn("  " + entry, proc.stdout)
        with open(self.settings_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["permissions"]["deny"],
                             keep[:1] + keep[1:2] + equivalents + keep[2:])

    def test_a_state_folder_that_is_not_safe_is_refused_warned_and_exits_non_zero(self):
        util.write_state(self.home, "s1", "{}")
        util.write_file(os.path.join(self.temp_state, "s2.json"), "{}")
        os.chmod(self.temp_state, 0o777)
        env = {"TMPDIR": os.path.dirname(self.temp_state)}
        preview = self.admin("doctor", "--root", self.proj, env=env).stdout
        self.assertIn(f"cannot remove {self.temp_state}: it is not owned by you, "
                      f"or anyone can write to it", preview)
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"not removing {self.temp_state}", proc.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.temp_state, "s2.json")))
        self.assertFalse(os.path.exists(util.state_dir(self.home)), "the others go on")
        self.assertIn("/plugin uninstall rules-by-trigger@pdmartins", proc.stdout)

    def test_a_malformed_project_deny_list_never_stops_the_listing(self):
        util.write_rule(self.proj, "CONV_x.md", "src/**", "KEEP",
                        extra_frontmatter=["block: true"])
        util.write_file(self.project_settings, json.dumps(
            {"permissions": {"deny": [{"x": 1}, 7, "Edit(src/**)"]}}))
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("  Edit(src/**)", proc.stdout)
        self.assertIn("/plugin uninstall rules-by-trigger@pdmartins", proc.stdout)
        util.write_file(self.project_settings, json.dumps({"permissions": ["x"]}))
        self.assertEqual(self.uninstall().returncode, 0)
        plain = self.admin("doctor", "--root", self.proj,
                           env={"TMPDIR": os.path.dirname(self.temp_state)})
        self.assertNotIn("Traceback", plain.stderr)
        self.assertIn("then run /plugin uninstall", plain.stdout)

    def test_invalid_settings_json_is_left_byte_for_byte_and_the_folders_go(self):
        util.write_file(self.settings_path, '{"permissions": {"deny": [')
        before = self.read_bytes(self.settings_path)
        util.write_state(self.home, "s1", "{}")
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 1, "the clean-up stays incomplete")
        self.assertEqual(self.read_bytes(self.settings_path), before)
        self.assertFalse(os.path.exists(util.state_dir(self.home)))
        self.assertIn("it is not valid JSON", proc.stderr)
        self.assertNotIn("--sync", proc.stderr)
        self.assertIn("/plugin uninstall rules-by-trigger@pdmartins", proc.stdout)

    def test_no_settings_file_goes_on_with_the_folders(self):
        util.write_state(self.home, "s1", "{}")
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to remove there", proc.stdout)
        self.assertFalse(os.path.exists(util.state_dir(self.home)))
        self.assertFalse(os.path.exists(self.settings_path))

    def test_running_it_twice_changes_nothing_more(self):
        util.write_file(self.settings_path, json.dumps({"permissions": {"deny": HARDENING_ENTRIES}}))
        self.assertEqual(self.uninstall().returncode, 0)
        after_first = self.read_bytes(self.settings_path)
        proc = self.uninstall()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.read_bytes(self.settings_path), after_first)

    def test_a_folder_that_cannot_be_removed_warns_goes_on_and_exits_non_zero(self):
        # Run in-process with `rmtree` refusing one folder: a chmod would not
        # stop a root user, so the refusal is simulated instead.
        sys.path.insert(0, os.path.join(util.PLUGIN_ROOT, "scripts"))
        from rules_by_trigger_admin import uninstall
        util.write_state(self.home, "s1", "{}")
        util.write_file(os.path.join(self.temp_state, "s2.json"), "{}")
        real_rmtree = uninstall.shutil.rmtree

        def refuse_cache(path, *args, **kwargs):
            if path == util.state_dir(self.home):
                raise PermissionError(13, "Permission denied", path)
            return real_rmtree(path, *args, **kwargs)

        args = SimpleNamespace(use_global=True, root=None)
        with mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home}), \
                mock.patch.object(uninstall.tempfile, "gettempdir",
                                  return_value=os.path.dirname(self.temp_state)), \
                mock.patch.object(uninstall.shutil, "rmtree", side_effect=refuse_cache), \
                mock.patch.object(uninstall, "warn") as warned, \
                mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as raised:
                uninstall.cmd_uninstall(args)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("Permission denied", warned.call_args[0][0])
        self.assertTrue(os.path.isdir(util.state_dir(self.home)))
        self.assertFalse(os.path.exists(self.temp_state))


if __name__ == "__main__":
    unittest.main()
