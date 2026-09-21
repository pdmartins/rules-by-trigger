"""Unit and end-to-end tests for the cap on `injected_rules`
(rules_by_trigger.due.trim_injected_rules, applied by `open_state`): a key
recorded inside a subagent is never cleared on its own — not until /clear, a
compaction, or the 14-day stale sweep — so without a cap it would grow without
end across a long session that spawns many subagents."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import util  # noqa: E402

HOOK = util.load_hook_module()


def entry(call_number):
    """A minimal `injected_rules` value: [call number, tokens, reinjections]."""
    return [call_number, None, 0]


class TrimInjectedRulesTest(unittest.TestCase):
    """Direct coverage of the function: no I/O, just the dict shape
    `open_state` hands it."""

    def test_under_the_cap_nothing_changes(self):
        injected_rules = {"a": entry(1), "b": entry(2)}
        before = dict(injected_rules)
        HOOK.trim_injected_rules(injected_rules, cap=5)
        self.assertEqual(injected_rules, before)

    def test_exactly_at_the_cap_changes_nothing(self):
        injected_rules = {"a": entry(1), "b": entry(2)}
        before = dict(injected_rules)
        HOOK.trim_injected_rules(injected_rules, cap=2)
        self.assertEqual(injected_rules, before)

    def test_over_the_cap_the_lowest_call_numbers_go(self):
        injected_rules = {"a": entry(1), "b": entry(2), "c": entry(3),
                          "d": entry(4)}
        HOOK.trim_injected_rules(injected_rules, cap=2)
        self.assertEqual(set(injected_rules), {"c", "d"},
                         "the two lowest call numbers are dropped")

    def test_ties_drop_the_earliest_inserted(self):
        """A and B share the same call number; A was inserted first, so a cap
        that only has room for one of the tied pair drops A."""
        injected_rules = {"A": entry(1), "B": entry(1), "C": entry(5)}
        HOOK.trim_injected_rules(injected_rules, cap=2)
        self.assertEqual(set(injected_rules), {"B", "C"})

    def test_the_cap_drops_exactly_enough_to_reach_it(self):
        injected_rules = {f"k{i}": entry(i) for i in range(10)}
        HOOK.trim_injected_rules(injected_rules, cap=4)
        self.assertEqual(len(injected_rules), 4)
        self.assertEqual(set(injected_rules), {"k6", "k7", "k8", "k9"})

    def test_default_cap_is_max_injected_rules(self):
        over = HOOK.MAX_INJECTED_RULES + 5
        injected_rules = {f"k{i}": entry(i) for i in range(over)}
        HOOK.trim_injected_rules(injected_rules)
        self.assertEqual(len(injected_rules), HOOK.MAX_INJECTED_RULES)
        self.assertEqual(
            set(injected_rules),
            {f"k{i}" for i in range(5, over)},
            "the newest MAX_INJECTED_RULES entries survive")


class OpenStateAppliesTheCapTest(unittest.TestCase):
    """End to end: a state file already over the cap comes back capped the
    moment it is opened — the cap lives in `open_state`, not in `main.py`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def state_path(self):
        return os.path.join(self.tmp.name, "s1.json")

    def write_raw_state(self, injected_rules, calls):
        with open(self.state_path(), "w", encoding="utf-8") as handle:
            json.dump({"calls": calls, "injected_rules": injected_rules,
                      "unverified_writes": [], "rules_written": []}, handle)

    def test_a_state_file_with_cap_plus_n_entries_returns_exactly_cap(self):
        cap = HOOK.MAX_INJECTED_RULES
        extra = 5
        injected_rules = {f"k{i}": entry(i) for i in range(cap + extra)}
        self.write_raw_state(injected_rules, cap + extra)
        fd, state = HOOK.open_state(self.state_path())
        try:
            self.assertEqual(len(state["injected_rules"]), cap)
            self.assertEqual(
                set(state["injected_rules"]),
                {f"k{i}" for i in range(extra, cap + extra)},
                "the newest entries are the ones that survive")
        finally:
            HOOK.close_state(fd)

    def test_a_state_file_under_the_cap_is_untouched(self):
        injected_rules = {f"k{i}": entry(i) for i in range(10)}
        self.write_raw_state(injected_rules, 10)
        fd, state = HOOK.open_state(self.state_path())
        try:
            self.assertEqual(set(state["injected_rules"]),
                             set(injected_rules))
        finally:
            HOOK.close_state(fd)


if __name__ == "__main__":
    unittest.main()
