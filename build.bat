@echo off
REM Ludodex - construit l'application autonome sous Windows (miroir de build.sh).
REM
REM Produit build\ludodex\ avec ludodex.exe (interface), ludodex-cli.exe (CLI)
REM et _internal\ (Python, GTK4, libadwaita, icones et traductions embarques).
REM Pre requis : MSYS2 avec les paquets de setup-windows.bat, plus PyInstaller
REM dans l'interpreteur MSYS2 (installe automatiquement ci-dessous).
setlocal
set "MSYS=C:\msys64"
if exist "%MSYS%\ucrt64\bin\python.exe" (
    set "PYBIN=%MSYS%\ucrt64\bin"
) else (
    set "PYBIN=%MSYS%\mingw64\bin"
)
set "PATH=%PYBIN%;%PATH%"

"%PYBIN%\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip est absent de l interpreteur MSYS2 - relance setup-windows.bat.
    exit /b 1
)

"%PYBIN%\python.exe" -m pip install --quiet pyinstaller
if errorlevel 1 (
    echo Impossible d installer PyInstaller.
    exit /b 1
)

cd /d "%~dp0"
"%PYBIN%\python.exe" -m PyInstaller ludodex.spec --noconfirm --distpath build --workpath .build-cache %*
exit /b %errorlevel%
