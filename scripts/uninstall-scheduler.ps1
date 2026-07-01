<#
.SYNOPSIS
  auction-arbitrage 정기 새로고침 작업스케줄러 등록 해제.

.PARAMETER TaskName
  제거할 작업 이름(기본 AuctionArbitrage-DailyRefresh).

.EXAMPLE
  .\scripts\uninstall-scheduler.ps1
  .\scripts\uninstall-scheduler.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "AuctionArbitrage-DailyRefresh"
)

$ErrorActionPreference = "Stop"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Output "작업 '$TaskName' 없음 — 할 일 없음."
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "제거 완료: $TaskName"
} else {
    Write-Output "[WhatIf] 작업 '$TaskName' 을 제거할 예정."
}
