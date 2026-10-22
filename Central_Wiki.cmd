@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local.ps1"
if errorlevel 1 (
    echo.
    echo O launcher foi encerrado com erro. Consulte a mensagem acima e os logs em data.
    pause
)
endlocal
