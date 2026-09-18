from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QColor, QCursor, QMouseEvent, QPainter
from PyQt6.QtWidgets import QScrollBar, QStyle, QStyleOptionSlider


class RoundedScrollBar(QScrollBar):
    """Scrollbar with a real rounded painted thumb.

    Qt stylesheet border-radius does not clip QScrollBar handles reliably on
    this app's Windows/PyQt stack, so the thumb is painted directly.
    """

    THICKNESS = 12
    MARGIN = 2

    def __init__(self, orientation: Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self._pressed = False
        self.setStyleSheet("QScrollBar { background: transparent; border: none; }")
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(self.THICKNESS)
        else:
            self.setFixedWidth(self.THICKNESS)

    def _slider_rect(self) -> QRect:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar,
            opt,
            QStyle.SubControl.SC_ScrollBarSlider,
            self,
        )

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton and self._slider_rect().contains(e.pos()):
            self._pressed = True
            self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self.update()
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        super().mouseMoveEvent(e)
        self.update()

    def leaveEvent(self, e):
        self._pressed = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        from app.ui.theme import COLORS

        rect = self.rect()
        slider = self._slider_rect().adjusted(
            self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN
        )
        track = QColor(COLORS["bg_control"])
        thumb = QColor(COLORS["border_strong"])
        hover = QColor(COLORS["accent"])
        pressed = QColor(COLORS["accent_pressed"])

        pos = self.mapFromGlobal(QCursor.pos())
        over_slider = self.isEnabled() and slider.contains(pos)
        thumb_color = pressed if self._pressed else hover if over_slider else thumb

        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(track)
            r = min(rect.width(), rect.height()) / 2
            p.drawRoundedRect(rect, r, r)

            if slider.isValid() and slider.width() > 0 and slider.height() > 0:
                p.setBrush(thumb_color)
                sr = min(slider.width(), slider.height()) / 2
                p.drawRoundedRect(slider, sr, sr)
