@echo off
REM Ludodex — ligne de commande (Windows, via MSYS2).
REM Adapte le chemin ci-dessous si MSYS2 n'est pas installé dans C:\msys64.
set "MSYS=C:\msys64"
set "PATH=%MSYS%\mingw64\bin;%PATH%"
"%MSYS%\mingw64\bin\python.exe" "%~dp0picker.py" %*
