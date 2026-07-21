@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CS2 Voice Overlay 1.0.5 — Setup

echo.
echo  ============================================
echo   CS2 Voice Overlay 1.0.5 — Einmal-Setup
echo   Macht das Programm auf DIESEM PC lauffaehig
echo  ============================================
echo.

set "USE_PY_LAUNCHER=0"
where py >nul 2>&1 && set "USE_PY_LAUNCHER=1"
if "%USE_PY_LAUNCHER%"=="0" (
  where python >nul 2>&1
  if errorlevel 1 (
    echo.
    echo  FEHLER: Python 3 fehlt.
    echo.
    echo  1^) https://www.python.org/downloads/
    echo  2^) Python 3.11 oder 3.12 installieren
    echo  3^) WICHTIG: Haken "Add python.exe to PATH"
    echo  4^) PC neu starten, dann setup.bat erneut
    echo.
    start "" "https://www.python.org/downloads/"
    pause
    exit /b 1
  )
)

echo  [1/5] Python:
if "%USE_PY_LAUNCHER%"=="1" ( py -3 --version ) else ( python --version )
if errorlevel 1 (
  echo  FEHLER: Python startet nicht.
  pause
  exit /b 1
)

echo  [2/5] Virtuelle Umgebung .venv ...
if not exist ".venv\Scripts\python.exe" (
  if "%USE_PY_LAUNCHER%"=="1" ( py -3 -m venv .venv ) else ( python -m venv .venv )
  if errorlevel 1 (
    echo  FEHLER: venv fehlgeschlagen.
    pause
    exit /b 1
  )
) else (
  echo        vorhanden.
)

set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo  FEHLER: .venv\Scripts\python.exe fehlt.
  pause
  exit /b 1
)

echo  [3/5] pip ...
"%VPY%" -m pip install --upgrade pip wheel
if errorlevel 1 echo  WARNUNG: pip-Upgrade problematisch — weiter...

echo  [4/5] Abhaengigkeiten ^(Internet, 2-10 Min^) ...
"%VPY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo  FEHLER: pip install fehlgeschlagen.
  echo  Internet/Firewall/Antivirus pruefen, setup.bat erneut.
  pause
  exit /b 1
)

echo  [5/5] Import + optional Whisper-Modell "base" vorladen ...
"%VPY%" -c "import PySide6, soundcard, numpy, faster_whisper, deep_translator; print('Module OK')"
if errorlevel 1 (
  echo  FEHLER: Module fehlen.
  pause
  exit /b 1
)

REM Preload default Whisper model so first game start is faster
"%VPY%" -c "from faster_whisper import WhisperModel; print('Lade Whisper base (einmalig)...'); WhisperModel('base', device='cpu', compute_type='int8'); print('Whisper base bereit.')"
if errorlevel 1 (
  echo  WARNUNG: Modell-Preload fehlgeschlagen — wird beim ersten Start nachgeholt.
)

echo.
echo  ============================================
echo   Setup fertig auf diesem PC.
echo   Ab jetzt: start.bat doppelklicken
echo  ============================================
echo.
pause
endlocal
exit /b 0
