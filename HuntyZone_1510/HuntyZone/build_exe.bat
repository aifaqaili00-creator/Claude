@echo off
setlocal
cd /d "%~dp0"
rem Builds dist\ProductChecker.exe, one file to share. It uses the .venv that start.bat made (start the app once first).
if not exist ".venv\Scripts\python.exe" (
  echo Start Product Checker once with start.bat first: it sets up the .venv this build uses.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" build_exe.py
pause
