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

:: Wait until the VOICEVOX engine is ready before starting the sally agent.
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

:: Start the sally agent.
start "agent-sally" powershell -Command "docker run -i %USE_GPU% --rm --name easyllm_agent_sally -p 7891:7891 -p 8765:8765 --network bnnet -v '%~dp0..\src:/app/src' -w /app/src easyllmvoice_agent python3 -u main.py --config sally_cfg.yml; if ($LASTEXITCODE -ne 0) { cmd /c pause; exit $LASTEXITCODE }"
exit /b 0
