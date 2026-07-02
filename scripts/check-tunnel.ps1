<#
.SYNOPSIS
  터널 접속 사전점검 — Tailscale / Cloudflare Tunnel 설치 여부와 로컬 서버 포트 상태를 점검한다.

.DESCRIPTION
  폰 등 외부에서 올-로컬 waitress 서버(127.0.0.1:PORT)에 안전하게 접속하기 전에
  다음을 점검한다(외부 실서버 호출 없음, 순수 로컬 진단):

    1) tailscale CLI 설치·로그인·이 노드의 Tailnet IP(100.x)
    2) cloudflared CLI 설치 여부
    3) 로컬 서버 포트(기본 8000) LISTEN 여부 + 어떤 호스트에 바인드됐는지
       (127.0.0.1 로컬전용 / 0.0.0.0 전체 — 터널 방식에 따라 필요 바인드가 다름)
    4) 방화벽에서 해당 포트 인바운드 규칙 존재 여부(참고용)

  각 항목을 OK / WARN / MISSING 으로 표시하고, 마지막에 권장 다음 단계를 출력한다.
  이 스크립트는 진단만 한다 — 터널을 켜거나 서버를 띄우지 않는다.

.PARAMETER Port
  점검할 로컬 서버 포트(기본 8000, scripts\start.ps1 과 동일).

.PARAMETER OutFile
  결과를 텍스트로 저장할 경로(선택). 콘솔 출력과 동일 내용을 파일로도 남긴다.

.EXAMPLE
  .\scripts\check-tunnel.ps1
  .\scripts\check-tunnel.ps1 -Port 8080 -OutFile evidence\tunnel_guide.txt
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$OutFile = ""
)

$ErrorActionPreference = "Continue"

# --- 결과를 콘솔+파일 양쪽으로 남기기 위한 버퍼 ---
$script:Lines = New-Object System.Collections.Generic.List[string]
function Emit([string]$s = "") {
    $script:Lines.Add($s)
    Write-Host $s
}

function Get-CommandPath([string]$name) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if ($c) { return $c.Source } else { return $null }
}

Emit "=== auction-arbitrage tunnel preflight (LOCAL ONLY, no external calls) ==="
Emit ("generated : " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"))
Emit ("host      : " + [System.Net.Dns]::GetHostName())
Emit ("port      : $Port  (local waitress; scripts\start.ps1)")
Emit "------------------------------------------------------------"

# ============================================================
# [1] Tailscale
# ============================================================
Emit ""
Emit "[1] Tailscale"
$tsPath = Get-CommandPath "tailscale"
if (-not $tsPath) {
    # 기본 설치 경로도 확인(PATH 에 없을 수 있음)
    $tsDefault = "C:\Program Files\Tailscale\tailscale.exe"
    if (Test-Path $tsDefault) { $tsPath = $tsDefault }
}
if (-not $tsPath) {
    Emit "    MISSING  tailscale CLI 미설치 (PATH/기본경로에 없음)"
    Emit "             설치: winget install tailscale.tailscale"
} else {
    Emit "    OK       tailscale : $tsPath"
    $tsStatus = & $tsPath status 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0 -or $tsStatus -match "Logged out|NeedsLogin|stopped") {
        Emit "    WARN     로그인/연결 안 됨 — 'tailscale up' 실행 필요"
    } else {
        Emit "    OK       tailnet 연결됨"
    }
    # 이 노드의 Tailnet IPv4 (100.64.0.0/10 CGNAT 대역)
    $tsIp = & $tsPath ip -4 2>&1 | Select-Object -First 1
    if ($tsIp -match "^100\.") {
        Emit "    OK       tailnet IP : $tsIp  ->  폰에서 http://$tsIp`:$Port"
    } else {
        Emit "    WARN     tailnet IP 확인 불가 (연결 후 재시도)"
    }
}

# ============================================================
# [2] Cloudflare Tunnel (cloudflared)
# ============================================================
Emit ""
Emit "[2] Cloudflare Tunnel (cloudflared)"
$cfPath = Get-CommandPath "cloudflared"
if (-not $cfPath) {
    Emit "    MISSING  cloudflared CLI 미설치"
    Emit "             설치: winget install --id Cloudflare.cloudflared"
    Emit "             빠른 임시터널: cloudflared tunnel --url http://127.0.0.1:$Port"
} else {
    Emit "    OK       cloudflared : $cfPath"
    $cfVer = & $cfPath --version 2>&1 | Select-Object -First 1
    Emit "    OK       version : $cfVer"
}

# ============================================================
# [3] 로컬 서버 포트 LISTEN 상태
# ============================================================
Emit ""
Emit "[3] 로컬 서버 포트 상태 (port $Port)"
$listeners = @()
try {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop
} catch {
    $listeners = @()
}
if (-not $listeners -or $listeners.Count -eq 0) {
    Emit "    WARN     포트 $Port LISTEN 없음 — 서버 미기동."
    Emit "             먼저 서버를 띄우세요: .\scripts\start.ps1 -Port $Port"
} else {
    $addrs = ($listeners | Select-Object -ExpandProperty LocalAddress -Unique)
    foreach ($a in $addrs) {
        if ($a -eq "127.0.0.1" -or $a -eq "::1") {
            Emit "    OK       LISTEN $a`:$Port  (로컬전용 — Tailscale/터널 데몬이 로컬에서 프록시하는 방식에 적합)"
        } elseif ($a -eq "0.0.0.0" -or $a -eq "::") {
            Emit "    OK       LISTEN $a`:$Port  (전체 바인드 — Tailnet 다른 기기가 직접 접속 가능. start.ps1 -BindAll)"
        } else {
            Emit "    OK       LISTEN $a`:$Port"
        }
    }
}

# ============================================================
# [4] 방화벽 인바운드 규칙 (참고)
# ============================================================
Emit ""
Emit "[4] 방화벽 인바운드 규칙 (참고용)"
try {
    $fw = Get-NetFirewallRule -Direction Inbound -Enabled True -ErrorAction Stop |
        Where-Object { $_.DisplayName -match "auction|waitress|Tailscale" }
    if ($fw) {
        foreach ($r in $fw) { Emit ("    INFO     rule: " + $r.DisplayName) }
    } else {
        Emit "    INFO     auction/waitress 관련 인바운드 규칙 없음."
        Emit "             Tailnet 직접접속(0.0.0.0 바인드) 시에만 포트 개방 필요:"
        Emit "             New-NetFirewallRule -DisplayName 'auction-arbitrage $Port' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
        Emit "             (Cloudflare/Tailscale 로컬 프록시 방식은 인바운드 개방 불필요 — 아웃바운드로 동작)"
    }
} catch {
    Emit "    INFO     방화벽 규칙 조회 불가(권한 부족일 수 있음) — 건너뜀."
}

# ============================================================
# 요약 / 다음 단계
# ============================================================
Emit ""
Emit "------------------------------------------------------------"
Emit "다음 단계 (docs\remote-access.md 참고):"
if ($tsPath) {
    Emit "  A. Tailscale(권장, 비공개): 서버 기동 후 폰에서 http://<tailnet-ip>:$Port 접속."
} else {
    Emit "  A. Tailscale 설치 -> 'tailscale up' -> 폰에도 Tailscale 로그인 -> tailnet IP 로 접속."
}
if ($cfPath) {
    Emit "  B. Cloudflare(공개 URL): cloudflared tunnel --url http://127.0.0.1:$Port"
} else {
    Emit "  B. Cloudflare 설치 후 임시터널로 https 공개 URL 발급 가능."
}
Emit "  주의: 공개 노출 전 인증/접근제한을 반드시 확인(현재 서버는 무인증)."
Emit "------------------------------------------------------------"

# --- 파일 저장(선택) ---
if ($OutFile) {
    $dir = Split-Path -Parent $OutFile
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $script:Lines | Out-File -FilePath $OutFile -Encoding utf8
    Write-Host ""
    Write-Host "[saved] $OutFile"
}
