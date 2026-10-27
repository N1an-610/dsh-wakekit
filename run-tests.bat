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

set "NODEEXE="
if exist "%WAKEKIT_NODE%" set "NODEEXE=%WAKEKIT_NODE%"
if defined NODEEXE goto :havenode
where node >nul 2>&1 && set "NODEEXE=node"
:havenode
if defined NODEEXE set "WAKEKIT_NODE=%NODEEXE%"

set "PYTHONIOENCODING=utf-8"
"%PY%" tools\run_tests.py
set "CODE=%errorlevel%"

echo.
if "%CODE%"=="0" goto :allok
echo [!] Some tests failed. Scroll up for details.
echo.
pause
exit /b 1

:allok
echo [ok] All tests passed. You can close this window.
echo.
pause
exit /b 0

:nopy
echo [!] Python not found.
echo.
echo     Install Python 3.8+ (tick "Add Python to PATH"),
echo     or set the environment variable WAKEKIT_PYTHON to python.exe.
echo     Then run this file again.
echo.
pause
exit /b 2
