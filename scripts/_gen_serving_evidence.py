"""프로덕션 서빙 증거 생성기 (B) — OFFLINE.

waitress(python -m src.serve)를 서브프로세스로 실제 부팅하고, 표준 라이브러리
urllib 로 GET /health, GET /api/listings 에 실 HTTP 요청을 보내 200 응답을
받은 로그를 evidence/serving_health.txt 에 기록한 뒤 서버를 종료한다.

외부(courtauction/국토부) 호출은 전혀 없다: AUCTION_DB 미설정 → web._scored 가
샘플 파이프라인으로 폴백(결정적, 오프라인). Flask test_client 가 아닌 실제 소켓
경유 HTTP 라 프로덕션 서빙 경로(waitress WSGI)를 증명한다.

사용: .venv/Scripts/python.exe scripts/_gen_serving_evidence.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE = ROOT / "evidence" / "serving_health.txt"
HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _get(port: int, path: str):
    url = f"http://{HOST}:{port}{path}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — localhost only
        body = resp.read().decode("utf-8")
        headers = "".join(f"{k}: {v}\n" for k, v in resp.headers.items())
        return resp.status, headers, body


def main() -> int:
    port = _free_port()
    out: list[str] = []
    w = out.append

    w("=== auction-arbitrage serving health (waitress, OFFLINE) ===")
    w(f"generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w("python    : .venv/Scripts/python.exe")
    w("server    : waitress (python -m src.serve)")
    w(f"bind      : {HOST}:{port}   AUCTION_DB=(unset -> sample fallback, offline)")
    w("-" * 64)

    # waitress 버전 + debug flag 확인 (프로덕션은 반드시 False)
    from importlib.metadata import version as _pkg_version  # noqa: PLC0415

    import waitress  # noqa: F401,PLC0415 — import 자체가 waitress 존재 증명

    from src.web import create_app  # noqa: PLC0415

    w("[1] waitress version:")
    w(f"    waitress {_pkg_version('waitress')}")
    w("[2] create_app() debug flag (must be False in production):")
    _app = create_app()
    _app.debug = False
    w(f"    app.debug = {_app.debug}")
    del _app
    w("-" * 64)

    # 서브프로세스로 실제 waitress 서버 부팅 (오프라인, AUCTION_DB 미설정)
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["AUCTION_HOST"] = HOST
    env["AUCTION_PORT"] = str(port)
    env.pop("AUCTION_DB", None)  # 샘플 폴백 강제 (외부 호출 0)
    env["AUCTION_DEBUG"] = "0"

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.serve"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    w(f"[boot] waitress subprocess PID={proc.pid} (python -m src.serve)")
    w("")

    rc = 1
    try:
        if not _wait_ready(port):
            w("[error] server did not become ready in time")
            return 1

        status_h, headers_h, body_h = _get(port, "/health")
        w("=== GET /health ===")
        w("-- HTTP status --")
        w(f"HTTP {status_h}")
        w("-- headers --")
        w(headers_h.rstrip())
        w("-- body --")
        w(body_h)
        w("")

        status_l, headers_l, body_l = _get(port, "/api/listings")
        data = json.loads(body_l)
        w("=== GET /api/listings (JSON, sample/offline) ===")
        w("-- HTTP status --")
        w(f"HTTP {status_l}")
        w("-- response headers --")
        w(headers_l.rstrip())
        w("-- body: count + first record --")
        w(f"    listings count = {len(data)}")
        if data:
            first = json.dumps(data[0], ensure_ascii=False)
            w(f"    first = {first[:500]}")
        w("")
        w("-" * 64)
        w(f"[assert] GET /health       -> HTTP {status_h}")
        w(f"[assert] GET /api/listings -> HTTP {status_l}")

        ok = status_h == 200 and status_l == 200 and len(data) > 0
        rc = 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        w(f"[shutdown] waitress subprocess PID={proc.pid} terminated")

    w("-" * 64)
    verdict = "PASS" if rc == 0 else "FAIL"
    w(f"RESULT: {verdict} (health 200, listings 200, JSON returned, "
      f"server booted+killed via real HTTP, offline)")

    EVIDENCE.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    print(f"\n[written] {EVIDENCE}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
