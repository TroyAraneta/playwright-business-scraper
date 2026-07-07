@echo off
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\maps_batch_gui.py
) else (
  python src\maps_batch_gui.py
)
pause
