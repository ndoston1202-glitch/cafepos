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

rem Telefon/planshetdan kirish uchun fayervol ruxsati (bir marta so'raladi)
netsh advfirewall firewall show rule name="CafePOS" >nul 2>&1
if errorlevel 1 (
    echo Telefon va planshetlar ulanishi uchun Windows ruxsat so'raydi - "Da / Yes" ni bosing.
    call "%~dp0TARMOQQA_RUXSAT.bat"
)

python server.py
echo.
echo Server to'xtadi. Yuqoridagi xabarni o'qing.
pause
