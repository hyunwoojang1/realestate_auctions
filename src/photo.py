"""물건 사진 base64 썸네일 유틸.

pgj15B `csPicLst[].picFile` 은 현장 물건사진을 **base64 JPEG 로 인라인** 반환한다(원본 다건×
전물건은 수 GB → 저장 불가). 상세페이지 히어로용으로 가로 상한 리사이즈 + JPEG 재인코딩해
소형 썸네일(base64)로 줄인다. Pillow 는 수집(크롤) 경로에서만 쓰므로 지연 임포트한다.
"""
from __future__ import annotations

import base64
import binascii
import io
import logging

logger = logging.getLogger(__name__)

THUMB_MAX_W = 640       # 썸네일 가로 상한(px) — 히어로 표시 충분, 용량 억제
THUMB_QUALITY = 75      # JPEG 재인코딩 품질(공유 Supabase 티어 용량 고려)


def thumbnail_jpeg(src_b64: str, max_w: int = THUMB_MAX_W,
                   quality: int = THUMB_QUALITY) -> bytes | None:
    """base64 JPEG → 리사이즈 JPEG '바이트'(Storage 업로드용). 실패 시 None(침묵 아님, 로그)."""
    if not src_b64:
        return None
    try:
        from PIL import Image  # noqa: PLC0415 — 서빙 경로엔 Pillow 불필요, 지연 임포트
    except ImportError:
        logger.warning("Pillow 미설치 — 사진 썸네일 생략(pip install pillow)")
        return None
    try:
        raw = base64.b64decode(src_b64)
    except (binascii.Error, ValueError) as e:
        logger.debug("사진 base64 디코드 실패: %s", e)
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if img.width > max_w:
            h = round(img.height * max_w / img.width)
            img = img.resize((max_w, h), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception as e:  # noqa: BLE001 — 비이미지/디코더 오류 등은 사진 없음으로 강등
        logger.debug("썸네일 생성 실패: %s", e)
        return None
