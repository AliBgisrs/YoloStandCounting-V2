@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  YOLO Stand Counting -- dev launcher
echo ============================================================
echo Working dir: %CD%
echo.

REM --- 1. Pick a Python: prefer 3.12 (best wheel coverage), then 3.11, then default ---
set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -V >nul 2>nul && set "PYCMD=py -3.12"
    if not defined PYCMD py -3.11 -V >nul 2>nul && set "PYCMD=py -3.11"
)
if not defined PYCMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD (
    echo [ERROR] No Python found on PATH.
    echo Install Python 3.12 from https://www.python.org/downloads/release/python-31210/
    echo and tick "Add python.exe to PATH" during setup.
    goto :end
)

REM --- 2. Verify it actually runs (not the MS Store stub) ---
%PYCMD% --version 2>nul | findstr /R "Python [0-9]" >nul
if errorlevel 1 (
    echo [ERROR] "%PYCMD%" does not look like a real Python.
    echo This is usually the Microsoft Store stub. Install real Python from python.org.
    goto :end
)
for /f "delims=" %%v in ('%PYCMD% --version 2^>^&1') do echo Detected: %%v  ^(via %PYCMD%^)
echo.

REM --- 3. venv ---
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment in .venv using %PYCMD% ...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv creation failed.
        goto :end
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe still does not exist after venv creation.
    goto :end
)

set "VPY=%CD%\.venv\Scripts\python.exe"
echo Using venv: %VPY%
"%VPY%" --version
echo.

REM --- 4. Install requirements once ---
if not exist ".venv\.installed" (
    echo [setup] Upgrading pip ...
    "%VPY%" -m pip install --upgrade pip
    echo [setup] Installing requirements -- 5 to 10 minutes the first time...
    "%VPY%" -m pip install --prefer-binary -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Scroll up to see the actual pip error.
        goto :end
    )
    echo done> ".venv\.installed"
)

REM --- 5. Sanity-check that the venv really has all imports ---
echo [check] Verifying imports ...
"%VPY%" -c "import PySide6, ultralytics, rasterio, fiona, pyproj, shapely, cv2, numpy"
if errorlevel 1 (
    echo.
    echo [ERROR] One or more required modules failed to import.
    echo The venv install was partial. To fix:
    echo   1. Close this window.
    echo   2. Delete the .venv folder ^(in cmd: rmdir /s /q .venv^).
    echo   3. Re-run run.bat -- it will reinstall everything.
    goto :end
)

REM --- 6. Model file ---
if not exist "models\best.pt" (
    echo [ERROR] models\best.pt is missing.
    goto :end
)

REM --- 7. Launch ---
echo.
echo [run] Launching native window ...
echo       This console will stay open until the app window closes.
echo.
"%VPY%" -m app.main
set "RC=%ERRORLEVEL%"
echo.
echo [run] App exited with code %RC%

:end
echo.
echo ============================================================
echo  Done. Press any key to close this window.
echo ============================================================
pause
