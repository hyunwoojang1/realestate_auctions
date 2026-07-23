# install-watchdog.ps1 - registers AuctionArbitrage-Watchdog (every 30 min, battery-safe). Idempotent.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script   = Join-Path $RepoRoot "scripts\watchdog.ps1"
$TaskName = "AuctionArbitrage-Watchdog"

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
# RepetitionDuration: [TimeSpan]::MaxValue serializes to invalid task XML on Win11 - use 10 years.
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "registered: $TaskName (every 30 min). Uninstall: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
