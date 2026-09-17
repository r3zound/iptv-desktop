@echo off
REM ============================================================
REM   iptv-desktop build script
REM
REM   Output: dist\iptv-desktop\iptv-desktop.exe (onedir, console)
REM   Compat: Windows 7 SP1+ / Windows 10 / Windows 11
REM
REM   Toolchain: Python 3.8.10 + PyInstaller 5.13.2
REM
REM   Icon: assets\icons\10-globe.ico (digital-globe network nodes)
REM
REM   WHY PYTHON 3.8, NOT 3.9:
REM     Python 3.9 DROPPED Windows 7 support (PEP 11).
REM     python39.dll imports "api-ms-win-core-path-l1-1-0.dll", a Windows 8+
REM     API set. On a plain Win7 SP1 box that DLL does not exist and you get:
REM       "The program can't start because api-ms-win-core-path-l1-1-0.dll
REM        is missing from your computer"
REM     Python 3.8 is the LAST version that supports Windows 7.
REM
REM   Usage: double-click build.bat
REM ============================================================

setlocal
cd /d "%~dp0\.."

set PY=C:\Python38\python.exe
if not exist "%PY%" (
    echo !!! Cannot find %PY%
    echo !!! Need Python 3.8.10 embed for Windows 7 compatibility.
    exit /b 1
)

REM Absolute icon path: PyInstaller resolves --icon relative to --specpath,
REM so a bare relative path becomes tools\assets\... which is wrong.
set ICON_PATH=%~dp0..\assets\icons\10-globe.ico
if not exist "%ICON_PATH%" (
    echo !!! Cannot find %ICON_PATH%
    echo !!! Run: python tools\build_icons.py
    exit /b 1
)

echo.
echo === [1/4] Verify Python 3.8 ===
%PY% --version
if errorlevel 1 goto :fail

echo.
echo === [2/4] Verify PyInstaller 5.13.2 ===
%PY% -m PyInstaller --version
if errorlevel 1 goto :fail

echo.
echo === [3/4] Clean previous output ===
taskkill /f /im iptv-desktop.exe >nul 2>&1
timeout /t 1 /nobreak >nul 2>&1
if exist "tools\dist"      rmdir /s /q "tools\dist"
if exist "tools\build"     rmdir /s /q "tools\build"
if exist "tools\iptv-desktop.spec" del /q "tools\iptv-desktop.spec"
if exist "dist\iptv-desktop" rmdir /s /q "dist\iptv-desktop"

echo.
echo === [4/4] PyInstaller onedir console build ===
REM Hidden imports cover Win7 + idna codec edge cases (see iptv-tool
REM AGENTS.md pitfall #5 for the gory details).
REM --icon: digital-globe network nodes (assets\icons\10-globe.ico).
REM --console: gives the exe a console window so it can show
REM   progress (the tool has a Tk-free ConsoleSink). Use --silent
REM   at runtime to suppress output and exit silently.
REM --onedir:  faster cold-start than --onefile.
%PY% -m PyInstaller ^
    --onedir ^
    --name iptv-desktop ^
    --icon "%ICON_PATH%" ^
    --console ^
    --noconfirm ^
    --clean ^
    --hidden-import encodings.idna ^
    --hidden-import encodings.utf_8_sig ^
    --hidden-import encodings.punycode ^
    --hidden-import stringprep ^
    --hidden-import unicodedata ^
    --workpath "tools\build" ^
    --specpath "tools" ^
    "tools\iptv-desktop.py"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   BUILD COMPLETE
echo   Output: dist\iptv-desktop\iptv-desktop.exe
echo.
echo   Next step: python tools\make_portable.py
echo   (or run make_portable.py from project root)
echo ============================================================
endlocal
exit /b 0

:fail
echo.
echo !!! BUILD FAILED - check errors above !!!
endlocal
exit /b 1
