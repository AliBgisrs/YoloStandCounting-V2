@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  YOLO Stand Counting -- PyInstaller build
echo ============================================================
echo Working dir: %CD%
echo.

REM --- 1. Pick Python: prefer 3.12 ---
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
    goto :end
)

%PYCMD% --version 2>nul | findstr /R "Python [0-9]" >nul
if errorlevel 1 (
    echo [ERROR] "%PYCMD%" looks like the Microsoft Store stub. Install real Python.
    goto :end
)

REM --- 2. venv ---
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating venv using %PYCMD% ...
    %PYCMD% -m venv .venv
    if errorlevel 1 ( echo [ERROR] venv creation failed. & goto :end )
)
set "VPY=%CD%\.venv\Scripts\python.exe"
echo Using venv: %VPY%
"%VPY%" --version
echo.

REM --- 3. Make sure deps are installed ---
if not exist ".venv\.installed" (
    echo [setup] Upgrading pip ...
    "%VPY%" -m pip install --upgrade pip
    echo [setup] Installing requirements -- slow first time...
    "%VPY%" -m pip install --prefer-binary -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Scroll up for the real error.
        goto :end
    )
    echo done> ".venv\.installed"
)

REM --- 4. Ensure PyInstaller is in the venv ---
"%VPY%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [setup] PyInstaller missing -- installing into venv ...
    "%VPY%" -m pip install pyinstaller
    if errorlevel 1 ( echo [ERROR] Could not install PyInstaller. & goto :end )
)

if not exist "models\best.pt" (
    echo [ERROR] models\best.pt is missing.
    goto :end
)

echo.
echo [build] Cleaning previous build ...
if exist "build" rmdir /S /Q "build"
if exist "dist"  rmdir /S /Q "dist"

echo [build] Running PyInstaller ...
"%VPY%" -m PyInstaller ^
    --noconfirm ^
    --windowed ^
    --name StandCounting ^
    --collect-all rasterio ^
    --collect-all fiona ^
    --collect-all pyproj ^
    --collect-all shapely ^
    --collect-all ultralytics ^
    --copy-metadata ultralytics ^
    --copy-metadata torch ^
    --copy-metadata numpy ^
    --copy-metadata opencv-python ^
    --copy-metadata Pillow ^
    --hidden-import rasterio._shim ^
    --hidden-import rasterio.vrt ^
    --hidden-import rasterio.sample ^
    --hidden-import fiona._shim ^
    --hidden-import fiona.schema ^
    --add-data "models\best.pt;models" ^
    app\main.py

if errorlevel 1 (
    echo [ERROR] PyInstaller failed. Scroll up for the real error.
    goto :end
)

echo.
echo [build] Done. Output:  dist\StandCounting\StandCounting.exe

:end
echo.
echo ----- Press any key to close this window -----
pause
