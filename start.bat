@echo off
setlocal
cd /d "%~dp0"
title CS2 Voice Overlay 1.0.2
echo.
echo  ============================================
echo   CS2 Voice Overlay  1.0.2
echo   NUR Counter-Strike 2  (Team-Voice / Callouts)
echo  ============================================
echo.
echo  Empfohlen:
echo   1) CS2 Borderless Windowed
echo   2) AUDIO - OUT = Headset (wo du Team hoerst)
echo      oder MIC zum Testen deiner eigenen Calls
echo   3) Pegelbalken muss zucken bei Voice
echo   4) Konsole: [tick] / [heard]
echo   5) Beenden: Esc
echo.
echo  model tiny=schnell  base=standard  small=genauer
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo FEHLER: python nicht gefunden.
  pause
  exit /b 1
)

set PYTHONUNBUFFERED=1
python -u main.py %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo Fehlercode %ERR%
  if exist crash.log type crash.log
  pause
  exit /b %ERR%
)
echo.
echo Beendet.
timeout /t 2 >nul
endlocal
