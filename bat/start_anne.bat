@echo off
setlocal

docker network inspect bnnet >nul 2>&1
if errorlevel 1 docker network create bnnet >nul

:: Build the agent image if it does not exist yet.
docker image inspect easyllmvoice_agent >nul 2>&1
if errorlevel 1 (
  echo Building easyllmvoice_agent image, this may take several minutes...
  docker build -t easyllmvoice_agent -f "%~dp0..\src\Dockerfile" "%~dp0.."
  if errorlevel 1 ( echo Build failed. & pause & exit /b 1 )
)

:: Check GPU availability.
set "USE_GPU="
nvidia-smi >nul 2>&1
if not errorlevel 1 (
  set "USE_GPU=--gpus all"
  echo GPU detected. Running with GPU support.
) else (
  echo GPU not detected. Running on CPU.
)

:: Start VOICEVOX engine if not running.
call "%~dp0start_VOICEVOX.bat"

:: Start Minecraft server if not running.
call "%~dp0start_Minecraft_server.bat"

:: Start Mineflayer server if not running.
call "%~dp0start_Mineflayer_server.bat"

:: Wait until the Minecraft server is ready before starting the Anne agent.
:wait_mc_container
set "MC_RUNNING="
set "MC_STARTED_AT="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" mc_server_flat_25565 2^>nul`) do set "MC_RUNNING=%%S"
for /f "usebackq delims=" %%T in (`docker inspect -f "{{.State.StartedAt}}" mc_server_flat_25565 2^>nul`) do set "MC_STARTED_AT=%%T"
if not "%MC_RUNNING%"=="true" (
  timeout /t 2 /nobreak >nul
  goto wait_mc_container
)

:wait_mc_ready
docker logs --since "%MC_STARTED_AT%" mc_server_flat_25565 2>&1 | findstr /C:"Done (" >nul
if errorlevel 1 (
  timeout /t 2 /nobreak >nul
  goto wait_mc_ready
)

:: Wait until the Mineflayer server is ready before starting the Anne agent.
:wait_mineflayer_container
set "MF_RUNNING="
set "MF_STARTED_AT="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" beliefnestjs 2^>nul`) do set "MF_RUNNING=%%S"
for /f "usebackq delims=" %%T in (`docker inspect -f "{{.State.StartedAt}}" beliefnestjs 2^>nul`) do set "MF_STARTED_AT=%%T"
if not "%MF_RUNNING%"=="true" (
  timeout /t 2 /nobreak >nul
  goto wait_mineflayer_container
)

:wait_mineflayer_ready
docker logs --since "%MF_STARTED_AT%" beliefnestjs 2>&1 | findstr /C:"Server started on port" >nul
if errorlevel 1 (
  timeout /t 2 /nobreak >nul
  goto wait_mineflayer_ready
)

:: Wait until the VOICEVOX engine is ready before starting the Anne agent.
:wait_voicevox_container
set "VV_RUNNING="
set "VV_STARTED_AT="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" voicevox_engine 2^>nul`) do set "VV_RUNNING=%%S"
for /f "usebackq delims=" %%T in (`docker inspect -f "{{.State.StartedAt}}" voicevox_engine 2^>nul`) do set "VV_STARTED_AT=%%T"
if not "%VV_RUNNING%"=="true" (
  timeout /t 2 /nobreak >nul
  goto wait_voicevox_container
)

:wait_voicevox_ready
docker logs --since "%VV_STARTED_AT%" voicevox_engine 2>&1 | findstr /C:"Application startup complete." >nul
if errorlevel 1 (
  timeout /t 2 /nobreak >nul
  goto wait_voicevox_ready
)

:: Start the Anne agent.
start "agent-anne" powershell -Command "docker run -i %USE_GPU% --rm --name easyllm_agent_anne -p 7892:7892 -p 8766:8766 --network bnnet -v '%~dp0..\src:/app/src' -w /app/src easyllmvoice_agent python3 -u main.py --config anne_cfg.yml; if ($LASTEXITCODE -ne 0) { cmd /c pause; exit $LASTEXITCODE }"
exit /b 0
