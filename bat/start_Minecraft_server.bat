@echo off
setlocal

docker network inspect bnnet >nul 2>&1
if errorlevel 1 docker network create bnnet >nul

:: Start Minecraft only when it is not already running.
set "MC_RUNNING="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" mc_server_flat_25565 2^>nul`) do set "MC_RUNNING=%%S"
if not "%MC_RUNNING%"=="true" (
  start "minecraft" powershell -Command "Set-Location '%~dp0..'; docker compose up -d minecraft; if ($LASTEXITCODE -ne 0) { cmd /c pause; exit $LASTEXITCODE }; docker attach mc_server_flat_25565; cmd /c pause"
)

exit /b 0
