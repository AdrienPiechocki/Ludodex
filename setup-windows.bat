@echo off
REM Ludodex - installe sous Windows TOUTES les dependances, via pacman (MSYS2).
REM
REM gi (PyGObject) et cairo (PyCairo) n'ont aucun wheel Windows sur PyPI : ils
REM viennent de MSYS2, comme GTK4, libadwaita, PyYAML, pip et les icones Adwaita.
REM Voir docs\WINDOWS.md pour l'alternative gvsbuild (Python de python.org).
setlocal
set "MSYS=C:\msys64"
if not exist "%MSYS%\usr\bin\bash.exe" (
    echo MSYS2 introuvable dans %MSYS%.
    echo Installe-le depuis https://www.msys2.org/ puis relance ce script.
    exit /b 1
)

echo [1/2] Mise a jour de MSYS2...
"%MSYS%\usr\bin\bash.exe" -lc "pacman -Syu --noconfirm"
if errorlevel 1 (
    echo La mise a jour a echoue. Ferme tout terminal MSYS2 et relance ce script.
    exit /b 1
)

echo [2/2] Installation des paquets Ludodex...
"%MSYS%\usr\bin\bash.exe" -lc "pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-pip mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-python-cairo mingw-w64-ucrt-x86_64-python-yaml mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita mingw-w64-ucrt-x86_64-adwaita-icon-theme mingw-w64-ucrt-x86_64-hicolor-icon-theme"
if errorlevel 1 (
    echo L'installation a echoue.
    exit /b 1
)

echo.
echo Termine. Lance "ludodex-cli.bat --help" pour verifier, puis "ludodex.bat".
exit /b 0
