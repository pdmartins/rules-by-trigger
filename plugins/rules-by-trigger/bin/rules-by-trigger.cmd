@echo off
rem rules-by-trigger admin CLI launcher (Windows).
rem No for-loop: %errorlevel% inside a for body expands at parse time, so an
rem `exit /b %errorlevel%` there always reports 0.
setlocal
set "RBT_SCRIPT=%~dp0..\scripts\rules-by-trigger-admin.py"
where py >nul 2>&1 && (
  py -3 "%RBT_SCRIPT%" %*
  exit /b
)
where python >nul 2>&1 && (
  python "%RBT_SCRIPT%" %*
  exit /b
)
where python3 >nul 2>&1 && (
  python3 "%RBT_SCRIPT%" %*
  exit /b
)
echo rules-by-trigger: no Python found on PATH; install Python 3.8+ 1>&2
exit /b 1
