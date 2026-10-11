@echo off
REM Windows: GUI za lokalno potpisivanje PDF-a (pokreni iz mape projekta).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Nema .venv - vidi README ^(Instalacija^).
  exit /b 1
)
".venv\Scripts\python.exe" potpisi-gui.py %*
