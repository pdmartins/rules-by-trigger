"""Which repository a rule's firing is counted for, and where a rule lives.

One identity for the three places that need it — the hook recording a firing,
the migration of the old usage file, and `status` — so a number counted by one
is found by the others. The repository is the git root of the folder Claude
Code was opened in (`CLAUDE_PROJECT_DIR`) or, without it, of the current
folder; outside git, that folder itself. Every path goes through `realpath`,
as the usage file's rule key already does.
Each worktree has its own root, so each counts separately.

Computed once per session and cached in the session state: the hook runs on
every file tool call and a repository does not change under a session."""

import os

from .constants import GIT_ROOT_TIMEOUT_SECONDS, RULES_DIR_RELPATH, warn
from .discovery import git_root_of, home_dir
from .state import close_state, open_state, save_state, state_file_for

PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"
GIT_TOPLEVEL_COMMAND = ("git", "rev-parse", "--show-toplevel")


def git_toplevel(folder):
    """The git root of `folder`; "" when it is not in a repository; None when
    git could not answer — not installed, a folder it may not enter, no reply
    in time, or an error inside a repository (a `.git` sits above `folder` yet
    git exited non-zero, e.g. "dubious ownership"). Each of those is a failure
    to compute the root and is said on stderr; "not a repository" is the
    ordinary answer outside git and is not."""
    import subprocess  # deferred: imported once per session, not per call
    try:
        done = subprocess.run(GIT_TOPLEVEL_COMMAND, cwd=folder, text=True,
                              capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=GIT_ROOT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"git root of {folder} unavailable ({exc})")
        return None
    if done.returncode == 0:
        return done.stdout.strip()
    if git_root_of(folder) is None:
        return ""
    warn(f"git root of {folder} unavailable (git exited {done.returncode}: "
         f"{done.stderr.strip()[:200]})")
    return None


def repo_root_of(folder):
    """The repository `folder` belongs to: its git root, or the folder itself
    when it has none or git cannot say."""
    real = os.path.realpath(folder)
    top = git_toplevel(real)
    return os.path.realpath(top) if top else real


def current_folder():
    """The current folder, normalized like every other path here, or None when
    even that cannot be read."""
    try:
        return os.path.realpath(os.getcwd())
    except OSError as exc:
        warn(f"current folder unreadable ({exc}); usage not counted per repo")
        return None


def repo_identity():
    """The repository of this session, or None when even the current folder
    cannot be read.

    The git root of `CLAUDE_PROJECT_DIR`, or of the current folder when the
    variable is absent (a CLI run by hand); that folder itself outside git.
    When the root cannot be computed — git unable to run, a folder it cannot
    enter — the current folder stands in."""
    project_dir = os.environ.get(PROJECT_DIR_ENV)
    folder = os.path.realpath(project_dir) if project_dir else current_folder()
    if folder is None:
        return None
    top = git_toplevel(folder)
    if top is None:
        return current_folder()
    return os.path.realpath(top) if top else folder


def repo_of_state(state):
    """The session's repository, computed on the first ask and kept in `state`
    for the caller to save."""
    repo = state.get("repo")
    if not isinstance(repo, str) or not repo:
        repo = repo_identity()
        state["repo"] = repo
    return repo


def repo_of_session(session_id):
    """`repo_of_state` for a caller that holds no open state — the Stop hook."""
    state_path = state_file_for(session_id, create=False)
    if state_path is None or not os.path.isfile(state_path):
        return repo_identity()
    state_fd, state = open_state(state_path)
    try:
        known = state.get("repo")
        repo = repo_of_state(state)
        if repo and not known:
            save_state(state_fd, state)
        return repo
    finally:
        close_state(state_fd)


def rule_base(scope_dir):
    """The project folder a rule's scope belongs to — the one holding its
    `.claude` — or None for the global scope, which belongs to no project."""
    real = os.path.realpath(scope_dir)
    if real == os.path.realpath(os.path.join(home_dir(), RULES_DIR_RELPATH)):
        return None
    return os.path.dirname(os.path.dirname(real))


def is_above(base, repo):
    """True when `base` is a folder strictly above the repository root."""
    return base != repo and repo.startswith(base.rstrip(os.sep) + os.sep)


def counts_for(scope_dir, repo):
    """False for a project rule sitting above the repository's root: it
    belongs to no single repository below it, so its firing is not counted."""
    base = rule_base(scope_dir)
    return not (base is not None and repo is not None and is_above(base, repo))
