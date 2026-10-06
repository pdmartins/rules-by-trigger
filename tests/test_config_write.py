"""`config <key> <value>` (spec 0008, FR-007): every key the reader knows is
written through the reader's own validations and reads back the same, in the
global and in the project layer; what the reader would clamp or drop is
refused, and the file is left as it was."""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()
NEW_TYPE = {"prefix": "DOCS", "name": "Docs", "purpose": "How the docs read"}
OTHER_KEY = {"language": "en"}
NOT_ROOT_OVER_CEILING = HOOK.MAX_RULE_CHARS + 1

# (the key path, the value as typed, the key of the layer it lands in, what the
# reader makes of that key in the layer). Every one is written, then read back.
BOTH_LAYERS = [
    ("language", "pt-BR", "language", "pt-BR"),
    ("rule_types", json.dumps([NEW_TYPE]), "rule_types", [NEW_TYPE]),
    ("rule_types.DOCS", json.dumps({"name": "Docs", "purpose": "How the docs read"}),
     "rule_types", None),
    ("remember_again_after.tokens", "30k", "remember_again_after",
     {"tokens": "30k"}),
    ("remember_again_after.calls", "25 calls", "remember_again_after",
     {"calls": "25 calls"}),
    ("rule_size.max_chars", "3000", "rule_size", {"max_chars": 3000}),
    ("rule_size.warn_chars", "1500", "rule_size", {"warn_chars": 1500}),
    ("reinject_budget", "5", "reinject_budget", 5),
    ("show_injections", "true", "show_injections", True),
]


class ConfigWriteTestCase(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def setUp(self):
        super().setUp()
        util.write_config(self.global_scope, {})  # the machine is set up

    def start_from_a_set_up_machine(self):
        """No layer holds anything but the record of the setup."""
        for path in (self.global_path(), self.project_path()):
            if os.path.exists(path):
                os.unlink(path)
        util.write_config(self.global_scope, {})

    def global_path(self):
        return os.path.join(self.global_scope, "config.json")

    def project_path(self):
        return os.path.join(self.scope, "config.json")

    def write(self, layer, key, value):
        scope = ("--global",) if layer == "global" else ("--root", self.proj)
        return self.admin("config", *scope, key, value)

    def layer(self, layer):
        """The layer as the reader sees it."""
        path = self.global_path() if layer == "global" else self.project_path()
        return HOOK.load_layer(path, layer == "global")

    def raw(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def assert_refused(self, layer, key, value, *mentions):
        path = self.global_path() if layer == "global" else self.project_path()
        before = self.raw(path) if os.path.exists(path) else None
        proc = self.write(layer, key, value)
        self.assertNotEqual(proc.returncode, 0, (key, value))
        for text in mentions:
            self.assertIn(text, proc.stderr)
        after = self.raw(path) if os.path.exists(path) else None
        self.assertEqual(before, after, "the file must stay as it was")
        return proc


class EveryKeyReadsBackTest(ConfigWriteTestCase):

    def test_every_key_path_reads_back_in_both_layers(self):
        for layer in ("global", "project"):
            for key, value, top, expected in BOTH_LAYERS:
                with self.subTest(layer=layer, key=key):
                    self.start_from_a_set_up_machine()
                    proc = self.write(layer, key, value)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    read = self.layer(layer)[top]
                    if key == "rule_types.DOCS":
                        self.assertEqual(read[-1], NEW_TYPE)
                        self.assertEqual(len(read), len(HOOK.rule_types(
                            HOOK.load_config())) + 1)
                    else:
                        self.assertEqual(read, expected)

    def test_a_type_field_writes_the_list_in_force_with_that_one_change(self):
        for layer in ("global", "project"):
            with self.subTest(layer=layer):
                self.start_from_a_set_up_machine()
                proc = self.write(layer, "rule_types.busn.remember_again_after", "15k")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                shipped = HOOK.rule_types(HOOK.load_config())
                written = self.layer(layer)["rule_types"]
                self.assertEqual([t["prefix"] for t in written],
                                 [t["prefix"] for t in shipped])
                self.assertEqual(written[0]["remember_again_after"], "15k")
                self.assertEqual(written[1:], shipped[1:])
                for field in ("name", "purpose"):
                    proc = self.write(layer, f"rule_types.CONV.{field}", "Style")
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(self.layer(layer)["rule_types"][2][field], "Style")

    def test_the_project_list_in_force_starts_from_the_global_one(self):
        self.write("global", "rule_types", json.dumps([NEW_TYPE]))
        proc = self.write("project", "rule_types.DOCS.remember_again_after", "20k")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.layer("project")["rule_types"],
                         [{**NEW_TYPE, "remember_again_after": "20k"}])

    def test_the_effective_config_shows_what_was_written(self):
        self.write("global", "rule_size.max_chars", "3000")
        self.write("project", "rule_size.warn_chars", "1000")
        self.write("project", "language", "pt-BR")
        config = HOOK.load_config([self.global_scope, self.scope], 1)
        self.assertEqual((HOOK.max_rule_chars(config), HOOK.warn_rule_chars(config),
                          HOOK.language(config)), (3000, 1000, "pt-BR"))

    def test_the_trusted_layer_goes_past_what_a_project_may(self):
        self.assertEqual(self.write("global", "rule_size.max_chars",
                                    str(NOT_ROOT_OVER_CEILING)).returncode, 0)
        self.assertEqual(self.layer("global")["rule_size"],
                         {"max_chars": NOT_ROOT_OVER_CEILING})
        self.assertEqual(self.write("global", "remember_again_after.calls",
                                    "1 calls").returncode, 0)
        self.assertEqual(self.write("global", "show_injections", "false").returncode, 0)
        self.assertIs(self.layer("global")["show_injections"], False)

    def test_a_json_object_writes_a_whole_small_object(self):
        proc = self.write("global", "rule_size", '{"max_chars": 3000, "warn_chars": 1000}')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.layer("global")["rule_size"],
                         {"max_chars": 3000, "warn_chars": 1000})

    def test_a_type_is_renamed_and_removed_through_the_list(self):
        self.write("global", "rule_types", json.dumps([NEW_TYPE, {
            "prefix": "OLD", "name": "Old", "purpose": "To be renamed"}]))
        proc = self.write("global", "rule_types.OLD", json.dumps(
            {"prefix": "NEWER", "name": "Old", "purpose": "To be renamed"}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual([t["prefix"] for t in self.layer("global")["rule_types"]],
                         ["DOCS", "NEWER"])
        proc = self.write("global", "rule_types", json.dumps([NEW_TYPE]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.layer("global")["rule_types"], [NEW_TYPE])

    def test_every_other_key_of_the_file_rides_through(self):
        util.write_config(self.global_scope, {"_comment": ["mine"], **OTHER_KEY})
        self.assertEqual(self.write("global", "reinject_budget", "7").returncode, 0)
        with open(self.global_path(), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"_comment": ["mine"], **OTHER_KEY,
                                                 "reinject_budget": 7})

    def test_the_same_command_again_gives_the_same_file(self):
        self.write("project", "rule_size.max_chars", "3000")
        first = self.raw(self.project_path())
        self.assertEqual(self.write("project", "rule_size.max_chars", "3000").returncode, 0)
        self.assertEqual(self.raw(self.project_path()), first)


class ShowConfigTest(ConfigWriteTestCase):
    """`config` with no key shows every key in force, and where it came from."""

    def show(self):
        proc = self.admin("config", "--root", self.proj)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_every_key_is_shown_with_its_value_and_source(self):
        out = self.show()
        for heading in ("rule types:", "remember_again_after defaults",
                        "rule size:", "language:", "reinject_budget:",
                        "show_injections:"):
            self.assertIn(heading, out)
        self.assertIn(f"  {HOOK.reinject_budget(HOOK.load_config())}  —", out)
        self.assertIn("  true  —", out)

    def test_a_written_value_is_what_is_shown_and_its_layer_is_the_source(self):
        self.write("global", "reinject_budget", "7")
        self.write("global", "show_injections", "false")
        self.write("project", "reinject_budget", "9")
        out = self.show()
        self.assertIn("  9  — ", out)
        self.assertIn(f"(from {self.project_path()})", out)
        self.assertIn("  false  — ", out)
        self.assertIn(f"(from {self.global_path()})", out)


class ReaderStaysSilentTest(ConfigWriteTestCase):

    def test_the_reader_says_nothing_of_a_soft_limit_above_the_hard_cut(self):
        """The refusal belongs to the writer: a layer that already holds the
        pair is read as it always was, with nothing on stderr."""
        util.write_config(self.global_scope,
                          {"rule_size": {"max_chars": 1000, "warn_chars": 1500}})
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            layer = HOOK.load_layer(self.global_path(), True)
        self.assertEqual(layer["rule_size"], {"max_chars": 1000, "warn_chars": 1000})
        self.assertEqual(captured.getvalue(), "")


class ProjectLocksTest(ConfigWriteTestCase):

    def test_raising_max_chars_in_a_project_is_refused_and_the_file_is_unchanged(self):
        util.write_config(self.scope, {"rule_size": {"max_chars": 3000}})
        before = self.raw(self.project_path())
        proc = self.write("project", "rule_size.max_chars", str(NOT_ROOT_OVER_CEILING))
        self.assertNotEqual(proc.returncode, 0)
        for text in ("rule_size.max_chars", str(NOT_ROOT_OVER_CEILING),
                     f"-{HOOK.MAX_RULE_CHARS}"):
            self.assertIn(text, proc.stderr)
        self.assertEqual(self.raw(self.project_path()), before)

    def test_a_refusal_does_not_create_the_file(self):
        self.assert_refused("project", "rule_size.max_chars",
                            str(NOT_ROOT_OVER_CEILING), str(HOOK.MAX_RULE_CHARS))
        self.assertFalse(os.path.exists(self.project_path()))
        self.assertFalse(os.path.isdir(self.scope))

    def test_a_project_cannot_turn_the_injection_notice_off(self):
        self.assert_refused("project", "show_injections", "false", "false")
        self.assertEqual(self.write("project", "show_injections", "true").returncode, 0)

    def test_a_project_call_interval_under_the_floor_is_refused(self):
        self.assert_refused("project", "remember_again_after.calls", "1 calls",
                            str(HOOK.MIN_REMEMBER_AGAIN_CALLS))
        self.assert_refused("project", "rule_types.BUSN.remember_again_after",
                            "2 calls", str(HOOK.MIN_REMEMBER_AGAIN_CALLS))

    def test_a_soft_limit_over_the_hard_cut_is_refused(self):
        self.write("project", "rule_size.max_chars", "1000")
        self.assert_refused("project", "rule_size.warn_chars", "1500", "1000")

    def test_a_value_outside_the_range_is_refused_in_both_layers(self):
        for layer in ("global", "project"):
            self.assert_refused(layer, "rule_size.max_chars", "10", "200")
            self.assert_refused(layer, "reinject_budget", "999",
                                str(HOOK.MAX_CONFIGURABLE_REINJECT_BUDGET))


class RefusalsTest(ConfigWriteTestCase):

    def test_values_the_reader_would_not_take_are_refused(self):
        cases = [
            ("language", "en\\nx"), ("language", "123"),
            ("rule_size.max_chars", "many"), ("rule_size.max_chars", "3000.7"),
            ("rule_size.max_chars", "true"), ("rule_size.max_chars", '"3000"'),
            ("reinject_budget", "true"), ("reinject_budget", "-1"),
            ("show_injections", "yes"), ("show_injections", "1"),
            ("remember_again_after.tokens", "soon"),
            ("remember_again_after.tokens", "25 calls"),
            ("remember_again_after.calls", "20k"),
            ("rule_types", "[]"), ("rule_types", '{"a": 1}'),
            ("rule_types", '[{"prefix": "X1", "name": "x"}]'),
            ("rule_types", '[{"prefix": "A", "name": "a", "purpose": "p"}, '
                           '{"prefix": "a", "name": "b", "purpose": "q"}]'),
            ("rule_types.DOCS", "not json"),
            ("rule_types.DOCS", '{"name": "Docs"}'),
            ("rule_types.BUSN.remember_again_after", "null"),
            ("rule_types.NOPE.name", "Nope"),
        ]
        for layer in ("global", "project"):
            for key, value in cases:
                with self.subTest(layer=layer, key=key, value=value):
                    self.assert_refused(layer, key, value)

    def test_a_refusal_names_the_key_the_value_and_the_limit(self):
        proc = self.assert_refused("global", "reinject_budget", "999", "999",
                                   "reinject_budget",
                                   f"0-{HOOK.MAX_CONFIGURABLE_REINJECT_BUDGET}")
        self.assertIn("is unchanged", proc.stderr)

    def test_a_key_that_does_not_exist_is_refused_with_the_list(self):
        for key in ("nope", "rule_size.nope", "rule_size.max_chars.x",
                    "show_injections.x", "rule_types.BUSN.nope",
                    "rule_types.BUSN.name.x", "rule_size.", "legacy_type_prefixes"):
            with self.subTest(key=key):
                self.assert_refused("global", key, "1")

    def test_an_unreadable_file_is_never_replaced(self):
        util.write_config(self.scope, "{not json")
        self.assert_refused("project", "reinject_budget", "3", "fix it by hand")

    def test_a_file_that_would_outgrow_what_the_reader_accepts_is_refused(self):
        big = json.dumps([{"prefix": f"T{n}", "name": "n" * 60, "purpose": "p" * 60}
                          for n in range(HOOK.MAX_RULE_TYPES)])
        self.write("global", "rule_types", big)
        util.write_config(self.global_scope,
                          {**json.loads(self.raw(self.global_path())),
                           "_comment": "x" * HOOK.MAX_CONFIG_BYTES})
        self.assert_refused("global", "reinject_budget", "3", "fix it by hand")

    def test_the_operands_are_checked(self):
        for operands in (("language",), ("a", "b", "c")):
            proc = self.admin("config", "--global", *operands)
            self.assertNotEqual(proc.returncode, 0, operands)
            self.assertIn("<key> <value>", proc.stderr)

    def test_writing_a_key_needs_a_scope(self):
        proc = self.admin("config", "language", "en")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--root --global", proc.stderr)


class BeforeTheSetupTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)

    def test_the_global_layer_is_not_written_outside_the_setup(self):
        proc = self.admin("config", "--global", "language", "pt-BR")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("setup", proc.stderr)
        self.assertIn("/rules-by-trigger:config", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.global_scope, "config.json")))

    def test_the_project_layer_does_not_depend_on_the_setup(self):
        proc = self.admin("config", "--root", self.proj, "language", "pt-BR")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(HOOK.load_layer(os.path.join(self.scope, "config.json"),
                                         False)["language"], "pt-BR")
        self.assertFalse(os.path.exists(os.path.join(self.global_scope, "config.json")))

    def test_the_setup_itself_writes_the_global_config(self):
        proc = self.admin("config", "--setup", "--language", "pt-BR", "--no-harden")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.admin("config", "--global", "reinject_budget", "5")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_setup_notice_comes_with_the_writer(self):
        proc = self.admin("config", "--root", self.proj, "language", "en")
        self.assertNotIn("not set up", proc.stdout)


if __name__ == "__main__":
    unittest.main()
