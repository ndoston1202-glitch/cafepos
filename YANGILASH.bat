@echo off
chcp 65001 >nul
title CafePOS - yangilash
cd /d "%~dp0"
set GIT_TERMINAL_PROMPT=0

where git >nul 2>nul
if errorlevel 1 (
    echo Git topilmadi. https://git-scm.com/download/win dan o'rnating.
    pause
    exit /b 1
)

echo GitHub dan yangi versiya olinmoqda...
git pull --ff-only
if errorlevel 1 (
    echo.
    echo Yangilab bo'lmadi. Internetni tekshiring.
) else (
    echo.
    echo Tayyor! Endi ISHGA_TUSHIR.bat ni qayta ishga tushiring.
)
pause
