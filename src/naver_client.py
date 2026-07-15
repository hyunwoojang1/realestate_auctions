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
            res = self._page.evaluate(_JS_FETCH, {"url": path, "tok": self.token})
            self.calls += 1
            self.n += 1
            if res["status"] == 200:
                try:
                    return json.loads(res["body"])
                except (json.JSONDecodeError, TypeError):
                    # 200인데 JSON이 아님 = 안티봇 챌린지 HTML. '데이터 없음'이 아니라 차단이므로
                    # 조용히 None으로 삼키지 않고 즉시 중단(차단 감지 안전장치 우회 방지).
                    raise NaverBlocked(f"200 non-JSON 응답(안티봇 챌린지 의심): {path}")
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
        raise NaverBlocked("429 연속 3회 — 차단 확실, 중단(장기 대기 필요)")

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

    def articles(self, complex_no, trade="A1", kind="APT"):
        j = self.fetch(f"/api/articles/complex/{complex_no}?realEstateType={kind}&tradeType={trade}"
                       f"&priceMin=0&priceMax=900000000&areaMin=0&areaMax=900000000"
                       f"&page=1&complexNo={complex_no}&order=prc")
        return (j or {}).get("articleList") or []

    def close(self):
        for fn in (getattr(self, "_browser", None), getattr(self, "_pw", None)):
            try:
                (fn.close if hasattr(fn, "close") else fn.stop)()
            except Exception:  # noqa: BLE001
                pass
