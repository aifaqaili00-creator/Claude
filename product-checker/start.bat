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
echo Creating a "Product Checker" shortcut on your desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Product Checker.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()" >nul 2>&1

:run
if /i "%~1"=="debug" goto debug
".venv\Scripts\python.exe" -c "import pandas, openpyxl, playwright" 2>nul || ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:debug
".venv\Scripts\python.exe" app.py
pause
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
