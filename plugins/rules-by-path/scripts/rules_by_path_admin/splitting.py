"""The "should this rule be split?" check of `validate`.

Its own module because it is the only part of validation that looks at the
FILESYSTEM rather than at the rule file: it reads the directories a glob is
rooted at to see whether the rule's own text names something narrower than
itself. Directory listings stay out of the hook — this runs in the CLI, never
in the injection path.

`scope_findings` is the only caller."""

import os
import re


# Bounds for the "should this rule be split?" check (CLI only, never the hook).
MAX_SCANNED_CHILDREN = 200
MAX_SPLIT_SUGGESTIONS = 3
MIN_MENTION_CHARS = 4  # below this a name matches prose by accident


def glob_base_dir(glob, anchor):
    """The deepest directory a glob is rooted at, or None.

    `src/Api/**` -> <anchor>/src/Api. Everything from the first segment carrying
    a metacharacter onwards is dropped, because that is where the glob stops
    naming a place and starts describing a set."""
    text = glob.strip()
    segments = []
    for segment in text.strip("/").split("/"):
        if not segment or any(ch in segment for ch in "*?"):
            break
        segments.append(segment)
    if not segments:
        return None
    if text.startswith("/"):
        return "/" + os.path.join(*segments)
    return os.path.join(anchor, *segments) if anchor else None


def targets_one_file(glob):
    """True when the glob names a single file rather than a set of them."""
    text = glob.strip().rstrip("/")
    if any(ch in text for ch in "*?"):
        return False
    return "." in os.path.basename(text)


def split_candidates(name, globs, body, anchor):
    """Notes about constraints that look narrower than the rule that carries them.

    The premise of this plugin is that nothing reaches the context until it is
    relevant, and a rule is the unit of that decision: every file its glob
    matches receives ALL of it. So a rule that mixes "controllers look like X",
    "the DI file looks like Y" and "no file over 300 lines" under `src/Api/**`
    hands two thirds of itself to files that cannot act on it — the first two
    belong in their own rules, with their own globs.

    Deciding that needs judgement, so this only raises the question, and only on
    the signal that is actually checkable: the rule's own text naming a path
    that exists UNDER its glob and is narrower than it. Directory listings stay
    out of the hook — this runs in the CLI, never in the injection path."""
    if not body:
        return []
    notes = []
    for glob in globs:
        if targets_one_file(glob):
            continue
        base = glob_base_dir(glob, anchor)
        if not base or not os.path.isdir(base):
            continue
        declared = {segment.lower() for segment in glob.strip("/").split("/")}
        found = []  # [(name mentioned in the body, glob that would target it)]
        try:
            with os.scandir(base) as entries:
                children = sorted(entries, key=lambda entry: entry.name)[:MAX_SCANNED_CHILDREN]
        except OSError:
            continue
        prefix = glob.strip().split("*")[0].rstrip("/")
        for child in children:
            if child.name.startswith("."):
                continue
            stem = os.path.splitext(child.name)[0]
            if len(stem) < MIN_MENTION_CHARS or stem.lower() in declared:
                continue
            if not re.search(rf"\b{re.escape(stem)}\b", body, re.IGNORECASE):
                continue
            if child.is_dir(follow_symlinks=False):
                found.append((child.name, f"{prefix}/{child.name}/**"))
            else:
                found.append((child.name, f"{prefix}/{child.name}"))
            if len(found) >= MAX_SPLIT_SUGGESTIONS:
                break
        if found:
            notes.append(
                f"{name}: mentions {', '.join(repr(mention) for mention, _ in found)}, "
                f"which live under {glob!r} but are narrower than it. Every file "
                f"matched by a rule receives the WHOLE rule, so a constraint that "
                f"only governs those belongs in its own rule: "
                f"{' / '.join(f'--glob {suggestion!r}' for _, suggestion in found)}")
    return notes
