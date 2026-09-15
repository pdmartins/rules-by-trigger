"""Tests for the written-paths list: the trail PreToolUse leaves in the session
state so the end of the turn knows which files were written and therefore which
`verify:` commands have anything to check."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


class RecordWrittenTest(unittest.TestCase):
    """Direct coverage of the two operations: no subprocess, just the state
    dict shape main() and the Stop hook hand them."""

    def test_a_new_path_is_appended(self):
        state = {"calls": 1, "seen": {}, "written": []}
        HOOK.record_written(state, "/proj/src/a.py")
        self.assertEqual(state["written"], ["/proj/src/a.py"])

    def test_the_same_path_twice_is_recorded_once(self):
        state = {"calls": 1, "seen": {}, "written": ["/proj/src/a.py"]}
        HOOK.record_written(state, "/proj/src/a.py")
        self.assertEqual(state["written"], ["/proj/src/a.py"])

    def test_order_is_the_order_the_files_were_first_written_in(self):
        state = {"calls": 1, "seen": {}, "written": []}
        for path in ("/proj/b.py", "/proj/a.py", "/proj/b.py", "/proj/c.py"):
            HOOK.record_written(state, path)
        self.assertEqual(state["written"],
                         ["/proj/b.py", "/proj/a.py", "/proj/c.py"])

    def test_a_missing_key_is_created_rather_than_crashing(self):
        state = {"calls": 1, "seen": {}}
        HOOK.record_written(state, "/proj/src/a.py")
        self.assertEqual(state["written"], ["/proj/src/a.py"])

    def test_the_cap_drops_the_oldest_path(self):
        cap = HOOK.MAX_WRITTEN_PATHS
        state = {"calls": 1, "seen": {},
                 "written": [f"/proj/f{i}.py" for i in range(cap)]}
        HOOK.record_written(state, "/proj/new.py")
        self.assertEqual(len(state["written"]), cap, "the list stays bounded")
        self.assertNotIn("/proj/f0.py", state["written"], "the oldest goes")
        self.assertIn("/proj/f1.py", state["written"], "the next oldest stays")
        self.assertEqual(state["written"][-1], "/proj/new.py")

    def test_take_returns_the_paths_and_clears_them(self):
        state = {"calls": 1, "seen": {}, "written": ["/proj/a.py", "/proj/b.py"]}
        taken = HOOK.take_written(state)
        self.assertEqual(state["written"], [], "cleared at each verification")
        self.assertEqual(taken, ["/proj/a.py", "/proj/b.py"],
                         "the caller keeps what it took, clear or not")

    def test_take_on_a_state_that_has_no_list_answers_empty(self):
        state = {"calls": 1, "seen": {}}
        self.assertEqual(HOOK.take_written(state), [])
        self.assertEqual(state["written"], [])

    def test_coercion_keeps_the_usable_strings_and_drops_the_rest(self):
        self.assertEqual(HOOK.coerce_written(["/a", 5, None, "", "/a", "/b"]),
                         ["/a", "/b"])

    def test_coercion_of_something_that_is_not_a_list_is_empty(self):
        for value in (None, "/proj/a.py", 7, {"/a": 1}):
            self.assertEqual(HOOK.coerce_written(value), [], repr(value))


class WriteRecordingTest(util.SandboxTestCase):
    """End to end: what one tool call leaves in the session's state file."""

    PROJECT_SUBDIRS = ("src", "docs")
    SESSION = "s1"

    def target(self, rel=None):
        """The absolute path the hook records for a touch on <proj>/<rel> —
        the literal path the tool named, not its resolved form."""
        rel = self.TOUCHED if rel is None else rel
        return os.path.join(self.proj, rel).replace(os.sep, "/")

    def written(self):
        return util.read_state(self.home, self.SESSION)["written"]

    def test_a_write_records_the_path(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        self.hook_for(session=self.SESSION, tool="Write")
        self.assertEqual(self.written(), [self.target()])

    def test_every_write_tool_records(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        for index, tool in enumerate(HOOK.WRITE_TOOL_NAMES):
            session = f"tool-{index}"
            self.hook_for(session=session, tool=tool)
            self.assertEqual(util.read_state(self.home, session)["written"],
                             [self.target()], tool)

    def test_a_read_records_nothing(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        self.hook_for(session=self.SESSION, tool="Read")
        self.assertEqual(self.written(), [], "a read is a touch, not a write")

    def test_two_writes_to_the_same_file_record_it_once(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        self.hook_for(session=self.SESSION, tool="Write")
        self.hook_for(session=self.SESSION, tool="Edit")
        self.assertEqual(self.written(), [self.target()])

    def test_a_write_no_rule_covers_is_still_recorded(self):
        """The `verify:` that will run at the end of the turn is selected by
        the Stop hook, with no `tool:` filter and against every rule — so a
        write nothing matched here is not a write nothing needs to check."""
        util.write_rule(self.proj, "docs.md", "docs/**", "Docs rule.")
        _proc, text = self.touch(session=self.SESSION, tool="Write")
        self.assertIsNone(text, "nothing matched src/a.py, so nothing injected")
        self.assertEqual(self.written(), [self.target()])

    def test_a_denied_write_is_not_recorded(self):
        """A blocked path was never written, so it must never be verified.

        An allowed write to another file comes first on purpose: the denial
        returns before the state file is opened, so against an empty list the
        assertion would pass however the recording were placed. With one path
        already in it, appending the denied one is visible."""
        util.write_rule(self.home, "BUSN_no-src-writes.md",
                        f"{self.proj}/src/**".replace(os.sep, "/"),
                        "Never write here.", extra_frontmatter=["block: true"])
        self.hook_for("docs/x.md", session=self.SESSION, tool="Write")
        self.assertEqual(self.written(), [self.target("docs/x.md")])
        proc = self.hook_for(session=self.SESSION, tool="Write")
        self.assertEqual(util.hook_specific_output(proc)["permissionDecision"],
                         "deny", proc.stderr)
        self.assertEqual(self.written(), [self.target("docs/x.md")],
                         "the refused path was never appended")

    def test_a_malformed_written_list_on_disk_is_coerced(self):
        """Same contract as `seen`: a hand-edited or half-written state file
        repairs itself on the next save instead of reaching the end of the
        turn as something the glob matcher cannot read."""
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        util.write_state(self.home, self.SESSION,
                         json.dumps({"calls": 3, "seen": {},
                                     "written": ["/proj/a.py", 5, "", None]}))
        self.hook_for(session=self.SESSION, tool="Write")
        self.assertEqual(self.written(), ["/proj/a.py", self.target()])

    def test_written_of_the_wrong_type_entirely_becomes_a_list(self):
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        util.write_state(self.home, self.SESSION,
                         json.dumps({"calls": 3, "seen": {}, "written": "nope"}))
        self.hook_for(session=self.SESSION, tool="Read")
        self.assertEqual(self.written(), [])

    def test_reset_session_clears_the_list(self):
        """SessionStart(compact|clear) drops the state file, and the list of
        written paths goes with it — the writes of a turn the reset interrupted
        are not verified against a context that no longer holds them."""
        util.write_rule(self.proj, "src.md", "src/**", "Rule text.")
        self.hook_for(session=self.SESSION, tool="Write")
        self.assertEqual(self.written(), [self.target()])
        util.run_hook({"session_id": self.SESSION}, self.home,
                      args=["--reset-session"])
        self.assertFalse(os.path.exists(util.state_path(self.home, self.SESSION)),
                         "the state file itself is gone")
        self.hook_for(session=self.SESSION, tool="Read")
        self.assertEqual(self.written(), [], "the fresh state starts empty")


if __name__ == "__main__":
    unittest.main()
