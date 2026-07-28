"""크롤 진행 상황 한 화면 — 막대그래프로.

새로고침(scripts/refresh-daily.ps1)이 도는 동안 "지금 어디쯤인가"를 사람이 5초 안에
읽을 수 있게 만든다. 5분 간격 감시 루프가 이걸 호출해 결과를 그대로 보고한다.

측정하는 것(전부 실제 상태 — 추정 아님):
  · 크롤 프로세스 생존·CPU 누적(멈췄는지 판별)
  · 경과 시간 / 단계(리스트 → 권리 → 현황조사서 → 재채점)
  · 전국 17개 시도 샤딩 진행
  · courtauction 일일 요청 예산 소모(안티밴 서킷 500)
  · DB 실제 변화(활성·낙찰 건수, 원본 최신 시각)

사용: PYTHONUTF8=1 .venv/Scripts/python.exe scripts/crawl_progress.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAR_W = 28

# 단계 → 로그에 찍히는 표식. 리스트 스윕은 전용 표식이 없어 '시작했고 [2/4] 전'으로 판정한다.
STEPS = [("① 법원 리스트·채점·낙찰diff", None),
         ("② 권리 크롤", "[2/4]"),
         ("③ 현황조사서 백필", "[3/4]"),
         ("④ 재채점", "[4/4]")]


def bar(done: float, total: float, width: int = BAR_W) -> str:
    """0~1 비율 막대. total 0 이면 미상(물음표)으로 — 0으로 나눠 100%인 척하지 않는다."""
    if not total:
        return "?" * width
    r = max(0.0, min(1.0, done / total))
    n = int(round(r * width))
    return "█" * n + "░" * (width - n)


def _read_log(path: Path) -> list[str]:
    """PowerShell Tee-Object 는 UTF-16 으로 쓴다. 실패해도 죽지 않고 빈 목록."""
    for enc in ("utf-16", "utf-8", "cp949"):
        try:
            t = path.read_text(encoding=enc, errors="replace")
            if "===" in t or "---" in t:
                return [ln.rstrip() for ln in t.splitlines() if ln.strip()]
        except (OSError, UnicodeError):
            continue
    return []


def molit_progress(today: str) -> dict:
    """국토부 실거래 수집 진행 — **분모가 있는 유일한 실시간 지표**.

    캐시 테이블 molit_trades 는 (kind, lawd_cd, ymd) 한 조합당 1행이다. 크롤은 지역마다
    3종(아파트·연립·오피스텔) × 조회창 개월수를 채우므로, "그 지역이 몇 행을 채웠는가"로
    지역별 완료를 판정할 수 있다. 전체 지역 수는 크롤이 수집한 물건에서 정해져 **미리 알 수
    없으므로**, 분모는 '지금까지 등장한 지역'으로 두고 그 사실을 라벨에 밝힌다(모르는 분모를
    아는 척하지 않는다 — 새 지역이 계속 나오면 분모도 함께 늘어난다).
    """
    out = {"rows": 0, "regions": 0, "done": 0, "months": 0, "kinds": 0}
    try:
        c = sqlite3.connect(f"file:{ROOT / 'data' / 'molit_trades.db'}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        q = c.execute
        out["rows"] = q("SELECT COUNT(*) FROM molit_trades WHERE fetched_at >= ?",
                        (today,)).fetchone()[0]
        if not out["rows"]:
            return out
        out["regions"] = q("SELECT COUNT(DISTINCT lawd_cd) FROM molit_trades "
                           "WHERE fetched_at >= ?", (today,)).fetchone()[0]
        out["months"] = q("SELECT COUNT(DISTINCT ymd) FROM molit_trades WHERE fetched_at >= ?",
                          (today,)).fetchone()[0]
        out["kinds"] = q("SELECT COUNT(DISTINCT kind) FROM molit_trades "
                         "WHERE fetched_at >= ?", (today,)).fetchone()[0]
        full = max(1, out["kinds"] * out["months"])
        out["done"] = q("SELECT COUNT(*) FROM (SELECT lawd_cd FROM molit_trades "
                        "WHERE fetched_at >= ? GROUP BY lawd_cd HAVING COUNT(*) >= ?)",
                        (today, full)).fetchone()[0]
    except sqlite3.Error:
        pass
    finally:
        c.close()
    return out


def main() -> int:
    now = datetime.now()
    print(f"■ 크롤 진행 — {now:%Y-%m-%d %H:%M:%S} KST")

    logs = sorted(glob.glob(str(ROOT / "evidence" / "refresh-*.log")))
    if not logs:
        print("  로그 없음 — 새로고침이 시작되지 않았습니다.")
        return 0
    log = Path(logs[-1])
    lines = _read_log(log)

    # 시작 시각 — 파일명 refresh-YYYYMMDD-HHMMSS.log
    m = re.search(r"refresh-(\d{8})-(\d{6})", log.name)
    started = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None
    elapsed = (now - started) if started else None

    # ── 생존·활동 판정 ─────────────────────────────────────────────────────
    # 2026-07-28 하루에 이 판정으로 **네 번** 틀렸다. 남은 교훈을 전부 여기에 박아 둔다.
    #
    #  실패1 "가장 최근 python"을 골랐다 → 방금 띄운 **이 스크립트 자신**을 쟀다(CPU 0 오경보).
    #  실패2 프로세스 하나만 골랐다 → run.py 는 래퍼+작업자 2개라 대기 중인 래퍼를 집었다.
    #  실패3 예산·로그 지표가 0인 걸 '진행 없음'으로 읽었다 → 애초에 그 단계에선 측정 불가였다.
    #  실패4 **3초 샘플**로 판정했다 → 이 크롤러는 안티밴으로 요청 간 3~8초를 쉰다. 그 대기를
    #        정지와 구분할 수 없다(실측: 3초 샘플 0.0초 → 15초 샘플 0.45초, 정상이었다).
    #
    # 그래서 지금은 **보고 사이 누적 CPU 증가**를 1순위 근거로 쓴다(5분 간격이면 표본이 충분).
    # 짧은 샘플은 보조일 뿐이고, 첫 실행이라 직전 값이 없을 때만 단독으로 쓴다.
    #  실패5 `run.py` 만 매칭했다 → ②단계는 crawl_rights 라 '프로세스 없음'으로 오판했다
    #        (예산 막대가 34/500 로 움직이는데 종료됐다고 보고할 뻔했다).
    # ⚠ 프로세스는 명령줄로 특정하고(파이프라인 3종 전부), 매칭되는 것 **전부를 합산**한다.
    ps = ("$me = " + str(os.getpid()) + "; "
          "$ids = @(Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
          "  Where-Object { $_.ProcessId -ne $me -and ("
          "    $_.CommandLine -like '*run.py*' -or "
          "    $_.CommandLine -like '*crawl_rights*' -or "
          "    $_.CommandLine -like '*crawl_naver*') } | "
          "  Select-Object -ExpandProperty ProcessId); "
          "if ($ids.Count -eq 0) { 'none'; exit }; "
          "$ps0 = @($ids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }); "
          "$a = ($ps0 | Measure-Object CPU -Sum).Sum; "
          "Start-Sleep -Seconds 3; "
          "$ps1 = @($ids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }); "
          "$b = ($ps1 | Measure-Object CPU -Sum).Sum; "
          "$c = 0; foreach ($i in $ids) { "
          "  $c += (Get-NetTCPConnection -OwningProcess $i -State Established "
          "         -ErrorAction SilentlyContinue | Measure-Object).Count }; "
          "'{0} {1} {2} {3}' -f $ids.Count, [math]::Round($b - $a, 2), [math]::Round($b, 1), $c")
    nproc, delta, cpu, conns = None, None, 0.0, None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        parts = (out.stdout or "").split()
        if len(parts) >= 4:
            nproc = int(parts[0])
            delta, cpu, conns = float(parts[1]), float(parts[2]), int(parts[3])
    except Exception:  # noqa: BLE001 — 관측 실패는 관측 실패라고 표시(추정 금지)
        pass

    # 직전 보고의 누적 CPU — 이게 1순위 근거다(5분 간격이면 대기 구간에 속지 않는다).
    state_f = ROOT / "evidence" / ".crawl_progress_state.json"
    prev = {}
    try:
        prev = json.loads(state_f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    same_run = prev.get("log") == log.name
    since = round(cpu - float(prev.get("cpu", 0)), 1) if same_run else None
    gap_min = round((now.timestamp() - float(prev.get("ts", now.timestamp()))) / 60, 1) \
        if same_run else None

    if not nproc:
        stat = "종료됨(또는 관측 실패) — run.py 프로세스 없음"
    elif since is not None:
        # 1순위: 직전 보고 이후 실제로 CPU 를 썼는가
        if since > 0.5:
            stat = f"작업 중 — 직전 보고 이후 {gap_min}분간 CPU +{since}초"
        elif (delta or 0) > 0.05 or (conns or 0) > 0:
            stat = (f"작업 중(느림) — {gap_min}분간 CPU +{since}초 · "
                    f"짧은샘플 +{delta}초 · 연결 {conns}개")
        else:
            stat = (f"⚠ 정지 의심 — {gap_min}분간 CPU +{since}초 · "
                    f"연결 {conns}개 (두 지표 모두 정지)")
    # 첫 실행 — 비교할 직전 값이 없다. 짧은 샘플만으로는 대기와 정지를 못 가르므로 그렇게 쓴다.
    elif (delta or 0) > 0.05 or (conns or 0) > 0:
        stat = f"작업 중 — 짧은샘플 CPU +{delta}초 · 연결 {conns}개 · 프로세스 {nproc}개"
    else:
        stat = "판정 보류(첫 보고) — 짧은샘플만으론 대기/정지 구분 불가. 다음 보고에서 확정"

    el = f"{int(elapsed.total_seconds() // 60)}분" if elapsed else "?"
    print(f"  {stat}")
    print(f"  경과 {el} · CPU 누적 {cpu:.0f}초 · 로그 {len(lines)}줄")
    try:
        _mp = molit_progress(now.strftime("%Y-%m-%d"))
        state_f.write_text(json.dumps({
            "log": log.name, "cpu": cpu, "ts": now.timestamp(),
            "molit_rows": _mp["rows"], "molit_regions": _mp["regions"]}), encoding="utf-8")
    except OSError:
        pass

    # 단계
    text = "\n".join(lines)
    cur = 0
    for i, (_, marker) in enumerate(STEPS):
        if marker and marker in text:
            cur = i
    print()
    print("  [단계]")
    for i, (name, _) in enumerate(STEPS):
        mark = "▶" if i == cur else ("✓" if i < cur else "·")
        print(f"    {mark} {name}")

    print()
    # 국토부 실거래 수집 — ①단계에서 실시간으로 움직이는 **유일한** 진행 지표.
    mp = molit_progress(now.strftime("%Y-%m-%d"))
    if mp["rows"]:
        d_rows = mp["rows"] - int(prev.get("molit_rows", 0)) if same_run else None
        d_reg = mp["regions"] - int(prev.get("molit_regions", 0)) if same_run else None
        inc = f"  (+{d_rows:,}행 · 지역 +{d_reg})" if d_rows is not None else ""
        print(f"  [국토부 수집] {bar(mp['done'], mp['regions'])} "
              f"{mp['done']}/{mp['regions']}곳 완료{inc}")
        print(f"               조합 {mp['rows']:,}개 · {mp['kinds']}종 × {mp['months']}개월"
              f"  ※ 분모는 '지금까지 등장한 지역' — 새 지역이 나오면 함께 늘어남")
        # 지금 어느 지역·어느 달을 받고 있는지(로그 마지막 URL)
        cu = re.findall(r"LAWD_CD=(\d+)&DEAL_YMD=(\d+)", text)
        if cu:
            print(f"               현재 조회: 지역 {cu[-1][0]} · {cu[-1][1][:4]}년 {cu[-1][1][4:]}월")

    # 전국 시도 샤딩 — stdout 이 파이프로 블록 버퍼링돼 실시간 반영이 안 된다. 0/17 은
    # "아무것도 안 했다"가 아니라 "아직 로그가 안 나왔다"이므로, 값이 있을 때만 보여준다.
    sido = len(set(re.findall(r"시도\s+(\d{2})", text)))
    if sido:
        print(f"  [시도 샤딩] {bar(sido, 17)} {sido}/17")

    # 요청 예산 — 상한은 **실행 중인 프로세스의 `--cap`** 이 진짜다.
    # (2026-07-28 실사고) 클라이언트 기본값 500 을 분모로 박아 뒀더니 519/500 처럼 100% 를
    # 넘겨 표시됐다. refresh-daily.ps1 은 --cap 800 을 넘긴다. 분모를 상수로 두면 이렇게
    # 조용히 틀리므로, 실행 중 명령줄에서 읽고 못 읽을 때만 기본값으로 떨어진다.
    used, cap = 0, 500
    try:
        capout = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             r"  Where-Object { $_.CommandLine -match '--cap\s+\d+' } | "
             r"  ForEach-Object { if ($_.CommandLine -match '--cap\s+(\d+)') { $Matches[1] } } | "
             "  Select-Object -First 1"],
            capture_output=True, text=True, timeout=30)
        if capout.stdout.strip().isdigit():
            cap = int(capout.stdout.strip())
    except Exception:  # noqa: BLE001 — 못 읽으면 기본값(그 사실은 라벨에 안 숨긴다)
        pass
    bf = ROOT / ".courtauction_budget.json"
    if bf.exists():
        try:
            d = json.loads(bf.read_text(encoding="utf-8"))
            if d.get("date") == now.strftime("%Y-%m-%d"):
                used = int(d.get("count") or 0)
        except (json.JSONDecodeError, ValueError):
            pass
    # ⚠ 리스트 크롤(run.py)은 budget_file 을 배선하지 않는다 — 이 파일은 권리/현황조사서
    # 크롤(--cap)만 갱신한다. 그래서 ①단계에서는 항상 0이며 "진행 없음"의 근거가 못 된다.
    if cur == 0:
        print("  [요청 예산] (①단계는 미배선 — 측정 불가. ②③단계부터 유효)")
    else:
        over = "  ⚠ 상한 초과 — 곧 중단" if used > cap else ""
        print(f"  [요청 예산] {bar(used, cap)} {used}/{cap}  (안티밴 서킷){over}")

    # DB 실제 변화
    try:
        c = sqlite3.connect(str(ROOT / "auction.db"))
        act = c.execute("SELECT COUNT(*) FROM scored_listings").fetchone()[0]
        sold = c.execute("SELECT COUNT(*) FROM sold_listings").fetchone()[0]
        raw = c.execute("SELECT MAX(fetched_at) FROM raw_listings").fetchone()[0]
        c.close()
        fresh = "오늘" if (raw or "").startswith(now.strftime("%Y-%m-%d")) else "이전"
        print()
        print(f"  [DB] 활성 {act:,}건 · 낙찰 {sold:,}건 · 원본 최신 {raw} ({fresh})")
    except sqlite3.Error as e:
        print(f"\n  [DB] 조회 실패: {e}")

    # 경고 신호만 추려서
    warn = [ln for ln in lines[-40:]
            if any(k in ln for k in ("오류", "실패", "차단", "Traceback", "중단"))]
    if warn:
        print()
        print("  [경고 신호]")
        for w in warn[-3:]:
            print(f"    {w[:96]}")

    if lines:
        print()
        print(f"  최근: {lines[-1][:96]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
