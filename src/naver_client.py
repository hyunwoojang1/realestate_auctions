"""네이버 부동산 KB시세·호가 수집 클라이언트 (헤드리스 브라우저 + in-page fetch).

네이버는 토큰/쿠키 없는 순수 요청을 429로 거부하므로, Playwright로 실제 페이지를 띄워
authorization 토큰과 세션 쿠키를 확보한 뒤 페이지 컨텍스트 안에서 fetch 한다(쿠키·토큰 자동).

Safety(안티봇 회피): 요청 간 랜덤 지터 지연 · N건마다 세션 갱신 · 429 지수 백오프 후 장기대기 ·
연속 429=중단 · kill-switch(NAVER_STOP) · 순차 전용(병렬 절대 금지, courtauction과 동일 정책).
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
STOP_FILE = "NAVER_STOP"
_MAP_URL = "https://new.land.naver.com/complexes?ms=37.4993,127.0625,16&a=APT"
_JS_FETCH = """async ({url, tok}) => {
    const r = await fetch(url, {headers: {'authorization': tok, 'accept':'application/json'}});
    return {status: r.status, body: await r.text()};
}"""


class NaverBlocked(Exception):
    """차단/kill-switch — 즉시 중단(우회 금지)."""


# 드라이버/브라우저가 죽었다는 신호. 이때는 브라우저만 다시 띄우는 _refresh 로는 못 살아나고
# playwright 인스턴스를 통째로 교체하는 _hard_restart 가 필요하다 — 못 잡으면 _launch 가
# 죽은 드라이버를 붙잡고 **무한 대기**한다(실측 2026-07-20 절전).
# (2026-08-07) 'Page.evaluate: Target crashed' 가 이 목록에 없어(= 'target closed' 만 있었다)
# 8/4~8/6 사흘 연속 같은 무한 대기 → 작업 스케줄러 5시간 제한 강제종료를 냈다. 로그가 매번
# "세션 갱신"에서 끊기고 "세션 확보"가 없었던 게 그 증거다. 'closed' 와 'crashed' 를 모두 잡는다.
_DRIVER_DEAD_TOKENS = (
    "driver", "connection closed", "target closed", "browser has been closed",
    "crashed",          # target crashed · page crashed · renderer crashed
)


def is_driver_dead(msg: object) -> bool:
    """예외 메시지가 '드라이버/브라우저 사망' 신호인가 — _hard_restart 발동 판정(순수 함수)."""
    m = str(msg or "").lower()
    return any(tok in m for tok in _DRIVER_DEAD_TOKENS)


def _row_ymd(row: dict) -> str:
    """실거래 행 → YYYYMMDD. naver_store 와 **같은 추출 규칙**을 쓴다(판정 갈림 방지)."""
    from .naver_store import _trade_ymd  # noqa: PLC0415 — 순환 없음(naver_store는 stdlib만 import)
    return _trade_ymd(row)


def batch_has_new(batch: list, stop_before_ymd: str | None) -> bool:
    """이 페이지에 stop_before_ymd 보다 새로운 거래가 하나라도 있는가(조기 종료 판정).

    - stop_before_ymd=None(=처음 보는 쌍) → 항상 True(전량 수집).
    - 날짜를 못 읽은 행은 '새 거래일 수 있음'으로 보고 True — 모름을 없음으로 바꾸지 않는다.
    """
    if not stop_before_ymd:
        return True
    for r in batch:
        ymd = _row_ymd(r)
        if not ymd or ymd > stop_before_ymd:
            return True
    return False


class NaverClient:
    def __init__(self, min_delay: float = 2.0, max_delay: float = 4.0,
                 refresh_every: int = 80, verbose: bool = True):
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 — 크롤 시에만 필요
        self._sync_playwright = sync_playwright
        self.min_delay, self.max_delay = min_delay, max_delay
        self.refresh_every = refresh_every
        self.verbose = verbose
        self.n = self.calls = 0
        self._pw = sync_playwright().start()
        self._launch()

    def _hard_restart(self):
        """드라이버(node) 연결이 죽으면(절전·순단) 브라우저만 재생성해선 못 살아난다 —
        (실측 2026-07-20: 절전으로 'Connection closed while reading from the driver' 후
        _launch가 죽은 드라이버를 붙잡고 무한 대기·크롤 정지). playwright 인스턴스를 통째로
        정지·재시작해 드라이버부터 새로 띄운다. 각 정리 단계는 죽은 핸들에서 멈추지 않게 무시."""
        self._log("  [naver] ⚠ 드라이버 재시작(playwright 인스턴스 교체)")
        for closer in (lambda: self._browser.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:  # noqa: BLE001 — 죽은 핸들 정리 실패는 무시
                pass
        self._pw = self._sync_playwright().start()
        self._launch()
        self.n = 0

    def _log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def _launch(self):
        # timeout 명시(2026-08-07): 기본값에 의존하면 드라이버가 반쯤 죽은 상태에서 launch 가
        # 응답을 영원히 기다린다. 명시하면 TimeoutError 로 떨어져 호출부의 _hard_restart 폴백이 돈다.
        self._browser = self._pw.chromium.launch(headless=True, timeout=60_000)
        self._ctx = self._browser.new_context(user_agent=UA, locale="ko-KR")
        self._page = self._ctx.new_page()
        self.token = None
        self._page.on("request", self._capture)
        self._page.goto(_MAP_URL, wait_until="domcontentloaded", timeout=30000)
        for _ in range(20):
            if self.token:
                break
            self._page.wait_for_timeout(500)
        if not self.token:
            raise NaverBlocked("토큰 확보 실패(페이지 차단/변경 의심)")
        self._log(f"  [naver] 세션 확보(쿠키 {len(self._ctx.cookies())}개)")

    def _capture(self, req):
        if "/api/" in req.url and not self.token:
            a = req.headers.get("authorization")
            if a:
                self.token = a

    def _refresh(self):
        self._log("  [naver] 세션 갱신")
        try:
            self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(random.uniform(3, 6))
        self._launch()
        self.n = 0

    def fetch(self, path: str):
        if Path(STOP_FILE).exists():
            raise NaverBlocked(f"kill-switch '{STOP_FILE}' 감지")
        if self.n >= self.refresh_every:
            self._refresh()
        time.sleep(random.uniform(self.min_delay, self.max_delay))
        for attempt in range(3):
            try:
                res = self._page.evaluate(_JS_FETCH, {"url": path, "tok": self.token})
            except Exception as e:  # noqa: BLE001 — Playwright 일시 오류(Failed to fetch·페이지 크래시)
                # (실측 2026-07-19) 장시간 크롤 중 브라우저 컨텍스트의 fetch가 네트워크 순단으로 죽으면
                # playwright Error가 그대로 전파돼 크롤 전체가 사망(246쌍에서 크래시). 일시 오류로 보고
                # 세션 재생성 후 재시도 — 3회 모두 실패면 NaverBlocked로 정상 중단(부분 저장 유지).
                self._log(f"  [naver] evaluate 오류({type(e).__name__}: {str(e)[:60]}) — "
                          f"세션 재생성 후 재시도({attempt + 1}/3)")
                time.sleep(random.uniform(5, 12))
                # 드라이버 연결이 끊긴 경우(절전·순단)엔 _refresh(브라우저만 재생성)로는 못 살아나
                # 무한 대기하므로, 드라이버 죽음 신호면 playwright 인스턴스를 통째로 교체한다.
                driver_dead = is_driver_dead(e)
                try:
                    if driver_dead:
                        self._hard_restart()
                    else:
                        self._refresh()
                except Exception as e2:  # noqa: BLE001 — 재생성조차 실패 = 네트워크 다운 의심
                    self._log(f"  [naver] 세션 재생성 실패({type(e2).__name__}) — 30s 대기 후 하드 재시작")
                    time.sleep(30)
                    try:
                        self._hard_restart()
                    except Exception as e3:  # noqa: BLE001 — 그래도 실패면 다음 attempt로
                        self._log(f"  [naver] 하드 재시작 실패({type(e3).__name__})")
                continue
            self.calls += 1
            self.n += 1
            if res["status"] == 200:
                try:
                    return json.loads(res["body"])
                except (json.JSONDecodeError, TypeError):
                    # 200인데 JSON이 아님 = 안티봇 챌린지 HTML. '데이터 없음'이 아니라 차단이므로
                    # 조용히 None으로 삼키지 않고 즉시 중단(차단 감지 안전장치 우회 방지).
                    raise NaverBlocked(f"200 non-JSON 응답(안티봇 챌린지 의심): {path}") from None
            if res["status"] == 429:
                wait = 30 * (attempt + 1) + random.uniform(0, 15)
                self._log(f"  [naver 429] {wait:.0f}s 대기({attempt+1}/3)")
                time.sleep(wait)
                if attempt == 1:
                    self._refresh()
                continue
            if res["status"] == 404:
                return None   # 리소스 미존재(단지 없음 등) — 정상적 '데이터 없음'
            # 403 등 그 외 상태 = 차단 신호. 조용히 None으로 삼키면 '데이터 없음'과 구분 불가.
            raise NaverBlocked(f"HTTP {res['status']} 응답 — 차단 의심: {path}")
        raise NaverBlocked("재시도 3회 소진(429 연속 또는 브라우저 fetch 실패) — 중단(부분 저장 유지)")

    # --- 도메인 메서드 ---
    def cortar_for(self, lat, lng, zoom=16):
        j = self.fetch(f"/api/cortars?zoom={zoom}&centerLat={lat}&centerLon={lng}")
        return (j or {}).get("cortarNo") if j else None

    def complexes_in(self, cortar_no, kind="APT"):
        j = self.fetch(f"/api/regions/complexes?cortarNo={cortar_no}&realEstateType={kind}&order=")
        return (j or {}).get("complexList") or []

    def complex_detail(self, complex_no):
        return self.fetch(f"/api/complexes/{complex_no}?sameAddressGroup=false")

    def kb_price(self, complex_no, area_no):
        j = self.fetch(f"/api/complexes/{complex_no}/prices?complexNo={complex_no}&tradeType=A1"
                       f"&year=5&priceChartChange=false&type=table&areaNo={area_no}&provider=kbstar")
        return (j or {}).get("marketPrices") or []

    def articles(self, complex_no, trade="A1", kind="APT", page=1):
        """호가 매물 1페이지(20건, 가격순). 반환 (articleList, isMoreData).

        ⚠ (실측 2026-07-19) priceMax=99900000000(999억, 감사 7/15 픽스값)은 서버 검증
        "유효하지 않은 priceMax"로 **200 에러바디**를 돌려줘 호가 수집이 통째로 죽어 있었다.
        서버 단위는 만원 — priceMax=999999(99.9억)·areaMax=999999가 검증을 통과한다(실측).
        """
        j = self.fetch(f"/api/articles/complex/{complex_no}?realEstateType={kind}&tradeType={trade}"
                       f"&tag=%3A%3A%3A%3A%3A%3A%3A%3A&rentPriceMin=0&rentPriceMax=999999"
                       f"&priceMin=0&priceMax=999999&areaMin=0&areaMax=999999"
                       f"&showArticle=false&sameAddressGroup=false&priceType=RETAIL&directions="
                       f"&page={page}&complexNo={complex_no}&buildingNos=&areaNos="
                       f"&type=list&order=prc")
        if isinstance(j, dict) and j.get("error"):
            # 200 + error 바디 = 파라미터 거부 — 조용히 0건으로 삼키면 '매물 없음'과 구분 불가.
            self._log(f"  [naver] ⚠ 호가 API 오류(complex {complex_no}): "
                      f"{(j.get('error') or {}).get('message', '?')}")
            return [], False
        return ((j or {}).get("articleList") or []), bool((j or {}).get("isMoreData"))

    def articles_all(self, complex_no, trade="A1", kind="APT", max_pages=5):
        """호가 전 페이지 수집(C2-b 수정 — 종전 page=1·가격순 20건만은 ask_max가
        '20번째로 싼 매물'로 붕괴). 종료 = isMoreData=False(실측 신호).
        max_pages 도달 시(=100건 초과 단지) 잘림 경고 로그(침묵 잘림 금지)."""
        out, page = [], 1
        while page <= max_pages:
            batch, more = self.articles(complex_no, trade=trade, kind=kind, page=page)
            out.extend(batch)
            if not more:
                return out, True
            page += 1
        self._log(f"  [naver] ⚠ 호가 {max_pages}p({len(out)}건) 상한 잘림 — complex {complex_no} "
                  f"ask_max 과소 가능")
        return out, False

    def overview(self, complex_no):
        """단지 요약 — 평형목록(pyeongs)·매물수·전세가율(leasePerDealRate)·세대수·사용승인일."""
        return self.fetch(f"/api/complexes/overview/{complex_no}?complexNo={complex_no}")

    def real_prices(self, complex_no, area_no, trade="A1", max_pages=80,
                    stop_before_ymd=None, safety_pages=1):
        """단지·평형 국토부 실거래 이력 — addedRowCount 커서 루프(P0 실측 2026-07-19).

        실측: 1p는 priceChartChange=true·커서 없음(≈6행), 2p부터 false+누적 addedRowCount.
        종료 = 빈 페이지(totalRowCount는 의미 불명이라 종료판정에 안 씀).
        반환 (rows, meta) — rows=평탄화 실거래 행,
        meta={pages, total_row_count, exhausted, early_stop}.
        exhausted=False(=max_pages 캡 도달)면 잘림 — 호출부가 로그로 드러내야 한다.

        stop_before_ymd(2026-08-07 조기 종료): 이미 보유한 **최신 거래일**(YYYYMMDD).
        그보다 새로운 거래가 없는 페이지가 safety_pages+1 연속 나오면 아래는 전부 보유분이므로
        멈춘다. None(=처음 보는 쌍)이면 종전대로 전량 수집.

        근거: 이 응답은 최신→과거 순이다(캐시 2,786쌍 전수 검증, 순서 역전 0쌍).
        그래도 정렬 가정에만 기대지 않는다 — safety_pages 만큼 더 보고 멈춰서 한 페이지 분의
        정렬 흔들림을 흡수한다. 날짜를 못 읽은 행은 '새 거래일 수 있음'으로 본다(batch_has_new).

        왜 필요했나(실측 2026-08-07): 증분 갱신인데 매번 20년치를 처음부터 다시 받고 있었다 —
        325쌍에서 34,614행을 다시 써넣었고 그중 최근 14일 새 거래는 53행(0.15%)이었다.
        쌍당 32.7초의 거의 전부가 이미 가진 데이터를 다시 받는 대기시간이었다.
        """
        rows: list[dict] = []
        added = 0
        total = None
        pages = 0
        stale_pages = 0        # 새 거래가 없는 페이지 연속 횟수
        while pages < max_pages:
            first = "true" if pages == 0 else "false"
            cursor = "" if pages == 0 else f"&addedRowCount={added}"
            j = self.fetch(f"/api/complexes/{complex_no}/prices/real?complexNo={complex_no}"
                           f"&tradeType={trade}&year=5&priceChartChange={first}"
                           f"&areaNo={area_no}&type=table{cursor}")
            pages += 1
            if j is None:      # 404 = 실거래 리소스 없음(정상적 '데이터 없음')
                return rows, {"pages": pages, "total_row_count": total,
                              "exhausted": True, "early_stop": False}
            if total is None:
                total = j.get("totalRowCount")
            batch = [r for m in (j.get("realPriceOnMonthList") or [])
                     for r in (m.get("realPriceList") or [])]
            if not batch:
                return rows, {"pages": pages, "total_row_count": total,
                              "exhausted": True, "early_stop": False}
            rows.extend(batch)
            added = j.get("addedRowCount") or (added + len(batch))
            # 조기 종료 — 보유 최신 거래일보다 새로운 게 없는 페이지가 안전마진만큼 반복되면 중단.
            if stop_before_ymd:
                if batch_has_new(batch, stop_before_ymd):
                    stale_pages = 0
                else:
                    stale_pages += 1
                    if stale_pages > safety_pages:
                        return rows, {"pages": pages, "total_row_count": total,
                                      "exhausted": True, "early_stop": True}
        self._log(f"  [naver] ⚠ 실거래 {max_pages}p({len(rows)}행) 상한 잘림 — complex {complex_no} "
                  f"area {area_no} (totalRowCount={total})")
        return rows, {"pages": pages, "total_row_count": total,
                      "exhausted": False, "early_stop": False}

    def close(self):
        for fn in (getattr(self, "_browser", None), getattr(self, "_pw", None)):
            try:
                (fn.close if hasattr(fn, "close") else fn.stop)()
            except Exception:  # noqa: BLE001
                pass
