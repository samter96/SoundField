"""앱 아이콘(.ico) 생성 — SoundField 아이덴티티(파형 막대)를 프로 오디오 톤으로.

## 왜 이 설계인가 — 실제 레퍼런스 원칙

Apple 의 아이콘 가이드/Icon Composer 자료와 프로 오디오 앱 아이콘에서 공통으로 쓰는 것:

1. **스퀴클 마스크, 반지름 = 폭의 22%** (1024 캔버스에서 224px).
2. **층을 나눈 깊이** — 바닥/재질/글리프를 각각 다른 광원으로 처리한다.
   한 장에 그라디언트만 얹으면 스티커처럼 보인다.
3. **부드러운 inner shadow** — 아래쪽 안쪽을 어둡게 해 볼록한 느낌을 만든다.
   진하면 노이즈가 되므로 옅고 넓게.
4. **specular 하이라이트** — 상단에 유리 반사 한 줄. 재질감의 핵심.
5. **글리프는 단순·불투명·꽉 찬 형태**로 둔다. 그래야 조명/그림자가 읽힌다.
   얇은 선(예전 버전의 링 + 심박선)은 16px 에서 사라지고 값싸 보인다.
6. **16px 가독성** — 큰 크기의 그림을 그대로 줄이지 않고, 작은 크기는 요소 수를 줄인다
   (Apple 도 크기별로 다른 아트워크를 넣는다).

## 이 아이콘의 구성

- 바닥: 깊은 남색(#16233A → #080D17) 대각 그라디언트 + 좌하단 액센트 글로우.
  프로 오디오 도구는 밝은 원색 타일보다 어두운 바닥이 격에 맞는다.
- 재질: 상단 specular 아치 / 상단 엣지 림라이트 / 하단 inner shadow.
- 글리프: **좌우 대칭 파형 막대**. 스플래시·중앙 로더의 박동 막대와 같은 모티프라
  앱 전체가 하나의 아이덴티티로 묶인다. 가운데 막대만 액센트색으로 초점을 준다.
- 크기별 막대 수: 16~24px = 3개 / 32~48px = 5개 / 64px 이상 = 7개.

왜 정사각형이 중요한가: 기존 icon.ico 는 32x31 / 48x47 / 64x62 / 128x124 / 256x249 로
**비정사각**이었다. 윈도우는 정사각 아이콘을 기대해 늘려 그리므로 흐릿하게 보였다.

⚠ icon.ico 만 바꾸고 빌드하면 cargo 가 build.rs 를 다시 돌리지 않아 exe 에 옛 아이콘이
남는다. tauri.conf.json 의 수정 시각을 갱신해 강제 재실행한 뒤 빌드할 것.

사용: python tools/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT_ICO = ROOT / "src-tauri" / "icons" / "icon.ico"
OUT_PNG = ROOT / "public" / "app_icon.png"

# 바닥 — **스플래시 카드와 같은 램프**를 쓴다 (사용자 지시 2026-09-03: 스플래시/툴
# 로고와 작업표시줄 아이콘을 하나로 통일). 남색 타일이던 예전 버전은 앱 어디에도
# 없는 색이라 따로 놀았다.
GROUND = ((0.00, (25, 27, 31)),      # #191b1f  스플래시 카드 상단
          (0.55, (16, 18, 22)),      # #101216  중간
          (1.00, (8, 9, 11)))        # #08090b  하단
ACCENT = (78, 144, 232)              # #4E90E8  앱 액센트 (좌하단 글로우)
# 글리프 — 툴 로고(WaveMark)/스플래시와 같은 파란 그라디언트. 가운데 막대만 밝게.
GLYPH_TOP = (127, 180, 242)          # #7fb4f2
GLYPH_BOTTOM = (47, 109, 196)        # #2f6dc4
GLYPH_MID = (141, 188, 245)          # #8dbcf5  가운데 막대

# 파형 봉우리 (좌우 대칭, 0~1). 7개 / 5개 / 3개 세 벌 — 작은 크기는 요소를 줄인다.
ENVELOPES = {7: (0.30, 0.56, 0.86, 1.00, 0.86, 0.56, 0.30),
             5: (0.40, 0.76, 1.00, 0.76, 0.40),
             3: (0.58, 1.00, 0.58)}

SS = 4          # 슈퍼샘플링


def _mix(t: float) -> tuple[int, int, int]:
    for i in range(len(GROUND) - 1):
        t0, c0 = GROUND[i]
        t1, c1 = GROUND[i + 1]
        if t <= t1 or i == len(GROUND) - 2:
            k = 0.0 if t1 == t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
            return tuple(round(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
    return GROUND[-1][1]


def rounded_mask(px: int, radius: float) -> Image.Image:
    m = Image.new("L", (px, px), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=255)
    return m


def vertical_fade(px: int, peak: int, reach: float, power: float) -> Image.Image:
    """위에서 아래로 사라지는 밝기 램프 (specular/림라이트 공용)."""
    fade = Image.new("L", (px, px), 0)
    fp = fade.load()
    for y in range(px):
        v = max(0.0, 1.0 - y / (px * reach))
        val = int(peak * (v ** power))
        if val:
            for x in range(px):
                fp[x, y] = val
    return fade


def draw_tiny(size: int) -> Image.Image:
    """16~20px 전용 — 픽셀에 딱 맞춰 직접 그린다.

    큰 그림을 축소하면 2px 짜리 막대와 그 사이 틈이 서로 섞여 뭉개진다(실측).
    이 크기에서는 효과를 전부 걷고, 정수 좌표로 막대를 놓아 경계를 살린다.
    Apple 도 작은 크기에는 별도 아트워크를 넣는다."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = max(2, round(size * 0.24))
    # 바닥 — 그라디언트 대신 단색 두 톤 (작은 크기에서 그라디언트는 보이지 않는다)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(18, 20, 25, 255))
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius,
                        outline=(44, 48, 56, 255), width=1)

    bar_w = 2 if size <= 17 else 3
    gap = 2
    count = 3
    span = count * bar_w + (count - 1) * gap
    left = (size - span) // 2
    cy = size / 2
    heights = (round(size * 0.44), round(size * 0.70), round(size * 0.44))
    for i, h in enumerate(heights):
        h = h if (h % 2 == 0) == (size % 2 == 0) else h + 1      # 중앙 정렬 어긋남 방지
        x0 = left + i * (bar_w + gap)
        y0 = round(cy - h / 2)
        # 이 크기에서는 그라디언트가 안 보이므로 밝은 쪽 파랑 한 톤으로 (대비 확보)
        d.rectangle([x0, y0, x0 + bar_w - 1, y0 + h - 1], fill=(141, 188, 245, 255))
    return img


def draw_icon(size: int) -> Image.Image:
    if size <= 20:
        return draw_tiny(size)
    px = size * SS
    radius = px * 0.22                       # 레퍼런스 규격: 폭의 22%
    tile = rounded_mask(px, radius)

    # ── 1층: 바닥 그라디언트 + 좌하단 액센트 글로우 ───────────────────────
    small = Image.new("RGB", (72, 72))
    sp = small.load()
    for y in range(72):
        for x in range(72):
            sp[x, y] = _mix((x / 71 * 0.5) + (y / 71 * 0.5))
    ground = small.resize((px, px), Image.BICUBIC)

    glow = Image.new("L", (px, px), 0)
    ImageDraw.Draw(glow).ellipse([-px * 0.35, px * 0.42, px * 0.78, px * 1.35], fill=92)
    glow = glow.filter(ImageFilter.GaussianBlur(px * 0.09))
    ground = Image.composite(Image.new("RGB", (px, px), ACCENT), ground, glow)

    card = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    card.paste(ground, (0, 0), tile)

    # ── 2층: 재질 (specular 아치 / 림라이트 / inner shadow) ────────────────
    # specular — 상단을 가로지르는 넓은 타원 반사
    spec = Image.new("L", (px, px), 0)
    ImageDraw.Draw(spec).ellipse([-px * 0.30, -px * 0.62, px * 1.30, px * 0.46], fill=30)
    spec = spec.filter(ImageFilter.GaussianBlur(px * 0.06))
    card.paste(Image.new("RGB", (px, px), (255, 255, 255)), (0, 0),
               Image.composite(spec, Image.new("L", (px, px), 0), tile))

    # 림라이트 — 라운드 모서리를 따라 위쪽만
    rim = Image.new("L", (px, px), 0)
    rw = max(1, round(px * 0.014))
    ImageDraw.Draw(rim).rounded_rectangle(
        [rw / 2, rw / 2, px - 1 - rw / 2, px - 1 - rw / 2],
        radius=radius - rw / 2, outline=255, width=rw)
    rim = Image.composite(vertical_fade(px, 132, 0.40, 1.3), Image.new("L", (px, px), 0), rim)
    card.paste(Image.new("RGB", (px, px), (255, 255, 255)), (0, 0),
               Image.composite(rim, Image.new("L", (px, px), 0), tile))

    # inner shadow — 아래쪽 안쪽을 옅게 어둡게 (볼록감)
    inner = Image.new("L", (px, px), 0)
    iw = max(2, round(px * 0.05))
    ImageDraw.Draw(inner).rounded_rectangle(
        [iw / 2, iw / 2, px - 1 - iw / 2, px - 1 - iw / 2],
        radius=radius, outline=255, width=iw)
    inner = inner.filter(ImageFilter.GaussianBlur(px * 0.035))
    inner_strength = 1.0 if size > 24 else 0.45
    down = Image.new("L", (px, px), 0)
    dp = down.load()
    ip = inner.load()
    for y in range(px):
        w = min(1.0, max(0.0, (y / px - 0.34) / 0.66)) ** 1.2
        if w <= 0:
            continue
        for x in range(px):
            v = ip[x, y]
            if v:
                dp[x, y] = int(v * 0.42 * w * inner_strength)
    card.paste(Image.new("RGB", (px, px), (2, 5, 12)), (0, 0),
               Image.composite(down, Image.new("L", (px, px), 0), tile))

    # ── 3층: 글리프 (좌우 대칭 파형 막대) ─────────────────────────────────
    # 작은 크기는 막대를 줄이고 굵게 — 그대로 축소하면 16px 에서 뭉개진다
    count = 7 if size >= 64 else 5 if size >= 32 else 3
    env = ENVELOPES[count]
    if count == 7:
        span, gap_ratio, max_h = px * 0.60, 0.62, px * 0.46
    elif count == 5:
        span, gap_ratio, max_h = px * 0.58, 0.50, px * 0.50
    else:
        span, gap_ratio, max_h = px * 0.60, 0.40, px * 0.58
    bar_w = span / (count + (count - 1) * gap_ratio)
    gap = bar_w * gap_ratio
    left = (px - span) / 2
    cy = px * 0.505

    bars = Image.new("L", (px, px), 0)
    db = ImageDraw.Draw(bars)
    rects = []
    for i, e in enumerate(env):
        x0 = left + i * (bar_w + gap)
        h = max_h * e
        rects.append((x0, cy - h / 2, x0 + bar_w, cy + h / 2))
        db.rounded_rectangle([x0, cy - h / 2, x0 + bar_w, cy + h / 2],
                             radius=bar_w / 2, fill=255)

    # 글리프 그림자 — 막대가 표면 위에 떠 보이게.
    # ⚠ 24px 이하에서는 생략한다. 그 크기에서 그림자는 형태를 흐리는 노이즈다.
    if size > 24:
        sh = bars.filter(ImageFilter.GaussianBlur(px * 0.018)).point(lambda v: int(v * 0.5))
        card.paste(Image.new("RGB", (px, px), (2, 6, 16)), (0, round(px * 0.016)),
                   Image.composite(sh, Image.new("L", (px, px), 0), tile))

    # 막대 본체 — 툴 로고/스플래시와 같은 **파란 세로 그라디언트**
    body = Image.new("RGB", (px, px))
    bp = body.load()
    for y in range(px):
        k = y / (px - 1)
        col = tuple(round(GLYPH_TOP[i] + (GLYPH_BOTTOM[i] - GLYPH_TOP[i]) * k) for i in range(3))
        for x in range(px):
            bp[x, y] = col
    card.paste(body, (0, 0), bars)

    # 가운데 막대만 밝게 — 툴 로고와 같은 초점 규칙
    mid = Image.new("L", (px, px), 0)
    dm = ImageDraw.Draw(mid)
    x0, y0, x1, y1 = rects[count // 2]
    dm.rounded_rectangle([x0, y0, x1, y1], radius=bar_w / 2, fill=255)
    card.paste(Image.new("RGB", (px, px), GLYPH_MID), (0, 0), mid)

    card.putalpha(Image.composite(card.split()[3], Image.new("L", (px, px), 0), tile))
    return card.resize((size, size), Image.LANCZOS)


def write_ico(frames: list[Image.Image], path: Path) -> None:
    """ICO 컨테이너를 직접 쓴다.

    ⚠ PIL 의 `save(format="ICO", sizes=[...])` 는 **넘긴 이미지 한 장을 축소해서**
    모든 크기를 만든다. 크기별로 따로 그린 그림(작은 크기용 단순화 아트워크)이
    전부 버려진다 — 실측으로 확인했다. 그래서 프레임을 그대로 담아 직접 기록한다.
    각 프레임은 PNG 로 넣는다 (Vista 이상이 읽는 표준 방식)."""
    import io
    import struct

    blobs = []
    for frame in frames:
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = b""
    for frame, blob in zip(frames, blobs):
        w = 0 if frame.width >= 256 else frame.width
        h = 0 if frame.height >= 256 else frame.height
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> int:
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [draw_icon(s) for s in sizes]
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    write_ico(frames, OUT_ICO)
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(OUT_PNG, optimize=True)
    print(f"icon.ico: {OUT_ICO.stat().st_size / 1024:.1f}KB, 정사각 {len(sizes)}종 {sizes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
