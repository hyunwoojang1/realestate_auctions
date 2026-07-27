# install-warm-ping.ps1 - registers AuctionArbitrage-WarmPing (every 5 min, battery-safe). Idempotent.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script   = Join-Path $RepoRoot "scripts\warm-ping.ps1"
$TaskName = "AuctionArbitrage-WarmPing"

$action   = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`""
# RepetitionDuration: [TimeSpan]::MaxValue serializes to invalid task XML on Win11 - use 10 years.
$trigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "registered: $TaskName (every 5 min). Uninstall: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
