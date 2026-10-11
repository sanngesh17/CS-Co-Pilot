@echo off
title CS Copilot
cd /d "%~dp0"
rem Keep temp files on this drive (C: may be full)
if not exist ".tmp" mkdir ".tmp"
set "TEMP=%~dp0.tmp"
set "TMP=%~dp0.tmp"

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up a private Python environment...
  python -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --no-cache-dir -q -r requirements.txt || goto :fail
)
echo Opening CS Copilot at http://localhost:8501  (close this window to stop)
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
pause
exit /b 0

:fail
echo Setup failed - see the message above.
pause
exit /b 1
