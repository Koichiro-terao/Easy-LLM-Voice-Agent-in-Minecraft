@echo off
setlocal

docker network inspect bnnet >nul 2>&1
if errorlevel 1 docker network create bnnet >nul

:: Check GPU availability.
set "USE_GPU="
nvidia-smi >nul 2>&1
if not errorlevel 1 (
  set "USE_GPU=--gpus all"
  echo GPU detected. Running with GPU support.
) else (
  echo GPU not detected. Running on CPU.
)

:: Select image based on GPU availability.
set "VOICEVOX_IMAGE=voicevox/voicevox_engine:cpu-ubuntu20.04-latest"
if "%USE_GPU%"=="--gpus all" (
  set "VOICEVOX_IMAGE=voicevox/voicevox_engine:nvidia-ubuntu20.04-latest"
)

:: Start VOICEVOX only when it is not already running.
set "VV_RUNNING="
for /f "usebackq delims=" %%S in (`docker inspect -f "{{.State.Running}}" voicevox_engine 2^>nul`) do set "VV_RUNNING=%%S"
if not "%VV_RUNNING%"=="true" (
  start "voicevox" powershell -Command "docker run %USE_GPU% --rm --name voicevox_engine -p 50021:50021 --network bnnet %VOICEVOX_IMAGE%; if ($LASTEXITCODE -ne 0) { cmd /c pause; exit $LASTEXITCODE }"
)
exit /b 0
