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


class NaverClient:
    def __init__(self, min_delay: float = 2.0, max_delay: float = 4.0,
                 refresh_every: int = 80, verbose: bool = True):
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 — 크롤 시에만 필요
        self.min_delay, self.max_delay = min_delay, max_delay
        self.refresh_every = refresh_every
        self.verbose = verbose
        self.n = self.calls = 0
        self._pw = sync_playwright().start()
        self._launch()

    def _log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def _launch(self):
        self._browser = self._pw.chromium.launch(headless=True)
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
                try:
                    self._refresh()
                except Exception as e2:  # noqa: BLE001 — 재생성조차 실패 = 네트워크 다운 의심
                    self._log(f"  [naver] 세션 재생성 실패({type(e2).__name__}) — 30s 대기 후 재시도")
                    time.sleep(30)
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

    def real_prices(self, complex_no, area_no, trade="A1", max_pages=80):
        """단지·평형 국토부 실거래 이력 — addedRowCount 커서 루프(P0 실측 2026-07-19).

        실측: 1p는 priceChartChange=true·커서 없음(≈6행), 2p부터 false+누적 addedRowCount.
        종료 = 빈 페이지(totalRowCount는 의미 불명이라 종료판정에 안 씀).
        반환 (rows, meta) — rows=평탄화 실거래 행, meta={pages, total_row_count, exhausted}.
        exhausted=False(=max_pages 캡 도달)면 잘림 — 호출부가 로그로 드러내야 한다.
        """
        rows: list[dict] = []
        added = 0
        total = None
        pages = 0
        while pages < max_pages:
            first = "true" if pages == 0 else "false"
            cursor = "" if pages == 0 else f"&addedRowCount={added}"
            j = self.fetch(f"/api/complexes/{complex_no}/prices/real?complexNo={complex_no}"
                           f"&tradeType={trade}&year=5&priceChartChange={first}"
                           f"&areaNo={area_no}&type=table{cursor}")
            pages += 1
            if j is None:      # 404 = 실거래 리소스 없음(정상적 '데이터 없음')
                return rows, {"pages": pages, "total_row_count": total, "exhausted": True}
            if total is None:
                total = j.get("totalRowCount")
            batch = [r for m in (j.get("realPriceOnMonthList") or [])
                     for r in (m.get("realPriceList") or [])]
            if not batch:
                return rows, {"pages": pages, "total_row_count": total, "exhausted": True}
            rows.extend(batch)
            added = j.get("addedRowCount") or (added + len(batch))
        self._log(f"  [naver] ⚠ 실거래 {max_pages}p({len(rows)}행) 상한 잘림 — complex {complex_no} "
                  f"area {area_no} (totalRowCount={total})")
        return rows, {"pages": pages, "total_row_count": total, "exhausted": False}

    def close(self):
        for fn in (getattr(self, "_browser", None), getattr(self, "_pw", None)):
            try:
                (fn.close if hasattr(fn, "close") else fn.stop)()
            except Exception:  # noqa: BLE001
                pass
