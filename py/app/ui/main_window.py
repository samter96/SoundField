import json
import logging
import os
import subprocess
import time
from html import escape as _html_escape
from pathlib import Path
from threading import Event
from typing import Optional

from datetime import datetime

from PyQt6.QtCore import (
    Qt, QThread, QEvent, pyqtSignal, pyqtSlot, QObject, QSize, QTimer, QPointF, QRectF, QPoint,
    QByteArray, QRect
)
from PyQt6.QtGui import (
    QBrush, QColor, QGuiApplication, QIcon, QKeySequence, QPainter,
    QPen, QPixmap, QPolygonF, QRadialGradient, QShortcut, QValidator, QTextDocument
)
from PyQt6.QtSvg import QSvgRenderer
import math
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QMessageBox, QProgressBar, QStatusBar, QComboBox,
    QSpinBox, QDoubleSpinBox, QSplitter, QDialog, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QSplitterHandle,
    QGraphicsOpacityEffect, QSizePolicy,
    QScrollArea, QFrame, QMenu, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTabWidget, QFormLayout, QRadioButton, QButtonGroup, QKeySequenceEdit,
    QGroupBox, QCheckBox, QSlider, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QApplication, QListWidget, QListWidgetItem,
)
from PyQt6.QtMultimedia import QMediaPlayer

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# 폴더 트리/status bar 미완료 인디케이터 공통 색 (warning tone)
_INCOMPLETE_COLOR_HEX = "#ffa64d"

from app.database import Database
from app.library_manager import LibraryManager, IndexProgress, _prefix as _lib_prefix
from app.ui.results_table import ResultsTable, _reveal_in_explorer as _shell_reveal
from app.ui.folder_tree import FolderTree
from app.ui.library_tabs import LibraryTabs
from app.ui.blacklist_panel import BlacklistPanel
from app.ui.multi_search import MultiSearch, _HelpButton
from app.ui.player_widget import PlayerWidget, SEGMENT_HEADER_H
from app.ui.history_panel import HistoryPanel
from app.ui.anim import FRAME_MS, WINDOW_FADE_MS, WindowFadeIn, block_value_wheel
from app.ui.theme import COLORS, build_qss, apply_theme, current_theme
from app.ui.anim_button import AnimButton

logger = logging.getLogger(__name__)

CONFIG_FILE = Path.home() / ".soundfield_config.json"
DEFAULT_STORE = Path.home() / "AppData" / "Local" / "SoundField"

# 다이얼로그 수명과 분리해야 하는 백그라운드 QThread 보관소.
# QThread(self=다이얼로그) 로 부모를 붙이면 로딩 중 다이얼로그를 닫는 순간
# 실행 중인 스레드가 함께 파괴되어 access violation 크래시
# ("QThread: Destroyed while thread is still running", 2026-06-12 실측).
# → 부모 없이 만들고 여기 ref 를 보관, 스레드 종료 시 자동 해제.
_BG_THREAD_REFS: set = set()


def _hold_bg_thread(thread: QThread, worker: QObject):
    """thread 가 끝날 때까지 (thread, worker) 참조 유지 — 소유 다이얼로그가
    먼저 닫혀도 C++/Python 어느 쪽도 조기 파괴되지 않게 한다.
    수신측 슬롯은 다이얼로그 파괴 시 Qt 가 자동 disconnect 하므로 안전."""
    pair = (thread, worker)
    _BG_THREAD_REFS.add(pair)
    thread.finished.connect(lambda: _BG_THREAD_REFS.discard(pair))


class _GripSplitterHandle(QSplitterHandle):
    """Splitter handle with a centered grip, distinct from scrollbars."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._dragging = False
        self._hover_alpha = 0
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

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._anim_timer.start()
            self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._anim_timer.start()
            self.update()
        super().mouseReleaseEvent(e)

    def _update_anim(self):
        target = 255 if (self.isEnabled() and (self.underMouse() or self._dragging)) else 0
        step = 34
        if abs(self._hover_alpha - target) <= step:
            self._hover_alpha = target
            self._anim_timer.stop()
        else:
            self._hover_alpha += step if self._hover_alpha < target else -step
        self.update()

    def paintEvent(self, e):
        rect = QRectF(self.rect())
        t = self._hover_alpha / 255.0
        bg = QColor(COLORS["bg_control_hi"])
        sep = QColor(COLORS["border"])
        grip = QColor(COLORS["border_strong"] if self.isEnabled() else COLORS["border_soft"])
        hover = QColor(COLORS["accent_hover"])
        pressed = QColor(COLORS["accent_pressed"])
        wash = QColor(COLORS["accent"])
        wash.setAlpha(0 if not self.isEnabled() else int(16 + 36 * t))
        r = int(grip.red() + (hover.red() - grip.red()) * t)
        g = int(grip.green() + (hover.green() - grip.green()) * t)
        b = int(grip.blue() + (hover.blue() - grip.blue()) * t)
        grip_col = QColor(r, g, b)
        if self._dragging:
            grip_col = QColor(
                (grip_col.red() + pressed.red()) // 2,
                (grip_col.green() + pressed.green()) // 2,
                (grip_col.blue() + pressed.blue()) // 2,
            )

        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRect(rect)
            p.setBrush(wash)
            p.drawRect(rect)

            p.setBrush(sep)
            if self.orientation() == Qt.Orientation.Vertical:
                edge = QColor(COLORS["border_strong"])
                edge.setAlpha(105 + int(40 * t))
                p.setBrush(edge)
                p.drawRect(QRectF(rect.left(), rect.top(), rect.width(), 2))
                p.drawRect(QRectF(rect.left(), rect.bottom() - 2, rect.width(), 2))
                band = QColor(COLORS["bg_elev"])
                band.setAlpha(230)
                cy = rect.center().y()
                band_h = max(6.0, rect.height() * 0.62)
                guide_h = max(3.0, rect.height() * 0.32)
                grip_h = max(5.0, rect.height() * 0.48) + int(1 * t)
                p.setBrush(band)
                p.drawRoundedRect(QRectF(6, cy - band_h / 2, rect.width() - 12, band_h), 3, 3)
                guide = QColor(COLORS["border_strong"])
                guide.setAlpha(135 + int(70 * t))
                p.setBrush(guide)
                p.drawRoundedRect(QRectF(12, cy - guide_h / 2, rect.width() - 24, guide_h), 3, 3)
                grip_w = min(220, max(96, int(rect.width() * 0.18))) + int(34 * t)
                cx = rect.center().x()
                p.setBrush(grip_col)
                p.drawRoundedRect(
                    QRectF(cx - grip_w / 2, cy - grip_h / 2, grip_w, grip_h),
                    grip_h / 2, grip_h / 2
                )
                if self._hover_alpha > 70:
                    dot_col = QColor(COLORS["on_accent"])
                    dot_col.setAlpha(min(210, (self._hover_alpha - 70) * 2))
                    p.setBrush(dot_col)
                    for x in (-8, 0, 8):
                        p.drawEllipse(QRectF(cx + x - 1.3, cy - 1.3, 2.6, 2.6))
            else:
                edge = QColor(COLORS["border_strong"])
                edge.setAlpha(105 + int(40 * t))
                p.setBrush(edge)
                p.drawRect(QRectF(rect.left(), rect.top(), 2, rect.height()))
                p.drawRect(QRectF(rect.right() - 2, rect.top(), 2, rect.height()))
                band = QColor(COLORS["bg_elev"])
                band.setAlpha(230)
                cx = rect.center().x()
                band_w = max(6.0, rect.width() * 0.62)
                guide_w = max(3.0, rect.width() * 0.32)
                grip_w = max(5.0, rect.width() * 0.48) + int(1 * t)
                p.setBrush(band)
                p.drawRoundedRect(QRectF(cx - band_w / 2, 6, band_w, rect.height() - 12), 3, 3)
                guide = QColor(COLORS["border_strong"])
                guide.setAlpha(135 + int(70 * t))
                p.setBrush(guide)
                p.drawRoundedRect(QRectF(cx - guide_w / 2, 12, guide_w, rect.height() - 24), 3, 3)
                grip_h = min(180, max(80, int(rect.height() * 0.20))) + int(30 * t)
                cy = rect.center().y()
                p.setBrush(grip_col)
                p.drawRoundedRect(
                    QRectF(cx - grip_w / 2, cy - grip_h / 2, grip_w, grip_h),
                    grip_w / 2, grip_w / 2
                )
                if self._hover_alpha > 70:
                    dot_col = QColor(COLORS["on_accent"])
                    dot_col.setAlpha(min(210, (self._hover_alpha - 70) * 2))
                    p.setBrush(dot_col)
                    for y in (-8, 0, 8):
                        p.drawEllipse(QRectF(cx - 1.3, cy + y - 1.3, 2.6, 2.6))


class _GripSplitter(QSplitter):
    def __init__(self, orientation: Qt.Orientation, handle_width: int = 10, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(handle_width)
        self.setMouseTracking(True)

    def createHandle(self):
        return _GripSplitterHandle(self.orientation(), self)


class _CurrentDotDelegate(QStyledItemDelegate):
    """드랍다운 popup view 의 현재 선택 항목 좌측에 작은 teal dot.
    main display (콤보박스 닫힌 상태) 에는 영향 없음."""
    def __init__(self, combo: QComboBox):
        super().__init__(combo)
        self._combo = combo
        self._color = QColor("#4E90E8")

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.row() == self._combo.currentIndex():
            r = option.rect
            cy = r.center().y()
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(self._color)
            painter.setPen(Qt.PenStyle.NoPen)
            d = 4.5  # 60% of ~7
            painter.drawEllipse(QRectF(float(r.left() + 8), cy - d / 2, d, d))
            painter.restore()


def _setup_dot_combo(combo: QComboBox):
    """popup view 만 dot delegate 적용. main display 의 icon 영역은 0."""
    combo.setIconSize(QSize(0, 0))
    delegate = _CurrentDotDelegate(combo)
    combo.view().setItemDelegate(delegate)




class _FilterResetButton(QPushButton):
    """필터 초기화 버튼 — 세그먼트 토글(_TransportButton, accent_border)과 프레임을
    완전 동일하게 paintEvent 에서 직접 그린다: 외곽선 full + fill α55 + roundedRect 반경 2.
    (세그먼트 토글은 호버에 따라 변하지 않으므로 호버 애니메이션 없음.)
    아이콘은 반복재생 토글(↻)과 동일 메트릭(원호 radius 4.8 / pen 1.3)의 새로고침 화살표.
    active=필터 걸림(빨강) / inactive=필터 없음(초록 #3ec870, 세그먼트 ON 과 동일)."""
    _GREEN = (62, 200, 112)   # #3ec870 — 세그먼트 토글 ON 과 동일
    _RED = (219, 67, 67)      # #db4343

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 전역 QPushButton QSS 간섭 차단 — 배경/테두리뿐 아니라 padding/min-height 까지
        # 0 으로 풀어야 setFixedSize 가 그대로 적용됨 (안 하면 전역 min-height+padding 이
        # 높이를 28px 로 올려 옆 콤보(26px)보다 아래로 더 길어짐. 직접 그림).
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            "padding: 0px; margin: 0px; min-width: 0px; min-height: 0px; }"
        )
        self._active = False
        self._hover_t = 0.0
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)

    def _animate_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(float(self._hover_t))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def _on_anim_tick(self, v):
        self._hover_t = float(v)
        self.update()

    def enterEvent(self, e):
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self.update()

    def paintEvent(self, e):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            t = self._hover_t
            if self._active:
                cr, cg, cb = self._GREEN
                fill = QColor(cr, cg, cb, int(55 + 26 * t))
                pen = QColor(cr, cg, cb)
                icon = QColor(cr, cg, cb)
            else:
                border_idle = QColor(COLORS["border_strong"])
                border_hot = QColor(COLORS["accent_hover"])
                text_idle = QColor(COLORS["text_secondary"])
                fill = QColor(COLORS["accent"])
                fill.setAlpha(int(24 * t))
                pen = QColor(
                    int(border_idle.red() * (1 - t) + border_hot.red() * t),
                    int(border_idle.green() * (1 - t) + border_hot.green() * t),
                    int(border_idle.blue() * (1 - t) + border_hot.blue() * t),
                    int(170 + 70 * t),
                )
                icon = QColor(
                    int(text_idle.red() * (1 - t) + border_hot.red() * t),
                    int(text_idle.green() * (1 - t) + border_hot.green() * t),
                    int(text_idle.blue() * (1 - t) + border_hot.blue() * t),
                )
            # active 는 초록 fill, inactive 는 기본 테두리/아이콘 색으로 직접 그림.
            p.setBrush(fill)
            p.setPen(QPen(pen, 1))
            p.drawRoundedRect(r, 2, 2)
            # 새로고침 화살표 — 반복재생 토글(↻)과 동일 메트릭
            cx, cy = r.center().x(), r.center().y()
            radius = 4.8
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(icon, 1.3))
            arc_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            # 시계방향 290도: 시작 60°(1시) → 스팬 -290°
            p.drawArc(arc_rect, 60 * 16, -290 * 16)
            # 시작점(1시 방향) 화살촉 — 시계방향 진행
            sx = cx + radius * math.cos(math.radians(60))
            sy = cy - radius * math.sin(math.radians(60))
            arrow = QPolygonF([
                QPointF(sx + 1.5, sy - 2.0),
                QPointF(sx - 2.5, sy - 0.5),
                QPointF(sx + 0.5, sy + 2.2),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(icon)
            p.drawPolygon(arrow)


class _PlaceholderSpinBox(QSpinBox):
    """기본값(=최소값 0)일 때 0 대신 빈칸 + dim placeholder 를 보여주는 스핀박스.
    값을 직접 입력할 때 0 을 지울 필요 없이 빈 칸에서 바로 타이핑 (사용자 요청).
    값이 0 이면 textFromValue 가 '' 를 반환 → lineEdit 이 비어 placeholder 노출.
    빈 입력 = 기본값(최소값) — 안 그러면 QSpinBox 가 빈 칸을 '미완성 입력'으로
    취급해 Enter/포커스아웃 시 직전 값으로 되돌려서, 값을 지워도 안 지워짐."""
    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.lineEdit().setPlaceholderText(placeholder)

    def textFromValue(self, v: int) -> str:
        return "" if v == self.minimum() else super().textFromValue(v)

    def valueFromText(self, text: str) -> int:
        if not text.strip():
            return self.minimum()
        return super().valueFromText(text)

    def validate(self, text: str, pos: int):
        if not text.strip():
            # 빈 칸을 유효 입력으로 — 즉시 기본값으로 해석돼 placeholder 복귀
            return (QValidator.State.Acceptable, text, pos)
        return super().validate(text, pos)


class _PlaceholderDoubleSpinBox(QDoubleSpinBox):
    """길이 필터 0.1초 단위 입력 — 표시 상태가 두 가지.

    · 편집 상태(`_editing`): 숫자만, 정수는 소수점 없이("2"), 's' 없음 →
      백스페이스가 's'/'.0'를 안 거치고 숫자에 바로 먹는다.
    · 확정 상태: 'X.Xs' (소수 한 자리 + 's'). Enter / 포커스아웃 시 전환.

    keyboardTracking=False 라 키 하나마다 재포맷되지 않고, 확정 시점에만 포맷한다.
    """
    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self.lineEdit().setPlaceholderText(placeholder)
        self.setKeyboardTracking(False)
        self._editing = False
        self.valueChanged.connect(lambda *_: self._render())
        self._render()

    # ── 표시 상태 ──
    def _render(self):
        """현재 value + _editing 상태에 맞게 표시 갱신. setSuffix 변경이 textFromValue
        재호출(재렌더)을 트리거한다 — 상태 전환 시 suffix 가 항상 바뀌어 안전."""
        v = self.value()
        suffix = "" if (v == self.minimum() or self._editing) else "s"
        if self.suffix() != suffix:
            self.setSuffix(suffix)

    def textFromValue(self, v: float) -> str:
        if v == self.minimum():
            return ""                       # 빈칸 → placeholder
        if self._editing and v == int(v):
            return str(int(v))              # 편집 중 정수는 '.0' 없이
        return super().textFromValue(v)     # 그 외엔 'X.X'

    # ── 입력 파싱 ──
    @staticmethod
    def _number_part(text: str) -> str:
        t = text.strip()
        if t[-1:] in ("s", "S"):
            t = t[:-1].strip()
        return t

    def valueFromText(self, text: str) -> float:
        if not self._number_part(text):
            return self.minimum()           # 숫자 비면(삭제 등) 빈 값(0)
        return super().valueFromText(text)

    def validate(self, text: str, pos: int):
        if not self._number_part(text):
            return (QValidator.State.Acceptable, text, pos)
        return super().validate(text, pos)

    # ── 프로그램적 설정 (reset/restore) ──
    def setValue(self, v: float):
        super().setValue(v)
        self._render()

    # ── 편집 ↔ 확정 전환 ──
    def _is_edit_key(self, e) -> bool:
        if e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            return True
        t = e.text()
        return bool(t) and (t.isdigit() or t in ".,")

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(e)        # 값 확정
            self._editing = False
            self._render()                  # 'X.Xs' 로 (엔터도 s 부착)
            return
        # 확정 표시 상태에서 다시 타이핑/삭제 → 입력 전에 편집형(숫자만)으로 전환
        if not self._editing and self._is_edit_key(e):
            self._editing = True
            self._render()
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        self._editing = True
        super().focusInEvent(e)
        self._render()                      # 정수면 '2', 소수면 '1.5' (s 없음)
        self.lineEdit().selectAll()

    def focusOutEvent(self, e):
        self._editing = False
        super().focusOutEvent(e)            # 값 확정
        self._render()                      # 'X.Xs'


def _make_pause_icon(size: int = 12, color: str = "#cfd6e3") -> QIcon:
    """수직 두 막대 일시정지 아이콘 — QPainter (Unicode ⏸ 폰트 의존 회피)."""
    s = size * 2
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    bar_w = s * 0.22
    bar_h = s * 0.62
    gap = s * 0.18
    y0 = (s - bar_h) / 2.0
    cx = s / 2.0
    p.drawRoundedRect(QRectF(cx - gap/2 - bar_w, y0, bar_w, bar_h), bar_w * 0.25, bar_w * 0.25)
    p.drawRoundedRect(QRectF(cx + gap/2, y0, bar_w, bar_h), bar_w * 0.25, bar_w * 0.25)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


def _make_play_icon(size: int = 12, color: str = "#cfd6e3") -> QIcon:
    """우향 삼각형 재생 아이콘 — QPainter."""
    s = size * 2
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    margin = s * 0.22
    tri = QPolygonF([
        QPointF(margin, margin),
        QPointF(s - margin, s / 2.0),
        QPointF(margin, s - margin),
    ])
    p.drawPolygon(tri)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


def _badge_style(color: str) -> str:
    # index-badge: danger 색 + 깜빡임 (blink_timer 에서 alpha 변화)
    return (
        f"QLabel {{ background: rgba(231,76,60,40); color: {color}; "
        f"border: 1px solid {color}; padding: 2px 9px; border-radius: 99px; "
        f"font-family: 'JetBrains Mono','Consolas','Malgun Gothic',monospace; "
        f"font-weight: 800; font-size: 10px; letter-spacing: 0.10em; }}"
    )


def _control_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("controlLabel")
    return label


class _BrandMark(QWidget):
    """YSG Audio Labs 단색 브랜드 마크 — SVG 렌더링으로 상단바에서 선명하게 유지."""
    def __init__(self, parent=None, size: int = 18):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._renderer = QSvgRenderer(str(ASSETS_DIR / "ysg_labs_mark_mono.svg"))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._renderer.isValid():
            pad = max(1.0, self._size * 0.04)
            self._renderer.render(p, QRectF(pad, pad, self.width() - pad * 2, self.height() - pad * 2))
        p.end()


class _ThemeToggleButton(QPushButton):
    """라이트/다크 토글 — 현재 테마의 반대 아이콘 (다크면 달, 라이트면 해).
    외곽선 + 호버 시 외곽선/배경 부드러운 alpha 보간 애니메이션.
    """
    toggled_theme = pyqtSignal(str)  # 새 테마 이름

    def __init__(self, parent=None, size: int = 22):
        super().__init__(parent)
        # 외곽선 표시 공간 확보
        self.setFixedSize(size + 14, size + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self._size = size
        self._hover_t = 0.0  # 0.0(idle) → 1.0(hover)

        # QVariantAnimation 으로 부드러운 보간 (160ms OutCubic)
        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)

        self._update_tooltip()
        self.clicked.connect(self._on_click)

    def _update_tooltip(self):
        nxt = "라이트" if current_theme() == "dark" else "다크"
        self.setToolTip(f"테마 전환 → {nxt} 모드")

    def _animate_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(float(self._hover_t))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def _on_anim_tick(self, v):
        self._hover_t = float(v)
        self.update()

    def enterEvent(self, e):
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    def _on_click(self):
        new_theme = "light" if current_theme() == "dark" else "dark"
        self.toggled_theme.emit(new_theme)
        self._update_tooltip()
        # 클릭 직후 살짝 강조 — 1.0 → 0.6 → 1.0 으로 짧게
        self._animate_to(1.0)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._hover_t

        # 외곽선: idle 시 border, hover 시 accent 로 부드럽게 전환
        border_idle = QColor(COLORS["border_strong"])
        border_hot = QColor(COLORS["accent_hover"])
        br = QColor(
            int(border_idle.red()   * (1 - t) + border_hot.red()   * t),
            int(border_idle.green() * (1 - t) + border_hot.green() * t),
            int(border_idle.blue()  * (1 - t) + border_hot.blue()  * t),
            int(140 + 115 * t),  # alpha 140 → 255
        )
        # 호버 배경 — accent 의 매우 옅은 톤
        bg_hot = QColor(COLORS["accent"])
        bg_hot.setAlpha(int(28 * t))

        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(bg_hot)
        p.setPen(QPen(br, 1.2))
        p.drawRoundedRect(rect, 6, 6)

        # 아이콘 색도 hover 시 accent 로 살짝 이동
        text_col = QColor(COLORS["text"])
        accent_col = QColor(COLORS["accent_hover"])
        icon_col = QColor(
            int(text_col.red()   * (1 - t * 0.5) + accent_col.red()   * t * 0.5),
            int(text_col.green() * (1 - t * 0.5) + accent_col.green() * t * 0.5),
            int(text_col.blue()  * (1 - t * 0.5) + accent_col.blue()  * t * 0.5),
        )

        cx = self.width() // 2
        cy = self.height() // 2
        r = self._size // 2 - 4

        if current_theme() == "dark":
            # 달: 채운 원 → 우상단 배경색으로 덮어 초승달
            p.setBrush(icon_col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setBrush(QColor(COLORS["bg_header"]))
            p.drawEllipse(QPointF(cx + r * 0.45, cy - r * 0.35), r * 0.85, r * 0.85)
        else:
            # 해: 중앙 원 + 8방향 광선
            p.setBrush(icon_col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), r * 0.55, r * 0.55)
            p.setPen(QPen(icon_col, 1.6))
            import math
            for i in range(8):
                ang = i * math.pi / 4
                x1 = cx + math.cos(ang) * (r * 0.78)
                y1 = cy + math.sin(ang) * (r * 0.78)
                x2 = cx + math.cos(ang) * (r * 1.05)
                y2 = cy + math.sin(ang) * (r * 1.05)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.end()


class _SettingsButton(QPushButton):
    """환경설정 톱니바퀴 — _ThemeToggleButton 과 동일 외곽선/hover 애니메이션,
    아이콘만 톱니바퀴 (ring + 8 spokes paint)."""

    def __init__(self, parent=None, size: int = 22):
        super().__init__(parent)
        self.setFixedSize(size + 14, size + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self._size = size
        self._hover_t = 0.0

        from PyQt6.QtCore import QVariantAnimation, QEasingCurve
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)

    def _animate_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(float(self._hover_t))
        self._anim.setEndValue(float(target))
        self._anim.start()

    def _on_anim_tick(self, v):
        self._hover_t = float(v)
        self.update()

    def enterEvent(self, e):
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._hover_t

        border_idle = QColor(COLORS["border_strong"])
        border_hot = QColor(COLORS["accent_hover"])
        br = QColor(
            int(border_idle.red()   * (1 - t) + border_hot.red()   * t),
            int(border_idle.green() * (1 - t) + border_hot.green() * t),
            int(border_idle.blue()  * (1 - t) + border_hot.blue()  * t),
            int(140 + 115 * t),
        )
        bg_hot = QColor(COLORS["accent"])
        bg_hot.setAlpha(int(28 * t))

        rect = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(bg_hot)
        p.setPen(QPen(br, 1.2))
        p.drawRoundedRect(rect, 6, 6)

        text_col = QColor(COLORS["text"])
        accent_col = QColor(COLORS["accent_hover"])
        icon_col = QColor(
            int(text_col.red()   * (1 - t * 0.5) + accent_col.red()   * t * 0.5),
            int(text_col.green() * (1 - t * 0.5) + accent_col.green() * t * 0.5),
            int(text_col.blue()  * (1 - t * 0.5) + accent_col.blue()  * t * 0.5),
        )

        cx = self.width() / 2
        cy = self.height() / 2
        r = self._size / 2 - 3  # 톱니 두 개 들어갈 공간 확보

        # 레퍼런스: 큰 톱니바퀴 좌상단 + 작은 톱니바퀴 우하단 살짝 겹침.
        # outline only (fill 없음) → hover 배경과 자연스럽게 어울림 + hole 처리 불필요.
        self._draw_gear(p, cx - r * 0.28, cy - r * 0.22, r * 0.62, icon_col, teeth=8)
        self._draw_gear(p, cx + r * 0.50, cy + r * 0.45, r * 0.40, icon_col, teeth=7)
        p.end()

    @staticmethod
    def _draw_gear(p, cx, cy, r, color, teeth: int = 8):
        """outline 톱니바퀴 — 톱니 polygon + 가운데 hole 원."""
        import math
        pen_w = max(1.1, r * 0.26)
        pen = QPen(color, pen_w)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        r_outer = r * 1.00
        r_inner = r * 0.74
        pts = []
        for i in range(teeth):
            c = (i * 2 * math.pi / teeth) - math.pi / 2
            gap_half = (math.pi / teeth) * 0.55  # inner base 폭
            top_half = (math.pi / teeth) * 0.32  # outer top 폭 (좁음 → 사다리꼴)
            pts.append((c - gap_half, r_inner))
            pts.append((c - top_half, r_outer))
            pts.append((c + top_half, r_outer))
            pts.append((c + gap_half, r_inner))
        qpts = [QPointF(cx + math.cos(a) * rr, cy + math.sin(a) * rr) for a, rr in pts]
        p.drawPolygon(QPolygonF(qpts))

        # 가운데 hole — outline 만, fill 없음
        p.drawEllipse(QPointF(cx, cy), r * 0.36, r * 0.36)


class _IndexSpinner(QWidget):
    def __init__(self, parent=None, size: int = 14):
        super().__init__(parent)
        self._size = size
        self._angle = 0
        self._paused = False
        self.setFixedSize(size, size)
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._paused = False
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self._paused = False
        self.setVisible(False)

    def show_paused(self):
        """분석 일시정지 상태 — 회전 멈추고 ‖ 아이콘 표시."""
        self._timer.stop()
        self._paused = True
        self.setVisible(True)
        self.update()

    def _tick(self):
        self._angle = (self._angle + 18) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._paused:
                # 일시정지 — 두 개의 둥근 세로 바
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(COLORS["text_secondary"]))
                bar_w = self._size * 0.22
                gap = self._size * 0.18
                cx = self._size / 2
                y = self._size * 0.18
                h = self._size * 0.64
                p.drawRoundedRect(QRectF(cx - gap / 2 - bar_w, y, bar_w, h), 1.5, 1.5)
                p.drawRoundedRect(QRectF(cx + gap / 2, y, bar_w, h), 1.5, 1.5)
                return
            rect = QRectF(2.0, 2.0, self._size - 4.0, self._size - 4.0)
            p.setPen(QPen(QColor(COLORS["accent_teal"]), 2))
            p.drawArc(rect, int(-self._angle * 16), int(270 * 16))
        finally:
            p.end()


class _GlobalLoader(QWidget):
    """헤더 펄스 로더. start(key, msg) 첫 호출 시 visible+timer 시작.
    stop(key) 모든 호출 finish 시 hide. ref-count 패턴이라 여러 워커 동시 진행
    중에도 한 위젯이 마지막 메시지 표시. 사용자에게 "freeze 가 아니라 진행
    중" 시각 피드백."""

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(6)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(
            f"color: #ff5a5a; font-size: 13px; " # 크기 약간 키움
            "background: transparent;"
        )
        self._dot_eff = QGraphicsOpacityEffect(self._dot)
        self._dot_eff.setOpacity(1.0)
        self._dot.setGraphicsEffect(self._dot_eff)
        h.addWidget(self._dot)
        self._msg = QLabel("")
        self.apply_theme_change()
        h.addWidget(self._msg)

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._opacity_value = 1.0
        self._direction = -1

        self._loaders: dict = {}  # key → msg, insertion order
        self.setVisible(False)

    def apply_theme_change(self):
        """테마 전환 시 메인 윈도우가 호출 — 인라인 텍스트 색 재적용."""
        # light(베이지 배경): 검정 / dark·grey(어두운 배경): 흰색
        text_color = "#000000" if current_theme() == "light" else "#ffffff"
        self._msg.setStyleSheet(
            f"color: {text_color}; font-size: 11px; "
            "background: transparent; letter-spacing: 0.02em; font-weight: 700;"
        )

    def start(self, key: str, msg: str):
        was_empty = not self._loaders
        # dict 의 insertion order 유지를 위해 기존 키 제거 후 재삽입
        self._loaders.pop(key, None)
        self._loaders[key] = msg
        self._msg.setText(msg)
        if was_empty:
            self.setVisible(True)
            self._opacity_value = 1.0
            self._direction = -1
            self._timer.start()

    def stop(self, key: str):
        self._loaders.pop(key, None)
        if not self._loaders:
            self._timer.stop()
            self.setVisible(False)
            self._dot_eff.setOpacity(1.0)
            self._msg.setText("")
        else:
            # 남은 loader 중 가장 마지막 메시지 표시
            self._msg.setText(next(reversed(self._loaders.values())))

    def _tick(self):
        self._opacity_value += self._direction * 0.1 # 더 빠르게
        if self._opacity_value <= 0.6: # 최저 투명도를 더 높여서 명확하게 보이게 함 (0.5 -> 0.6)
            self._opacity_value = 0.6
            self._direction = 1
        elif self._opacity_value >= 1.0:
            self._opacity_value = 1.0
            self._direction = -1
        self._dot_eff.setOpacity(self._opacity_value)


class _CenterLoaderOverlay(QWidget):
    """툴 중앙 모달 로딩 오버레이. 부모(central) 전체를 덮어 입력 차단 + 중앙에
    회전 링 + 메시지 표시. start(key,msg)/stop(key) ref-count. 트리 갱신처럼
    UI 가 실질적으로 사용 불가한 작업용."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._angle = 0
        self._msg = ""
        self._progress_text = ""
        self._t0 = 0.0
        self._loaders: dict = {}
        self._progress: dict = {}
        # 게이지(막대) — key별 진행률. 값: 0~100=확정 채움, -1=불확정(마퀴).
        # 키가 없으면(None) 막대 자체를 그리지 않음(스피너+텍스트만).
        self._gauge: dict = {}
        self._gauge_pct = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        # 30fps — 120fps 풀스크린 리페인트는 백그라운드 스캔과 UI 스레드 경합 시
        # 오히려 프레임이 밀려 더 버벅여 보임. 회전이 시간 기반이라 30fps 로 충분.
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, ev)

    def start(self, key: str, msg: str):
        was_empty = not self._loaders
        self._loaders.pop(key, None)
        self._loaders[key] = msg
        self._progress.pop(key, None)
        self._gauge.pop(key, None)
        self._msg = msg
        self._progress_text = ""
        self._gauge_pct = None
        if was_empty:
            self.raise_()
            self.setVisible(True)
            self._t0 = time.monotonic()
            self._timer.start()
        self.update()

    def set_progress(self, key: str, text: str, pct=None):
        """진행률 줄 갱신 — 메시지 아래 작은 글씨. 활성 로더 key 일 때만 표시.
        pct: None=막대 없음(텍스트만), 0~100=확정 게이지, -1=불확정(마퀴)."""
        if key not in self._loaders:
            return
        changed = False
        if self._progress.get(key) != text:
            self._progress[key] = text
            changed = True
        if pct is None:
            if key in self._gauge:
                del self._gauge[key]
                changed = True
        elif self._gauge.get(key) != pct:
            self._gauge[key] = pct
            changed = True
        if key == next(reversed(self._loaders)):
            self._progress_text = text
            self._gauge_pct = self._gauge.get(key)
            if changed:
                self.update()

    def stop(self, key: str):
        self._loaders.pop(key, None)
        self._progress.pop(key, None)
        self._gauge.pop(key, None)
        if not self._loaders:
            self._timer.stop()
            self.setVisible(False)
        else:
            top = next(reversed(self._loaders))
            self._msg = self._loaders[top]
            self._progress_text = self._progress.get(top, "")
            self._gauge_pct = self._gauge.get(top)
            self.update()

    def _tick(self):
        # 시간 기반 회전 — 프레임이 밀려도(UI 바쁨) 다음 프레임에서 실제 경과
        # 시간만큼 돌아가 있어 "멈춘 로더"로 보이지 않는다. (프레임 증분 방식은
        # 드랍된 프레임만큼 회전이 정지한 것처럼 보였음 — 사용자 보고)
        self._angle = ((time.monotonic() - self._t0) * 270.0) % 360
        # 패널 영역만 리페인트 — 풀스크린 딤 배경까지 매 프레임 다시 그리지 않음
        cx, cy = self.width() // 2, self.height() // 2
        self.update(QRect(cx - 170, cy - 95, 340, 190))

    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        e.accept()

    def mouseDoubleClickEvent(self, e):
        e.accept()

    def keyPressEvent(self, e):
        e.accept()

    def wheelEvent(self, e):
        e.accept()

    def paintEvent(self, e):
        # 로더 실행 중 매 프레임(8ms) 마다 발생하는 import 부하 제거
        from app.ui.theme import COLORS, current_theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 배경 딤드 처리 (테마에 따라 투명도 조절)
        is_dark = current_theme() == "dark"
        dim = QColor(0, 0, 0, 140) if is_dark else QColor(0, 0, 0, 80)
        p.fillRect(self.rect(), dim)

        # 게이지 막대가 있으면 그 자리(막대+텍스트)를 위해 패널을 조금 키운다.
        has_gauge = self._gauge_pct is not None
        panel_w = 300
        panel_h = 176 if has_gauge else 150
        cx, cy = self.width() // 2, self.height() // 2
        panel = QRect(cx - panel_w // 2, cy - panel_h // 2, panel_w, panel_h)

        # QSS 빌드 전/후 일관성을 위해 COLORS 딕셔너리 사용
        bg = QColor(COLORS.get("bg_elev", "#1a1d22" if is_dark else "#fdfbf3"))
        border = QColor(COLORS.get("accent", "#5dd0d8"))
        p.setBrush(bg)
        p.setPen(QPen(border, 1.5))
        p.drawRoundedRect(panel, 12, 12)

        ring_color = QColor(COLORS.get("accent", "#5dd0d8"))
        text_color = QColor(COLORS.get("text", "#ffffff" if is_dark else "#000000"))

        # 게이지 유무에 따라 링/메시지 y 위치를 패널 기준으로 배치.
        ring_cy = (panel.top() + 44) if has_gauge else (cy - 20)
        ring_size = 40
        p.save()
        p.translate(cx, ring_cy)
        p.rotate(self._angle)
        pen = QPen(ring_color, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(-ring_size // 2, -ring_size // 2, ring_size, ring_size, 0, 280 * 16)
        p.restore()

        p.setPen(text_color)
        font = p.font()
        font.setPointSize(11)
        font.setBold(True)
        p.setFont(font)
        msg_y = (panel.top() + 74) if has_gauge else (cy + 10)
        p.drawText(QRect(panel.x(), msg_y, panel_w, 24),
                   Qt.AlignmentFlag.AlignCenter, self._msg)

        # 게이지 막대 — 확정(채움) 또는 불확정(마퀴)
        if has_gauge:
            bar_w, bar_h = 220, 8
            bar_x, bar_y = cx - bar_w // 2, panel.top() + 108
            track = QColor(text_color); track.setAlpha(45)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(QRect(bar_x, bar_y, bar_w, bar_h), 4, 4)
            p.setBrush(QColor(ring_color))
            if self._gauge_pct >= 0:
                fw = int(bar_w * min(100, self._gauge_pct) / 100)
                if fw > 0:
                    p.drawRoundedRect(QRect(bar_x, bar_y, fw, bar_h), 4, 4)
            else:
                # 마퀴 — 시간 기반으로 짧은 세그먼트가 좌→우로 흐른다.
                seg = 70
                pos = int(((time.monotonic() - self._t0) * 190) % (bar_w + seg)) - seg
                left = bar_x + max(0, pos)
                right = bar_x + min(bar_w, pos + seg)
                if right > left:
                    p.drawRoundedRect(QRect(left, bar_y, right - left, bar_h), 4, 4)

        # 진행률 줄 (있을 때만) — 메시지(또는 막대) 아래 작은 글씨
        if self._progress_text:
            sub = QColor(text_color)
            sub.setAlpha(190)
            p.setPen(sub)
            font.setPointSize(9)
            font.setBold(False)
            p.setFont(font)
            prog_y = (panel.top() + 126) if has_gauge else (cy + 36)
            p.drawText(QRect(panel.x(), prog_y, panel_w, 20),
                       Qt.AlignmentFlag.AlignCenter, self._progress_text)
        p.end()


class _CenterToast(QWidget):
    """중앙 토스트 — _CenterLoaderOverlay 와 동일한 패널 디자인 재사용.
    스피너 대신 아이콘(+ / − / i) + 메시지. 입력 차단 안 함(투명), 일정 시간 후
    자동 소멸. 탭에 라이브러리 추가/삭제/중복 등 사용자 피드백용."""

    # (글리프, 아이콘 색) — 상태 의미색 고정: 추가=초록 / 삭제=빨강 / 실패·안내(!)=노랑.
    _KINDS = {
        "add":    ("+", "#2fb344"),
        "remove": ("−", "#db4343"),
        "info":   ("!", "#e0a82b"),
    }

    HOLD_MS = 500    # 완전 불투명 유지 시간 (바로 나타남)
    FADE_MS = 1000   # 이후 점점 희미해지며 사라지는 시간

    def __init__(self, parent=None):
        super().__init__(parent)
        # 입력 통과 — 토스트가 클릭/드래그를 막지 않음.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # 패널 밖(둥근 모서리 포함)은 투명 — dim 없이 패널만 떠 보이게.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._kind = "info"
        self._msg = ""
        self._opacity = 1.0
        self._show_t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps 페이드
        self._timer.timeout.connect(self._anim_tick)
        self.setVisible(False)
        if parent is not None:
            parent.installEventFilter(self)
            self.setGeometry(parent.rect())

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return super().eventFilter(obj, ev)

    def show_message(self, kind: str, msg: str):
        self._kind = kind if kind in self._KINDS else "info"
        self._msg = msg or ""
        self._opacity = 1.0
        self._show_t = time.monotonic()
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.setVisible(True)
        self.update()
        self._timer.start()

    def _anim_tick(self):
        elapsed = (time.monotonic() - self._show_t) * 1000.0
        if elapsed < self.HOLD_MS:
            return  # 완전 불투명 유지 — 재그리기 불필요
        if elapsed < self.HOLD_MS + self.FADE_MS:
            self._opacity = 1.0 - (elapsed - self.HOLD_MS) / self.FADE_MS
            self.update()
            return
        self._opacity = 0.0
        self._timer.stop()
        self.setVisible(False)

    def paintEvent(self, e):
        from app.ui.theme import COLORS, current_theme
        if not self._msg:
            return
        is_dark = current_theme() == "dark"
        glyph, accent_color = self._KINDS.get(self._kind, self._KINDS["info"])
        accent = QColor(accent_color)
        bg = QColor(COLORS.get("bg_elev", "#1a1d22" if is_dark else "#fdfbf3"))
        text_color = QColor(COLORS.get("text", "#ffffff" if is_dark else "#000000"))
        # 페이드 — 모든 색 alpha 에 현재 opacity 곱.
        a = max(0, min(255, int(255 * self._opacity)))
        accent.setAlpha(a); bg.setAlpha(a); text_color.setAlpha(a)
        white = QColor(255, 255, 255, a)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        panel_w, panel_h = 330, 134
        cx, cy = self.width() // 2, self.height() // 2
        panel = QRect(cx - panel_w // 2, cy - panel_h // 2, panel_w, panel_h)
        p.setBrush(bg)
        p.setPen(QPen(accent, 1.5))
        p.drawRoundedRect(panel, 12, 12)

        # 아이콘 원 + 글리프 — 크게 (메시지가 짧아 아이콘으로 상태를 한눈에).
        r = 58
        icy = panel.top() + 46
        p.setBrush(accent)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRect(cx - r // 2, icy - r // 2, r, r))
        p.setPen(white)
        gf = p.font(); gf.setPointSize(30); gf.setBold(True); p.setFont(gf)
        p.drawText(QRect(cx - r // 2, icy - r // 2, r, r),
                   Qt.AlignmentFlag.AlignCenter, glyph)

        # 메시지 (타겟 탭/문제 상황만 — 짧게)
        p.setPen(text_color)
        mf = p.font(); mf.setPointSize(11); mf.setBold(True); p.setFont(mf)
        top = icy + r // 2 + 8
        msg_rect = QRect(panel.x() + 14, top, panel_w - 28, panel.bottom() - top - 8)
        p.drawText(msg_rect,
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap, self._msg)
        p.end()


class _LibStatusFetchWorker(QObject):
    """LibraryStatusInline / LibraryStatusDialog 공용 — 30 roots × Path.exists,
    count_files_under, count_metadata_status_under 동기 호출을 워커로.
    NAS 경로면 Path.exists 만 root당 수십~수백 ms 이라 UI 스레드 동기는 1.5~3초 멈춤."""
    finished = pyqtSignal(object)  # list[dict]

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def run(self):
        data: list = []
        try:
            db = self.manager.open_db_for_search()
            roots = self.manager.get_roots()
            for root in roots:
                p = root["path"]
                prefix = os.path.normpath(p).rstrip("\\/") + os.sep
                try:
                    exists = Path(p).exists()
                except Exception:
                    exists = False
                try:
                    count = db.count_files_under(prefix)
                except Exception:
                    count = 0
                try:
                    meta = db.count_metadata_status_under(prefix)
                    pending = int(meta.get("pending", 0))
                    failed = int(meta.get("failed", 0))
                except Exception:
                    pending = failed = 0
                data.append({
                    "path": p,
                    "exists": exists,
                    "count": count,
                    "pending": pending,
                    "failed": failed,
                    "last_indexed_at": root.get("last_indexed_at"),
                })
        except Exception:
            logger.exception("LibStatus fetch 실패")
        self.finished.emit(data)


class LibraryStatusInline(QWidget):
    """항상 보이는 라이브러리 현황 — 라이브러리별 한 줄 요약."""
    detailRequested = pyqtSignal()

    # 인덱싱 상태별 글로우 색상 (class-level — legend 도 동일 색 참조).
    GLOW_OK      = "#58d97c"   # 초록 (완전 정상)
    GLOW_PENDING = "#ffb84d"   # 주황 (대기 존재)
    GLOW_FAIL    = "#ff5a5a"   # 빨강 (실패 존재)
    GLOW_MISSING = "#ff5a5a"   # 빨강 (경로 자체 없음)

    class _GlowDot(QWidget):
        """고해상도 pixmap 캐시로 그리는 작고 진한 상태광."""
        _pixmap_cache: dict = {}

        def __init__(self, color_hex: str, parent=None, size: int = 13,
                     hollow: bool = False, glow: bool = True):
            super().__init__(parent)
            self._color = QColor(color_hex)
            self._hollow = hollow
            self._glow = glow
            self.setFixedSize(size, size)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        def set_color(self, color_hex: str, hollow: bool = False):
            self._color = QColor(color_hex)
            self._hollow = hollow
            self.update()

        def paintEvent(self, _):
            with QPainter(self) as p:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                pm = self._pixmap(
                    self._color, min(self.width(), self.height()),
                    self.devicePixelRatioF(), self._hollow, self._glow
                )
                x = (self.width() - pm.deviceIndependentSize().width()) / 2.0
                y = (self.height() - pm.deviceIndependentSize().height()) / 2.0
                p.drawPixmap(QPointF(x, y), pm)

        @classmethod
        def _pixmap(cls, color: QColor, size: int, dpr: float,
                    hollow: bool, glow: bool) -> QPixmap:
            scale = max(4.0, float(dpr or 1.0))
            key = (color.name(), int(size), round(scale, 2), bool(hollow), bool(glow))
            cached = cls._pixmap_cache.get(key)
            if cached is not None:
                return cached

            physical = max(1, int(math.ceil(size * scale)))
            pm = QPixmap(physical, physical)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.GlobalColor.transparent)

            base = QColor(color)
            rect = QRectF(0.0, 0.0, float(size), float(size))
            center = rect.center()

            with QPainter(pm) as p:
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                p.setPen(Qt.PenStyle.NoPen)

                if glow:
                    halo = QRadialGradient(center, size * 0.50)
                    halo.setColorAt(0.00, QColor(base.red(), base.green(), base.blue(), 118))
                    halo.setColorAt(0.35, QColor(base.red(), base.green(), base.blue(), 88))
                    halo.setColorAt(0.68, QColor(base.red(), base.green(), base.blue(), 28))
                    halo.setColorAt(1.00, QColor(base.red(), base.green(), base.blue(), 0))
                    p.setBrush(QBrush(halo))
                    p.drawEllipse(rect.adjusted(0.3, 0.3, -0.3, -0.3))

                    bloom = QColor(base)
                    bloom.setAlpha(32)
                    bloom_d = size * 0.68
                    p.setBrush(bloom)
                    p.drawEllipse(QRectF(
                        center.x() - bloom_d / 2.0, center.y() - bloom_d / 2.0,
                        bloom_d, bloom_d
                    ))

                core_d = max(5.5, size * 0.47)
                core = QRectF(
                    center.x() - core_d / 2.0, center.y() - core_d / 2.0,
                    core_d, core_d
                )

                if hollow:
                    ring = QColor(base)
                    ring.setAlpha(235)
                    pen = QPen(ring, max(1.25, size * 0.095))
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(pen)
                    p.drawEllipse(core)
                else:
                    fill = QRadialGradient(
                        QPointF(core.left() + core.width() * 0.42,
                                core.top() + core.height() * 0.34),
                        core.width() * 0.72
                    )
                    fill.setColorAt(0.0, base.lighter(112))
                    fill.setColorAt(0.50, base)
                    fill.setColorAt(1.0, base.darker(118))
                    edge = QColor(base.darker(160))
                    edge.setAlpha(155)
                    p.setPen(QPen(edge, max(0.55, size * 0.045)))
                    p.setBrush(QBrush(fill))
                    p.drawEllipse(core)

            if len(cls._pixmap_cache) > 96:
                cls._pixmap_cache.clear()
            cls._pixmap_cache[key] = pm
            return pm

    @staticmethod
    def _make_legend_chip(color_hex: str, label: str, tooltip: str) -> QWidget:
        # DropShadowEffect 제거 — 30 roots + legend chip 3개에 CPU rasterize 가
        # 매 paint cycle 마다 실행되어 마우스 hover 부드러움 깎였음. 색만으로 충분.
        chip = QWidget()
        chip.setToolTip(tooltip)
        h = QHBoxLayout(chip)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        dot = LibraryStatusInline._GlowDot(color_hex, size=14)
        dot.setToolTip(tooltip)
        h.addWidget(dot)
        txt = QLabel(label)
        txt.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 10px; "
            f"background: transparent; padding: 0;"
        )
        h.addWidget(txt)
        return chip

    # 펼침 상태 영구 기억 (드라이브별)
    _collapsed_drives: set = set()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 0, 4, 0)
        v.setSpacing(2)

        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr_row.setSpacing(4)
        hdr = QLabel("라이브러리 현황")
        hdr.setObjectName("categoryLabel")
        hdr_row.addWidget(hdr, 0, Qt.AlignmentFlag.AlignVCenter)
        detail_btn = AnimButton("..")
        detail_btn.setFixedSize(26, 22)
        detail_btn.setToolTip("자세한 라이브러리 정보")
        detail_btn.clicked.connect(self.detailRequested)
        hdr_row.addWidget(detail_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        # 글로우 색 범례
        hdr_row.addSpacing(6)
        hdr_row.addWidget(self._make_legend_chip(self.GLOW_OK, "정상", "완전 정상 — 모든 메타데이터 추출 완료"), 0, Qt.AlignmentFlag.AlignVCenter)
        hdr_row.addWidget(self._make_legend_chip(self.GLOW_PENDING, "대기", "메타데이터 추출 대기 중인 파일 존재"), 0, Qt.AlignmentFlag.AlignVCenter)
        hdr_row.addWidget(self._make_legend_chip(self.GLOW_FAIL, "실패", "추출 실패 파일 존재 — 또는 경로 없음"), 0, Qt.AlignmentFlag.AlignVCenter)
        # 상태 정의 도움말 ? — 색상 범례 바로 옆. 검색 AND/OR/NOT ? 버튼과 동일.
        status_help = _HelpButton()
        status_help.setHelpText(
            "라이브러리 인덱싱 상태\n\n"
            "● 정상 — 모든 메타데이터 추출 완료\n"
            "● 대기 — 메타데이터 추출 대기 중인 파일 존재\n"
            "● 실패 — 추출 실패 파일 존재 (또는 경로 자체 없음)"
        )
        hdr_row.addWidget(status_help, 0, Qt.AlignmentFlag.AlignVCenter)
        hdr_row.addStretch()
        v.addLayout(hdr_row)

        # 스크롤 가능 영역 — 라이브러리 늘어나도 패널 폭주 방지
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setMaximumHeight(140)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")

        rows_container = QWidget()
        rows_container.setStyleSheet("background: transparent;")
        self._rows = QVBoxLayout(rows_container)
        self._rows.setSpacing(2)
        self._rows.setContentsMargins(0, 0, 4, 0)
        self._rows.addStretch()  # 행이 위로 정렬되도록
        self._scroll.setWidget(rows_container)

        v.addWidget(self._scroll, 1)

    def _status_color(self, exists: bool, pending: int, failed: int) -> str:
        if not exists:
            return self.GLOW_MISSING
        if failed > 0:
            return self.GLOW_FAIL
        if pending > 0:
            return self.GLOW_PENDING
        return self.GLOW_OK

    @staticmethod
    def _apply_glow(widget: QLabel, color_hex: str):
        # noop — 30 roots × DropShadowEffect 가 마우스 hover 시마다 CPU rasterize.
        # 호출처는 dot 색만으로 상태 구분 (GLOW_OK/PENDING/FAIL/MISSING 다 다른 색).
        pass

    def refresh(self, manager, db=None):
        """호환 wrapper. db arg 무시 — 데이터 fetch 는 _LibStatusFetchWorker 에서."""
        self.refresh_async(manager)

    def refresh_async(self, manager):
        """30 roots × Path.exists / count_files_under / count_metadata_status_under
        를 UI 스레드 동기 호출하면 부팅·인덱싱 완료마다 1.5~3초 멈춤.
        워커에서 모든 fetch 후 _apply_data 가 위젯 채움.
        진행 중이면 중복 호출 무시."""
        # 이전 thread C++ 객체가 deleteLater 로 삭제됐는데 Python 쪽 ref 가 남으면
        # isRunning() 호출 시 RuntimeError("wrapped C/C++ object deleted"). 그 예외가
        # 상위 _refresh_after_index_change 를 깨고 _add_library 의 후속 흐름
        # (스캔 dialog/IndexWorker) 도 같이 깨졌었음. ref-reset 슬롯 + try guard 둘 다.
        prev = getattr(self, "_fetch_thread", None)
        if prev is not None:
            try:
                if prev.isRunning():
                    return
            except RuntimeError:
                self._fetch_thread = None
        self._fetch_worker = _LibStatusFetchWorker(manager)
        self._fetch_thread = QThread()   # 부모 X — 다이얼로그 조기 닫힘 시 동반 파괴 방지
        _hold_bg_thread(self._fetch_thread, self._fetch_worker)
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.finished.connect(self._apply_data)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_worker.finished.connect(self._fetch_worker.deleteLater)
        self._fetch_thread.finished.connect(self._clear_fetch_refs)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.start()

    def _clear_fetch_refs(self):
        self._fetch_thread = None
        self._fetch_worker = None

    def _apply_data(self, data):
        # rows 클리어 (마지막 stretch 항목은 유지)
        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not data:
            lbl = QLabel("라이브러리 없음")
            lbl.setObjectName("libPillText")
            self._rows.insertWidget(0, lbl)
            return

        # 드라이브별 그룹화
        drive_groups: dict = {}
        for root_data in data:
            p = root_data["path"]
            if len(p) >= 2 and p[1] == ":":
                drv = p[:2].upper() + "\\"
            else:
                drv = ""
            drive_groups.setdefault(drv.lower(), []).append(root_data)

        insert_idx = 0
        for drv_low in sorted(drive_groups.keys()):
            group_data = drive_groups[drv_low]
            if drv_low:
                drv_disp = drv_low[0].upper() + drv_low[1:]
                section = self._build_drive_section(drv_low, drv_disp, group_data)
                self._rows.insertWidget(insert_idx, section)
                insert_idx += 1
            else:
                # 드라이브 추출 불가 (네트워크 경로 등) — 평탄 표시
                for root_data in group_data:
                    self._rows.insertWidget(insert_idx, self._build_root_row(root_data, indent=False))
                    insert_idx += 1

    def _build_drive_section(self, drv_low: str, drv_disp: str,
                             group_data: list) -> QWidget:
        """드라이브 그룹 박스 — 헤더 클릭 시 접기/펼치기."""
        section = QWidget()
        sv = QVBoxLayout(section)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(2)

        collapsed = drv_low in self._collapsed_drives
        arrow = "▶" if collapsed else "▼"
        header = QPushButton(f"{arrow}  {drv_disp}    ({len(group_data)}개)")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setStyleSheet(
            f"QPushButton {{"
            f"  color: {COLORS['text_caps']};"
            f"  font-size: 10px;"
            f"  font-weight: 800;"
            f"  letter-spacing: 0.08em;"
            f"  text-align: left;"
            f"  padding: 4px 4px 1px 0;"
            f"  background: transparent;"
            f"  border: none;"
            f"}}"
            f"QPushButton:hover {{ color: {COLORS['text']}; }}"
        )
        sv.addWidget(header)

        rows_wrap = QWidget()
        rwl = QVBoxLayout(rows_wrap)
        rwl.setContentsMargins(0, 0, 0, 0)
        rwl.setSpacing(2)
        for root_data in group_data:
            rwl.addWidget(self._build_root_row(root_data, indent=True))
        rows_wrap.setVisible(not collapsed)
        sv.addWidget(rows_wrap)

        def on_click():
            now_collapsed = rows_wrap.isVisible()  # 현재 보이면 → 접을 예정
            rows_wrap.setVisible(not now_collapsed)
            if now_collapsed:
                self._collapsed_drives.add(drv_low)
            else:
                self._collapsed_drives.discard(drv_low)
            new_arrow = "▶" if now_collapsed else "▼"
            header.setText(f"{new_arrow}  {drv_disp}    ({len(group_data)}개)")

        header.clicked.connect(on_click)
        return section

    def _build_root_row(self, root_data: dict, indent: bool = False) -> QWidget:
        p = root_data["path"]
        exists = bool(root_data.get("exists", False))
        count = int(root_data.get("count", 0))
        pending = int(root_data.get("pending", 0))
        failed = int(root_data.get("failed", 0))
        color_hex = self._status_color(exists, pending, failed)
        ts = root_data.get("last_indexed_at")
        scan_txt = (datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
                    if ts else "-")
        name = os.path.basename(p.rstrip("\\/")) or p

        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(12 if indent else 0, 0, 0, 0)
        row.setSpacing(6)
        dot = self._GlowDot(color_hex, size=16, hollow=not exists)
        dot.setObjectName("libPillDot")
        tip_parts = [p]
        if not exists:
            tip_parts.append("경로 없음")
        else:
            if failed:
                tip_parts.append(f"실패 {failed:,}")
            if pending:
                tip_parts.append(f"대기 {pending:,}")
            if not failed and not pending:
                tip_parts.append("정상")
        dot.setToolTip(" · ".join(tip_parts))
        row.addWidget(dot)
        name_lbl = QLabel(name)
        name_lbl.setObjectName("libPillText")
        name_lbl.setToolTip(p)
        row.addWidget(name_lbl)
        num_lbl = QLabel(f"{count:,}")
        num_lbl.setObjectName("libPillNum")
        row.addWidget(num_lbl)
        scan_lbl = QLabel(scan_txt)
        scan_lbl.setObjectName("libPillText")
        scan_lbl.setStyleSheet(f"color: {COLORS['text_muted']};")
        row.addWidget(scan_lbl)
        row.addStretch()
        return wrap


class _FetchIncompleteWorker(QObject):
    """FailedPendingDialog._reload_list 백그라운드 fetch.
    get_incomplete_metadata_under + count_incomplete_under 두 SQL 을 워커 스레드에서.
    메인 스레드 freeze 차단 (100만 audio_files prefix LIKE COUNT 가 풀스캔 가능)."""
    finished = pyqtSignal(list, int)  # rows, total

    def __init__(self, db_path: str, prefix: str,
                 include_pending: bool, include_failed: bool,
                 limit: int = 5000):
        super().__init__()
        self.db_path = db_path
        self.prefix = prefix
        self.include_pending = include_pending
        self.include_failed = include_failed
        self.limit = limit

    def run(self):
        try:
            db = Database(self.db_path, read_only=True)
            rows = db.get_incomplete_metadata_under(
                self.prefix,
                include_pending=self.include_pending,
                include_failed=self.include_failed,
                limit=self.limit,
            )
            total = db.count_incomplete_under(
                self.prefix,
                include_pending=self.include_pending,
                include_failed=self.include_failed,
            )
            self.finished.emit(rows, int(total))
        except Exception:
            logger.exception("_FetchIncompleteWorker 실패")
            self.finished.emit([], 0)


class _DbActionWorker(QObject):
    """FailedPendingDialog/LibraryStatusDialog 의 DB 제거 액션을 백그라운드에서 실행.
    UI 스레드 응답없음 방지 — delete_incomplete_under / delete_files_by_paths 는
    prefix LIKE + 큰 IN 절이라 수천 ~ 수십만 행 처리 가능 (수 초 ~ 수십 초).
    (재시도(reset) 는 requestScopedRetry → IndexWorker 번호표 경로로 분리됨.)

    kind: 'delete_paths' | 'delete_under'
    """
    finished = pyqtSignal(int, str)  # (영향받은 행 수, action kind)

    def __init__(self, db_path: str, kind: str, **kwargs):
        super().__init__()
        self.db_path = db_path
        self.kind = kind
        self.kwargs = kwargs

    def run(self):
        try:
            db = Database(self.db_path)
            self.orphan_ids: list = []   # 고아 중복 후보 — 완료 후 UI 팝업으로 복원 확인
            if self.kind == "delete_paths":
                n, self.orphan_ids = db.delete_files_by_paths(self.kwargs["paths"])
            elif self.kind == "delete_under":
                n, self.orphan_ids = db.delete_incomplete_under(
                    self.kwargs["prefix"],
                    include_pending=self.kwargs.get("include_pending", True),
                    include_failed=self.kwargs.get("include_failed", True),
                )
            else:
                n = 0
            self.finished.emit(int(n), self.kind)
        except Exception as e:
            logger.exception(f"_DbActionWorker.run({self.kind}) 실패")
            self.finished.emit(-1, f"{self.kind}: {e}")


def _prompt_orphan_dup_restore(parent, db_path: str, ids: list) -> int:
    """지우기 작업으로 '더 이상 중복이 아니게 된' 숨김 사운드 복원 확인 팝업 (공용).
    사용자 확정 정책: 모든 지우기 경로에서 자동 복원 금지 — 항상 묻고,
    [Yes] 일 때만 unhide. 반환: 복원된 수 (0 = 없음/거절/오류)."""
    if not ids:
        return 0
    # 기본 버튼 [No] — 엔터/무심코 클릭으로 대량 복원되는 사고 방지. 영향 범위를
    # 본문에 명시 (제외 목록에서 빠지고 검색·파일 브라우저에 다시 나온다는 점).
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("중복 아님 — 복원 확인")
    box.setText(
        f"방금 작업으로 더 이상 중복이 아니게 된 사운드 {len(ids):,}개가 있습니다.\n"
        "(살아있던 사본이 사라져, 중복 검수로 숨겨둔 사본만 남은 소리들)\n\n"
        "숨김을 해제해 검색에 다시 나오게 복원할까요?\n\n"
        f"[Yes] 복원 — {len(ids):,}개가 [제외 관리] 목록에서 빠지고 "
        "검색·파일 브라우저에 다시 나옵니다.\n"
        "[No] 유지 — 검색 제외를 그대로 두고 [제외 관리]에 "
        "[중복 아님 · 미복원] 로 표시합니다 (언제든 복원 가능)."
    )
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    ret = box.exec()
    if ret != QMessageBox.StandardButton.Yes:
        # 거절 기록 — 제외 관리 목록에 [중복 아님 · 미복원] 태그로 표시
        try:
            Database(db_path).mark_dup_orphans(list(ids))
        except Exception:
            logger.exception("고아 중복 표시 실패")
        return 0
    try:
        n = Database(db_path).unhide_by_ids(list(ids))
    except Exception:
        logger.exception("고아 중복 복원 실패")
        return 0
    if n:
        # 복원 결과 통지 — 예전엔 조용히 풀려서 '재스캔이 제외를 날렸다'로 보였음.
        try:
            remain = Database(db_path).count_hidden()
        except Exception:
            remain = None
        QMessageBox.information(
            parent, "복원 완료",
            f"중복 검수 제외 {n:,}개를 복원했습니다 — 검색·파일 브라우저에 다시 나옵니다."
            + (f"\n남은 검색 제외: {remain:,}개" if remain is not None else "")
        )
    return n


class _SearchGen:
    """검색 세대 카운터 — 메인 스레드가 올리고 워커가 읽음 (GIL 로 int 접근 원자적)."""
    __slots__ = ("value",)

    def __init__(self):
        self.value = 0


class _SearchWorker(QObject):
    """검색 쿼리 전용 상주 워커 — 메인(UI) 스레드 프리즈 방지.

    1~2글자 검색어는 FTS 미적용 → 12컬럼 LIKE 풀스캔이라 큰 라이브러리에서
    수 초 걸릴 수 있고, Phase2 인덱싱 중엔 읽기도 잠금 대기 가능. 둘 다
    메인 스레드에서 돌면 "응답 없음" — 그래서 워커로 분리.

    과거 검색 워커화 시도가 프리즈 악화/크래시로 실패한 원인을 피하는 안전장치 4종:
    1. DB 연결은 워커 스레드 안에서 생성 — 요청마다 read_only Database 를 새로 연다
       (sqlite 연결은 만든 스레드 전용. 메인 스레드의 self.db 공유 금지)
    2. 상주 스레드 1개 재사용 — 검색마다 QThread 생성/파괴 X
       (참조 놓쳐 GC 가 실행 중 QThread 소멸시키는 크래시 방지)
    3. 요청 세대(gen) 비교 — 요청이 밀리면 오래된 요청은 쿼리 실행 전에 스킵
       (느린 쿼리 중첩 → 결과 테이블 연속 재구성으로 프리즈 악화되는 패턴 방지)
    4. 결과는 시그널로 메인 스레드 전달, 수신측에서도 최신 gen 만 반영
       (먼저 던진 느린 검색이 나중에 도착해 최신 결과 덮어쓰는 역전 방지)
    """
    request = pyqtSignal(int, object)        # (gen, query kwargs) 메인 → 워커
    finished = pyqtSignal(int, object, str)  # (gen, rows, error) 워커 → 메인

    batchReady = pyqtSignal(int, object, bool, str)  # (gen, rows, done, error)

    def __init__(self, db_path: str, gen_holder: _SearchGen):
        super().__init__()
        self._db_path = db_path
        self._gen = gen_holder
        self.request.connect(self._run_query)

    # @pyqtSlot 필수 — 데코레이터 없는 일반 메서드 연결은 PyQt 가 프록시를
    # connect 시점 스레드(메인)에 만들어, moveToThread 후에도 emit 시 검색 쿼리가
    # UI 스레드에서 실행됨 (freeze.log 실측 — 2초+ 프리즈 다발 원인).
    # QueuedConnection 명시만으로도 부족 (큐잉돼도 메인 이벤트루프에서 실행됨, 실측).
    @pyqtSlot(int, object)
    def _run_query(self, gen: int, kwargs: object):
        if gen != self._gen.value:
            return  # 이미 새 검색이 예약됨 — 실행 자체를 스킵
        try:
            db = Database(self._db_path, read_only=True)
            t0 = time.perf_counter()
            total = 0

            def _emit_batch(rows):
                nonlocal total
                if gen != self._gen.value:
                    return
                total += len(rows)
                self.batchReady.emit(gen, rows, False, "")

            db.query(
                **kwargs,
                cancel_check=lambda: gen != self._gen.value,
                batch_callback=_emit_batch,
                batch_size=kwargs.get("batch_size", 120),
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if gen != self._gen.value:
                logger.info("[perf-search] cancelled gen=%d elapsed=%dms", gen, elapsed_ms)
                return
            logger.info("[perf-search] done gen=%d rows=%d elapsed=%dms",
                        gen, total, elapsed_ms)
            self.batchReady.emit(gen, [], True, "")
        except Exception as e:
            if gen != self._gen.value:
                return
            logger.exception("검색 워커 실패")
            self.batchReady.emit(gen, [], True, str(e))


class FailedPendingDialog(QDialog):
    """라이브러리 안 미완료 파일(pending=0 / failed=2) 상세 리스트.
    체크박스로 선택 후 [재시도(Phase2)] / [제거].
    재시도 클릭 시 parent (LibraryStatusDialog) 가 시그널을 다시 MainWindow 로 전달.

    제거는 _DbActionWorker 백그라운드. 재시도는 requestScopedRetry emit →
    MainWindow 가 범위 한정 reset+Phase2(번호표 스코프) 실행."""

    requestScopedRetry = pyqtSignal(object)  # 범위 한정 재시도 scope dict

    def __init__(self, manager, prefix: str, root_path: str, parent=None,
                 pending: Optional[int] = None, failed: Optional[int] = None):
        super().__init__(parent)
        self.manager = manager
        self.prefix = prefix
        self.root_path = root_path
        self.setWindowTitle(f"미완료 항목 — {os.path.basename(root_path.rstrip(chr(92)+'/')) or root_path}")
        self.resize(900, 520)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        # 헤더 — 카운트 요약. 호출처(현황표)가 표시값을 넘기면 그대로 사용 →
        # 여는 순간 메인 스레드 동기 count_metadata_status_under(100만행 ~1s) 회피.
        # 값이 없을 때만(다른 호출처 호환) 조회. 정확한 최신값은 _reload_list 가 갱신.
        if pending is None or failed is None:
            meta_status = manager.open_db_for_search().count_metadata_status_under(prefix)
            pending = int(meta_status.get("pending", 0))
            failed = int(meta_status.get("failed", 0))
        pending = int(pending)
        failed = int(failed)

        header = QLabel(
            f"<b>{root_path}</b><br>"
            f"<span style='color:#7adfc4;'>대기 {pending:,}</span> · "
            f"<span style='color:#ff7878;'>실패 {failed:,}</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(header)

        # 재분석 진행 배너 — 재시도 시 창을 닫지 않고 여기서 직접 진행을 보여줌.
        self.status_banner = QLabel("")
        self.status_banner.setVisible(False)
        self.status_banner.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 12px; font-weight: 700; padding: 4px 2px;"
        )
        v.addWidget(self.status_banner)

        # 필터 콤보 + 행 카운트
        bar1 = QHBoxLayout()
        bar1.setSpacing(8)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["전체 (대기+실패)", "대기만", "실패만"])
        self.filter_combo.currentIndexChanged.connect(self._reload_list)
        bar1.addWidget(self.filter_combo)

        select_all = QPushButton("전체 선택")
        select_all.setObjectName("pill")
        select_all.setFixedHeight(24)
        select_all.clicked.connect(lambda: self._set_all_checked(True))
        bar1.addWidget(select_all)
        clear_all = QPushButton("선택 해제")
        clear_all.setObjectName("pill")
        clear_all.setFixedHeight(24)
        clear_all.clicked.connect(lambda: self._set_all_checked(False))
        bar1.addWidget(clear_all)

        bar1.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        bar1.addWidget(self.count_lbl)
        v.addLayout(bar1)

        # 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "상태", "파일명", "경로", "사유"])
        h = self.table.horizontalHeader()
        # 컬럼별 적절한 default 너비를 미리 세팅 — 데이터 유무와 무관하게 동일하게 보이고
        # (실패/대기 0건이어도 폭이 튀지 않음), 사용자가 드래그로 조절 가능. 마지막 '사유'
        # 컬럼이 남은 공간을 채움. (load 시 resizeColumnsToContents 미사용)
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(True)
        self.table.setColumnWidth(0, 32)    # 체크박스
        self.table.setColumnWidth(1, 60)    # 상태
        self.table.setColumnWidth(2, 260)   # 파일명
        self.table.setColumnWidth(3, 380)   # 경로
        
        # 텍스트가 잘리지 않고 전체가 나오도록 설정 + 가로 스크롤 활성화
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setWordWrap(False) # 줄바꿈 대신 가로로 길게 표시
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        
        # Shift+휠 가로 스크롤 이벤트 필터 설치
        self.table.viewport().installEventFilter(self)
        v.addWidget(self.table, 1)

        # 1번째 하단 줄 — 선택 항목 처리
        bar2 = QHBoxLayout()
        refresh_btn = QPushButton("↻ 새로고침")
        refresh_btn.setObjectName("pill")
        refresh_btn.setFixedHeight(28)
        refresh_btn.setToolTip("Phase2 가 백그라운드에서 처리 중이면 잠시 후 다시 클릭하여 결과 확인")
        refresh_btn.clicked.connect(self._reload_list)
        bar2.addWidget(refresh_btn)
        bar2.addStretch(1)

        retry_btn = QPushButton("선택 항목 재시도 (Phase2)")
        retry_btn.setObjectName("primary")
        retry_btn.setFixedHeight(28)
        retry_btn.setToolTip(
            "선택한 항목들의 meta_extracted=0 으로 리셋 후 "
            "백그라운드에서 메타 추출만 즉시 실행 (Phase1 스캔 스킵)"
        )
        retry_btn.clicked.connect(self._retry_selected)
        bar2.addWidget(retry_btn)

        del_btn = QPushButton("선택 항목 제거")
        del_btn.setObjectName("cancelDanger")
        del_btn.setFixedHeight(28)
        del_btn.clicked.connect(self._delete_selected)
        bar2.addWidget(del_btn)
        v.addLayout(bar2)

        # 2번째 하단 줄 — 필터된 전체 (limit 5000 넘는 라이브러리용)
        bar3 = QHBoxLayout()
        info = QLabel("필터된 전체 (표시 limit 무시):")
        info.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        bar3.addWidget(info)
        bar3.addStretch(1)
        self.retry_all_btn = QPushButton("필터된 전체 재시도")
        self.retry_all_btn.setObjectName("primary")
        self.retry_all_btn.setFixedHeight(28)
        self.retry_all_btn.setToolTip(
            "현재 필터 조건(전체/대기만/실패만)에 해당하는 "
            "모든 항목을 SQL 한 번에 재시도 큐로 — 5000+ 도 처리"
        )
        self.retry_all_btn.clicked.connect(self._retry_all_filtered)
        bar3.addWidget(self.retry_all_btn)
        self.delete_all_btn = QPushButton("필터된 전체 제거")
        self.delete_all_btn.setObjectName("cancelDanger")
        self.delete_all_btn.setFixedHeight(28)
        self.delete_all_btn.setToolTip(
            "현재 필터 조건에 해당하는 모든 항목을 인덱스에서 제거"
        )
        self.delete_all_btn.clicked.connect(self._delete_all_filtered)
        bar3.addWidget(self.delete_all_btn)
        v.addLayout(bar3)

        # 3번째 하단 줄 — 닫기
        bar4 = QHBoxLayout()
        bar4.addStretch(1)
        close_btn = QPushButton("닫기")
        close_btn.setObjectName("pill")
        close_btn.setFixedHeight(28)
        close_btn.clicked.connect(self.reject)
        bar4.addWidget(close_btn)
        v.addLayout(bar4)

        self._reload_list()
        self._refresh_status_banner()
        # 재시도 진행 중 목록/배너 실시간 갱신 — 창을 닫지 않고 여기서 진행이 보이게.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(2000)
        self._live_timer.timeout.connect(self._on_live_tick)
        self._live_timer.start()
        self.finished.connect(self._live_timer.stop)

    def _on_live_tick(self):
        self._refresh_status_banner()
        # 인덱싱(재분석) 중일 때만 목록 자동 갱신 — 처리될수록 미완료 목록이 줄어든다.
        if not (self.manager.is_indexing() or getattr(self, "_retry_pending", False)):
            return
        # 사용자가 체크박스로 선택 중이면 표 재구성 보류 — 선택 풀림/스크롤 튐 방지.
        if self._selected_paths():
            return
        self._reload_list()

    def _refresh_status_banner(self):
        try:
            busy = self.manager.is_indexing()
        except Exception:
            busy = False
        if busy:
            self._retry_pending = False   # 인덱싱 실제 시작됨 → 이후엔 is_indexing 따라감
        show = busy or getattr(self, "_retry_pending", False)
        if show:
            prog = getattr(self.manager, "current_progress", None)
            msg = (getattr(prog, "message", "") or "").strip() if prog else ""
            self.status_banner.setText(
                f"● 메타 재분석 진행 중 — {msg}" if msg else "● 메타 재분석 진행 중..."
            )
            self.status_banner.setVisible(True)
        else:
            self.status_banner.setVisible(False)

    def eventFilter(self, obj, event):
        """Shift+휠 가로 스크롤 구현."""
        if obj == self.table.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta = event.angleDelta().y()
                if delta != 0:
                    hbar = self.table.horizontalScrollBar()
                    hbar.setValue(hbar.value() - int(delta * 1.5)) # 가로 스크롤 감도 조절
                    event.accept()
                    return True
        return super().eventFilter(obj, event)

    def _current_filter(self) -> tuple:
        """현재 콤보 → (include_pending, include_failed)."""
        idx = self.filter_combo.currentIndex()
        return (idx in (0, 1), idx in (0, 2))

    def _reload_list(self):
        """백그라운드 fetch — 메인 스레드 freeze 차단.
        SQL COUNT(*) prefix LIKE 가 100만 행이면 풀스캔 수 초 가능."""
        if getattr(self, "_fetch_running", False):
            return
        self._fetch_running = True
        include_pending, include_failed = self._current_filter()
        self.count_lbl.setText("불러오는 중...")

        worker = _FetchIncompleteWorker(
            str(self.manager.db_path), self.prefix,
            include_pending, include_failed, limit=5000,
        )
        thread = QThread()   # 부모 X — 다이얼로그 조기 닫힘 시 동반 파괴 방지
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_fetch_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._fetch_worker = worker
        self._fetch_thread = thread
        thread.start()

    def _on_fetch_done(self, rows: list, total: int):
        self._fetch_running = False

        # 재구성 전 체크 상태 보존 — 자동갱신이 사용자의 선택을 지우지 않게.
        checked_paths = set(self._selected_paths())

        # UI 업데이트 일시 중지 (깜빡임 및 성능 저하 방지)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(rows))
            
            # 에러 메시지 한글 변환 맵
            ERR_MAP = {
                "Unsupported format": "지원하지 않는 파일 형식",
                "FileNotFoundError": "파일을 찾을 수 없음",
                "PermissionError": "접근 권한 없음",
                "Timeout": "분석 시간 초과 (대용량/네트워크 지연)",
                "Ignored sidecar file": "무시된 사이드카 파일",
                "Unknown extraction error": "알 수 없는 추출 오류",
                "OSError": "OS 입출력 오류",
                "EOFError": "파일 끝(EOF) 도달 오류 (손상된 파일 가능성)",
                "ValueError": "데이터 형식 오류",
            }

            for r, row in enumerate(rows):
                chk = QTableWidgetItem()
                chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                chk.setCheckState(Qt.CheckState.Checked
                                  if row["file_path"] in checked_paths
                                  else Qt.CheckState.Unchecked)
                chk.setData(Qt.ItemDataRole.UserRole, row["file_path"])
                self.table.setItem(r, 0, chk)
                
                status = row["status"]
                stat_item = QTableWidgetItem("대기" if status == "pending" else "실패")
                color = "#7adfc4" if status == "pending" else "#ff7878"
                stat_item.setForeground(QBrush(QColor(color)))
                self.table.setItem(r, 1, stat_item)
                
                self.table.setItem(r, 2, QTableWidgetItem(row.get("file_name", "")))
                self.table.setItem(r, 3, QTableWidgetItem(row["file_path"]))
                
                # 사유 컬럼 추가 (한글화)
                err = row.get("meta_error")
                if not err:
                    reason_txt = "분석 대기 중" if status == "pending" else "알 수 없는 오류"
                else:
                    reason_txt = err
                    for eng, kor in ERR_MAP.items():
                        if eng in err:
                            reason_txt = kor
                            break
                
                reason_item = QTableWidgetItem(reason_txt)
                reason_item.setToolTip(err if err else reason_txt)
                self.table.setItem(r, 4, reason_item)
                
            # 컬럼 너비는 __init__ 의 default 를 유지 — resizeColumnsToContents 미사용
            # (빈/적은 데이터에서 폭 튐 방지 + 대량 데이터 프리즈 방지, 사용자 조절분 보존).
        finally:
            self.table.setUpdatesEnabled(True)

        if total > len(rows):
            cap_txt = (f"표시 {len(rows):,} / 필터 전체 {total:,} — "
                       f"limit 초과분은 [필터된 전체] 버튼으로 처리")
        else:
            cap_txt = f"표시 {len(rows):,} (전체 {total:,})"
        self.count_lbl.setText(cap_txt)

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None:
                item.setCheckState(state)

    def _selected_paths(self) -> list:
        paths = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is None:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                p = item.data(Qt.ItemDataRole.UserRole)
                if p:
                    paths.append(str(p))
        return paths

    def _path_for_row(self, row: int) -> str:
        item = self.table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _on_table_context_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        path = self._path_for_row(row)
        if not path:
            return
        menu = QMenu(self)
        reveal = menu.addAction("파일 위치 열기")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == reveal:
            self._reveal_path(path)

    def _reveal_path(self, path: str):
        # SHOpenFolderAndSelectItems 사용 (results_table._reveal_in_explorer) —
        # explorer 의 /select, 인자 파싱 변덕으로 파일이 선택 안 되던 사용자 보고 fix.
        try:
            _shell_reveal(path)
        except Exception:
            logger.exception(f"파일 위치 열기 실패: {path}")

    def _set_actions_enabled(self, enabled: bool):
        """진행 중 다이얼로그 전체 잠금 (모달 효과). 중복 워커 시작 방지."""
        self.setEnabled(enabled)

    def _start_action(self, kind: str, busy_msg: str, **kwargs):
        """공통 — _DbActionWorker QThread 시작.
        UI 잠금 + status 메시지 + finished 시 _on_action_done 으로 분기."""
        if getattr(self, "_action_running", False):
            return
        self._action_running = True
        self._set_actions_enabled(False)
        self.count_lbl.setText(busy_msg)
        worker = _DbActionWorker(str(self.manager.db_path), kind, **kwargs)
        thread = QThread()   # 부모 X — 다이얼로그 조기 닫힘 시 동반 파괴 방지
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_action_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # 인스턴스 참조 유지 — GC 로 QThread 소멸되면 크래시
        self._action_worker = worker
        self._action_thread = thread
        thread.start()

    def _on_action_done(self, n: int, kind: str):
        # 제거(delete_paths / delete_under) 전용 — 재시도는 requestScopedRetry 경로.
        self._action_running = False
        self._set_actions_enabled(True)
        self._reload_list()
        if n < 0:
            QMessageBox.warning(self, "오류", kind)
            return
        QMessageBox.information(self, "완료", f"{n:,}개 항목을 제거했습니다.")
        _prompt_orphan_dup_restore(
            self, str(self.manager.db_path),
            list(getattr(self._action_worker, "orphan_ids", None) or []))

    def _retry_selected(self):
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 필요", "재시도할 항목을 선택하세요.")
            return
        # 선택분만 번호표로 한정해 reset+Phase2. 창은 열어둔 채 배너+목록을 실시간 갱신.
        self.requestScopedRetry.emit({"paths": paths})
        self._retry_pending = True
        self._refresh_status_banner()

    def _delete_selected(self):
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 필요", "제거할 항목을 선택하세요.")
            return
        ret = QMessageBox.warning(
            self, "선택 항목 제거",
            f"선택한 {len(paths):,}개 항목을 인덱스에서 제거할까요?\n"
            "(실제 파일은 삭제되지 않습니다.)\n\n"
            "※ 제거분은 검색/목록에서만 빠집니다. 디스크에 파일이 남아 있으면\n"
            "   '전체 갱신' 시 다시 돌아옵니다 (영구 제외는 중복 숨김/블랙리스트).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._start_action(
            "delete_paths",
            f"제거 중... ({len(paths):,}개) — 백그라운드",
            paths=paths,
        )

    def _retry_all_filtered(self):
        """필터된 전체 = 현재 필터 조건에 맞는 모든 미완료 파일을 재시도.
        실패(2)는 SQL UPDATE 한 번으로 0 리셋(백그라운드), 그 후 정규 Phase2 가
        meta_extracted=0 전체를 스레드풀/잠금/취소와 함께 드레인. 표시 limit 무관.
        """
        include_pending, include_failed = self._current_filter()
        if not (include_pending or include_failed):
            return
        # 이 라이브러리(prefix) 의 필터 대상만 번호표로 한정 reset+Phase2. 창은 열어둠.
        self.requestScopedRetry.emit({
            "prefix": self.prefix,
            "include_pending": include_pending,
            "include_failed": include_failed,
        })
        self._retry_pending = True
        self._refresh_status_banner()

    def _delete_all_filtered(self):
        """필터된 전체를 SQL DELETE 한 번에 제거. 백그라운드 실행."""
        include_pending, include_failed = self._current_filter()
        if not (include_pending or include_failed):
            return
        kinds = []
        if include_pending: kinds.append("대기")
        if include_failed: kinds.append("실패")
        ret = QMessageBox.warning(
            self, "필터된 전체 제거",
            f"이 라이브러리의 {' + '.join(kinds)} 항목 전체를\n"
            f"인덱스에서 제거할까요?\n(실제 파일은 삭제되지 않습니다.)\n\n"
            "※ 제거분은 검색/목록에서만 빠집니다. 디스크에 파일이 남아 있으면\n"
            "   '전체 갱신' 시 다시 돌아옵니다 (영구 제외는 중복 숨김/블랙리스트).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._start_action(
            "delete_under",
            "필터된 전체 제거 중... (DB DELETE 백그라운드)",
            prefix=self.prefix,
            include_pending=include_pending,
            include_failed=include_failed,
        )


class LibraryStatusDialog(QDialog):
    """등록된 라이브러리별 경로/파일 수/마지막 스캔 시각."""

    requestScopedRetry = pyqtSignal(object)  # 범위 한정 재시도 scope dict (자식서 포워딩 포함)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("현재 라이브러리")
        self.resize(920, 360)
        v = QVBoxLayout(self)

        # 재시도(메타 재분석) 진행 상태 배너 — 창 안에서 바로 보이는 피드백.
        # (백그라운드 작업이 메인 진행바/중앙로더로만 표시돼 창에 가려지던 문제 해소)
        self.status_banner = QLabel("")
        self.status_banner.setVisible(False)
        self.status_banner.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 12px; font-weight: 700; "
            f"padding: 4px 2px;"
        )
        v.addWidget(self.status_banner)

        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["상태", "경로", "파일 수", "대기", "실패", "마지막 스캔"])
        h = table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c in (2, 3, 4, 5):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)

        # 즉시 표시될 로딩 placeholder. 30 roots 동기 fetch (NAS Path.exists +
        # count_metadata_status_under) 는 3초 멈춤을 만들었음. 워커로 옮김.
        table.setRowCount(1)
        table.setItem(0, 0, QTableWidgetItem("…"))
        table.setItem(0, 1, QTableWidgetItem("라이브러리 정보 로딩 중..."))
        for c in (2, 3, 4, 5):
            table.setItem(0, c, QTableWidgetItem(""))

        v.addWidget(table)
        # 더블클릭 → 미완료 파일 상세 다이얼로그
        table.cellDoubleClicked.connect(self._open_details_for_row)

        self.manager = manager
        self.table = table

        self._start_fetch()
        self._refresh_status_banner()

        # 창이 열려 있는 동안 주기적으로 카운트/진행상태 갱신 — 재시도하면 껐다 켜지
        # 않아도 대기/실패 수가 줄어드는 게 바로 보인다 (인라인 패널 5s 타이머와 동격).
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(2000)
        self._live_timer.timeout.connect(self._on_live_tick)
        self._live_timer.start()
        self.finished.connect(self._live_timer.stop)

        bar = QHBoxLayout()
        details_btn = QPushButton("선택 라이브러리 상세 보기…")
        details_btn.setToolTip(
            "선택한 라이브러리의 대기/실패 파일 리스트 — "
            "체크박스로 선택 후 재시도(Phase2)/제거. 더블클릭으로도 열림."
        )
        details_btn.clicked.connect(self._open_details_for_selected)
        bar.addWidget(details_btn)
        retry_btn = QPushButton("실패 전체 재시도")
        retry_btn.setToolTip(
            "선택한 라이브러리의 모든 실패 항목을 대기 상태로 리셋 후 "
            "백그라운드 Phase2 즉시 실행 (Phase1 스캔 스킵)"
        )
        retry_btn.clicked.connect(self._retry_failed_selected)
        bar.addWidget(retry_btn)
        delete_failed_btn = QPushButton("실패 항목 제거")
        delete_failed_btn.setToolTip("선택한 라이브러리의 실패 항목을 인덱스에서 제거")
        delete_failed_btn.clicked.connect(self._delete_failed_selected)
        bar.addWidget(delete_failed_btn)
        bar.addStretch(1)
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        v.addLayout(bar)

    def _start_fetch(self):
        """30 roots 상태 fetch (백그라운드). __init__ 최초 로드 + 액션 후 갱신 공용."""
        if getattr(self, "_fetch_running", False):
            return
        self._fetch_running = True
        self._fetch_worker = _LibStatusFetchWorker(self.manager)
        self._fetch_thread = QThread()   # 부모 X — 로딩 중 닫기 → 크래시 방지
        _hold_bg_thread(self._fetch_thread, self._fetch_worker)
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.finished.connect(self._populate_table)
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_worker.finished.connect(self._fetch_worker.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.start()

    def _on_live_tick(self):
        """주기 갱신 — 배너는 항상(가벼움), 카운트 재fetch 는 재분석 중일 때만.
        idle 에 매 2초 30-root 스캔을 돌리면 프리즈 유발 → busy/pending 일 때만 fetch."""
        self._refresh_status_banner()
        if self.manager.is_indexing() or getattr(self, "_retry_pending", False):
            self._start_fetch()

    def _refresh_status_banner(self):
        """인덱싱/메타 재분석 진행 중이면 창 상단에 표시.
        _retry_pending: 재시도 클릭 후 인덱싱이 실제 시작되기 전 짧은 공백을 메운다."""
        try:
            busy = self.manager.is_indexing()
        except Exception:
            busy = False
        if busy:
            self._retry_pending = False
        show = busy or getattr(self, "_retry_pending", False)
        if show:
            prog = getattr(self.manager, "current_progress", None)
            msg = (getattr(prog, "message", "") or "").strip() if prog else ""
            self.status_banner.setText(
                f"● 메타 재분석 진행 중 — {msg}" if msg else "● 메타 재분석 진행 중..."
            )
            self.status_banner.setVisible(True)
        else:
            self.status_banner.setVisible(False)

    def _populate_table(self, data):
        self._fetch_running = False
        self.table.setRowCount(len(data))
        if not data:
            return
        for r, root in enumerate(data):
            p = root["path"]
            exists = bool(root.get("exists", False))
            count = int(root.get("count", 0))
            pending = int(root.get("pending", 0))
            failed = int(root.get("failed", 0))
            ts = root.get("last_indexed_at")
            scan_txt = (datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
                        if ts else "-")
            self.table.setItem(r, 0, QTableWidgetItem("정상" if exists else "없음"))
            self.table.setItem(r, 1, QTableWidgetItem(p))
            self.table.setItem(r, 2, QTableWidgetItem(f"{count:,}"))
            self.table.setItem(r, 3, QTableWidgetItem(f"{pending:,}"))
            self.table.setItem(r, 4, QTableWidgetItem(f"{failed:,}"))
            self.table.setItem(r, 5, QTableWidgetItem(scan_txt))

    def _selected_prefix(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "선택 필요", "처리할 라이브러리를 선택하세요.")
            return None
        item = self.table.item(row, 1)
        if item is None:
            return None
        return os.path.normpath(item.text()).rstrip("\\/") + os.sep

    def _selected_root_path(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        return item.text() if item else None

    def _row_pending_failed(self, row: int) -> tuple:
        """현황표 행의 대기(3)/실패(4) 표시값 — 상세창 헤더에 넘겨 메인 COUNT 회피."""
        def _val(c):
            it = self.table.item(row, c)
            try:
                return int(it.text().replace(",", "").strip() or 0) if it else 0
            except ValueError:
                return 0
        return _val(3), _val(4)

    def _open_details_for_row(self, row: int, _col: int):
        item = self.table.item(row, 1)
        if item is None:
            return
        path = item.text()
        prefix = os.path.normpath(path).rstrip("\\/") + os.sep
        pending, failed = self._row_pending_failed(row)
        dlg = FailedPendingDialog(self.manager, prefix, path, self,
                                  pending=pending, failed=failed)
        dlg.requestScopedRetry.connect(self.requestScopedRetry)   # 메인으로 포워딩
        dlg.exec()

    def _open_details_for_selected(self):
        row = self.table.currentRow()
        path = self._selected_root_path()
        if not path:
            QMessageBox.information(self, "선택 필요", "라이브러리를 선택하세요.")
            return
        prefix = os.path.normpath(path).rstrip("\\/") + os.sep
        pending, failed = self._row_pending_failed(row)
        dlg = FailedPendingDialog(self.manager, prefix, path, self,
                                  pending=pending, failed=failed)
        dlg.requestScopedRetry.connect(self.requestScopedRetry)   # 메인으로 포워딩
        dlg.exec()

    def _selected_failed_count(self) -> int:
        """현재 행의 '실패' 컬럼(표시값) 재사용 — 메인 스레드 동기 COUNT(100만행 ~1s) 회피."""
        row = self.table.currentRow()
        if row < 0:
            return 0
        item = self.table.item(row, 4)  # 실패 컬럼
        if item is None:
            return 0
        try:
            return int(item.text().replace(",", "").strip() or 0)
        except ValueError:
            return 0

    def _retry_failed_selected(self):
        root_path = self._selected_root_path()
        if not root_path:
            QMessageBox.information(self, "선택 필요", "처리할 라이브러리를 선택하세요.")
            return
        prefix = os.path.normpath(root_path).rstrip("\\/") + os.sep
        failed = self._selected_failed_count()
        if failed <= 0:
            QMessageBox.information(self, "실패 없음", "선택한 라이브러리에 실패 항목이 없습니다.")
            return
        ret = QMessageBox.question(
            self, "실패 재시도",
            f"실패 항목 {failed:,}개를 백그라운드에서 재분석할까요?\n"
            f"(Phase1 스캔 스킵 · 잠금/취소/진행률 지원)"
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 이 라이브러리의 실패만 번호표로 한정 reset+Phase2 (메인 IndexWorker 경로).
        # 창은 닫지 않고 열어둔 채 배너+타이머로 진행을 보여줌.
        self.requestScopedRetry.emit({
            "prefix": prefix,
            "include_pending": False,
            "include_failed": True,
        })
        self._retry_pending = True
        self.status_banner.setText("● 메타 재분석 시작됨 — 잠시 후 실패 수가 줄어듭니다")
        self.status_banner.setVisible(True)

    def _delete_failed_selected(self):
        prefix = self._selected_prefix()
        if not prefix:
            return
        failed = self._selected_failed_count()
        if failed <= 0:
            QMessageBox.information(self, "실패 없음", "선택한 라이브러리에 실패 항목이 없습니다.")
            return
        ret = QMessageBox.warning(self, "실패 항목 제거", f"실패 항목 {failed:,}개를 인덱스에서 제거할까요?\n실제 파일은 삭제되지 않습니다.\n\n※ 제거분은 '전체 갱신' 시 디스크에 파일이 있으면 다시 돌아옵니다 (영구 제외는 중복 숨김/블랙리스트).", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        if getattr(self, "_action_running", False):
            return
        # 메인 스레드 동기 DELETE(프리즈 원인) 제거 — _DbActionWorker 백그라운드.
        self._action_running = True
        self.setEnabled(False)
        worker = _DbActionWorker(
            str(self.manager.db_path), "delete_under",
            prefix=prefix, include_pending=False, include_failed=True,
        )
        thread = QThread()   # 부모 X — 다이얼로그 조기 닫힘 시 동반 파괴 방지
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_delete_failed_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._action_worker = worker
        self._action_thread = thread
        thread.start()

    def _on_delete_failed_done(self, n: int, kind: str):
        self._action_running = False
        self.setEnabled(True)
        if n < 0:
            QMessageBox.warning(self, "오류", kind)
            return
        QMessageBox.information(self, "완료", f"실패 항목 {n:,}개를 인덱스에서 제거했습니다.")
        _prompt_orphan_dup_restore(
            self, str(self.manager.db_path),
            list(getattr(self._action_worker, "orphan_ids", None) or []))
        self._start_fetch()   # 현황 테이블 갱신


class IndexWorker(QObject):
    progress = pyqtSignal(object)
    phase1_done = pyqtSignal()
    finished = pyqtSignal(dict)

    def __init__(self, manager: LibraryManager, scan_path: Optional[str] = None,
                 force_rescan: bool = False, phase2_only: bool = False,
                 reset_scope_prefix: Optional[str] = None,
                 reset_scope_paths: Optional[list] = None,
                 reset_include_pending: bool = False,
                 reset_include_failed: bool = True):
        super().__init__()
        self.manager = manager
        self.scan_path = scan_path
        self.force_rescan = force_rescan
        self.phase2_only = phase2_only
        self.reset_scope_prefix = reset_scope_prefix
        self.reset_scope_paths = reset_scope_paths
        self.reset_include_pending = reset_include_pending
        self.reset_include_failed = reset_include_failed

    def run(self):
        # 인덱싱 동안 GIL 스위치 간격을 낮춰(기본 5ms→1ms) UI 스레드(중앙 로더)가
        # GIL 을 더 자주 받게 한다. Phase1 의 기존 인덱스 dict 로딩·배치 커밋 등
        # 파이썬 무거운 구간에서 로더가 통째로 얼어붙던 것을 완화. 완료 시 원복.
        import sys as _sys
        _prev_switch = _sys.getswitchinterval()
        _sys.setswitchinterval(0.001)
        try:
            result = self.manager.update_index(
                scan_path=self.scan_path,
                progress_cb=lambda p: self.progress.emit(p),
                phase1_done_cb=(None if self.phase2_only else lambda: self.phase1_done.emit()),
                force_rescan=self.force_rescan,
                phase2_only=self.phase2_only,
                reset_scope_prefix=self.reset_scope_prefix,
                reset_scope_paths=self.reset_scope_paths,
                reset_include_pending=self.reset_include_pending,
                reset_include_failed=self.reset_include_failed,
                run_phase2=self.phase2_only,
            )
        finally:
            _sys.setswitchinterval(_prev_switch)
        result["phase2_only"] = self.phase2_only
        self.finished.emit(result)


class BackgroundMetaWorker(QObject):
    progress = pyqtSignal(object)
    finished = pyqtSignal(dict)

    def __init__(self, manager: LibraryManager):
        super().__init__()
        self.manager = manager

    def run(self):
        # Phase2 는 32스레드로 도는 무거운 구간 — GIL 스위치 간격을 낮춰(기본 5ms→1ms)
        # UI 스레드가 GIL 을 더 자주 받게 한다. 인덱싱 중 GUI 렉 완화. 완료 시 원복.
        import sys as _sys
        _prev = _sys.getswitchinterval()
        _sys.setswitchinterval(0.001)
        try:
            result = self.manager.update_metadata_background(
                progress_cb=lambda p: self.progress.emit(p)
            )
        finally:
            _sys.setswitchinterval(_prev)
        self.finished.emit(result)


class DetectChangesWorker(QObject):
    """빠른 갱신 1단계 — DB write 없이 변경사항 감지."""
    progress = pyqtSignal(object)
    finished = pyqtSignal(dict)

    def __init__(self, manager: LibraryManager, scan_path: Optional[str] = None):
        super().__init__()
        self.manager = manager
        self.scan_path = scan_path

    def run(self):
        result = self.manager.detect_changes(
            scan_path=self.scan_path,
            progress_cb=lambda p: self.progress.emit(p),
        )
        self.finished.emit(result)


class ChangeReviewDialog(QDialog):
    """감지된 변경사항 요약 다이얼로그.
    추가/수정/삭제 카운트 + 샘플 경로 + [진행/취소] 버튼."""

    def __init__(self, parent, result: dict, scope_label: str):
        super().__init__(parent)
        self.setWindowTitle("변경사항 검토")
        self.setMinimumWidth(560)

        added = int(result.get("added", 0))
        updated = int(result.get("updated", 0))
        deleted = int(result.get("deleted", 0))
        scanned = int(result.get("scanned", 0))
        elapsed = float(result.get("elapsed", 0.0))
        total = added + updated + deleted

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        title = QLabel(f"[{scope_label}] 변경 감지 결과")
        title.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 13px; font-weight: 700;"
        )
        v.addWidget(title)

        summary = QLabel(
            f"스캔 {scanned:,}개 · 소요 {elapsed:.1f}s\n\n"
            f"추가  +{added:,}\n"
            f"수정  ~{updated:,}\n"
            f"삭제  −{deleted:,}"
        )
        summary.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 12px; "
            "font-family: 'JetBrains Mono', 'Consolas', monospace;"
        )
        v.addWidget(summary)

        # 샘플 (있을 때만)
        def _sample_block(label: str, paths: list, color: str):
            if not paths:
                return
            lbl = QLabel(f"{label} 예시 ({len(paths)}개):")
            lbl.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 10px; "
                "font-weight: 600; letter-spacing: 0.06em;"
            )
            v.addWidget(lbl)
            txt = QLabel("\n".join(paths[:10]))
            txt.setStyleSheet(
                f"color: {color}; font-size: 10px; "
                "font-family: 'JetBrains Mono', 'Consolas', monospace; "
                "background: rgba(255,255,255,0.03); padding: 6px 10px; "
                "border-radius: 4px;"
            )
            txt.setWordWrap(False)
            v.addWidget(txt)

        _sample_block("추가", result.get("added_sample", []), "#7adfc4")
        _sample_block("수정", result.get("updated_sample", []), "#ffd166")
        _sample_block("삭제", result.get("deleted_sample", []), "#ff7878")

        # 버튼
        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("취소")
        cancel_btn.setObjectName("pill")
        cancel_btn.setFixedHeight(28)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)

        ok_btn = QPushButton(
            "갱신 진행" if total > 0 else "확인"
        )
        ok_btn.setObjectName("primary")
        ok_btn.setFixedHeight(28)
        ok_btn.setDefault(True)
        if total > 0:
            ok_btn.clicked.connect(self.accept)
        else:
            ok_btn.clicked.connect(self.reject)  # 변경 없으면 진행할 게 없음
        btns.addWidget(ok_btn)
        v.addLayout(btns)


class TreeReloadWorker(QObject):
    finished = pyqtSignal(object, object, object, int, object, int)
    # folders, roots, folder_counts, total, incomplete_counts, total_incomplete

    def __init__(self, manager: LibraryManager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            db = self.manager.open_db_for_search()
            folders = db.get_folders()
            roots_data = self.manager.get_roots()
            roots = [r["path"] for r in roots_data]
            folder_counts = db.get_all_folder_counts()
            total = db.count_files()
            incomplete_counts = db.get_all_folder_incomplete_counts()
            total_incomplete = db.count_incomplete_total()
            self.finished.emit(folders, roots, folder_counts, total,
                               incomplete_counts, total_incomplete)
        except Exception:
            logger.exception("트리 갱신 실패")
            self.finished.emit([], [], {}, 0, {}, 0)


class TreeCountsWorker(QObject):
    """Phase1 진행 중 가벼운 카운트 전용 워커. get_folders()/get_roots() 안 호출 →
    audio_files 풀스캔 2번 (folder_counts + incomplete_counts) + count 2번만."""
    finished = pyqtSignal(object, int, object, int)
    # folder_counts, total, incomplete_counts, total_incomplete

    def __init__(self, manager: LibraryManager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            db = self.manager.open_db_for_search()
            folder_counts = db.get_all_folder_counts()
            total = db.count_files()
            incomplete_counts = db.get_all_folder_incomplete_counts()
            total_incomplete = db.count_incomplete_total()
            self.finished.emit(folder_counts, total, incomplete_counts, total_incomplete)
        except Exception:
            logger.exception("트리 카운트 워커 실패")
            self.finished.emit({}, 0, {}, 0)


class IncompleteCountsWorker(QObject):
    finished = pyqtSignal(object, int)

    def __init__(self, manager: LibraryManager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            db = self.manager.open_db_for_search()
            counts = db.get_all_folder_incomplete_counts()
            total = db.count_incomplete_total()
            self.finished.emit(counts, total)
        except Exception:
            logger.exception("미완료 카운트 갱신 실패")
            self.finished.emit({}, 0)


class RemoveLibraryWorker(QObject):
    """라이브러리 삭제 백그라운드 워커. UI 스레드 블로킹 차단.
    다중 paths 한 워커에서 순차 처리 — 매 path 당 worker 띄우면 thread churn."""
    finished = pyqtSignal(int, str)  # deleted_count (-1 = error), message

    def __init__(self, manager: LibraryManager, paths):
        super().__init__()
        self.manager = manager
        self.paths = [paths] if isinstance(paths, str) else list(paths)

    def run(self):
        try:
            total = 0
            self.orphan_ids: list = []   # 고아 중복 후보 — 완료 후 UI 팝업으로 복원 확인
            for p in self.paths:
                # cancel_prefix 먼저 등록 — 백그라운드 Phase2 워커가 즉시 해당 prefix
                # 파일을 skip. remove_root 의 DB 삭제 전에 호출해야 race 로 ghost
                # row 부활(extract→upsert) 막을 수 있음.
                self.manager.cancel_prefix(p)
                n_removed, oids = self.manager.remove_root(p)
                total += n_removed
                self.orphan_ids.extend(oids)
            n = len(self.paths)
            # soft 제거(숨김) — '삭제' 표현은 복구 불가로 오해됨. 재추가 시 복원 안내.
            if n == 1:
                msg = f"라이브러리 제거됨 — 항목 {total:,}개 (다시 추가하면 복원)"
            else:
                msg = f"라이브러리 {n:,}개 제거됨 — 항목 {total:,}개 (다시 추가하면 복원)"
            self.finished.emit(total, msg)
        except Exception as e:
            logger.exception("라이브러리 제거 실패")
            self.finished.emit(-1, f"제거 오류: {e}")


class PurgeLibraryWorker(QObject):
    """라이브러리 완전 제거 워커 — RemoveLibraryWorker 의 물리삭제판.
    순서 고정: ① 파형 캐시 키 수집 (행 삭제 '전' — 키가 md5(경로|크기|mtime|...)
    라 행이 사라지면 재계산 불가) ② DB purge (행+FTS 2종+블랙리스트+고아 중복
    숨김 복원) ③ 캐시 파일 삭제. finished(int, str) 시그니처는
    RemoveLibraryWorker 와 동일 — _on_remove_done 재사용."""
    finished = pyqtSignal(int, str)  # deleted_count (-1 = error), message

    def __init__(self, manager: LibraryManager, paths):
        super().__init__()
        self.manager = manager
        self.paths = [paths] if isinstance(paths, str) else list(paths)

    def run(self):
        from app.ui.player_widget import PEAKS_CACHE_DIR, _peaks_cache_path
        try:
            total = 0
            cache_deleted = 0
            self.orphan_ids: list = []   # 고아 중복 후보 — 완료 후 UI 팝업으로 복원 확인
            try:
                cache_names = {f.name for f in PEAKS_CACHE_DIR.iterdir()
                               if f.suffix == ".bin"}
            except OSError:
                cache_names = set()
            db_ro = self.manager.open_db_for_search()
            for p in self.paths:
                # cancel_prefix 먼저 — 백그라운드 Phase2 의 ghost row 부활 방지
                # (RemoveLibraryWorker 와 동일).
                self.manager.cancel_prefix(p)
                prefix = _lib_prefix(p)
                # ① 캐시 키 수집 — DB 행의 (크기, mtime) 으로 stat 없이 재계산.
                #    Phase1-only 행(size/mtime NULL)은 캐시가 없어 스킵. 키 상수가
                #    바뀐 옛 버전 캐시는 못 찾음 — 고아로 남되 무해 (best effort).
                cache_hits = []
                if cache_names:
                    for batch in db_ro.iter_path_size_mtime_under(prefix):
                        for fp, size, mtime in batch:
                            if size is None or mtime is None:
                                continue
                            cf = _peaks_cache_path(fp, stat_hint=(size, mtime),
                                                   allow_stat=False)
                            if cf is not None and cf.name in cache_names:
                                cache_hits.append(cf)
                # ② DB purge (행+FTS+블랙리스트, 단일 트랜잭션 — 고아 후보 수집)
                stats = self.manager.purge_root(p)
                total += stats["deleted"]
                self.orphan_ids.extend(stats.get("orphan_ids", []))
                # ③ 캐시 파일 삭제
                for cf in cache_hits:
                    try:
                        cf.unlink()
                        cache_deleted += 1
                    except OSError:
                        pass
            msg = (f"라이브러리 완전 제거됨 — 인덱스 {total:,}개, "
                   f"파형 캐시 {cache_deleted:,}개 삭제")
            self.finished.emit(total, msg)
        except Exception as e:
            logger.exception("라이브러리 완전 제거 실패")
            self.finished.emit(-1, f"완전 제거 오류: {e}")


class _FtsRepairWorker(QObject):
    """FTS 표적 수리 워커 — 어긋난 행만 등록/정리 (전체 재빌드 대비 초 단위).
    인덱싱과의 동시 실행은 manager.repair_fts 가 IndexBusy 락으로 차단."""
    progress = pyqtSignal(int, int)   # done, total (total=0 = 점검 중)
    finished = pyqtSignal(bool, str)

    def __init__(self, manager: LibraryManager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            r = self.manager.repair_fts(progress_cb=self.progress.emit)
            if not r.get("success"):
                self.finished.emit(False, r.get("message", "색인 수리 실패"))
                return
            if r["consistent"]:
                msg = (f"검색 색인 수리 완료 — 복구 {r['inserted']:,}개, "
                       f"유령 정리 {r['deleted']:,}개")
            else:
                msg = ("색인 수리 후에도 불일치 남음 — "
                       "[검색 인덱스 복구](전체 재빌드)를 사용하세요")
            self.finished.emit(True, msg)
        except Exception as e:
            logger.exception("FTS 표적 수리 실패")
            self.finished.emit(False, f"색인 수리 오류: {e}")


class FTSRebuildWorker(QObject):
    """search_fts 전체 재빌드 워커 (백그라운드)."""
    progress = pyqtSignal(int, int)   # done, total
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self._cancel = Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            db = Database(self.db_path)
            # 증분 백필 — 누락분만 채워 전체 재빌드보다 빠르고 인덱싱 부담 적음.
            n = db.backfill_missing_fts(
                progress_cb=lambda d, t: self.progress.emit(d, t),
                cancel_event=self._cancel,
            )
            if self._cancel.is_set():
                self.finished.emit(False, "복구 취소됨")
            else:
                self.finished.emit(True, f"검색 인덱스 복구 완료 (+{n:,}건)")
        except Exception as e:
            logger.exception("FTS 백필 실패")
            self.finished.emit(False, f"복구 오류: {e}")


class _DupRepairWorker(QObject):
    """중복 검수 일괄 수리 워커 — 인덱스 삭제가 아니라 검색 숨김(hidden=1).
    인덱스/FTS 가 그대로라 빠르고(플래그 UPDATE), 재스캔이 다시 등록할 일도,
    2단계 메타 재분석도 없음. 수십만 건도 수 초 수준이지만 UI 프리즈 방지를
    위해 백그라운드 + 진행률 유지."""
    progress = pyqtSignal(int, int)   # done, total
    finished = pyqtSignal(int, str)   # hidden_count, error("" = 성공)

    def __init__(self, db_path: str, paths: list):
        super().__init__()
        self._db_path = db_path
        self._paths = paths

    def run(self):
        try:
            db = Database(self._db_path)
            n = db.hide_files(self._paths, progress_cb=self.progress.emit)
            self.finished.emit(n, "")
        except Exception as e:
            logger.exception("중복 수리(숨김) 실패")
            self.finished.emit(0, str(e))


class _DupScanWorker(QObject):
    """중복 검수 스캔 워커 — find_duplicates 가 실DB(113만 행) 수 초 걸려
    UI 스레드 실행 시 프리즈 (사용자 보고). read-only 연결이라 인덱싱과 경합 X."""
    progress = pyqtSignal(int, int)     # 스캔한 행 수, 전체 행 수
    finished = pyqtSignal(object, str)  # groups(list), error("" = 성공)

    def __init__(self, db_path: str):
        super().__init__()
        self._db_path = db_path

    def run(self):
        try:
            db = Database(self._db_path, read_only=True)
            groups = db.find_duplicates(progress_cb=self.progress.emit)
            self.finished.emit(groups, "")
        except Exception as e:
            logger.exception("중복 검수 스캔 실패")
            self.finished.emit([], str(e))


class _HiddenCountWorker(QObject):
    """숨김(제외) 개수 카운트 워커 — count_hidden 이 실DB 1초+ 라
    다이얼로그 열 때 UI 스레드 호출이 프리즈 유발."""
    finished = pyqtSignal(int, int)  # count, gen(요청 세대 — 최신만 반영)

    def __init__(self, db_path: str, gen: int):
        super().__init__()
        self._db_path = db_path
        self._gen = gen

    def run(self):
        try:
            n = Database(self._db_path, read_only=True).count_hidden()
        except Exception:
            logger.exception("숨김 카운트 실패")
            n = 0
        self.finished.emit(n, self._gen)


class _HighlightListDelegate(QStyledItemDelegate):
    """제외 목록 delegate — 검색어 일치 부분 강조(굵게+배경). QListWidgetItem 은
    rich text 미지원이라 QTextDocument 로 직접 렌더. 체크박스/선택 배경은
    기본 스타일(CE_ItemViewItem, text 비움)로 먼저 그리고 글자만 얹는다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.term = ""

    def _html(self, text: str, base_color: str) -> str:
        t = self.term
        if not t:
            return f'<span style="color:{base_color}">{_html_escape(text)}</span>'
        out = []
        low = text.lower()
        tl = t.lower()
        i = 0
        while True:
            j = low.find(tl, i)
            if j < 0:
                out.append(_html_escape(text[i:]))
                break
            out.append(_html_escape(text[i:j]))
            # 배경 = 액센트 원색 + 어두운 글자 — 다크/라이트 어느 테마에서도 또렷.
            # Qt 리치텍스트는 background 축약형 미지원 → background-color 사용.
            out.append(
                f'<span style="background-color:{COLORS["accent"]}; color:#101317; '
                f'font-weight:700">{_html_escape(text[j:j + len(t)])}</span>')
            i = j + len(t)
        return f'<span style="color:{base_color}">{"".join(out)}</span>'

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        base = fg.color().name() if isinstance(fg, QBrush) else COLORS["text"]
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        doc.setHtml(self._html(text, base))
        painter.save()
        y = rect.y() + max(0, (rect.height() - int(doc.size().height())) // 2)
        painter.translate(rect.x(), y)
        doc.drawContents(painter, QRectF(0, 0, rect.width(), rect.height()))
        painter.restore()


class SettingsDialog(QDialog):
    """환경설정 다이얼로그 (탭 방식).

    open_settings 단축키(Ctrl+P 등) 가 양방향으로 동작하도록 자체 QShortcut 등록 —
    modal exec 상태에서도 dialog 안에서 같은 단축키 누르면 reject() (저장 안 하고 닫힘).
    """
    def __init__(self, parent=None, config: dict = None):
        super().__init__(parent)
        self.setWindowTitle("환경설정")
        self.resize(500, 500)
        self._config = config or {}
        self._shortcuts = self._config.get("shortcuts", {})

        # 양방향 토글 — 다이얼로그 열린 상태에서 단축키 누르면 닫힘.
        # modal 안에서는 메인 윈도우의 QShortcut 이 안 잡히므로 dialog 자체에 등록.
        open_seq = self._shortcuts.get("open_settings", "Ctrl+P")
        self._close_shortcut = QShortcut(QKeySequence(open_seq), self)
        self._close_shortcut.activated.connect(self.reject)

        v = QVBoxLayout(self)
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)
        
        self._setup_general_tab()
        self._setup_theme_tab()
        self._setup_playback_tab()
        self._setup_indexing_tab()
        self._setup_duplicate_tab()
        self._setup_shortcuts_tab()
        
        btns = QHBoxLayout()
        btns.addStretch()
        reset_btn = QPushButton("기본값으로 초기화")
        reset_btn.clicked.connect(self._on_reset)
        btns.addWidget(reset_btn)
        
        ok_btn = QPushButton("저장")
        ok_btn.setObjectName("primary")
        ok_btn.setFixedHeight(28)
        ok_btn.clicked.connect(self.accept)
        btns.addWidget(ok_btn)
        
        cancel_btn = QPushButton("취소")
        cancel_btn.setObjectName("pill")
        cancel_btn.setFixedHeight(28)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        v.addLayout(btns)

    def _setup_general_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 검색 최대 노출
        self.search_limit = QSpinBox()
        self.search_limit.setRange(100, 5000)
        self.search_limit.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons) # 버튼 제거 (직접 입력)
        self.search_limit.setValue(self._config.get("search_limit", 500))
        layout.addRow("검색 최대 노출 개수:", self.search_limit)
        
        hint = QLabel("(추천: 1000개 이하 / 최대: 5000개. 값이 클수록 검색 성능에 영향을 줄 수 있습니다.)")
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        layout.addRow("", hint)
        
        layout.addRow(QFrame())
        
        # 결과 클릭 동작
        self.click_play = QButtonGroup(self)
        w_one = QRadioButton("원클릭으로 재생")
        w_double = QRadioButton("더블클릭으로 재생")
        self.click_play.addButton(w_one, 1)
        self.click_play.addButton(w_double, 2)
        
        if self._config.get("double_click_to_play", False):
            w_double.setChecked(True)
        else:
            w_one.setChecked(True)

        layout.addRow("결과 목록 동작:", w_one)
        layout.addRow("", w_double)

        layout.addRow(QFrame())

        # 결과 텍스트 크기 — 작게 모드는 한 화면에 더 많은 결과 노출
        self.result_text_size = QButtonGroup(self)
        w_normal = QRadioButton("보통 (기본)")
        w_compact = QRadioButton("작게 (한 화면에 더 많이)")
        self.result_text_size.addButton(w_normal, 1)
        self.result_text_size.addButton(w_compact, 2)
        if self._config.get("compact_results", False):
            w_compact.setChecked(True)
        else:
            w_normal.setChecked(True)
        layout.addRow("결과 텍스트 크기:", w_normal)
        layout.addRow("", w_compact)

        self.tabs.addTab(tab, "일반")

    def _setup_theme_tab(self):
        """테마 선택 — 뉴트럴 / 라이트 / 네온 3중택1. 저장 버튼 누를 때 반영."""
        tab = QWidget()
        layout = QFormLayout(tab)

        self.theme_group = QButtonGroup(self)
        # 순서: 뉴트럴(기본) → 라이트 → 네온
        w_neutral = QRadioButton("뉴트럴 (기본)")
        w_light = QRadioButton("라이트")
        w_neon = QRadioButton("네온")
        self.theme_group.addButton(w_neutral, 1)
        self.theme_group.addButton(w_light, 2)
        self.theme_group.addButton(w_neon, 3)

        # 실제 적용된 테마 (current_theme()) 직접 사용 — _config 의존 X (key 누락 대비).
        cur = current_theme()  # 내부 키: "dark"/"light"/"grey"
        if cur == "light":
            w_light.setChecked(True)
        elif cur == "dark":
            w_neon.setChecked(True)
        else:  # grey
            w_neutral.setChecked(True)

        layout.addRow("테마:", w_neutral)
        layout.addRow("", w_light)
        layout.addRow("", w_neon)

        hint = QLabel("저장 버튼을 누르면 적용됩니다.")
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        layout.addRow("", hint)

        # 라디오 ID → 알리아스 매핑 (저장 시 _selected_theme() 가 읽음)
        self._theme_alias_for_id = {1: "neutral", 2: "light", 3: "neon"}

        self.tabs.addTab(tab, "테마")

    def selected_theme(self) -> str:
        """저장 시 main_window 가 호출 — 현재 선택된 테마 alias 반환."""
        cid = self.theme_group.checkedId()
        return self._theme_alias_for_id.get(cid, "neutral")

    def _setup_playback_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 재생 시작 방식
        self.playback_restart = QButtonGroup(self)
        w_start = QRadioButton("처음부터 다시 재생")
        w_pos = QRadioButton("현재 위치에서 이어서 재생")
        self.playback_restart.addButton(w_start, 1)
        self.playback_restart.addButton(w_pos, 2)
        
        if self._config.get("playback_restart_from_zero", True):
            w_start.setChecked(True)
        else:
            w_pos.setChecked(True)
            
        layout.addRow("정지 후 재생 방식:", w_start)
        layout.addRow("", w_pos)
        
        layout.addRow(QFrame())
        
        # 자동 미리보기
        self.auto_preview = QCheckBox("결과 선택 시 즉시 재생 (자동 미리보기)")
        self.auto_preview.setChecked(self._config.get("auto_preview", False))
        layout.addRow("자동 재생:", self.auto_preview)

        # 드래그 시 재생 중단 (default ON)
        self.stop_on_drag = QCheckBox("DAW 로 드래그 시작 시 재생 정지")
        self.stop_on_drag.setChecked(self._config.get("stop_on_drag", True))
        self.stop_on_drag.setToolTip(
            "툴에서 사운드를 DAW 로 드래그 앤 드롭하는 순간 재생을 정지합니다.\n"
            "체크 해제 시 드래그 중에도 계속 재생됩니다."
        )
        layout.addRow("드래그 동작:", self.stop_on_drag)

        self.tabs.addTab(tab, "재생")

    def _setup_indexing_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # 백그라운드 인덱스 정책
        self.index_policy = QButtonGroup(self)
        w_immediate = QRadioButton("스캔 완료 후 즉시 분석 시작")
        w_idle = QRadioButton("앱 미사용 중일 때만 분석 시작 (Idle)")
        self.index_policy.addButton(w_immediate, 1)
        self.index_policy.addButton(w_idle, 2)
        
        if self._config.get("index_on_idle", False):
            w_idle.setChecked(True)
        else:
            w_immediate.setChecked(True)
            
        layout.addRow("메타 분석 정책:", w_immediate)
        layout.addRow("", w_idle)

        idle_hint = QLabel(
            "Idle 정책: 클릭 · 키보드 입력 · 마우스 휠 스크롤이 30초간 없으면 백그라운드 메타 분석을 시작하고, "
            "분석 중 동일한 입력이 감지되면 즉시 중단합니다. (단순 마우스 호버는 입력으로 간주하지 않음. 재개는 다시 30초 idle 도달 시)"
        )
        idle_hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        idle_hint.setWordWrap(True)
        layout.addRow("", idle_hint)

        self.tabs.addTab(tab, "인덱싱")

    def _setup_duplicate_tab(self):
        """중복 검수 — 파일명+크기 동일 항목 그룹 표시. 전체 라이브러리 단일 스캔."""
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        hint = QLabel(
            "전체 라이브러리에서 파일명과 파일 크기가 동일한 항목을 그룹으로 묶어 보여줍니다.\n"
            "(파일 내용이 같더라도 이름이나 크기가 다르면 검출되지 않습니다.)"
        )
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        btn_row = QHBoxLayout()
        self._dup_scan_btn = QPushButton("중복 검수 시작")
        self._dup_scan_btn.setObjectName("primary")
        self._dup_scan_btn.setFixedHeight(28)
        self._dup_scan_btn.clicked.connect(self._run_duplicate_scan)
        btn_row.addWidget(self._dup_scan_btn)

        self._dup_repair_btn = QPushButton("검색 제외")
        self._dup_repair_btn.setFixedHeight(28)
        self._dup_repair_btn.setToolTip(
            "각 중복 그룹에서 경로가 가장 짧은 항목만 남기고 나머지를 검색에서 제외합니다.\n"
            "(파일·인덱스는 그대로 — [제외 관리]에서 언제든 복원 가능)"
        )
        self._dup_repair_btn.setEnabled(False)
        self._dup_repair_btn.clicked.connect(self._run_duplicate_repair)
        btn_row.addWidget(self._dup_repair_btn)

        self._dup_status = QLabel("")
        self._dup_status.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        btn_row.addWidget(self._dup_status, 1)

        # 수리로 제외된 항목 — 재스캔에도 다시 등록 안 됨. 관리 다이얼로그에서
        # 목록 확인 + 선택/전체 해제 가능 (해제 시 다음 갱신 때 복귀).
        self._dup_unsuppress_btn = QPushButton("제외 관리")
        self._dup_unsuppress_btn.setFixedHeight(28)
        self._dup_unsuppress_btn.setToolTip(
            "검색에서 제외된 파일 목록을 보고, 체크/전체 복원할 수 있습니다.\n"
            "복원된 파일은 즉시 검색 결과에 다시 나타납니다."
        )
        self._dup_unsuppress_btn.clicked.connect(self._open_suppressed_manager)
        # 첫 비동기 카운트(_on_hidden_count) 도착 전 클릭 방지 — 도착 시 활성/비활성 확정
        self._dup_unsuppress_btn.setEnabled(False)
        btn_row.addWidget(self._dup_unsuppress_btn)
        v.addLayout(btn_row)

        # 검사 진행바 — 스캔 워커 진행률 (검사 안 할 때는 숨김)
        self._dup_progress = QProgressBar()
        self._dup_progress.setFixedHeight(10)
        self._dup_progress.setTextVisible(False)
        self._dup_progress.hide()
        v.addWidget(self._dup_progress)

        self._dup_fill_gen = 0   # 청크 채움 세대 — 새 스캔 시작 시 이전 채움 폐기
        self._refresh_suppressed_label()

        # 결과 트리 도구줄 — 결과 내 검색 + 모두 펼치기/접기
        tools_row = QHBoxLayout()
        self._dup_filter = QLineEdit()
        self._dup_filter.setPlaceholderText("결과 내 검색 (파일명/경로)")
        self._dup_filter.setClearButtonEnabled(True)
        self._dup_filter.textChanged.connect(self._apply_dup_filter)
        tools_row.addWidget(self._dup_filter, 1)
        expand_btn = QPushButton("모두 펼치기")
        expand_btn.setFixedHeight(24)
        expand_btn.clicked.connect(lambda: self._dup_tree.expandAll())
        tools_row.addWidget(expand_btn)
        collapse_btn = QPushButton("모두 접기")
        collapse_btn.setFixedHeight(24)
        collapse_btn.clicked.connect(lambda: self._dup_tree.collapseAll())
        tools_row.addWidget(collapse_btn)
        v.addLayout(tools_row)

        self._dup_tree = QTreeWidget()
        self._dup_tree.setHeaderLabels(["파일명 / 경로", "크기", "개수"])
        self._dup_tree.setColumnWidth(0, 320)
        self._dup_tree.setColumnWidth(1, 90)
        self._dup_tree.setColumnWidth(2, 60)
        # 경로 잘려도 호버 시 풀 경로 툴팁 (item 의 setToolTip 으로 처리)
        self._dup_tree.setMouseTracking(True)
        # 우클릭 메뉴
        self._dup_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._dup_tree.customContextMenuRequested.connect(self._on_dup_context_menu)
        v.addWidget(self._dup_tree, 1)

        self.tabs.addTab(tab, "중복 검수")

    def _refresh_suppressed_label(self):
        """제외 관리 버튼 — 숨김 항목 수 표시 + 0개면 비활성.
        count_hidden 이 실DB 1초+ 라 워커에서 세고 도착 시 갱신 (최신 요청만 반영)."""
        parent = self.parent()
        if parent is None or not getattr(parent, "manager", None):
            self._dup_unsuppress_btn.setText("제외 관리")
            self._dup_unsuppress_btn.setEnabled(False)
            return
        self._hidden_count_gen = getattr(self, "_hidden_count_gen", 0) + 1
        worker = _HiddenCountWorker(str(parent.manager.db_path), self._hidden_count_gen)
        thread = QThread()   # 부모 X — 다이얼로그 닫혀도 안전 (_hold_bg_thread)
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_hidden_count)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_hidden_count(self, n: int, gen: int):
        if gen != getattr(self, "_hidden_count_gen", 0):
            return  # 더 최신 요청이 진행 중
        self._hidden_count_cache = n   # 제외 관리 창 열 때 재사용 (재카운트 프리즈 방지)
        self._dup_unsuppress_btn.setText(f"제외 관리 ({n:,})" if n else "제외 관리")
        self._dup_unsuppress_btn.setEnabled(n > 0)

    _SUPPRESSED_LIST_CAP = 2000  # 표시 상한 — 2만 개를 QListWidget 에 한 번에 올리면
                                 # 메인 스레드에서 위젯 생성 폭주로 프리즈/크래시 (사용자 보고).
                                 # 검색 필터 + [전체 해제]로 보완. 전체 수는 COUNT 로 표시.

    def _open_suppressed_manager(self):
        """숨김 목록 관리 — 목록 표시 + 선택 해제 / 전체 해제."""
        parent = self.parent()
        if parent is None or not getattr(parent, "manager", None):
            return
        db = Database(str(parent.manager.db_path))
        # 개수는 버튼 라벨용 비동기 카운트가 이미 세어 둔 값 재사용 — 여기서
        # 다시 세면 cold 디스크 캐시에서 1초+ UI 프리즈 (실측 2026-07-15).
        total_hidden = getattr(self, "_hidden_count_cache", None)
        if total_hidden is None:
            total_hidden = db.count_hidden()  # 카운트 도착 전엔 버튼 비활성 — 사실상 미도달
        if total_hidden <= 0:
            self._refresh_suppressed_label()
            return
        # 목록 로드는 아래 _fill_list() — 해제 후에도 창을 유지하며 재사용.

        dlg = QDialog(self)
        dlg.setWindowTitle(f"검색 제외 목록 — {total_hidden:,}건")
        dlg.resize(780, 480)
        v = QVBoxLayout(dlg)
        hint = QLabel(
            "검색에서 제외된 파일들입니다 (디스크의 파일·인덱스는 그대로).\n"
            "체크 후 복원하면 즉시 검색 결과에 다시 나타납니다. "
            "Shift/Ctrl+클릭 다중 선택 시 자동으로 체크됩니다.\n"
            "[중복 아님 · 미복원] = 상대 사본이 삭제되어 더 이상 중복이 아니지만, "
            "복원하지 않기로 한 항목입니다."
        )
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # 검색 — 표시 상한과 무관하게 제외 '전체'에서 찾음 (일치 부분 강조)
        search = QLineEdit()
        search.setPlaceholderText("검색 (파일명/경로 — 전체 목록에서 찾음)")
        search.setClearButtonEnabled(True)
        v.addWidget(search)

        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        _hl_delegate = _HighlightListDelegate(lst)
        lst.setItemDelegate(_hl_delegate)
        v.addWidget(lst, 1)
        over = QLabel("")
        over.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        v.addWidget(over)

        _ORPHAN_TAG = "[중복 아님 · 미복원]  "
        # checked: 복원 대상으로 체크한 경로 — 검색을 바꿔 목록이 재로딩돼도 유지
        state = {"total": total_hidden, "loaded": 0, "match": total_hidden,
                 "checked": set(), "syncing": False}

        def _update_over():
            if search.text().strip():
                cap_note = (f" (상한 {self._SUPPRESSED_LIST_CAP:,})"
                            if state["match"] > state["loaded"] else "")
                over.setText(f"일치 {state['match']:,}건 · 표시 {state['loaded']:,}건{cap_note}")
            else:
                extra = (f" · 미표시 {state['total'] - state['loaded']:,}건 "
                         f"(상한 {self._SUPPRESSED_LIST_CAP:,} — 검색은 전체에서 찾음)"
                         if state["total"] > state["loaded"] else "")
                over.setText(f"표시 {state['loaded']:,}건{extra}")

        def _update_sel_btn():
            n = len(state["checked"])
            sel_btn.setText(f"체크 항목 복원 ({n:,})" if n else "체크 항목 복원")
            sel_btn.setEnabled(n > 0)

        def _fill_list():
            # 상한까지만 로드 — 2만+ 를 한 번에 QListWidget 에 올리면 프리즈/크래시.
            # 검색어가 있으면 로드분이 아니라 제외 '전체'에서 DB 검색 (상한 무관).
            term = search.text().strip()
            _hl_delegate.term = term
            rows = db.get_hidden_paths(limit=self._SUPPRESSED_LIST_CAP,
                                       search=term or None)
            state["match"] = db.count_hidden(term) if term else state["total"]
            # [중복 아님·미복원] 태그 검증 — 쌍둥이가 돌아온 행은 표시 자동 해제(stale 방지).
            flagged = [p for p, fl in rows if fl]
            still_orphan = db.verify_dup_orphans(flagged) if flagged else set()
            state["syncing"] = True
            lst.clear()
            for p, _fl in rows:
                it = QListWidgetItem()
                if p in still_orphan:
                    it.setText(_ORPHAN_TAG + p)
                    it.setForeground(QColor(_INCOMPLETE_COLOR_HEX))
                else:
                    it.setText(p)
                # 복원/체크/메뉴는 표시 텍스트가 아니라 원본 경로(UserRole) 기준
                it.setData(Qt.ItemDataRole.UserRole, p)
                it.setToolTip(p)   # 창 크기와 무관하게 풀 파일명+경로 확인
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if p in state["checked"]
                                 else Qt.CheckState.Unchecked)
                lst.addItem(it)
            state["syncing"] = False
            state["loaded"] = lst.count()
            _update_over()
            _update_sel_btn()

        # 검색 디바운스 — 입력 250ms 멈추면 DB 재조회
        _search_timer = QTimer(dlg)
        _search_timer.setSingleShot(True)
        _search_timer.setInterval(250)
        _search_timer.timeout.connect(_fill_list)
        search.textChanged.connect(lambda _t: _search_timer.start())

        def _on_item_changed(it):
            if state["syncing"]:
                return
            p = it.data(Qt.ItemDataRole.UserRole)
            if it.checkState() == Qt.CheckState.Checked:
                state["checked"].add(p)
            else:
                state["checked"].discard(p)
            _update_sel_btn()
        lst.itemChanged.connect(_on_item_changed)

        def _on_selection_changed():
            # Shift/Ctrl 다중 선택 → 선택 항목 자동 체크 (개별 해제는 체크박스 클릭)
            for it in lst.selectedItems():
                if it.checkState() != Qt.CheckState.Checked:
                    it.setCheckState(Qt.CheckState.Checked)
        lst.itemSelectionChanged.connect(_on_selection_changed)

        def _after_release(n: int, closing_all: bool = False, notify: bool = False):
            _sync_outside(f"{n:,}건 복원 — 검색 결과에 즉시 다시 나타납니다.")
            state["total"] = db.count_hidden()
            # notify=True(버튼 복원)만 완료 팝업 — 우클릭 '즉시 복원'은 흐름 유지 위해 생략
            if notify:
                left = state["total"]
                extra = "\n\n남은 검색 제외 항목이 없어 목록을 닫습니다." if (closing_all or left <= 0) \
                        else f"\n\n검색 제외 목록에 {left:,}건이 남아 있습니다."
                QMessageBox.information(
                    dlg, "복원 완료",
                    f"{n:,}개 항목을 검색 결과로 복원했습니다.{extra}",
                )
            if closing_all or state["total"] <= 0:
                dlg.accept()   # 남은 항목 없음 — 관리할 게 없어 닫음
                return
            dlg.setWindowTitle(f"검색 제외 목록 — {state['total']:,}건")
            _fill_list()   # 창 유지 — 복원분만 빠진 목록으로 재로딩 (검색어 유지)

        # 우클릭 — 탐색기/경로 복사/즉시 복원/같은 폴더 일괄 체크
        lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _on_list_menu(pos):
            it = lst.itemAt(pos)
            if it is None:
                return
            path = it.data(Qt.ItemDataRole.UserRole)
            menu = QMenu(lst)
            a_open = menu.addAction("탐색기에서 보기")
            a_copy = menu.addAction("경로 복사")
            menu.addSeparator()
            a_one = menu.addAction("이 항목만 즉시 복원")
            a_dir = menu.addAction("같은 폴더 전부 체크")
            chosen = menu.exec(lst.viewport().mapToGlobal(pos))
            if chosen is a_open:
                self._reveal_in_explorer(path)
            elif chosen is a_copy:
                QApplication.clipboard().setText(path)
            elif chosen is a_one:
                n = db.unhide_files([path])
                state["checked"].discard(path)
                _after_release(n)
            elif chosen is a_dir:
                d = os.path.dirname(path).lower()
                for i in range(lst.count()):
                    itx = lst.item(i)
                    if os.path.dirname(itx.data(Qt.ItemDataRole.UserRole)).lower() == d:
                        itx.setCheckState(Qt.CheckState.Checked)
        lst.customContextMenuRequested.connect(_on_list_menu)

        btns = QHBoxLayout()
        sel_btn = QPushButton("체크 항목 복원")
        sel_btn.setEnabled(False)
        all_btn = QPushButton("전체 복원")
        close_btn = QPushButton("닫기")
        btns.addWidget(sel_btn); btns.addWidget(all_btn); btns.addStretch(1); btns.addWidget(close_btn)
        v.addLayout(btns)

        def _sync_outside(msg: str):
            """복원 후 바깥(탭 라벨/카운트/검색) 동기화 — 창 닫지 않음."""
            self._dup_status.setText(msg)
            self._refresh_suppressed_label()
            self._notify_parent_counts_changed()  # 중앙 로더 없는 가벼운 갱신

        def _release_checked():
            # 체크한 것 전부 — 검색을 바꿔 현재 화면에 안 보이는 체크분도 포함
            chosen = sorted(state["checked"])
            if not chosen:
                return
            n = db.unhide_files(chosen)
            state["checked"].clear()
            _after_release(n, notify=True)

        def _release_all():
            ret = QMessageBox.question(
                dlg, "전체 복원",
                f"검색 제외된 {state['total']:,}건 전부를 검색 결과에 복원합니다.\n진행할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            n = db.unhide_all()
            state["checked"].clear()
            _after_release(n, closing_all=True, notify=True)

        sel_btn.clicked.connect(_release_checked)
        all_btn.clicked.connect(_release_all)
        close_btn.clicked.connect(dlg.reject)
        _fill_list()
        dlg.exec()
        self._refresh_suppressed_label()

    def _run_duplicate_repair(self):
        """각 그룹에서 경로 가장 짧은 1개 유지, 나머지 인덱스에서 제거."""
        parent = self.parent()
        if parent is None or not hasattr(parent, "manager"):
            return
        groups = getattr(self, "_dup_groups_cache", []) or []
        if not groups:
            return

        # 각 그룹에서 경로 길이 짧은 것을 keep, 나머지 remove
        keep_list = []
        remove_list = []
        for g in groups:
            paths = sorted(g["paths"], key=lambda p: (len(p), p))
            keep_list.append(paths[0])
            remove_list.extend(paths[1:])

        if not remove_list:
            self._dup_status.setText("제거할 항목 없음.")
            return

        msg = (
            f"중복 그룹 {len(groups):,}개에서 경로가 가장 짧은 1개씩 유지하고\n"
            f"나머지 {len(remove_list):,}개를 검색에서 제외합니다.\n\n"
            f"파일과 인덱스는 그대로 유지되며 언제든 [제외 관리]에서 복원할 수 있습니다.\n"
            f"진행할까요?"
        )
        ret = QMessageBox.question(
            self, "검색 제외 확인", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # 백그라운드 처리 + 진행률은 _dup_status 라벨에 표시.
        if getattr(self, "_dup_repair_running", False):
            return
        self._dup_repair_running = True
        self._dup_repair_btn.setEnabled(False)
        self._dup_scan_btn.setEnabled(False)
        self._dup_status.setText(f"검색 제외 처리 중... (0 / {len(remove_list):,})")

        worker = _DupRepairWorker(str(parent.manager.db_path), remove_list)
        thread = QThread()   # 부모 X — 다이얼로그 닫혀도 안전 (_hold_bg_thread)
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_dup_repair_progress)
        worker.finished.connect(self._on_dup_repair_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_dup_repair_progress(self, done: int, total: int):
        pct = done * 100 // max(1, total)
        self._dup_status.setText(f"검색 제외 중... {done:,} / {total:,} ({pct}%)")

    def _on_dup_repair_done(self, removed: int, error: str):
        self._dup_repair_running = False
        self._dup_scan_btn.setEnabled(True)
        if error:
            self._dup_status.setText(f"수리 실패: {error}")
            QMessageBox.critical(self, "검색 제외 실패", f"검색 제외 처리 중 오류가 발생했습니다.\n\n{error}")
            return
        self._dup_status.setText(f"{removed:,}개 항목 검색 제외 완료 ([제외 관리]에서 복원 가능).")
        self._dup_repair_btn.setEnabled(False)
        self._refresh_suppressed_label()
        self._notify_parent_counts_changed()
        QMessageBox.information(
            self, "검색 제외 완료",
            f"{removed:,}개 항목을 검색에서 제외했습니다.\n\n"
            f"파일과 인덱스는 그대로 유지됩니다.\n"
            f"[제외 관리]에서 언제든 검색 결과로 복원할 수 있습니다.",
        )
        # 남은 중복 갱신용 자동 재스캔 — 이때 스캔 완료 팝업은 억제(중복 팝업 방지)
        self._suppress_scan_popup = True
        self._run_duplicate_scan()

    def _notify_parent_counts_changed(self):
        """검색 + 카운트류 가벼운 갱신 — 루트 구조가 안 바뀌므로 전체 트리
        리빌드(_refresh_after_index_change)는 호출하지 않는다. 그 경로는 중앙
        로더를 띄우는데, 모달인 이 다이얼로그 '뒤'에 깔려 사용자에겐 갑자기
        앱이 딤드된 것처럼 보임 (사용자 보고)."""
        parent = self.parent()
        if parent is None:
            return
        if hasattr(parent, "_on_blacklist_changed"):
            parent._on_blacklist_changed()        # 검색 재실행
        try:
            if hasattr(parent, "manager") and parent.manager:
                parent.db = parent.manager.open_db_for_search()
                # 총 파일수 라벨은 아래 _refresh_tree_counts_async 가 비동기 갱신 →
                # 메인 스레드 동기 count_files() 제거(프리즈 방지).
                parent.lib_status.refresh_async(parent.manager)
        except Exception:
            logger.exception("카운트 갱신 실패")
        if hasattr(parent, "_refresh_tree_counts_async"):
            parent._refresh_tree_counts_async()   # 트리 카운트만 비동기 (로더 X)

    def _on_dup_context_menu(self, pos):
        """우클릭 메뉴 — 탐색기에서 보기 / 경로 복사."""
        item = self._dup_tree.itemAt(pos)
        if item is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            # 그룹 헤더 — 파일명 복사만
            menu = QMenu(self._dup_tree)
            act_name = menu.addAction("파일명 복사")
            if menu.exec(self._dup_tree.viewport().mapToGlobal(pos)) is act_name:
                QApplication.clipboard().setText(item.text(0))
            return
        menu = QMenu(self._dup_tree)
        act_open = menu.addAction("탐색기에서 보기")
        act_copy = menu.addAction("경로 복사")
        chosen = menu.exec(self._dup_tree.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._reveal_in_explorer(path)
        elif chosen is act_copy:
            QApplication.clipboard().setText(path)

    def _reveal_in_explorer(self, path: str):
        try:
            _shell_reveal(path)
        except Exception as e:
            QMessageBox.warning(self, "탐색기 열기 실패", str(e))

    def _run_duplicate_scan(self):
        """중복 그룹 스캔 — 실DB 4초+ 라 워커 스레드 + 진행바 (UI 스레드
        직접 조회는 프리즈, 사용자 보고). 트리 채움은 _fill_dup_tree_chunked."""
        parent = self.parent()
        if parent is None or not hasattr(parent, "manager"):
            return
        if getattr(self, "_dup_scan_running", False):
            return
        self._dup_scan_running = True
        self._dup_fill_gen += 1          # 진행 중이던 청크 채움 폐기
        self._dup_tree.clear()
        self._dup_scan_btn.setEnabled(False)
        self._dup_repair_btn.setEnabled(False)
        self._dup_status.setText("검사 준비 중...")
        self._dup_progress.setRange(0, 0)   # 준비 단계 — 총량 미확정 애니메이션
        self._dup_progress.show()
        worker = _DupScanWorker(str(parent.manager.db_path))
        thread = QThread()   # 부모 X — 다이얼로그 닫혀도 안전 (_hold_bg_thread)
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_dup_scan_progress)
        worker.finished.connect(self._on_dup_scan_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_dup_scan_progress(self, done: int, total: int):
        if self._dup_progress.maximum() == 0:
            # 첫 진행 신호 — 확정 진행바로 전환 + 남은 시간 추정 기준점
            self._dup_progress.setRange(0, 100)
            self._dup_scan_t0 = time.monotonic()
            self._dup_scan_base = done
        pct = min(100, done * 100 // max(1, total))
        self._dup_progress.setValue(pct)
        if done >= total:
            self._dup_status.setText("결과 정리 중...")   # 중복 키 경로 조회 단계
            return
        remain_txt = ""
        seen = done - self._dup_scan_base
        elapsed = time.monotonic() - self._dup_scan_t0
        if seen > 0 and elapsed > 0.2:
            remain = (total - done) * elapsed / seen
            remain_txt = f" — 약 {self._fmt_remain(remain)} 남음"
        self._dup_status.setText(f"검사 중... {pct}%{remain_txt}")

    @staticmethod
    def _fmt_remain(sec: float) -> str:
        sec = max(1, int(round(sec)))
        return f"{sec // 60}분 {sec % 60}초" if sec >= 60 else f"{sec}초"

    def _on_dup_scan_done(self, groups: list, error: str):
        self._dup_scan_running = False
        self._dup_scan_btn.setEnabled(True)
        self._dup_progress.hide()
        # 검색 제외 직후 자동 재스캔이면 완료 팝업 억제 (제외 팝업과 중복 방지)
        popup = not getattr(self, "_suppress_scan_popup", False)
        self._suppress_scan_popup = False
        if error:
            self._dup_status.setText(f"오류: {error}")
            self._dup_groups_cache = []
            if popup:
                QMessageBox.critical(self, "중복 검수 실패", f"검사 중 오류가 발생했습니다.\n\n{error}")
            return
        if not groups:
            self._dup_status.setText("중복 항목 없음.")
            self._dup_groups_cache = []
            if popup:
                QMessageBox.information(
                    self, "중복 검수 완료",
                    "중복 항목이 없습니다.\n(파일명과 크기가 모두 같은 항목이 발견되지 않았습니다.)",
                )
            return
        total_groups = len(groups)
        total_files = sum(len(g["paths"]) for g in groups)
        removable = total_files - total_groups  # 각 그룹에서 1개 남기고 나머지
        self._dup_status.setText(
            f"{total_groups:,}개 그룹 / {total_files:,}개 파일 — 제외 대상 {removable:,}개"
        )
        self._dup_groups_cache = groups
        self._dup_repair_btn.setEnabled(removable > 0)
        self._fill_dup_tree_chunked(groups)
        if popup:
            QMessageBox.information(
                self, "중복 검수 완료",
                f"중복 그룹 {total_groups:,}개 · 파일 {total_files:,}개를 찾았습니다.\n\n"
                f"[검색 제외]를 누르면 각 그룹에서 경로가 가장 짧은 1개만 남기고\n"
                f"나머지 {removable:,}개를 검색에서 제외합니다.\n"
                f"(파일·인덱스는 그대로 — [제외 관리]에서 언제든 복원 가능)",
            )

    _DUP_FILL_CHUNK = 300  # 그룹/틱 — 수만 그룹 한 번에 넣으면 아이템 생성 폭주로
                           # 프리즈 (제외 관리 목록 상한과 동일 메커니즘)

    def _fill_dup_tree_chunked(self, groups: list):
        """결과 트리를 타이머 틱으로 나눠 채움 — 틱 사이에 이벤트 루프가 돌아
        화면이 안 멈춘다. 새 스캔 시작(_dup_fill_gen 증가) 시 즉시 중단."""
        gen = self._dup_fill_gen
        state = {"idx": 0}
        timer = QTimer(self)   # 다이얼로그 소멸 시 함께 정지 — 파괴 후 틱 X
        timer.setInterval(0)

        def _step():
            if gen != self._dup_fill_gen:
                timer.stop()
                timer.deleteLater()
                return
            i = state["idx"]
            end = min(i + self._DUP_FILL_CHUNK, len(groups))
            items = []
            for g in groups[i:end]:
                size = g["file_size"] or 0
                size_str = f"{size/1024/1024:.2f} MB" if size >= 1024*1024 else f"{size/1024:.1f} KB"
                # "(N개)" 텍스트 제거 — 개수 컬럼과 중복 표기였음
                parent_item = QTreeWidgetItem([
                    g["file_name"],
                    size_str,
                    str(len(g['paths'])),
                ])
                parent_item.setToolTip(0, f"{g['file_name']} — {len(g['paths'])}개 위치")
                # 제외 실행 시 살아남는 항목(경로 짧은 순 1개)을 미리 표시 —
                # _run_duplicate_repair 의 keep 선정 규칙과 동일해야 함.
                kept = min(g["paths"], key=lambda p: (len(p), p))
                for p in g["paths"]:
                    is_keep = (p == kept)
                    child = QTreeWidgetItem([("✓ 유지   " if is_keep else "") + p, "", ""])
                    if is_keep:
                        child.setForeground(0, QBrush(QColor(COLORS["accent"])))
                        child.setToolTip(0, f"{p}\n(제외 실행 시 이 항목이 검색에 남습니다)")
                    else:
                        child.setToolTip(0, f"{p}\n(제외 실행 시 검색에서 제외 — [제외 관리]에서 복원 가능)")
                    # 경로 데이터 보관 — 우클릭/처리 시 사용
                    child.setData(0, Qt.ItemDataRole.UserRole, p)
                    parent_item.addChild(child)
                items.append(parent_item)
            self._dup_tree.addTopLevelItems(items)
            # 필터 입력이 남아 있으면 새로 추가된 그룹에만 적용 (전체 재순회 X)
            t = (self._dup_filter.text() or "").strip().lower()
            if t:
                for it in items:
                    self._dup_filter_apply_group(it, t)
            state["idx"] = end
            if end >= len(groups):
                timer.stop()
                timer.deleteLater()

        timer.timeout.connect(_step)
        timer.start()

    @staticmethod
    def _dup_filter_apply_group(g, t: str):
        """그룹 1개에 필터 적용 — 그룹명 일치 시 그룹 전체, 아니면 일치 경로만."""
        if not t:
            g.setHidden(False)
            for j in range(g.childCount()):
                g.child(j).setHidden(False)
            return
        gname_hit = t in g.text(0).lower()
        any_child = False
        for j in range(g.childCount()):
            c = g.child(j)
            hit = gname_hit or (t in c.text(0).lower())
            c.setHidden(not hit)
            any_child = any_child or hit
        g.setHidden(not (gname_hit or any_child))

    def _apply_dup_filter(self, text: str):
        """결과 트리 전체에 필터 적용 (검색창 textChanged)."""
        t = (text or "").strip().lower()
        tree = getattr(self, "_dup_tree", None)
        if tree is None:
            return
        for i in range(tree.topLevelItemCount()):
            self._dup_filter_apply_group(tree.topLevelItem(i), t)

    def _setup_shortcuts_tab(self):
        tab = QWidget()
        v = QVBoxLayout(tab)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QFormLayout(content)
        
        self._shortcut_edits = {}
        
        shortcuts = [
            ("play_pause", "재생 / 일시정지", "Space"),
            ("seek_to_start", "처음으로", "Home"),
            ("toggle_loop", "반복 재생", "R"),
            ("toggle_segments", "세그먼트 토글", "S"),
            ("toggle_history", "히스토리 토글", "H"),
            ("open_settings", "환경설정 열기/닫기", "Ctrl+P"),
        ]
        
        for key, label, default in shortcuts:
            edit = QKeySequenceEdit(QKeySequence(self._shortcuts.get(key, default)))
            layout.addRow(f"{label}:", edit)
            self._shortcut_edits[key] = edit

        # 휠 기반 단축키 (변경 불가). QKeySequenceEdit 는 휠을 표현 못 하므로 dimmed QLineEdit.
        wheel_shortcuts = [
            ("파형 가로 확대/축소", "Ctrl+휠"),
            ("파형 세로 확대/축소", "Ctrl+Alt+휠"),
            ("가로 스크롤 (파형/파일브라우저/결과)", "Shift+휠"),
        ]
        for label, value in wheel_shortcuts:
            display = QLineEdit(value)
            display.setReadOnly(True)
            display.setEnabled(False)
            display.setToolTip("이 단축키는 변경할 수 없습니다")
            # 명시적 회색 — disabled 만으론 OS/테마에 따라 거의 검정인 경우 있음
            display.setStyleSheet("QLineEdit { color: #888888; }")
            layout.addRow(f"{label} (개별 설정 불가):", display)

        scroll.setWidget(content)
        v.addWidget(scroll)
        self.tabs.addTab(tab, "단축키")

    def _on_reset(self):
        self.search_limit.setValue(500)
        self.click_play.button(1).setChecked(True)
        self.playback_restart.button(1).setChecked(True)
        self.auto_preview.setChecked(False)
        self.stop_on_drag.setChecked(True)
        self.index_policy.button(1).setChecked(True)
        
        defaults = {"play_pause": "Space", "seek_to_start": "Home", "toggle_loop": "R",
                    "toggle_segments": "S", "toggle_history": "H", "open_settings": "Ctrl+P"}
        for key, edit in self._shortcut_edits.items():
            edit.setKeySequence(QKeySequence(defaults[key]))

    def get_config(self) -> dict:
        sc = {k: e.keySequence().toString() for k, e in self._shortcut_edits.items()}
        return {
            "search_limit": self.search_limit.value(),
            "double_click_to_play": self.click_play.checkedId() == 2,
            "compact_results": self.result_text_size.checkedId() == 2,
            "playback_restart_from_zero": self.playback_restart.checkedId() == 1,
            "auto_preview": self.auto_preview.isChecked(),
            "stop_on_drag": self.stop_on_drag.isChecked(),
            "index_on_idle": self.index_policy.checkedId() == 2,
            "shortcuts": sc
        }


class _TermIndexBuildWorker(QObject):
    """단어색인(search_fts_term) 최초 1회 백그라운드 빌드 — 없을 때만."""
    finished = pyqtSignal(bool)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def run(self):
        ok = False
        try:
            ok = Database(self.db_path).build_term_index()
        except Exception:
            logger.exception("단어색인 자동 빌드 실패")
        self.finished.emit(bool(ok))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SoundField")
        self.resize(1500, 850)
        self.setWindowIcon(QIcon(str(ASSETS_DIR / "icon.ico")))

        self.manager: LibraryManager = LibraryManager(DEFAULT_STORE)
        self.db: Database = self.manager.open_db_for_search()
        self._current_prefix: str = ""
        self._current_prefixes = []
        self._folder_display_names = {}
        self._favorites = []
        self._history_expanded_width = 280  # 사용자가 splitter handle 로 조정한 값 (config 저장)
        self._config_data = {
            "search_limit": 500,
            "double_click_to_play": False,
            "compact_results": False,
            "playback_restart_from_zero": True,
            "auto_preview": False,
            "stop_on_drag": True,
            "index_on_idle": False,
            # 인덱싱 집중 모드: True=재생 중에도 100% 인덱싱(재생 끊길 수 있음),
            # False(기본)=재생 중엔 백그라운드 메타 분석 양보(디스크 여유 → 재생 매끄럽게).
            "index_focus_mode": False,
            "blacklist_expanded_h": 220,
            "shortcuts": {
                "play_pause": "Space",
                "toggle_segments": "S",
                "toggle_history": "H",
                "open_settings": "Ctrl+P",
                "seek_to_start": "Home",
                "toggle_loop": "R",
            }
        }

        self.index_thread = self.index_worker = None
        self.meta_thread = self.meta_worker = None
        self.incomplete_thread = self.incomplete_worker = None
        self._last_incomplete_refresh = 0.0

        # Idle 감지용 (마지막 입력 시각). idle 정책: 30초 미사용 → 메타 분석 시작,
        # 분석 중 사용자 입력(클릭/키/휠) 들어오면 즉시 중단.
        self._last_input_time = time.monotonic()
        self._meta_cancelling = False
        # 사용자 명시 일시정지 상태 (idle 정책과 독립)
        self._meta_paused = False
        self._paused_at = 0.0
        # 재생 중 인덱싱 양보 상태 (집중 모드 OFF 일 때만). _meta_paused 와 별개.
        self._meta_yielded_playback = False
        self._playback_active = False
        # 일시정지로 취소된 워커가 아직 종료 중일 때 재개를 누르면, 워커 완전 종료 후
        # 재개하도록 대기 플래그(레이스 방지 — 취소 중 _ensure 는 early-return 되므로).
        self._meta_resume_pending = False
        self._index_resume_timer = QTimer(self)
        self._index_resume_timer.setSingleShot(True)
        self._index_resume_timer.setInterval(1500)  # 재생 멈춘 뒤 잠시 후 재개(연속 미리듣기 churn 방지)
        self._index_resume_timer.timeout.connect(self._resume_meta_after_playback)
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(2000)
        self._idle_timer.timeout.connect(self._check_idle_and_index)
        self._idle_timer.start()
        # eventFilter 가 자식 위젯까지 입력을 보려면 QApplication 에 설치해야 함.
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._build_ui()
        self._load_config()
        # 시작 시 '1단계 검색 색인 마무리'가 담당하므로, _check_fts_stale 의
        # Yes/No 수동 수리 팝업은 억제(_prompt_fts_repair 가 이 플래그를 본다).
        self._startup_resume_pending = True
        self._refresh_after_index_change()
        self._setup_shortcuts()
        # 메타 분석기가 개선됐으면 이전 실패분을 대기(pending) 로 되돌려 자동 재분석.
        # _startup_resume_indexing 보다 먼저 — 그 안의 2단계 시작이 곧바로 집어간다.
        self._retry_failed_on_parser_upgrade()
        # 재시작 = 처음 실행처럼: 1단계(검색 색인)가 덜 됐으면 블로킹으로 마저 만든
        # 뒤에야 2단계(백그라운드 메타)로 넘어간다. _startup_resume_indexing 가 분기.
        QTimer.singleShot(1000, self._startup_resume_indexing)
        QTimer.singleShot(2500, self._ensure_term_index_async)   # 단어색인 없으면 1회 자동 빌드

    def eventFilter(self, obj, event):
        # 의도적 사용자 액션만 idle 해제로 인정 — 클릭/키보드/휠 스크롤.
        # MouseMove(단순 호버) 는 제외 — 마우스가 우연히 윈도우 위를 지나가도 idle 해제되는 과민 반응 방지.
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
        ):
            self._last_input_time = time.monotonic()
        return super().eventFilter(obj, event)

    def _check_idle_and_index(self):
        # 사용자 명시 일시정지 — 5분간 앱 미사용 시 자동 재개 (idle 정책 OFF 와 무관)
        if self._meta_paused:
            idle_time = time.monotonic() - self._last_input_time
            if idle_time >= 300:  # 5분
                self._resume_meta_analysis(reason="5분간 앱 미사용 — 자동 재개")
            return
        # idle 정책 OFF: 자동 시작도 자동 중단도 하지 않음 (사용자가 명시적으로 시작한 분석은 그대로)
        if not self._config_data.get("index_on_idle", False):
            return
        idle_time = time.monotonic() - self._last_input_time
        # 분석 중인데 최근 입력 감지 → 즉시 중단 (idle 정책)
        if self._is_background_meta_running():
            if idle_time < 5 and not self._meta_cancelling:
                self._meta_cancelling = True
                self.manager.cancel()
                self.indexing_badge.setVisible(False)
                # worker 종료를 기다리지 않고 즉시 일시정지 표시로 전환
                self._update_meta_indicator(self._meta_pending_n, active=False)
                self._set_status("앱 사용 감지 — 백그라운드 메타 분석 일시정지 (30초 미사용 시 자동 재개)")
            return
        # 30초 이상 idle → 분석 시작
        if idle_time > 30:
            self._meta_cancelling = False
            self._ensure_background_meta_running()

    # ─────────── 사용자 명시 일시정지 / 재개 ───────────
    def _on_meta_pause_clicked(self):
        """status bar 일시정지/재개 버튼 클릭 핸들러."""
        if self._meta_paused:
            self._resume_meta_analysis(reason="사용자가 직접 재개")
            return
        ret = QMessageBox.question(
            self, "백그라운드 인덱싱 일시정지",
            "앱 사용 성능을 위해 백그라운드 메타 인덱싱을 잠시 정지할까요?\n\n"
            "재개 시점:\n"
            "  ▶ 재생/정지 옆 ▶ 버튼을 직접 클릭, 또는\n"
            "  • 5분간 앱을 사용하지 않으면 자동 재개",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._pause_meta_analysis()

    def _style_meta_pause_btn(self, paused: bool):
        """일시정지/재개 버튼 — 새로고침/세그먼트 버튼처럼 외곽선+내부 fill + 호버.
        paused=True(재생 ▶ 표시, 클릭 시 재개)=초록 / False(정지 ⏸ 표시, 클릭 시 정지)=빨강.
        #icon QSS 의 min-height/padding 을 0 으로 눌러 22px 정사각형이 안 잘리게 한다."""
        if paused:
            border, fill, fill_h, col = ("rgba(47,179,68,0.55)", "rgba(47,179,68,0.13)",
                                         "rgba(47,179,68,0.22)", "#3a9d52")
            self.meta_pause_btn.setIcon(_make_play_icon(12, col))
        else:
            border, fill, fill_h, col = ("rgba(219,67,67,0.55)", "rgba(219,67,67,0.20)",
                                         "rgba(219,67,67,0.30)", "#db4343")
            self.meta_pause_btn.setIcon(_make_pause_icon(12, col))
        self.meta_pause_btn.setStyleSheet(
            "QPushButton#icon { min-height:0px; padding:0px; border-radius:4px; "
            "border:1px solid %s; background-color:%s; }"
            "QPushButton#icon:hover { border:1px solid %s; background-color:%s; }"
            "QPushButton#icon:pressed { border:1px solid %s; background-color:%s; }"
            % (border, fill, border, fill_h, border, fill_h)
        )

    def _pause_meta_analysis(self):
        self._meta_paused = True
        self._meta_resume_pending = False  # 재개 대기 취소(다시 멈춤)
        self._paused_at = time.monotonic()
        self._meta_cancelling = True
        # 활성 워커 cancel — 다음 file pop 시점에 즉시 종료
        if self._is_background_meta_running() or self.manager.is_indexing():
            self.manager.cancel()
        self._style_meta_pause_btn(paused=True)  # 재생(▶) 초록
        self.meta_pause_btn.setToolTip(
            "백그라운드 인덱싱 재개\n"
            "재개 조건: 클릭 또는 5분간 앱 미사용 시 자동 재개"
        )
        self._update_meta_indicator(self._meta_pending_n, active=False)
        self._set_status(
            "백그라운드 인덱싱 일시정지 — 재개 버튼 또는 5분간 앱 미사용 시 자동 재개"
        )

    def _resume_meta_analysis(self, reason: str = ""):
        if not self._meta_paused:
            return
        self._meta_paused = False
        self._meta_cancelling = False
        # 수동 재개는 명시적 사용자 의도 — 재생 양보 상태도 함께 해제(우선).
        # 안 그러면 _ensure 의 양보 가드에 막혀 재개가 안 됨.
        self._meta_yielded_playback = False
        self._index_resume_timer.stop()
        self._style_meta_pause_btn(paused=False)  # 정지(⏸) 빨강
        self.meta_pause_btn.setToolTip(
            "백그라운드 인덱싱 일시정지\n"
            "재개 조건: 재개 버튼 클릭 또는 5분간 앱 미사용"
        )
        if self._is_background_meta_running():
            # 일시정지로 취소된 워커가 아직 종료 중 — 종료 콜백(_clear_background_meta_refs)
            # 이 완전 종료 후 재개한다(지금 _ensure 호출하면 '실행 중'이라 무시됨).
            self._meta_resume_pending = True
        else:
            self._ensure_background_meta_running()
        msg = "백그라운드 인덱싱 재개"
        if reason:
            msg += f" ({reason})"
        self._set_status(msg)

    # ─────────── 인덱싱 집중 모드 / 재생 중 양보 ───────────
    def _on_focus_index_toggled(self, checked: bool):
        """우하단 '인덱싱 집중' 체크박스. 체크 시 경고 팝업 후 재생 중에도 100% 인덱싱.
        해제 시 재생 중엔 백그라운드 메타 분석을 양보(디스크 여유 → 재생 매끄럽게)."""
        if checked:
            ret = QMessageBox.warning(
                self, "인덱싱 집중 모드",
                "재생 중에도 인덱싱을 100% 속도로 진행합니다.\n"
                "그동안 디스크를 꽉 써서 사운드 재생이 끊기거나 버벅일 수 있습니다.\n\n"
                "켤까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                self.focus_index_chk.blockSignals(True)
                self.focus_index_chk.setChecked(False)
                self.focus_index_chk.blockSignals(False)
                return
        self._config_data["index_focus_mode"] = checked
        self._save_config()
        if checked:
            # 집중 모드 ON — 양보 중이던 인덱싱 즉시 재개(100% 속도).
            self._index_resume_timer.stop()
            if self._meta_yielded_playback:
                self._meta_yielded_playback = False
                self._meta_cancelling = False
                if not self._meta_paused:
                    self._ensure_background_meta_running()
        elif self._playback_active:
            # 집중 모드 OFF + 재생 중 → 즉시 양보.
            self._yield_meta_for_playback()

    def _on_playback_for_index(self, path: str):
        """재생 상태 변화 → 집중 모드 OFF 면 인덱싱 양보/재개."""
        self._playback_active = bool(path)
        if self._config_data.get("index_focus_mode", False):
            return  # 집중 모드 — 양보 안 함(100% 인덱싱)
        if path:
            self._index_resume_timer.stop()
            self._yield_meta_for_playback()
        elif self._meta_yielded_playback:
            # 재생 멈춤 → 잠시 후 재개(연속 미리듣기 churn 방지)
            self._index_resume_timer.start()

    def _yield_meta_for_playback(self):
        """백그라운드 메타 분석만 양보(취소). 명시적 인덱싱(라이브러리 추가/재스캔)은
        건드리지 않음 — 그건 의도된 전경 작업이라 재개 훅이 없어 끊으면 안 됨."""
        if self._meta_yielded_playback:
            return
        if self._is_background_meta_running():
            self._meta_yielded_playback = True
            self._meta_cancelling = True   # 잔여 progress emit 무시
            self.manager.cancel()
            self.indexing_badge.setVisible(False)
            # 인디케이터는 숨기지 말고 즉시 '일시정지(재생 중)' 로 — 누적 숫자 유지.
            self._update_meta_indicator(self._meta_pending_n, active=False)

    def _resume_meta_after_playback(self):
        if not self._meta_yielded_playback:
            return
        if self._playback_active or self._config_data.get("index_focus_mode", False):
            return
        if self._is_background_meta_running():
            # 취소된 워커가 아직 종료 중 — 잠시 후 재시도
            self._index_resume_timer.start()
            return
        self._meta_yielded_playback = False
        self._meta_cancelling = False
        if not self._meta_paused:
            self._ensure_background_meta_running()

    def _show_settings(self):
        dlg = SettingsDialog(self, config=self._config_data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings(dlg.get_config())
            # 테마 적용 — 저장 시점에 반영 (라디오 즉시 미리보기 X).
            # apply_theme 는 alias 해석 + idempotent.
            chosen = dlg.selected_theme()
            self._on_theme_toggle(chosen)

    def _apply_settings(self, new_config: dict):
        self._config_data.update(new_config)
        self._save_config()
        # 하위 위젯에 설정 전달
        self.results.set_config(self._config_data)
        self.player.set_config(self._config_data)
        # 단축키 즉시 반영
        self._setup_shortcuts()
        # 검색 제한 반영을 위해 재검색
        self._do_search()
        self._set_status("설정이 저장되었습니다.")

    def _on_manual_play(self, path: str):
        """더블클릭 등 사용자 명시 재생 — auto_preview 설정 무시하고 항상 재생."""
        if path:
            self.results.set_pip(path, "loading")
            self.player.load_and_play(path, meta=self.results.get_meta_for_path(path))

    def _on_external_drag_dropped(self):
        """결과 테이블/히스토리 → DAW 등 외부로 drop 완료 시 호출 (드래그 시작 X).
        stop_on_drag 설정 (default True) 따라 재생 정지하되, 파형/파일명/메타는
        그대로 유지 (stop_playback_only)."""
        if self._config_data.get("stop_on_drag", True):
            self.player.stop_playback_only()

    def _on_results_reveal_in_browser(self, path: str):
        """결과 목록 우클릭 -> 라이브러리 브라우저에서 보기.
        활성 탭의 트리에 focus. 사용자 탭이고 경로가 탭 items 밖이면 focus 실패하지만
        조용히 무시 (사용자 의도가 모호 — 다른 탭 전환까지 강제하지 않음)."""
        folder = os.path.dirname(path)
        self.library_tabs.current_tree().focus_path(folder)

    def _on_results_add_to_blacklist(self, path: str):
        """결과 목록 우클릭 -> 이 라이브러리 블랙리스트에 추가.
        파일이 속한 최상위 라이브러리 루트를 찾아 블랙리스트에 추가합니다.
        """
        if not self.manager:
            return
        
        norm_path = os.path.normpath(path).lower()
        self._invalidate_search_signature()
        roots = self.manager.get_roots()
        matched_root = None
        
        # 가장 긴 매칭 루트를 찾음 (중첩 루트 고려)
        sorted_roots = sorted([r["path"] for r in roots], key=len, reverse=True)
        for r in sorted_roots:
            r_norm = os.path.normpath(r).lower()
            if norm_path.startswith(r_norm + os.sep.lower()) or norm_path == r_norm:
                matched_root = r
                break
                
        if matched_root:
            self.blacklist_panel.add_paths([matched_root])
        else:
            # 루트를 못 찾으면 상위 폴더라도 추가 (안전책)
            self.blacklist_panel.add_paths([os.path.dirname(path)])

    def _on_results_add_file_to_blacklist(self, paths: list):
        """결과 목록 우클릭 -> 개별 사운드(파일) 경로만 블랙리스트에 추가.
        DB query 가 정확일치(=)로 그 파일만 검색에서 제외 (라이브러리 전체 아님)."""
        if self.manager and paths:
            self.blacklist_panel.add_paths([p for p in paths if p])

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        central.setMouseTracking(True)
        v = QVBoxLayout(central)
        v.setContentsMargins(6, 6, 6, 6)

        # 상단: 앱 이름 + 라이브러리 버튼들 (목업과 동일한 슬림 헤더)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.setContentsMargins(8, 0, 4, 0)
        # 브랜드 마크 + 타이틀
        self.brand_mark = _BrandMark(size=16)
        top.addWidget(self.brand_mark)

        self.brand_name_label = QLabel("SoundField")
        self.brand_name_label.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 12px; font-weight: 700; "
            "letter-spacing: 0.08em; background: transparent;"
        )
        top.addWidget(self.brand_name_label)

        self.brand_sub_label = QLabel("YSG AUDIO LABS")
        # 더 검고 선명하게 (text_caps 사용)
        self.brand_sub_label.setStyleSheet(
            f"color: {COLORS['text_caps']}; font-size: 8.5px; font-weight: 700; "
            "letter-spacing: 0.12em; background: transparent;"
        )
        top.addWidget(self.brand_sub_label)

        self.brand_version_label = QLabel("V1.3.0")
        # 기존 서브라벨 채도 (text_secondary 사용)
        self.brand_version_label.setStyleSheet(
            f"color: {COLORS['text_secondary']}; font-size: 8.5px; font-weight: 500; "
            "letter-spacing: 0.08em; background: transparent;"
        )
        top.addWidget(self.brand_version_label)

        # 테마 토글은 환경설정 안 "테마" 탭으로 이동 (3중택1).
        top.addSpacing(8)

        # 환경설정 버튼 — 동일 paint 스타일 (외곽선/hover 애니메이션)
        self.settings_btn = _SettingsButton(size=20)
        self.settings_btn.setToolTip("환경설정 (Ctrl+P)")
        self.settings_btn.clicked.connect(self._show_settings)
        top.addWidget(self.settings_btn)

        self.global_loader = _GlobalLoader()
        top.addWidget(self.global_loader)
        top.addStretch(1)
        BTN_H = 24  # 목업 일치 — 슬림 헤더
        # 상단 액션 버튼 — 파일 브라우저 돋보기와 동일 결(투명+둥근 테두리+호버 색
        # 페이드). AnimButton 이 paintEvent 로 전부 그려 QSS 상속 싸움/구운 아이콘 제거.
        self.add_btn = AnimButton("라이브러리 추가", accent="accent", glyph="plus")
        self.add_btn.setFixedHeight(BTN_H)
        self.add_btn.setToolTip("새로운 폴더 경로를 라이브러리로 등록합니다.")
        self.add_btn.clicked.connect(self._add_library); top.addWidget(self.add_btn)
        # 빠른 갱신: 변경 감지 → 다이얼로그 → 확인 시 인덱싱
        self.quick_update_btn = AnimButton("빠른 갱신", accent="accent",
                                           glyph="refresh", glyph_color="#48a870")
        self.quick_update_btn.setFixedHeight(BTN_H)
        self.quick_update_btn.setToolTip(
            "최근 변경된 파일만 빠르게 감지합니다.\n"
            "새 파일/삭제된 파일만 처리되어 속도가 빠릅니다.\n"
            "메타데이터는 변경된 파일에만 다시 추출됩니다."
        )
        self.quick_update_btn.clicked.connect(lambda: self._do_quick_update())
        top.addWidget(self.quick_update_btn)
        # 전체 갱신: existing 비교 우회, 라이브러리 추가와 동일 hot path
        self.full_update_btn = AnimButton("전체 갱신", accent="accent",
                                          glyph="refresh", glyph_color="#c05050")
        self.full_update_btn.setFixedHeight(BTN_H)
        self.full_update_btn.setToolTip(
            "기존 인덱스 보존 + 모든 파일 강제 재스캔.\n"
            "라이브러리 추가와 동일한 속도. mtime 같으면 메타는 보존."
        )
        self.full_update_btn.clicked.connect(self._do_full_update)
        top.addWidget(self.full_update_btn)
        self.cancel_btn = AnimButton("취소", accent="#c44848", glyph="stop")
        self.cancel_btn.setFixedHeight(BTN_H)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip(
            "인덱싱 중지. 이미 스캔된 파일은 보관됨 — "
            "[전체 인덱스 업데이트] 다시 클릭하면 이어서 진행."
        )
        self.cancel_btn.clicked.connect(self._cancel_index); top.addWidget(self.cancel_btn)

        # 검색 인덱스 정합성 깨졌을 때만 표시 — 수동 복구 버튼
        self.fts_rebuild_btn = QPushButton("⚠ 검색 정리")
        self.fts_rebuild_btn.setObjectName("ftsRecovery")
        self.fts_rebuild_btn.setFixedHeight(BTN_H)
        self.fts_rebuild_btn.setToolTip(
            "검색 데이터가 실제 파일 목록과 어긋나 이 버튼이 나타났습니다.\n"
            "(보통 인덱싱 도중 앱이 꺼진 경우 — 일부 파일이 검색에 안 나올 수 있음)\n"
            "누르면 검색 데이터를 다시 만들어 파일 목록과 맞춥니다."
        )
        self.fts_rebuild_btn.setVisible(False)
        self.fts_rebuild_btn.clicked.connect(self._start_fts_rebuild)
        top.addWidget(self.fts_rebuild_btn)

        v.addLayout(top)

        self.main_splitter = _GripSplitter(Qt.Orientation.Horizontal, 16)

        side = QWidget()
        side.setMinimumWidth(200) # 트리 영역 최소 너비 상향
        side.setMouseTracking(True)
        sv = QVBoxLayout(side); sv.setContentsMargins(0, 0, 7, 0); sv.setSpacing(0)
        
        # 한 줄로 합친 헤더 — 좌측 '파일 브라우저'(가장 큰), 우측 '폴더 N'.
        # 기존 FolderTree 자체 column header bar 는 숨김 (setHeaderHidden) — 중복 제거.
        side_header = QHBoxLayout()
        side_header.setContentsMargins(8, 12, 12, 8)
        side_label = QLabel("파일 브라우저")
        side_label.setObjectName("categoryLabel")
        side_header.addWidget(side_label)
        side_header.addStretch()
        self.side_count = QLabel("경로 폴더 0")
        self.side_count.setObjectName("categoryCount")
        side_header.addWidget(self.side_count)
        sv.addLayout(side_header)

        # LibraryTabs — 탭별 별도 FolderTree 컨테이너. 시그널은 활성 탭 트리 것만
        # 외부로 전달. 'tree' 속성은 호환용 alias (focus_path 등 일부 직접 호출).
        self.library_tabs = LibraryTabs()
        self.library_tabs.setMinimumWidth(150)
        self.library_tabs.folderSelected.connect(self._on_folder_selected)
        self.library_tabs.foldersSelected.connect(self._on_folders_selected)
        self.library_tabs.favoriteAdded.connect(self._on_favorite_added)
        self.library_tabs.favoriteRemoved.connect(self._on_favorite_removed)
        self.library_tabs.rescanRequested.connect(self._on_rescan_request)
        self.library_tabs.rescanManyRequested.connect(self._on_rescan_many_request)
        self.library_tabs.fullRescanRequested.connect(self._on_full_rescan_request)
        self.library_tabs.fullRescanManyRequested.connect(self._on_full_rescan_many_request)
        self.library_tabs.removeRootRequested.connect(self._on_remove_root_request)
        self.library_tabs.removeRootsRequested.connect(self._on_remove_roots_request)
        self.library_tabs.purgeRootsRequested.connect(self._on_purge_roots_request)
        self.library_tabs.renameRequested.connect(self._on_folder_rename_request)
        self.library_tabs.userExpandedStateChanged.connect(self._save_config)
        self.library_tabs.rootOrderChangeRequested.connect(self._on_root_order_change_requested)
        self.library_tabs.tabsChanged.connect(self._save_config)
        # 탭 전환 시 — 검색 prefix 자동 필터(사용자/즐겨찾기 탭) 갱신.
        self.library_tabs.activeTabChanged.connect(self._on_active_tab_changed)
        self.tree = self.library_tabs  # 호환 alias

        # 블랙리스트 패널 — 사용자가 위로 사이즈 드래그 조절 가능 (QSplitter).
        # 접힘: blacklist 헤더만(~40px), tree 가 나머지. 펼침: 사용자 저장 사이즈.
        self.blacklist_panel = BlacklistPanel()
        self.blacklist_panel.set_manager(self.manager)
        self.library_tabs.addToBlacklistRequested.connect(self.blacklist_panel.add_paths)
        self.blacklist_panel.changed.connect(self._on_blacklist_changed)
        # 펼침/접힘 시 splitter 사이즈 자동 조정
        self.blacklist_panel.expandedChanged.connect(self._on_blacklist_expanded_changed)

        self.side_splitter = _GripSplitter(Qt.Orientation.Vertical, 6)
        self.side_splitter.setChildrenCollapsible(False)
        self.side_splitter.addWidget(self.tree)
        self.side_splitter.addWidget(self.blacklist_panel)
        self.side_splitter.setStretchFactor(0, 1)
        self.side_splitter.setStretchFactor(1, 0)
        self.side_splitter.splitterMoved.connect(self._on_side_splitter_moved)
        sv.addWidget(self.side_splitter, 1)
        # 접힘 상태 (default) — 핸들 드래그 비활성
        h = self.side_splitter.handle(1)
        if h is not None:
            h.setEnabled(self.blacklist_panel.is_expanded())
        # 트리 첫 빌드 시점부터 visibility 적용 — load_folders 가 캐시된 set 으로 자동 hide.
        self._sync_blacklist_to_tree()

        self.main_splitter.addWidget(side)

        right = QWidget()
        right.setMinimumWidth(600) # 메인 결과 영역 최소 너비 확보
        right.setMouseTracking(True)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 6, 4, 0)
        rv.setSpacing(4)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.multi = MultiSearch()
        self.multi.changed.connect(self._schedule_search)
        search_row.addWidget(self.multi, 3)
        self.lib_status = LibraryStatusInline()
        self.lib_status.detailRequested.connect(self._show_library_status)
        search_row.addWidget(self.lib_status, 1)
        rv.addLayout(search_row)

        filt = QHBoxLayout()
        filt.setSpacing(6)
        filt.addWidget(_control_label("LENGTH"))
        self.min_dur = _PlaceholderDoubleSpinBox("최소")
        self.min_dur.setDecimals(1)            # 0.1초 단위
        self.min_dur.setRange(0, 36000)
        self.min_dur.setSingleStep(0.1)
        self.min_dur.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.min_dur.valueChanged.connect(self._on_filter_changed); filt.addWidget(self.min_dur)
        filt.addWidget(_control_label("~"))
        self.max_dur = _PlaceholderDoubleSpinBox("최대")
        self.max_dur.setDecimals(1)            # 0.1초 단위
        self.max_dur.setRange(0, 36000)
        self.max_dur.setSingleStep(0.1)
        self.max_dur.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.max_dur.valueChanged.connect(self._on_filter_changed); filt.addWidget(self.max_dur)
        filt.addWidget(_control_label("SAMPLE RATE"))
        self.sr_combo = QComboBox()
        self.sr_combo.addItems(["전체", "44.1K", "48K", "88.2K", "96K", "192K"])
        _setup_dot_combo(self.sr_combo)
        block_value_wheel(self.sr_combo)   # 휠로 필터 값 실수 변경 방지
        self.sr_combo.currentIndexChanged.connect(self._on_filter_changed); filt.addWidget(self.sr_combo)
        filt.addWidget(_control_label("CHANNELS"))
        self.ch_combo = QComboBox()
        self.ch_combo.addItems([
            "전체", "MONO", "STEREO", "4CH", "5.1 / 6CH", "7채널 이상"])
        _setup_dot_combo(self.ch_combo)
        block_value_wheel(self.ch_combo)   # 휠로 필터 값 실수 변경 방지
        self.ch_combo.currentIndexChanged.connect(self._on_filter_changed); filt.addWidget(self.ch_combo)

        # 필터 초기화 버튼 — Length / Sample Rate / Channels 모두 default 로 복원.
        # 높이는 옆 ComboBox 의 sizeHint 와 동일하게 맞춤 (windows11 style 메트릭
        # + theme padding 에 따라 26~30px 가변 → 정수 하드코딩하면 어긋남).
        self.filter_reset_btn = _FilterResetButton()
        self.filter_reset_btn.setToolTip(
            "필터 초기화\n"
            "길이/샘플레이트/채널 필터를 모두 기본값(전체)으로 복원합니다."
        )
        self.filter_reset_btn.clicked.connect(self._reset_filters)
        # 높이는 옆 CHANNELS 콤보와 동일하게 (정사각형 → 폭도 동일). 하드코딩하면
        # theme padding/스타일 메트릭 변화 시 어긋나므로 sizeHint 추종 (사용자 요청).
        _h = self.ch_combo.sizeHint().height()
        self.filter_reset_btn.setFixedSize(_h, _h)
        # LENGTH 스핀박스 크기 고정 — 높이는 콤보와 동일.
        # 폭은 (정수)QSpinBox 기준으로 고정: QDoubleSpinBox 는 소수 자리("36000.0")
        # 때문에 sizeHint 폭이 넓어져 박스가 기존보다 길어 보였다. 같은 범위의 정수
        # 스핀박스 폭으로 맞춰 기존(0.1초 도입 전)과 동일한 폭으로 되돌린다.
        _ref_sb = QSpinBox()
        _ref_sb.setRange(0, 36000)
        _ref_sb.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        _dur_w = _ref_sb.sizeHint().width()
        _ref_sb.deleteLater()
        for _sb in (self.min_dur, self.max_dur):
            _sb.setFixedHeight(_h)
            _sb.setFixedWidth(_dur_w)
            block_value_wheel(_sb)   # 휠로 길이 값 실수 변경 방지
            # 내부 lineEdit 의 이중 padding 제거 — 디센던트 QSS(QSpinBox QLineEdit)는
            # 스핀박스 내부 lineEdit 에 안 먹는 경우가 있어 인스턴스에 직접 지정.
            # 이게 빠지면 스핀박스 padding(8) + lineEdit padding(8) = 텍스트가 콤보보다
            # 두 배 들여써져 '첫 글자 여백'이 커 보였다.
            _sb.lineEdit().setStyleSheet("padding:0; border:none; background:transparent;")
        self._update_filter_reset_btn_style()  # 초기 상태(연한 초록) 적용
        filt.addWidget(self.filter_reset_btn)

        filt.addStretch()
        # 히스토리 토글 — 필터 행 우측, Dur/SR/Ch 와 같은 높이
        self.history_toggle_btn = AnimButton("히스토리 ◀")
        self.history_toggle_btn.setFixedHeight(28)
        self.history_toggle_btn.setCheckable(True)
        self.history_toggle_btn.setToolTip("재생 히스토리 열기/닫기\n단축키: H")
        self.history_toggle_btn.clicked.connect(self._toggle_history_drawer)
        filt.addWidget(self.history_toggle_btn)
        rv.addLayout(filt)

        # 결과 + 히스토리(우측 드로어) 가로 배치
        self.results = ResultsTable()
        self.results.setMinimumHeight(200) # 결과창 최소 높이 확보
        self.results.fileActivated.connect(self._on_file_activated)
        self.results.manualPlayRequested.connect(self._on_manual_play)
        self.results.playToggleRequested.connect(self._on_play_toggle)
        self.results.revealInBrowserRequested.connect(self._on_results_reveal_in_browser)
        self.results.addToBlacklistRequested.connect(self._on_results_add_to_blacklist)
        self.results.addFileToBlacklistRequested.connect(self._on_results_add_file_to_blacklist)
        self.results.dragDropped.connect(self._on_external_drag_dropped)

        # 결과 + 히스토리 — splitter 대신 QHBoxLayout 으로 가로 배치. history_panel 의
        # maximumWidth 애니메이션으로 슬라이드 인/아웃. layout 기반이라 history_panel 이
        # 늘어나면 results 가 자동으로 좁아져 스크롤바가 panel 좌측에 노출됨 (overlay
        # 일 때는 패널이 results 스크롤바를 가렸음).
        self.results_wrap = QWidget()
        _wl = QHBoxLayout(self.results_wrap)
        _wl.setContentsMargins(0, 0, 0, 7)
        _wl.setSpacing(4)
        _wl.addWidget(self.results, 1)

        self.history_panel = HistoryPanel()
        self.history_panel.itemPicked.connect(self._on_history_pick)
        self.history_panel.clearRequested.connect(self._on_history_clear)
        self.history_panel.collapseRequested.connect(self._toggle_history_drawer)
        self.history_panel.dragDropped.connect(self._on_external_drag_dropped)
        self.history_panel.widthChanged.connect(self._on_history_width_changed)
        _wl.addWidget(self.history_panel, 0)

        # 결과 / 플레이어 세로 splitter — 플레이어는 풀폭
        self.right_splitter = _GripSplitter(Qt.Orientation.Vertical, 20)
        self.right_splitter.addWidget(self.results_wrap)

        self.player = PlayerWidget()
        self.player.setMinimumHeight(180) # 플레이어 영역 최소 높이 상향
        self.player.historyChanged.connect(self._refresh_history_panel)
        self.player.playingChanged.connect(self.results.set_playing_path)
        self.player.playingChanged.connect(self._on_playback_for_index)
        # 세그먼트 헤더 토글 시 결과창 ↔ 플레이어 사이 SEGMENT_HEADER_H 이동
        # → 헤더가 파형 위로 오버레이되지 않고 별도 영역으로 분리되면서도
        #   파형 body 높이는 그대로 유지됨.
        self.player.wave.segmentHeaderVisibilityChanged.connect(
            self._on_seg_header_visibility_changed
        )
        self.right_splitter.addWidget(self.player)
        # 자식 추가 후 설정해야 안정적으로 동작함
        self.right_splitter.setCollapsible(0, False)
        self.right_splitter.setCollapsible(1, False)
        self.right_splitter.setStretchFactor(0, 4)
        self.right_splitter.setStretchFactor(1, 1)
        self.right_splitter.setSizes([550, 160])
        rv.addWidget(self.right_splitter, 1)

        self.main_splitter.addWidget(right)
        self.main_splitter.setCollapsible(0, False)
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([250, 1250])
        v.addWidget(self.main_splitter, 1)

        # 중앙 모달 로딩 오버레이 — 트리 갱신처럼 UI 가 사실상 freeze 되는 작업용
        self.center_loader = _CenterLoaderOverlay(central)
        self.center_loader.setGeometry(central.rect())
        # 탭 추가/삭제/중복 피드백용 중앙 토스트 (로더와 동일 디자인, 비차단)
        self.center_toast = _CenterToast(central)
        self.center_toast.setGeometry(central.rect())
        self.library_tabs.feedbackRequested.connect(self.center_toast.show_message)

        sb = QStatusBar(); self.setStatusBar(sb)
        self.indexing_badge = QLabel("")
        self.indexing_badge.setVisible(False)
        self.indexing_badge.setStyleSheet(_badge_style(COLORS["danger"]))
        sb.addWidget(self.indexing_badge)

        self.status_label = QLabel("")
        # 긴 메시지가 윈도우 minimum width를 끌어올리지 않도록 Ignored 정책
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)
        self.status_label.setMinimumWidth(0)
        sb.addWidget(self.status_label, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumWidth(360); self.progress_bar.setMaximumWidth(420)
        self.progress_bar.setTextVisible(True)
        sb.addPermanentWidget(self.progress_bar)

        self.count_label = QLabel("")
        self.count_label.setObjectName("countLabel")
        self.count_label.setStyleSheet(self._count_label_style())
        sb.addPermanentWidget(self.count_label)

        # 메타 분석 인디케이터 — Phase2 진행 중 또는 미완료 잔량 있을 때 표시
        self.meta_spinner = _IndexSpinner(size=14)
        sb.addPermanentWidget(self.meta_spinner)
        self.meta_indicator = QLabel("")
        self.meta_indicator.setObjectName("metaIndicator")
        self.meta_indicator.setStyleSheet(self._meta_indicator_style())
        self.meta_indicator.setMinimumWidth(280)
        self.meta_indicator.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.meta_indicator.setVisible(False)
        # 텍스트 길이에 맞춰 가변 폭 — 길어지면 늘어나고 짧아지면 줄어듦
        self.meta_indicator.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        sb.addPermanentWidget(self.meta_indicator)

        # 백그라운드 인덱싱 일시정지/재개 버튼 — 인디케이터 옆.
        # 텍스트 X (Unicode ⏸/▶ 는 폰트 따라 깨지거나 배경 박스 생김) → QPainter 아이콘.
        self.meta_pause_btn = QPushButton("")
        self.meta_pause_btn.setObjectName("icon")
        self.meta_pause_btn.setFixedSize(22, 22)
        self.meta_pause_btn.setIconSize(QSize(12, 12))
        self.meta_pause_btn.setToolTip(
            "백그라운드 인덱싱 일시정지\n"
            "재개 조건: 재개 버튼 클릭 또는 5분간 앱 미사용"
        )
        self.meta_pause_btn.setVisible(False)
        self.meta_pause_btn.clicked.connect(self._on_meta_pause_clicked)
        self._style_meta_pause_btn(paused=False)  # 색/아이콘/사이징 적용(정지=빨강)
        sb.addPermanentWidget(self.meta_pause_btn)

        # 인덱싱 집중 모드 체크박스 — 우하단 메타 인디케이터 옆.
        # 체크: 재생 중에도 인덱싱 100% (재생 끊길 수 있음 — 토글 시 경고 팝업)
        # 해제(기본): 재생 중엔 인덱싱 양보 → 재생 안 끊김.
        self.focus_index_chk = QCheckBox("인덱싱 집중")
        self.focus_index_chk.setToolTip(
            "체크: 재생 중에도 인덱싱을 100% 속도로 — 인덱싱 빠름, 재생이 끊길 수 있음\n"
            "해제: 재생 중엔 인덱싱을 잠시 양보 — 재생이 매끄러움 (기본)"
        )
        self.focus_index_chk.toggled.connect(self._on_focus_index_toggled)
        sb.addPermanentWidget(self.focus_index_chk)
        self._meta_dot_state = 0  # 0..2 (애니메이션용)
        self._meta_pending_n = 0
        self._meta_active = False  # Phase2 진행 중 여부
        self._meta_done_n = 0
        self._meta_total_n = 0
        # 진행도 분모 = '미완료가 남은 라이브러리 루트들의 전체 파일 수' (DB 기준).
        # 스캔한 라이브러리 범위만 잡히고(전체 DB 아님), DB 에서 다시 계산되므로
        # 앱 재시작/양보·재개에도 그대로 이어진다(0 리셋 안 됨).
        # _meta_total_files > 0 이면 '진행 중 세션' (전체수 캐시). 완료 시 0 으로 리셋.
        self._meta_total_files = 0    # 현재 세션 전체수 (스코프 루트들 count_files 합)
        self._meta_base_done = 0      # 이번 워커 run 시작 시점의 누적 완료 수
        self._last_meta_search_refresh = 0.0
        self._meta_anim_timer = QTimer(self)
        self._meta_anim_timer.setInterval(400)
        self._meta_anim_timer.timeout.connect(self._tick_meta_indicator)

        self._blink_state = False
        self._blink_timer = QTimer(); self._blink_timer.setInterval(600); self._blink_timer.timeout.connect(self._blink_badge)

        # 검색은 worker 에서 취소 가능한 batch 스트리밍으로 처리한다.
        # 입력 지연은 중복 key event 를 한 프레임권으로만 묶고, 오래 기다리지 않는다.
        self._search_timer = QTimer(); self._search_timer.setSingleShot(True); self._search_timer.setInterval(40); self._search_timer.timeout.connect(self._do_search)
        self._last_search_signature = None

        # 검색 워커 (상주 스레드) — _ensure_search_worker 에서 1회 생성
        self._search_gen = _SearchGen()
        self._search_thread: Optional[QThread] = None
        self._search_worker: Optional[_SearchWorker] = None
        self._pending_search = None  # (gen, signature, scope, streamed_count)
        # 쿼리가 길어질 때만(400ms 초과) "검색 중" 표시 — 평소엔 깜빡임 없이 조용히
        self._search_busy_timer = QTimer(self)
        self._search_busy_timer.setSingleShot(True)
        self._search_busy_timer.setInterval(400)
        self._search_busy_timer.timeout.connect(self._on_search_busy_timeout)

        self._progress_poll_timer = QTimer(self)
        self._progress_poll_timer.setInterval(250)
        self._progress_poll_timer.timeout.connect(self._poll_progress)

        # 인덱싱 중에 라이브러리 현황 글로우 색을 주기적으로 갱신
        # (pending 줄어들수록 주황 → 초록 자동 전환)
        # 2초마다 30 roots × Path.exists + count_metadata_status_under 워커
        # trigger 는 NAS IO/sqlite 부담 + 워커 중복 호출 빈번. 5초면 라이브러리
        # 현황 글로우 색 갱신 체감엔 충분.
        self._lib_status_refresh_timer = QTimer(self)
        self._lib_status_refresh_timer.setInterval(5000)
        self._lib_status_refresh_timer.timeout.connect(self._refresh_lib_status_only)

        # 설정 저장용 디바운스 타이머 (잦은 IO 방지)
        self._config_save_timer = QTimer(self)
        self._config_save_timer.setSingleShot(True)
        self._config_save_timer.setInterval(1000)
        self._config_save_timer.timeout.connect(self._actual_save_config)

        # 레이아웃 변경 시 자동 저장 연결
        self.main_splitter.splitterMoved.connect(self._save_config)
        self.right_splitter.splitterMoved.connect(self._save_config)
        self.results.horizontalHeader().sectionResized.connect(self._save_config)
        self.results.horizontalHeader().sectionMoved.connect(self._save_config)
        self.results.horizontalHeader().columnCloseRequested.connect(self._save_config)
        # 볼륨/스피드도 레이아웃처럼 위젯 상태로 기억
        self.player.vol_slider.valueChanged.connect(self._save_config)
        self.player.speed_slider.valueChanged.connect(self._save_config)
        # 검색어/길이/샘플레이트/채널 필터도 재시작 후 복원되도록 위젯 상태로 기억
        self.multi.changed.connect(self._save_config)
        self.min_dur.valueChanged.connect(self._save_config)
        self.max_dur.valueChanged.connect(self._save_config)
        self.sr_combo.currentIndexChanged.connect(self._save_config)
        self.ch_combo.currentIndexChanged.connect(self._save_config)

        # 창 경계 제한용 타이머 (실시간 이동 중 충돌/흔들림 방지)
        self._constrain_timer = QTimer(self)
        self._constrain_timer.setSingleShot(True)
        self._constrain_timer.setInterval(2000)  # 이동 종료 2초 후 체크
        self._constrain_timer.timeout.connect(self._constrain_to_screen)

    def _load_config(self):
        """기존 단일 라이브러리 설정 → roots 테이블로 마이그레이션 (1회).
        컬럼 레이아웃(순서/너비/표시여부) 복원.
        """
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                
                # 마이그레이션 및 기본 레이아웃 복원
                old_root = data.get("library_root", "").strip()
                if old_root and not self.manager.get_roots():
                    self.manager.add_root(old_root)
                
                geom = data.get("geometry")
                if geom: self.restoreGeometry(QByteArray.fromBase64(geom.encode()))
                state = data.get("state")
                if state: self.restoreState(QByteArray.fromBase64(state.encode()))
                
                ms = data.get("main_splitter")
                if ms: self.main_splitter.restoreState(QByteArray.fromBase64(ms.encode()))
                rs = data.get("right_splitter")
                if rs: self.right_splitter.restoreState(QByteArray.fromBase64(rs.encode()))
                # top_h_splitter 는 legacy — overlay 전환 후 더 이상 사용 안 함

                hw = data.get("history_width")
                if isinstance(hw, int) and hw > 0:
                    self._history_expanded_width = hw
                
                # 히스토리 창 가시성 복원
                if data.get("history_visible", False):
                    # _toggle_history_drawer 는 현재 상태를 반전시키므로, 
                    # 기본이 닫힌 상태라면 한 번 호출해서 열어줌.
                    self._toggle_history_drawer()

                cols = data.get("columns")
                if cols: self.results.apply_column_layout(cols)

                # 볼륨/스피드 복원 — setValue 가 valueChanged 발생시켜 오디오/레이블까지 적용
                vol = data.get("volume_pos1k")  # 0~1000 스케일
                if not isinstance(vol, int):
                    legacy = data.get("volume")  # 레거시 0~100 스케일 → ×10
                    vol = legacy * 10 if isinstance(legacy, int) else None
                if isinstance(vol, int):
                    self.player.vol_slider.setValue(max(0, min(1000, vol)))
                rate = data.get("speed_rate")
                if rate is None:
                    # 레거시 정수 스케일 마이그레이션: 5~20(0.1단위) 또는 50~200(0.01단위)
                    spd = data.get("speed")
                    if isinstance(spd, int):
                        rate = spd / 10.0 if spd <= 20 else spd / 100.0
                if isinstance(rate, (int, float)):
                    rate = max(0.5, min(2.0, rate))
                    self.player.speed_slider.setValue(round(100 * math.log2(rate)))
                
                aliases = data.get("folder_display_names")
                if isinstance(aliases, dict):
                    self._folder_display_names = {
                        os.path.normpath(k).rstrip("\\/").lower(): str(v)
                        for k, v in aliases.items() if str(v).strip()
                    }
                self._favorites = data.get("favorites", [])
                
                # 환경설정 데이터 복원
                for key in self._config_data:
                    if key in data:
                        if isinstance(self._config_data[key], dict) and isinstance(data[key], dict):
                            self._config_data[key].update(data[key])
                        else:
                            self._config_data[key] = data[key]
                
                # 하위 위젯 동기화
                self.results.set_config(self._config_data)
                self.player.set_config(self._config_data)
                # 인덱싱 집중 모드 체크박스 상태 복원 (팝업/적용 없이 표시만)
                self.focus_index_chk.blockSignals(True)
                self.focus_index_chk.setChecked(bool(self._config_data.get("index_focus_mode", False)))
                self.focus_index_chk.blockSignals(False)
                
                # 펼침 상태는 재시작 간 복원 안 함(정책: 항상 기본 깊이). 대신 마지막
                # 선택 폴더만 복원 — 트리 데이터가 채워진 뒤 한 번 select_saved.
                sel = data.get("last_selection")
                if isinstance(sel, dict) and sel.get("path"):
                    self._pending_last_selection = (sel.get("tab") or "",
                                                    str(sel.get("path")))

                saved_tabs = data.get("tabs")
                if isinstance(saved_tabs, list):
                    self.library_tabs.load_user_tabs(saved_tabs)

                # 검색어/길이/샘플레이트/채널 필터 복원
                filters = data.get("filters")
                if isinstance(filters, dict):
                    self._restore_filters(filters)
        except Exception as e:
            logger.warning(f"설정 로드 실패: {e}")

    def _restore_filters(self, filters: dict):
        """재시작 후 검색어/길이/샘플레이트/채널 필터 복원.
        콤보/스핀은 blockSignals 로 중복 검색·저장을 막고, 마지막에 한 번만
        _schedule_search 로 복원된 필터 기준 결과를 다시 띄운다."""
        for w, key in ((self.min_dur, "min_dur"), (self.max_dur, "max_dur")):
            v = filters.get(key)
            if isinstance(v, (int, float)):
                w.blockSignals(True)
                try:
                    w.setValue(max(0.0, min(36000.0, float(v))))
                finally:
                    w.blockSignals(False)
        for w, key in ((self.sr_combo, "sample_rate"), (self.ch_combo, "channels")):
            idx = filters.get(key)
            if isinstance(idx, int) and 0 <= idx < w.count():
                w.blockSignals(True)
                try:
                    w.setCurrentIndex(idx)
                finally:
                    w.blockSignals(False)
        matchers = filters.get("matchers")
        if isinstance(matchers, list):
            self.multi.set_state(matchers)
        # 위젯 신호를 막아둔 채 값만 넣었으므로, 복원된 필터 기준으로 결과를 1회 갱신.
        self._schedule_search()

    def _save_config(self):
        """설정 저장을 예약 (디바운스)."""
        self._config_save_timer.start()

    def _actual_save_config(self):
        """실제 디스크에 설정 저장."""
        try:
            cfg = {
                "geometry": self.saveGeometry().toBase64().data().decode(),
                "state": self.saveState().toBase64().data().decode(),
                "main_splitter": self.main_splitter.saveState().toBase64().data().decode(),
                "right_splitter": self.right_splitter.saveState().toBase64().data().decode(),
                "history_width": int(self._history_expanded_width),
                "history_visible": self.history_panel.is_expanded(),
                "columns": self.results.column_layout(),
                "volume_pos1k": self.player.vol_slider.value(),  # 0~1000 스케일
                "speed_rate": self.player._playback_rate,
                "folder_display_names": self._folder_display_names,
                "favorites": self._favorites,
                "tabs": self.library_tabs.get_user_tabs_for_save(),
                # 펼침 상태는 재시작 간 저장 안 함(정책: 항상 기본 깊이로 시작). 대신
                # 마지막 선택 폴더(탭+경로)만 저장 → 재시작 시 그 경로만 펼쳐 선택 복원.
                "last_selection": {
                    "tab": self.library_tabs.active_tab().get("name", ""),
                    "path": getattr(self, "_current_prefix", "") or "",
                },
                "theme": current_theme(),
                "filters": {
                    "matchers": self.multi.get_state(),
                    "min_dur": self.min_dur.value(),
                    "max_dur": self.max_dur.value(),
                    "sample_rate": self.sr_combo.currentIndex(),
                    "channels": self.ch_combo.currentIndex(),
                },
            }
            cfg.update(self._config_data)
            CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"설정 저장 실패: {e}")

    def _setup_shortcuts(self):
        sc = self._config_data.get("shortcuts", {})
        
        if hasattr(self, "_space_shortcut"): self._space_shortcut.deleteLater()
        self._space_shortcut = QShortcut(QKeySequence(sc.get("play_pause", "Space")), self)
        self._space_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._space_shortcut.activated.connect(self._toggle_selected_playback)

        if hasattr(self, "_seg_shortcut"): self._seg_shortcut.deleteLater()
        self._seg_shortcut = QShortcut(QKeySequence(sc.get("toggle_segments", "S")), self)
        self._seg_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._seg_shortcut.activated.connect(self.player.toggle_segments)

        if hasattr(self, "_hist_shortcut"): self._hist_shortcut.deleteLater()
        self._hist_shortcut = QShortcut(QKeySequence(sc.get("toggle_history", "H")), self)
        self._hist_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._hist_shortcut.activated.connect(self._toggle_history_drawer)
        
        if hasattr(self, "_settings_shortcut"): self._settings_shortcut.deleteLater()
        self._settings_shortcut = QShortcut(QKeySequence(sc.get("open_settings", "Ctrl+P")), self)
        self._settings_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._settings_shortcut.activated.connect(self._show_settings)

        if hasattr(self, "_seek_start_shortcut"): self._seek_start_shortcut.deleteLater()
        self._seek_start_shortcut = QShortcut(QKeySequence(sc.get("seek_to_start", "Home")), self)
        self._seek_start_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._seek_start_shortcut.activated.connect(self.player.seek_to_start)

        if hasattr(self, "_loop_shortcut"): self._loop_shortcut.deleteLater()
        self._loop_shortcut = QShortcut(QKeySequence(sc.get("toggle_loop", "R")), self)
        self._loop_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._loop_shortcut.activated.connect(self.player.toggle_loop)

    def _on_theme_toggle(self, new_theme: str):
        """테마 전환 — QSS 재적용 + paintEvent 위젯 invalidate + 인라인 스타일 재적용."""
        from PyQt6.QtWidgets import QApplication
        apply_theme(new_theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss())
        self._reapply_inline_styles()
        # 파형 위젯의 캐시된 그라데이션/pixmap 무효화
        pw = getattr(self, "player", None)
        wf = getattr(pw, "wave", None) if pw else None
        if wf is not None and hasattr(wf, "apply_theme_change"):
            wf.apply_theme_change()
        self._save_config()

    def _count_label_style(self) -> str:
        # light(베이지): 다크브라운 accent / neon(dark): 밝은 블루 / neutral(grey): 무채색 accent
        t = current_theme()
        if t == "light":
            col = COLORS["accent"]
        elif t == "grey":
            col = COLORS["text"]   # 무채색 톤 유지
        else:
            col = "#9bb4ff"
        # 폰트 굵기/자간 지정 X — 하단 다른 상태 텍스트(결과 수/체크박스)와 동일 렌더링
        return f"color:{col}; padding:2px 10px;"

    def _meta_indicator_style(self) -> str:
        # 다크/라이트 통일 주황. 폰트는 다른 하단 상태 메시지(결과 수/체크박스)와 통일 —
        # 별도 font-size 제거(기본 상속), letter-spacing 도 count_label 과 동일(0.5px).
        return f"color:{_INCOMPLETE_COLOR_HEX}; padding:2px 10px;"

    def _reapply_inline_styles(self):
        """COLORS dict 시점값으로 박힌 setStyleSheet 호출들을 새 색으로 재호출.
        인라인 스타일을 쓴 라벨/배지들 한 곳에 모아 토글 시 일괄 갱신."""
        if hasattr(self, "count_label"):
            self.count_label.setStyleSheet(self._count_label_style())
        if hasattr(self, "min_dur"):
            self._update_filter_widget_styles()  # 활성 필터 틴트 — 새 accent 추종
        if hasattr(self, "meta_indicator"):
            self.meta_indicator.setStyleSheet(self._meta_indicator_style())
        if hasattr(self, "indexing_badge"):
            self.indexing_badge.setStyleSheet(_badge_style(COLORS["danger"]))
        if hasattr(self, "global_loader"):
            self.global_loader.apply_theme_change()
        if hasattr(self, "brand_name_label"):
            self.brand_name_label.setStyleSheet(
                f"color: {COLORS['text']}; font-size: 12px; font-weight: 700; "
                "letter-spacing: 0.08em; background: transparent;"
            )
        if hasattr(self, "brand_sub_label"):
            # 더 검고 선명하게 (text_caps)
            self.brand_sub_label.setStyleSheet(
                f"color: {COLORS['text_caps']}; font-size: 8.5px; font-weight: 700; "
                "letter-spacing: 0.12em; background: transparent;"
            )
        if hasattr(self, "brand_version_label"):
            # 기존 서브라벨 채도 (text_secondary)
            self.brand_version_label.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 8.5px; font-weight: 500; "
                "letter-spacing: 0.08em; background: transparent;"
            )
        if hasattr(self, "count_lbl"):
            self.count_lbl.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-size: 11px;"
            )
        # paintEvent 위젯들 (brand_mark, theme_toggle, indexing_badge 등) 강제 repaint
        for w in self.findChildren(QWidget):
            w.update()

    def moveEvent(self, event):
        super().moveEvent(event)
        # 스냅/이동 중 실시간 보정은 OS와 충돌하여 흔들림 유발. 
        # 이동 종료 2초 후에만 위치를 보정하도록 지연 처리.
        self._constrain_timer.start()
        self._save_config()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 리사이징 시에도 즉시 보정 대신 지연 보정.
        self._constrain_timer.start()
        self._save_config()

    def _constrain_to_screen(self):
        """창이 모든 모니터(가상 데스크탑) 밖으로 완전히 나가는 것만 방지.
        멀티모니터 이동을 막지 않도록 각 화면 단일 기준이 아닌 전체 합으로 판정."""
        if self.isMaximized() or self.isMinimized() or self.isFullScreen():
            return

        screens = QGuiApplication.screens()
        if not screens:
            return

        # 이전 버전에서 영구 적용했을 수 있는 maximumSize 제한 해제
        # (QWIDGETSIZE_MAX = 16777215)
        if self.maximumWidth() < 16777215 or self.maximumHeight() < 16777215:
            self.setMaximumSize(16777215, 16777215)

        frame_geo = self.frameGeometry()

        # 1. 사이즈 제한: 창 중심이 속한 (또는 가장 많이 겹치는) 스크린 기준으로만
        # 1회성 resize 적용. setMaximumSize는 사용하지 않음 — 큰 모니터로 옮겼을 때
        # 영구히 작은 크기에 갇히는 버그 방지.
        center = frame_geo.center()
        target = None
        for s in screens:
            if s.availableGeometry().contains(center):
                target = s
                break
        if target is None:
            best_area = -1
            for s in screens:
                inter = s.availableGeometry().intersected(frame_geo)
                area = inter.width() * inter.height()
                if area > best_area:
                    best_area = area
                    target = s
        if target is None:
            target = self.screen() or screens[0]

        screen_geo = target.availableGeometry()
        new_w = min(frame_geo.width(), screen_geo.width())
        new_h = min(frame_geo.height(), screen_geo.height())
        if new_w != frame_geo.width() or new_h != frame_geo.height():
            self.resize(new_w, new_h)
            frame_geo = self.frameGeometry()

        # 2. 위치 제한: 모든 스크린을 합친 가상 데스크탑 기준
        virtual = QRect()
        for s in screens:
            virtual = virtual.united(s.availableGeometry())

        new_x = frame_geo.x()
        new_y = frame_geo.y()
        margin = 40  # 최소 이 정도는 보여야 사용자가 다시 잡을 수 있음

        # 제목 표시줄이 가상 데스크탑 위로 나가는 것만 막음
        if frame_geo.top() < virtual.top():
            new_y = virtual.top()
        elif frame_geo.top() > virtual.bottom() - margin:
            new_y = virtual.bottom() - frame_geo.height()

        if frame_geo.right() < virtual.left() + margin:
            new_x = virtual.left()
        elif frame_geo.left() > virtual.right() - margin:
            new_x = virtual.right() - frame_geo.width()

        if new_x != frame_geo.x() or new_y != frame_geo.y():
            self.move(new_x, new_y)

    def _show_library_status(self):
        dlg = LibraryStatusDialog(self.manager, self)
        dlg.requestScopedRetry.connect(self._start_scoped_retry)
        dlg.exec()
        self.db = self.manager.open_db_for_search()
        self.lib_status.refresh_async(self.manager)
        # 총 파일수 라벨은 아래 _refresh_tree_counts_async 완료 시 비동기 갱신(_on_tree_counts_done).
        # 메인 스레드 동기 count_files()(100만행 ~1s 프리즈) 제거.
        # 라이브러리 현황의 실패/대기 항목 삭제는 루트 구조를 바꾸지 않는다.
        # 전체 트리 재빌드 대신 기존 트리의 카운트만 비동기로 갱신해 중앙 로더와
        # 불필요한 UI 재구성을 피한다. 루트 추가/삭제나 인덱싱 완료는 별도 경로에서
        # _reload_tree_async()를 호출한다.
        self._refresh_tree_counts_async()

    def _is_background_meta_running(self) -> bool:
        return bool(self.meta_thread and self.meta_thread.isRunning())

    def _retry_failed_on_parser_upgrade(self):
        """메타 분석기 버전(META_PARSER_VERSION) 이 올라갔으면 이전 실패분을 1회 재분석 큐로.
        DB meta 에 버전을 남겨 같은 버전에서 재실행 시엔 아무것도 하지 않는다."""
        from app.metadata import META_PARSER_VERSION
        cur = str(META_PARSER_VERSION)
        try:
            db = Database(str(self.manager.db_path))   # 쓰기 필요 — self.db 는 read-only
            prev = db.get_meta("meta_parser_version")
            if prev == cur:
                return
            # v2 전환 1회 한정: 분석 실패 때문에 '제거'했던 항목의 제거 표시를 해제.
            # (이후 버전 상승에서는 사용자가 제거한 항목을 되살리지 않는다.)
            n_restored = db.restore_removed_failed() if prev is None else 0
            n = db.reset_all_failed_metadata()
            db.set_meta("meta_parser_version", cur)
        except Exception:
            logger.exception("분석기 버전 상승 재시도 준비 실패")
            return
        if n:
            msg = f"메타 분석 기능이 개선됐습니다 — 이전에 실패한 {n:,}개 파일을 다시 분석합니다"
            if n_restored:
                msg += f" (제거해뒀던 {n_restored:,}개 복원)"
            self._set_status(msg)

    def _startup_resume_indexing(self):
        """재시작 시 진입점 — 처음 실행처럼 동작시킨다.
        1단계(검색 색인)가 강제 종료로 덜 만들어졌으면(fts_stale) 앱을 잠그고
        (중앙 로더) 그 색인을 마저 완성한 '뒤에야' 2단계(백그라운드 메타)로 넘어간다.
        (이전엔 곧장 2단계로 가서 검색 색인이 덜 된 채 남았음.)"""
        try:
            stale = bool(self.db.is_fts_stale())
        except Exception:
            stale = False
        # 이미 인덱싱 중이면 표적 수리는 IndexBusy 로 막히므로 시도하지 않고,
        # 정상 흐름에 맡긴다(억제 플래그만 해제).
        if not stale or self.manager.is_indexing() \
                or getattr(self, "_fts_repairing", False) \
                or getattr(self, "_fts_rebuilding", False):
            self._startup_resume_pending = False
            self._ensure_background_meta_running()
            return
        # 1단계 검색 색인 마무리 — 강제 블로킹 (사용자 요구: 처음 실행처럼).
        self._fts_repairing = True
        self.center_loader.start("fts_finish", "검색 색인 마무리 중...")
        self.center_loader.set_progress(
            "fts_finish", "덜 만들어진 검색 색인을 채우는 중입니다 — 잠시만요.", -1)
        # 하단 상태바에도 즉시 표시 — 로더만 뜨고 아래가 비어 답답하던 문제 해소.
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("검색 색인 마무리 · 점검 중...")
        self._set_status("검색 색인 마무리 — 덜 만들어진 부분을 점검 중입니다. 잠시만 기다려 주세요.")
        worker = _FtsRepairWorker(self.manager)
        thread = QThread()   # 부모 X — _hold_bg_thread 로 수명 유지
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_startup_fts_finish_progress)
        worker.finished.connect(self._on_startup_fts_finish_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_startup_fts_finish_progress(self, done: int, total: int):
        """검색 색인 마무리 진행 — 중앙 로더 게이지 + 하단 상태바 동시 갱신."""
        if total <= 0:
            # 점검 단계 — 채울 대상 집계 중(불확정 마퀴)
            self.center_loader.set_progress("fts_finish", "덜 만들어진 검색 색인을 점검 중...", -1)
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("검색 색인 마무리 · 점검 중...")
            self._set_status("검색 색인 마무리 — 덜 만들어진 부분을 점검 중입니다. 잠시만 기다려 주세요.")
            return
        pct = min(100, done * 100 // max(1, total))
        self.center_loader.set_progress(
            "fts_finish", f"검색 색인 채우는 중 {pct}% ({done:,}/{total:,})", pct)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        self.progress_bar.setFormat(f"검색 색인 마무리 · {pct}% ({done:,}/{total:,})")
        self._set_status(
            f"검색 색인 마무리 중 — {done:,}/{total:,} ({pct}%). 다 되면 자동으로 메타 분석으로 넘어갑니다.")

    def _on_startup_fts_finish_done(self, ok: bool, msg: str):
        self._fts_repairing = False
        self._startup_resume_pending = False
        self.center_loader.stop("fts_finish")
        logger.info(f"시작 시 검색 색인 마무리: ok={ok} · {msg}")
        # 색인이 채워졌으니 DB 재오픈 + 현재 검색/카운트 갱신 후 2단계로.
        try:
            self.db = self.manager.open_db_for_search()
        except Exception:
            logger.exception("색인 마무리 후 DB 재오픈 실패")
        try:
            self._check_fts_stale()   # 여전히 불일치면 수동 '검색 정리' 버튼 노출
            self._do_search()         # 완성된 색인으로 결과 갱신
        except Exception:
            logger.exception("색인 마무리 후 갱신 실패")
        # → 2단계(백그라운드 메타). 대기 없으면 진행바를 정리(마무리 100% 잔상 방지).
        started = self._ensure_background_meta_running()
        if not started:
            self.progress_bar.setVisible(False)
            self._set_status("검색 색인 마무리 완료 — 검색 준비 완료.")

    def _ensure_background_meta_running(self) -> bool:
        """2단계(백그라운드 메타) 워커를 필요 시 시작. 반환: 진행 중/시작됨=True,
        할 일 없어 안 돎=False (호출부가 이 값으로 최종 완료 처리 여부를 판단)."""
        # 사용자 일시정지 중엔 어떤 경로(Phase1 done, idle, 타이머) 로 호출되어도 무시.
        # 재개는 _resume_meta_analysis 가 명시적으로 수행.
        if self._meta_paused:
            return True   # 할 일은 있으나 일시정지 — 완료로 처리하지 않음
        # 재생 중 양보 상태 — 재생 끝나고 _resume_meta_after_playback 가 재개.
        if self._meta_yielded_playback:
            return True
        if self._is_background_meta_running():
            return True
        try:
            pending = Database(str(self.manager.db_path), read_only=True).count_pending_metadata()
        except Exception:
            pending = 0
        if pending <= 0:
            self._meta_total_files = 0  # 세션 종료(전체수 캐시 리셋)
            try:
                self._update_meta_indicator(self.db.count_incomplete_total(), active=False)
            except Exception:
                pass
            return False  # 분석할 항목 없음 → 호출부가 최종 완료 처리
        # 여기부턴 할 일(pending)이 확실히 있음 → 워커는 무조건 시작한다.
        # 분모(모수) = 이번에 분석할 개수(pending). 이전엔 미완료가 남은 라이브러리
        # 루트의 '전체 파일 수'를 분모로 써서, 빠른 갱신으로 1천 개만 추가돼도
        # "N / 90만" 처럼 보였음 (사용자 보고). 진행 중 세션에 파일이 더 추가되면
        # (pending 이 캐시보다 커지면) 분모를 새 pending 으로 갱신.
        if self._meta_total_files > 0 and pending <= self._meta_total_files:
            total = self._meta_total_files
        else:
            total = pending
        self._meta_total_files = total
        self._meta_base_done = max(0, total - pending)
        self.meta_worker = BackgroundMetaWorker(self.manager)
        self.meta_thread = QThread()
        _hold_bg_thread(self.meta_thread, self.meta_worker)
        self.meta_worker.moveToThread(self.meta_thread)
        self.meta_thread.started.connect(self.meta_worker.run)
        self.meta_worker.progress.connect(self._on_background_meta_progress)
        self.meta_worker.finished.connect(self._on_background_meta_done)
        self.meta_worker.finished.connect(self.meta_thread.quit)
        self.meta_worker.finished.connect(self.meta_worker.deleteLater)
        self.meta_thread.finished.connect(self._clear_background_meta_refs)
        self.meta_thread.finished.connect(self.meta_thread.deleteLater)
        self.meta_thread.start()
        self.indexing_badge.setVisible(True)
        self.indexing_badge.setText("● 메타 분석 중")
        self._update_meta_indicator(pending, active=True,
                                    done=self._meta_base_done, total=total)
        # 2단계 진행바 즉시 표시 (첫 progress emit 전 공백 방지)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(self._meta_base_done)
        self.progress_bar.setFormat("2단계/2 · 메타 분석 준비 중...")
        return True

    def _finalize_index_ui(self, status_msg: str):
        """인덱싱(2단계까지) 최종 완료 시 상태/배지 정리. 어떤 규모에서도 완료가
        상태바·배지에 반영되도록 공통화. (진행바 숨김은 호출부가 처리)"""
        if not self.manager.is_indexing() and not self._is_background_meta_running():
            self.indexing_badge.setVisible(False)
        self._set_status(status_msg)

    def _compute_scoped_meta_counts(self):
        """미완료(pending)가 남아있는 라이브러리 루트들에 한해 (전체수, 미완료수) 합산.
        분모를 '이번에 스캔한 라이브러리 범위'로 제한 — 전체 DB 가 아님. DB 기준이라
        앱 재시작에도 동일 값이 나와 진행도가 유지된다."""
        try:
            db = Database(str(self.manager.db_path), read_only=True)
            prefixes = [
                os.path.normpath(root["path"]).rstrip("\\/") + os.sep
                for root in self.manager.get_roots()
            ]
            # 루트마다 카운트 쿼리 2개 + 연결 2개씩 열던 N+1 패턴 →
            # 연결 1개 + 루트당 쿼리 1개 통합 (합산 규칙 동일)
            return db.count_scoped_meta_counts(prefixes)
        except Exception:
            return 0, 0

    def _on_background_meta_progress(self, p: IndexProgress):
        if p.phase != IndexProgress.PHASE_META:
            return
        # idle 중단 진행 중 — 잔여 progress emit 으로 인디케이터가 다시 보이지 않게 skip
        if self._meta_cancelling:
            return
        run_done = p.indexed + p.errors          # 이번 run 에서 처리한 수 (속도 계산용)
        # 세션 분모/누적은 IndexProgress 가 단일 출처다 — _phase2_metadata 가
        # index_meta.meta_session_total 을 읽어 채운다(앱 재시작 후에도 이어짐).
        # UI 가 따로 셈하면 두 출처가 갈리므로 여기서는 계산하지 않는다.
        total = p.total or self._meta_total_files
        cumulative = p.meta_done if p.total > 0 else min(
            total, self._meta_base_done + run_done)
        remaining = max(0, total - cumulative)
        self._update_meta_indicator(remaining, active=remaining > 0,
                                    done=cumulative, total=total)
        # 메인 진행바를 2단계 게이지로 — 실측 누적/전체 기반(가짜 0→100 아님) + 실제
        # 처리속도 기반 ETA. Phase1 후 숨겨지던 진행바를 Phase2 동안 다시 보여준다.
        if total > 0:
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(cumulative)
            pct = int(cumulative * 100 / total)
            eta_txt = ""
            st = getattr(p, "meta_start_time", 0) or 0
            elapsed = (time.monotonic() - st) if st else 0
            if run_done > 0 and elapsed > 1.0 and remaining > 0:
                rate = run_done / elapsed
                if rate > 0:
                    eta_txt = f" · 남은 시간: {self._fmt_remaining(remaining / rate)}"
            self.progress_bar.setFormat(
                f"2단계/2 · 메타 분석 중 · {pct}% ({cumulative:,}/{total:,}){eta_txt}")
        self._refresh_search_after_meta_flush()
        self._reload_incomplete_counts_async()

    def _on_background_meta_done(self, result: dict):
        # 재생 양보로 취소된 경우 — 무거운 트리/검색 reload 스킵(그게 오히려 재생을
        # 더 끊음). 인디케이터만 '일시정지(숫자 유지)' 로 갱신하고 끝낸다.
        if self._meta_yielded_playback:
            self._update_meta_indicator(self._meta_pending_n, active=False)
            return
        # 재개 대기 중(취소 직후 사용자가 재개를 누름) — '미완료'로 표시하거나 무거운
        # 갱신을 하지 말고, _clear_background_meta_refs 의 재개에 맡긴다(미완료 깜빡임 방지).
        if self._meta_resume_pending:
            return
        # 자연 완료 — 분석 세션 종료(전체수 캐시 리셋, 다음 스캔은 스코프 재계산).
        self._meta_total_files = 0
        try:
            db_ro = self.manager.open_db_for_search()
            remaining = db_ro.count_incomplete_total()
            self._update_meta_indicator(remaining, active=False)
            if not self.manager.is_indexing():
                self.indexing_badge.setVisible(False)
            self.db = self.manager.open_db_for_search()
            self.lib_status.refresh_async(self.manager)
            self._schedule_search()
            # 전체 트리 reload — incomplete_counts_async 만으로는 race 로 stale
            # 유지되는 경우가 있어 (사용자 보고). _reload_tree_async 가 pending
            # 큐로 안전하게 갱신.
            self._reload_tree_async()
            self._reload_incomplete_counts_async(force=True)
        except Exception:
            pass
        # 최종 완료 상태/배지 정리 — 이전엔 상태바가 'Phase1 완료' 메시지에 머물렀음.
        self._finalize_index_ui("메타 분석 완료 — 갱신 완료.")
        # 2단계 게이지 100% 후 숨김
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.progress_bar.setFormat("2단계/2 · 메타 분석 완료 · 100%")
        QTimer.singleShot(2500, lambda: self.progress_bar.setVisible(False))
        # 변경분이 있어 Phase1 완료 시 미뤄둔 '전체 완료' 팝업 (2단계까지 끝난 지금).
        if getattr(self, "_await_meta_completion", False):
            self._await_meta_completion = False
            prog = result.get("progress", None)
            analyzed = int(getattr(prog, "indexed", 0) or 0)
            errs = int(getattr(prog, "errors", 0) or 0)
            summ = getattr(self, "_rescan_summary", {}) or {}
            lines = ["갱신이 끝났습니다 — 2단계 메타 분석까지 완료.", ""]
            if summ.get("scanned"):
                lines.append(f"스캔한 파일: {int(summ['scanned']):,}개")
            lines.append(f"신규·변경: {int(summ.get('new', 0)):,}개")
            lines.append(f"메타 분석: {analyzed:,}개")
            if errs:
                lines.append(f"분석 오류: {errs:,}개")
            hidden_line = self._hidden_count_line()
            if hidden_line:
                lines.append(hidden_line)
            # 위 try 의 _reload_tree_async 가 끝나면 _on_tree_reload_done 이 이 팝업을 띄운다
            # (트리 갱신 로더와 팝업이 겹치지 않게 순서 정리).
            self._phase2_done_message = "\n".join(lines)
            self._pending_phase2_popup = True

    def _hidden_count_line(self) -> str:
        """갱신 완료 요약용 — 현재 검색 제외(중복 검수) 개수 1줄.
        partial index(idx_hidden_path_v2) COUNT — 실DB 158만 행에서 ~140ms.
        갱신 직후라 페이지 캐시가 따뜻해 팝업 지연으로 체감되지 않음. 0이면 생략."""
        try:
            n = self.manager.open_db_for_search().count_hidden()
        except Exception:
            return ""
        return f"검색 제외(중복 검수): {n:,}개 유지됨" if n else ""

    def _clear_background_meta_refs(self):
        self.meta_thread = self.meta_worker = None
        self._meta_cancelling = False
        # 재개 대기(레이스) — 워커가 완전히 종료된 지금 이어서 재개.
        if self._meta_resume_pending:
            self._meta_resume_pending = False
            QTimer.singleShot(0, self._ensure_background_meta_running)

    def _start_scoped_retry(self, scope: dict):
        """현황/미완료 다이얼로그의 재시도 → 범위 한정 reset + Phase2.
        IndexWorker 백그라운드 스레드에서 번호표(run_id) 발급 → 대상만 reset+스탬프 →
        그 번호표로 Phase2 한정. 잠금(_busy)·취소·진행률 모두 정규 인덱싱과 동일.

        scope: {"paths": [...]} (선택분) 또는
               {"prefix": str, "include_pending": bool, "include_failed": bool} (라이브러리/필터)
        """
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중", "다른 인덱싱 작업이 진행 중입니다")
            return
        paths = scope.get("paths")
        prefix = scope.get("prefix")
        if not paths and not prefix:
            return
        self._start_index(
            phase2_only=True,
            reset_scope_paths=paths,
            reset_scope_prefix=prefix,
            reset_include_pending=bool(scope.get("include_pending", False)),
            reset_include_failed=bool(scope.get("include_failed", True)),
        )

    # ─────────── 히스토리 드로어 ───────────
    def _toggle_history_drawer(self):
        if self.history_panel.is_expanded():
            # 닫기 직전 너비 보존
            w = self.history_panel.width()
            if w > 0:
                self._history_expanded_width = w
            self.history_panel.collapse()
            self.history_toggle_btn.setChecked(False)
            self.history_toggle_btn.setText("◀ 히스토리")
            self._save_config()
        else:
            self._refresh_history_panel()
            self.history_panel.expand(self._history_expanded_width)
            self.history_toggle_btn.setChecked(True)
            self.history_toggle_btn.setText("▶ 히스토리")

    def _on_history_width_changed(self, w: int):
        # grip 드래그로 히스토리 너비 조정 — 보존 후 디바운스 저장
        if w > 0:
            self._history_expanded_width = w
            self._save_config()

    def _refresh_history_panel(self):
        self.history_panel.set_items(self.player.history())

    def _on_seg_header_visibility_changed(self, visible: bool):
        """세그먼트 헤더 표시/숨김 → 우측 splitter 에서 결과창 ↔ 플레이어 간
        SEGMENT_HEADER_H(20px) 만큼 이동. 파형 body 높이는 그대로 유지하고
        결과 테이블 영역만 헤더 분만큼 줄어들거나 늘어남.

        부팅 시 WaveformView 가 1회 초기 상태 동기화 emit 을 보내는데, 이걸
        실제 토글로 오인해서 splitter 가 매 부팅마다 20px 씩 drift → 사용자가
        지정한 player 영역 너비/높이가 안 기억됨. 첫 emit 은 sync 로 처리하고
        실제 splitter 조정은 사용자가 직접 토글했을 때만.
        """
        if not getattr(self, "_seg_visibility_initialized", False):
            self._seg_visibility_initialized = True
            return  # 초기 동기화 — splitter 손대지 않음 (저장된 사용자 크기 유지)
        sizes = self.right_splitter.sizes()
        if len(sizes) < 2:
            return
        delta = SEGMENT_HEADER_H if visible else -SEGMENT_HEADER_H
        # 결과창이 너무 작아지지 않도록 가드 (최소 100px)
        if sizes[0] - delta < 100:
            return
        sizes[0] -= delta
        sizes[1] += delta
        self.right_splitter.setSizes(sizes)

    def _on_history_pick(self, path: str):
        self.player.load_and_play(path, meta=self.results.get_meta_for_path(path))

    def _on_history_clear(self):
        self.player.clear_history()

    @staticmethod
    def _filter_active_qss(cls_name: str, accent_hex: str) -> str:
        """활성 필터 위젯 QSS — 현재 테마 accent 의 틴트 fill + 외곽선.
        클래스 선택자라 콤보 팝업에는 안 샘."""
        hx = accent_hex.lstrip("#")
        rgb = f"{int(hx[0:2], 16)},{int(hx[2:4], 16)},{int(hx[4:6], 16)}"
        return (
            f"{cls_name} {{ border: 1px solid rgba({rgb},0.65);"
            f" background-color: rgba({rgb},0.09); }}"
            f"{cls_name}:hover {{ border-color: rgba({rgb},0.85);"
            f" background-color: rgba({rgb},0.26); }}"
            f"{cls_name}:focus {{ border: 1px solid rgba({rgb},0.90); }}"
        )

    def _update_filter_widget_styles(self):
        """개별 필터 위젯 활성 시인성 — 값이 걸린 위젯 자체에 주황 틴트
        fill+외곽선. 새로고침 버튼 색만으로는 '어느 필터가 걸렸는지' 안 보인다는
        피드백 반영. (테마 accent 는 '걸려있다'는 느낌이 약하다는 사용자 피드백으로
        전 테마 공통 주황으로 변경 — 미완료 인디케이터와 같은 warning tone.)
        상태가 바뀐 위젯만 restyle."""
        accent = _INCOMPLETE_COLOR_HEX  # 주황 #ffa64d
        states = (
            ("min_dur", self.min_dur, self.min_dur.value() > 0, "QDoubleSpinBox"),
            ("max_dur", self.max_dur, self.max_dur.value() > 0, "QDoubleSpinBox"),
            ("sr", self.sr_combo, self.sr_combo.currentIndex() != 0, "QComboBox"),
            ("ch", self.ch_combo, self.ch_combo.currentIndex() != 0, "QComboBox"),
        )
        prev = getattr(self, "_filter_widget_active", {})
        cur = {}
        for key, w, active, cls in states:
            cur[key] = (active, accent if active else "")
            if prev.get(key) == cur[key]:
                continue
            w.setStyleSheet(self._filter_active_qss(cls, accent) if active else "")
        self._filter_widget_active = cur

    def _update_filter_reset_btn_style(self):
        """필터 새로고침(초기화) 버튼 — 반복재생/세그먼트 토글처럼 외곽선+내부 fill.
        길이/샘플레이트/채널 중 하나라도 걸려 있으면 빨강, 아무것도 없으면 초록.
        상태가 바뀔 때만 restyle (매 검색마다 아이콘 재생성 방지).
        시각 강도(외곽선 풀 불투명 + fill α≈0.2 + vivid 색)는 하단 세그먼트
        토글(_TransportButton)과 동일하게 맞춤 — 이전엔 외곽선 0.55/fill 0.13/
        칙칙한 초록(#3a9d52)이라 혼자 희미하게 보였음 (사용자 보고)."""
        if not hasattr(self, "filter_reset_btn"):
            return
        self._update_filter_widget_styles()
        active = (self.min_dur.value() > 0 or self.max_dur.value() > 0
                  or self.sr_combo.currentIndex() != 0 or self.ch_combo.currentIndex() != 0)
        # 색/외곽선/fill 은 _FilterResetButton.paintEvent 가 직접 그림.
        # 여기서는 active(빨강/초록) 상태만 전달 (setStyleSheet 금지 — paint 와 충돌).
        self.filter_reset_btn.set_active(active)

    def _reset_filters(self):
        """필터 행의 LENGTH / SAMPLE RATE / CHANNELS 모두 기본값(전체) 복원.
        각 위젯 시그널 blockSignals 로 차단 → 마지막에 _do_search 1회 호출
        (중간 signal 마다 검색 재실행 방지).
        """
        for w, val in (
            (self.min_dur, 0),
            (self.max_dur, 0),
            (self.sr_combo, 0),
            (self.ch_combo, 0),
        ):
            w.blockSignals(True)
            try:
                if isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    w.setValue(val)
                else:
                    w.setCurrentIndex(val)
            finally:
                w.blockSignals(False)
        self._do_search()

    def _refresh_after_index_change(self):
        """루트/인덱스 변경 후 트리 + 카운트 + 라이브러리 현황 갱신."""
        # 트리/카운트는 비동기. sync `_reload_tree()` 는 get_all_folder_counts +
        # get_all_folder_incomplete_counts 가 audio_files 풀스캔 2번이라 부팅 UI
        # 스레드 수 초 블로킹 (200GB ~1M 행 기준 "20배 느림" 회귀의 직접 원인).
        # 워커 완료 시 _on_tree_reload_done 이 count_label/meta_indicator 갱신.
        roots = self.manager.get_roots()
        self._reload_tree_async()
        self.lib_status.refresh_async(self.manager)
        self._check_fts_stale()
        if not roots:
            self._set_status("등록된 라이브러리 없음. [+ 라이브러리 추가] 클릭.")
        else:
            self._set_status(f"라이브러리 {len(roots)}개")

    def _ensure_term_index_async(self):
        """단어색인(정확검색용)이 없으면 백그라운드로 1회 빌드. 이미 있으면 아무것도 안 함.
        인덱싱 중엔 미뤘다가(다음 호출에서) 빌드 — 동시 대량 쓰기 회피."""
        if not self.manager:
            return
        if getattr(self, "_term_build_thread", None) is not None:
            return  # 이미 빌드 중
        try:
            if self.manager.is_indexing():
                QTimer.singleShot(10000, self._ensure_term_index_async)
                return
            cnt = self.manager.open_db_for_search().term_index_count()
        except Exception:
            return
        if cnt > 0:
            return  # 이미 빌드됨 — 인덱싱 시 미러로 동기 유지
        worker = _TermIndexBuildWorker(str(self.manager.db_path))
        thread = QThread()
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: setattr(self, "_term_build_thread", None))
        thread.finished.connect(thread.deleteLater)
        self._term_build_worker = worker
        self._term_build_thread = thread
        thread.start()

    def _check_fts_stale(self):
        """검색 인덱스 정합성 플래그 확인 → 복구 버튼 표시 토글.
        어긋남 감지 시 세션당 1회, 툴이 뜬 뒤 팝업으로 표적 수리 제안."""
        try:
            stale = self.db.is_fts_stale()
        except Exception:
            stale = False
        # 재빌드 중이면 버튼 자체는 비활성 (텍스트는 _on_fts_rebuild_* 가 갱신)
        if not getattr(self, "_fts_rebuilding", False):
            self.fts_rebuild_btn.setVisible(stale)
        if stale and not getattr(self, "_fts_repair_prompted", False) \
                and not getattr(self, "_fts_repairing", False):
            self._fts_repair_prompted = True  # 세션당 1회만 — 거절 시 다음 실행에서 재안내
            QTimer.singleShot(800, self._prompt_fts_repair)

    def _prompt_fts_repair(self):
        """색인 어긋남 팝업 — 사용자 승인 시에만 백그라운드 표적 수리 시작."""
        # 시작 시 '1단계 검색 색인 마무리'가 블로킹으로 처리 중/예정이면 팝업 생략
        # (재시작을 처음 실행처럼 자동 진행 — Yes/No 로 사용자를 막지 않는다).
        if getattr(self, "_startup_resume_pending", False):
            return
        if getattr(self, "_fts_repairing", False) or getattr(self, "_fts_rebuilding", False):
            return
        if getattr(self, "_removing", False):
            return  # 제거 진행 중 — 역방향 레이스 차단, 다음 감지 때 재안내
        if self.manager.is_indexing():
            return  # 인덱싱 종료 후 _check_fts_stale 경로에서 재감지됨
        try:
            if not self.db.is_fts_stale():
                return
        except Exception:
            return
        ret = QMessageBox.question(
            self, "검색 색인 어긋남",
            "파일 목록과 검색 색인의 개수가 일치하지 않습니다.\n"
            "(인덱싱 중 강제 종료 등이 원인 — 일부 파일이 검색에서 누락될 수 있습니다)\n\n"
            "어긋난 항목만 찾아 백그라운드로 수리할까요?\n"
            "검색은 계속 사용 가능하며, [No] 선택 시 다음 실행 때 다시 안내합니다."
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._start_fts_repair()

    def _start_fts_repair(self):
        self._fts_repairing = True
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("검색 색인 수리 중...")
        self._set_status("검색 색인 수리 중 — UI 사용 가능")
        worker = _FtsRepairWorker(self.manager)
        thread = QThread()
        _hold_bg_thread(thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_fts_repair_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._fts_repair_thread = thread
        thread.start()

    def _on_fts_repair_done(self, success: bool, message: str):
        self._fts_repairing = False
        QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False))
        # 재오픈해서 fts_stale 재확인 + 복구된 파일이 결과에 나오도록 재검색
        self.db = self.manager.open_db_for_search()
        self._check_fts_stale()
        self._set_status(message)
        if success:
            self._do_search()

    def _start_fts_rebuild(self):
        if getattr(self, "_fts_rebuilding", False):
            return
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중",
                                "인덱싱 중에는 검색 인덱스 복구 불가. 끝난 뒤 다시 시도.")
            return
        ret = QMessageBox.question(
            self, "검색 정리 — 검색 데이터 다시 만들기",
            "이 버튼은 '검색에 쓰는 데이터'가 실제 파일 목록과 어긋났을 때만 나타납니다.\n"
            "보통 인덱싱(파일 정리) 도중 앱이 꺼졌을 때 생기고, 이 상태에선 일부 "
            "파일이 검색에 안 나올 수 있습니다.\n\n"
            "지금 누르면 검색용 데이터에서 빠진 부분을 채워 파일 목록과 정확히 맞춥니다.\n"
            "라이브러리가 크면 몇 분~수십 분 걸릴 수 있고, 채우는 동안에는 "
            "앱이 잠깁니다(다른 작업 불가).\n\n"
            "지금 채울까요?"
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self._fts_rebuilding = True
        self.fts_rebuild_btn.setText("⟳ 다시 만드는 중...")
        self.fts_rebuild_btn.setEnabled(False)
        # 블로킹 — 중앙 로더로 앱 잠금(1단계 종료 = 앱 잠금 일관성). 실제 작업은
        # 백그라운드 스레드에서 돌고 게이지만 갱신(UI 스레드 프리즈 방지).
        self.center_loader.start("fts_rebuild", "검색 데이터 다시 만드는 중...")
        self.center_loader.set_progress(
            "fts_rebuild", "검색용 데이터를 처음부터 다시 만들고 있습니다 — 잠시만요.", -1)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("검색 데이터 다시 만드는 중...")

        self.fts_worker = FTSRebuildWorker(str(self.manager.db_path))
        self.fts_thread = QThread()
        _hold_bg_thread(self.fts_thread, self.fts_worker)
        self.fts_worker.moveToThread(self.fts_thread)
        self.fts_thread.started.connect(self.fts_worker.run)
        self.fts_worker.progress.connect(self._on_fts_rebuild_progress)
        self.fts_worker.finished.connect(self._on_fts_rebuild_done)
        self.fts_worker.finished.connect(self.fts_thread.quit)
        self.fts_thread.start()

    def _on_fts_rebuild_progress(self, done: int, total: int):
        if total > 0:
            pct = int(done * 100 / total) if total else 0
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            self.progress_bar.setFormat(
                f"검색 데이터 다시 만드는 중 · {pct}% ({done:,}/{total:,})"
            )
            self.center_loader.set_progress(
                "fts_rebuild", f"검색 데이터 다시 만드는 중 {pct}% ({done:,}/{total:,})", pct)
        else:
            self.center_loader.set_progress("fts_rebuild", "검색 데이터 다시 만드는 중...", -1)

    def _on_fts_rebuild_done(self, success: bool, message: str):
        self._fts_rebuilding = False
        self.center_loader.stop("fts_rebuild")   # 블로킹 해제
        self.fts_rebuild_btn.setEnabled(True)
        self.fts_rebuild_btn.setText("⚠ 검색 정리")
        QTimer.singleShot(3000, lambda: self.progress_bar.setVisible(False))
        # 재오픈해서 fts_stale 플래그 재확인
        self.db = self.manager.open_db_for_search()
        self._check_fts_stale()
        self._set_status(message)
        if success:
            self._do_search()   # 다시 만든 색인으로 결과 갱신

    def _update_count_label(self, n: int):
        self.count_label.setText(f"인덱싱됨  {n:,}")

    def _update_meta_indicator(self, pending_count: int, active: Optional[bool] = None,
                               done: Optional[int] = None, total: Optional[int] = None):
        """status bar 메타 분석 인디케이터.
        - pending_count: 남은 미완료(pending+failed) 카운트
        - active=True: Phase2 가 실제 진행 중 (점 애니메이션)
        - active=False: Phase2 끝났음 (수치만 또는 숨김)
        - active=None: 현재 상태 유지
        - done/total: Phase2 진행률 (% 표시용). None 이면 직전 값 유지.

        active=False 인데 idle 정책 ON 이고 pending 잔량 있으면 "일시정지" 표시 —
        사용자가 명시적으로 취소한 "미완료" 와는 다른 상태로 시각화.
        """
        self._meta_pending_n = max(0, int(pending_count))
        if active is not None:
            self._meta_active = bool(active)
        if done is not None:
            self._meta_done_n = max(0, int(done))
        if total is not None:
            self._meta_total_n = max(0, int(total))
        if self._meta_pending_n <= 0 and not self._meta_active:
            self.meta_indicator.setVisible(False)
            self.meta_spinner.stop()
            self._meta_anim_timer.stop()
            if hasattr(self, "meta_pause_btn"):
                self.meta_pause_btn.setVisible(False)
            return
        self.meta_indicator.setVisible(True)
        # 일시정지 버튼은 인덱싱이 실제 돌아갈 때만 (또는 사용자 명시 정지 상태일 때만)
        # "미완료"/"앱 사용 중" 상태 — 정지할 백그라운드 작업이 없으므로 버튼 노출 X.
        if hasattr(self, "meta_pause_btn"):
            # 양보 중에도 정지 버튼 유지(위치 고정·기능 유지) — 사라지면 스피너의 paused
            # 아이콘이 좌측에 남아 버튼이 옮겨간 것처럼 보였음.
            self.meta_pause_btn.setVisible(
                self._meta_active or self._meta_paused or self._meta_yielded_playback)
        if self._meta_active:
            self.meta_spinner.start()
            if not self._meta_anim_timer.isActive():
                self._meta_anim_timer.start()
            self.meta_indicator.setText(self._build_meta_active_text())
            return
        # active=False — 재생 양보 / 사용자 일시정지 / idle 정책 일시정지 / 단순 미완료
        self._meta_anim_timer.stop()
        if self._meta_yielded_playback and self._meta_pending_n > 0:
            # 재생 중 양보 — 누적 진행도 유지하며 '일시정지(재생 중)' 표시.
            self.meta_spinner.show_paused()
            self.meta_indicator.setText(self._build_meta_progress_text("분석 일시정지", "재생 중"))
        elif self._meta_paused and self._meta_pending_n > 0:
            self.meta_spinner.show_paused()
            self.meta_indicator.setText(self._build_meta_progress_text("분석 일시정지", "사용자 정지"))
        elif self._config_data.get("index_on_idle", False) and self._meta_pending_n > 0:
            self.meta_spinner.show_paused()
            self.meta_indicator.setText(self._build_meta_progress_text("분석 일시정지", "앱 사용 중"))
        else:
            self.meta_spinner.stop()
            self.meta_indicator.setText(f"미완료  {self._meta_pending_n:,}")

    def _build_meta_active_text(self) -> str:
        dots = "." * (self._meta_dot_state + 1)
        if self._meta_total_n > 0:
            pct = (self._meta_done_n / self._meta_total_n) * 100.0
            return (f"백그라운드 분석 {pct:.1f}%  "
                    f"({self._meta_done_n:,} / {self._meta_total_n:,}) {dots}")
        return f"백그라운드 분석 {dots:<3}"

    def _build_meta_progress_text(self, label: str, note: str) -> str:
        """일시정지/양보 상태 텍스트 — 누적 진행도(완료/전체) 유지해 시인성 확보."""
        if self._meta_total_n > 0:
            pct = (self._meta_done_n / self._meta_total_n) * 100.0
            return (f"{label} {pct:.1f}%  "
                    f"({self._meta_done_n:,} / {self._meta_total_n:,}) — {note}")
        return f"{label}  {self._meta_pending_n:,} — {note}"

    def _tick_meta_indicator(self):
        if not self._meta_active:
            self._meta_anim_timer.stop()
            return
        self._meta_dot_state = (self._meta_dot_state + 1) % 3
        self.meta_indicator.setText(self._build_meta_active_text())

    def _reload_tree(self):
        folders = self.db.get_folders()
        roots_data = self.manager.get_roots()
        roots = [r["path"] for r in roots_data]
        # 한 번의 쿼리로 모든 폴더 누적 카운트 취득 (성능 개선)
        folder_counts = self.db.get_all_folder_counts()
        total = self.db.count_files()
        incomplete_counts = self.db.get_all_folder_incomplete_counts()
        total_incomplete = self.db.count_incomplete_total()
        self.library_tabs.set_data(
            folders, roots, root_counts=folder_counts,
            total_count=total,
            incomplete_counts=incomplete_counts,
            total_incomplete=total_incomplete,
            display_names=self._folder_display_names,
            favorites=self._favorites,
        )
        self.side_count.setText(f"경로 폴더 {len(roots)}")
        self._update_meta_indicator(total_incomplete)
        self._maybe_restore_last_selection()

    def _maybe_restore_last_selection(self):
        """재시작 후 첫 트리 데이터가 채워지면 마지막 선택 폴더를 1회 복원.
        노드가 아직 없으면(부분 로드) pending 유지 후 다음 set_data 때 재시도."""
        sel = getattr(self, "_pending_last_selection", None)
        if not sel:
            return
        tab, path = sel
        if self.library_tabs.select_saved(tab, path):
            self._pending_last_selection = None

    # ─────────── 라이브러리 추가/제거 ───────────
    def _reload_tree_async(self):
        # 진행 중 trigger 는 큐에 잡아두고 finished 직후 한 번 더 — 그렇지 않으면
        # 인덱싱/메타 추출 끝났을 때 직전 worker 가 가져온 stale 카운트로 트리가
        # 멈춰버림 (사용자 보고: "메타 추출 끝나도 전체 사운드 수 주황색 유지").
        if getattr(self, "_tree_reloading", False):
            self._tree_reload_pending = True
            return
        self._tree_reload_pending = False
        self._tree_reloading = True
        # 큰 갱신 직후 트리 재구성도 클릭 입력이 겹치면 "응답 없음"처럼 보일 수 있어
        # 항상 중앙 로더로 막고, 실제 worker 시작은 이벤트 루프 한 박자 뒤로 넘긴다.
        self.center_loader.start("tree", "폴더 트리 갱신 중...")
        self._tree_loader_target = "center"
        self.tree_reload_worker = TreeReloadWorker(self.manager)
        self.tree_reload_thread = QThread()
        _hold_bg_thread(self.tree_reload_thread, self.tree_reload_worker)
        self.tree_reload_worker.moveToThread(self.tree_reload_thread)
        self.tree_reload_thread.started.connect(self.tree_reload_worker.run)
        self.tree_reload_worker.finished.connect(self._on_tree_reload_done)
        self.tree_reload_worker.finished.connect(self.tree_reload_thread.quit)
        self.tree_reload_worker.finished.connect(self.tree_reload_worker.deleteLater)
        self.tree_reload_thread.finished.connect(self._clear_tree_reload_refs)
        self.tree_reload_thread.finished.connect(self.tree_reload_thread.deleteLater)
        QTimer.singleShot(0, self.tree_reload_thread.start)

    def _on_tree_reload_done(self, folders, roots, folder_counts, total,
                             incomplete_counts, total_incomplete):
        try:
            self.library_tabs.set_data(
                folders, roots,
                root_counts=folder_counts, total_count=total,
                incomplete_counts=incomplete_counts,
                total_incomplete=total_incomplete,
                display_names=self._folder_display_names,
                favorites=self._favorites,
            )
            self.side_count.setText(f"경로 폴더 {len(roots)}")
            self._update_count_label(total)
            self._update_meta_indicator(total_incomplete)
            self._maybe_restore_last_selection()
        except Exception:
            logger.exception("트리 갱신 실패")
        finally:
            if getattr(self, "_tree_loader_target", "center") == "global":
                self.global_loader.stop("tree")
            else:
                self.center_loader.stop("tree")
            # 비동기 전체 로드 완료 — 경로를 못 찾았어도(라이브러리 제거 등) 여기서
            # pending 정리해 이후 set_data 마다 헛도는 일 방지.
            self._first_tree_reload_done = True
            self._pending_last_selection = None
        # 1단계 결과 반영 트리 갱신이 끝났음 → 2단계 시작 여부에 따라 분기.
        if getattr(self, "_pending_phase1_popup", False):
            self._pending_phase1_popup = False
            started = self._ensure_background_meta_running()
            if started:
                # 2단계가 돎(또는 이미 진행 중) → '1단계 완료(검색 가능)' 팝업.
                # 최종 완료·요약 팝업은 2단계 끝(_on_background_meta_done→트리갱신)에서.
                self._show_phase1_done_popup()
            else:
                # 2단계 할 일 없음(소규모/변경없음) → 지금이 최종 완료. 상태/배지 정리 + 팝업.
                self._await_meta_completion = False
                summ = getattr(self, "_rescan_summary", {}) or {}
                self._finalize_index_ui("갱신 완료 — 검색 가능.")
                self.progress_bar.setVisible(False)
                hidden_line = self._hidden_count_line()
                QMessageBox.information(
                    self, "갱신 완료",
                    f"{summ.get('message', '완료')}\n\n"
                    f"스캔한 파일: {int(summ.get('scanned', 0)):,}개\n"
                    f"신규·변경: {int(summ.get('new', 0)):,}개 · 추가 분석할 항목 없음"
                    + (f"\n{hidden_line}" if hidden_line else "")
                )
        # 2단계(메타) 완료 후 트리 갱신까지 끝났으니 '갱신 완료 + 요약' 팝업.
        elif getattr(self, "_pending_phase2_popup", False):
            self._pending_phase2_popup = False
            QMessageBox.information(self, "갱신 완료",
                                    getattr(self, "_phase2_done_message", "갱신이 완료되었습니다."))

    def _clear_tree_reload_refs(self):
        self.tree_reload_thread = self.tree_reload_worker = None
        self._tree_reloading = False
        if getattr(self, "_tree_reload_pending", False):
            QTimer.singleShot(0, self._reload_tree_async)

    def _on_root_order_change_requested(self, paths: list):
        try:
            self.manager.reorder_roots(paths)
            last_data = getattr(self.library_tabs, "_last_data", None)
            if last_data is not None:
                known = {
                    os.path.normpath(p).lower(): p
                    for p in last_data.get("roots", [])
                }
                ordered = []
                seen = set()
                for path in paths:
                    key = os.path.normpath(path).lower()
                    if key in known and key not in seen:
                        ordered.append(known[key])
                        seen.add(key)
                for path in last_data.get("roots", []):
                    key = os.path.normpath(path).lower()
                    if key not in seen:
                        ordered.append(path)
                last_data["roots"] = ordered
            self._set_status("라이브러리 순서를 저장했습니다")
            self.lib_status.refresh()
        except Exception:
            logger.exception("라이브러리 순서 저장 실패")
            self._set_status("라이브러리 순서 저장 실패")

    def _on_favorite_added(self, path: str):
        if path not in self._favorites:
            self._favorites.append(path)
            self._save_config()
            self._reload_tree_async()

    def _on_favorite_removed(self, path: str):
        if path in self._favorites:
            self._favorites.remove(path)
            self._save_config()
            self._reload_tree_async()

    def _prune_tab_refs_for_removed(self, removed: list):
        """앱에서 제거된 라이브러리(및 그 하위)를 즐겨찾기/사용자 탭 참조에서 제거.
        제거된 root 의 *상위* 폴더를 가리키는 item 은 유지(_filter_folders 가 자연
        정리). 변경 시 한 번만 저장 — 실제 트리 갱신은 호출부의 reload 가 수행."""
        rkeys = [os.path.normpath(p).replace("/", "\\").lower().rstrip("\\")
                 for p in removed if p]
        if not rkeys:
            return

        def under(p: str) -> bool:
            k = os.path.normpath(p).replace("/", "\\").lower().rstrip("\\")
            return any(k == r or k.startswith(r + "\\") for r in rkeys)

        favs = [f for f in self._favorites if not under(f)]
        changed = self.library_tabs.prune_removed_roots(removed)
        if len(favs) != len(self._favorites):
            self._favorites = favs
            changed = True
        if changed:
            self._save_config()

    def _refresh_tree_counts_async(self):
        """Phase1 진행 중 가벼운 카운트 부분 갱신. 트리 노드 재생성 없이 카운트 셀만
        업데이트 → UI 스레드 freeze 없음. _reload_tree_async 의 load_folders() 가
        매번 트리 전체를 clear + rebuild 해서 1초+ freeze 일으키던 회귀 해결."""
        if getattr(self, "_counts_thread", None) and self._counts_thread.isRunning():
            return
        self._counts_worker = TreeCountsWorker(self.manager)
        self._counts_thread = QThread()
        _hold_bg_thread(self._counts_thread, self._counts_worker)
        self._counts_worker.moveToThread(self._counts_thread)
        self._counts_thread.started.connect(self._counts_worker.run)
        self._counts_worker.finished.connect(self._on_tree_counts_done)
        self._counts_worker.finished.connect(self._counts_thread.quit)
        self._counts_worker.finished.connect(self._counts_worker.deleteLater)
        self._counts_thread.finished.connect(self._clear_counts_refs)
        self._counts_thread.finished.connect(self._counts_thread.deleteLater)
        self._counts_thread.start()

    def _on_tree_counts_done(self, folder_counts, total, incomplete_counts, total_incomplete):
        try:
            self.tree.update_counts(folder_counts, total, incomplete_counts, total_incomplete)
            self._update_count_label(int(total or 0))
            self._update_meta_indicator(int(total_incomplete or 0))
        except Exception:
            logger.exception("트리 카운트 갱신 실패")

    def _clear_counts_refs(self):
        self._counts_thread = None
        self._counts_worker = None

    def _reload_incomplete_counts_async(self, force: bool = False):
        if getattr(self, "incomplete_thread", None) and self.incomplete_thread.isRunning():
            # force=True 호출 (인덱싱/메타 종료 시) 가 race 로 스킵되면 stale 유지 →
            # 끝난 후 한 번 더 trigger.
            if force:
                self._incomplete_reload_pending = True
            return
        now = time.monotonic()
        if not force and now - self._last_incomplete_refresh < 30.0:
            return
        self._incomplete_reload_pending = False
        self._last_incomplete_refresh = now
        self.incomplete_worker = IncompleteCountsWorker(self.manager)
        self.incomplete_thread = QThread()
        _hold_bg_thread(self.incomplete_thread, self.incomplete_worker)
        self.incomplete_worker.moveToThread(self.incomplete_thread)
        self.incomplete_thread.started.connect(self.incomplete_worker.run)
        self.incomplete_worker.finished.connect(self._on_incomplete_counts_done)
        self.incomplete_worker.finished.connect(self.incomplete_thread.quit)
        self.incomplete_worker.finished.connect(self.incomplete_worker.deleteLater)
        self.incomplete_thread.finished.connect(self._clear_incomplete_refs)
        self.incomplete_thread.finished.connect(self.incomplete_thread.deleteLater)
        self.incomplete_thread.start()

    def _on_incomplete_counts_done(self, incomplete_counts, total_incomplete):
        try:
            self.tree.update_incomplete_counts(incomplete_counts, total_incomplete)
            self._update_meta_indicator(total_incomplete, active=self._is_background_meta_running())
        except Exception:
            logger.exception("미완료 표시 갱신 실패")

    def _clear_incomplete_refs(self):
        self.incomplete_thread = self.incomplete_worker = None
        if getattr(self, "_incomplete_reload_pending", False):
            QTimer.singleShot(0, lambda: self._reload_incomplete_counts_async(force=True))

    def _add_library(self):
        p = QFileDialog.getExistingDirectory(self, "라이브러리 폴더 선택")
        if not p:
            return

        p_norm = os.path.normpath(p).rstrip("\\/")
        p_low = p_norm.lower()

        # 1. 중복/상속 여부 먼저 가볍게 체크
        existing = self.manager.get_roots()
        parent_root = None
        for r in existing:
            r_norm = os.path.normpath(r["path"]).rstrip("\\/")
            r_low = r_norm.lower()
            if r_low == p_low:
                parent_root = r_norm
                break
            if p_low.startswith(r_low + "\\") or p_low.startswith(r_low + "/"):
                if parent_root is None or len(r_norm) > len(parent_root):
                    parent_root = r_norm

        if parent_root:
            same_root = parent_root.lower() == p_low
            if same_root:
                msg = (
                    f"[{p_norm}]\n\n"
                    "이미 등록된 라이브러리입니다.\n"
                    "파일 내용이 많이 바뀌었다면 이 경로를 다시 스캔할 수 있습니다.\n\n"
                    "다시 스캔할까요?"
                )
            else:
                msg = (
                    f"선택한 경로: {p_norm}\n"
                    f"상위 라이브러리: {parent_root}\n\n"
                    "이미 상위 라이브러리 안에 포함된 폴더입니다.\n"
                    "새 라이브러리 루트로 중복 등록하지 않고, 파일 브라우저에서는 상위 폴더 구조 안에 표시합니다.\n\n"
                    "파일 내용이 많이 바뀌었다면 선택한 하위 경로만 다시 스캔할 수 있습니다.\n"
                    "다시 스캔할까요?"
                )
            ret_rescan = QMessageBox.question(
                self, "이미 인덱싱된 폴더", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret_rescan == QMessageBox.StandardButton.Yes:
                self._start_index(scan_path=p_norm)
            else:
                self._set_status("이미 인덱싱된 폴더라 새 라이브러리로 추가하지 않았습니다")
            return

        # 2. 자식 루트 자동 흡수 검사. 상위 폴더를 추가하면 하위 루트는
        # 라이브러리 현황에서 빼고, 파일 브라우저에서는 상위 구조 안에 자연 표시한다.
        children = []
        for r in existing:
            r_norm = os.path.normpath(r["path"]).rstrip("\\/")
            r_low = r_norm.lower()
            if (r_low.startswith(p_low + "\\") or r_low.startswith(p_low + "/")
                    or r_low.startswith(p_low + os.sep.lower())):
                children.append(r_norm)

        if children:
            shown = "\n".join(f"  · {c}" for c in children[:12])
            more = f"\n  · ... 외 {len(children) - 12:,}개" if len(children) > 12 else ""
            msg = (
                f"선택한 경로: {p_norm}\n\n"
                "이 경로 아래에 이미 등록된 하위 라이브러리가 있습니다:\n\n"
                f"{shown}{more}\n\n"
                "하위 라이브러리는 별도 루트에서 빼고, 선택한 상위 폴더를 라이브러리로 통합합니다.\n"
                "기존 인덱스 데이터는 유지되며, 파일 브라우저에서는 상위 폴더 구조 안에 표시됩니다.\n\n"
                "상위 폴더를 다시 스캔할까요?"
            )
            ret_sync = QMessageBox.question(
                self, "라이브러리 통합",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret_sync != QMessageBox.StandardButton.Yes:
                self._set_status("하위 라이브러리와 겹쳐 새 라이브러리로 추가하지 않았습니다")
                return
            for c in children:
                self.manager.remove_root_only(c)
        else:
            # 3. 지금 스캔할지 확인 (여기서 No 하면 아무것도 하지 않음)
            ret = QMessageBox.question(
                self, "라이브러리 추가",
                f"선택한 경로: {p_norm}\n\n지금 이 폴더를 스캔하여 라이브러리에 등록할까요?\n"
                "(스캔하지 않으면 목록에 추가되지 않습니다.)"
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        # 4. 실제 DB 등록 및 인덱싱 시작
        self.manager.add_root(p_norm)
        self._refresh_after_index_change()
        self._start_index(scan_path=p_norm)

    def _on_remove_root_request(self, path: str):
        self._on_remove_roots_request([path])

    def _on_remove_roots_request(self, paths: list):
        if getattr(self, "_removing", False):
            return
        # 색인 수리 중 제거 금지 — 수리가 방금 지운 행을 색인에 되살리는 레이스 차단
        if getattr(self, "_fts_repairing", False):
            QMessageBox.warning(self, "진행 중",
                                "검색 색인 수리 중에는 라이브러리 제거 불가. 잠시 후 다시 시도.")
            return
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중",
                                "인덱싱 중에는 라이브러리 제거 불가. 끝난 뒤 다시 시도.")
            return
        paths = list(dict.fromkeys(str(p) for p in paths if p))
        if not paths:
            return

        # confirm dialog 띄우기 직전에 일시정지 상태의 백그라운드 메타 워커를 미리
        # cancel — 사용자가 dialog 응답하는 동안 워커가 자연스럽게 종료되어 ① dialog
        # 자체 응답성 개선 (GIL 경합 완화) ② 이후 remove worker 의 DB write 와 충돌 X.
        # 동기 wait() 는 UI freeze 직접 원인이라 절대 사용 X — finished 시그널 콜백 패턴.
        if self._meta_paused and self._is_background_meta_running():
            self.manager.cancel()
            try:
                self.meta_thread.quit()
            except RuntimeError:
                pass

        if len(paths) == 1:
            msg_path = paths[0]
        else:
            msg_path = "\n".join(paths[:12]) + ("\n..." if len(paths) > 12 else "")
        ret = QMessageBox.question(
            self, "라이브러리 제거",
            f"{msg_path}\n\n선택한 라이브러리 {len(paths):,}개와 인덱싱된 데이터를 삭제할까요?\n"
            "(실제 파일은 삭제되지 않습니다.)"
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self._start_remove_or_wait(paths, purge=False)

    def _on_purge_roots_request(self, paths: list):
        """라이브러리 완전 제거 — 모든 저장 정보(인덱스/캐시/히스토리/블랙리스트)
        영구 삭제. 흐름은 _on_remove_roots_request 와 동일하되 경고 수위와 워커만
        다름."""
        if getattr(self, "_removing", False):
            return
        if getattr(self, "_fts_repairing", False):
            QMessageBox.warning(self, "진행 중",
                                "검색 색인 수리 중에는 라이브러리 제거 불가. 잠시 후 다시 시도.")
            return
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중",
                                "인덱싱 중에는 라이브러리 제거 불가. 끝난 뒤 다시 시도.")
            return
        paths = list(dict.fromkeys(str(p) for p in paths if p))
        if not paths:
            return

        if self._meta_paused and self._is_background_meta_running():
            self.manager.cancel()
            try:
                self.meta_thread.quit()
            except RuntimeError:
                pass

        if len(paths) == 1:
            msg_path = paths[0]
        else:
            msg_path = "\n".join(paths[:12]) + ("\n..." if len(paths) > 12 else "")
        ret = QMessageBox.warning(
            self, "라이브러리 완전 제거",
            f"{msg_path}\n\n"
            f"선택한 라이브러리 {len(paths):,}개에 대해 이 도구에 저장된 "
            "모든 정보가 영구 삭제됩니다:\n"
            "  · 인덱스/메타데이터 전체 (숨김·제거·실패 항목 포함)\n"
            "  · 파형 캐시 / 재생 히스토리 / 블랙리스트 항목\n"
            "  · 즐겨찾기 · 사용자 탭 참조\n\n"
            "실제 오디오 파일은 삭제되지 않습니다.\n"
            "대형 라이브러리는 수 분이 걸릴 수 있습니다.\n\n"
            "※ 완전 제거해도 디스크 용량·검색 성능에는 사실상 차이가 없습니다.\n"
            "   일반적으로는 [라이브러리 제거]를 권장하며, 완전 제거는 이 도구에\n"
            "   남은 모든 기록을 지우고 싶을 때만 사용하세요. 계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        self._start_remove_or_wait(paths, purge=True)

    def _start_remove_or_wait(self, paths: list, purge: bool):
        """confirm 후 공통 진입점 — 백그라운드 메타 워커가 살아있으면 종료 대기."""
        if self._is_background_meta_running():
            self._pending_remove_paths = paths
            self._pending_remove_purge = purge
            self._set_status("백그라운드 분석 종료 대기 중 — 곧 제거를 시작합니다...")
            self.global_loader.start("remove_wait", "백그라운드 분석 종료 대기 중")
            try:
                self.meta_thread.finished.connect(self._on_meta_stopped_then_remove)
            except RuntimeError:
                # thread 가 race 로 이미 사라진 경우 — 즉시 진행
                self.global_loader.stop("remove_wait")
                self._do_remove_after_confirm(paths, purge=purge)
            return

        self._do_remove_after_confirm(paths, purge=purge)

    def _on_meta_stopped_then_remove(self):
        try:
            self.meta_thread.finished.disconnect(self._on_meta_stopped_then_remove)
        except (RuntimeError, TypeError, AttributeError):
            pass
        paths = getattr(self, "_pending_remove_paths", None) or []
        purge = bool(getattr(self, "_pending_remove_purge", False))
        self._pending_remove_paths = None
        self._pending_remove_purge = False
        self.global_loader.stop("remove_wait")
        if not paths:
            return
        # _clear_background_meta_refs 가 thread.finished 에서 self.meta_thread = None
        # 처리. 0ms 큐잉으로 그 정리 후 실제 제거 진행.
        QTimer.singleShot(0, lambda: self._do_remove_after_confirm(paths, purge=purge))

    def _do_remove_after_confirm(self, paths: list, purge: bool = False):
        # current_prefix 즉시 처리 (UI 측 상태만)
        lows = [p.lower().rstrip("\\/") for p in paths]
        if any(self._current_prefix.lower().startswith(low) for low in lows):
            self._current_prefix = ""
            self._current_prefixes = []

        # UI 잠금 + 진행 표시. 실제 DB 삭제는 RemoveLibraryWorker 가 백그라운드
        # 에서. 직전 회귀에서 sync `self.manager.remove_root(p)` 루프가 UI freeze
        # 원인이었음. 200GB / 1M 행 삭제는 수 초~수십 초 걸림.
        self._removing = True
        self._removing_paths = list(paths)  # _on_remove_done 에서 탭/즐겨찾기 참조 정리용
        label = "완전 제거" if purge else "제거"
        self.add_btn.setEnabled(False)
        self._set_index_btns_enabled(False)
        self.fts_rebuild_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(
            f"라이브러리 {label} 중: {len(paths):,}개..."
        )
        self._set_status(f"라이브러리 {label} 중 — UI 사용 가능, 진행률은 상단 표시")
        self.global_loader.start(
            "remove",
            f"라이브러리 {len(paths):,}개 {label} 중" if len(paths) > 1
            else f"라이브러리 {label} 중"
        )

        self.center_loader.start("remove", f"라이브러리 {label} 중...")

        worker_cls = PurgeLibraryWorker if purge else RemoveLibraryWorker
        self.remove_worker = worker_cls(self.manager, paths)
        self.remove_thread = QThread()
        _hold_bg_thread(self.remove_thread, self.remove_worker)
        self.remove_worker.moveToThread(self.remove_thread)
        self.remove_thread.started.connect(self.remove_worker.run)
        self.remove_worker.finished.connect(
            self._on_purge_done if purge else self._on_remove_done)
        self.remove_worker.finished.connect(self.remove_thread.quit)
        self.remove_worker.finished.connect(self.remove_worker.deleteLater)
        self.remove_thread.finished.connect(self.remove_thread.deleteLater)
        QTimer.singleShot(0, self.remove_thread.start)

    def _on_folder_rename_request(self, path: str, display_name: str):
        key = os.path.normpath(path).rstrip("\\/").lower()
        self._folder_display_names[key] = display_name.strip()
        self._save_config()
        # sync _reload_tree 회귀 — get_all_folder_counts/incomplete_counts 가
        # UI 스레드에서 932k 행 fetch + 파이썬 normpath 루프 (수 초 멈춤).
        self._reload_tree_async()

    def _on_purge_done(self, n: int, message: str):
        """완전 제거 전용 마무리 — 재생 히스토리/폴더 표시이름 정리 후
        공통 완료 처리(_on_remove_done: 탭·즐겨찾기 정리, 트리 갱신)로 위임.
        _removing_paths 는 _on_remove_done 이 pop 하므로 그 전에 읽는다."""
        removed = list(getattr(self, "_removing_paths", None) or [])
        if n >= 0 and removed:
            try:
                self.player.remove_history_under(
                    [_lib_prefix(p) for p in removed])
            except Exception:
                logger.exception("완전 제거 — 재생 히스토리 정리 실패")
            # 폴더 표시이름(config) — key 는 normpath.rstrip.lower (rename 저장 형식)
            lows = [os.path.normpath(p).rstrip("\\/").lower() for p in removed]
            stale = [k for k in self._folder_display_names
                     if any(k == l or k.startswith(l + os.sep) for l in lows)]
            for k in stale:
                self._folder_display_names.pop(k, None)
            if stale:
                self._save_config()
        self._on_remove_done(n, message)

    def _on_remove_done(self, n: int, message: str):
        self._removing = False
        self.add_btn.setEnabled(True)
        self._set_index_btns_enabled(True)
        self.fts_rebuild_btn.setEnabled(True)
        self.global_loader.stop("remove")
        self.center_loader.stop("remove")
        QTimer.singleShot(2000, lambda: self.progress_bar.setVisible(False))
        if n < 0:
            self._set_status(message)
            return
        # 제거된 라이브러리(및 하위)를 즐겨찾기/사용자 탭 참조에서 정리 — 안 하면
        # 탭에 빈 stale 노드가 남아 클릭해도 결과가 안 나옴. 트리 rebuild 전에 수행.
        removed = getattr(self, "_removing_paths", None) or []
        self._removing_paths = []
        if removed:
            self._prune_tab_refs_for_removed(removed)
        # 삭제 후 트리/카운트 갱신 — 새 read-only Database 인스턴스 (캐시 리셋)
        self.db = self.manager.open_db_for_search()
        self._refresh_after_index_change()
        self._set_status(message)
        self._do_search()
        # 고아 중복 후보 — 팝업 승인 시에만 복원 (일반/완전 제거 공통)
        oids = list(getattr(self.remove_worker, "orphan_ids", None) or [])
        if oids and _prompt_orphan_dup_restore(self, str(self.manager.db_path), oids):
            self._do_search()

    # ─────────── 인덱싱 ───────────
    def _set_index_btns_enabled(self, enabled: bool):
        """헤더의 인덱싱 관련 버튼 (빠른/전체 갱신) 일괄 enable/disable."""
        self.quick_update_btn.setEnabled(enabled)
        self.full_update_btn.setEnabled(enabled)

    def _do_quick_update(self, scan_path: Optional[str] = None):
        """빠른 갱신: 변경 감지 → 다이얼로그 → 확인 시 인덱싱."""
        if not self.manager.get_roots():
            QMessageBox.information(self, "라이브러리 없음",
                                    "[+ 라이브러리 추가] 로 먼저 등록하세요.")
            return
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중", "다른 인덱싱 작업이 진행 중입니다")
            return
        scope = (f"폴더: {os.path.basename(scan_path.rstrip(chr(92) + '/'))}"
                 if scan_path else "전체 라이브러리")
        ret = QMessageBox.question(
            self, "빠른 갱신",
            f"{scope} 의 최근 변경 사항을 감지합니다.\n"
            "(새 파일/삭제된 파일만 처리되어 빠릅니다. "
            "메타데이터는 변경된 파일에만 다시 추출됩니다.)\n\n"
            "진행할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._start_detection(scan_path=scan_path)

    def _do_full_update(self):
        """전체 갱신: 모든 파일 강제 재스캔 (라이브러리 추가와 동일 hot path)."""
        if not self.manager.get_roots():
            QMessageBox.information(self, "라이브러리 없음",
                                    "[+ 라이브러리 추가] 로 먼저 등록하세요.")
            return
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중", "다른 인덱싱 작업이 진행 중입니다")
            return
        ret = QMessageBox.question(
            self, "전체 갱신",
            "모든 라이브러리를 강제 재스캔합니다.\n"
            "(라이브러리 추가와 동일한 속도. 기존 메타데이터는 보존됨.)\n\n"
            "⚠ '제거'했던 항목도 디스크에 파일이 남아 있으면 다시 인덱스에 돌아옵니다.\n"
            "   (중복 숨김 처리한 파일은 그대로 숨김 유지 — 복원은 환경설정에서.)\n\n"
            "진행할까요?"
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._start_index(scan_path=None, force_rescan=True)

    def _on_rescan_request(self, path: str):
        """폴더 트리 우클릭 → Rescan. 기본은 빠른 갱신 흐름."""
        self._on_rescan_many_request([path])

    def _on_rescan_many_request(self, paths: list):
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중", "다른 인덱싱 작업이 진행 중입니다")
            return
        paths = list(dict.fromkeys(str(p) for p in paths if p))
        if not paths:
            return
        self._start_index_many(paths, force_rescan=False)

    def _on_full_rescan_request(self, path: str):
        """폴더 트리 우클릭 → 강제 재스캔. mtime 무시하고 모든 파일 재확인."""
        self._on_full_rescan_many_request([path])

    def _on_full_rescan_many_request(self, paths: list):
        if self.manager.is_indexing():
            QMessageBox.warning(self, "진행 중", "다른 인덱싱 작업이 진행 중입니다")
            return
        paths = list(dict.fromkeys(str(p) for p in paths if p))
        if not paths:
            return
        label = f"{len(paths):,}개 폴더"
        ret = QMessageBox.question(
            self, "강제 재스캔",
            f"[{label}] 강제로 다시 스캔합니다.\n"
            "(변경 여부와 관계없이 모든 파일을 확인합니다.)\n\n"
            "진행할까요?"
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._start_index_many(paths, force_rescan=True)

    def _start_index_many(self, paths: list, force_rescan: bool = False):
        self._batch_index_active = True
        self._pending_index_jobs = [(p, force_rescan) for p in paths[1:]]
        self._multi_index_total = len(paths)
        self._multi_index_done = 0
        self._start_index(scan_path=paths[0], force_rescan=force_rescan)

    def _cancel_index(self):
        if not self.manager:
            return
        self.manager.cancel()
        self._cancelling = True
        # 진행 표시 즉시 정적으로 — polling/progress 메시지 모두 무시
        if hasattr(self, "_progress_poll_timer"):
            self._progress_poll_timer.stop()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("취소됨 — 진행 보관됨")
        self._set_status(
            "취소됨 — 스캔된 파일은 보관됨. "
            "[전체 인덱스 업데이트] 클릭하여 이어서 진행."
        )
        self.cancel_btn.setEnabled(False)

    def _start_index(self, scan_path: Optional[str] = None,
                     force_rescan: bool = False,
                     phase2_only: bool = False,
                     reset_scope_prefix: Optional[str] = None,
                     reset_scope_paths: Optional[list] = None,
                     reset_include_pending: bool = False,
                     reset_include_failed: bool = True):
        if self.manager.is_indexing():
            return
        if not getattr(self, "_batch_index_active", False):
            self._pending_index_jobs = []
            self._multi_index_total = 1
            self._multi_index_done = 0
        self._cancelling = False
        self._set_index_btns_enabled(False)
        self.add_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.fts_rebuild_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        if phase2_only:
            mode_tag = " (메타 재시도)"
        elif force_rescan:
            mode_tag = " (전체 강제)"
        else:
            mode_tag = ""
        if phase2_only:
            self.progress_bar.setFormat("Phase2 시작 — 메타 추출만 (Phase1 스킵)")
        else:
            self.progress_bar.setFormat(f"준비 중{mode_tag}... · 검색 색인 작성부터 % 표시")
        self.indexing_badge.setVisible(True)
        self.indexing_badge.setText(
            "● 메타 분석 중" if phase2_only else "● 인덱싱 중"
        )
        self._blink_timer.start()
        label = os.path.basename(scan_path.rstrip("\\/")) if scan_path else "전체"
        self.setWindowTitle(f"SoundField — 인덱싱 중 [{label}]{mode_tag}")
        self._set_status(
            "Phase2 메타 추출 중 — Phase1 스캔은 스킵됨." if phase2_only else
            "인덱싱 준비 중 — 1단계는 파일 목록 작성 "
            "(퍼센트 표시 없음) 후 검색 색인 작성에서 진행률 표시."
        )

        # Phase1 throttle 카운트 갱신 시작 시각 — 첫 갱신이 5초 후 일어나도록.
        self._last_phase1_count_refresh = time.monotonic()

        # 메타 재시도(phase2_only)는 전체화면 중앙로더 생략 — 현황 다이얼로그 배너 +
        # 진행바로 충분하고, 모달 뒤에서 도는 큰 로더가 오히려 거슬림.
        if not phase2_only:
            self.center_loader.start("index", "라이브러리 갱신 중...")

        self.index_worker = IndexWorker(
            self.manager, scan_path=scan_path,
            force_rescan=force_rescan,
            phase2_only=phase2_only,
            reset_scope_prefix=reset_scope_prefix,
            reset_scope_paths=reset_scope_paths,
            reset_include_pending=reset_include_pending,
            reset_include_failed=reset_include_failed,
        )
        self.index_thread = QThread()
        _hold_bg_thread(self.index_thread, self.index_worker)
        self.index_worker.moveToThread(self.index_thread)
        self.index_thread.started.connect(self.index_worker.run)
        self.index_worker.progress.connect(self._on_index_progress)
        self.index_worker.phase1_done.connect(self._on_phase1_done)
        self.index_worker.finished.connect(self._on_index_done)
        self.index_worker.finished.connect(self.index_thread.quit)
        QTimer.singleShot(0, self.index_thread.start)
        self._progress_poll_timer.start()

    def _start_detection(self, scan_path: Optional[str] = None):
        """빠른 갱신 1단계 — 변경 감지만 (DB write X). 완료 시 다이얼로그 표시."""
        if self.manager.is_indexing():
            return
        # detect 는 dry-run(DB 불변) → 진행 중 트리 카운트 갱신 불필요. 1.1M행을 훑는
        # get_all_folder_counts 워커가 detect 워커와 겹쳐 GIL 경합 → UI 멈춤 가중되므로 차단.
        self._detect_active = True
        self._set_index_btns_enabled(False)
        self.add_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.fts_rebuild_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("변경 감지 중... · 변경된 파일만 빠르게 스캔")
        self.indexing_badge.setVisible(True)
        self.indexing_badge.setText("● 변경 감지 중")
        self._blink_timer.start()
        label = os.path.basename(scan_path.rstrip("\\/")) if scan_path else "전체"
        self.setWindowTitle(f"SoundField — 변경 감지 중 [{label}]")
        self._set_status("변경 감지 중 — DB 변경 없음, 통계만 산정...")
        self._pending_detect_scan_path = scan_path
        self._pending_detect_label = label

        self.center_loader.start("detect", "변경 감지 중...")

        self.detect_worker = DetectChangesWorker(self.manager, scan_path=scan_path)
        self.detect_thread = QThread()
        _hold_bg_thread(self.detect_thread, self.detect_worker)
        self.detect_worker.moveToThread(self.detect_thread)
        self.detect_thread.started.connect(self.detect_worker.run)
        self.detect_worker.progress.connect(self._on_index_progress)
        self.detect_worker.progress.connect(self._on_detect_loader_progress)
        self.detect_worker.finished.connect(self._on_detect_done)
        self.detect_worker.finished.connect(self.detect_thread.quit)
        QTimer.singleShot(0, self.detect_thread.start)
        self._progress_poll_timer.start()

    def _on_detect_loader_progress(self, p: IndexProgress):
        """빠른 갱신 중앙 로더 진행률 — 전체 개수를 모르는 스캔이라 카운트 표시."""
        parts = [f"스캔 {p.scanned:,}개"]
        if p.new_count:
            parts.append(f"변경 +{p.new_count:,}")
        if p.current_root:
            parts.append(os.path.basename(p.current_root.rstrip("\\/")))
        self.center_loader.set_progress("detect", " · ".join(parts))

    def _on_detect_done(self, result: dict):
        """변경 감지 완료 → 다이얼로그 띄움. 사용자 OK 시 update_index 실행."""
        self._detect_active = False
        if hasattr(self, "_progress_poll_timer"):
            self._progress_poll_timer.stop()
        self._blink_timer.stop()
        self.indexing_badge.setVisible(False)
        self.setWindowTitle("SoundField")
        self._set_index_btns_enabled(True)
        self.add_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.fts_rebuild_btn.setEnabled(True)
        self.center_loader.stop("detect")
        QTimer.singleShot(1500, lambda: self.progress_bar.setVisible(False))
        self._set_status(result.get("message", ""))

        if not result.get("success"):
            return

        scope_label = getattr(self, "_pending_detect_label", "전체")
        scan_path = getattr(self, "_pending_detect_scan_path", None)
        total = (int(result.get("added", 0)) + int(result.get("updated", 0))
                 + int(result.get("deleted", 0)))
        if total == 0:
            QMessageBox.information(
                self, "변경사항 없음",
                f"[{scope_label}] 스캔 완료 ({result.get('elapsed', 0.0):.1f}s)\n"
                f"총 {result.get('scanned', 0):,}개 파일을 확인했으나 변경된 항목이 없습니다."
            )
            return

        dlg = ChangeReviewDialog(self, result, scope_label)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # 일반 갱신 (existing 비교 + Phase1 + Phase2) — 실제 변경만 처리됨
            self._start_index(scan_path=scan_path, force_rescan=False)
        # _lib_status_refresh_timer 는 인덱싱 중 시작하지 않음 (2초마다 root별
        # count 쿼리가 30 roots × 2 query = 60회 × UI 스레드 → WAL 락 경합 + 응답없음
        # 유발). lib_status 는 _on_index_done 에서 1회 갱신.

    def _poll_progress(self):
        if self.manager and self.manager.current_progress is not None:
            self._on_index_progress(self.manager.current_progress)

    def _on_phase1_done(self):
        # 1단계 완료 = 검색 가능. 중앙 인덱싱 로더 종료.
        self.center_loader.stop("index")
        if hasattr(self, "indexing_badge"):
            self.indexing_badge.setVisible(True)
            self.indexing_badge.setText("● 메타 분석 중")
        self.progress_bar.setFormat("파일 목록 완료 — 검색 가능 · 폴더 트리 갱신 중")
        self._set_index_btns_enabled(True)
        self.add_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._set_status("1단계 완료 (검색 가능) — 폴더 트리 갱신 중...")
        self._schedule_search()
        # 폴더 트리를 먼저 갱신(1단계 결과 반영)하고, 그게 끝나면 _on_tree_reload_done 이
        # '1단계 완료' 팝업 → 2단계(백그라운드 메타) 순서로 이어간다. (로더/팝업 겹침 정리)
        if self.manager and self.manager.db_path.exists():
            self.db = self.manager.open_db_for_search()
            self._pending_phase1_popup = True
            self._reload_tree_async()
        else:
            self._pending_phase1_popup = False
            self._show_phase1_done_popup()
            QTimer.singleShot(0, self._ensure_background_meta_running)

    def _show_phase1_done_popup(self):
        QMessageBox.information(
            self, "1단계 완료",
            "파일 목록 작성이 완료되었습니다.\n지금부터 검색이 가능하며, "
            "상세 오디오 분석(2단계)은 백그라운드에서 진행됩니다."
        )

    def _set_status(self, msg: str):
        """긴 메시지는 잘려서 표시되니 풀 버전은 tooltip 으로 보관."""
        self.status_label.setText(msg)
        self.status_label.setToolTip(msg)

    @staticmethod
    def _fmt_time(sec: float) -> str:
        """경과 시간 표시용 — 실제로 흐른 시간이므로 정확한 값을 그대로 보여준다."""
        sec = int(sec)
        if sec < 60:
            return f"{sec}s"
        if sec < 3600:
            return f"{sec//60}m {sec%60}s"
        return f"{sec//3600}h {(sec%3600)//60}m"

    @staticmethod
    def _fmt_remaining(sec: float) -> str:
        """남은 시간 표시용 — **정밀도를 일부러 낮춘다.**

        추정 오차가 실측 ±15% 수준(1시간 34분 참값에 1:20~1:50 관측)인데 초 단위로
        보여주면 0.3초마다 숫자가 바뀌어 "널뛴다"고 느껴진다 (사용자 지적 2026-09-07).
        없는 정밀도를 없다고 표시하는 것이 맞다 → 10분 단위로 뭉갠다.

        ⚠ 여기에 초를 다시 넣지 말 것. 그리고 이동 평균으로 바꾸는 것도 답이 아니다 —
          최근 구간에 민감해져 오히려 더 튄다. 누적 평균은 진행이 쌓이면 저절로 안정된다.

        ⚠ 반올림에 `round()` 를 쓰지 말 것. 파이썬 round 는 .5 에서 짝수로 붙어
          (round(6.5)==6) 65분이 "약 1시간" 으로 내려가고, PoC 의 JS Math.round
          (7 로 올림) 와 값이 갈린다 — 실측 0~6시간 구간에서 1,042곳 불일치.
          두 앱이 같은 문구를 보여야 하므로 양쪽 다 **.5 는 올림**으로 맞춘다.
        """
        sec = max(0.0, float(sec))
        if sec < 60:
            return "거의 끝남"
        half_up = lambda x: math.floor(x + 0.5)      # noqa: E731 (JS Math.round 와 동일)
        minutes = half_up(sec / 60.0)
        if minutes < 10:
            return f"약 {max(1, minutes)}분"
        minutes = half_up(minutes / 10.0) * 10
        if minutes < 60:
            return f"약 {minutes}분"
        hours, mins = divmod(minutes, 60)
        return f"약 {hours}시간" if mins == 0 else f"약 {hours}시간 {mins}분"

    def _on_index_progress(self, p: IndexProgress):
        # 취소 누른 후엔 백그라운드에서 잔여 emit 와도 UI 갱신 안 함
        if getattr(self, "_cancelling", False):
            return
        phase = p.phase
        folder = ""
        if p.current_file:
            d = p.current_file if os.path.isdir(p.current_file) else os.path.dirname(p.current_file)
            folder = os.path.basename(d) or d

        if phase == IndexProgress.PHASE_SCAN:
            if getattr(p, "phase1_counting", False):
                # 구버전 진행 객체 방어. 현재는 전체 개수 사전 파악 패스를 돌리지 않는다.
                self.progress_bar.setRange(0, 0)
                self.progress_bar.setFormat(
                    f"1단계/2 · 파일 목록 확인 중... {int(getattr(p,'count_seen',0)):,}개")
                self._set_status("파일 목록 확인 중 — 검색 색인 작성부터 진행률(%)이 표시됩니다.")
                self.center_loader.set_progress(
                    "index",
                    f"파일 목록 확인 중... {int(getattr(p,'count_seen',0)):,}개", -1)
                return
            elapsed = time.monotonic() - p.start_time if p.start_time else 0
            rate = int(p.scanned / elapsed) if elapsed > 0.5 and p.scanned else 0
            rate_txt = f" · {rate:,}/초" if rate > 0 else ""
            ft = int(getattr(p, "fts_total", 0) or 0)
            pt = int(getattr(p, "phase1_total", 0) or 0)
            if ft > 0:
                # 신규 경로 검색 색인 작성 단계 (대량 삽입) — 실측 % 게이지
                fd = int(getattr(p, "fts_done", 0) or 0)
                fpct = min(100, int(fd * 100 / ft))
                self.progress_bar.setRange(0, ft)
                self.progress_bar.setValue(fd)
                self.progress_bar.setFormat(
                    f"1단계/2 · 검색 색인 작성 · {fpct}% ({fd:,}/{ft:,})")
                self.center_loader.set_progress(
                    "index", f"검색 색인 작성 {fpct}% ({fd:,}/{ft:,})", fpct)
            elif pt > 0:
                # 재스캔은 기존 인덱스 개수를 분모로 실제 % 표시.
                ppct = min(99, int(p.scanned * 100 / pt))
                self.progress_bar.setRange(0, pt)
                self.progress_bar.setValue(min(p.scanned, pt))
                self.progress_bar.setFormat(
                    f"1단계/2 · 파일 목록 작성 {ppct}% ({p.scanned:,}/{pt:,}){rate_txt}")
                self.center_loader.set_progress(
                    "index", f"파일 목록 작성 {ppct}% ({p.scanned:,}/{pt:,}){rate_txt}", ppct)
            elif p.scanned == 0:
                self.progress_bar.setRange(0, 0)
                self.progress_bar.setFormat("1단계/2 · 스캔 시작... · 검색 색인 작성부터 % 표시")
                self.center_loader.set_progress("index", "스캔 시작...", -1)
            else:
                # 첫 인덱싱 — 전체 개수 사전 파악 없이 바로 훑는다.
                # 정확한 % 는 신규 파일 수가 확정된 뒤 검색 색인 작성 단계에서 표시.
                self.progress_bar.setRange(0, 0)
                self.progress_bar.setFormat(
                    f"1단계/2 · {p.scanned:,}개 발견 · {self._fmt_time(elapsed)}{rate_txt} · "
                    f"검색 색인 작성부터 % 표시")
                self.center_loader.set_progress(
                    "index",
                    f"{p.scanned:,}개 발견{rate_txt} · 검색 색인 작성부터 % 표시", -1)
            root_tag = f"[{os.path.basename(p.current_root.rstrip(os.sep))}] " if p.current_root else ""
            msg = (
                f"{root_tag}파일 목록 작성 중 (1단계/2) — "
                f"파일 수 확인 중엔 전체 진행률 없음, 검색 색인 작성부터 % 표시. "
                f"신규 {p.new_count:,} · 변경없음 {p.skipped:,} · "
                f"언제든 취소 가능 — 진행 보관됨."
            )
            if folder:
                msg += f" · 폴더: {folder}"
            self._set_status(msg)
            # Phase1 동안 미완료 인디케이터 / 폴더 트리 root 카운트 주기 갱신.
            # _reload_tree_async 는 load_folders() 에서 트리 clear + rebuild 라 매 호출
            # 1초+ UI freeze (사용자 보고). 가벼운 카운트 전용 워커 + update_counts()
            # 부분 갱신으로 분기 — 트리 노드 재생성 없이 카운트 셀만 setData.
            now_t = time.monotonic()
            # detect(dry-run) 중엔 트리 카운트 갱신 스킵 — DB 불변 + GIL 경합 가중 방지.
            if (not getattr(self, "_detect_active", False)
                    and now_t - getattr(self, "_last_phase1_count_refresh", 0.0) >= 5.0):
                self._last_phase1_count_refresh = now_t
                self._refresh_tree_counts_async()
            return
        elif phase == IndexProgress.PHASE_META:
            if p.total > 0:
                # ⚠ p.indexed 는 **이번 조각**만 센다. p.total 은 세션 분모라
                # (index_meta.meta_session_total, 조각/앱 재시작을 넘어 유지)
                # 분자도 세션 누적인 p.meta_done 을 써야 짝이 맞는다.
                done = p.meta_done
                self.progress_bar.setRange(0, p.total)
                self.progress_bar.setValue(done)
                eta = p.eta_seconds
                eta_txt = (f" · 남은 시간: {self._fmt_remaining(eta)}" if eta
                           else " · 남은 시간: 계산 중")
                self.progress_bar.setFormat(
                    f"2단계/2 · 분석 중 · {p.percent}% ({done:,}/{p.total:,}){eta_txt}"
                )
                msg = (
                    f"오디오 분석 중 (2단계/2) · {done:,}/{p.total:,} · "
                    f"오류 {p.errors:,} · 언제든 취소 가능 — 진행 보관됨."
                )
                if folder:
                    msg += f" · 폴더: {folder}"
                self._set_status(msg)
                # status bar 인디케이터 — 남은 미완료 카운트로 갱신
                remaining = max(0, p.total - done)
                self._update_meta_indicator(remaining, active=True)
            else:
                self.progress_bar.setRange(0, 1); self.progress_bar.setValue(1)
                self.progress_bar.setFormat("분석할 항목 없음")
                self._set_status(p.message or "")
                self._update_meta_indicator(0, active=False)
            return
        elif phase == IndexProgress.PHASE_DONE:
            self.progress_bar.setRange(0, 1); self.progress_bar.setValue(1)
            self.progress_bar.setFormat("완료 · 100%")

        self._set_status(p.message or "")

    def _refresh_lib_status_only(self):
        """글로우 색만 갱신 (인덱싱 중 주기 호출). DB 카운트는 캐시 없음 — 가벼움."""
        if not hasattr(self, "lib_status"):
            return
        try:
            self.lib_status.refresh_async(self.manager)
        except Exception:
            pass

    def _on_index_done(self, result: dict):
        self._multi_index_done = int(getattr(self, "_multi_index_done", 0)) + 1
        pending_jobs = getattr(self, "_pending_index_jobs", [])
        if pending_jobs and not getattr(self, "_cancelling", False):
            next_path, next_force = pending_jobs.pop(0)
            self._pending_index_jobs = pending_jobs
            self._start_index(scan_path=next_path, force_rescan=next_force)
            return
        if hasattr(self, "_progress_poll_timer"):
            self._progress_poll_timer.stop()
        if hasattr(self, "_lib_status_refresh_timer"):
            self._lib_status_refresh_timer.stop()
        self._blink_timer.stop()
        self.indexing_badge.setVisible(False)
        self.setWindowTitle("SoundField")
        self._set_index_btns_enabled(True)
        self.add_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.fts_rebuild_btn.setEnabled(True)
        self.center_loader.stop("index")
        # Phase2 종료 — 인디케이터 active 해제, 남은 미완료 카운트 표시
        try:
            db_ro = self.manager.open_db_for_search()
            self._update_meta_indicator(db_ro.count_incomplete_total(), active=False)
        except Exception:
            self._update_meta_indicator(0, active=False)
        was_cancelled = getattr(self, "_cancelling", False)
        self._cancelling = False
        self._batch_index_active = False
        self._pending_index_jobs = []
        # 진행바 숨김은 곧 이어질 Phase2(배경 메타) 게이지와 껌뻑이지 않게,
        # 배경 메타가 안 돌 때만. (돌면 _on_background_meta_progress 가 계속 갱신)
        QTimer.singleShot(3000, lambda: (
            None if self._is_background_meta_running()
            else self.progress_bar.setVisible(False)))
        if self.manager and self.manager.db_path.exists():
            self.db = self.manager.open_db_for_search()
            self._invalidate_search_signature()
            # 총 파일수 라벨은 아래 트리 갱신(_on_tree_reload_done/_on_tree_counts_done)이
            # 비동기로 갱신 → 메인 스레드 동기 count_files()(100만행 ~1s 프리즈) 제거.
            self.lib_status.refresh_async(self.manager)
            self._check_fts_stale()
            # 메타 재시도는 폴더 구조 불변 → 카운트만 갱신(로더 없음).
            # 정상 인덱싱의 구조 갱신은 _on_phase1_done 이 담당(팝업 순서 정리) → 여기선 중복 X.
            # 취소는 phase1_done 이 안 떴을 수 있어 여기서 갱신.
            if result.get("phase2_only"):
                self._refresh_tree_counts_async()
            elif was_cancelled:
                self._reload_tree_async()
        if not was_cancelled:
            message = result.get("message", "완료")
            total_jobs = int(getattr(self, "_multi_index_total", 1))
            if total_jobs > 1:
                message = f"선택 폴더 {total_jobs:,}개 스캔 완료"
            self._set_status(message)
            # 갱신 중 '디스크에서 사라진 파일' 정리로 생긴 고아 중복 후보 —
            # 갱신 완료 시점 팝업으로 복원 확인 (사용자 확정 정책).
            prog0 = result.get("progress", None)
            oids = list(getattr(prog0, "orphan_dup_ids", None) or [])
            if oids and _prompt_orphan_dup_restore(
                    self, str(self.manager.db_path), oids):
                self._do_search()
            # 완료 팝업 시점 — 일반 인덱싱은 여기서 Phase1 만 끝났고 Phase2(메타)는
            # BackgroundMetaWorker 가 이어서 한다. 변경분이 있으면 팝업을 Phase2 완료
            # (_on_background_meta_done)로 미루고, 변경이 없으면(분석할 것 없음) 지금 알림.
            # '1단계 완료' 팝업은 _on_tree_reload_done 에서 (트리 갱신 후) 뜬다.
            # 변경분이 있으면(nc>0) 2단계 완료 시 '갱신 완료 + 요약' 팝업을 한 번 더 띄운다.
            if not result.get("phase2_only"):
                nc = int(getattr(prog0, "new_count", 0) or 0)
                sc = int(getattr(prog0, "scanned", 0) or 0)
                self._rescan_summary = {"scanned": sc, "new": nc, "message": message}
                self._await_meta_completion = (nc > 0)
            # 범위 한정 재시도(phase2_only + run_id) → 성공/실패 결과 팝업.
            if result.get("phase2_only"):
                prog = result.get("progress", None)
                run_id = getattr(prog, "phase2_run_id", "") if prog else ""
                if run_id:
                    # 성공 개수는 진행객체(이 run 처리수)에서 — 성공 행은 phase2_run_id 가
                    # NULL 로 비워지므로 DB run_id 집계로는 못 셈. 실패 사유만 run_id 로 집계.
                    self._show_retry_result(run_id, int(getattr(prog, "indexed", 0)))

    def _show_retry_result(self, run_id: str, success: int):
        """재시도 완료 결과 팝업 — 성공 N / 실패 M (+ 사유별 개수)."""
        try:
            o = self.manager.open_db_for_search().phase2_run_outcome(run_id)
        except Exception:
            return
        failed, reasons = o["failed"], o["reasons"]
        if success == 0 and failed == 0:
            return  # 대상 없음 — 조용히 넘어감
        ERR_MAP = {
            "Unsupported format": "지원하지 않는 파일 형식",
            "FileNotFoundError": "파일을 찾을 수 없음",
            "PermissionError": "접근 권한 없음",
            "Timeout": "분석 시간 초과 (대용량/네트워크 지연)",
            "Ignored sidecar file": "무시된 사이드카 파일",
            "Unknown extraction error": "알 수 없는 추출 오류",
            "OSError": "OS 입출력 오류",
            "EOFError": "파일 끝(EOF) 오류 (손상 가능성)",
            "ValueError": "데이터 형식 오류",
        }
        def _kor(err: str) -> str:
            for eng, kor in ERR_MAP.items():
                if eng in err:
                    return kor
            return err
        lines = [f"성공: {success:,}개", f"실패: {failed:,}개"]
        if reasons:
            lines.append("")
            lines.append("실패 사유:")
            for err, cnt in reasons[:8]:
                lines.append(f"  · {_kor(err)} — {cnt:,}개")
            if len(reasons) > 8:
                lines.append(f"  · 그 외 {len(reasons) - 8}종")
        icon = QMessageBox.Icon.Information if failed == 0 else QMessageBox.Icon.Warning
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle("재시도 완료")
        box.setText("\n".join(lines))
        box.exec()


    def _blink_badge(self):
        self._blink_state = not self._blink_state
        color = "#e74c3c" if self._blink_state else "#a93226"
        self.indexing_badge.setStyleSheet(_badge_style(color))

    def _sync_blacklist_to_tree(self):
        """현재 DB blacklist_paths 를 트리에 적용. 트리는 캐시 보관 후 load_folders
        끝마다 자동으로 setHidden 적용 — 노드는 그대로 두고 가시성만 토글."""
        if not getattr(self, "tree", None):
            return
        try:
            rows = self.db.get_blacklist_paths() if self.db else []
        except Exception:
            rows = []
        self.tree.set_blacklist_paths(rows)

    def _on_blacklist_changed(self):
        self._sync_blacklist_to_tree()
        self._schedule_search(force=True)

    def _on_blacklist_expanded_changed(self, expanded: bool):
        """블랙리스트 접힘/펼침 시 splitter 사이즈 조정 + 핸들 enabled 토글.
        - 접힘: blacklist 헤더만 (~40px), tree 가 나머지. 핸들 드래그 비활성.
        - 펼침: 저장된 사용자 사이즈 또는 default(220px). 핸들 드래그 활성.
        """
        if not hasattr(self, "side_splitter"):
            return
        total = sum(self.side_splitter.sizes()) or self.side_splitter.height()
        if total <= 0:
            return
        if expanded:
            saved = int(self._config_data.get("blacklist_expanded_h", 220))
            saved = max(80, min(total - 100, saved))
            self.side_splitter.setSizes([max(100, total - saved), saved])
        else:
            collapsed = self.blacklist_panel.collapsed_height()
            self.side_splitter.setSizes([total - collapsed, collapsed])
        # 핸들 enabled 토글 — 접힘 시 드래그 불가
        h = self.side_splitter.handle(1)
        if h is not None:
            h.setEnabled(expanded)

    def _on_side_splitter_moved(self, *_):
        """사용자 드래그 — 펼침 상태에서만 사이즈 저장 (접힘 사이즈는 고정값)."""
        if not hasattr(self, "blacklist_panel"):
            return
        if not self.blacklist_panel.is_expanded():
            return
        sizes = self.side_splitter.sizes()
        if len(sizes) == 2 and sizes[1] > 40:
            self._config_data["blacklist_expanded_h"] = sizes[1]
            self._save_config()

    def _on_folder_selected(self, prefix: str):
        self._current_prefix = prefix
        self._current_prefixes = [prefix] if prefix else []
        # currentItemChanged 와 selectionChanged 가 한 클릭에서 연달아 온다.
        # 실제 검색은 selectionChanged(foldersSelected)의 최종 선택 상태만 기준으로
        # 한 번 실행한다. 여기서 즉시 검색하면 빠른 A→B 클릭 때 요청이 중복/역순으로
        # 섞여 최신 폴더가 반영되지 않는 체감 버그가 난다.

    def _on_active_tab_changed(self):
        """탭 전환 시 — 옛 탭의 폴더 선택 상태는 의미 없음 (탭마다 다른 트리).
        리셋 후 _do_search 가 active_tab_prefixes 로 자동 필터."""
        self._current_prefix = ""
        self._current_prefixes = []
        self._schedule_search()

    def _on_folders_selected(self, prefixes: list):
        self._current_prefixes = list(dict.fromkeys(str(p) for p in prefixes if p))
        self._current_prefix = self._current_prefixes[0] if len(self._current_prefixes) == 1 else ""
        self._search_timer.stop()
        self._invalidate_search_signature()
        self._do_search()

    def _invalidate_search_signature(self):
        self._last_search_signature = None

    def _on_filter_changed(self, *_):
        """길이/SR/채널 필터 변경 — 입력 중 이전 검색 batch append 는 즉시 중단."""
        self._update_filter_reset_btn_style()
        self._cancel_pending_search_stream()
        self._search_timer.start()

    def _schedule_search(self, force: bool = False):
        if force:
            self._invalidate_search_signature()
        self._cancel_pending_search_stream()
        self._search_timer.start()

    def _cancel_pending_search_stream(self):
        if not self._pending_search:
            return
        self._search_gen.value += 1
        self._pending_search = None
        self._search_busy_timer.stop()

    def _refresh_search_after_meta_flush(self):
        """Phase2 flush 뒤 현재 결과 테이블을 천천히 재검색.
        Phase2 throughput 와 main thread GIL contention 트레이드오프 —
        너무 자주 갱신하면 Phase2 worker stall. 30초 간격으로 충분.
        사용자가 검색 자체 누르면 즉시 갱신됨.
        """
        now = time.monotonic()
        if now - self._last_meta_search_refresh < 30.0:
            return
        self._last_meta_search_refresh = now
        self._schedule_search(force=True)

    _SR_MAP = {
        "44.1K": 44100, "48K": 48000, "88.2K": 88200, "96K": 96000, "192K": 192000,
    }
    _CH_MAP = {"MONO": 1, "STEREO": 2, "4CH": 4, "5.1 / 6CH": 6}
    _CH_MIN_MAP = {"7채널 이상": 7}

    def _do_search(self):
        self._update_filter_reset_btn_style()
        if not self.db:
            return
        sr_txt, ch_txt = self.sr_combo.currentText(), self.ch_combo.currentText()
        sr = self._SR_MAP.get(sr_txt)
        ch = self._CH_MAP.get(ch_txt)
        min_ch = self._CH_MIN_MAP.get(ch_txt)
        min_d = float(self.min_dur.value())
        max_d = float(self.max_dur.value()) if self.max_dur.value() > 0 else None

        # 사용자가 폴더를 명시 선택한 게 없으면 활성 탭의 자동 prefix 사용
        # (즐겨찾기 탭 → favorites, 사용자 탭 → tab.items, 전체 탭 → 빈 리스트).
        effective_prefixes = self._current_prefixes
        if not effective_prefixes:
            effective_prefixes = self.library_tabs.active_tab_prefixes()

        search_prefixes = []
        for prefix in effective_prefixes:
            if prefix == "__FAVORITES__":
                search_prefixes.extend(self._favorites)
                continue
            if prefix and not prefix.endswith(("\\", "/")):
                prefix = prefix.rstrip("\\/") + os.sep
            if prefix:
                search_prefixes.append(prefix)

        limit = self._config_data.get("search_limit", 500)
        matchers = self.multi.matchers()
        signature = (
            tuple(
                (
                    m.get("field") or "",
                    m.get("operator") or "",
                    (m.get("value") or "").strip(),
                )
                for m in matchers
            ),
            float(min_d),
            None if max_d is None else float(max_d),
            sr,
            ch,
            min_ch,
            tuple(search_prefixes or ()),
            int(limit),
            bool(self.multi.is_precise()),   # 정확한 검색 토글 변경 시 재검색
        )
        if signature == self._last_search_signature:
            return

        if not self._ensure_search_worker():
            return
        # 새 세대 발급 — 진행 중/대기 중인 옛 요청은 워커가 실행 전에 스킵
        self._search_gen.value += 1
        gen = self._search_gen.value
        # 검색 기준 브레드크럼 — 선택이 화면 밖이어도 "어디 기준" 확인.
        # status_label 은 시각상 잘려도 _set_status 가 풀 메시지를 tooltip 에 보관.
        if len(self._current_prefixes) > 1:
            first = self._current_prefixes[0]
            scope = (f" [폴더 {len(self._current_prefixes):,}개: {first} 외 "
                     f"{len(self._current_prefixes) - 1}곳]")
        elif self._current_prefix:
            scope = f" [기준: {self._current_prefix}]"
        else:
            scope = ""
        self._pending_search = (gen, signature, scope, 0)
        self._search_busy_timer.start()
        self._search_worker.request.emit(gen, {
            "matchers": matchers,
            "min_duration": min_d, "max_duration": max_d,
            "sample_rate": sr, "channels": ch, "min_channels": min_ch,
            "path_prefixes": search_prefixes or None,
            "limit": limit,
            "precise": self.multi.is_precise(),   # 정확한 검색(단어색인) 토글
        })

    def _on_search_busy_timeout(self):
        if not self._pending_search:
            return
        self._set_status("검색 중...")

    def _ensure_search_worker(self) -> bool:
        """검색 상주 워커 스레드 1회 생성. 참조는 인스턴스 속성으로 유지 (GC 방지)."""
        if self._search_thread is not None:
            return True
        if not self.manager:
            return False
        self._search_thread = QThread(self)
        self._search_worker = _SearchWorker(str(self.manager.db_path), self._search_gen)
        self._search_worker.moveToThread(self._search_thread)
        self._search_worker.batchReady.connect(self._on_search_batch)
        self._search_thread.start()
        return True

    def _on_search_result(self, gen: int, rows: object, error: str):
        """검색 워커 결과 수신 (메인 스레드). 최신 세대만 반영."""
        if gen != self._search_gen.value or not self._pending_search:
            return  # 그 사이 새 검색이 발급됨 — 옛 결과 폐기
        _g, signature, scope, _count = self._pending_search
        self._pending_search = None
        self._search_busy_timer.stop()
        if error:
            self._set_status(f"오류: {error}")
            return  # signature 미저장 — 다음 시도에서 재검색됨
        self._last_search_signature = signature
        self.results.set_results(rows)
        self._set_status(f"결과 {len(rows)}개{scope}")

    def _on_search_batch(self, gen: int, rows: object, done: bool, error: str):
        if gen != self._search_gen.value or not self._pending_search:
            return
        _g, signature, scope, count = self._pending_search
        batch = list(rows or [])
        if error:
            self._pending_search = None
            self._search_busy_timer.stop()
            self._set_status(f"오류: {error}")
            return
        if batch:
            if count == 0:
                self.results.begin_results_stream()
                self._search_busy_timer.stop()
            self.results.append_results(batch)
            count += len(batch)
            self._pending_search = (gen, signature, scope, count)
            now = time.monotonic()
            if (count == len(batch)
                    or now - getattr(self, "_last_search_status_update", 0.0) >= 0.2):
                self._last_search_status_update = now
                self._set_status(f"결과 {count:,}개 로딩 중{scope}")
        if done:
            if count == 0:
                self.results.begin_results_stream()
            self.results.finish_results_stream()
            self._pending_search = None
            self._search_busy_timer.stop()
            self._last_search_signature = signature
            self._set_status(f"결과 {count:,}개{scope}")

    def _on_file_activated(self, path: str):
        # 단일 클릭 / 키보드 nav 자동 재생 — auto_preview OFF면 스킵.
        # 더블클릭 명시 재생은 manualPlayRequested → _on_manual_play 로 분리됨.
        if not path:
            return
        if not self._config_data.get("auto_preview", False):
            return
        if self.manager.is_indexing():
            return
        self.results.set_pip(path, "loading")
        self.player.load_and_play(path, meta=self.results.get_meta_for_path(path))

    def _on_play_toggle(self, path: str):
        if path:
            self.results.set_pip(path, "loading")
            self.player.toggle_for_path(path, meta=self.results.get_meta_for_path(path))

    def _toggle_selected_playback(self):
        paths = self.results.selected_paths()
        path = paths[0] if paths else ""
        self.player.toggle_keyboard_for_path(path, meta=self.results.get_meta_for_path(path))

    def closeEvent(self, e):
        self._actual_save_config()
        if self.manager:
            self.manager.cancel()
        if self.meta_thread and self.meta_thread.isRunning():
            self.meta_thread.quit()
            self.meta_thread.wait(1500)
        if self.incomplete_thread and self.incomplete_thread.isRunning():
            self.incomplete_thread.quit()
            self.incomplete_thread.wait(1500)
        if self.index_thread and self.index_thread.isRunning():
            self.index_thread.quit()
            self.index_thread.wait(1500)
        if self._search_thread is not None and self._search_thread.isRunning():
            self._search_gen.value += 1  # 진행 중 요청 무효화 (워커가 실행 전 스킵)
            self._search_thread.quit()
            self._search_thread.wait(1500)
        if hasattr(self.player, "shutdown_audio"):
            self.player.shutdown_audio()
        else:
            self.player.stop()
        super().closeEvent(e)
