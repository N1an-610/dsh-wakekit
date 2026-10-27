@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY="
if exist "%WAKEKIT_PYTHON%" set "PY=%WAKEKIT_PYTHON%"
if defined PY goto :checkpy
set "PY=python"
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 set "PY="
if defined PY goto :checkpy
set "PY=py -3"
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 set "PY="
:checkpy
if not defined PY goto :nopy

set "PYTHONIOENCODING=utf-8"
echo === demo: 3 sample memories to wake pack ===
echo.
"%PY%" tools\demo.py
if errorlevel 1 goto :done

echo.
echo Opening the wake pack with Notepad...
timeout /t 2 /nobreak >nul
if exist "data\wake.md" start notepad "data\wake.md"

:done
echo.
echo To clean up, double-click demo-clean.bat
pause
exit /b 0

:nopy
echo [!] Python not found. Install Python 3.8+ or set WAKEKIT_PYTHON.
pause
exit /b 2
