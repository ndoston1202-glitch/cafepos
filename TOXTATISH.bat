@echo off
chcp 65001 >nul
title CafePOS - to'xtatish
cd /d "%~dp0"

if not exist "cafepos.pid" goto notrunning
set /p PID=<"cafepos.pid"
taskkill /PID %PID% /F >nul 2>&1
del "cafepos.pid" >nul 2>&1
echo CafePOS serveri to'xtatildi.
echo Telefon va planshetlar ham endi ulana olmaydi.
goto end

:notrunning
echo CafePOS serveri ishlamayapti.

:end
pause
