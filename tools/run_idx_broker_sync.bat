@echo off
setlocal
cd /d "%~dp0.."

set "LOG_DIR=%cd%\data"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\idx_broker_auto_sync.log"

echo [%date% %time%] Mulai auto sync broker IDX >> "%LOG_FILE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.idx.co.id/id' -Method Head -TimeoutSec 20 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo [%date% %time%] Internet/IDX tidak tersedia, lewati sinkronisasi broker. >> "%LOG_FILE%"
  exit /b 0
)

set "CMD_ARGS=--db data\zaiden_trader.db fetch-idx-summary --start 2020-01-01 --delay 0.8 --max-days 40 --checkpoint data\.idx_broker_summary_checkpoint.json"

set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%CODEX_PY%" (
  "%CODEX_PY%" tools\idx_broker_pipeline.py %CMD_ARGS% >> "%LOG_FILE%" 2>&1
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\idx_broker_pipeline.py %CMD_ARGS% >> "%LOG_FILE%" 2>&1
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python tools\idx_broker_pipeline.py %CMD_ARGS% >> "%LOG_FILE%" 2>&1
  exit /b %errorlevel%
)

echo [%date% %time%] Python 3 tidak ditemukan untuk sinkronisasi broker. >> "%LOG_FILE%"
exit /b 1
