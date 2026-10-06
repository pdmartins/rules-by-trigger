"""Tunables of the per-rule usage file, apart from `constants.py`, which
re-exports them: that module reached its line ceiling.

Per-rule usage lives beside the session state, in one file that the stale
sweep never touches: it is the record that outlives sessions on purpose.
Every collection in it is capped so it stays small however long it lives."""

# The per-repo file has a name of its own. The first format kept its data in
# `usage-stats.json` and a hook of that version, still running in an open
# session, rewrites whatever it finds there as its own: a version-2 file in that
# name would be flattened under the new hook's feet. The old file is read once,
# to start the new one, and never written or read again.
STATS_FILE_NAME = "usage-stats-v2.json"
LEGACY_STATS_FILE_NAME = "usage-stats.json"
LOCK_SUFFIX = ".lock"            # the lock lives beside the file it guards
ASIDE_SUFFIX = ".corrupt-"       # + epoch: a file set aside, kept for the user
TEMP_SUFFIX = ".tmp-"            # + pid: a write in flight, renamed into place
STATS_VERSION = 2
# The largest file the plugin reads, and the largest it writes (a write that
# would exceed it first drops the repos fired longest ago), so a file the plugin
# wrote is always one it can read back instead of setting it aside as corrupt.
# A file filled to every cap below, with typical content in each entry:
#   dirs            20 x (80-char path + 8 bytes of JSON)      = 1,760 B
#   globs           16 x (64-char glob + 8 bytes of JSON)      = 1,152 B
#   recent_sessions 16 x (36-char session id + 4 bytes)        =   640 B
#   counters, dates, key names                                 =   250 B
#   one repo entry                                             = 3,802 B
#   x 512 rules x 64 repos (32,768 entries)  ~ 124.6 MB        = 118.8 MiB
# Keys are not length-capped, so the true worst case is unbounded; the write-side
# trim is what holds the line there.
STATS_READ_LIMIT_BYTES = 128 * 1024 * 1024
MAX_STATS_RULES = 512
MAX_STATS_REPOS_PER_RULE = 64
MAX_STATS_DIRS_PER_RULE = 20
MAX_STATS_GLOBS_PER_RULE = 16
MAX_STATS_RECENT_SESSIONS = 16
# `git rev-parse` runs once per session; a repository that cannot answer in
# this time is treated as no repository.
GIT_ROOT_TIMEOUT_SECONDS = 5
