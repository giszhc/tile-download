@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "tile-download.exe" goto runexe

where py >nul 2>nul
if %errorlevel%==0 goto runpy

where python >nul 2>nul
if %errorlevel%==0 goto runpython

echo.
echo 没找到 tile-download.exe，也没检测到 Python。
echo 请到 Releases 页面下载 Windows 版（压缩包里带 exe）。
echo.
pause
exit /b 1

:runexe
"tile-download.exe"
goto done

:runpy
py -3 tile_download.py
goto done

:runpython
python tile_download.py

:done
echo.
pause
