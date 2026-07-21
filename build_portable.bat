@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title CS2 Voice Overlay — portable EXE bauen

echo.
echo  Baut portablen Ordner: dist\CS2-Voice-Overlay\
echo  Den Ordner ZIP-en und an Freunde schicken (Windows).
echo.

if not exist ".venv\Scripts\python.exe" (
  echo  Zuerst setup.bat ausfuehren.
  pause
  exit /b 1
)

set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install -U pyinstaller
if errorlevel 1 (
  echo  PyInstaller fehlgeschlagen.
  pause
  exit /b 1
)

if exist "dist\CS2-Voice-Overlay" rmdir /s /q "dist\CS2-Voice-Overlay"
if exist "build" rmdir /s /q "build"
if exist "CS2-Voice-Overlay.spec" del /q "CS2-Voice-Overlay.spec"

REM console=True so Freunde Fehler sehen (nicht silent crash)
"%VPY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --console ^
  --name "CS2-Voice-Overlay" ^
  --paths "%~dp0" ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all tokenizers ^
  --collect-all onnxruntime ^
  --hidden-import soundcard ^
  --hidden-import deep_translator ^
  --hidden-import bootstrap ^
  --hidden-import paths ^
  --hidden-import capture ^
  --hidden-import config ^
  --hidden-import cs2_callouts ^
  --hidden-import languages ^
  --hidden-import memory_db ^
  --hidden-import overlay ^
  --hidden-import pipeline ^
  --hidden-import speech_filter ^
  --hidden-import sfx_memory ^
  --hidden-import stt ^
  --hidden-import translate ^
  --add-data "VERSION.txt;." ^
  --add-data "requirements.txt;." ^
  main.py

if errorlevel 1 (
  echo.
  echo  Build fehlgeschlagen.
  pause
  exit /b 1
)

REM README for friends next to exe
(
echo CS2 Voice Overlay — portable
echo.
echo 1^) CS2-Voice-Overlay.exe starten
echo 2^) Erster Start: Whisper-Modell wird ggf. heruntergeladen ^(Internet^)
echo 3^) CS2 Borderless Windowed
echo 4^) AUDIO = Headset wo Team-Voice laeuft
echo 5^) Pegelbalken muss zucken
echo 6^) Esc = Beenden
echo.
echo Bei schwachem PC:  CS2-Voice-Overlay.exe --model tiny
) > "dist\CS2-Voice-Overlay\LIESMICH.txt"

echo.
echo  ============================================
echo   Fertig: dist\CS2-Voice-Overlay\
echo   ZIP diesen Ordner und schick ihn weiter.
echo  ============================================
echo.
pause
endlocal
exit /b 0
