@echo off
REM ChaseOS coordination-watch daemon template.
REM Copy this file into a private local bootstrap path and replace placeholders there.

set "CHASEOS_VAULT=%CHASEOS_VAULT%"
set "CHASEOS_PYTHON=%CHASEOS_PYTHON%"
set "CHASEOS_RUNTIME=%CHASEOS_RUNTIME%"
set "CHASEOS_LOG=%CHASEOS_VAULT%\runtime\lifecycle\run\%CHASEOS_RUNTIME%-coordination-watch.log"

if "%CHASEOS_VAULT%"=="" (
  echo CHASEOS_VAULT is required.
  exit /b 1
)

if "%CHASEOS_PYTHON%"=="" (
  set "CHASEOS_PYTHON=python"
)

if "%CHASEOS_RUNTIME%"=="" (
  echo CHASEOS_RUNTIME is required. Example: hermes or openclaw.
  exit /b 1
)

cd /d "%CHASEOS_VAULT%"

echo [%DATE% %TIME%] %CHASEOS_RUNTIME% coordination-watch daemon starting >> "%CHASEOS_LOG%" 2>&1

"%CHASEOS_PYTHON%" -m runtime.cli.main runtime daemon --runtime "%CHASEOS_RUNTIME%" --daemon-interval 30 --daemon-max-tasks 5 --vault-root "%CHASEOS_VAULT%" >> "%CHASEOS_LOG%" 2>&1

echo [%DATE% %TIME%] %CHASEOS_RUNTIME% coordination-watch daemon exited with code %ERRORLEVEL% >> "%CHASEOS_LOG%" 2>&1

