"""오늘의 추천 물건 푸시 — ntfy 로 폰에 "열어보고 싶어지는" 알림을 보낸다.

사용자 요구(2026-08-25): 홈 화면 앱은 깔아뒀는데 들어갈 계기가 없다 — "어느 지역
아파트가 감정가 얼마인데 최저 얼마로 나왔다" 같은 알림이 오면 열어보게 된다.

선별은 **홈 추천과 동일 기준**(digest.top_listings — 별도 로직 금지):
보수차익 > 0 · 같은 단지·같은 평형 표본 ≥ 5 · '위험' 제외 · 명세서 인수부담 제외.
여기에 ①당일 입찰 마감 제외 ②이미 보낸 물건 제외(data/notified_picks.json, 30일
유지 — 매일 같은 1등만 반복 수신하면 알림을 끄게 된다)를 더한다.

푸시 1건에 상위 N(기본 3)건 요약 + 클릭 시 1위 물건의 프로덕션 상세로 직행.
채널은 운영 알림과 같은 ntfy 토픽(harness/notify.json) — 실패해도 exit 0
(알림 실패가 파이프라인을 막으면 안 된다), 보낸 내용은 ALERTS.log 에도 남긴다.

사용:  python -m deploy.notify_picks --db auction.db [--n 3] [--dry-run] [--force]
배선:  refresh-daily.ps1 [5/5] 재채점 직후 (그날 최종 등급 기준으로 발송)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
NOTIFY_CFG = ROOT / "harness" / "notify.json"
ALERTS_LOG = ROOT / "harness" / "ALERTS.log"
SENT_PATH = ROOT / "data" / "notified_picks.json"
SENT_KEEP_DAYS = 30

PROD_URL = "https://auction-arbitrage-hyunwoo-jang-s-projects.vercel.app"


def _eok(won: int | None) -> str:
    """원 → '5.2억' (1억 미만은 '8,500만')."""
    if not won:
        return "?"
    if won >= 100_000_000:
        return f"{won / 1e8:.1f}억"
    return f"{won // 10_000:,}만"


def _region(address: str) -> str:
    """주소 → 짧은 지역 라벨('서울 노원구'). 두 토큰이면 충분 — 알림은 한 줄 싸움이다."""
    parts = (address or "").split()
    return " ".join(parts[:2]) if parts else "지역 미상"


def item_line(s, today: dt.date) -> str:
    """물건 1건 → 알림 한 줄. 사용자가 요구한 문장형: 지역·감정가·최저가(유찰)·차익·기일."""
    from src import query, report  # noqa: PLC0415
    pyeong = report.pyeong(s.area_m2)   # '25.7평' — 단위 포함 반환
    fail = f"(유찰 {s.fail_count}회)" if s.fail_count else "(신건)"
    profit = query.decision_profit(s)
    d = query.days_until(s.sale_date, today)
    dday = f" · 기일 {s.sale_date[5:] if len(s.sale_date) >= 10 else s.sale_date}" + (
        f"(D-{d})" if d is not None and d >= 0 else "")
    return (f"{_region(s.address)} {s.apt_name} {pyeong} — "
            f"감정 {_eok(s.appraisal_price)} → 최저 {_eok(s.min_bid_price)}{fail}"
            f" · 보수차익 +{_eok(profit)}{dday}")


def _load_sent() -> dict[str, str]:
    try:
        d = json.loads(SENT_PATH.read_text(encoding="utf-8"))
        cutoff = (dt.date.today() - dt.timedelta(days=SENT_KEEP_DAYS)).isoformat()
        return {k: v for k, v in d.items() if v >= cutoff}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_sent(sent: dict[str, str]) -> None:
    SENT_PATH.parent.mkdir(exist_ok=True)
    SENT_PATH.write_text(json.dumps(sent, ensure_ascii=False, indent=1), encoding="utf-8")


def pick(scored: list, badges: dict, sent: dict[str, str], n: int,
         now: dt.datetime) -> list:
    """홈 추천 게이트(top_listings) → 당일 마감 제외 → 기발송 제외 → 상위 n."""
    from src import digest, query  # noqa: PLC0415
    ranked = digest.top_listings(scored, n=50, badges=badges)
    out = []
    for s in ranked:
        if query.bidding_closed(s, now):
            continue
        if s.uid in sent:
            continue
        out.append(s)
        if len(out) >= n:
            break
    return out


def compose(picks: list, today: dt.date) -> tuple[str, str, str]:
    """→ (title, message, click_url). 클릭은 1위 물건 상세 직행."""
    title = f"오늘의 경매 추천 {len(picks)}건"
    message = "\n".join(f"{i}. {item_line(s, today)}" for i, s in enumerate(picks, 1))
    top = picks[0]
    click = f"{PROD_URL}/property/{quote(top.case_no)}"
    q = []
    if top.item_no:
        q.append(f"item={quote(top.item_no)}")
    if top.court:
        q.append(f"court={quote(top.court)}")
    if q:
        click += "?" + "&".join(q)
    return title, message, click


def send_webpush(title: str, message: str, click: str) -> int:
    """PWA Web Push(2026-08-25) — 홈 화면 앱 자체 알림. 반환 = 성공 발송 수.

    구독은 Supabase Storage(push_subs), 개인키는 harness/vapid.json(gitignore).
    410 Gone/404 = 만료 구독 → 저장소에서 삭제(다음 발송부터 제외). 실패 비차단.
    """
    try:
        vap = json.loads((ROOT / "harness" / "vapid.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0   # 키 미구성 = 웹푸시 미사용(ntfy 만)
    from pywebpush import WebPushException, webpush  # noqa: PLC0415

    from src import push_subs  # noqa: PLC0415
    subs = push_subs.list_subs()
    if not subs:
        return 0
    payload = json.dumps({"title": title, "body": message, "url": click}, ensure_ascii=False)
    sent = 0
    for sub in subs:
        try:
            webpush(subscription_info=sub, data=payload,
                    vapid_private_key=vap["private_pem"],
                    vapid_claims={"sub": vap.get("sub", "mailto:ops@example.com")})
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):   # 구독 만료 — 정리
                push_subs.delete_sub(sub.get("endpoint") or "")
                print(f"[notify_picks] 만료 구독 정리({code})")
            else:
                print(f"[notify_picks] webpush 실패(무시): {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 발송 실패가 파이프라인을 막으면 안 된다
            print(f"[notify_picks] webpush 오류(무시): {e}", file=sys.stderr)
    return sent


def send_ntfy(title: str, message: str, click: str) -> bool:
    """ntfy JSON publish(+클릭 URL). 미구성·실패는 False(로그만) — 파이프라인 비차단."""
    import requests  # noqa: PLC0415
    try:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with ALERTS_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] [default] [picks] {title} :: {message.replace(chr(10), ' / ')}\n")
    except OSError:
        pass
    try:
        cfg = json.loads(NOTIFY_CFG.read_text(encoding="utf-8"))
        if not cfg.get("enabled") or not cfg.get("ntfy_topic"):
            return False
        r = requests.post("https://ntfy.sh", timeout=10, json={
            "topic": cfg["ntfy_topic"], "title": title, "message": message,
            "click": click, "priority": 3, "tags": ["house", "moneybag"]})
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — 알림 실패가 크롤을 막으면 안 된다
        print(f"[notify_picks] push 실패(무시): {e}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="auction.db")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="발송·기록 없이 내용만 출력")
    ap.add_argument("--force", action="store_true", help="기발송 이력 무시(테스트용)")
    args = ap.parse_args()

    import os  # noqa: PLC0415

    from deploy.migrate_to_supabase import _load_env  # noqa: PLC0415 — 웹푸시(push_subs)가 SUPABASE 키 필요
    _load_env()
    os.environ["AUCTION_DB"] = args.db   # web 헬퍼(_scored·_rights_badges)가 이 DB를 읽게
    from src import query, web  # noqa: PLC0415
    scored = web._scored()
    badges = web._rights_badges()
    now = query.now_kst()
    sent = {} if args.force else _load_sent()
    picks = pick(scored, badges, sent, args.n, now)
    if not picks:
        print("보낼 신규 추천 없음(전부 기발송이거나 게이트 통과 0건) — 발송 생략")
        return 0
    title, message, click = compose(picks, now.date())
    print(title, "\n" + message, "\nclick:", click)
    if args.dry_run:
        return 0
    ok = send_ntfy(title, message, click)
    wp = send_webpush(title, message, click)
    print(f"발송 — ntfy: {'성공' if ok else '실패/미구성'} · 웹푸시(홈 화면 앱): {wp}대")
    if ok or wp:
        sent = _load_sent()   # force 모드여도 발송 기록은 남긴다(다음 정기 발송 중복 방지)
        today = dt.date.today().isoformat()
        for s in picks:
            sent[s.uid] = today
        _save_sent(sent)
        print(f"추천 {len(picks)}건 발송 이력 기록")
    return 0


if __name__ == "__main__":
    sys.exit(main())
