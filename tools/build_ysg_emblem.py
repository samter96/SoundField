# -*- coding: utf-8 -*-
"""시작 화면(splash) 좌상단의 YSG Audio Labs 엠블럼 자산 생성기.

    python tools/build_ysg_emblem.py    → public/ysg-emblem-2026.png

═══ 왜 스크립트로 만드는가 ═══════════════════════════════════════════════════
리뉴얼 로고는 **래스터 2종**으로만 존재한다. 벡터 원본이 없다.

    brand/리뉴얼로고.png        1254x1254  어두운 배경용 렌더 (별·성운 배경 위)
    brand/리뉴얼로고_헤더용.png 1916x821   **밝은 배경용** 렌더 (흰 띠 위 가로 락업)

⚠ 헤더용(밝은 배경) 쪽을 잘라 쓰지 말 것 — 2026-09-15 에 실제로 그렇게 만든
  자산이 통째로 깨졌다. 헤더용 엠블럼은 "흰 바탕 위의 발광"이라 **밝은 속살일수록
  흰색에 가깝다.** 흰색을 기준으로 알파를 뽑으면 엠블럼의 밝은 부분이 전부 투명해지고
  어두운 외곽선만 남아, 어두운 스플래시 위에서 속이 뚫린 채 보인다. 글자도 같은
  이유로 속이 빈다. 해상도를 올려도 안 고쳐진다 — 축소 문제가 아니라 배경 제거 실패다.
  (같은 방식으로 만들어진 brand/ysg-lockup-*-2026.png 3종도 모두 깨져 있다.)

  다크 렌더 쪽은 배경이 거의 검정이라 **스크린 합성과 수학적으로 같은 알파**를
  뽑을 수 있다. 아래가 그 방식이다.

═══ 알파 뽑는 법 ═════════════════════════════════════════════════════════════
배경이 검정인 발광 그림을 어두운 카드 위에 얹는 정석은 스크린 합성이다.
그걸 알파로 굽는다(굽지 않으면 splash.html 이 mix-blend-mode 에 의존하게 된다).

    A   = (max(R,G,B) - BLACK) / (255 - BLACK)      ← 검은 점을 들어올려 성운 잡티 제거
    RGB = 색 / max(R,G,B) * 255                      ← 알파를 되나눈다(언프리멀티플라이)

BLACK 을 0 으로 두면 배경의 옅은 파란 성운까지 살아나서 **네모난 판 자국**이 보인다
(카드 색 #101216 보다 성운이 밝다). 그래서 검은 점을 올리고, 그래도 남는 네 귀퉁이는
원형 페이드로 지운다 — 엠블럼은 원형 구도라 원형으로 잘라도 잃는 게 없다.

═══ 확정 파라미터 (사용자 결정 2026-09-15) ═══════════════════════════════════
  · 스플래시 좌상단은 **엠블럼만** 쓴다. 글자("YSG AUDIO LABS")는 로고 이미지에
    넣지 않는다 — 옆의 소속 3줄(YSG / AUDIO / LABS)과 문구가 겹치기 때문이다.
  · 궤도 구(엠블럼을 감싼 원)는 로고 구성 요소이므로 살린다 → BLACK 을 더 올리면
    사라진다. 36 이 구를 남기면서 판 자국을 없애는 값이다.
"""
import os

import numpy as np
from PIL import Image

# ── 원본 ─────────────────────────────────────────────────────────────────────
SRC = r"C:\Users\samter96\Desktop\YSGAudioTools\brand\리뉴얼로고.png"

# 잘라낼 정사각 영역 (원본 1254x1254 좌표).
# 매듭 + 궤도 구가 들어가고, 아래쪽 "YSG" 글자(y≈755 부터)는 들어오지 않는 범위다.
CROP = (340, 170, 920, 750)

BLACK = 36        # 검은 점 — 이보다 어두운 픽셀은 완전 투명
FADE_IN = 0.70    # 원형 페이드 시작 (반지름 1.0 = 정사각 절반)
FADE_OUT = 1.00   # 완전 투명이 되는 반지름
OUT_PX = 512      # 출력 한 변. 화면에는 100px 로 그리므로 고배율(4x)까지 여유가 있다

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "public", "ysg-emblem-2026.png")


def emblem_rgba(px: int = OUT_PX) -> Image.Image:
    """엠블럼을 px x px RGBA 로 뽑는다. 가로 락업 생성기도 이 함수를 쓴다 —
    두 자산이 **같은 수식·같은 파라미터**를 쓰게 묶어 둔 것이다 (따로 두면 어긋난다)."""
    src = np.asarray(Image.open(SRC).convert("RGB"), dtype=np.float32)
    c = src[CROP[1]:CROP[3], CROP[0]:CROP[2]]
    h, w, _ = c.shape

    lum = c.max(axis=2)
    alpha = np.clip((lum - BLACK) / (255.0 - BLACK), 0.0, 1.0)

    # 원형 페이드 — 네 귀퉁이에 남는 성운이 네모난 자국으로 보이는 걸 막는다.
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - (w - 1) / 2) / ((w - 1) / 2),
                 (yy - (h - 1) / 2) / ((h - 1) / 2))
    v = np.clip((FADE_OUT - r) / (FADE_OUT - FADE_IN), 0.0, 1.0)
    alpha *= v * v * (3 - 2 * v)          # smoothstep — 직선 페이드는 띠가 보인다

    # 언프리멀티플라이. lum 이 0 인 자리는 알파도 0 이라 색이 뭐든 상관없다.
    rgb = np.clip(c / np.maximum(lum, 1.0)[..., None] * 255.0, 0, 255)

    img = Image.fromarray(
        np.dstack([rgb, alpha * 255.0]).astype(np.uint8), "RGBA")
    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    img = emblem_rgba()
    img.save(OUT, optimize=True)

    # ⚠ 콘솔이 cp949 라 여기서 한글·em대시를 찍으면 UnicodeEncodeError 로 죽는다
    #   (파일은 이미 저장된 뒤라 더 헷갈린다). 결과 보고는 ASCII 로만.
    import numpy as _np
    covered = float((_np.asarray(img)[..., 3] > 127).mean())
    print(f"{OUT} : {OUT_PX}x{OUT_PX}, opaque {covered:.1%}, "
          f"{os.path.getsize(OUT) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
