$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runnerPath = Join-Path $projectRoot 'tools\run_idx_broker_sync.bat'
if (-not (Test-Path $runnerPath)) {
    throw "Runner broker tidak ditemukan: $runnerPath"
}

$taskName = 'ZaidenTrader-IdxBrokerDailySync-1900'
$description = 'Sinkronisasi ringkasan broker IDX ke SQLite setiap jam 19:00 secara bertahap dari 2020.'

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$runnerPath`"`""
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00PM
$settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $description -Force | Out-Null

Write-Host "Task broker terpasang: $taskName"
Write-Host "Runner: $runnerPath"
Write-Host "Jadwal: setiap hari jam 19:00"
Write-Host "Kondisi: hanya saat koneksi internet tersedia"
Write-Host "Mode: resume checkpoint, bertahap dari 2020"
