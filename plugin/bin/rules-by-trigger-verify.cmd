@echo off
rem rules-by-trigger Stop verification launcher (Windows). Always exits 0.
setlocal
set "RBT_SCRIPT=%~dp0..\hooks\rules-by-trigger.py"
where py >nul 2>&1 && ( py -3 "%RBT_SCRIPT%" --verify & exit /b 0 )
where python >nul 2>&1 && ( python "%RBT_SCRIPT%" --verify & exit /b 0 )
where python3 >nul 2>&1 && ( python3 "%RBT_SCRIPT%" --verify & exit /b 0 )
exit /b 0
