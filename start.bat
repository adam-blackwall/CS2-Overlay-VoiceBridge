@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CS2 Voice Overlay 1.0.5

echo.
echo  ============================================
echo   CS2 Voice Overlay  1.0.5
echo   NUR Counter-Strike 2  (Team-Voice / Callouts)
echo  ============================================
echo.
echo  Tipps: Borderless ^| AUDIO=Headset ^| Pegel zuckt ^| Esc=Ende
echo.

set "VPY=%~dp0.venv\Scripts\python.exe"

if not exist "%VPY%" (
  echo  Erster Start auf diesem PC — Setup laeuft...
  echo.
  call "%~dp0setup.bat"
  if not exist "%VPY%" (
    echo  Setup fehlgeschlagen.
    pause
    exit /b 1
  )
)

"%VPY%" -c "import PySide6, soundcard, numpy, faster_whisper, deep_translator" >nul 2>&1
if errorlevel 1 (
  echo  Pakete unvollstaendig — Setup erneut...
  call "%~dp0setup.bat"
  "%VPY%" -c "import PySide6, soundcard, numpy, faster_whisper, deep_translator" >nul 2>&1
  if errorlevel 1 (
    echo  FEHLER: Setup unvollstaendig.
    pause
    exit /b 1
  )
)

set PYTHONUNBUFFERED=1
echo  Starte...
echo.
"%VPY%" -u "%~dp0main.py" %*
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo  Fehlercode %ERR%
  if exist "%~dp0crash.log" (
    echo  --- crash.log ---
    type "%~dp0crash.log"
  )
  echo.
  echo  Hilfe: setup.bat  ^|  start.bat --model tiny  ^|  AUDIO-Geraet
  pause
  exit /b %ERR%
)
echo.
echo  Beendet.
timeout /t 2 >nul
endlocal
exit /b 0
