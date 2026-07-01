@echo off
setlocal

docker network inspect bnnet >nul 2>&1
if errorlevel 1 docker network create bnnet >nul

:: Start Mineflayer only when it is not already running.
set "MF_RUNNING="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" beliefnestjs 2^>nul`) do set "MF_RUNNING=%%S"
if not "%MF_RUNNING%"=="true" (
  start "mineflayer" powershell -Command "Set-Location '%~dp0..'; docker compose up mineflayer; if ($LASTEXITCODE -ne 0) { cmd /c pause; exit $LASTEXITCODE }"
)

exit /b 0
