"""Shared setup for the per-repo usage tests: a sandbox that can run the hook
and the CLI as a session opened in a given folder, and read and plant the
usage file."""

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()
HAS_GIT = shutil.which("git") is not None


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def v1_entry(injections, last=5, dirs=None, sessions=("old",)):
    return {"injections": injections, "reinjections": 1, "sessions": len(sessions),
            "recent_sessions": list(sessions), "first": 1, "last": last,
            "dirs": dirs or {"src": injections}, "globs": {"src/**": injections}}


class StatsSandbox(util.SandboxTestCase):
    PROJECT_SUBDIRS = ("src/api/handlers", "src/web")
    SESSION_COUNTER = 0

    def stats_file(self):
        return os.path.join(util.state_dir(self.home), HOOK.STATS_FILE_NAME)

    def stats(self):
        with open(self.stats_file(), encoding="utf-8") as handle:
            return json.load(handle)

    def stats_bytes(self):
        with open(self.stats_file(), "rb") as handle:
            return handle.read()

    def legacy_file(self):
        """Where the first format kept its usage: the file a hook of that
        version, still running in some session, keeps writing."""
        return os.path.join(util.state_dir(self.home), HOOK.LEGACY_STATS_FILE_NAME)

    def legacy_bytes(self):
        with open(self.legacy_file(), "rb") as handle:
            return handle.read()

    def plant(self, stats):
        util.write_file(self.stats_file(), json.dumps(stats))

    def plant_legacy(self, stats):
        util.write_file(self.legacy_file(), json.dumps(stats))

    def key(self, scope, name):
        return f"{os.path.realpath(scope)}::{name}"

    def fire(self, relative, project_dir=None, session=None, cwd=None, env=None):
        """One tool call of a session opened in `project_dir` (the sandbox's
        project by default), touching `relative` inside the sandbox project."""
        if session is None:
            StatsSandbox.SESSION_COUNTER += 1
            session = f"session-{StatsSandbox.SESSION_COUNTER}"
        project_dir = self.proj if project_dir is None else project_dir
        extra = {"CLAUDE_PROJECT_DIR": project_dir} if project_dir else {}
        extra.update(env or {})
        payload = util.read_payload("Read", os.path.join(self.proj, relative),
                                    session=session, cwd=cwd or self.proj)
        proc = util.run_hook(payload, self.home, env=extra, cwd=cwd)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def status_json(self, project_dir=None):
        project_dir = self.proj if project_dir is None else project_dir
        proc = self.admin("status", "--root", self.proj, "--json",
                          env={"CLAUDE_PROJECT_DIR": project_dir})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def rule_of(self, report, name):
        """The entry of `status --json` for a rule, by file name."""
        for rule in report["rules"]:
            if rule["name"] == name:
                return rule
        raise AssertionError(f"{name} not in the report")

    def aside_files(self):
        directory = util.state_dir(self.home)
        return sorted(name for name in os.listdir(directory)
                      if name.startswith(HOOK.STATS_FILE_NAME + ".corrupt-"))


class RepoSandbox(StatsSandbox):
    """A StatsSandbox whose project is a git repository of its own, so the
    repo `status` and the hook identify is the project whatever TMPDIR is —
    inside another repository or not."""

    def setUp(self):
        super().setUp()
        if HAS_GIT:
            git("init", "-q", cwd=self.proj)
