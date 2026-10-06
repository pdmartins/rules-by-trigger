"""Shared setup for the tests of the operations behind `update` (spec 0008,
FR-004): `rename`, `split`, `move` and glob changes, with the history of the
rule and the settings lines that follow them."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402
from disabledutil import BODY, DISABLED, NOT_ROOT, DisabledSandbox, line_for  # noqa: E402,F401
from statsutil import HOOK  # noqa: E402

SECOND_REPO = "/planted/other-repo"
THIS_REPO_COUNT = 4
OTHER_REPO_COUNT = 3
TOTAL_COUNT = 9  # more than the two repos add up to: a repo evicted once
KIND_PROJECT = "project"
KIND_GLOBAL = "global"


class OperationsSandbox(DisabledSandbox):
    """A project that is a git repository of its own, a global scope, and
    helpers to plant a rule's history and read it back through `status`."""

    def history(self, scope, name, this_repo=THIS_REPO_COUNT,
                other_repo=OTHER_REPO_COUNT, total=TOTAL_COUNT):
        """Plant what a rule fired: `this_repo` times here, `other_repo` times in
        another repository, `total` overall."""
        try:
            stats = self.stats()
        except FileNotFoundError:
            stats = {"version": 2, "since": 1, "rules": {}}
        entry = HOOK.empty_entry()
        entry.update(injections=this_repo, first=1, last=5)
        other = HOOK.empty_entry()
        other.update(injections=other_repo, first=1, last=4)
        stats["rules"][self.key(scope, name)] = {
            "total": total, "last": 5,
            "repos": {os.path.realpath(self.proj): entry, SECOND_REPO: other}}
        self.plant(stats)

    def row(self, name, kind=KIND_PROJECT):
        for rule in self.status_json()["rules"]:
            if rule["name"] == name and rule["scope"] == kind:
                return rule
        raise AssertionError(f"{name} ({kind}) is not in the status report")

    def counts(self, name, kind=KIND_PROJECT):
        """(this repo, total) as `status` shows them."""
        rule = self.row(name, kind)
        return rule["this_repo"], rule["total"]

    def stats_keys(self):
        try:
            return set(self.stats()["rules"])
        except FileNotFoundError:
            return set()

    def global_rule(self, name, glob, *extra):
        return util.write_rule(self.home, name, glob, BODY,
                               extra_frontmatter=list(extra))

    def read_global_rule(self, name):
        with open(os.path.join(self.global_scope, name), encoding="utf-8") as handle:
            return handle.read()

    def global_exists(self, name):
        return os.path.exists(os.path.join(self.global_scope, name))

    def lock_path(self):
        return self.stats_file() + HOOK.stats.LOCK_SUFFIX

    def break_stats(self):
        """The usage file cannot be updated any more: its lock is a folder."""
        os.makedirs(self.lock_path())
        self.addCleanup(self.repair_stats)

    def repair_stats(self):
        if os.path.isdir(self.lock_path()):
            os.rmdir(self.lock_path())

    def snapshot(self):
        """Every rule file of both scopes, the settings file and the usage
        file, as bytes — what 'nothing was touched' compares."""
        found = {}
        for folder in (self.scope, self.global_scope,
                       os.path.join(self.proj, ".claude")):
            for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
                path = os.path.join(folder, name)
                if os.path.isfile(path):
                    with open(path, "rb") as handle:
                        found[path] = handle.read()
        if os.path.isfile(self.stats_file()):
            found[self.stats_file()] = self.stats_bytes()
        return found

    def pieces(self, *pieces):
        return json.dumps(list(pieces))

    def split(self, name, pieces_json, *flags):
        return self.admin("split", name, "--root", self.proj, *flags, stdin=pieces_json)

    def rename(self, old, new, *flags):
        return self.admin("rename", "--root", self.proj, old, new, *flags)

    def move_to_global(self, name, *flags):
        return self.admin("move", "--root", self.proj, "--rule", name,
                          "--to-global", "--anchor", "any-project", *flags)

    def move_to_project(self, name, *flags):
        return self.admin("move", "--global", "--rule", name,
                          "--to-root", self.proj, *flags)
