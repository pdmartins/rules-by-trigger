"""The repeat-distance finding: a rule whose text reads like a prohibition but
repeats 'never', or a rule with no prohibition language repeating so tightly it
looks like over-treatment.

One finding, two readers: `validate` prints it as a note naming the rule, and
`status --json` reports its reason as the `repeat` improvement candidate."""

import re

from .common import HOOK
from .config import split_type_prefix

# Case-insensitive: a rule stating a prohibition needs the opposite
# reinforcement default from one stating a requirement or convention — only
# prohibition-shaped constraints are known to decay under long context
# (arXiv:2604.20911). Advice for a human reading `validate`, never a judgement
# injected by the hook, which never looks at a rule's own text this way.
PROHIBITION_PATTERN = re.compile(
    r"never|do not|don't|must not|forbidden|nunca|não (deve|pode)|proibido",
    re.IGNORECASE)
# A repeat this tight, on a rule with no prohibition language at all, is more
# often a copy-pasted interval than a deliberate choice.
AGGRESSIVE_INTERVAL_TOKENS = 10_000
AGGRESSIVE_INTERVAL_CALLS = 10

# ---- user-visible text ------------------------------------------------------
REASON_PROHIBITION_NEVER = ("reads like a prohibition but remember_again_after "
                            "is 'never' — only prohibition constraints are "
                            "known to decay under long context; consider "
                            "giving it a repeat distance")
REASON_OVER_TREATMENT = ("repeats every {value} {unit} with no prohibition "
                         "language in its body — requirements and conventions "
                         "hold up without reinforcement; this may be "
                         "over-treatment")
NOTE_WITH_NAME = "{name}: {reason}"
# -----------------------------------------------------------------------------


def effective_interval(name, fields, config):
    """(value, unit) this rule would actually repeat at, following the same
    precedence the hook applies: the rule's own `remember_again_after`, else
    its type's default. Returns None when neither says anything — the
    session/global default then applies, and that is a property of the
    session, not of this rule, so there is nothing here worth a note about."""
    own = HOOK.remember_again_after_of(fields)
    if own is not None:
        return own
    prefix, _rest = split_type_prefix(name, config)
    type_default = HOOK.remember_again_after_for_type(config, prefix) if prefix else None
    return HOOK.parse_remember_again_after(type_default, name) if type_default else None


def reinforcement_reason(name, body, fields, config):
    """Why a rule's text and its repeat distance disagree, or None when they
    do not: a prohibition with reinforcement off, or a non-prohibition
    reinforced as tightly as one — never an error, since both are legitimate
    choices."""
    if not body:
        return None
    interval = effective_interval(name, fields, config)
    if interval is None:
        return None
    value, unit = interval
    prohibits = bool(PROHIBITION_PATTERN.search(body))
    if prohibits and not value:
        return REASON_PROHIBITION_NEVER
    aggressive = (unit == "tokens" and value < AGGRESSIVE_INTERVAL_TOKENS) or \
                 (unit == "calls" and value < AGGRESSIVE_INTERVAL_CALLS)
    if not prohibits and value and aggressive:
        return REASON_OVER_TREATMENT.format(value=value, unit=unit)
    return None


def reinforcement_notes(name, body, fields, config):
    """The `validate` notes for a mismatch between what a rule's text asks for
    and how often it is set to repeat (own frontmatter or inherited type
    default): zero or one, naming the rule."""
    reason = reinforcement_reason(name, body, fields, config)
    return [NOTE_WITH_NAME.format(name=name, reason=reason)] if reason else []
