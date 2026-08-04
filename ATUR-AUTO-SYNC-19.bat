@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\register_idx_daily_task.ps1"
if errorlevel 1 (
  echo Gagal memasang Task Scheduler. Jalankan sebagai user yang sama dengan penggunaan aplikasi.
  pause
  exit /b 1
)
echo Berhasil memasang jadwal auto-sync IDX harian jam 19:00.
pause
exit /b 0
