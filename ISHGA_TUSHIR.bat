@echo off
chcp 65001 >nul
title CafePOS
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python topilmadi!
    echo https://www.python.org/downloads/ dan o'rnating va "Add Python to PATH" ni belgilang.
    pause
    exit /b 1
)

python server.py
echo.
echo Server to'xtadi. Yuqoridagi xabarni o'qing.
pause
