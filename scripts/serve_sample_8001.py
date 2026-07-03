"""샘플 모드 미리보기 서버(8001) — AUCTION_DB 없이 샘플 fixture 서빙(디자인 확인용)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("AUCTION_DB", None)

from waitress import serve  # noqa: E402

from src.web import create_app  # noqa: E402

if __name__ == "__main__":
    serve(create_app(), host="127.0.0.1", port=8001, threads=2)
