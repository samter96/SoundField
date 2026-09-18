"""인터랙션 헬퍼: 윈도우 페이드인 + 포커스 글로우."""
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QVariantAnimation
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget


class _ValueWheelGuard(QObject):
    """휠 스크롤이 콤보/스핀박스 값을 바꾸지 않게 막는 이벤트 필터.
    페이지를 스크롤하다 마우스가 위젯 위를 지나며 필터/값이 실수로 바뀌는 것 방지."""
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Wheel:
            return True   # 휠 이벤트 소비 → 위젯이 값을 못 바꿈
        return False


def block_value_wheel(widget):
    """widget(콤보/스핀박스)에서 '휠로 값 변경' 기본 동작을 끈다.
    가드 객체를 widget 자식으로 두어 수명을 함께 한다."""
    widget.installEventFilter(_ValueWheelGuard(widget))


ANIMATION_FPS = 120
FRAME_MS = max(1, round(1000 / ANIMATION_FPS))
FAST_MS = 120
BASE_MS = 180
WINDOW_FADE_MS = 240
DRAWER_MS = 168


class WindowFadeIn:
    """QMainWindow windowOpacity 0→1."""

    @staticmethod
    def apply(win, duration: int = WINDOW_FADE_MS):
        win.setWindowOpacity(0.0)
        anim = QPropertyAnimation(win, b"windowOpacity", win)
        anim.setDuration(duration)
        anim.setStartValue(0.0); anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        win._fade_in_anim = anim  # 참조 유지
        return anim


class GlowOnFocus(QObject):
    """포커스 시 DropShadow blurRadius 0↔target 애니메이션."""

    def __init__(self, widget: QWidget, target_blur: int = 16, color: str = "#4a9eff"):
        super().__init__(widget)
        self.widget = widget
        self.target = target_blur

        self.effect = QGraphicsDropShadowEffect(widget)
        self.effect.setOffset(0, 0)
        self.effect.setBlurRadius(0)
        self.effect.setColor(QColor(color))
        widget.setGraphicsEffect(self.effect)
        widget.installEventFilter(self)

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(BASE_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(lambda v: self.effect.setBlurRadius(float(v)))

    def _to(self, end: float):
        self.anim.stop()
        self.anim.setStartValue(float(self.effect.blurRadius()))
        self.anim.setEndValue(float(end))
        self.anim.start()

    def eventFilter(self, obj, event):
        if obj is self.widget:
            t = event.type()
            if t == QEvent.Type.FocusIn:
                self._to(self.target)
            elif t == QEvent.Type.FocusOut:
                self._to(0)
        return False
