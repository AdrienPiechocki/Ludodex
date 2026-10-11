@echo off
REM Ludodex — interface graphique (Windows, via MSYS2).
REM Adapte le chemin ci-dessous si MSYS2 n'est pas installé dans C:\msys64.
set "MSYS=C:\msys64"
set "PATH=%MSYS%\mingw64\bin;%PATH%"
start "" "%MSYS%\mingw64\bin\pythonw.exe" "%~dp0gui.py" %*
