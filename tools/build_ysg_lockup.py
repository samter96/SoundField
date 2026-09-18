# -*- coding: utf-8 -*-
"""시작 화면(splash) 좌상단의 YSG Audio Labs **가로 락업** 자산 생성기.

    python tools/build_ysg_lockup.py    → public/ysg-lockup-2026.png

═══ 무엇을 만드는가 ═══════════════════════════════════════════════════════════
엠블럼 + "YSG AUDIO LABS" 워드마크가 한 줄로 붙은 가로 락업이다. 배치는 허브
페이지 상단바와 같다 (사용자 결정 2026-09-15).

왜 글자를 웹폰트로 찍지 않는가 — 워드마크의 "YSG" 는 폰트가 아니라 **그라데이션이
입혀진 그림**이다. 어떤 글꼴을 골라도 같은 모양이 안 나온다. 브랜드 원본이 래스터
뿐이라(벡터 없음) 이미지를 얹는 것이 원본과 일치하는 유일한 방법이다.

═══ ⚠ 완성된 락업 파일을 그대로 줄여 쓰지 말 것 ══════════════════════════════
2026-09-15 첫 시도가 그렇게 해서 **엠블럼만 뭉개져 나갔다** (사용자 신고 "로고가
깨져 보인다"). 브랜드 폴더의 완성 락업(`ysg-lockup-compact-dark-2026.png`)은
1060x190 짜리 **이미 축소된 합성본**이라, 그 안의 엠블럼은 190px 밖에 안 된다.
화면에서 락업을 380px 로 그리면 엠블럼은 55px 이 되는데, 저해상도 원본을
또 줄이니 색 덩어리로 뭉갠다 (같은 크기라도 1254px 원본에서 직접 줄이면 선명하다).

그래서 여기서는 **부품을 각각 제일 좋은 원본에서 가져와 조립**한다.

    엠블럼   brand/리뉴얼로고.png (1254x1254 다크 렌더)
             → build_ysg_emblem.emblem_rgba() 가 필요한 크기로 바로 굽는다.
    워드마크 tools/brand_src/ysg-wordmark-2026.png
             → 허브 락업(2026-09-15 16:21 이전 판)에서 글자 부분만 떼어 둔 것.
               ⚠ 브랜드 폴더의 그 파일은 그 뒤 **깨진 판으로 덮어써졌다**
                 (엠블럼이 흰 바탕 키잉본으로 바뀜). 다시 뜨지 말 것.
               ⚠ brand/리뉴얼로고_헤더용.png 의 글자를 대신 쓰지 말 것 — 자간이
                 다르다 (AUDIO LABS 가 좁다). 허브와 다른 그림이 된다.

═══ 배치 (기준 락업에서 실측한 비율) ════════════════════════════════════════
YSG 대문자 높이(C=55, 아래 WORD 원본 기준)를 단위로:
    엠블럼 높이 2.00C · 엠블럼~YSG 사이 0.53C · 글자 세로 중앙 정렬
엠블럼은 궤도 구까지 들어간 정사각이라, 매듭 크기를 기준 락업과 맞추려면
정사각 변을 조금 키워야 한다 (EMBLEM_SCALE).
"""
import os

from PIL import Image

from build_ysg_emblem import emblem_rgba

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

WORD = os.path.join(HERE, "brand_src", "ysg-wordmark-2026.png")
OUT = os.path.join(ROOT, "public", "ysg-lockup-2026.png")

# 워드마크 원본 안에서 글자가 차지하는 세로 범위 (실측: y27~91, YSG 대문자 34~88)
CAP = 55            # YSG 대문자 높이
EMBLEM_H = 2.00     # 엠블럼 높이 = CAP x 이 값
GAP = 0.53          # 엠블럼과 글자 사이 = CAP x 이 값
EMBLEM_SCALE = 1.18  # 궤도 구 여백 보정 — 매듭이 기준 락업과 같은 크기로 보이게

# 출력 배율. 화면에는 splash.html / app.css 의 .lockup 폭으로 그린다.
# 그 2배 이상이 되게 잡는다 (고배율 화면 대비).
SCALE = 2.0


def main() -> None:
    word = Image.open(WORD).convert("RGBA")
    wb = word.split()[-1].getbbox()
    word = word.crop((wb[0], 0, wb[2], word.height))   # 좌우 여백만 턴다

    cap = CAP * SCALE
    em_px = round(cap * EMBLEM_H * EMBLEM_SCALE)
    gap = round(cap * GAP)

    w = word.resize((round(word.width * SCALE), round(word.height * SCALE)),
                    Image.LANCZOS)
    em = emblem_rgba(em_px)

    h = max(em.height, w.height)
    out = Image.new("RGBA", (em.width + gap + w.width, h), (0, 0, 0, 0))
    out.alpha_composite(em, (0, (h - em.height) // 2))
    out.alpha_composite(w, (em.width + gap, (h - w.height) // 2))

    box = out.split()[-1].getbbox()
    out = out.crop(box)
    out.save(OUT, optimize=True)

    # ⚠ 콘솔이 cp949 라 한글을 찍으면 UnicodeEncodeError 로 죽는다. ASCII 로만.
    print(f"{OUT} : {out.width}x{out.height}, emblem {em_px}px, "
          f"{os.path.getsize(OUT) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
