# 원격 접속 가이드 — 올-로컬 서버를 폰에서 안전하게 (Tailscale / Cloudflare Tunnel)

auction-arbitrage 웹은 로컬 PC에서 waitress WSGI 서버로 뜬다(기본 `http://127.0.0.1:8000`).
이 문서는 **집 밖의 폰**에서 그 서버에 안전하게 접속하는 두 가지 방법을 정리한다.

- **방식 A — Tailscale (권장·비공개)**: 내 기기들만 이루는 사설 VPN(tailnet). 인터넷에 공개되지
  않고, 내 폰·PC 사이 암호화 P2P 로만 접속. 무인증 서버라도 tailnet 밖에서는 보이지 않아 안전.
- **방식 B — Cloudflare Tunnel (공개 URL)**: `https://<랜덤>.trycloudflare.com` 같은 **공개 URL**
  을 즉석 발급. 링크를 아는 누구나 접근 가능하므로, **공개 전 반드시 접근제한**이 필요하다.

> 현재 서버는 인증이 없다(`/health`, `/api/listings`, `/` 모두 공개). Tailscale 는 네트워크 경계로
> 이를 가려주지만, Cloudflare 공개 URL 은 그렇지 않다. 아래 "보안" 절을 반드시 읽을 것.

---

## 0. 사전점검 (먼저 실행)

무엇이 설치돼 있고 서버가 떠 있는지 한 번에 진단:

```powershell
Set-Location "C:\Users\notebiz765\장현우\auction-arbitrage"
.\scripts\check-tunnel.ps1                 # 포트 8000 점검
.\scripts\check-tunnel.ps1 -Port 8080      # 다른 포트일 때
```

이 스크립트는 **로컬 진단만** 한다(외부 서버 호출 없음):
tailscale/cloudflared 설치·로그인 여부, tailnet IP, 로컬 포트 LISTEN 상태·바인드 주소,
방화벽 규칙을 `OK / WARN / MISSING` 으로 보여주고 권장 다음 단계를 출력한다.

---

## 1. 서버 띄우기 (공통)

원격 접속 전에 로컬 서버가 떠 있어야 한다.

```powershell
Set-Location "C:\Users\notebiz765\장현우\auction-arbitrage"
.\scripts\start.ps1                 # 127.0.0.1:8000 (로컬 전용 바인드)
```

- **바인드 주소가 방식에 따라 다르다:**
  - **Cloudflare Tunnel** 또는 **PC에서 로컬 실행되는 Tailscale 프록시** 방식이면
    기본값 `127.0.0.1` 로 충분하다(터널 데몬이 같은 PC의 localhost 로 프록시).
  - **Tailnet 안의 다른 기기가 PC IP로 직접** 접속하게 하려면 전체 인터페이스에 바인드:
    ```powershell
    .\scripts\start.ps1 -BindAll      # 0.0.0.0:8000
    ```
    이 경우 Windows 방화벽 인바운드 개방이 필요할 수 있다(아래 참고).

---

## 방식 A — Tailscale (권장, 비공개)

### A-1. 설치 & 로그인 (PC)

```powershell
winget install tailscale.tailscale
tailscale up                        # 브라우저로 로그인(Google/GitHub/MS 등)
tailscale ip -4                     # 이 PC 의 tailnet IP (100.x.y.z) 확인
```

### A-2. 폰에 Tailscale 설치

- iOS: App Store / Android: Play 스토어에서 "Tailscale" 설치 후 **같은 계정**으로 로그인.
- 폰과 PC 가 같은 tailnet 에 들어오면 서로 사설 IP 로 보인다.

### A-3. 접속

- 서버를 **전체 바인드**로 띄우고(`start.ps1 -BindAll`), 폰 브라우저에서:
  ```
  http://<PC-tailnet-IP>:8000        예: http://100.101.102.103:8000
  ```
- PC-tailnet-IP 는 `tailscale ip -4` 또는 `check-tunnel.ps1` [1] 항목에서 확인.

### A-4. (선택) MagicDNS / HTTPS

- Tailscale admin 에서 MagicDNS 를 켜면 IP 대신 `http://<머신이름>:8000` 사용 가능.
- `tailscale serve` / `tailscale funnel` 로 HTTPS 종단·공개 노출도 가능하나, 여기서는
  기본 사설 접속만 다룬다(공개가 필요하면 방식 B 또는 funnel + 인증).

**장점**: 인터넷에 포트를 열지 않는다. tailnet 밖에서는 서버가 아예 안 보임 → 무인증이어도 안전.
**단점**: 접속하는 폰마다 Tailscale 로그인이 필요(내 기기 전용).

---

## 방식 B — Cloudflare Tunnel (공개 URL, 즉석)

로그인·계정 없이 임시 공개 URL 을 즉석 발급하는 "quick tunnel" 방식.

### B-1. 설치

```powershell
winget install --id Cloudflare.cloudflared
cloudflared --version
```

### B-2. 임시 터널 실행

서버가 `127.0.0.1:8000` 에 떠 있는 상태에서:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

- 실행 로그에 `https://<랜덤>.trycloudflare.com` 형태의 **공개 URL** 이 찍힌다.
- 폰 브라우저에서 그 URL 로 접속. HTTPS 가 기본 제공된다.
- 이 터널은 아웃바운드 연결로 동작하므로 **인바운드 방화벽 개방이 불필요**하다.
- 프로세스를 끄면 URL 도 사라진다(임시). 고정 도메인이 필요하면 named tunnel + Cloudflare 계정 설정.

**장점**: 계정 없이 즉석 HTTPS 공개 URL, 방화벽 손 안 댐.
**단점**: **링크를 아는 누구나 접근**. 무인증 서버를 그대로 공개하면 안 됨 → 아래 보안 필수.

---

## 보안 (중요)

현재 서버는 인증이 없다. 방식별 위험도가 다르다.

| 방식 | 노출 범위 | 무인증 서버 안전성 | 필요 조치 |
|------|-----------|---------------------|-----------|
| A. Tailscale | 내 tailnet 기기만 | 상대적으로 안전(네트워크 경계로 격리) | 계정/기기 관리, tailnet ACL |
| B. Cloudflare quick tunnel | 링크 아는 전원(공개 인터넷) | **위험** | 아래 접근제한 필수 |

**Cloudflare 공개 URL 을 쓸 때 최소 하나는 반드시:**
1. **Cloudflare Access** 로 이메일/SSO 인증 게이트를 앞단에 건다(named tunnel + Access 정책).
2. 앱단에 기본인증/토큰을 추가한다(예: 리버스 프록시 basic-auth, 또는 서버에 토큰 검사).
3. 최소한 데모 시간에만 잠깐 띄우고 즉시 종료한다(장시간 방치 금지).

공통 주의:
- `/api/listings` 등 데이터 엔드포인트도 함께 공개된다. 민감 데이터가 아니어도 링크 공유 범위 관리.
- 방식 A 에서 `-BindAll` 로 0.0.0.0 바인드 시, 같은 로컬 LAN(공유기) 안의 다른 기기에서도 보인다.
  카페/공용 와이파이에서는 `-BindAll` 대신 Cloudflare 로컬 프록시(127.0.0.1) 방식을 고려.

---

## 방화벽 (방식 A -BindAll 인 경우만)

Tailnet 다른 기기가 PC IP로 직접 접속하는데 막힌다면, 인바운드 규칙을 추가:

```powershell
New-NetFirewallRule -DisplayName "auction-arbitrage 8000" `
  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

제거:

```powershell
Remove-NetFirewallRule -DisplayName "auction-arbitrage 8000"
```

> Cloudflare Tunnel, 또는 tailscale serve/localhost 프록시 방식은 **아웃바운드**로 동작하므로
> 인바운드 개방이 필요 없다. Tailscale 자체 트래픽은 Tailscale 설치 시 규칙이 처리한다.

---

## 트러블슈팅

| 증상 | 확인 |
|------|------|
| 폰에서 접속 안 됨 | `check-tunnel.ps1` [3] 에서 포트 LISTEN 확인. 미기동이면 `start.ps1` 먼저. |
| Tailnet IP 로 접속 불가 | 서버가 `127.0.0.1` 만 바인드했을 수 있음 → `start.ps1 -BindAll`. |
| tailnet IP 가 안 뜸 | `tailscale up` 재실행, 폰·PC 같은 계정인지 확인. |
| Cloudflare URL 이 502 | 서버가 실제로 지정 포트에 떠 있는지, URL 의 포트와 일치하는지 확인. |
| 공개 URL 을 껐다 켜니 바뀜 | quick tunnel 은 임시. 고정 URL 은 named tunnel + Cloudflare 계정 필요. |

---

## 요약 체크리스트

- [ ] `.\scripts\check-tunnel.ps1` 로 설치·포트 상태 확인
- [ ] `.\scripts\start.ps1`(필요 시 `-BindAll`) 로 서버 기동
- [ ] 비공개면 방식 A(Tailscale), 공개 데모면 방식 B(Cloudflare)
- [ ] **공개(B) 전에는 반드시 접근제한** 또는 짧은 데모 후 종료
- [ ] 다 쓰면 터널 프로세스와 서버 종료
