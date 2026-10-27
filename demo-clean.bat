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
"%PY%" tools\demo_clean.py
pause
exit /b 0

:nopy
echo [!] Python not found.
pause
exit /b 2
