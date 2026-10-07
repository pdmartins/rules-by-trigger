#!/usr/bin/env bash
# shellcheck shell=bash
# publish.sh — release rules-by-trigger: bump the version, close the CHANGELOG
# section, merge develop into main and push both.
#
# VERSIONING POLICY. The version is MAJOR.MINOR.REVISION and it changes ONLY on
# a release, i.e. only when develop is merged into main. Between releases the
# version in develop equals the last published one; `0.0.0` means never
# published. There are no `-beta` suffixes: the version stays put between
# releases, and a release changes it only in plugin.json.
# The CHANGELOG follows the same policy: entries are written under
# `## Unreleased` while developing, and a release renames that heading to the
# number it just computed, dated. This script refuses to release an empty one.
#
# WHERE USERS GET IT. The marketplace `pdmartins` lives in another repository,
# pdmartins/claude-plugins, and points at this one by a `git-subdir` source with
# `path: plugin` (the plugin lives in plugin/, the repo-level files stay at the
# root), with no ref and no version, so it serves the default branch. This script
# makes main that default branch. It never touches the other repository and
# never touches this machine's install.
#
# TRYING UNRELEASED CODE. Start a session with `claude --plugin-dir plugin`. For
# that session only, the directory REPLACES an installed plugin of the same name
# (rules-by-trigger@pdmartins): the installed copy is not loaded, so the hooks
# do not fire twice. `/reload-plugins` picks up edits. (Claude Code docs:
# Plugins > Loading > Name conflicts.)
#
# Usage:
#   bash publish.sh                      release: 0.3.1 -> 0.4.0   (--minor)
#   bash publish.sh --major              release: 0.4.0 -> 1.0.0
#   bash publish.sh --revision           release: 1.0.0 -> 1.0.1   (or --patch)
#   bash publish.sh --dry-run            print the plan, execute nothing
#   bash publish.sh --yes                skip the confirmation prompt (or -y)
#
# A release merges into main and pushes it. That is not undoable from here, so
# everything that can refuse — branch, clean tree, tests, manifests — refuses
# BEFORE the first push, and nothing after it is allowed to abort the script.

set -euo pipefail

# ─── user-visible text and settings ───────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
TAG="rules-by-trigger"
RELEASE_BRANCH="main"
DEV_BRANCH="develop"
MARKETPLACE_NAME="pdmartins"
MARKETPLACE_REPO="pdmartins/claude-plugins"

info()    { echo -e "${BLUE}[$TAG]${NC} $*"; }
success() { echo -e "${GREEN}[$TAG]${NC} ✅ $*"; }
warn()    { echo -e "${YELLOW}[$TAG]${NC} ⚠️  $*"; }
error()   { echo -e "${RED}[$TAG]${NC} ❌ $*"; exit 1; }
dry()     { echo -e "${YELLOW}[dry-run]${NC} $*"; }

# ─── arguments ────────────────────────────────────────────────────────────────
DRY_RUN=false
ASSUME_YES=false
BUMP="minor"

for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=true ;;
    --yes|-y)     ASSUME_YES=true ;;
    --major)      BUMP="major" ;;
    --minor)      BUMP="minor" ;;
    --revision|--patch) BUMP="revision" ;;
    *)            error "unknown argument: $arg (see the header of this script)" ;;
  esac
done

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

# The repository root is the development scaffolding; the
# plugin itself is the `plugin/` directory below, and that directory is exactly
# what `claude plugin install` copies.
PLUGIN_DIR="$REPO_DIR/plugin"
PLUGIN_JSON="$PLUGIN_DIR/.claude-plugin/plugin.json"
CHANGELOG_MD="$REPO_DIR/CHANGELOG.md"

read_json() { python3 -c "import json,sys; print(json.load(open('$1'))$2)"; }

PLUGIN_NAME=$(read_json "$PLUGIN_JSON" "['name']")
INSTALL_ID="${PLUGIN_NAME}@${MARKETPLACE_NAME}"
CURRENT_VERSION=$(read_json "$PLUGIN_JSON" "['version']")

# owner/repo, read from the remote rather than hardcoded, so renaming the
# repository does not need an edit here.
REMOTE_SLUG=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null \
  | sed -E 's#^git@[^:]+:##; s#^https?://[^/]+/##; s#\.git$##' || true)

# ─── release gates ────────────────────────────────────────────────────────────
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$CURRENT_BRANCH" = "$RELEASE_BRANCH" ] && \
  error "already on $RELEASE_BRANCH. Release from $DEV_BRANCH."

# Getting back to the branch you started on must not depend on the happy path.
# The release checks out main to merge, and `set -e` means a conflicted merge or
# a rejected push kills the script right there — leaving you on main, mid-merge,
# without saying so. This trap runs on every exit, success or failure.
ORIGINAL_BRANCH="$CURRENT_BRANCH"
restore_branch() {
  local status=$?
  local current
  current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  { [ -z "$current" ] || [ "$current" = "$ORIGINAL_BRANCH" ]; } && return $status
  if [ -f "$(git rev-parse --git-dir 2>/dev/null)/MERGE_HEAD" ]; then
    # Never abort it silently: an unfinished merge is the user's to resolve,
    # and throwing it away could discard conflict resolution already done.
    warn "a merge is still in progress on '$current' — finish it, or run:"
    warn "    git merge --abort && git checkout $ORIGINAL_BRANCH"
    return $status
  fi
  info "returning to $ORIGINAL_BRANCH (was left on $current)"
  git checkout "$ORIGINAL_BRANCH" >/dev/null 2>&1 || \
    warn "could not switch back — you are on '$current'; run: git checkout $ORIGINAL_BRANCH"
  return $status
}
trap restore_branch EXIT
[ "$CURRENT_BRANCH" = "$DEV_BRANCH" ] || \
  warn "releasing from '$CURRENT_BRANCH', not '$DEV_BRANCH'"

git diff --quiet && git diff --cached --quiet || \
  error "the working tree has uncommitted changes. Commit them before releasing."

# The release notes are the one thing this script cannot compute, so they are
# checked while the release can still be called off: once main is pushed, a
# release that says nothing about itself is permanent.
CHANGELOG_PROBLEM=$(python3 - <<PYEOF
import io, re
text = io.open("$CHANGELOG_MD", encoding="utf-8").read()
section = re.search(r"^## Unreleased[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
if section is None:
    print("there is no '## Unreleased' heading")
elif not section.group(1).strip():
    print("the '## Unreleased' section is empty")
PYEOF
)
[ -z "$CHANGELOG_PROBLEM" ] || \
  error "CHANGELOG.md: $CHANGELOG_PROBLEM — write what this release changes under that heading first, the release renames it to the new version."

info "Running the test suite..."
TEST_OUT=$(python3 -m unittest discover -s tests -q 2>&1) || {
  echo "$TEST_OUT" | tail -25
  error "tests failed — release aborted."
}
success "tests OK ($(echo "$TEST_OUT" | grep -E '^Ran ' | tail -1))"

info "Validating the plugin manifests..."
claude plugin validate "$PLUGIN_DIR" --strict >/dev/null 2>&1 || \
  error "'claude plugin validate $PLUGIN_DIR --strict' failed — release aborted."
success "manifests OK"

# ─── compute the new version ──────────────────────────────────────────────────
MAJOR=$(echo "$CURRENT_VERSION" | cut -d. -f1)
MINOR=$(echo "$CURRENT_VERSION" | cut -d. -f2)
REVISION=$(echo "$CURRENT_VERSION" | cut -d. -f3)
case "$BUMP" in
  major)    NEW_VERSION="$((MAJOR + 1)).0.0" ;;
  minor)    NEW_VERSION="${MAJOR}.$((MINOR + 1)).0" ;;
  revision) NEW_VERSION="${MAJOR}.${MINOR}.$((REVISION + 1))" ;;
esac

echo ""
info "Plugin:      $INSTALL_ID"
info "Branch:      $CURRENT_BRANCH → $RELEASE_BRANCH"
info "Version:     $CURRENT_VERSION → $NEW_VERSION  ($BUMP)"
info "Remote:      ${REMOTE_SLUG:-<none>}"
echo ""

if $DRY_RUN; then
  dry "bump $CURRENT_VERSION → $NEW_VERSION in plugin.json (the only place the version lives)"
  dry "rename CHANGELOG.md's '## Unreleased' to '## $NEW_VERSION — $(date +%F)', empty one above"
  dry "git commit -am 'release: v$NEW_VERSION' && git push origin $CURRENT_BRANCH"
  dry "git checkout $RELEASE_BRANCH  (created from $CURRENT_BRANCH if absent)"
  dry "git merge --no-ff $CURRENT_BRANCH -m 'release: v$NEW_VERSION'"
  dry "git push origin $RELEASE_BRANCH && git checkout $CURRENT_BRANCH"
  dry "gh repo edit $REMOTE_SLUG --default-branch $RELEASE_BRANCH"
  dry "not touched: $MARKETPLACE_REPO (the marketplace) and this machine's install"
  warn "Dry-run finished — nothing was executed."
  exit 0
fi

if ! $ASSUME_YES; then
  printf "Merge %s into %s and push v%s? [y/N] " "$CURRENT_BRANCH" "$RELEASE_BRANCH" "$NEW_VERSION"
  read -r reply
  case "$reply" in [yY]*) ;; *) error "aborted." ;; esac
fi

# ─── bump, commit, merge, push ────────────────────────────────────────────────
RBT_NEW_VERSION="$NEW_VERSION" python3 - <<PYEOF
import datetime, json, os, re

version = os.environ["RBT_NEW_VERSION"]

def rewrite(path, mutate):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    mutate(data)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

def set_plugin_version(data):
    data["version"] = version

def release_changelog(path):
    """`## Unreleased` becomes the number computed above, and a fresh empty
    `## Unreleased` takes its place for the next cycle. It happens here, with
    the manifests and inside their commit, because this is the first moment the
    number exists — and the gate above already refused an empty section."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    heading = "## %s — %s" % (version, datetime.date.today().isoformat())
    text, renamed = re.subn(r"^## Unreleased[^\n]*\n",
                            "## Unreleased\n\n%s\n" % heading,
                            text, count=1, flags=re.M)
    if not renamed:
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)

rewrite("$PLUGIN_JSON", set_plugin_version)
release_changelog("$CHANGELOG_MD")
PYEOF
success "plugin.json bumped to $NEW_VERSION, CHANGELOG section renamed"

claude plugin validate "$PLUGIN_DIR" --strict >/dev/null 2>&1 || \
  error "the bumped plugin does not validate — nothing was pushed, fix and retry."

git commit -am "release: v$NEW_VERSION"
git push origin "$CURRENT_BRANCH"
success "committed and pushed on $CURRENT_BRANCH"

info "Merging $CURRENT_BRANCH → $RELEASE_BRANCH..."
if git show-ref --verify --quiet "refs/heads/$RELEASE_BRANCH"; then
  git checkout "$RELEASE_BRANCH"
  git merge --no-ff "$CURRENT_BRANCH" -m "release: v$NEW_VERSION"
else
  git checkout -b "$RELEASE_BRANCH"   # first release: main starts here
fi
git push origin "$RELEASE_BRANCH"
# Tolerant on purpose: the release is public from the line above, so nothing
# after it may abort the script. The EXIT trap is the backstop if this fails.
git checkout "$CURRENT_BRANCH" || warn "could not return to $CURRENT_BRANCH"
success "v$NEW_VERSION is on $RELEASE_BRANCH"

# ─── nothing below may abort: the release is already public ───────────────────
# The marketplace entry has no ref, so it serves the repository's DEFAULT branch:
# one still pointing at develop would hand users the development code — the
# exact opposite of what this release just did.
if [ -n "$REMOTE_SLUG" ] && command -v gh >/dev/null 2>&1; then
  DEFAULT_BRANCH=$(gh repo view "$REMOTE_SLUG" --json defaultBranchRef \
    --jq .defaultBranchRef.name 2>/dev/null || true)
  if [ "$DEFAULT_BRANCH" != "$RELEASE_BRANCH" ]; then
    info "GitHub default branch is '$DEFAULT_BRANCH' — switching it to $RELEASE_BRANCH..."
    gh repo edit "$REMOTE_SLUG" --default-branch "$RELEASE_BRANCH" \
      && success "default branch is now $RELEASE_BRANCH" \
      || warn "could not switch it — run: gh repo edit $REMOTE_SLUG --default-branch $RELEASE_BRANCH"
  fi
  VISIBILITY=$(gh repo view "$REMOTE_SLUG" --json visibility --jq .visibility 2>/dev/null || true)
  [ "$VISIBILITY" = "PRIVATE" ] && \
    warn "the repository is still PRIVATE — nobody can install it until you make it public"
fi

echo ""
success "Released v$NEW_VERSION"
echo ""
echo "  Users install it with:"
echo "    /plugin marketplace add $MARKETPLACE_REPO"
echo "    /plugin install $INSTALL_ID"
echo ""
echo "  To update this machine (this script did not touch it), run:"
echo "    /plugin marketplace update $MARKETPLACE_NAME"
echo "    claude plugin update $INSTALL_ID"
echo ""
echo "  Then open a NEW session: hooks load at session start."
