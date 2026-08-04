@echo off
setlocal
cd /d "%~dp0.."

set "LOG_DIR=%cd%\data"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG_FILE=%LOG_DIR%\idx_fundamental_auto_sync.log"

echo [%date% %time%] Mulai auto sync fundamental IDX >> "%LOG_FILE%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.idx.co.id/id' -Method Head -TimeoutSec 20 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo [%date% %time%] Internet/IDX tidak tersedia, lewati sinkronisasi. >> "%LOG_FILE%"
  exit /b 0
)

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo [%date% %time%] Python 3 tidak ditemukan. >> "%LOG_FILE%"
  exit /b 1
)

echo [%date% %time%] 1/4 Snapshot PER/PBV/ROE terkini >> "%LOG_FILE%"
%PYEXE% tools\sync_idx_fundamental_yfinance.py >> "%LOG_FILE%" 2>&1

echo [%date% %time%] 2/4 Backfill laporan keuangan historis (maksimal kedalaman Yahoo Finance) >> "%LOG_FILE%"
%PYEXE% tools\sync_idx_fundamental_history.py >> "%LOG_FILE%" 2>&1

echo [%date% %time%] 3/4 Hitung ulang rasio valuasi per periode (PER/PBV kuartalan/tahunan) >> "%LOG_FILE%"
%PYEXE% tools\build_fundamental_ratio_history.py >> "%LOG_FILE%" 2>&1

echo [%date% %time%] 4/4 Deteksi corporate action (stock split/reverse split/rights issue) >> "%LOG_FILE%"
%PYEXE% tools\detect_corporate_actions.py >> "%LOG_FILE%" 2>&1

echo [%date% %time%] Selesai auto sync fundamental IDX >> "%LOG_FILE%"
exit /b 0
