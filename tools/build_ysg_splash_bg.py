# -*- coding: utf-8 -*-
"""시작 화면(splash) 배경 사진 생성기.

    python tools/build_ysg_splash_bg.py    → public/ysg-splash-bg.jpg

허브 페이지의 SoundField 제품 사진을 흐리게·어둡게 깔아 배경으로 쓴다
(사용자 결정 2026-09-15). 강도는 후보 6종을 화면에 띄워 고른 값이다:

    흐림 3 · 어둡게 62% · 채도 75%   ← "가장 덜 흐린" 쪽으로 고름

⚠ 흐림을 **CSS filter 로 걸지 말 것.** 창 가장자리에서 흐림이 카드 배경을 물어
  테두리가 뿌옇게 번지고, 첫 프레임에 흐림 계산이 얹힌다. 여기서 미리 구워 둔다.

  vite.config.ts 가 넣고, tools/build_release.ps1 이 확인한다.

크기는 창(680x360)의 2배다. 자르기는 제품 판이 가운데 오도록 세로 42% 지점에서
가로 띠를 딴다 — 원본이 정사각(1254x1254)이라 그대로 늘리면 판이 찌그러진다.
"""
import os

from PIL import Image, ImageEnhance, ImageFilter

SRC = (r"C:\Users\samter96\Desktop\YSGAudioTools\soundfield\assets\v2"
       r"\product-disk.png")

OUT_W, OUT_H = 1360, 720     # 창 680x360 의 2배
CROP_Y = 0.42                # 가로 띠를 딸 세로 위치 (0=위, 1=아래)
BLUR = 3                     # 가우시안 흐림 반경 (원본 1360px 기준)
DARK = 0.62                  # 어둡게 하는 정도
SAT = 0.75                   # 채도
QUALITY = 88

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "public", "ysg-splash-bg.jpg")


def main() -> None:
    im = Image.open(SRC).convert("RGB")
    w, h = im.size
    band = round(w / (OUT_W / OUT_H))
    top = round((h - band) * CROP_Y)
    img = im.crop((0, top, w, top + band)).resize((OUT_W, OUT_H), Image.LANCZOS)

    img = img.filter(ImageFilter.GaussianBlur(BLUR))
    img = ImageEnhance.Color(img).enhance(SAT)
    img = ImageEnhance.Brightness(img).enhance(1 - DARK)
    img.save(OUT, quality=QUALITY, optimize=True)

    # ⚠ 콘솔이 cp949 라 한글을 찍으면 UnicodeEncodeError 로 죽는다. ASCII 로만.
    print(f"{OUT} : {OUT_W}x{OUT_H}, blur {BLUR}, "
          f"{os.path.getsize(OUT) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
