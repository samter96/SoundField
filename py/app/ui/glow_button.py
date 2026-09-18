"""GlowButton — 젤리 글로우 스타일 버튼 (paintEvent 직접 그리기, QSS 미사용).

레퍼런스 구조 5겹: 바깥 글로우 / 외곽 링 / 세로 그라데이션 채움 /
윗변 안쪽 하이라이트 / 내용물(아이콘 칩 + 텍스트).

테마 전환: paint 시점에 COLORS[color_key] 조회 — update() 만으로 즉시 반영.
"""
from PyQt6.QtCore import QPointF, QRectF, QVariantAnimation, QEasingCurve, QSize, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient,
                         QPainter, QPainterPath, QPen)
from PyQt6.QtWidgets import QPushButton

from .theme import COLORS


def _mix(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(
        round(c1.red() + (c2.red() - c1.red()) * t),
        round(c1.green() + (c2.green() - c1.green()) * t),
        round(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


class GlowButton(QPushButton):
    """color_key 하나로 글로우/링/그라데이션/칩 색을 전부 파생.

    content_height 가 본체(채움 영역) 높이 — 글로우 여백(glow_margin)이
    상하좌우로 더해져 위젯 자체는 그만큼 커진다. 옆 버튼들과 시각 높이를
    맞추려면 content_height 를 옆 버튼 높이와 같게 줄 것.
    """

    def __init__(self, text: str = "", color_key: str = "accent",
                 content_height: int = 24, glow_margin: int = 6,
                 plus_chip: bool = False, pill: bool = False, parent=None):
        super().__init__(text, parent)
        self._color_key = color_key
        self._content_h = content_height
        self._margin = glow_margin
        self._plus_chip = plus_chip
        self._pill = pill
        self._hover_t = 0.0

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(content_height + glow_margin * 2)
        # QSS 간섭 차단 — 전부 paintEvent 가 그림
        self.setStyleSheet("background: transparent; border: none;")

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)
        self.setFont(f)

    # ───────── 호버 애니메이션 ─────────
    def _on_anim(self, v):
        self._hover_t = float(v)
        self.update()

    def _animate_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._hover_t)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):
        if self.isEnabled():
            self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    # ───────── 크기 ─────────
    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text()) + 20 + 2  # 좌우 패딩 10px + 0.5px 정렬 보정
        if self._plus_chip:
            w += self._chip_size() + 6
        return QSize(w + self._margin * 2, self._content_h + self._margin * 2)

    def _chip_size(self) -> int:
        return round(self._content_h * 14 / 24)

    # ───────── 그리기 ─────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        m = self._margin
        # 0.5px 정렬 — 1px 펜이 픽셀 경계에 걸려 모서리가 깨지는 것 방지
        body = QRectF(self.rect()).adjusted(m + 0.5, m + 0.5, -m - 0.5, -m - 0.5)
        # HTML 스펙: radius 7px @ height 24px
        radius = body.height() / 2 if self._pill else body.height() * (7 / 24)

        base = QColor(COLORS.get(self._color_key, COLORS["accent"]))
        enabled = self.isEnabled()
        if not enabled:
            g = round(base.red() * 0.299 + base.green() * 0.587 + base.blue() * 0.114)
            base = _mix(QColor(g, g, g), QColor(COLORS["bg_elev"]), 0.55)
        pressed = enabled and self.isDown()
        t = self._hover_t

        # ① 바깥 글로우 — 기본 blur10/α.45 → 호버 blur14/α.60 → 눌림 blur6/α.30
        if enabled and m > 0:
            glow = QColor(base)
            extent = m * ((0.6 if pressed else 0.72 + 0.28 * t))
            peak = (0.30 if pressed else 0.45 + 0.15 * t) * 255
            # 1px 두께 링을 바깥으로 쌓으며 이차 감쇠 — 단차 없는 부드러운 번짐
            layers = max(3, round(extent * 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(layers):
                f = 1 - i / layers
                glow.setAlpha(max(0, round(peak * 0.30 * f * f)))
                grow = extent * (i + 0.5) / layers
                p.setPen(QPen(glow, extent / layers + 0.6))
                p.drawRoundedRect(body.adjusted(-grow, -grow, grow, grow),
                                  radius + grow, radius + grow)

        # ③ 세로 그라데이션 채움 (0% / 55% / 100% 3단)
        if pressed:
            s1 = _mix(base, QColor(0, 0, 0), 0.14)
            s2 = _mix(base, QColor(0, 0, 0), 0.23)
            s3 = _mix(base, QColor(0, 0, 0), 0.33)
        else:
            s1 = _mix(base, QColor(255, 255, 255), 0.13 + 0.15 * t)
            s2 = _mix(base, QColor(255, 255, 255), 0.11 * t)
            s3 = _mix(base, QColor(0, 0, 0), 0.14 - 0.07 * t)
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.0, s1)
        grad.setColorAt(0.55, s2)
        grad.setColorAt(1.0, s3)

        # ② 테두리 1px — 위 밝음 → 아래 본체색 세로 그라데이션 (분리감 방지)
        if pressed:
            ring_top, ring_bot = 0.10, 0.0
        elif enabled:
            ring_top, ring_bot = 0.27 + 0.18 * t, 0.05
        else:
            ring_top, ring_bot = 0.12, 0.02
        ring_grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        ring_grad.setColorAt(0.0, _mix(base, QColor(255, 255, 255), ring_top))
        ring_grad.setColorAt(1.0, _mix(s3, QColor(255, 255, 255), ring_bot))
        p.setPen(QPen(QBrush(ring_grad), 1.0))
        p.setBrush(grad)
        p.drawRoundedRect(body, radius, radius)

        # ④ 윗변 안쪽 하이라이트 — CSS `inset 0 1px` 와 동일한 초승달 모양:
        # 안쪽 둥근사각형에서 아래로 민 같은 모양을 빼낸 영역만 칠한다.
        inner = body.adjusted(0.5, 0.5, -0.5, -0.5)
        inner_r = max(0.0, radius - 0.5)
        path = QPainterPath()
        path.addRoundedRect(inner, inner_r, inner_r)
        p.setPen(Qt.PenStyle.NoPen)
        if pressed:
            # 눌림: 하이라이트 대신 안쪽 어두운 그림자 (inset 0 2px 4px 근사 — 2겹)
            for dy, a in ((2.0, 0.20), (3.5, 0.12)):
                p.setBrush(QColor(20, 8, 60, round(a * 255)))
                p.drawPath(path - path.translated(0, dy))
        else:
            a = (0.35 + 0.10 * t) if enabled else 0.06
            p.setBrush(QColor(255, 255, 255, round(a * 255)))
            p.drawPath(path - path.translated(0, 1.0))

        # ⑤ 내용물 — 텍스트 색: 채움 밝기에 따라 자동 (라이트 테마 다크브라운 대응)
        lum = s2.red() * 0.299 + s2.green() * 0.587 + s2.blue() * 0.114
        fg = QColor(255, 255, 255) if lum < 150 else QColor(20, 18, 12)
        if not enabled:
            fg.setAlpha(140)
        elif pressed:
            fg.setAlpha(232)

        content_left = body.left() + 10
        if self._plus_chip:
            cs = self._chip_size()
            chip = QRectF(content_left, body.center().y() - cs / 2, cs, cs)
            chip_bg = QColor(fg)
            if not enabled:
                chip_a = 0.10
            elif pressed:
                chip_a = 0.18
            else:
                chip_a = 0.26 + 0.06 * t
            chip_bg.setAlpha(round(chip_a * 255))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(chip_bg)
            p.drawRoundedRect(chip, cs * (4 / 14), cs * (4 / 14))
            # + 기호
            pen = QPen(fg, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            cx, cy, arm = chip.center().x(), chip.center().y(), cs * 0.24
            p.drawLine(QRectF(cx - arm, cy, arm * 2, 0).topLeft(),
                       QRectF(cx - arm, cy, arm * 2, 0).topRight())
            p.drawLine(QRectF(cx, cy - arm, 0, arm * 2).topLeft(),
                       QRectF(cx, cy - arm, 0, arm * 2).bottomLeft())
            content_left = chip.right() + 6

        # 글자는 "실제 장치 픽셀"에 정렬해 그린다 — 디스플레이 배율(125% 등)에서도
        # 획이 픽셀 사이에 걸쳐 흐려지지 않게 논리좌표 * 배율을 정수로 스냅.
        p.setPen(fg)
        p.setFont(self.font())
        fm = p.fontMetrics()
        dpr = self.devicePixelRatioF()
        baseline = body.center().y() + (fm.ascent() - fm.descent()) / 2
        baseline = round(baseline * dpr) / dpr
        tx = round(content_left * dpr) / dpr
        p.drawText(QPointF(tx, baseline), self.text())
        p.end()
