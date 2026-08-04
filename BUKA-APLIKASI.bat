@echo off
cd /d "%~dp0"

rem Coba .venv dulu (sudah ada scikit-learn untuk ML)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app.py
  exit /b
)

rem Fallback ke Codex Python
set CODEX=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
if exist "%CODEX%" (
  "%CODEX%" app.py
  exit /b
)

rem Fallback system Python
python app.py
