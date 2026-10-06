"""Shared setup for the disabled-rule tests (spec 0008, FR-005/006/003): a
sandbox that can plant rules and settings and run `remove`/`update --enable`."""

import json
import os
import stat
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statsutil  # noqa: E402
import util  # noqa: E402

SETTINGS_RELPATH = os.path.join(".claude", "settings.json")
DISABLED = "enabled: false"
BODY = "Keep handlers thin."
SKILL = "workflow-authoring"
SKILL_CALL = f"Skill(skill={SKILL})"
NOT_ROOT = unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                           "root ignores read-only folders")


def line_for(glob):
    return f"Edit({glob})"


class DisabledSandbox(statsutil.RepoSandbox):
    """Helpers to plant rules and read back the settings file. The project is a
    git repository of its own, so `status` does not depend on what holds the
    temporary folder."""

    def write_settings(self, lines):
        util.write_file(os.path.join(self.proj, SETTINGS_RELPATH),
                        json.dumps({"permissions": {"deny": list(lines)}}))

    def deny_lines(self):
        path = os.path.join(self.proj, SETTINGS_RELPATH)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)["permissions"]["deny"]

    def block_rule(self, name, glob, *extra):
        return util.write_rule(self.proj, name, glob, BODY,
                               extra_frontmatter=["block: true", *extra])

    def remove(self, name, *flags):
        return self.admin("remove", "--root", self.proj, "--rule", name, *flags)

    def enable(self, name, stdin=""):
        return self.admin("update", "--root", self.proj, "--rule", name,
                          "--enable", stdin=stdin)

    def rule_exists(self, name):
        return os.path.exists(os.path.join(self.scope, name))

    def make_dot_claude_read_only(self):
        """Settings can no longer be written, the rules still can: the rule
        file is written first, so this is exactly 'the rule changed, the
        settings write failed'."""
        folder = os.path.join(self.proj, ".claude")
        self.addCleanup(os.chmod, folder, 0o700)
        os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)

    def make_dot_claude_writable(self):
        os.chmod(os.path.join(self.proj, ".claude"), 0o700)
