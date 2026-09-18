"""FlowLayout 기반 다중 행 탭바.

QTabBar 는 단일 행 + 좌우 스크롤 한계 — 탭 많아지면 사용성 떨어짐.
이 위젯은:
- FlowLayout 으로 자동 줄바꿈 (최대 3줄)
- 각 탭은 _TabButton (toggle + drop + 우클릭)
- 탭 최대 너비 = '★ 즐겨찾기' 폰트 너비. 초과 시 elide + tooltip 풀네임
- API 는 _TabBar(QTabBar) 와 호환 (addTab/setTabText/removeTab/currentIndex/setCurrentIndex/tabAt/count + 동일 시그널)
"""
import json
from typing import List, Optional

from PyQt6.QtCore import (
    Qt, pyqtSignal, QPoint, QRect, QSize, QPointF, QMargins,
)
from PyQt6.QtGui import QFontMetrics, QMouseEvent, QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QWidget, QLayout, QSizePolicy, QStyle, QToolButton, QLayoutItem,
)

from app.ui.folder_tree import MIME_PATHS
from app.ui.theme import COLORS

_CLOSE_SZ = 14       # 호버 시 우상단 X 버튼 한 변(px)
_CLOSE_MARGIN = 3    # 탭 우상단 모서리에서의 여백(px)


class FlowLayout(QLayout):
    """Qt 표준 FlowLayout 의 PyQt6 변형 — 자동 줄바꿈 + 최대 줄 수 cap.

    max_lines > 0 이면 그 줄 수만큼만 배치, 이후 항목은 보이지 않음 (clip).
    sizeHint 가 max_lines 기준으로 계산되어 부모 레이아웃이 cap 된 높이만 할당.
    """
    def __init__(self, parent=None, h_spacing: int = 4, v_spacing: int = 4,
                 max_lines: int = 3):
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self._max_lines = max_lines
        # 첫 줄만 우측을 이만큼 비워둔다 — 우상단에 겹쳐 놓인 코너 버튼(+/🔍) 자리.
        # 둘째 줄부터는 전체 폭 사용 → 버튼 아래 공간을 탭이 채운다.
        self._first_line_inset = 0
        self.setContentsMargins(0, 0, 0, 0)

    def set_first_line_inset(self, px: int):
        px = max(0, int(px))
        if px != self._first_line_inset:
            self._first_line_inset = px
            self.invalidate()

    def addItem(self, item: QLayoutItem):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, idx: int):
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def takeAt(self, idx: int):
        return self._items.pop(idx) if 0 <= idx < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _right_limit(self, rect: QRect, line_index: int) -> int:
        m = self.contentsMargins()
        rl = rect.right() - m.right()
        if line_index == 0:
            rl -= self._first_line_inset   # 첫 줄만 코너 버튼 자리 확보
        return rl

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        right_limit = self._right_limit(rect, 0)
        line_h = 0
        line_index = 0  # 0-based
        for it in self._items:
            w = it.widget()
            sh = it.sizeHint()
            next_x = x + sh.width()
            if next_x > right_limit and line_h > 0:
                # 줄바꿈
                line_index += 1
                if self._max_lines > 0 and line_index >= self._max_lines:
                    # cap — 나머지 항목 0 크기로 (보이지 않게).
                    if not test_only and w is not None:
                        w.setGeometry(QRect(0, 0, 0, 0))
                    continue
                x = rect.x() + m.left()
                y = y + line_h + self._v
                right_limit = self._right_limit(rect, line_index)
                next_x = x + sh.width()
                line_h = 0
            if self._max_lines > 0 and line_index >= self._max_lines:
                if not test_only and w is not None:
                    w.setGeometry(QRect(0, 0, 0, 0))
                continue
            if not test_only:
                it.setGeometry(QRect(x, y, sh.width(), sh.height()))
            x = next_x + self._h
            line_h = max(line_h, sh.height())
        return (y + line_h + m.bottom()) - rect.y()


class _TabButton(QToolButton):
    rightClicked = pyqtSignal(QPoint)
    pathsDropped = pyqtSignal(list)
    closeRequested = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("flowTabButton")
        self.setCheckable(True)
        self.setAutoRaise(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)  # 버튼 안 들어왔을 때도 X 위 호버 감지
        self._full_text: str = ""
        self._max_w: int = 9999
        self._closable: bool = False   # 사용자 탭만 True — 호버 시 우상단 X 표시
        self._hover: bool = False
        self._over_close: bool = False
        self.set_full_text(text)

    def set_closable(self, closable: bool):
        self._closable = bool(closable)
        if not self._closable:
            self._hover = self._over_close = False
        self.update()

    def _close_rect(self) -> QRect:
        return QRect(self.width() - _CLOSE_SZ - _CLOSE_MARGIN, _CLOSE_MARGIN,
                     _CLOSE_SZ, _CLOSE_SZ)

    def enterEvent(self, e):
        if self._closable and not self._hover:
            self._hover = True
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._hover or self._over_close:
            self._hover = self._over_close = False
            self.update()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._closable:
            over = self._close_rect().contains(e.pos())
            if over != self._over_close:
                self._over_close = over
                self.update()
        super().mouseMoveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not (self._closable and self._hover):
            return
        r = self._close_rect()
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            if self._over_close:
                p.setBrush(QColor(COLORS.get("danger", "#c44848")))
                line_col = QColor("#ffffff")
            else:
                bg = QColor(COLORS.get("text_secondary", "#888888"))
                bg.setAlpha(70)
                p.setBrush(bg)
                line_col = QColor(COLORS.get("text", "#cccccc"))
            p.drawEllipse(r)
            pen = QPen(line_col, 1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            pad = 4
            p.drawLine(r.left() + pad, r.top() + pad, r.right() - pad, r.bottom() - pad)
            p.drawLine(r.right() - pad, r.top() + pad, r.left() + pad, r.bottom() - pad)

    def set_max_width_px(self, w: int):
        self._max_w = max(40, int(w))
        self.setMaximumWidth(self._max_w)
        self._apply_elide()

    def set_full_text(self, text: str):
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def _apply_elide(self):
        fm = QFontMetrics(self.font())
        avail = max(20, self._max_w - 20)
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, avail)
        super().setText(elided)
        # 텍스트 길이가 짧으면 sizeHint 가 줄어듦 — 자동 fit.

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit(e.globalPosition().toPoint())
            return
        if (e.button() == Qt.MouseButton.LeftButton and self._closable
                and self._close_rect().contains(e.pos())):
            self.closeRequested.emit()
            e.accept()
            return
        super().mousePressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_PATHS):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_PATHS):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(MIME_PATHS):
            e.ignore(); return
        try:
            paths = json.loads(bytes(e.mimeData().data(MIME_PATHS)).decode("utf-8"))
        except Exception:
            e.ignore(); return
        if isinstance(paths, list) and paths:
            self.pathsDropped.emit([str(p) for p in paths])
        e.acceptProposedAction()


class FlowTabBar(QWidget):
    """QTabBar 호환 API + 다중 행."""
    currentChanged = pyqtSignal(int)
    pathsDropped = pyqtSignal(int, list)
    contextRequested = pyqtSignal(int, QPoint)
    closeRequested = pyqtSignal(int)

    def __init__(self, parent=None, max_lines: int = 3,
                 max_width_sample: str = "★ 즐겨찾기"):
        super().__init__(parent)
        self.setObjectName("flowTabBar")
        self._layout = FlowLayout(self, h_spacing=4, v_spacing=4, max_lines=max_lines)
        self.setLayout(self._layout)
        self._buttons: List[_TabButton] = []
        self._current: int = -1
        self._max_width_sample = max_width_sample
        self._max_btn_w = self._calc_max_width()
        # 우상단에 겹쳐 그릴 코너 위젯(+/🔍 버튼 묶음). 레이아웃 아이템이 아니라
        # 수동 배치 자식 — 첫 줄 우측만 비우고(set_first_line_inset) 아래 줄은 전체 폭.
        self._corner: Optional[QWidget] = None

    def set_corner_widget(self, widget: QWidget):
        """탭바 우상단에 고정 배치할 위젯(버튼 묶음). 폭만큼 첫 줄 우측을 비운다."""
        self._corner = widget
        widget.setParent(self)
        widget.show()
        widget.raise_()
        self._reposition_corner()

    def _reposition_corner(self):
        if self._corner is None:
            return
        hint = self._corner.sizeHint()
        cw, chh = hint.width(), hint.height()
        self._corner.setGeometry(max(0, self.width() - cw), 0, cw, chh)
        self._corner.raise_()
        # 첫 줄이 버튼과 겹치지 않도록 폭 + 간격만큼 확보.
        self._layout.set_first_line_inset(cw + self._layout._h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reposition_corner()

    def _calc_max_width(self) -> int:
        # QSS padding(6px 12px) + border(1px×2) + 안전 마진 — '★ 즐겨찾기' 가
        # padding 내에서 잘리지 않을 정도. 너무 빡빡하면 한 글자 더 추가될 때
        # 바로 elide → 불편. +44 로 여유.
        fm = QFontMetrics(self.font())
        return fm.horizontalAdvance(self._max_width_sample) + 44

    # ───── QTabBar 호환 API ─────
    def addTab(self, text: str) -> int:
        btn = _TabButton(text, self)
        btn.set_max_width_px(self._max_btn_w)
        idx = len(self._buttons)
        btn.clicked.connect(lambda _checked=False, i=idx: self._on_button_clicked(i))
        btn.rightClicked.connect(lambda pos, i=idx: self.contextRequested.emit(i, pos))
        btn.pathsDropped.connect(lambda paths, i=idx: self.pathsDropped.emit(i, paths))
        btn.closeRequested.connect(lambda i=idx: self.closeRequested.emit(i))
        self._buttons.append(btn)
        self._layout.addWidget(btn)
        if self._current < 0:
            self.setCurrentIndex(idx)
        return idx

    def setTabText(self, idx: int, text: str):
        if 0 <= idx < len(self._buttons):
            self._buttons[idx].set_full_text(text)

    def tabText(self, idx: int) -> str:
        if 0 <= idx < len(self._buttons):
            return self._buttons[idx].full_text()
        return ""

    def setTabClosable(self, idx: int, closable: bool):
        """해당 탭에 호버 시 우상단 X(닫기) 버튼 표시 여부. 사용자 탭만 True."""
        if 0 <= idx < len(self._buttons):
            self._buttons[idx].set_closable(closable)

    def removeTab(self, idx: int):
        if not (0 <= idx < len(self._buttons)):
            return
        btn = self._buttons.pop(idx)
        self._layout.removeWidget(btn)
        btn.deleteLater()
        # index 재계산 — 모든 버튼의 connect lambda 의 i 가 갱신되어야 함.
        self._rewire_all()
        if self._current == idx:
            new = min(idx, len(self._buttons) - 1)
            self._current = -1
            if new >= 0:
                self.setCurrentIndex(new)
            else:
                self.currentChanged.emit(-1)
        elif self._current > idx:
            self._current -= 1

    def _rewire_all(self):
        for i, btn in enumerate(self._buttons):
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass
            try:
                btn.rightClicked.disconnect()
            except TypeError:
                pass
            try:
                btn.pathsDropped.disconnect()
            except TypeError:
                pass
            try:
                btn.closeRequested.disconnect()
            except TypeError:
                pass
            btn.clicked.connect(lambda _checked=False, idx=i: self._on_button_clicked(idx))
            btn.rightClicked.connect(lambda pos, idx=i: self.contextRequested.emit(idx, pos))
            btn.pathsDropped.connect(lambda paths, idx=i: self.pathsDropped.emit(idx, paths))
            btn.closeRequested.connect(lambda idx=i: self.closeRequested.emit(idx))

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, idx: int):
        if not (0 <= idx < len(self._buttons)) or idx == self._current:
            if idx == self._current and 0 <= idx < len(self._buttons):
                self._buttons[idx].setChecked(True)
            return
        for i, b in enumerate(self._buttons):
            b.setChecked(i == idx)
        self._current = idx
        self.currentChanged.emit(idx)

    def tabAt(self, pos: QPoint) -> int:
        for i, b in enumerate(self._buttons):
            if b.geometry().contains(pos):
                return i
        return -1

    def count(self) -> int:
        return len(self._buttons)

    def _on_button_clicked(self, idx: int):
        if idx == self._current:
            self._buttons[idx].setChecked(True)  # 같은 탭 재클릭 — 토글 off 방지.
            return
        self.setCurrentIndex(idx)

    def heightForWidth(self, w: int) -> int:
        return self._layout.heightForWidth(w)

    def hasHeightForWidth(self) -> bool:
        return True

    def sizeHint(self) -> QSize:
        return self._layout.sizeHint()
