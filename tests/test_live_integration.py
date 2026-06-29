"""--live 경로 통합테스트 (F10 사전검증).

실제 국토부 API 키 없이도, 로컬 mock HTTP 서버가 fixture XML을 서빙하고
molit_client.ENDPOINTS를 mock URL로 바꿔치기하여 fetch_trades와 pipeline.run(use_live=True)
전체 라이브 경로를 검증한다. 키가 도착하면 동일 경로가 그대로 동작한다.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src import molit_client, pipeline

DATA = Path(__file__).resolve().parent.parent / "data"
_FIXTURES = {
    "/apt": (DATA / "sample_molit_apt.xml").read_text(encoding="utf-8"),
    "/rh": (DATA / "sample_rh_trades.xml").read_text(encoding="utf-8"),
    "/officetel": (DATA / "sample_offi_trades.xml").read_text(encoding="utf-8"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = next((xml for p, xml in _FIXTURES.items() if self.path.startswith(p)),
                    _FIXTURES["/apt"])
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 테스트 로그 소음 제거
        pass


@pytest.fixture
def mock_molit(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(molit_client, "ENDPOINTS", {
        "apt": base + "/apt", "rh": base + "/rh", "officetel": base + "/officetel",
    })
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


def test_fetch_trades_live_path(mock_molit):
    """라이브 fetch_trades — mock 서버에서 아파트 실거래 수집·파싱."""
    trades = molit_client.fetch_trades("apt", "11350", "202605", "DUMMYKEY")
    assert len(trades) >= 10
    assert any(t.apt_name == "상계주공" and t.price > 0 for t in trades)


def test_fetch_rh_and_officetel_live(mock_molit):
    rh = molit_client.fetch_trades("rh", "11500", "202605", "DUMMYKEY")
    assert any(t.dong == "화곡동" for t in rh)
    offi = molit_client.fetch_trades("officetel", "11680", "202605", "DUMMYKEY")
    assert any("강남역삼" in t.apt_name for t in offi)


def test_live_pipeline_end_to_end(mock_molit, monkeypatch):
    """pipeline.run(use_live=True) — 키는 더미, 시세는 mock 서버. F10 경로 사전검증."""
    monkeypatch.setenv("MOLIT_API_KEY", "DUMMYKEY")
    scored = pipeline.run(use_live=True, deal_ymd="202605")
    assert len(scored) == 6
    assert scored[0].arb_score is not None
    # 상계주공이 라이브 경로로도 '확실한 차익'으로 산출되는지
    assert any(s.apt_name == "상계주공" and s.grade == "확실한 차익" for s in scored)


def test_live_requires_key(monkeypatch):
    """키 없이 --live면 명확한 RuntimeError."""
    monkeypatch.delenv("MOLIT_API_KEY", raising=False)
    monkeypatch.delenv("MOLIT_DEAL_YMD", raising=False)
    with pytest.raises(RuntimeError):
        pipeline.run(use_live=True, deal_ymd="202605")
