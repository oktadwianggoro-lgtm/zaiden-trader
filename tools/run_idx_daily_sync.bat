@echo off
setlocal
cd /d "%~dp0.."

set "LOG_DIR=%cd%\data"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\idx_auto_sync.log"

echo [%date% %time%] Mulai auto sync IDX >> "%LOG_FILE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.idx.co.id/id' -Method Head -TimeoutSec 20 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo [%date% %time%] Internet/IDX tidak tersedia, lewati sinkronisasi. >> "%LOG_FILE%"
  exit /b 0
)

set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (
  echo [%date% %time%] 1/2 Ringkasan Saham harian >> "%LOG_FILE%"
  "%CODEX_PY%" tools\sync_idx_daily.py --refresh-recent 10 --workers 3 --retries 4 --timeout 60 --delay 0.5 --max-days 40 >> "%LOG_FILE%" 2>&1
  echo [%date% %time%] 2/2 Ringkasan Indeks (IHSG dst) >> "%LOG_FILE%"
  "%CODEX_PY%" tools\sync_idx_index_daily.py --refresh-recent 10 --workers 2 --retries 4 --timeout 60 --delay 0.6 --max-days 40 >> "%LOG_FILE%" 2>&1
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  echo [%date% %time%] 1/2 Ringkasan Saham harian >> "%LOG_FILE%"
  py -3 tools\sync_idx_daily.py --refresh-recent 10 --workers 3 --retries 4 --timeout 60 --delay 0.5 --max-days 40 >> "%LOG_FILE%" 2>&1
  echo [%date% %time%] 2/2 Ringkasan Indeks (IHSG dst) >> "%LOG_FILE%"
  py -3 tools\sync_idx_index_daily.py --refresh-recent 10 --workers 2 --retries 4 --timeout 60 --delay 0.6 --max-days 40 >> "%LOG_FILE%" 2>&1
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  echo [%date% %time%] 1/2 Ringkasan Saham harian >> "%LOG_FILE%"
  python tools\sync_idx_daily.py --refresh-recent 10 --workers 3 --retries 4 --timeout 60 --delay 0.5 --max-days 40 >> "%LOG_FILE%" 2>&1
  echo [%date% %time%] 2/2 Ringkasan Indeks (IHSG dst) >> "%LOG_FILE%"
  python tools\sync_idx_index_daily.py --refresh-recent 10 --workers 2 --retries 4 --timeout 60 --delay 0.6 --max-days 40 >> "%LOG_FILE%" 2>&1
  exit /b %errorlevel%
)

echo [%date% %time%] Python 3 tidak ditemukan. >> "%LOG_FILE%"
exit /b 1
