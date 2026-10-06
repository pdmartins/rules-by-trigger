"""Whether a rule is switched on.

A rule is active unless its frontmatter says `enabled: false`. That is the whole
contract, and it is deliberately lopsided: only the word `false` disables, so a
typo (`enabled: flase`, a list, an empty value) leaves the rule injecting
instead of silently muting a rule someone relies on. `validate` is where such a
value is reported, where a human is listening — the hook never warns on this
path, which every tool call walks."""

ENABLED_KEY = "enabled"
ENABLED_TRUE = "true"
ENABLED_FALSE = "false"
ENABLED_WORDS = (ENABLED_TRUE, ENABLED_FALSE)


def enabled_word(fields):
    """The lowercased word the rule declares under `enabled`, or None when the
    key is absent or carries anything but a single value (a list, nothing)."""
    raw = fields.get(ENABLED_KEY)
    return raw.strip().lower() if isinstance(raw, str) else None


def is_enabled(fields):
    """False only for a rule that says `enabled: false`."""
    return enabled_word(fields) != ENABLED_FALSE


def enabled_is_valid(fields):
    """True when the key is absent or holds one of the two boolean words."""
    return ENABLED_KEY not in fields or enabled_word(fields) in ENABLED_WORDS


def enabled_entries(entries):
    """The `(name, fields)` pairs of a scope index that are switched on."""
    return [(name, fields) for name, fields in entries if is_enabled(fields)]
