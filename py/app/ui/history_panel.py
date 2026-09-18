import os
import time
from pathlib import Path
from typing import List

from PyQt6.QtCore import Qt, QMimeData, QPoint, QRectF, QUrl, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QDrag, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem
)

from app.ui.anim import DRAWER_MS, FRAME_MS
# 실시간 버튼 상태 — QApplication.mouseButtons() 는 Qt 가 이벤트를 처리하며 갱신하는
# 캐시된 값이라, 버튼을 이미 뗐어도 release 가 큐에 남아 있으면 '눌림' 으로 보고한다.
# 결과표는 그 함정 때문에 GetAsyncKeyState 로 물리 상태를 읽는다 — 히스토리 목록도
# 같은 드래그를 하므로 같은 함수를 쓴다.
from app.ui.results_table import _left_button_down_now
from app.ui.rounded_scrollbar import RoundedScrollBar


class _DraggableHistoryList(QListWidget):
    """선택한 히스토리 항목을 파일 URL로 드래그 — DAW 드롭 가능."""

    dragDropped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start = QPoint()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBar(RoundedScrollBar(Qt.Orientation.Horizontal, self))
        self.setVerticalScrollBar(RoundedScrollBar(Qt.Orientation.Vertical, self))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            sb = self.horizontalScrollBar()
            delta = e.angleDelta().y() or e.angleDelta().x()
            sb.setValue(sb.value() - delta)
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(e)
            return
        if (e.pos() - self._drag_start).manhattanLength() < 10:
            super().mouseMoveEvent(e)
            return
        # 빠른 클릭 시 버튼을 이미 뗀 뒤 처리되는 stale move 이벤트로 drag.exec() 가
        # 버튼 없이 시작돼 라벨이 마우스에 붙어 안 떨어지는 문제 방지 (실시간 버튼 확인).
        # ⚠ QApplication.mouseButtons() 로는 부족하다 — 위 import 주석 참고.
        if not _left_button_down_now():
            super().mouseMoveEvent(e)
            return
        paths = []
        seen = set()
        for it in self.selectedItems():
            p = it.data(Qt.ItemDataRole.UserRole)
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
        if not paths:
            return
        mime = QMimeData()
        from app.ui.results_table import _draggable_path
        mime.setUrls([QUrl.fromLocalFile(_draggable_path(str(Path(p))))
                      for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        result = drag.exec(Qt.DropAction.CopyAction)
        if result != Qt.DropAction.IgnoreAction:
            self.dragDropped.emit()


class _HistoryResizeGrip(QWidget):
    """패널 좌측 가장자리 드래그 핸들 — 너비 수동 조절.
    QSplitter handle 톤(border→accent_hover→accent_pressed) + transport 버튼식 hover alpha 보간."""

    GRIP_W = 8

    def __init__(self, panel: "HistoryPanel"):
        super().__init__(panel)
        self._panel = panel
        self.setObjectName("historyGrip")
        self.setFixedWidth(self.GRIP_W)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        # QSS 배경 간섭 방지 — paintEvent 로 직접 그림
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._dragging = False
        self._press_x = 0.0
        self._start_w = 0
        self._hover_alpha = 0  # 0..255 보간값

        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.setInterval(FRAME_MS)
        self._anim_timer.timeout.connect(self._update_anim)

    def enterEvent(self, e):
        self._anim_timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim_timer.start()
        super().leaveEvent(e)

    def _update_anim(self):
        target = 255 if (self.underMouse() or self._dragging) else 0
        step = 24  # 약 120ms 페이드 인/아웃
        if abs(self._hover_alpha - target) <= step:
            self._hover_alpha = target
            self._anim_timer.stop()
        else:
            self._hover_alpha += step if self._hover_alpha < target else -step
        self.update()

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._press_x = e.globalPosition().x()
            self._start_w = self._panel.width()
            self._anim_timer.start()  # pressed 톤으로 즉시 보간
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging:
            dx = e.globalPosition().x() - self._press_x
            # 우측 정렬 overlay — 좌측 grip 드래그하면 dx 만큼 패널이 좁아짐(우측으로 가면 +dx).
            new_w = int(self._start_w - dx)
            new_w = max(self._panel.MIN_WIDTH, min(self._panel.MAX_WIDTH, new_w))
            self._panel.set_width_manual(new_w)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._anim_timer.start()  # hover or off 톤으로 복귀 보간
            self._panel.widthChanged.emit(self._panel.width())
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        from .theme import COLORS
        base = QColor(COLORS["border_strong"])
        hover = QColor(COLORS["accent_hover"])
        pressed = QColor(COLORS["accent_pressed"])

        ha = self._hover_alpha  # 0..255
        # base ↔ hover 보간 (RGB 선형). 드래그 중이면 결과를 pressed 와 추가 50% 블렌드.
        t = ha / 255.0
        r = int(base.red()   + (hover.red()   - base.red())   * t)
        g = int(base.green() + (hover.green() - base.green()) * t)
        b = int(base.blue()  + (hover.blue()  - base.blue())  * t)
        if self._dragging:
            r = (r + pressed.red())   // 2
            g = (g + pressed.green()) // 2
            b = (b + pressed.blue())  // 2
        fill = QColor(r, g, b)

        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(COLORS["bg_control_hi"]))
            p.drawRect(rect)
            wash = QColor(COLORS["accent"])
            wash.setAlpha(int(16 + 36 * t))
            p.setBrush(wash)
            p.drawRect(rect)
            cx = rect.center().x()
            edge = QColor(COLORS["border_strong"])
            edge.setAlpha(105 + int(40 * t))
            p.setBrush(edge)
            p.drawRect(QRectF(rect.left(), rect.top(), 2, rect.height()))
            p.drawRect(QRectF(rect.right() - 2, rect.top(), 2, rect.height()))
            band_bg = QColor(COLORS["bg_elev"])
            band_bg.setAlpha(230)
            p.setBrush(band_bg)
            band_w = max(6.0, rect.width() * 0.62)
            guide_w = max(3.0, rect.width() * 0.32)
            grip_w = max(5.0, rect.width() * 0.48) + int(1 * t)
            p.drawRoundedRect(QRectF(cx - band_w / 2, 6, band_w, rect.height() - 12), 3, 3)
            guide = QColor(COLORS["border_strong"])
            guide.setAlpha(135 + int(70 * t))
            p.setBrush(guide)
            p.drawRoundedRect(QRectF(cx - guide_w / 2, 12, guide_w, rect.height() - 24), 3, 3)
            grip_h = min(180, max(80, int(rect.height() * 0.20))) + int(30 * t)
            cy = rect.center().y()
            band = QRectF(cx - grip_w / 2, cy - grip_h / 2, grip_w, grip_h)
            p.setBrush(fill)
            p.drawRoundedRect(band, grip_w / 2, grip_w / 2)

            # 호버 시 중앙에 ⋮ 형태의 미세 도트 3개 — splitter handle 시각 단서
            if ha > 80:
                dot_alpha = min(255, (ha - 80) * 2)
                dot_col = QColor(COLORS["on_accent"])
                dot_col.setAlpha(dot_alpha)
                p.setBrush(dot_col)
                for i in (-1, 0, 1):
                    dy = cy + i * 8
                    p.drawEllipse(QRectF(cx - 1.3, dy - 1.3, 2.6, 2.6))


class HistoryPanel(QWidget):
    """우측 슬라이드 드로어 — 재생 히스토리.
    results_wrap 의 QHBoxLayout 안에 results 옆에 가로 배치. maximumWidth 애니메이션
    으로 슬라이드 인/아웃 — width 가 늘어나면 results 가 자동으로 좁아져 스크롤바가
    panel 좌측에 노출 (overlay 시절 스크롤바 가림 문제 해결).
    좌측 8px _HistoryResizeGrip 으로 너비 수동 조절."""

    itemPicked = pyqtSignal(str)
    clearRequested = pyqtSignal()
    collapseRequested = pyqtSignal()
    dragDropped = pyqtSignal()
    widthChanged = pyqtSignal(int)  # grip 드래그 끝났을 때 — 사용자 너비 저장용

    EXPANDED_WIDTH = 280
    MIN_WIDTH = 180
    MAX_WIDTH = 800
    ANIM_MS = DRAWER_MS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("historyPanel")
        self.setMinimumWidth(0)
        self.setMaximumWidth(0)  # 시작은 접힘
        self._anim: QTimer | None = None
        self._anim_start_w = 0
        self._anim_target_w = 0
        self._anim_start_t = 0.0
        self._current_w = 0  # 펼침 상태 width 기억

        # grip + content 가로 배치
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._grip = _HistoryResizeGrip(self)
        root.addWidget(self._grip)

        self._content = QWidget(self)
        self._content.setObjectName("historyContent")
        root.addWidget(self._content, 1)

        v = QVBoxLayout(self._content)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 6)
        label = QLabel("히스토리"); label.setObjectName("categoryLabel")
        header.addWidget(label, 1)
        v.addLayout(header)

        self.list_widget = _DraggableHistoryList()
        self.list_widget.setObjectName("historyList")
        self.list_widget.setAlternatingRowColors(False)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setFrameShape(QListWidget.Shape.NoFrame)
        self.list_widget.itemActivated.connect(self._on_activated)
        self.list_widget.dragDropped.connect(self.dragDropped)
        v.addWidget(self.list_widget, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 4, 4, 4)
        self.count_label = QLabel("0개")
        self.count_label.setObjectName("controlLabel")
        footer.addWidget(self.count_label, 1)
        clear_btn = QPushButton("지우기")
        clear_btn.setObjectName("pill")
        clear_btn.clicked.connect(lambda: self.clearRequested.emit())
        footer.addWidget(clear_btn)
        v.addLayout(footer)

    # ── 너비 제어 (layout 기반: setMinimum/MaximumWidth) ────────────────
    def _set_width(self, w: int):
        # min/max 동시 — layout 즉시 반응 (프레임 드롭 감소)
        w = max(0, int(w))
        self.setMinimumWidth(w)
        self.setMaximumWidth(w)

    # ── public API ────────────────────────────────────────────────────
    def set_items(self, items: List[str]):
        """items: 오래된 → 최신 순. 화면엔 최신이 위로."""
        self.list_widget.clear()
        for p in reversed(items):
            li = QListWidgetItem(os.path.basename(p) or p)
            li.setData(Qt.ItemDataRole.UserRole, p)
            li.setToolTip(p)
            self.list_widget.addItem(li)
        self.count_label.setText(f"{len(items)}개")

    def _on_activated(self, item: QListWidgetItem):
        p = item.data(Qt.ItemDataRole.UserRole)
        if p:
            self.itemPicked.emit(p)

    def is_expanded(self) -> bool:
        return self.maximumWidth() > 0

    def set_width_manual(self, w: int):
        """grip 드래그 — 애니메이션 중이 아닐 때만 즉시 너비 반영."""
        if self._anim is not None and self._anim.isActive():
            return
        self._current_w = w
        self._set_width(w)

    def expand(self, width: int | None = None):
        target = int(width) if width and width > 0 else self.EXPANDED_WIDTH
        target = max(self.MIN_WIDTH, min(self.MAX_WIDTH, target))
        if not self.is_expanded():
            self._animate_to(target)

    def collapse(self):
        if self.is_expanded():
            self._current_w = self.maximumWidth()
            self._animate_to(0)

    # ── 애니메이션 ────────────────────────────────────────────────────
    def _animate_to(self, target_w: int):
        if self._anim is not None:
            self._anim.stop()
        self._anim_start_w = self.maximumWidth()
        self._anim_target_w = target_w
        self._anim_start_t = time.monotonic()
        timer = QTimer(self)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(FRAME_MS)
        timer.timeout.connect(self._anim_step)
        timer.start()
        self._anim = timer

    def _anim_step(self):
        elapsed = (time.monotonic() - self._anim_start_t) * 1000
        t = min(elapsed / self.ANIM_MS, 1.0)
        t_e = 1.0 - (1.0 - t) ** 3  # OutCubic
        w = int(self._anim_start_w + (self._anim_target_w - self._anim_start_w) * t_e)
        self._set_width(w)
        if t >= 1.0:
            self._set_width(self._anim_target_w)
            self._anim.stop()
            if self._anim_target_w > 0:
                self._current_w = self._anim_target_w
