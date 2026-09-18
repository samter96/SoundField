import ctypes
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Dict, Optional

from PyQt6.QtCore import Qt, QEvent, QMimeData, QUrl, QPoint, QPointF, QRect, QRectF, QSize, QTimer, pyqtSignal
from app.ui.anim import FRAME_MS
from app.ui.rounded_scrollbar import RoundedScrollBar
from app.ui.theme import COLORS
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QDrag, QFont, QFontMetrics, QKeyEvent, QMouseEvent,
    QPainter, QPalette, QPen, QPixmap, QPolygonF, QStaticText, QTextOption
)
from PyQt6.QtWidgets import (
    QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QMenu,
    QToolTip,
)

logger = logging.getLogger(__name__)
_DENSE_LIST_TOOLTIP_DELAY_MS = 650

_VK_LBUTTON = 0x01


def _left_button_down_now() -> bool:
    """좌클릭이 *지금 물리적으로* 눌려있는지 — 큐와 무관한 실시간 OS 상태.

    QApplication.mouseButtons() 는 Qt 가 이벤트를 처리하며 갱신하는 '캐시된' 값이라,
    버튼을 이미 뗐어도 release 이벤트가 큐에 남아 처리 전이면 여전히 '눌림'으로 보고한다.
    그 상태에서 stale move 이벤트로 drag.exec() 가 시작되면 떼는 이벤트가 없어 라벨이
    커서에 붙는다. Windows 는 GetAsyncKeyState 로 진짜 물리 상태를 즉시 읽는다.
    (마우스 버튼 스왑 시 VK_LBUTTON 은 주 버튼을 따른다 — 드래그 의도와 일치.)
    """
    if os.name != "nt":
        return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(_VK_LBUTTON) & 0x8000)
    except Exception:
        return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)


# 디자인 토큰 (theme.py 의 COLORS 와 동기) — accent/state 컬러는 다크/라이트 둘 다
# 비슷한 hex 라 module 상수로 유지. 배경/행 컬러는 paint 시점에 COLORS 에서 lookup
# (테마 토글 즉시 반영).
_C_ACCENT      = QColor("#4E90E8")
_C_ACCENT_2    = QColor("#6BAAF5")
_C_ACCENT_DIM  = QColor(78, 144, 232, 38)
_C_TEAL        = QColor("#7BB4F5")
_C_WARM        = QColor("#ffb84d")
_C_LOADING     = QColor("#ff5a5a")   # 로딩 박동 (빨강)
_C_PLAYING     = QColor("#58d97c")   # 재생 박동 (초록)
_C_ENDED       = QColor("#4E90E8")   # 재생종료 점등 (파란)
_C_TEXT        = QColor("#e6ebf2")
_C_TEXT_2      = QColor("#9ba6b8")
_C_TEXT_3      = QColor("#5d6779")
_C_BG_INPUT    = QColor("#0d1119")
_C_BG_ELEV     = QColor("#161b26")
_C_ROW_ALT     = QColor("#12161f")
_C_BORDER      = QColor(255, 255, 255, 16)


_FMT_COLOR_MAP = {
    "wav":  (_C_ACCENT,             QColor(78, 144, 232, 64)),     # 파랑
    "flac": (QColor("#2DD4BF"),     QColor(45, 212, 191, 60)),     # 청록 (lossless 표시)
    "mp3":  (_C_WARM,               QColor(255, 184, 77, 60)),     # 주황
    "aif":  (QColor("#E879F9"),     QColor(232, 121, 249, 60)),    # 마젠타
    "aiff": (QColor("#E879F9"),     QColor(232, 121, 249, 60)),    # 마젠타
    "ogg":  (QColor("#A06CFF"),     QColor(160, 108, 255, 60)),    # 보라
}


def _fmt_of(path: str) -> str:
    if not path:
        return ""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext[:4]


class FilenameCellDelegate(QStyledItemDelegate):
    """파일명 컬럼: play-pip (8px 원) + fmt 배지 (WAV/FLAC/MP3) + 파일명 텍스트.

    HTML 의 .tbl-row .col.fname { display: flex; align-items: center; gap: 8px; } 와
    .play-pip / .fmt / .fmt.wav 등 룰을 paint 로 재현.
    재생 중인 행은 pulse-pip 애니메이션 + 첫 셀 텍스트 accent-2.
    """

    PIP_SIZE = 8
    BADGE_H = 16
    BADGE_PAD_H = 6
    GAP = 8
    PAD_LEFT = 10

    def __init__(self, table: "ResultsTable"):
        super().__init__(table)
        self._table = table
        self._pulse_phase = 0.0
        self._compact = False  # True 시 fmt 배지/파일명 폰트 1pt 축소
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(FRAME_MS)  # 120fps
        self._timer.timeout.connect(self._on_pulse)
        # playing path 가 있으면만 활성. 초기 정지.

    def set_compact(self, compact: bool):
        if self._compact != compact:
            self._compact = compact

    def set_pulsing(self, active: bool):
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active and self._timer.isActive():
            self._timer.stop()

    def _on_pulse(self):
        # 0.9초 주기 sin wave (120fps 기준)
        self._pulse_phase = (self._pulse_phase + FRAME_MS / 900.0) % 1.0
        # playing row만 redraw — 비용 적음
        if self._table._playing_row >= 0:
            idx = self._table.model().index(self._table._playing_row, 0)
            self._table.update(idx)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        # Antialiasing 만 켬 (도형용). TextAntialiasing 은 명시 안 함 → 시스템
        # default (Windows ClearType subpixel) 활용. 명시하면 grayscale AA 강제됨.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = option.rect
        row = index.row()
        is_playing = (row == self._table._playing_row)
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # 배경 (playing 일 때 gradient + 좌측 라인)
        if is_playing:
            # linear-gradient(90deg, accent-dim 0%, transparent 60%)
            grad_w = int(rect.width() * 0.6)
            for i in range(grad_w):
                a = int(38 * (1.0 - i / max(1, grad_w)))
                painter.setPen(QColor(78, 168, 255, a))
                painter.drawLine(rect.left() + i, rect.top(), rect.left() + i, rect.bottom())
            # 좌측 inset accent line
            painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()), _C_ACCENT)
        elif is_selected:
            painter.fillRect(rect, QColor(78, 168, 255, 46))
            painter.fillRect(QRect(rect.left(), rect.top(), 2, rect.height()), _C_ACCENT)
        elif is_hover:
            painter.fillRect(rect, QColor(COLORS["row_hover"]))
        elif row % 2 == 1:
            painter.fillRect(rect, QColor(COLORS["row_alt"]))

        # data
        path = ""
        first = self._table.item(row, 0)
        if first:
            p = first.data(Qt.ItemDataRole.UserRole)
            if isinstance(p, str):
                path = p
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        fmt = _fmt_of(path)

        x = rect.left() + self.PAD_LEFT
        cy = rect.center().y()

        # play-pip — 상태별 색/박동
        pip_r = self.PIP_SIZE
        pip_rect = QRectF(x, cy - pip_r / 2, pip_r, pip_r)
        state = getattr(self._table, "_pip_state", "")
        pip_path = getattr(self._table, "_pip_path", "")
        is_pip_row = bool(pip_path) and pip_path == path
        if is_pip_row and state == "loading":
            # 빨강 박동
            phase = abs(0.5 - self._pulse_phase) * 2.0
            glow = 4 + int(6 * (1.0 - phase))
            painter.setPen(Qt.PenStyle.NoPen)
            for r_off in range(glow, 0, -2):
                a = int(80 * (1.0 - r_off / max(1, glow)))
                painter.setBrush(QColor(255, 90, 90, a))
                painter.drawEllipse(pip_rect.adjusted(-r_off, -r_off, r_off, r_off))
            painter.setBrush(_C_LOADING)
            painter.drawEllipse(pip_rect)
        elif is_pip_row and state == "playing":
            # 초록 박동
            phase = abs(0.5 - self._pulse_phase) * 2.0
            glow = 4 + int(6 * (1.0 - phase))
            painter.setPen(Qt.PenStyle.NoPen)
            for r_off in range(glow, 0, -2):
                a = int(75 * (1.0 - r_off / max(1, glow)))
                painter.setBrush(QColor(88, 217, 124, a))
                painter.drawEllipse(pip_rect.adjusted(-r_off, -r_off, r_off, r_off))
            painter.setBrush(_C_PLAYING)
            painter.drawEllipse(pip_rect)
        elif is_pip_row and state == "ended":
            # 파란 점등
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_C_ENDED)
            painter.drawEllipse(pip_rect)
        elif is_selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_C_ACCENT)
            painter.drawEllipse(pip_rect)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_C_TEXT_3)
            painter.drawEllipse(pip_rect)
        x += pip_r + self.GAP

        # fmt 배지
        if fmt:
            color, border_col = _FMT_COLOR_MAP.get(fmt, (_C_TEXT_2, _C_BORDER))
            mono = QFont()
            mono.setFamilies(["Cascadia Code", "Cascadia Mono", "Consolas", "JetBrains Mono"])
            mono.setStyleHint(QFont.StyleHint.Monospace)
            mono.setPointSize(7 if self._compact else 8)
            mono.setWeight(QFont.Weight.Bold)
            # hinting/strategy 는 시스템 default 위임 — native rendering path 활용
            painter.setFont(mono)
            fmt_text = fmt.upper()
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(fmt_text)
            badge_h = self.BADGE_H - 2 if self._compact else self.BADGE_H
            badge_w = tw + self.BADGE_PAD_H * 2
            badge_rect = QRectF(x, cy - badge_h / 2, badge_w, badge_h)
            # bg
            painter.setBrush(QColor(COLORS["bg_control"]))
            painter.setPen(QPen(border_col, 1))
            painter.drawRoundedRect(badge_rect, 2, 2)
            painter.setPen(color)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, fmt_text)
            x += badge_w + self.GAP

        # 파일명 — Qt native delegate path 로 그리기 (ClearType subpixel 활용).
        # painter.drawText() 직접 호출은 grayscale anti-alias 만 됨 → 거칠게 보임.
        # super().paint() 를 우리가 만든 option 으로 호출하면 Qt 가 native widget
        # 텍스트 path 와 동일한 raster path 사용 — subpixel ClearType 적용.
        # is_playing 강조 — 현재 테마 accent (라이트=다크 갈색 / 다크=파랑)
        name_color = QColor(COLORS["accent"]) if is_playing else QColor(COLORS["text"])
        # 폰트 family 명시 제거 — 시스템 default font 사용 (option.font 그대로 활용)
        name_font = QFont(option.font)
        name_font.setPointSize(9 if self._compact else 10)
        if is_playing:
            name_font.setWeight(QFont.Weight.DemiBold)
        # hinting/strategy 는 시스템 default 위임 — native rendering path 활용

        text_rect = QRect(x, rect.top() + 1, rect.right() - x - 6, rect.height() - 1)
        fm2 = painter.fontMetrics()
        # 우리 폰트 기준 elide
        elide_metrics = painter.fontMetrics() if painter.font() == name_font else None
        # painter 폰트 임시 변경하여 elide width 정확히 계산
        painter.save()
        painter.setFont(name_font)
        elided = painter.fontMetrics().elidedText(
            str(text), Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.restore()

        text_opt = QStyleOptionViewItem(option)
        text_opt.rect = text_rect
        text_opt.text = elided
        text_opt.font = name_font
        text_opt.displayAlignment = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        # 우리가 background 이미 그렸으므로 native delegate background 차단
        text_opt.state = (text_opt.state
                          & ~QStyle.StateFlag.State_Selected
                          & ~QStyle.StateFlag.State_HasFocus
                          & ~QStyle.StateFlag.State_MouseOver)
        text_opt.backgroundBrush = QBrush()
        text_opt.features = text_opt.features & ~QStyleOptionViewItem.ViewItemFeature.Alternate
        # palette text 색 우리 색으로
        text_opt.palette.setColor(QPalette.ColorRole.Text, name_color)
        text_opt.palette.setColor(QPalette.ColorRole.WindowText, name_color)
        text_opt.palette.setColor(QPalette.ColorRole.HighlightedText, name_color)

        painter.save()
        super().paint(painter, text_opt, index)
        painter.restore()

        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 30)


# 숫자 정렬 키 role — UserRole 은 파일경로 저장에 사용 중이라 별도 슬롯.
_NUM_SORT_ROLE = Qt.ItemDataRole.UserRole + 7


class _NumericSortItem(QTableWidgetItem):
    """숫자 컬럼 셀 — 표시는 포맷 문자열('44100'), 정렬은 원본 숫자.
    기본 QTableWidgetItem 은 문자열 비교라 '192000' < '44100' 이 돼
    SR/길이/크기 정렬이 뒤집혔음. 숫자 키 없는(빈/미분석) 셀은
    정렬 방향과 무관하게 항상 맨 뒤로 (내림차순은 Qt 가 오름차순을 뒤집는
    구조라, 빈 값을 방향에 따라 ±inf 로 치환해야 양방향 모두 뒤로 감)."""

    def __lt__(self, other):
        a = self.data(_NUM_SORT_ROLE)
        b = other.data(_NUM_SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if a is None and b is None:
            return super().__lt__(other)
        if a is None or b is None:
            none_val = float("inf")
            tw = self.tableWidget()
            if (tw is not None and tw.horizontalHeader().sortIndicatorOrder()
                    == Qt.SortOrder.DescendingOrder):
                none_val = float("-inf")
            a = none_val if a is None else a
            b = none_val if b is None else b
        return a < b


# Windows MAX_PATH(260) 한계 — 이 이상이면 탐색기/DAW(누엔도 등)가 경로를 못 연다.
# 앱 자체(파이썬)는 긴 경로를 열 수 있어 재생/파형은 정상인 비대칭이 생김.
_LONG_PATH_LIMIT = 256
_LONG_PATH_TMP = Path(tempfile.gettempdir()) / "SoundField_longpath"


def _draggable_path(path: str) -> str:
    """드래그용 경로 — MAX_PATH 초과 파일은 로컬 임시 복사본 경로로 대체.

    복사본 파일명 = 원본 파일명(stem) + 해시 8자 + 확장자 (충돌 방지 + 짧은 경로 보장).
    해시를 뒤(suffix)에 둬 파일명 앞부분이 원본과 같게 — 이름으로 재검색 가능.
    이미 같은 크기의 복사본이 있으면 재사용. 임시 폴더는 24h 주기 자동 정리.
    """
    try:
        if len(os.path.normpath(path)) < _LONG_PATH_LIMIT:
            return path
        src = Path(path)
        digest = hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:8]
        dst = _LONG_PATH_TMP / f"{src.stem}_{digest}{src.suffix}"
        if not (dst.exists() and dst.stat().st_size == src.stat().st_size):
            _LONG_PATH_TMP.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return str(dst)
    except OSError:
        return path


def _reveal_in_explorer(path: str):
    threading.Thread(
        target=_reveal_in_explorer_worker,
        args=(path,),
        name="explorer-reveal",
        daemon=True,
    ).start()


def _reveal_in_explorer_worker(path: str):
    """탐색기에서 해당 파일을 선택한 상태로 폴더 오픈.

    Windows: subprocess 에 **string** (list 아님) 으로 raw command line 전달.
    list 형태로 넘기면 Python list2cmdline 이 공백 포함 인자 전체를 quote 로
    감싸서 `"/select,PATH"` 가 됨 → explorer 가 /select, 옵션 인식 못해 디폴트
    (내문서) 폴더만 오픈. 표준 형태 `explorer /select,"PATH"` 는 /select, 가
    quote 밖, PATH 만 quote 안이어야 함. 이 형태는 string 으로만 만들 수 있음.
    """
    p = Path(path)
    norm = os.path.normpath(str(p))

    # MAX_PATH 초과 — explorer /select 는 파일을 못 찾아 바탕화면만 띄움.
    # 한계 안에 들어오는 가장 가까운 부모 폴더를 대신 연다.
    if len(norm) >= _LONG_PATH_LIMIT:
        parent = p.parent
        while len(str(parent)) >= _LONG_PATH_LIMIT and parent != parent.parent:
            parent = parent.parent
        try:
            os.startfile(str(parent))
        except OSError:
            pass
        return

    if os.name == 'nt':
        # 경로 내 `"` 는 explorer 가 처리 못 — 사전 escape (현실적으로 사운드
        # 파일 경로에 `"` 들어갈 일 없지만 안전책).
        safe = norm.replace('"', '')
        cmd = f'explorer /select,"{safe}"'
        try:
            subprocess.Popen(cmd)
            return
        except OSError:
            pass

    # fallback — 부모 폴더만. exists()도 NAS 히컵 가능성이 있어 UI 스레드 밖에서만 실행.
    try:
        parent = p.parent
        if parent.exists():
            os.startfile(str(parent))
    except Exception:
        pass


class ClosableHeader(QHeaderView):
    """헤더 우상단에 X 표시. 클릭 시 columnCloseRequested(logical_idx). 드래그 이동/우클릭 메뉴/정렬 모두 호환."""
    columnCloseRequested = pyqtSignal(int)

    BTN_SIZE = 12

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(True)
        self.setSectionsMovable(True)        # 드래그로 순서 변경
        self.setMouseTracking(True)
        self._hover_close_idx = -1     # 마우스가 X 위에 있는 컬럼 (빨강 강조용)
        self._hover_section_idx = -1   # 마우스가 올라간 컬럼 (X 표시 여부 — 호버 시만)
        # 닫기(X) 버튼이 그려지지 않는 컬럼 (logical index 집합).
        # ResultsTable 의 file_name(0) 처럼 숨기면 행 매핑이 끊기는 컬럼 보호용.
        self._locked_columns: set = set()
        # Qt 기본 sort indicator 끄고 paintSection 에서 직접 그림 (X 좌측에 배치)
        super().setSortIndicatorShown(False)

    def set_locked_columns(self, idxs):
        self._locked_columns = set(idxs)
        self.viewport().update()

    def setSortIndicatorShown(self, show: bool):
        # 외부(setSortingEnabled)에서 True 호출되어도 무시 — 항상 수동 그림
        super().setSortIndicatorShown(False)

    def _close_rect_in(self, rect: QRect) -> QRect:
        s = self.BTN_SIZE
        # 우측 상단 모서리 (탭 닫기 버튼과 동일 정책). 우측 여백 2px →
        # 정렬 화살표와 가로 중심이 일치하도록 맞춤.
        return QRect(rect.right() - s - 2, rect.top() + 3, s, s)

    def _section_rect(self, logical_index: int) -> QRect:
        return QRect(self.sectionViewportPosition(logical_index), 0,
                     self.sectionSize(logical_index), self.height())

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int):
        super().paintSection(painter, rect, logical_index)
        if rect.width() < 36:
            return
        cr = self._close_rect_in(rect)
        is_locked = logical_index in self._locked_columns

        # 정렬 인디케이터 — 모든 컬럼 너비에서 우측 끝 + 세로 중앙 정렬.
        # (우측 padding 영역에 위치 → 헤더 텍스트와 겹치지 않음. X 는 우상단이라 세로로 분리됨.)
        if self.sortIndicatorSection() == logical_index:
            order = self.sortIndicatorOrder()
            iw, ih = 7, 4
            cx = (cr.left() + cr.right()) / 2.0   # X 박스 가로 중심
            il = cx - iw / 2.0                    # 화살표를 X 와 동일 가로축에 정렬
            icy = rect.center().y()
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(140, 152, 168))
            if order == Qt.SortOrder.AscendingOrder:
                poly = QPolygonF([
                    QPointF(il, icy + ih / 2),
                    QPointF(il + iw, icy + ih / 2),
                    QPointF(il + iw / 2, icy - ih / 2),
                ])
            else:
                poly = QPolygonF([
                    QPointF(il, icy - ih / 2),
                    QPointF(il + iw, icy - ih / 2),
                    QPointF(il + iw / 2, icy + ih / 2),
                ])
            painter.drawPolygon(poly)
            painter.restore()

        if is_locked:
            # 닫기 버튼 자체를 그리지 않음 — 잠금 컬럼 표시
            return

        # X 는 해당 컬럼에 마우스를 올렸을 때만 표시 (탭 닫기 버튼과 동일 정책)
        if self._hover_section_idx != logical_index:
            return

        hover = (self._hover_close_idx == logical_index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(230, 90, 90) if hover else QColor(150, 155, 165), 1.6)
        painter.setPen(pen)
        pad = 3
        painter.drawLine(cr.left() + pad, cr.top() + pad,
                         cr.right() - pad, cr.bottom() - pad)
        painter.drawLine(cr.right() - pad, cr.top() + pad,
                         cr.left() + pad, cr.bottom() - pad)
        painter.restore()

    def mousePressEvent(self, e: QMouseEvent):
        idx = self.logicalIndexAt(e.pos())
        if idx >= 0 and idx not in self._locked_columns:
            cr = self._close_rect_in(self._section_rect(idx))
            if cr.contains(e.pos()):
                self.columnCloseRequested.emit(idx)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        idx = self.logicalIndexAt(e.pos())
        # 현재 올라간 컬럼 (X 표시 여부) — 잠금 컬럼은 X 없음.
        section = idx if (idx >= 0 and idx not in self._locked_columns) else -1
        # X 위 정확히 호버 (빨강 강조).
        new_close = -1
        if section >= 0:
            cr = self._close_rect_in(self._section_rect(idx))
            if cr.contains(e.pos()):
                new_close = idx
        if section != self._hover_section_idx or new_close != self._hover_close_idx:
            self._hover_section_idx = section
            self._hover_close_idx = new_close
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hover_section_idx != -1 or self._hover_close_idx != -1:
            self._hover_section_idx = -1
            self._hover_close_idx = -1
            self.viewport().update()
        super().leaveEvent(e)


class ResultsTable(QTableWidget):
    """검색 결과: 선택→재생, 헤더 우클릭→컬럼 토글, 드래그→파일 드롭, Space→play/pause.
    + 컬럼 드래그 순서 변경 / X 버튼으로 숨기기 / 워터마크 로고.
    """

    fileActivated = pyqtSignal(str)
    manualPlayRequested = pyqtSignal(str)  # 더블클릭 등 사용자 명시 재생 (auto_preview 무시)
    playToggleRequested = pyqtSignal(str)
    revealInBrowserRequested = pyqtSignal(str)
    addToBlacklistRequested = pyqtSignal(str)
    addFileToBlacklistRequested = pyqtSignal(list)  # 개별 사운드(파일) 경로 블랙리스트
    dragDropped = pyqtSignal()  # DAW 등 외부로 드롭 완료 — MainWindow 가 재생 정지 결정

    def wheelEvent(self, e):
        """Shift+휠 → 가로 스크롤 (휠 위=좌, 아래=우). 그 외엔 기본 처리."""
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            delta = e.angleDelta().y()
            if delta != 0:
                hbar = self.horizontalScrollBar()
                hbar.setValue(hbar.value() - int(delta * 50 / 120))
                e.accept()
                return
        super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.viewport().update()

    def _compute_header_min_widths(self) -> Dict[int, int]:
        """컬럼별 최소 너비 = 헤더 라벨이 완전히 보이는 폭.
        헤더 QSS(폰트 10px Bold, letter-spacing 0.12em, 좌우 padding 10px)를 반영해
        그 이하로는 못 줄이게 한다. 실제 머신 폰트로 런타임 계산."""
        f = QFont(self.horizontalHeader().font())
        f.setPixelSize(10)
        f.setBold(True)
        fm = QFontMetrics(f)
        mins: Dict[int, int] = {}
        for i, (_, label, _) in enumerate(self.COLUMNS):
            label = label or ""
            adv = fm.horizontalAdvance(label)
            ls = int(0.12 * 10 * len(label))   # letter-spacing 0.12em 근사
            mins[i] = adv + ls + 20 + 6        # +좌우 padding(20) +여유(6)
        return mins

    def _min_of(self, i: int) -> int:
        return self._col_min_w.get(i, self.MIN_STRETCH_WIDTH)

    def _on_section_resized(self, logical: int, _old: int, _new: int):
        # 셀 elide 즉시 갱신.
        self.viewport().update()
        if self._fitting:
            return  # 프로그램(_fit_columns/레이아웃 복원)의 변경은 무시.
        # 헤더 라벨이 잘릴 만큼은 못 줄임 — 최소폭으로 클램프(드래그 floor).
        mn = self._min_of(logical)
        if _new < mn:
            self._fitting = True
            try:
                self.setColumnWidth(logical, mn)
            finally:
                self._fitting = False
            _new = mn
        # 사용자가 직접 끈 텍스트 컬럼만 '선호 너비'로 기억.
        if 0 <= logical < len(self.COLUMNS) and self.COLUMNS[logical][0] in self.STRETCH_COLUMN_KEYS:
            self._user_pref_w[logical] = max(mn, int(_new))

    def _fit_columns(self):
        """Legacy hook: column widths are user-owned.

        Window resize, history-panel resize, and column visibility changes only
        change the viewport. If visible columns exceed the viewport, Qt exposes
        the horizontal scrollbar instead of rewriting column widths.
        """
        self.viewport().update()

    COLUMNS = [
        ("file_name", "파일명", 360),
        ("file_path", "PATH", 320),
        ("duration", "길이", 60),
        ("sample_rate", "SR", 90),
        ("channels", "CH", 44),
        ("bit_depth", "BIT DEPTH", 88),
        ("codec", "코덱", 70),
        ("file_size", "크기", 70),
        ("title", "제목", 150),
        ("artist", "아티스트", 120),
        ("album", "앨범", 120),
        ("genre", "장르", 100),
        ("comments", "코멘트", 200),
        ("bitrate", "비트레이트", 80),
    ]

    DEFAULT_VISIBLE = {"file_name", "duration", "sample_rate", "channels",
                       "bit_depth", "codec", "file_path"}

    # 남는 가로 폭을 흡수(축약 ... 우선 해소)할 텍스트 컬럼. 숫자/고정 폭 컬럼은 제외.
    STRETCH_COLUMN_KEYS = frozenset({"file_name", "file_path", "title",
                                     "artist", "album", "genre", "comments"})
    MIN_STRETCH_WIDTH = 60

    # 숨길 수 없는 컬럼 — file_name(0) 에 path/UserRole/play-pip 가 묶여 있어
    # 숨기면 행 매핑/재생/드래그가 모두 끊긴다. 사용자가 실수로 모두 닫는
    # 사고를 막기 위한 잠금 컬럼.
    LOCKED_COLUMN_KEYS = frozenset({"file_name"})

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._list_tooltip_pos = QPoint()
        self._list_tooltip_text = ""
        self._list_tooltip_timer = QTimer(self)
        self._list_tooltip_timer.setSingleShot(True)
        self._list_tooltip_timer.setInterval(_DENSE_LIST_TOOLTIP_DELAY_MS)
        self._list_tooltip_timer.timeout.connect(self._show_delayed_list_tooltip)
        self._drag_start_pos: QPoint = QPoint()
        # 좌클릭이 '우리 위젯 안에서' 실제로 눌렸는지 자체 추적 — 큐에 적체된
        # stale move 이벤트나 빠른 연타로 press/release 짝이 꼬인 경우, 이 플래그가
        # False 면 드래그를 아예 시작하지 않는다 (방향 3: 꼬임 방지).
        self._left_pressed = False
        # 드래그 진행 중 재진입 가드 — drag.exec() 는 블로킹이지만 내부에서 이벤트
        # 루프를 돌려 중첩 mouseMoveEvent 가 들어올 수 있음. 이 플래그로 두 번째
        # 드래그(중복 라벨)가 시작되는 걸 막는다 (방향 2: 안전장치).
        self._dragging = False
        self._rows: List[Dict] = []
        self._row_by_path: Dict[str, Dict] = {}
        self._last_emitted_path: str = ""
        self._playing_path: str = ""
        self._playing_row: int = -1
        self._sort_requested = False
        # pip 상태: "" / "loading" / "playing" / "ended"
        self._pip_path: str = ""
        self._pip_state: str = ""
        self._activate_timer = QTimer(self)
        self._activate_timer.setSingleShot(True)
        self._activate_timer.setInterval(35)
        self._activate_timer.timeout.connect(self._fire_activate)
        self._fitting = False  # _fit_columns 재진입 가드 (setColumnWidth → 시그널)
        # 사용자가 수동 조절한 텍스트 컬럼의 '선호(목표) 너비' (logical idx → px).
        # 자동채움은 이 폭을 목표로 쓰되, 공간 부족 시 비례 축소(잘림 방지).
        self._user_pref_w: Dict[int, int] = {}
        # 컬럼별 최소 너비 = 헤더 라벨이 완전히 보이는 폭. _setup 에서 런타임 계산.
        self._col_min_w: Dict[int, int] = {}
        self._setup()
        # 파일명 컬럼에 커스텀 delegate (play-pip + fmt 배지 + 텍스트)
        self._fname_delegate = FilenameCellDelegate(self)
        self.setItemDelegateForColumn(0, self._fname_delegate)

        self.cellClicked.connect(self._on_cell_clicked)
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self._config = {"double_click_to_play": False}
        # 스크롤바 위에서 휠 무시 (드래그 전용) — 하단 가로바에 커서가 걸친 채
        # 휠을 돌리면 가로 스크롤로 오동작하는 일이 잦음. 목록 영역에서는
        # 휠(세로)/쉬프트+휠(가로)이 기존대로 동작.
        self.horizontalScrollBar().installEventFilter(self)
        self.verticalScrollBar().installEventFilter(self)

    def eventFilter(self, obj, e):
        # 목록 위 휠도 Qt 가 내부적으로 스크롤바 객체로 전달해 처리하므로,
        # 커서가 '실제로 스크롤바 위'에 있을 때만 막아야 목록 휠이 살아있다.
        if (e.type() == QEvent.Type.Wheel
                and obj in (self.horizontalScrollBar(), self.verticalScrollBar())
                and obj.rect().contains(obj.mapFromGlobal(e.globalPosition().toPoint()))):
            return True
        return super().eventFilter(obj, e)

    def set_config(self, config: dict):
        self._config.update(config)
        compact = bool(self._config.get("compact_results", False))
        self._fname_delegate.set_compact(compact)
        # 행 높이 — 작게 모드는 폰트 축소에 맞춰 22px (보통은 30px)
        self.verticalHeader().setDefaultSectionSize(22 if compact else 30)
        # 일반 셀(파일명 외 컬럼) 폰트 — 작게 모드 1pt 축소
        f = QFont(self.font())
        f.setPointSize(8 if compact else 9)
        self.setFont(f)
        self.viewport().update()

    def _on_cell_clicked(self, row, col):
        if not self._config.get("double_click_to_play", False):
            path = self._path_for_row(row)
            self._last_emitted_path = path
            self.fileActivated.emit(path)

    def _on_cell_double_clicked(self, row, col):
        # 더블클릭은 사용자 명시 재생 — 모드/auto_preview 무관 항상 재생.
        path = self._path_for_row(row)
        if path:
            self._last_emitted_path = path
            self.manualPlayRequested.emit(path)

    def _path_for_row(self, row: int) -> str:
        it = self.item(row, 0)
        return it.data(Qt.ItemDataRole.UserRole) if it else ""

    def set_playing_path(self, path: str):
        """player.playingChanged 호환 — 재생 시작/종료 신호."""
        path = path or ""
        if path:
            self.set_pip(path, "playing")
        else:
            # 빈 path = 정지. 직전 pip path 를 ended 로
            if self._pip_path:
                self.set_pip(self._pip_path, "ended")

    def set_pip(self, path: str, state: str):
        """파일별 pip 상태 변경. state ∈ {"loading","playing","ended",""}"""
        path = path or ""
        state = state or ""
        if path == self._pip_path and state == self._pip_state:
            return
        self._pip_path = path
        self._pip_state = state
        self._playing_path = path if state == "playing" else ""
        new_row = -1
        if path:
            for r in range(self.rowCount()):
                it = self.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == path:
                    new_row = r
                    break
        self._playing_row = new_row
        # 박동 활성: loading / playing 만
        self._fname_delegate.set_pulsing(state in ("loading", "playing"))
        self.viewport().update()

    def _setup(self):
        self.setColumnCount(len(self.COLUMNS))
        self.setHorizontalHeaderLabels([c[1] for c in self.COLUMNS])

        header = ClosableHeader(self)
        self.setHorizontalHeader(header)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.columnCloseRequested.connect(self._on_column_close_requested)
        # file_name 컬럼은 X 버튼/닫기 클릭 차단
        self._locked_column_indices = {
            i for i, (k, _, _) in enumerate(self.COLUMNS)
            if k in self.LOCKED_COLUMN_KEYS
        }
        header.set_locked_columns(self._locked_column_indices)

        # 컬럼 최소 너비 = 헤더 라벨이 다 보이는 폭 (이하로는 못 줄임). 실제 머신 폰트로 계산.
        self._col_min_w = self._compute_header_min_widths()
        # 너무 작은 폭으로 줄이는 것 자체를 헤더 레벨에서도 1차 차단(개별 min 은 핸들러에서).
        header.setMinimumSectionSize(min(self._col_min_w.values()) if self._col_min_w else 24)

        # 헤더 라벨 elide → 헤더 자체는 보통 짧음. 핵심은 셀 텍스트.
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setWordWrap(False)

        for i, (key, _, w) in enumerate(self.COLUMNS):
            self.setColumnWidth(i, w)
            if key not in self.DEFAULT_VISIBLE:
                self.setColumnHidden(i, True)

        # 기본 표시 순서 — 길이를 맨 앞에 (길이/파일명/경로/SR/채널/비트뎁스/코덱).
        # file_name 은 logical 0 고정(delegate·UserRole·경로 매핑)이라 시각 순서만 조정.
        # 저장된 레이아웃이 있으면 apply_column_layout 의 moveSection 이 이후 덮어씀.
        dur_idx = next((i for i, (k, _, _) in enumerate(self.COLUMNS) if k == "duration"), -1)
        if dur_idx >= 0:
            header.moveSection(header.visualIndex(dur_idx), 0)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        self.setShowGrid(False)
        self.setMouseTracking(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBar(RoundedScrollBar(Qt.Orientation.Horizontal, self))
        self.setVerticalScrollBar(RoundedScrollBar(Qt.Orientation.Vertical, self))
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSortingEnabled(True)

        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        # 컬럼 너비 변경시 셀 elide 갱신 + 사용자 수동 조절 컬럼 기억
        header.sectionResized.connect(self._on_section_resized)
        header.sectionClicked.connect(self._on_header_section_clicked)
        self.itemSelectionChanged.connect(self._activate_timer.start)

        # 행 우클릭 메뉴
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_row_menu)

    # ─────────── 행 컨텍스트 메뉴 ───────────
    def _on_header_section_clicked(self, logical_idx: int):
        if logical_idx < 0 or logical_idx >= len(self.COLUMNS):
            return
        self._sort_requested = True
        if not self.isSortingEnabled():
            self.setSortingEnabled(True)
            self.sortItems(logical_idx, self.horizontalHeader().sortIndicatorOrder())

    def _show_row_menu(self, pos: QPoint):
        # 우클릭은 선택을 바꾸지 않으므로(mousePressEvent), 우클릭한 행을 대상으로.
        # 우클릭 행이 현재 선택에 들어있으면 선택 기준(첫 항목), 아니면 우클릭 행.
        idx = self.indexAt(pos)
        clicked = self._path_for_row(idx.row()) if idx.isValid() else ""
        sel = self.selected_paths()
        if clicked and clicked not in sel:
            path = clicked
        elif sel:
            path = sel[0]
        elif clicked:
            path = clicked
        else:
            return
        name = os.path.basename(path)
        
        menu = QMenu(self)
        
        act_reveal_lib = QAction("이 라이브러리 파일 브라우저에서 보기", self)
        act_reveal_lib.triggered.connect(lambda: self.revealInBrowserRequested.emit(path))
        menu.addAction(act_reveal_lib)
        
        act_reveal = QAction("탐색기에서 보기", self)
        act_reveal.triggered.connect(lambda: _reveal_in_explorer(path))
        menu.addAction(act_reveal)
        
        menu.addSeparator()
        
        act_copy_name = QAction("이 파일 이름 복사", self)
        act_copy_name.triggered.connect(lambda: QApplication.clipboard().setText(name))
        menu.addAction(act_copy_name)
        
        act_copy = QAction("경로 복사", self)
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(path))
        menu.addAction(act_copy)
        
        menu.addSeparator()

        # 개별 사운드(파일) 블랙리스트 — 다중 선택 시 선택분 전체.
        sel_files = sel if (len(sel) > 1 and clicked in sel) else [path]
        act_bl_file = QAction(
            "이 사운드 블랙리스트에 추가" if len(sel_files) == 1
            else f"선택한 사운드 {len(sel_files)}개 블랙리스트에 추가", self)
        act_bl_file.triggered.connect(
            lambda: self.addFileToBlacklistRequested.emit(sel_files))
        menu.addAction(act_bl_file)

        act_blacklist = QAction("이 라이브러리 블랙리스트에 추가", self)
        act_blacklist.triggered.connect(lambda: self.addToBlacklistRequested.emit(path))
        menu.addAction(act_blacklist)

        menu.exec(self.viewport().mapToGlobal(pos))

    # ─────────── 컬럼 X 버튼 ───────────
    def _on_column_close_requested(self, logical_idx: int):
        # 잠금 컬럼(file_name)은 숨길 수 없음 — 행 매핑/재생이 깨짐
        if logical_idx in self._locked_column_indices:
            return
        # 최소 1개 컬럼은 남김
        visible = [i for i in range(self.columnCount()) if not self.isColumnHidden(i)]
        if len(visible) <= 1:
            return
        self.setColumnHidden(logical_idx, True)
        self._fit_columns()

    # ─────────── 컬럼 컨텍스트 메뉴 ───────────
    def _show_header_menu(self, pos: QPoint):
        menu = QMenu(self)
        for i, (_, label, _) in enumerate(self.COLUMNS):
            act = QAction(label, self)
            act.setCheckable(True)
            act.setChecked(not self.isColumnHidden(i))
            if i in self._locked_column_indices:
                # 잠금 컬럼은 비활성화 (체크된 상태로 표시만)
                act.setEnabled(False)
            else:
                act.triggered.connect(lambda checked, idx=i: self._toggle_column(idx, checked))
            menu.addAction(act)
        menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, idx: int, visible: bool):
        # 잠금 컬럼은 숨기기 차단 (보이기 요청은 그대로 허용)
        if not visible and idx in self._locked_column_indices:
            return
        self.setColumnHidden(idx, not visible)
        if visible and self._rows and self.item(0, idx) is None:
            self._fill_column(idx)
        self._fit_columns()

    def _fill_column(self, c: int):
        key = self.COLUMNS[c][0]
        self.setUpdatesEnabled(False)
        try:
            for r, row in enumerate(self._rows):
                full_path = row.get("file_path", "") or ""
                self.setItem(r, c, self._item_for_value(row, key, full_path))
        finally:
            self.setUpdatesEnabled(True)

    # 숫자 정렬 대상 컬럼 — 문자열 비교('192000'<'44100')로 SR 정렬이 뒤집히던 버그의
    # 근본 원인. 표시는 포맷 텍스트, 정렬은 _NumericSortItem 의 숫자 키.
    _NUMERIC_KEYS = frozenset({"duration", "file_size", "sample_rate",
                               "channels", "bit_depth", "bitrate"})

    def _item_for_value(self, row: Dict, key: str, full_path: str) -> QTableWidgetItem:
        val = row.get(key, "")
        if key == "duration" and val:
            text = f"{float(val):.2f}s"
        elif key == "file_size" and val:
            text = self._fmt_size(int(val))
        elif val is None:
            text = ""
        else:
            text = str(val)
        if key in self._NUMERIC_KEYS:
            item = _NumericSortItem(text)
            try:
                if val is not None and val != "":
                    item.setData(_NUM_SORT_ROLE, float(val))
            except (TypeError, ValueError):
                pass  # 숫자 아님 — 빈 키로 두면 정렬 시 뒤로 감
        else:
            item = QTableWidgetItem(text)
        # 파일명/경로/코멘트만 좌측 정렬, 나머지(숫자/메타)는 중앙 정렬.
        if key in ("file_name", "file_path", "comments"):
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if key == "file_name":
            item.setData(Qt.ItemDataRole.UserRole, full_path)
            item.setData(Qt.ItemDataRole.ToolTipRole, full_path)
        return item

    # ─────────── 컬럼 레이아웃 저장/복원 ───────────
    def _visible_columns(self):
        return [
            (i, self.COLUMNS[i][0])
            for i in range(len(self.COLUMNS))
            if not self.isColumnHidden(i)
        ]

    def _update_playing_row(self):
        if not self._playing_path:
            return
        new_row = -1
        for r in range(self.rowCount()):
            it = self.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == self._playing_path:
                new_row = r
                break
        self._playing_row = new_row
        self._fname_delegate.set_pulsing(new_row >= 0)

    def begin_results_stream(self):
        self._rows = []
        self._row_by_path = {}
        self._last_emitted_path = ""
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        prev_block = self.blockSignals(True)
        try:
            self.clearSelection()
            self.setRowCount(0)
        finally:
            self.blockSignals(prev_block)
            self.setUpdatesEnabled(True)

    def append_results(self, rows: List[Dict]):
        if not rows:
            return
        t0 = time.perf_counter()
        start = len(self._rows)
        self._rows.extend(rows)
        for row in rows:
            path = row.get("file_path")
            if path:
                self._row_by_path[path] = row
        visible_cols = self._visible_columns()
        self.setUpdatesEnabled(False)
        prev_block = self.blockSignals(True)
        try:
            self.setRowCount(start + len(rows))
            for offset, row in enumerate(rows):
                r = start + offset
                full_path = row.get("file_path", "") or ""
                for c, key in visible_cols:
                    self.setItem(r, c, self._item_for_value(row, key, full_path))
        finally:
            self.blockSignals(prev_block)
            self.setUpdatesEnabled(True)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[perf-results] append_results rows=%d total=%d cols=%d elapsed=%dms",
                    len(rows), len(self._rows), len(visible_cols), elapsed_ms)

    def finish_results_stream(self):
        if self._sort_requested:
            self.setSortingEnabled(True)
        self._update_playing_row()
        self.viewport().update()

    def column_layout(self) -> List[Dict]:
        h = self.horizontalHeader()
        n = self.columnCount()
        items = []
        for visual in range(n):
            logical = h.logicalIndex(visual)
            if logical < 0 or logical >= len(self.COLUMNS):
                continue
            items.append({
                "key": self.COLUMNS[logical][0],
                "visible": not self.isColumnHidden(logical),
                "width": self.columnWidth(logical),
                # 수동 조절한 텍스트 컬럼의 선호 너비 — 재시작 후에도 목표로 복원.
                "user_pref_w": self._user_pref_w.get(logical),
            })
        return items

    def apply_column_layout(self, items: List[Dict]):
        if not items:
            return
        h = self.horizontalHeader()
        key_to_logical = {c[0]: i for i, c in enumerate(self.COLUMNS)}
        # 복원 중 setColumnWidth 가 sectionResized 를 발생시켜도 user_pref_w 로 오인하지
        # 않게 가드. 선호 너비는 저장값으로 직접 복원.
        self._fitting = True
        try:
            for item in items:
                idx = key_to_logical.get(item.get("key"))
                if idx is None:
                    continue
                # 잠금 컬럼은 저장 상태와 무관하게 항상 visible (이전 버전에서 숨김으로
                # 저장돼 결과가 안 보이는 사고 복구)
                visible = True if idx in self._locked_column_indices else item.get("visible", True)
                self.setColumnHidden(idx, not visible)
                w = item.get("width")
                if w:
                    self.setColumnWidth(idx, int(w))
                upw = item.get("user_pref_w")
                if upw:
                    self._user_pref_w[idx] = int(upw)
        finally:
            self._fitting = False
        target_visual = 0
        for item in items:
            idx = key_to_logical.get(item.get("key"))
            if idx is None:
                continue
            current_visual = h.visualIndex(idx)
            if current_visual != target_visual:
                h.moveSection(current_visual, target_visual)
            target_visual += 1
        self._fit_columns()

    def visible_column_keys(self) -> List[str]:
        return [k for i, (k, _, _) in enumerate(self.COLUMNS) if not self.isColumnHidden(i)]

    def set_visible_column_keys(self, keys: List[str]):
        """legacy: visibility 만 적용 (순서/너비는 기본값)."""
        if not keys:
            return
        # 잠금 컬럼은 키 누락 시에도 무조건 포함
        keyset = set(keys) | set(self.LOCKED_COLUMN_KEYS)
        for i, (k, _, _) in enumerate(self.COLUMNS):
            self.setColumnHidden(i, k not in keyset)

    # ─────────── 선택/재생 ───────────
    def _fire_activate(self):
        paths = self.selected_paths()
        if not paths:
            return
        if paths[0] == self._last_emitted_path:
            return
        self._last_emitted_path = paths[0]
        self.fileActivated.emit(paths[0])

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            paths = self.selected_paths()
            if paths:
                self.playToggleRequested.emit(paths[0])
                e.accept()
                return
        super().keyPressEvent(e)

    def keyboardSearch(self, search: str):
        # 결과표 타입어헤드 비활성 — 글자 키(w, t …)를 누르면 해당 글자로 시작하는
        # 행으로 점프하며 선택이 바뀌어 사운드가 멋대로 재생되던 동작 차단.
        # 검색은 상단 검색창에서만 한다.
        return

    def set_results(self, rows: List[Dict]):
        t0 = time.perf_counter()
        self._rows = rows
        self._row_by_path = {
            r.get("file_path"): r for r in rows if r.get("file_path")
        }
        self._last_emitted_path = ""
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        prev_block = self.blockSignals(True)
        try:
            self.clearSelection()
            self.setRowCount(len(rows))
            visible_cols = [
                (i, self.COLUMNS[i][0])
                for i in range(len(self.COLUMNS))
                if not self.isColumnHidden(i)
            ]
            for r, row in enumerate(rows):
                full_path = row.get("file_path", "") or ""
                for c, key in visible_cols:
                    self.setItem(r, c, self._item_for_value(row, key, full_path))
        finally:
            self.blockSignals(prev_block)
            self.setUpdatesEnabled(True)
            if self._sort_requested:
                self.setSortingEnabled(True)
        # playing path 가 있으면 새 결과에서 다시 매핑
        if self._playing_path:
            new_row = -1
            for r in range(self.rowCount()):
                it = self.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == self._playing_path:
                    new_row = r
                    break
            self._playing_row = new_row
            self._fname_delegate.set_pulsing(new_row >= 0)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("[perf-results] set_results rows=%d cols=%d elapsed=%dms",
                    len(rows), len(visible_cols), elapsed_ms)

    @staticmethod
    def _fmt_size(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024:
                return f"{b:.0f}{unit}" if unit == "B" else f"{b:.1f}{unit}"
            b /= 1024
        return f"{b:.1f}TB"

    def get_meta_for_path(self, path: str) -> Optional[Dict]:
        """플레이어 메타 라벨용 — 채널/SR/Bit dict 반환."""
        if not path:
            return None
        return self._row_by_path.get(path)

    def selected_paths(self) -> List[str]:
        paths = []
        seen = set()
        for item in self.selectedItems():
            row = item.row()
            first = self.item(row, 0)
            if first:
                p = first.data(Qt.ItemDataRole.UserRole)
                if p and p not in seen:
                    seen.add(p)
                    paths.append(p)
        return paths

    def viewportEvent(self, e):
        if e.type() == QEvent.Type.ToolTip:
            e.accept()
            return True
        if e.type() in (QEvent.Type.Leave, QEvent.Type.Wheel):
            self._hide_delayed_list_tooltip()
        return super().viewportEvent(e)

    def _tooltip_text_at(self, pos: QPoint) -> str:
        item = self.itemAt(pos)
        if item is None:
            return ""
        value = item.data(Qt.ItemDataRole.ToolTipRole)
        return str(value or item.toolTip() or "")

    def _schedule_delayed_list_tooltip(self, pos: QPoint):
        text = self._tooltip_text_at(pos)
        if not text:
            self._hide_delayed_list_tooltip()
            return
        if text == self._list_tooltip_text:
            self._list_tooltip_pos = QPoint(pos)
            return
        self._list_tooltip_text = text
        self._list_tooltip_pos = QPoint(pos)
        QToolTip.hideText()
        self._list_tooltip_timer.start()

    def _show_delayed_list_tooltip(self):
        text = self._tooltip_text_at(self._list_tooltip_pos)
        if not text or text != self._list_tooltip_text:
            return
        global_pos = self.viewport().mapToGlobal(self._list_tooltip_pos + QPoint(16, 18))
        QToolTip.showText(global_pos, text, self.viewport())

    def _hide_delayed_list_tooltip(self):
        if self._list_tooltip_timer.isActive():
            self._list_tooltip_timer.stop()
        self._list_tooltip_text = ""
        QToolTip.hideText()

    # ─────────── 드래그 ───────────
    def mousePressEvent(self, e: QMouseEvent):
        # 우클릭은 선택/포커스/재생을 바꾸지 않고 컨텍스트 메뉴만 띄움.
        # (기존 선택·재생 항목 보존 — 우클릭만으로 다른 사운드가 재생/선택되지 않게.)
        if e.button() == Qt.MouseButton.RightButton:
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = e.pos()
            self._left_pressed = True
            logger.info("[drag] PRESS pos=(%d,%d) dragging=%s",
                        e.pos().x(), e.pos().y(), self._dragging)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        # 좌클릭을 떼는 즉시 자체 누름 플래그 해제 — 이후 도착하는 stale move 가
        # 드래그를 시작하지 못하게 한다.
        if e.button() == Qt.MouseButton.LeftButton:
            self._left_pressed = False
            logger.info("[drag] RELEASE dragging=%s", self._dragging)
        super().mouseReleaseEvent(e)

    def _within_press_row(self, pos: QPoint) -> bool:
        """현재 커서가 '누르기 시작한 행'의 세로 범위 안인지 (정책: 행을 벗어나야 드래그).

        좌표는 모두 viewport 기준 — indexAt/visualRect 로 눌린 셀의 실제 화면 사각형을
        얻어(스크롤·헤더 자동 보정) 그 행의 세로 띠 + 표 가로폭 안인지 본다. 행 밖으로
        나감 = 다른 행으로 넘어가거나(세로) 표 밖으로 커서가 나감(가로, 버튼 그랩 중엔
        음수/초과 좌표가 옴). 미세 jitter(<10px)는 무조건 행 안으로 간주.
        """
        if (pos - self._drag_start_pos).manhattanLength() < 10:
            return True
        idx = self.indexAt(self._drag_start_pos)
        if not idx.isValid():
            return False   # 빈 영역에서 시작 — 어차피 선택 없어 드래그 무산
        r = self.visualRect(idx)
        row_rect = QRect(0, r.top(), self.viewport().width(), r.height())
        return row_rect.contains(pos)

    def mouseMoveEvent(self, e: QMouseEvent):
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            self._schedule_delayed_list_tooltip(e.pos())
            super().mouseMoveEvent(e)
            return
        # ⚠️ 좌버튼 눌린 채 이동은 base class 로 절대 전달하지 않는다 —
        # QAbstractItemView 내장 드래그('행 이미지' pixmap + 비-파일 mime)와 러버밴드
        # 선택을 원천 차단. 드래그는 오직 아래 우리 커스텀 경로로만 시작한다.
        self._hide_delayed_list_tooltip()
        # 정책: 시작한 행을 벗어나야 드래그. 행 안 이동(클릭/오디션)은 무시 → 드래그 안 함.
        if self._within_press_row(e.pos()):
            return
        # 행을 벗어남 = 드래그 의도. 단, 이미 진행 중이거나 stale move(버튼 실제로 뗌)면
        # 시작하지 않는다 (실시간 OS 버튼 상태로 확인).
        if self._dragging or not self._left_pressed or not _left_button_down_now():
            return
        paths = self.selected_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(_draggable_path(str(Path(p))))
                      for p in paths if p])
        drag = QDrag(self)
        drag.setMimeData(mime)
        # 시각 효과 ─ drop 동작에 절대 영향 X, 단순 표시만:
        # (1) IgnoreAction 커서 = 투명 pixmap → OS default "drop OK" 커서로 fallback
        #     → 툴 내부 통과 시 금지 표시 X
        # (2) drag pixmap = 파일명 라벨 → 마우스에 라벨 따라옴
        try:
            empty = QPixmap(1, 1)
            empty.fill(Qt.GlobalColor.transparent)
            drag.setDragCursor(empty, Qt.DropAction.IgnoreAction)
        except Exception:
            pass
        try:
            label_pm = self._make_drag_label(paths)
            if label_pm is not None:
                drag.setPixmap(label_pm)
                drag.setHotSpot(QPoint(12, label_pm.height() // 2))
        except Exception:
            pass
        # exec 직전 마지막 실시간 버튼 확인 — 위 pixmap/mime 준비 사이에 손을 뗐다면
        # 여기서 버튼이 올라가 있어 차단(준비 구간 레이스 제거). 실시간 OS 상태 사용.
        if not _left_button_down_now():
            return
        # drag.exec() 는 drop 완료/취소까지 블로킹. drop 됐을 때만 정지 시그널.
        self._dragging = True
        logger.info("[drag] EXEC enter e.buttons=%d qapp=%d async=%s n=%d",
                    int(e.buttons().value),
                    int(QApplication.mouseButtons().value),
                    _left_button_down_now(), len(paths))
        _t0 = time.monotonic()
        try:
            result = drag.exec(Qt.DropAction.CopyAction)
            logger.info("[drag] EXEC return result=%s dt=%dms async_now=%s",
                        result, int((time.monotonic() - _t0) * 1000),
                        _left_button_down_now())
            if result != Qt.DropAction.IgnoreAction:
                self.dragDropped.emit()
        finally:
            # 방향 2 ─ 안전장치: 드래그가 어떻게 끝나든 누름/시작점 상태를 확실히
            # 초기화해 stale move 가 곧바로 재시작하지 못하게 한다.
            self._dragging = False
            self._left_pressed = False
            self._drag_start_pos = QPoint()

    def leaveEvent(self, e):
        self._hide_delayed_list_tooltip()
        super().leaveEvent(e)

    def _make_drag_label(self, paths: list) -> "QPixmap":
        """드래그 중 마우스 옆에 보이는 파일명 라벨 pixmap."""
        if not paths:
            return None
        name = paths[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        text = name if len(paths) == 1 else f"{name}  +{len(paths)-1}"
        fm = QFontMetrics(self.font())
        w = min(fm.horizontalAdvance(text) + 24, 480)
        h = fm.height() + 12
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(QColor(0, 0, 0, 200))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, float(w), float(h)), 4, 4)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(12, 0, float(w - 16), float(h)),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        p.end()
        return pm
