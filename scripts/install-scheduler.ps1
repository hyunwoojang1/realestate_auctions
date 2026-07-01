<#
.SYNOPSIS
  auction-arbitrage 정기 새로고침을 Windows 작업스케줄러에 **Disabled(비활성)** 로 등록.

.DESCRIPTION
  매일 지정 시각(기본 05:30)에 refresh-daily.ps1 -Live 를 실행하는 작업을 등록한다.
  단, 등록 직후 상태는 **Disabled** 다 — courtauction/국토부 이용약관을 사용자가 확인한 뒤
  수동으로 활성화해야 실제로 돈다:

      Enable-ScheduledTask -TaskName "AuctionArbitrage-DailyRefresh"

  이는 밤샘 자동화가 이용약관 확인 없이 실서버를 호출하는 것을 원천 차단하기 위함이다.

.PARAMETER Time
  실행 시각 HH:mm (기본 05:30).

.PARAMETER TaskName
  작업 이름(기본 AuctionArbitrage-DailyRefresh).

.PARAMETER Cash
  refresh-daily.ps1 에 넘길 가용현금 상한(원).

.PARAMETER WhatIf
  실제 등록 없이 무엇을 등록할지만 표시(검증용).

.EXAMPLE
  .\scripts\install-scheduler.ps1                 # 05:30, Disabled 로 등록
  .\scripts\install-scheduler.ps1 -Time 06:00 -WhatIf   # 등록 미리보기
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Time = "05:30",
    [string]$TaskName = "AuctionArbitrage-DailyRefresh",
    [long]$Cash = 100000000
)

$ErrorActionPreference = "Stop"

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$RefreshPs = Join-Path $RepoRoot "scripts\refresh-daily.ps1"
if (-not (Test-Path $RefreshPs)) { throw "refresh-daily.ps1 없음: $RefreshPs" }

# PowerShell 실행 파일(현재 호스트 기준)
$PwshExe = (Get-Process -Id $PID).Path
if (-not $PwshExe) { $PwshExe = "powershell.exe" }

$argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$RefreshPs`" -Live -Cash $Cash"

$action    = New-ScheduledTaskAction -Execute $PwshExe -Argument $argLine -WorkingDirectory $RepoRoot
$trigger   = New-ScheduledTaskTrigger -Daily -At $Time
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
                -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description "auction-arbitrage 전국 courtauction+국토부 시세 매칭 매일 새로고침. 이용약관 확인 후 Enable-ScheduledTask 로 활성화."

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task as DISABLED at $Time daily")) {
    # 기존 동일 이름 작업이 있으면 교체
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "기존 작업 '$TaskName' 제거 후 재등록."
    }

    Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null
    # 핵심: 등록 직후 즉시 Disabled 로 — 사용자가 약관 확인 후 수동 Enable.
    Disable-ScheduledTask -TaskName $TaskName | Out-Null

    $reg = Get-ScheduledTask -TaskName $TaskName
    Write-Output "등록 완료: $TaskName"
    Write-Output ("  상태(State): {0}   ← Disabled 여야 정상" -f $reg.State)
    Write-Output ("  트리거     : 매일 {0}" -f $Time)
    Write-Output ("  실행       : {0} {1}" -f $PwshExe, $argLine)
    Write-Output ""
    Write-Output "활성화(이용약관 확인 후): Enable-ScheduledTask -TaskName `"$TaskName`""
} else {
    # -WhatIf 경로: 실제 등록 없이 계획만 표시
    Write-Output "[WhatIf] 다음 작업을 DISABLED 로 등록할 예정:"
    Write-Output ("  TaskName : {0}" -f $TaskName)
    Write-Output ("  Trigger  : 매일 {0}" -f $Time)
    Write-Output ("  Execute  : {0}" -f $PwshExe)
    Write-Output ("  Argument : {0}" -f $argLine)
    Write-Output ("  초기상태 : Disabled (등록 직후 Disable-ScheduledTask 호출)")
    Write-Output "  활성화   : Enable-ScheduledTask -TaskName `"$TaskName`" (약관 확인 후 수동)"
}
