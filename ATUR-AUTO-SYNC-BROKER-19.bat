@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\register_idx_broker_task.ps1"
if errorlevel 1 (
  echo Gagal memasang Task Scheduler broker.
  pause
  exit /b 1
)
echo Berhasil memasang auto-sync broker harian jam 19:00.
pause
exit /b 0
