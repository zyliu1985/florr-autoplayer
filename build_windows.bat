@echo off
REM Build florr-auto-pathing.exe on Windows. Run from the repo root, in a venv
REM that has requirements.txt installed (pip install -r requirements.txt).
REM PyInstaller does not cross-compile — this MUST run on Windows, not on the
REM Mac dev box (see the "windows-is-real-deployment" project memory).

setlocal

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo [!] pyinstaller not found on PATH. Run: pip install -r requirements.txt
    exit /b 1
)

echo [*] Cleaning previous build...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

echo [*] Running PyInstaller (this takes a while the first time - torch is big)...
pyinstaller main.spec
if errorlevel 1 (
    echo [!] PyInstaller build failed.
    exit /b 1
)

echo [*] Copying maps\ next to the built exe...
xcopy /E /I /Y maps "dist\florr-auto-pathing\maps" >nul

echo.
echo [OK] Build done: dist\florr-auto-pathing\florr-auto-pathing.exe
echo.
echo [!] models\desert.pt (and sandstorm.pt if used) are gitignored and NOT
echo     bundled - copy them into dist\florr-auto-pathing\models\ yourself,
echo     same as running main.py unpackaged. See README.md for where to get
echo     desert.pt and the warning about verifying .pt sources before use.
echo.
echo Run it with florr.io already open fullscreen in Chrome, then:
echo     dist\florr-auto-pathing\florr-auto-pathing.exe

endlocal
