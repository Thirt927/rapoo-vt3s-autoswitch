@echo off
setlocal
cd /d "%~dp0"
title rapoo-autoswitch install

echo.
echo   rapoo-autoswitch - one-click install
echo   ===================================
echo.

rem ---- 1) locate Python ---------------------------------------------------
rem Order matters: `py` (launcher) is most reliable, then `python3`, then
rem `python` (which on Windows may be the Microsoft Store stub).
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python3 >nul 2>nul && set "PY=python3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY goto no_python
%PY% -c "import sys" >nul 2>nul
if errorlevel 1 goto no_python
echo   Python found:
%PY% -V

rem ---- 2) the folder path must be ASCII-only ------------------------------
rem A non-ASCII (e.g. Chinese) path breaks `pip install -e .`: the generated
rem .pth is written in the local codepage, which Python cannot read back.
%PY% -c "import sys;sys.exit(0 if sys.argv[1].isascii() else 3)" "%CD%"
if errorlevel 3 goto bad_path
echo   Path OK: %CD%
echo.

rem ---- 3) install ---------------------------------------------------------
echo   Installing dependencies, please wait ...
%PY% -m pip install --upgrade pip >nul 2>nul
%PY% -m pip install -e ".[tray]"
if errorlevel 1 goto pip_failed
echo.

rem ---- 4) self check ------------------------------------------------------
%PY% -m rapoo_autoswitch doctor
echo.

set "RUNSETUP="
set /p "RUNSETUP=  Run the setup wizard now? [Y/n] "
if /i "%RUNSETUP%"=="n" goto finished
%PY% -m rapoo_autoswitch setup

:finished
echo.
echo   Done.
echo.
pause
exit /b 0

:no_python
echo   [X] Python not found.
echo.
echo       1. Install Python 3.9+ : https://www.python.org/downloads/
echo       2. During setup, TICK "Add python.exe to PATH".
echo       3. If you see a Microsoft Store window instead, that is the
echo          Windows "App execution alias" - turn it off in
echo          Settings ^> Apps ^> Advanced app settings ^> App execution aliases.
echo.
pause
exit /b 1

:bad_path
echo   [X] This folder path contains non-ASCII characters:
echo.
echo       %CD%
echo.
echo       That breaks the install. Move this folder to a pure-English
echo       path such as D:\rapoo, then run this script again.
echo.
pause
exit /b 1

:pip_failed
echo   [X] pip install failed - scroll up for the reason.
echo.
pause
exit /b 1
