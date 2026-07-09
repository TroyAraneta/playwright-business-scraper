@echo off
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\gui.py
) else (
  python src\gui.py
)
pause
