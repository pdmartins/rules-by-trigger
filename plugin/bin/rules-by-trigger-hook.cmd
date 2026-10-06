@echo off
rem rules-by-trigger PreToolUse hook launcher (Windows).
rem Always exits 0: a hook must never block a tool call, and a non-zero exit on
rem every Read would make the plugin unusable instead of merely inactive.
setlocal
set "RBT_SCRIPT=%~dp0..\hooks\rules-by-trigger.py"
where py >nul 2>&1 && (
  py -3 "%RBT_SCRIPT%" %*
  exit /b 0
)
where python >nul 2>&1 && (
  python "%RBT_SCRIPT%" %*
  exit /b 0
)
where python3 >nul 2>&1 && (
  python3 "%RBT_SCRIPT%" %*
  exit /b 0
)
echo rules-by-trigger: no Python on PATH; rules are not being injected 1>&2
exit /b 0
