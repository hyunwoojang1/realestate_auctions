"""비포/애프터 PNG 두 장을 라벨 붙여 한 파일로 합성.

사용: python scripts/compose_ba.py <BEFORE_PNG> <AFTER_PNG> <OUT_PNG> [<TITLE>]
- 좌=BEFORE(빨강 라벨), 우=AFTER(초록 라벨). 세로로 다른 높이면 위 정렬, 흰 배경 패딩.
- 상단에 제목 띠(있으면). 한 눈에 변화를 보도록 나란히 배치.
"""
from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFont

PAD = 24
GAP = 24
LABEL_H = 44
TITLE_H = 52
BG = (247, 244, 238)
CARD = (255, 255, 255)
BEFORE_C = (198, 40, 40)
AFTER_C = (30, 140, 70)


def _font(size: int):
    for name in ("malgun.ttf", "malgunbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def main() -> None:
    before_p, after_p, out = sys.argv[1], sys.argv[2], sys.argv[3]
    title = sys.argv[4] if len(sys.argv) > 4 else ""
    b = Image.open(before_p).convert("RGB")
    a = Image.open(after_p).convert("RGB")
    # 폭 통일(둘 중 넓은 쪽 기준으로 각 카드 폭 = 원본 폭 유지, 높이는 큰 쪽에 맞춰 패딩)
    col_h = max(b.height, a.height)
    title_h = TITLE_H if title else 0
    W = PAD + b.width + GAP + a.width + PAD
    H = PAD + title_h + LABEL_H + col_h + PAD
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)

    if title:
        d.text((PAD, PAD - 2), title, fill=(20, 24, 32), font=_font(26))

    y0 = PAD + title_h
    fnt = _font(22)
    # BEFORE
    bx = PAD
    d.rectangle([bx, y0, bx + b.width, y0 + LABEL_H], fill=BEFORE_C)
    d.text((bx + 12, y0 + 9), "BEFORE", fill=(255, 255, 255), font=fnt)
    canvas.paste(CARD, [bx, y0 + LABEL_H, bx + b.width, y0 + LABEL_H + col_h])
    canvas.paste(b, (bx, y0 + LABEL_H))
    # AFTER
    ax = PAD + b.width + GAP
    d.rectangle([ax, y0, ax + a.width, y0 + LABEL_H], fill=AFTER_C)
    d.text((ax + 12, y0 + 9), "AFTER", fill=(255, 255, 255), font=fnt)
    canvas.paste(CARD, [ax, y0 + LABEL_H, ax + a.width, y0 + LABEL_H + col_h])
    canvas.paste(a, (ax, y0 + LABEL_H))

    canvas.save(out)
    print("saved", out, canvas.size)


if __name__ == "__main__":
    main()
