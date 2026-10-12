@echo off
REM Ludodex - interface graphique (Windows, via MSYS2).
REM MSYS2 fournit Python, PyGObject (gi), PyCairo (cairo), PyYAML, GTK4 et
REM libadwaita : rien de tout cela ne vient de pip (voir docs\WINDOWS.md).
REM Adapte le chemin ci-dessous si MSYS2 n'est pas dans C:\msys64.
set "MSYS=C:\msys64"
if exist "%MSYS%\ucrt64\bin\pythonw.exe" (
    set "PYBIN=%MSYS%\ucrt64\bin"
) else (
    set "PYBIN=%MSYS%\mingw64\bin"
)
set "PATH=%PYBIN%;%PATH%"
start "" "%PYBIN%\pythonw.exe" "%~dp0gui.py" %*
