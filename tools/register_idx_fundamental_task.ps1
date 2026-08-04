$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runnerPath = Join-Path $projectRoot 'tools\run_idx_fundamental_sync.bat'
if (-not (Test-Path $runnerPath)) {
    throw "Runner tidak ditemukan: $runnerPath"
}

$taskName = 'ZaidenTrader-IdxFundamentalSync-1930'
$description = 'Sinkronisasi harian IDX ke SQLite jam 19:30 (setelah sinkronisasi harga harian jam 19:00): (1) snapshot PER/PBV/ROE, (2) backfill laporan keuangan historis, (3) rasio valuasi per periode, (4) deteksi corporate action.'

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$runnerPath`"`""
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30PM
$settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $description -Force | Out-Null

Write-Host "Task terpasang: $taskName"
Write-Host "Runner: $runnerPath"
Write-Host "Jadwal: setiap hari jam 19:30 (30 menit setelah sync harga harian jam 19:00)"
Write-Host "Kondisi: hanya saat koneksi internet tersedia"
