"""단일 요소/전체 페이지 스크린샷 헬퍼 — 비포/애프터 캡처용.

사용: python scripts/shot_pane.py <URL> <CSS_SELECTOR|full> <OUT_PNG> [<CLICK_SELECTOR>] [<WxH>]
- CLICK_SELECTOR 를 주면 먼저 클릭(탭 전환 등) 후 대기. 클릭 생략은 "" 전달.
- SELECTOR 에 'full' 을 주면 전체 페이지(full_page) 캡처.
- WxH (예 1600x900) 로 뷰포트 지정. 기본 480x900(모바일).
- 요소가 없으면 전체 페이지를 저장(폴백).
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    url, selector, out = sys.argv[1], sys.argv[2], sys.argv[3]
    click = sys.argv[4] if len(sys.argv) > 4 else None
    vw, vh = 480, 900
    if len(sys.argv) > 5 and "x" in sys.argv[5]:
        vw, vh = (int(v) for v in sys.argv[5].split("x"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": vw, "height": vh})
        pg.goto(url, wait_until="networkidle")
        if click:
            pg.click(click)
        pg.wait_for_timeout(3000)  # 타일/차트 렌더 대기
        el = None if selector == "full" else pg.query_selector(selector)
        if el:
            el.screenshot(path=out)
        else:
            pg.screenshot(path=out, full_page=True)
        print("saved", out)
        b.close()


if __name__ == "__main__":
    main()
