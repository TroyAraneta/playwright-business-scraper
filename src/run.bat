@echo off
call "%~dp0..\.venv\Scripts\activate"
setlocal

set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python runtime not found at:
  echo %PYTHON_EXE%
  exit /b 1
)

"%PYTHON_EXE%" "%~dp0app.py" %*
