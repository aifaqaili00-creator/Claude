@echo off
setlocal
cd /d "%~dp0"
rem Product Checker runs with the first Python on this PC that has what it needs (deps.py checks): the .venv folder
rem here, else your own Python. A .venv that does not work on this PC (copied from another computer, or made by a
rem Python that is gone) is made again, so any copy of this folder works.
set "VENV=%~dp0.venv\Scripts\python.exe"
set "NEW="

set "PY=%VENV%"
if not exist "%PY%" goto base
"%PY%" deps.py <nul >nul 2>&1 && goto run
"%PY%" -c "pass" <nul >nul 2>&1 && goto install
echo The .venv folder does not work on this PC: it was made on another computer, or by a Python that is gone.
echo Making a new one...
rmdir /s /q .venv

:base
call :findpython
if not defined BASE goto nopython
set "PY=%BASE%"
"%PY%" deps.py <nul >nul 2>&1 && goto run
echo First start on this PC: installing what Product Checker needs. This takes 1-2 minutes...
set "NEW=1"
"%BASE%" -m venv .venv <nul
if not exist "%VENV%" goto ownpython
set "PY=%VENV%"

:install
if not defined NEW echo Updating what Product Checker needs...
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check <nul
"%PY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check <nul
"%PY%" deps.py <nul || goto failed
if defined NEW call :shortcut
goto run

:ownpython
rem A read-only folder, or security software said no: the packages go into your own Python instead.
echo Could not make a .venv folder here, so the packages go into your own Python: %BASE%
"%PY%" -m pip install --user -r requirements.txt --quiet --disable-pip-version-check <nul
"%PY%" deps.py <nul || goto failed
call :shortcut

:run
set "PYW=%PY:~0,-10%pythonw.exe"
if not exist "%PYW%" set "PYW=%PY%"
if /i "%~1"=="debug" goto debug
if /i "%~1"=="background" goto background
start "" "%PYW%" app.py
exit /b 0

:background
rem Started by Windows at sign-in ("Start with Windows"): no window, keeps the market data fresh.
start "" "%PYW%" app.py --background
exit /b 0

:debug
"%PY%" app.py
pause
exit /b 0

:findpython
rem The newest Python 3 the py launcher knows, else "python" on the PATH (the Microsoft Store stub prints nothing).
set "BASE="
for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul ^<nul') do set "BASE=%%i"
if not defined BASE for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul ^<nul') do set "BASE=%%i"
exit /b 0

:shortcut
echo Creating a "Product Checker" shortcut on your desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Product Checker.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()" >nul 2>&1
exit /b 0

:nopython
echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup. Then run start.bat again.
pause
exit /b 1

:failed
echo.
echo Product Checker could not be set up (see above). Check your internet connection and run start.bat again.
pause
exit /b 1
