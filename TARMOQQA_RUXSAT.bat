@echo off
chcp 65001 >nul
title CafePOS - tarmoqqa ruxsat
cd /d "%~dp0"

rem Administrator huquqi kerak - bo'lmasa o'zini qayta so'rab ishga tushiradi
net session >nul 2>&1
if errorlevel 1 (
    echo Administrator huquqi so'ralmoqda...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set PORT=8000
if not "%CAFEPOS_PORT%"=="" set PORT=%CAFEPOS_PORT%

netsh advfirewall firewall delete rule name="CafePOS" >nul 2>&1
netsh advfirewall firewall add rule name="CafePOS" dir=in action=allow protocol=TCP localport=%PORT% profile=any
if errorlevel 1 (
    echo.
    echo Ruxsat berib bo'lmadi.
) else (
    echo.
    echo Tayyor! Endi shu Wi-Fi'dagi telefon, planshet va kompyuterlar CafePOS'ga kira oladi.
    echo Manzil: dasturdagi Sozlamalar bo'limida yoki qora oynada ko'rsatilgan.
)
pause
