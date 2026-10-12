@echo off
REM Ludodex - ligne de commande (Windows, via MSYS2).
REM MSYS2 fournit Python et PyYAML (voir docs\WINDOWS.md).
REM Adapte le chemin ci-dessous si MSYS2 n'est pas dans C:\msys64.
set "MSYS=C:\msys64"
if exist "%MSYS%\ucrt64\bin\python.exe" (
    set "PYBIN=%MSYS%\ucrt64\bin"
) else (
    set "PYBIN=%MSYS%\mingw64\bin"
)
set "PATH=%PYBIN%;%PATH%"
"%PYBIN%\python.exe" "%~dp0picker.py" %*
