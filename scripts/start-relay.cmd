@echo off
rem Starts HomeStream on Windows from a source checkout: same as `homestream start`.
rem (setup.ps1 creates .venv. Pass --skip-checks to skip the startup checks.)
setlocal
set "ROOT=%~dp0.."
if not exist "%ROOT%\.venv\Scripts\python.exe" (
  echo error: Python environment missing - run setup.ps1 first
  exit /b 1
)
cd /d "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -m homestream start %*
