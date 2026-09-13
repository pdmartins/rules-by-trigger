"""Finding the rule directories that apply to a touched file, and refusing
the ones that are not safe to read: another user's, world-writable, or reached
through a symlinked `.claude`."""

import os
import stat

from .constants import (CLAUDE_DIR_NAME, MAX_ANCESTOR_STEPS, MAX_SCOPES,
                        RULES_DIR_RELPATH, warn)


def is_safely_owned(path):
    """True when `path` is owned by us and not world-writable.

    A rules directory in a world-writable shared parent (say /tmp) would let any
    local user inject instructions into every session below it. Group-writable
    is left alone on purpose: shared-group directories are the norm on team
    machines, and rejecting them would break more than it protects."""
    if os.name == "nt":
        return True  # POSIX ownership bits do not carry over; skip the check
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if info.st_uid != os.geteuid():
        return False
    return not bool(info.st_mode & stat.S_IWOTH)

def scope_is_contained(base_dir, scope_dir):
    """True when `scope_dir` physically lives at `base_dir/.claude/rules-by-trigger`.

    Validating only what is *inside* the scope is not enough: if `.claude` or
    `.claude/rules-by-trigger` is itself a symlink, every check below it resolves
    through the same link and passes trivially, so reads, writes and deletes
    land wherever the link points — including the user's global rules."""
    expected = os.path.join(os.path.realpath(base_dir), RULES_DIR_RELPATH)
    return os.path.realpath(scope_dir) == expected

def usable_scope(base_dir, is_global=False):
    """The scope directory for `base_dir`, or None when it is absent or unsafe.

    Physical containment is required for a PROJECT scope, where a symlinked
    `.claude` can arrive inside a cloned repository and redirect everything to
    the attacker's target. It is NOT required for the global scope: `~/.claude`
    is the user's own configuration, symlinking it elsewhere (shared config,
    dotfiles in a repo) is a normal and deliberate choice, and nobody can plant
    that link without already owning the home directory. Ownership of the real
    target is still checked in both cases."""
    scope_dir = os.path.join(base_dir, RULES_DIR_RELPATH)
    if not os.path.isdir(scope_dir):
        return None
    if not is_global and not scope_is_contained(base_dir, scope_dir):
        warn(f"ignoring {scope_dir}: it does not physically live inside {base_dir}")
        return None
    if not is_safely_owned(os.path.realpath(scope_dir)):
        warn(f"ignoring {scope_dir}: not safely owned (world-writable or another user's)")
        return None
    return scope_dir

def global_scope(scopes):
    """The machine owner's own scope in a `find_scopes` list — it comes first
    and is marked by having no base directory — or None when this tool call
    reached none.

    The mark lives here, next to the function that sets it, rather than being
    re-read from tuple positions by every caller that needs to know which layer
    it may trust."""
    return scopes[0] if scopes and scopes[0][0] is None else None


def home_dir():
    """The user's home directory, resolved through symlinks.

    The one computation that says what "home" means, so `find_scopes` (which
    locates the global scope) and `project_root_of` (which must NOT mistake it
    for a project) agree on the same directory rather than each resolving
    `~` on its own."""
    return os.path.realpath(os.path.expanduser("~"))


def project_root_of(start_dir):
    """The project `start_dir` belongs to — the innermost directory at or above
    it holding a `.claude`, home itself excluded — or None when it belongs to
    none.

    A rule from the global scope has no root of its own, so its command borrows
    the root of the project the written file belongs to (spec Q6). That project
    is the one the HARNESS recognises, which is the one with a `.claude`: a
    repository configuring Claude Code with settings, agents or native rules and
    no `.claude/rules-by-trigger/` at all is still a project, and its `pytest.ini`
    is still what a global `pytest` means there.

    Home itself is excluded from that rule: `~/.claude` is the GLOBAL scope,
    not a project, so a file written directly under home with no project in
    between must fall through to the session's cwd (see the caller in
    `verify.py`) rather than resolve to home. An ancestor of home that ALSO
    holds a `.claude` is a different directory and still counts — a repository
    checked out one level above the user's home is not home — so the walk does
    not stop there, only skips the one directory that is home.

    Deliberately NOT the innermost scope `find_scopes` returned: that is the
    innermost directory holding a `.claude/rules-by-trigger/`, which is a different
    and rarer thing — asking for it made a global rule run at the session's cwd
    in every repository that ships no rules of its own.

    Bounded by MAX_ANCESTOR_STEPS like the scope walk. `os.path.isdir` never
    raises — it reports False on any OSError — so a path that cannot be
    walked simply has no project, and the caller falls back to the session's
    own directory."""
    directory = start_dir
    steps = 0
    home = home_dir()
    while steps < MAX_ANCESTOR_STEPS:
        steps += 1
        if (os.path.isdir(os.path.join(directory, CLAUDE_DIR_NAME))
                and os.path.realpath(directory) != home):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return None  # filesystem root
        directory = parent
    return None


def find_scopes(start_dir):
    """[(base_dir_or_None, scope_dir, label)] for a touched file: the global
    scope first, then every project scope from the highest ancestor down to the
    touched file's own directory.

    The walk goes all the way to the filesystem root and collects every
    `.claude/rules-by-trigger` on the way. It used to stop at the first `.git`,
    which silently excluded git submodules: inside one, `.git` is a *file*, so
    `os.path.exists` matched and the walk halted there — a `.cs` under
    `libs/api/src/` received nothing at all, even with a `**/*.cs` rule at the
    parent repository's root.

    What that costs, stated plainly: a rules directory in an ancestor the user
    does not control can now inject into every session below it. The ownership
    and permission filter in `usable_scope` — another user's directory and
    world-writable ones are refused — is what remains of that defence.

    Two orderings, both deliberate, both about who gets served when a budget
    runs out. Global comes first so the user's own rules always get budget
    before rules that arrived with a cloned repository. Among project scopes the
    highest ancestor comes first, and it is the one kept when MAX_SCOPES is
    exceeded: the walk discovers scopes deepest-first, so a naive cap drops it —
    and anyone able to add directories to a repo (a PR into a monorepo, a
    vendored dependency) could bury the outer rules under a chain of nested
    scopes and silently suppress them for that whole subtree.
    """
    scopes = []
    seen = set()
    home = home_dir()

    owner_scope = usable_scope(home, is_global=True)
    if owner_scope:
        seen.add(os.path.realpath(owner_scope))
        scopes.append((None, owner_scope, "global"))

    chain = []  # project scopes, deepest first
    directory = start_dir
    steps = 0
    while steps < MAX_ANCESTOR_STEPS:
        steps += 1
        scope_dir = usable_scope(directory)
        if scope_dir:
            real = os.path.realpath(scope_dir)
            if real not in seen:
                seen.add(real)
                chain.append((directory, scope_dir))
        parent = os.path.dirname(directory)
        if parent == directory:
            break  # filesystem root
        directory = parent

    chain.reverse()  # highest ancestor first
    room = max(1, MAX_SCOPES - len(scopes))
    if len(chain) > room:
        warn(f"more than {MAX_SCOPES} scopes apply; keeping the outermost and "
             f"the {room - 1} nearest to the file, ignoring the rest")
        # Keep the outermost scope and the ones closest to the touched file;
        # drop the middle of the chain, which is the part nothing depends on.
        chain = chain[:1] + chain[len(chain) - room + 1:]
    for base_dir, scope_dir in chain:
        scopes.append((base_dir, scope_dir, f"project {base_dir}"))
    return scopes
