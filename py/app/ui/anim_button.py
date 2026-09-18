"""AnimButton — 파일 브라우저 돋보기 버튼과 동일 결의 액션 버튼.

투명 배경 + 둥근 테두리 + 호버 색 페이드(QVariantAnimation). 아이콘은 paintEvent
에서 현재 색으로 직접 그려 호버/비활성에 글자와 함께 반응(구워진 QIcon 문제 제거).
전역 QPushButton QSS 간섭은 인라인 stylesheet + paintEvent 로 차단 → 디자인이
한 곳(여기)에서 결정된다.
"""
import math

from PyQt6.QtCore import Qt, QSize, QRectF, QPointF, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QPushButton

from app.ui.theme import COLORS


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class AnimButton(QPushButton):
    """accent: 테마 COLORS 키('accent' 등) 또는 hex 문자열.
    glyph: None | 'plus' | 'refresh' | 'stop'."""

    _PAD_L = 11
    _PAD_R = 12
    _GLYPH = 13
    _GAP = 6

    def __init__(self, text: str = "", accent: str = "accent",
                 glyph: str = None, glyph_color: str = None, parent=None):
        super().__init__(text, parent)
        self._accent = accent
        self._glyph = glyph
        self._glyph_color = glyph_color  # None 이면 본체(accent) 색 사용
        self._hover = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 전역 QPushButton QSS(배경/테두리/padding) 차단 — 전부 paintEvent 가 그림.
        self.setStyleSheet("background: transparent; border: none;")
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setWeight(QFont.Weight.DemiBold)
        self.setFont(f)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._hover = float(v)
        self.update()

    def _animate_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(float(target))
        self._anim.start()

    def enterEvent(self, e):
        if self.isEnabled():
            self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    def _accent_color(self) -> QColor:
        return QColor(COLORS.get(self._accent, self._accent))

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = self._PAD_L + fm.horizontalAdvance(self.text()) + self._PAD_R
        if self._glyph:
            w += self._GLYPH + self._GAP
        return QSize(w, max(24, fm.height() + 8))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        t = self._hover if enabled else 0.0
        muted = QColor(COLORS.get("text_muted", "#8a93a3"))

        def _state(c: QColor) -> QColor:
            # 비활성: 회색쪽으로 죽임. 활성: 호버 시 살짝 밝게.
            if not enabled:
                c = _lerp(c, muted, 0.55); c.setAlpha(150)
                return c
            return _lerp(c, QColor("#ffffff"), 0.12 * t)

        accent = _state(self._accent_color())
        radius = 7
        # 배경 — 호버 시 accent 살짝 채움.
        bg = QColor(accent); bg.setAlpha(round(t * 42))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg)
        p.drawRoundedRect(r, radius, radius)
        # 테두리 — accent, 알파 호버에 따라 진해짐.
        border = QColor(accent)
        border.setAlpha(round((55 if enabled else 45) + t * 70))
        p.setPen(QPen(border, 1.0)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, radius, radius)
        # 내용 — 글자/테두리는 accent. 아이콘은 glyph_color 지정 시 그 색(상태 반영).
        fg = accent
        gc = (_state(QColor(COLORS.get(self._glyph_color, self._glyph_color)))
              if (self._glyph and self._glyph_color) else fg)
        if self._glyph and not self.text():
            # 아이콘 전용 — 중앙 정렬.
            box = QRectF(r.center().x() - self._GLYPH / 2,
                         r.center().y() - self._GLYPH / 2, self._GLYPH, self._GLYPH)
            self._draw_glyph(p, box, gc)
            p.end()
            return
        cx = self._PAD_L
        if self._glyph:
            box = QRectF(cx, r.center().y() - self._GLYPH / 2, self._GLYPH, self._GLYPH)
            self._draw_glyph(p, box, gc)
            cx += self._GLYPH + self._GAP
        p.setPen(fg); p.setFont(self.font())
        fm = p.fontMetrics()
        baseline = r.center().y() + (fm.ascent() - fm.descent()) / 2
        p.drawText(QPointF(cx, baseline), self.text())
        p.end()

    def _draw_glyph(self, p: QPainter, box: QRectF, color: QColor):
        g = self._glyph
        if g == "plus":
            p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            cx, cy = box.center().x(), box.center().y()
            arm = box.width() * 0.30
            p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
            p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))
        elif g == "x":
            p.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            d = box.width() * 0.30
            cx, cy = box.center().x(), box.center().y()
            p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
            p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
        elif g == "stop":
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
            s = box.width() * 0.6
            p.drawRoundedRect(
                QRectF(box.center().x() - s / 2, box.center().y() - s / 2, s, s),
                1.5, 1.5)
        elif g == "refresh":
            pen = QPen(color, max(1.3, box.width() * 0.11))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            m = box.width() * 0.14
            rect = QRectF(box.x() + m, box.y() + m,
                          box.width() - 2 * m, box.height() - 2 * m)
            start_deg, span_deg = 50, 280
            p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))
            cx, cy = rect.center().x(), rect.center().y()
            rx = rect.width() / 2
            end = math.radians(start_deg + span_deg)
            ex, ey = cx + rx * math.cos(end), cy - rx * math.sin(end)
            head = box.width() * 0.18
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(color)
            p.drawPolygon(QPolygonF([
                QPointF(ex, ey),
                QPointF(ex - head, ey - head * 0.3),
                QPointF(ex - head * 0.3, ey + head),
            ]))
