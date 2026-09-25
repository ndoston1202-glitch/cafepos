@echo off
chcp 65001 >nul
title CafePOS - yangilash

rem git pull shu faylning o'zini ham yangilashi mumkin - shuning uchun nusxadan ishlaymiz
if not "%~1"=="--nusxa" (
    copy /y "%~f0" "%TEMP%\cafepos_yangilash.bat" >nul
    call "%TEMP%\cafepos_yangilash.bat" --nusxa "%~dp0"
    exit /b
)
set "APPDIR=%~2"
cd /d "%APPDIR%"
set GIT_TERMINAL_PROMPT=0

where git >nul 2>nul
if errorlevel 1 (
    echo Git topilmadi. https://git-scm.com/download/win dan o'rnating.
    pause
    exit /b 1
)

echo GitHub dan yangi versiya olinmoqda...
git pull --ff-only
if errorlevel 1 goto failed

rem Yangi kod kuchga kirishi uchun orqa fondagi serverni qayta ishga tushiramiz
if not exist "cafepos.pid" goto started
set /p PID=<"cafepos.pid"
taskkill /PID %PID% /F >nul 2>&1
del "cafepos.pid" >nul 2>&1
:started
echo.
echo Tayyor! CafePOS yangi versiyada ochilmoqda...
call "%APPDIR%ISHGA_TUSHIR.bat"
timeout /t 3 >nul
exit /b 0

:failed
echo.
echo Yangilab bo'lmadi. Internetni tekshiring.
pause
