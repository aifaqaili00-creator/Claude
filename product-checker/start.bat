@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto run

echo First run: installing what the app needs. This takes 1-2 minutes...
py -3 -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto nopython
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto failed

:run
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:nopython
echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup. Then run start.bat again.
pause
exit /b 1

:failed
echo Installing failed. Check your internet connection and run start.bat again.
rmdir /s /q .venv
pause
exit /b 1
