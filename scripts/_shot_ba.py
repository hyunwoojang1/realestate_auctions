"""일회용: 비포/애프터 캡처(networkidle 미도달 페이지용 — domcontentloaded+셀렉터 대기)."""
import sys

from playwright.sync_api import sync_playwright

url, selector, out = sys.argv[1], sys.argv[2], sys.argv[3]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.goto(url, wait_until="domcontentloaded", timeout=60_000)
    pg.wait_for_selector(selector, timeout=30_000)
    pg.wait_for_timeout(3_000)
    el = pg.query_selector(selector)
    if el:
        el.screenshot(path=out)
    else:
        pg.screenshot(path=out, full_page=True)
    print("saved", out)
    b.close()
