"""What a verification leaves in the usage stats, and what `status` makes of
it: how often a rule's command ran, and how often it failed (spec Q16)."""

import json
import os
import shlex
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()

PYTHON = shlex.quote(sys.executable)
FAILING = f"{PYTHON} -c {shlex.quote('import sys; sys.exit(3)')}"
PASSING = f"{PYTHON} -c {shlex.quote('pass')}"


class VerifyStatsTest(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src",)
    SESSION = "s1"

    def wrote(self, *relatives):
        paths = [os.path.join(self.proj, rel).replace(os.sep, "/")
                 for rel in relatives]
        util.write_state(self.home, self.SESSION,
                         json.dumps({"calls": 1, "injected_rules": {},
                                     "unverified_writes": paths}))

    def verify(self):
        proc = util.run_hook({"session_id": self.SESSION, "cwd": self.proj,
                              "hook_event_name": "Stop"},
                             self.home, args=("--verify",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def stats_file(self):
        return os.path.join(util.state_dir(self.home), HOOK.STATS_FILE_NAME)

    def entry(self, name):
        with open(self.stats_file(), encoding="utf-8") as handle:
            rules = json.load(handle)["rules"]
        return rules[f"{os.path.realpath(self.scope)}::{name}"]

    def status(self, *extra):
        proc = self.admin("status", "--root", self.proj, *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_a_failed_verification_counts_once_for_every_rule_that_asked(self):
        """The command ran once for both rules (it is one (command, cwd) pair),
        and neither rule may lose the evidence to that deduplication."""
        for name in ("CONV_a.md", "CONV_b.md"):
            util.write_rule(self.proj, name, "src/**", "Rule.",
                            extra_frontmatter=[f"verify: {FAILING}"])
        self.wrote("src/a.py")
        self.verify()
        for name in ("CONV_a.md", "CONV_b.md"):
            self.assertEqual(self.entry(name)["verifications"], 1, name)
            self.assertEqual(self.entry(name)["failures"], 1, name)

    def test_a_passing_verification_counts_no_failure(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {PASSING}"])
        self.wrote("src/a.py")
        self.verify()
        self.assertEqual(self.entry("CONV_a.md")["verifications"], 1)
        self.assertEqual(self.entry("CONV_a.md")["failures"], 0)

    def test_a_stats_file_written_before_verify_existed_still_loads(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {FAILING}"])
        key = f"{os.path.realpath(self.scope)}::CONV_a.md"
        util.write_file(self.stats_file(), json.dumps(
            {"version": 1, "since": 1, "rules": {key: {
                "injections": 2, "reinjections": 1, "sessions": 1,
                "recent_sessions": ["old"], "first": 1, "last": 2,
                "dirs": {"src": 2}, "globs": {"src/**": 2}}}}))
        self.wrote("src/a.py")
        self.verify()
        entry = self.entry("CONV_a.md")
        self.assertEqual(entry["verifications"], 1, "the new counter starts at 0")
        self.assertEqual(entry["injections"], 2, "and the old ones survive")

    def test_status_reports_what_a_rule_verified(self):
        util.write_rule(self.proj, "CONV_a.md", "src/**", "Rule.",
                        extra_frontmatter=[f"verify: {FAILING}"])
        self.wrote("src/a.py")
        self.verify()
        self.assertIn("verified 1, failed 1", self.status())
        report = json.loads(self.status("--json"))
        usage = report["scopes"][1]["rules"][0]["usage"]
        self.assertEqual(usage["verifications"], 1)
        self.assertEqual(usage["failures"], 1)

    def test_a_rule_that_only_verified_is_still_never_injected(self):
        """Its command ran, its text never reached anyone — which is exactly
        what the note exists to say."""
        util.write_rule(self.proj, "CONV_a.md", "src/**", "Rule.",
                        extra_frontmatter=["tool: read", f"verify: {FAILING}"])
        self.wrote("src/a.py")
        self.verify()
        out = self.status()
        self.assertIn("never injected since usage stats began", out)
        self.assertIn("CONV_a.md", out.split("never injected")[1])
        self.assertNotIn("injected 0x", out, "and it claims no injection")


if __name__ == "__main__":
    unittest.main()
