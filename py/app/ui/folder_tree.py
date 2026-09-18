import html
import json
import os
import re
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    Qt, pyqtSignal, QPoint, QPointF, QRect, QRectF, QLineF, QEvent, QTimer, QMimeData,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QAction, QPainter, QPen, QPolygonF, QPalette, QDrag,
    QTextDocument, QAbstractTextDocumentLayout,
)
from PyQt6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QMenu, QHeaderView, QInputDialog, QStyle, QWidget,
    QAbstractItemView, QApplication, QStyledItemDelegate, QStyleOptionViewItem,
    QToolTip, QLabel, QWidgetAction,
)

from app.ui.rounded_scrollbar import RoundedScrollBar

# 다른 탭으로 라이브러리/폴더 드래그 시 mimeData 키.
MIME_PATHS = "application/x-soundfield-paths"

# 탭 컨텍스트 — 컨텍스트 메뉴 분기에 사용. LibraryTabs.TAB_* 와 문자열 일치.
TAB_TYPE_ALL = "all"
TAB_TYPE_FAVORITES = "favorites"
TAB_TYPE_USER = "user"

# categoryCount 라벨과 동일한 blue 액센트 (theme.COLORS["accent"]).
_COUNT_COLOR = "#4E90E8"
# 미완료 항목 표시용 주황 (warning tone)
_INCOMPLETE_COLOR = "#ffa64d"
# 펼침/접힘 화살표 색 — 기본은 대비 위해 약간 어둡게, 호버 시 밝게.
_ARROW_COLOR = "#727d8e"
_ARROW_COLOR_HOVER = "#d6deea"
# 라이브러리 순차 검색 — 매칭된 검색어 부분 강조색(채도 낮춘 빨강).
_SEARCH_HL_RED = "#cf7676"
_DENSE_LIST_TOOLTIP_DELAY_MS = 650


def _token_prefix_ranges(text: str, query: str) -> List[tuple]:
    """검색어가 '단어(토큰) 시작에서 접두로' 들어맞는 (시작, 길이) 구간들.
    토큰 = 구분자(_ 공백 - . 등)로 쪼갠 단위. 단어 중간 매칭은 제외("ui"가
    eq[ui]pment 에 안 걸림). 접두라 부족분은 자동 포함("equip"→equipment,
    "click"→clicked). 라이브러리 검색 매칭 규칙과 동일."""
    query_tokens = [t for t in re.split(r"[\W_]+", (query or "").strip().lower()) if t]
    if not query_tokens:
        return []
    low = text.lower()
    n = len(low)
    out: List[tuple] = []
    i = 0
    token_idx = 0
    while i < n:
        is_start = i == 0 or re.match(r"[\W_]", low[i - 1]) is not None
        if is_start and low.startswith(query_tokens[token_idx], i):
            length = len(query_tokens[token_idx])
            out.append((i, length))
            i += length
            token_idx += 1
            if token_idx >= len(query_tokens):
                break
        else:
            i += 1
    return out


def _highlight_html_red(text: str, ranges: List[tuple]) -> str:
    parts: List[str] = []
    pos = 0
    for s, length in ranges:
        parts.append(html.escape(text[pos:s]))
        parts.append(
            f'<span style="color:{_SEARCH_HL_RED};font-weight:bold">'
            f"{html.escape(text[s:s + length])}</span>"
        )
        pos = s + length
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


class _FolderHighlightDelegate(QStyledItemDelegate):
    """순차 검색 이동 시, 현재 매칭 폴더의 검색어 부분만 빨강으로 그린다(선택 아님).
    강조 대상이 아닌 행은 기본 렌더 그대로 — 시각 변화 0."""

    def __init__(self, tree: "FolderTree"):
        super().__init__(tree)
        self._tree = tree

    def paint(self, painter, option, index):
        tree = self._tree
        q = tree._search_hl_query
        hl = tree._search_hl_path
        if not q or not hl or (index.data(FolderTree.ROLE_PATH) or "") != hl:
            super().paint(painter, option, index)
            return
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        ranges = _token_prefix_ranges(text, q)
        if not ranges:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        opt.text = ""  # 배경/아이콘만 기본 스타일, 텍스트는 직접 리치 렌더.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        doc.setHtml(_highlight_html_red(text, ranges))
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        from app.ui.theme import COLORS
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(
            QPalette.ColorRole.Text, QColor(COLORS.get("text", "#c4cdd8")))
        painter.save()
        y = rect.top() + max(0, (rect.height() - int(doc.size().height())) // 2)
        painter.translate(rect.left(), y)
        doc.documentLayout().draw(painter, ctx)
        painter.restore()


class _CountOverlay(QWidget):
    """파일 개수 컬럼을 가로 스크롤과 무관하게 우측에 고정 표시.
    **tree(scroll area) 의 자식** — viewport 자식이면 QAbstractScrollArea 의
    `viewport.scroll()` 가 가로 스크롤 시 자식까지 같이 끌고 가버린다 (=count 도 스크롤됨).
    tree 자식이면 스크롤 area 의 일반 자식 위젯이라 content scroll 영향을 받지 않음.
    원본 column 1 은 setColumnHidden(1, True) 로 숨김 — 데이터는 그대로 유지되어
    item.text(1) 등으로 읽음.
    """
    OVERLAY_W = 50

    def __init__(self, tree: 'FolderTree'):
        super().__init__(tree)
        self._tree = tree
        self._viewport = tree.viewport()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        tree.verticalScrollBar().valueChanged.connect(self.update)
        tree.horizontalScrollBar().valueChanged.connect(self.update)
        tree.itemExpanded.connect(lambda *a: self.update())
        tree.itemCollapsed.connect(lambda *a: self.update())
        tree.itemSelectionChanged.connect(self.update)
        tree.itemChanged.connect(lambda *a: self.update())
        self._viewport.installEventFilter(self)
        tree.destroyed.connect(self._on_tree_destroyed)
        self.reposition()
        self.show()

    def _on_tree_destroyed(self, *_):
        self._tree = None
        self._viewport = None

    def eventFilter(self, obj, e):
        if obj is self._viewport:
            t = e.type()
            if t == QEvent.Type.Resize or t == QEvent.Type.Move or t == QEvent.Type.Show:
                try:
                    self.reposition()
                except RuntimeError:
                    self._on_tree_destroyed()
        return False

    def reposition(self):
        if self._tree is None or self._viewport is None:
            return
        vp_geo = self._viewport.geometry()
        w = self.OVERLAY_W
        # tree 좌표계 기준 — viewport 의 우측 가장자리에 정렬.
        self.setGeometry(
            max(0, vp_geo.x() + vp_geo.width() - w),
            vp_geo.y(),
            w,
            vp_geo.height(),
        )
        self.raise_()

    def paintEvent(self, e):
        if self._tree is None:
            return
        from app.ui.theme import COLORS
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg = QColor(COLORS.get("bg_panel", "#0f1219"))
        p.fillRect(self.rect(), bg)
        sep = QColor(COLORS.get("border_soft", "#1b2130"))
        p.setPen(QPen(sep, 1))
        p.drawLine(QLineF(0.5, 0.0, 0.5, float(self.height())))
        self._paint_items(p, self._tree.invisibleRootItem())
        p.end()

    def _paint_items(self, p: QPainter, item: QTreeWidgetItem):
        for i in range(item.childCount()):
            child = item.child(i)
            if child.isHidden():
                continue
            self._paint_one(p, child)
            if child.isExpanded():
                self._paint_items(p, child)

    def _paint_one(self, p: QPainter, item: QTreeWidgetItem):
        rect = self._tree.visualItemRect(item)
        if rect.isEmpty() or rect.bottom() < 0 or rect.top() > self.height():
            return
        text = item.text(1)
        if not text:
            return
        dragging = (
            item is getattr(self._tree, "_root_drag_item", None)
            and not getattr(self._tree, "_root_drag_committed", False)
        )
        if item.isSelected():
            from app.ui.theme import COLORS
            sel = QColor(COLORS.get("row_selected", "#0a1e38"))
            p.fillRect(QRect(0, rect.top(), self.width(), rect.height()), sel)
        font = item.font(1)
        p.setFont(font)
        brush = item.foreground(1)
        if brush.style() != Qt.BrushStyle.NoBrush:
            color = brush.color()
        else:
            from app.ui.theme import COLORS
            color = QColor(COLORS.get("text", "#c4cdd8"))
        if dragging:
            color = QColor("#dcecff")
        p.setPen(QPen(color))
        p.drawText(
            QRect(0, rect.top(), self.width() - 6, rect.height()),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            text,
        )


class _StickyAncestorHeader(QWidget):
    """선택한 폴더가 스크롤로 화면 위로 사라져도, 그 상위(조상) 폴더들을
    트리 맨 위에 고정 행으로 표시 (VS Code sticky scroll 방식).
    - 현재 선택(currentItem)의 부모 체인 중 '뷰포트 위로 스크롤되어 사라진'
      것들만 root→가까운부모 순으로 쌓아 그림.
    - 들여쓰기·화살표·좌측 액센트 바를 실제 트리 행과 동일 좌표로 맞춤.
    - 클릭하면 해당 조상으로 스크롤 (선택/검색 범위는 바꾸지 않음).
    tree(scroll area) 의 자식 — viewport 자식이면 가로 스크롤에 끌려감.
    """
    def __init__(self, tree: 'FolderTree'):
        super().__init__(tree)
        self._tree = tree
        self._viewport = tree.viewport()
        self._pinned: List[QTreeWidgetItem] = []
        self._rh = 0
        self._hover_idx = -1  # 화살표 호버 중인 행 — 화살표 밝게.
        self._hover_row = -1  # 마우스 올라간 행(라벨 포함) — 배경 호버 강조(클릭 가능감).
        self._press_pos: Optional[QPoint] = None
        self._press_item: Optional[QTreeWidgetItem] = None
        self.setMouseTracking(True)
        # 깊이별 좌측 콘텐츠 위치 캐시 — 처음 유효할 때 학습해 고정. 멀리 스크롤된
        # 항목의 visualRect 가 빈 값이어도 위치가 안 흔들리게.
        self._cl_cache: Dict[int, int] = {}
        tree.verticalScrollBar().valueChanged.connect(self.refresh)
        # 가로 스크롤 시 — 위치만 바뀌므로 가벼운 repaint (pinned/높이 재계산 불필요).
        tree.horizontalScrollBar().valueChanged.connect(self.update)
        tree.itemExpanded.connect(lambda *a: self.refresh())
        tree.itemCollapsed.connect(lambda *a: self.refresh())
        tree.itemSelectionChanged.connect(self.refresh)
        tree.currentItemChanged.connect(lambda *a: self.refresh())
        self._viewport.installEventFilter(self)
        tree.destroyed.connect(self._on_tree_destroyed)
        self.hide()

    def _on_tree_destroyed(self, *_):
        self._tree = None
        self._viewport = None
        self._press_pos = None
        self._press_item = None

    def eventFilter(self, obj, e):
        if obj is self._viewport and e.type() in (
            QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
        ):
            try:
                self.refresh()
            except RuntimeError:
                self._on_tree_destroyed()
        return False

    @staticmethod
    def _depth(item: QTreeWidgetItem) -> int:
        d = 0
        p = item.parent()
        while p is not None:
            d += 1
            p = p.parent()
        return d

    def _content_left(self, item: QTreeWidgetItem) -> int:
        """본문이 이 항목을 그리는 좌측 콘텐츠 위치(가로스크롤 반영). 그리기/클릭이
        같은 좌표를 쓰도록 공용화. base 는 스크롤 무관값으로 캐시 → 행마다 균일."""
        depth = self._depth(item)
        indent = self._tree.indentation()
        hval = self._tree.horizontalScrollBar().value()
        vr = self._tree.visualRect(self._tree.indexFromItem(item, 0))
        if not vr.isNull() and vr.height() > 0:
            base = vr.left() + hval
            self._cl_cache[depth] = base
        else:
            base = self._cl_cache.get(depth, (depth + 1) * indent)
        cl = base - hval
        # '전체'(최상위, 빈 path)는 화살표가 좌측 바에 붙어 비좁음 → 이 행만 4px 더
        # 들여씀. 전체는 항상 헤더 전용(본문 행은 늘 가려짐)이라 본문 정렬과 어긋남
        # 안 보임.
        if (item.data(0, self._tree.ROLE_PATH) or "") == "":
            cl += 4
        return cl

    def _find_all_root(self) -> Optional[QTreeWidgetItem]:
        """'전체'(all-root) = 경로가 빈 top-level 항목. 없는 탭(즐겨찾기/사용자)이면 None."""
        for i in range(self._tree.topLevelItemCount()):
            it = self._tree.topLevelItem(i)
            if (it.data(0, self._tree.ROLE_PATH) or "") == "":
                return it
        return None

    def _compute_pinned(self) -> List[QTreeWidgetItem]:
        # '전체'(all-root)는 있으면 항상 맨 위 고정 — 시작/선택없음에도 "현재 범위=전체"
        # anchor. 전체 행은 트리 맨 위라 본문과 정확히 겹쳐 중복이 안 보인다.
        # 고정 기준: 행의 top 이 자기 슬롯 y(=이미 고정된 행수 × rh, 헤더 바닥)보다
        # 위로 올라오면 고정. 'top<0'(완전 사라짐) 기준이면 헤더에 가려지지만 아직
        # 고정 안 된 死구간이 생기므로 슬롯 기준으로 그 순간 바로 고정(데드존 제거).
        all_root = self._find_all_root()
        cur = self._tree.currentItem()
        rh = self._row_height([])
        pinned: List[QTreeWidgetItem] = []
        if all_root is not None:
            pinned.append(all_root)
        if cur is not None and cur is not all_root:
            ancestors: List[QTreeWidgetItem] = []
            p = cur.parent()
            while p is not None:
                if p is not all_root:
                    ancestors.append(p)
                p = p.parent()
            ancestors.reverse()  # root 먼저
            for anc in ancestors:
                rect = self._tree.visualItemRect(anc)
                if rect.isNull() or rect.height() <= 0:
                    break
                if rect.top() < len(pinned) * rh:   # 자기 슬롯에 닿음 → 고정
                    pinned.append(anc)
                else:
                    break                            # 아직 본문에 보임 → 그 아래도 보임
            # 선택(검색 기준) 폴더 자신도 자기 슬롯에 닿으면 맨 아래 고정.
            rect = self._tree.visualItemRect(cur)
            if (not rect.isNull() and rect.height() > 0
                    and rect.top() < len(pinned) * rh):
                pinned.append(cur)
        return pinned

    def _row_height(self, pinned: List[QTreeWidgetItem]) -> int:
        """uniformRowHeights — 실제 행 높이를 안정적으로 측정.
        currentItem 은 멀리 스크롤되면 rect 가 0/누락이라 부정확 → 화면에
        보이는 행이나 고정 대상 행(레이아웃 보장됨)에서 잰다."""
        candidates = []
        vt = self._tree.itemAt(2, 2)          # 뷰포트 최상단에 실제로 보이는 행
        if vt is not None:
            candidates.append(vt)
        if pinned:
            candidates.append(pinned[0])
        cur = self._tree.currentItem()
        if cur is not None:
            candidates.append(cur)
        for it in candidates:
            h = self._tree.visualItemRect(it).height()
            if h > 0:
                return h
        return max(18, self._tree.fontMetrics().height() + 6)

    def refresh(self):
        if self._tree is None or self._viewport is None:
            return
        # 트리 빌드/복원 중 setExpanded 가 itemExpanded 를 수만 번 발생 → O(N) refresh
        # 방지. 빌드 끝에 selection/scroll 시 한 번씩만 갱신되면 충분.
        if getattr(self._tree, "_restoring", False):
            return
        pinned = self._compute_pinned()
        if not pinned:
            self._pinned = []
            if self.isVisible():
                self.hide()
            return
        self._rh = self._row_height(pinned)
        self._pinned = pinned
        vp = self._viewport.geometry()
        # 카운트 영역까지 꽉 채워 _CountOverlay 위를 덮음 — 고정 행의 카운트는
        # 헤더가 직접 그려 라벨과 항상 일치.
        self.setGeometry(vp.x(), vp.y(), vp.width(), self._rh * len(pinned))
        self.raise_()
        if not self.isVisible():
            self.show()
        self.update()

    def _arrow_idx_at(self, pos) -> int:
        """포인터 아래 헤더 행의 화살표(브랜치) 영역이면 그 행 index, 아니면 -1."""
        if not self._pinned or self._rh <= 0:
            return -1
        idx = int(pos.y()) // self._rh
        if not (0 <= idx < len(self._pinned)):
            return -1
        item = self._pinned[idx]
        if item.childCount() == 0:
            return -1
        cl = self._content_left(item)
        indent = self._tree.indentation()
        return idx if cl - indent <= int(pos.x()) < cl else -1

    def mousePressEvent(self, e):
        self._press_pos = None
        self._press_item = None
        if not self._pinned or self._rh <= 0:
            return
        # 우클릭 → 그 헤더 행(폴더) 대상으로 본문과 동일한 컨텍스트 메뉴.
        if e.button() == Qt.MouseButton.RightButton:
            idx = int(e.position().y()) // self._rh
            if 0 <= idx < len(self._pinned):
                self._tree.open_context_menu_for_item(
                    self._pinned[idx], e.globalPosition().toPoint())
            return
        # 화살표(브랜치) 영역 클릭 → 접기/펼치기 (본문과 동일). itemExpanded/Collapsed
        # → refresh 로 헤더 자동 갱신. 그 외 영역 → 해당 폴더로 스크롤(선택은 유지).
        ai = self._arrow_idx_at(e.position())
        if ai >= 0:
            it = self._pinned[ai]
            was_expanded = it.isExpanded()
            it.setExpanded(not was_expanded)
            if was_expanded:
                # 큰 서브트리를 접으면 콘텐츠 높이 급감 → 스크롤바 클램프 → 폴더가
                # 본문 중앙으로 튐. 폴더를 top=0 으로 스크롤하면 incremental pinning 이
                # 자기 슬롯(=원래 헤더 행 위치 N*rh)에 다시 고정 → 제자리 유지.
                self._tree.scrollToItem(
                    it, QAbstractItemView.ScrollHint.PositionAtTop)
            return
        # 라벨 클릭 → 그 폴더를 선택(검색 범위 지정). 헤더가 본문 행을 덮어 직접 못
        # 누르는 경우(특히 항상 고정된 '전체')에도 선택이 되도록. 브레드크럼처럼 동작.
        idx = int(e.position().y()) // self._rh
        if 0 <= idx < len(self._pinned):
            it = self._pinned[idx]
            if e.button() == Qt.MouseButton.LeftButton:
                self._press_pos = e.position().toPoint()
                self._press_item = it
            self._tree.setCurrentItem(it)
            self._tree.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtTop)

    def mouseMoveEvent(self, e):
        if (
            self._tree is not None
            and self._press_item is not None
            and self._press_pos is not None
            and e.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = e.position().toPoint() - self._press_pos
            if delta.manhattanLength() >= QApplication.startDragDistance():
                item = self._press_item
                self._press_pos = None
                self._press_item = None
                md = self._tree.mimeData([item])
                if md.hasFormat(MIME_PATHS):
                    drag = QDrag(self)
                    drag.setMimeData(md)
                    drag.exec(Qt.DropAction.CopyAction)
                    return
        row = -1
        if self._pinned and self._rh > 0:
            r = int(e.position().y()) // self._rh
            if 0 <= r < len(self._pinned):
                row = r
        ai = self._arrow_idx_at(e.position())
        if row != self._hover_row or ai != self._hover_idx:
            self._hover_row = row
            self._hover_idx = ai
            self.update()

    def mouseReleaseEvent(self, e):
        self._press_pos = None
        self._press_item = None
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._hover_row != -1 or self._hover_idx != -1:
            self._hover_row = -1
            self._hover_idx = -1
            self.update()

    def paintEvent(self, e):
        if not self._pinned or self._tree is None:
            return
        from app.ui.theme import COLORS
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rh = self._rh
        bg_row = QColor(COLORS.get("bg_sidebar", "#0f1219"))     # 트리 본문 배경
        bg_cnt = QColor(COLORS.get("bg_panel", "#0f1219"))       # 카운트 스트립 배경
        text2 = QColor(COLORS.get("text_secondary", "#8a93a3"))  # 본문 라벨 기본색
        text_sel = QColor(COLORS.get("text", "#c4cdd8"))         # 선택 행 라벨색
        sep = QColor(COLORS.get("border_soft", "#1b2130"))
        sel_bg = QColor(COLORS.get("row_selected", "#0a1e38"))   # 선택 행 전체 배경
        hover_bg = QColor(COLORS.get("hover", "#1b2130"))        # 본문 호버와 동일 톤
        count_x = self.width() - _CountOverlay.OVERLAY_W
        # 본문 QSS 와 동일한 12px 라벨 폰트 (item.font(0)=9pt 와 어긋나 폰트가
        # 달라 보이던 문제 해결).
        label_font = QFont(self._tree.font())
        label_font.setPixelSize(12)
        for i, item in enumerate(self._pinned):
            y = i * rh
            row = QRect(0, y, self.width(), rh)
            selected = item.isSelected()
            p.fillRect(QRect(0, y, count_x, rh), bg_row)
            p.fillRect(QRect(count_x, y, _CountOverlay.OVERLAY_W, rh), bg_cnt)
            if selected:
                # 선택 행 — 본문과 동일하게 행 전체 배경 + 좌측 바.
                p.fillRect(row, sel_bg)
            elif i == self._hover_row:
                # 호버 — 본문처럼 배경 강조해 클릭 가능감.
                p.fillRect(row, hover_bg)
            # 좌측 블루 액센트 바 — 모든 고정 행(선택 폴더 포함)에 동일하게.
            p.fillRect(QRect(0, y, 3, rh), QColor(_COUNT_COLOR))
            content_left = self._content_left(item)
            # 화살표 — 본문 drawBranches 와 동일 좌표/모양. 자식 있을 때만.
            if item.childCount() > 0:
                cx = content_left - 9
                cy = y + rh / 2
                size = 4.5
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(_ARROW_COLOR_HOVER if i == self._hover_idx
                                  else _ARROW_COLOR))
                if item.isExpanded():
                    poly = QPolygonF([
                        QPointF(cx - size, cy - size * 0.4),
                        QPointF(cx + size, cy - size * 0.4),
                        QPointF(cx, cy + size * 0.7),
                    ])
                else:
                    poly = QPolygonF([
                        QPointF(cx - size * 0.5, cy - size),
                        QPointF(cx + size * 0.7, cy),
                        QPointF(cx - size * 0.5, cy + size),
                    ])
                p.drawPolygon(poly)
            # 라벨 — 본문과 동일 폰트/색/위치. 본문 QSS(item margin 4 + padding 4)
            # 만큼 텍스트를 셀 왼쪽에서 8px 안쪽으로 — 화살표-텍스트 간격 본문과 일치.
            p.setFont(label_font)
            p.setPen(QPen(text_sel if selected else text2))
            tx = content_left + 8
            p.drawText(
                QRect(tx, y, count_x - tx - 6, rh),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                item.text(0),
            )
            # 카운트 — _CountOverlay 와 동일(항목의 font(1)/foreground(1)).
            ctext = item.text(1)
            if ctext:
                p.setFont(item.font(1))
                fg1 = item.foreground(1)
                p.setPen(QPen(fg1.color() if fg1.style() != Qt.BrushStyle.NoBrush
                              else text2))
                p.drawText(
                    QRect(count_x, y, _CountOverlay.OVERLAY_W - 6, rh),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    ctext,
                )
            # 행 하단 구분선 — 모든 헤더 행을 '전체'처럼 깔끔히 분리(붙어서 뭉쳐
            # 번져 보이던 것 해소 + 미세 이격). 파란 바(0~3px)는 가로지르지 않게
            # x=3 부터 그어 바를 끊김 없는 연속 스트라이프로 유지.
            p.setPen(QPen(sep, 1))
            p.drawLine(QLineF(3.0, y + rh - 0.5, float(self.width()), y + rh - 0.5))
        # 카운트 영역 좌측 세로 구분선 + 헤더 하단 경계선(바는 비껴 x=3 부터).
        p.setPen(QPen(sep, 1))
        p.drawLine(QLineF(count_x + 0.5, 0.0, count_x + 0.5, float(self.height())))
        p.drawLine(QLineF(3.0, self.height() - 0.5, float(self.width()), self.height() - 0.5))
        p.end()


class FolderTree(QTreeWidget):
    """All Media + 등록된 라이브러리 root 들 + 그 하위 트리.
    선택된 폴더의 절대경로(path_prefix)를 emit.
    우클릭으로 부분 rescan / 라이브러리 제거 요청.
    """
    folderSelected = pyqtSignal(str)         # '' = 전체
    foldersSelected = pyqtSignal(list)       # 다중 선택 경로
    rescanRequested = pyqtSignal(str)        # 절대경로
    rescanManyRequested = pyqtSignal(list)   # 절대경로 리스트
    fullRescanRequested = pyqtSignal(str)    # 절대경로 (강제 재스캔)
    fullRescanManyRequested = pyqtSignal(list)
    removeRootRequested = pyqtSignal(str)    # 절대경로 (등록 루트 한정)
    removeRootsRequested = pyqtSignal(list)
    purgeRootsRequested = pyqtSignal(list)   # 완전 제거 — 모든 저장 정보 물리 삭제
    renameRequested = pyqtSignal(str, str)   # path, display_name
    addToBlacklistRequested = pyqtSignal(list)  # 경로 리스트 (1개 이상)
    favoriteAdded = pyqtSignal(str)          # 즐겨찾기 추가 요청
    favoriteRemoved = pyqtSignal(str)        # 즐겨찾기 제거 요청
    removeFromTabRequested = pyqtSignal(list)  # 사용자 탭 items 에서 제거 요청
    userExpandedStateChanged = pyqtSignal()  # 사용자 토글 → config 저장 trigger

    rootOrderChangeRequested = pyqtSignal(list)

    ROLE_PATH = Qt.ItemDataRole.UserRole
    ROLE_IS_ROOT = Qt.ItemDataRole.UserRole + 1
    ROLE_IS_GROUP = Qt.ItemDataRole.UserRole + 2  # 가상 드라이브 그룹 노드
    ROLE_COUNT = Qt.ItemDataRole.UserRole + 3
    ROLE_INCOMPLETE = Qt.ItemDataRole.UserRole + 4
    ROLE_DEFAULT_NAME = Qt.ItemDataRole.UserRole + 5
    ROLE_IS_FAVORITE_ROOT = Qt.ItemDataRole.UserRole + 6
    ROLE_IS_FAVORITE_ITEM = Qt.ItemDataRole.UserRole + 7

    def wheelEvent(self, e):
        """Shift+휠 → native 가로 스크롤. column 0 = ResizeToContents 라 긴 이름 있으면
        스크롤바 자동 노출. 카운트는 overlay 가 우측에 고정 paint.
        """
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            delta = e.angleDelta().y()
            if delta != 0:
                hbar = self.horizontalScrollBar()
                hbar.setValue(hbar.value() - int(delta * 50 / 120))
                e.accept()
                return
        super().wheelEvent(e)

    def mimeTypes(self) -> List[str]:
        return [MIME_PATHS]

    def mimeData(self, items: List[QTreeWidgetItem]) -> QMimeData:
        """선택 노드들의 절대경로를 JSON 으로. 가상 root(__FAVORITES__)와
        빈 path('전체') 는 제외. 같은 탭 내 drop 은 LibraryTabs 가 source_tab
        비교로 차단."""
        md = QMimeData()
        paths: List[str] = []
        for it in items:
            p = it.data(0, self.ROLE_PATH) or ""
            if not p or p == "__FAVORITES__":
                continue
            paths.append(p)
        if paths:
            md.setData(MIME_PATHS, json.dumps(paths, ensure_ascii=False).encode("utf-8"))
        return md

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setColumnCount(2)
        self.setHeaderLabels(["파일 브라우저", ""])
        # 자체 column header bar 는 숨김 — 상위 side_header 가 '파일 브라우저' 라벨 표시.
        self.setHeaderHidden(True)
        h = self.header()
        # 0번(라이브러리 이름) = ResizeToContents — 콘텐츠보다 좁으면 가로 스크롤바 자동 노출.
        # 우측 50px 는 _CountOverlay 가 가리고 카운트 표시. column 1 은 hidden.
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(False)
        h.setMinimumSectionSize(0)
        self.setHorizontalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBar(RoundedScrollBar(Qt.Orientation.Horizontal, self))
        self.setVerticalScrollBar(RoundedScrollBar(Qt.Orientation.Vertical, self))
        self.setUniformRowHeights(True)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        # 18 = 화살표(▶/▼) 가 rect 안에 들어와 짤리지 않는 최소 indent.
        # 이전 10 은 root level("전체") 의 indicator 가 좌측 끝에서 잘림.
        self.setIndentation(18)
        # font-family 명시 제거 — 시스템 default font 그대로 사용 (검색 결과 목록과 동일 path).
        # 명시 family 의 한글 글리프 fallback 실패로 인한 깨짐 방지. 크기만 조정.
        tree_font = QFont(self.font())
        tree_font.setPointSize(9)
        self.setFont(tree_font)
        self._item_font = tree_font

        # 선택 표시는 drawRow()가 행 안쪽에 직접 그린다. Qt 기본 Highlight 는
        # 별도 선택 배경/장식 표시를 만들 수 있으므로 투명화한다. 텍스트색은 QSS가 담당.
        _pal = self.palette()
        _transparent = QColor(0, 0, 0, 0)
        for _grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                     QPalette.ColorGroup.Disabled):
            _pal.setColor(_grp, QPalette.ColorRole.Highlight, _transparent)
        self.setPalette(_pal)

        self.currentItemChanged.connect(self._on_item_changed)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        # 사용자가 직접 토글한 펼침/접힘 상태 — 재부팅·트리 reload 후에도 보존.
        # load_folders 안에서 setExpanded 가 시그널 발생시키므로 _restoring 가드 필요.
        self._user_expanded_state: Dict[str, bool] = {}
        self._restoring = False
        # 블랙리스트 prefix (정규화 lowercase) — load_folders 끝과 set_blacklist_paths 시
        # 트리 traverse 하며 매칭 노드 setHidden(True). 별도 위치 기억 불필요 —
        # 노드 자체가 트리에 그대로 있고 hidden 만 토글되므로 제거 시 원래 위치 복원.
        self._blacklist_set: set = set()
        self._blacklist_reason: Dict[str, str] = {}
        self.itemExpanded.connect(self._on_item_expanded)
        self.itemCollapsed.connect(self._on_item_collapsed)

        # 카운트 컬럼은 가로 스크롤과 무관하게 viewport 우측에 고정 (overlay 위젯).
        # column 1 native render 만 숨기고 데이터는 그대로 (item.text(1) 등).
        self.setColumnHidden(1, True)
        # 순차 검색(Ctrl+F식) 강조 — 이 경로의 행에서 검색어 부분만 빨강으로.
        self._search_hl_path: str = ""
        self._search_hl_query: str = ""
        self.setItemDelegateForColumn(0, _FolderHighlightDelegate(self))
        self._count_overlay = _CountOverlay(self)
        # 선택 폴더가 위로 스크롤돼 사라져도 상위 폴더를 맨 위에 고정 표시.
        self._sticky_header = _StickyAncestorHeader(self)
        self._list_tooltip_pos = QPoint()
        self._list_tooltip_text = ""
        self._list_tooltip_timer = QTimer(self)
        self._list_tooltip_timer.setSingleShot(True)
        self._list_tooltip_timer.setInterval(_DENSE_LIST_TOOLTIP_DELAY_MS)
        self._list_tooltip_timer.timeout.connect(self._show_delayed_list_tooltip)

        # 다른 탭(LibraryTabs)으로의 drag — 트리 내부 drop 은 차단.
        # selectedItems 의 경로들을 MIME_PATHS JSON 으로 운반.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._root_drag_item: Optional[QTreeWidgetItem] = None
        self._root_drag_parent: Optional[QTreeWidgetItem] = None
        self._root_drag_snapshot: List[QTreeWidgetItem] = []
        self._root_drag_committed = False
        self._root_drag_cancelled = False
        self._root_drag_animations: List[QPropertyAnimation] = []
        self._full_row_press_item: Optional[QTreeWidgetItem] = None
        self._full_row_press_pos: Optional[QPoint] = None

        # 탭 컨텍스트 — 컨텍스트 메뉴 분기. LibraryTabs 가 트리 생성 시 set.
        # favorites set 은 _norm(path).lower() — '즐겨찾기 추가' vs '즐겨찾기에서 제거'
        # 토글 정확성 위해. ROLE_IS_FAVORITE_ITEM 만으로는 가상 root 없는 즐겨찾기 탭
        # 에서 판정 불가.
        self._tab_type: str = TAB_TYPE_ALL
        self._favorites_set: set = set()

        # 검색 기준으로 선택된 폴더의 상위(조상) 경로 집합 (정규화 lowercase).
        # 선택 변경 시 재계산 → drawRow 가 좌측 액센트 바를 그려 "이 아래에
        # 선택된 검색 범위가 있다"를 표시. 선택 항목 자신은 풀 하이라이트라 제외.
        self._ancestor_paths: set = set()
        # '전체'(all-root)는 경로가 빈 문자열이라 _ancestor_paths(경로집합)에 못 담음.
        # 선택 폴더의 조상으로 '전체'가 포함되는지는 이 플래그로 별도 추적.
        self._root_is_ancestor = False
        # 화살표 호버 — 마우스가 올라간 항목의 화살표를 밝게. mouseTracking 켜져 있음.
        self._hover_arrow_item: Optional[QTreeWidgetItem] = None

    def set_tab_context(self, tab_type: str):
        self._tab_type = tab_type or TAB_TYPE_ALL

    def set_favorites(self, favorites: List[str]):
        self._favorites_set = {self._norm(p).lower() for p in (favorites or []) if p}

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_count_overlay'):
            self._count_overlay.reposition()

    # ───────────── 사용자 expanded state ─────────────
    def _on_item_expanded(self, item: QTreeWidgetItem):
        # 빌드/복원 중에는 visibility refresh 스킵. 빌드 끝에 _apply_counts_recursive
        # 에서 한 번만 호출됨. 수만 노드에서 매 setExpanded 호출되면 O(N²) freeze.
        if self._restoring:
            return
        self._refresh_incomplete_visibility()
        path = item.data(0, self.ROLE_PATH)
        if path:
            self._user_expanded_state[self._norm(path).lower()] = True
            self.userExpandedStateChanged.emit()

    def _on_item_collapsed(self, item: QTreeWidgetItem):
        if self._restoring:
            return
        self._refresh_incomplete_visibility()
        # 선택(검색 기준) 폴더의 상위를 접으면 → 선택을 접힌 그 상위 폴더로 이동.
        # 접힌 폴더의 자식은 숨겨져 선택 표시가 사라지므로, "상위를 접음 = 검색 범위를
        # 그 상위로 올림"으로 해석 (일반 탐색기 UX). currentItem 변경이 folderSelected
        # 를 emit 해 검색 범위가 따라 이동.
        cur = self.currentItem()
        if cur is not None and cur is not item:
            p = cur.parent()
            while p is not None:
                if p is item:
                    # setCurrentItem 은 Qt 기본 auto-scroll 로 뷰를 움직여(접힌 폴더가
                    # top<0 면 보이게 하려고 튐) 헤더 고정 위치가 무너짐 → 스크롤 위치를
                    # 전후로 보존해 접힌 폴더가 기존 헤더 행 자리를 지키게.
                    vbar = self.verticalScrollBar()
                    v = vbar.value()
                    self.setCurrentItem(item)
                    vbar.setValue(v)
                    break
                p = p.parent()
        path = item.data(0, self.ROLE_PATH)
        if path:
            self._user_expanded_state[self._norm(path).lower()] = False
            self.userExpandedStateChanged.emit()

    # ───────────── 블랙리스트 가시성 ─────────────
    def set_blacklist_paths(self, entries: List):
        """블랙리스트 prefix + 사유 갱신 + 즉시 트리에 적용. 빈 리스트면 모두 복원.
        entries: [{"path","description"}] 또는 [path str](사유 없음)."""
        self._blacklist_set = set()
        self._blacklist_reason = {}
        for e in (entries or []):
            if isinstance(e, dict):
                p, desc = e.get("path") or "", (e.get("description") or "").strip()
            else:
                p, desc = e, ""
            if not p:
                continue
            key = self._norm(p).lower()
            self._blacklist_set.add(key)
            self._blacklist_reason[key] = desc
        self._apply_blacklist_visibility(self.invisibleRootItem())

    def _apply_blacklist_visibility(self, item: QTreeWidgetItem):
        for i in range(item.childCount()):
            child = item.child(i)
            path = child.data(0, self.ROLE_PATH) or ""
            if path:
                hidden = self._norm(path).lower() in self._blacklist_set
                child.setHidden(hidden)
            self._apply_blacklist_visibility(child)

    def _apply_user_expanded(self, item: QTreeWidgetItem):
        for i in range(item.childCount()):
            child = item.child(i)
            path = child.data(0, self.ROLE_PATH)
            if path:
                key = self._norm(path).lower()
                if key in self._user_expanded_state:
                    child.setExpanded(self._user_expanded_state[key])
            self._apply_user_expanded(child)

    # ───────────── 헬퍼 ─────────────
    @staticmethod
    def _norm(p: str) -> str:
        return os.path.normpath(p).rstrip("\\/")

    @staticmethod
    def _common_parent(paths: List[str]) -> Optional[str]:
        if len(paths) < 2:
            return None
        splits = [p.replace("/", "\\").split("\\") for p in paths]
        common_parts: List[str] = []
        for parts in zip(*splits):
            first_low = parts[0].lower()
            if all(seg.lower() == first_low for seg in parts):
                common_parts.append(parts[0])
            else:
                break
        if not common_parts:
            return None
        if len(common_parts) == 1 and len(common_parts[0]) == 2 and common_parts[0][1] == ":":
            return common_parts[0].upper() + "\\"
        return "\\".join(common_parts)

    @staticmethod
    def _set_count_cell(item: QTreeWidgetItem, count: int, is_root: bool = True,
                        incomplete: int = 0):
        """카운트 셀.
        - 미완료 누적이 1 이상이면 색을 주황으로 (디자인/계층 그대로, 색만)
        - 그 외:
            - is_root=True: blue, bold
            - is_root=False: gray, 80% size
        """
        item.setText(1, f"{count:,}")
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if incomplete > 0:
            item.setForeground(1, QBrush(QColor(_INCOMPLETE_COLOR)))
            f = item.font(1)
            f.setBold(is_root)
            if not is_root:
                f.setPointSize(max(7, int(f.pointSize() * 0.8)))
            item.setFont(1, f)
            item.setToolTip(
                1,
                f"미완료(대기/실패) {incomplete:,}개 포함 — "
                f"라이브러리 현황 다이얼로그에서 재시도/제거",
            )
            return

        item.setToolTip(1, "")
        if is_root:
            item.setForeground(1, QBrush(QColor(_COUNT_COLOR)))
            f = item.font(1); f.setBold(True); item.setFont(1, f)
        else:
            item.setForeground(1, QBrush(QColor("#999999")))
            f = item.font(1); f.setBold(False)
            f.setPointSize(max(7, int(f.pointSize() * 0.8)))
            item.setFont(1, f)

    # ───────────── 트리 빌드 ─────────────
    def load_folders(self, folders: List[str], roots: List[str],
                     root_counts: Optional[Dict[str, int]] = None,
                     total_count: Optional[int] = None,
                     incomplete_counts: Optional[Dict[str, int]] = None,
                     total_incomplete: Optional[int] = None,
                     display_names: Optional[Dict[str, str]] = None,
                     favorites: Optional[List[str]] = None,
                     show_favorites_root: bool = False,
                     show_all_root: bool = True,
                     all_root_label: str = "전체"):
        """show_favorites_root=False: ★즐겨찾기 가상 root 생략 (LibraryTabs 도입 후
        즐겨찾기 탭이 별도 트리로 처리). show_all_root=False: '전체' root 도 생략
        (커스텀 탭에서 라이브러리 root 들이 직접 top-level)."""
        # setExpanded 가 itemExpanded/Collapsed 시그널 발생시키므로 _user_expanded_state
        # 갱신 차단. _apply_user_expanded 가 마지막에 사용자 상태 복원.
        # setUpdatesEnabled(False) — 빌드 중 paint 비용 0. _CountOverlay 의
        # 시그널 트리거 update() 도 paint 단계에서 skip 되어 비용 0.
        self._restoring = True
        self.setUpdatesEnabled(False)
        self.clear()
        self._ancestor_paths = set()
        display_names = display_names or {}
        favorites = favorites or []

        # 1. 즐겨찾기 root (legacy — show_favorites_root=True 일 때만)
        if show_favorites_root:
            fav_root = QTreeWidgetItem(["★ 즐겨찾기"])
            fav_root.setData(0, self.ROLE_PATH, "__FAVORITES__")
            fav_root.setData(0, self.ROLE_IS_FAVORITE_ROOT, True)
            self.addTopLevelItem(fav_root)

            for fav_path in sorted(favorites):
                name = display_names.get(fav_path.lower(), os.path.basename(fav_path) or fav_path)
                fav_item = QTreeWidgetItem([name])
                fav_item.setData(0, self.ROLE_PATH, fav_path)
                fav_item.setData(0, self.ROLE_IS_FAVORITE_ITEM, True)
                fav_item.setToolTip(0, fav_path)
                fav_root.addChild(fav_item)
                if root_counts:
                    low = self._norm(fav_path).lower()
                    if low in root_counts:
                        self._set_count_cell(fav_item, root_counts[low], is_root=False,
                                            incomplete=(incomplete_counts or {}).get(low, 0))

        # 2. 전체 root (show_all_root=False 면 라이브러리 root 들이 직접 top-level)
        all_item: Optional[QTreeWidgetItem]
        if show_all_root:
            all_item = QTreeWidgetItem([all_root_label])
            all_item.setData(0, self.ROLE_PATH, "")
            all_item.setData(0, self.ROLE_IS_ROOT, False)
            self.addTopLevelItem(all_item)
            if total_count is not None:
                self._set_count_cell(
                    all_item, int(total_count), is_root=False,
                    incomplete=int(total_incomplete or 0),
                )
        else:
            all_item = None

        # nested 분리 — 짧은 경로 순으로 정렬 후 부모 매칭
        root_order: Dict[str, int] = {}
        normalized_roots: List[str] = []
        for raw in roots:
            norm = self._norm(raw)
            key = norm.lower()
            if key in root_order:
                continue
            root_order[key] = len(root_order)
            normalized_roots.append(norm)
        roots_by_depth = sorted(normalized_roots, key=len)
        top_level_roots: List[str] = []
        nested_root_paths: set = set()
        for r in roots_by_depth:
            r_low = r.lower()
            is_nested = False
            for top in top_level_roots:
                t_low = top.lower()
                if (r_low.startswith(t_low + "\\") or r_low.startswith(t_low + "/")
                        or r_low.startswith(t_low + os.sep.lower())):
                    is_nested = True
                    break
            if is_nested:
                nested_root_paths.add(r)
            else:
                top_level_roots.append(r)
        top_level_roots.sort(key=lambda p: root_order.get(p.lower(), 0))

        # 드라이브별 그룹화 — 같은 드라이브 top_level 루트 2개 이상이면 공통 부모로 가상 그룹
        drive_groups: Dict[str, List[str]] = {}
        for r in top_level_roots:
            if len(r) >= 2 and r[1] == ":":
                drv = r[:2].lower()
            else:
                drv = ""
            drive_groups.setdefault(drv, []).append(r)

        # all_item 이 없으면(show_all_root=False) top-level 로 직접 add.
        def _add_top(node: QTreeWidgetItem):
            if all_item is not None:
                all_item.addChild(node)
            else:
                self.addTopLevelItem(node)

        root_nodes: Dict[str, QTreeWidgetItem] = {}
        group_nodes: Dict[str, QTreeWidgetItem] = {}
        ordered_drives = sorted(
            drive_groups.keys(),
            key=lambda d: min(root_order.get(p.lower(), 0) for p in drive_groups[d])
        )
        for drv in ordered_drives:
            grp_roots = drive_groups[drv]
            common = self._common_parent(grp_roots) if drv and len(grp_roots) >= 2 else None
            if common:
                group_node = QTreeWidgetItem([common])
                group_node.setData(0, self.ROLE_PATH, common)
                group_node.setData(0, self.ROLE_IS_ROOT, False)
                group_node.setData(0, self.ROLE_IS_GROUP, True)
                _add_top(group_node)
                group_nodes[common.lower()] = group_node
                for r in sorted(grp_roots, key=lambda p: root_order.get(p.lower(), 0)):
                    rel = r[len(common):].lstrip("\\/") or r
                    label = display_names.get(r.lower(), rel)
                    node = QTreeWidgetItem([label])
                    node.setData(0, self.ROLE_PATH, r)
                    node.setData(0, self.ROLE_IS_ROOT, True)
                    node.setData(0, self.ROLE_DEFAULT_NAME, rel)
                    node.setToolTip(0, r)
                    group_node.addChild(node)
                    root_nodes[r.lower()] = node
            else:
                for r in sorted(grp_roots, key=lambda p: root_order.get(p.lower(), 0)):
                    default_name = os.path.basename(r.rstrip("\\/")) or r
                    node = QTreeWidgetItem([display_names.get(r.lower(), default_name)])
                    node.setData(0, self.ROLE_PATH, r)
                    node.setData(0, self.ROLE_IS_ROOT, True)
                    node.setData(0, self.ROLE_DEFAULT_NAME, default_name)
                    node.setToolTip(0, r)
                    _add_top(node)
                    root_nodes[r.lower()] = node

        # 폴더들을 가장 긴 루트 prefix 부터 매칭 (사전 정렬/정규화)
        # rk는 이미 low_sep으로 정규화된 상태여야 함.
        sorted_root_keys = sorted(root_nodes.keys(), key=len, reverse=True)
        
        nodes_by_root = {k: {"": v} for k, v in root_nodes.items()}
        nested_lower = {self._norm(p).lower() for p in nested_root_paths}
        merged_folders = list(folders) + list(nested_root_paths)

        # 경로 구분자 통일 (Windows \ 기준)
        for folder in merged_folders:
            # 1. 슬래시 통일 (normpath 대신 가벼운 replace). 매칭·캐시 키는 소문자
            #    사본으로, 라벨과 ROLE_PATH 는 원본 대소문자로 만든다. 예전엔 소문자
            #    경로로 둘 다 만들어 (a) 폴더 이름이 전부 소문자로 보이고 (b) 그
            #    경로로 재스캔하면 DB 의 원본 대소문자 경로와 안 맞아 인덱스 행이
            #    새 id 로 교체되면서 중복 검수 제외까지 날아갔다.
            orig_folder = folder.replace("/", "\\").rstrip("\\")
            n_folder = orig_folder.lower()

            matched_root = None
            for rk in sorted_root_keys:
                # rk도 \로 통일되어 있다고 가정 (rebuild_tree에서 이미 수행)
                # 단순 startswith 가 아닌 폴더 경계( \ ) 확인으로 오진 방지
                if n_folder == rk:
                    matched_root = rk
                    break
                if n_folder.startswith(rk + "\\"):
                    matched_root = rk
                    break
            
            if matched_root is None:
                continue

            root_node = root_nodes[matched_root]
            root_abs = root_node.data(0, self.ROLE_PATH)
            
            # 상대 경로 추출
            rel = n_folder[len(matched_root):].lstrip("\\")
            if not rel:
                continue
                
            parts = rel.split("\\")
            # 원본 대소문자 조각 — lower() 가 길이를 바꾸는 예외 문자가 섞이면
            # 조각 수가 어긋나므로 그때만 소문자 조각으로 폴백.
            orig_parts = orig_folder[len(matched_root):].lstrip("\\").split("\\")
            if len(orig_parts) != len(parts):
                orig_parts = parts
            cur_rel = ""
            cur_orig = ""
            parent = root_node
            cache = nodes_by_root[matched_root]

            for part, orig_part in zip(parts, orig_parts):
                if not part: continue
                cur_rel = f"{cur_rel}\\{part}" if cur_rel else part
                cur_orig = f"{cur_orig}\\{orig_part}" if cur_orig else orig_part
                if cur_rel in cache:
                    parent = cache[cur_rel]
                    continue

                # 새로운 노드 생성 시 필요한 절대 경로 복원
                # root_abs의 슬래시 방향을 유지하여 저장
                cur_abs = f"{root_abs}\\{cur_orig}".replace("/", "\\")
                label = display_names.get(cur_abs.lower(), orig_part)
                node = QTreeWidgetItem([label])
                node.setData(0, self.ROLE_PATH, cur_abs)
                node.setData(0, self.ROLE_DEFAULT_NAME, orig_part)
                node.setToolTip(0, cur_abs)
                is_nested_root = cur_abs.lower() in nested_lower
                node.setData(0, self.ROLE_IS_ROOT, is_nested_root)
                parent.addChild(node)
                cache[cur_rel] = node
                parent = node

        if all_item is not None:
            # '전체' 탭: 라이브러리(root)가 보이는 데까지만 펼친다 — 전체+드라이브
            # 그룹만 펼치고 라이브러리 안쪽은 접힌 채(시인성). 사용자가 직접 펼친
            # 것은 아래 _apply_user_expanded 가 복원.
            all_item.setExpanded(True)
            for n in group_nodes.values():
                n.setExpanded(True)
        else:
            # 사용자/즐겨찾기 탭: 라이브러리 root 는 기본 접힌 상태로 둠 — 드래그로
            # 추가/이동한 직후 자동으로 1단계 펼쳐지던 현상 방지. 그룹 노드만 펼쳐
            # root 들이 보이게 하고, 사용자가 직접 펼친 상태는 아래 _apply_user_expanded 가 복원.
            for n in group_nodes.values():
                n.setExpanded(True)

        if root_counts or incomplete_counts:
            # all_item 가 None 이면 invisibleRootItem 부터 traverse (탭 모드).
            start = all_item if all_item is not None else self.invisibleRootItem()
            self._apply_counts_recursive(
                start, root_counts or {}, incomplete_counts or {}
            )

        self._apply_user_expanded(self.invisibleRootItem())
        self._apply_blacklist_visibility(self.invisibleRootItem())
        self._restoring = False
        self.setUpdatesEnabled(True)
        # 마지막에 한 번만 overlay/visibility 갱신.
        if hasattr(self, "_count_overlay"):
            self._count_overlay.update()
        # 빌드 직후 '전체' 상시 헤더가 뜨도록 명시 갱신 (선택/스크롤 신호가 없어도).
        if hasattr(self, "_sticky_header"):
            self._sticky_header.refresh()

    # ───────────── 증분 갱신 (탭 드롭/삭제 시 clear+rebuild 회피) ─────────────
    # 라이브러리 하나만 추가/제거해도 load_folders 가 그 탭 전체를 다시 그려 미세
    # 프리즈가 생긴다. 단순 케이스(새 단독 root / 기존 그룹 공통부모 하위 / 노드 1개
    # 제거)는 아래 증분 메서드로 처리하고, 그룹 경계(공통부모) 재구성이 필요한
    # 경우만 False 를 돌려 호출자가 load_folders 로 폴백한다.

    def _find_node_by_path(self, path: str,
                           parent: Optional[QTreeWidgetItem] = None) -> Optional[QTreeWidgetItem]:
        key = self._norm(path).lower()
        parent = parent or self.invisibleRootItem()
        for i in range(parent.childCount()):
            child = parent.child(i)
            cp = child.data(0, self.ROLE_PATH)
            if cp and self._norm(cp).lower() == key:
                return child
            found = self._find_node_by_path(path, child)
            if found is not None:
                return found
        return None

    def iter_folder_nodes(self) -> List[tuple]:
        """라이브러리 검색 제안용 — 현재 트리의 실제 폴더 노드 (name, path, count, reason).
        '전체'(빈 path)/즐겨찾기 가상 루트는 제외. reason 은 블랙리스트면 사유 문자열
        (사유 없으면 ""), 아니면 None. 블랙리스트 노드+하위도 포함해 검색 목록에서
        빗금·사유로 표시한다. name 은 실제 폴더명(경로 마지막 칸)."""
        out: List[tuple] = []

        def walk(item: QTreeWidgetItem, inherited):
            for i in range(item.childCount()):
                c = item.child(i)
                p = c.data(0, self.ROLE_PATH) or ""
                reason = inherited
                if p and reason is None:
                    reason = self._blacklist_reason.get(self._norm(p).lower())
                # 블랙리스트 외 사유로 숨긴 노드는 제외(현재 없음 — 방어적).
                if c.isHidden() and reason is None:
                    continue
                if p and p != "__FAVORITES__":
                    name = os.path.basename(p.rstrip("\\/")) or p
                    out.append((name, p, int(c.data(1, self.ROLE_COUNT) or 0), reason))
                walk(c, reason)

        walk(self.invisibleRootItem(), None)
        return out

    def select_folder(self, path: str) -> bool:
        """검색 제안에서 고른 폴더를 트리에서 선택(=검색 범위 지정). 상위를 펼쳐
        보이게 하고 가운데로 스크롤. setCurrentItem 이 folderSelected emit."""
        node = self._find_node_by_path(path)
        if node is None:
            return False
        p = node.parent()
        while p is not None:
            p.setExpanded(True)
            p = p.parent()
        self.setCurrentItem(node)
        self.scrollToItem(node, QAbstractItemView.ScrollHint.PositionAtCenter)
        # scrollToItem 이 긴 이름을 보이려 가로를 끝까지 밀어버린다 → 이름 시작이
        # 보이도록 가로 스크롤은 좌측(0)으로 되돌린다.
        self.horizontalScrollBar().setValue(0)
        return True

    def reveal_folder(self, path: str, query: str = "", select: bool = False) -> bool:
        """검색 이동 — 해당 폴더를 펼쳐 가운데로 스크롤 + 검색어 부분 빨강 강조.
        select=False(화살표 순차이동): 선택/folderSelected 없이 위치만(Ctrl+F식).
        select=True(드랍다운 클릭): setCurrentItem 으로 명시 선택 = folderSelected emit
        (검색범위 지정·legacy 선택 동작)."""
        node = self._find_node_by_path(path)
        if node is None:
            return False
        p = node.parent()
        while p is not None:
            p.setExpanded(True)
            p = p.parent()
        self._search_hl_path = path
        self._search_hl_query = query or ""
        if select:
            self.setCurrentItem(node)
        self.scrollToItem(node, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.horizontalScrollBar().setValue(0)
        self.viewport().update()
        return True

    def clear_search_highlight(self):
        """검색어 강조 해제 — 검색창 비우기/닫기 시."""
        if self._search_hl_path or self._search_hl_query:
            self._search_hl_path = ""
            self._search_hl_query = ""
            self.viewport().update()

    def collect_match_hierarchy(self, matched_paths) -> List[tuple]:
        """검색 결과 드랍다운(계층형)용 — 트리를 1회만 순회(O(트리))해 '매치이거나
        매치의 조상'인 노드를 pre-order 로 반환. 각 행:
          (depth, 표시이름, path, 매치여부, 막내여부, cont, 사운드개수)
        cont = 조상별 '뒤에 형제 더 있음' 플래그 튜플(len=depth). 델리게이트가 이걸로
        트리 연결선(│ ├ └)을 그린다. 개수는 트리 아이템에 이미 있어 추가 비용 없음.
        가상 루트(빈 path/__FAVORITES__)는 행 생략하고 자식만 같은 depth 로 끌어올려
        들여쓰기를 얕게 유지."""
        matched = set(matched_paths)

        # 1단계 — 매치/매치조상만 남긴 중첩 구조 (가상 루트는 자식 끌어올림).
        def prune(item):
            p = item.data(0, self.ROLE_PATH) or ""
            emittable = bool(p) and p != "__FAVORITES__"
            kids = []
            for i in range(item.childCount()):
                c = item.child(i)
                if c.isHidden():
                    continue
                kids.extend(prune(c))
            if not emittable:
                return kids
            if p in matched or kids:
                cnt = int(item.data(1, self.ROLE_COUNT) or 0)
                return [(item.text(0), p, p in matched, cnt, kids)]
            return []

        roots = prune(self.invisibleRootItem())

        # 2단계 — pre-order 평탄화 + 연결선 메타(막내여부/조상연속).
        out: List[tuple] = []

        def flatten(nodes, cont):
            depth = len(cont)
            last_i = len(nodes) - 1
            for i, (name, path, is_match, cnt, kids) in enumerate(nodes):
                is_last = (i == last_i)
                out.append((depth, name, path, is_match, is_last, tuple(cont), cnt))
                flatten(kids, cont + [not is_last])

        flatten(roots, [])
        return out

    def _collapse_all(self, items: List[QTreeWidgetItem]):
        """우클릭한 폴더(들) 자신과 그 아래 전부 접기. _restoring 가드로 항목마다
        오던 itemCollapsed 핸들러 비용을 막고, 끝에 한 번만 상태/오버레이 갱신."""
        self._restoring = True
        try:
            for it in items:
                self._collapse_subtree(it)
                it.setExpanded(False)  # (A) 클릭한 폴더 자신도 접음
                p = it.data(0, self.ROLE_PATH)
                if p:
                    self._user_expanded_state[self._norm(p).lower()] = False
        finally:
            self._restoring = False
        self.userExpandedStateChanged.emit()
        if hasattr(self, "_count_overlay"):
            self._count_overlay.update()
        if hasattr(self, "_sticky_header"):
            self._sticky_header.refresh()
        self.viewport().update()

    def _collapse_subtree(self, item: QTreeWidgetItem):
        for i in range(item.childCount()):
            c = item.child(i)
            self._collapse_subtree(c)
            if c.isExpanded():
                c.setExpanded(False)
            p = c.data(0, self.ROLE_PATH)
            if p:
                self._user_expanded_state[self._norm(p).lower()] = False

    def remove_path_node(self, path: str) -> bool:
        """단일 경로 노드(및 하위)를 제거. 그룹 노드가 자식 2개 미만으로 줄어
        해체가 필요하면 변경 없이 False (=호출자 full rebuild)."""
        node = self._find_node_by_path(path)
        if node is None:
            return True  # 이미 없음
        parent = node.parent()
        if parent is not None:
            if parent.data(0, self.ROLE_IS_GROUP) and parent.childCount() <= 2:
                return False  # 그룹 해체 필요 — 폴백
            parent.removeChild(node)
        else:
            self.takeTopLevelItem(self.indexOfTopLevelItem(node))
        self._refresh_incomplete_visibility()
        if hasattr(self, "_count_overlay"):
            self._count_overlay.update()
        return True

    def insert_root_subtree(self, root_path: str, folders: List[str],
                            root_counts: Optional[Dict[str, int]] = None,
                            incomplete_counts: Optional[Dict[str, int]] = None,
                            display_names: Optional[Dict[str, str]] = None) -> bool:
        """단일 라이브러리 root 를 증분 삽입. 드라이브 그룹 공통부모가 바뀌어야
        하거나 기존 단독 root 와 그룹을 새로 만들어야 하면 False (=호출자 rebuild)."""
        root_counts = root_counts or {}
        incomplete_counts = incomplete_counts or {}
        display_names = display_names or {}
        r = self._norm(root_path)
        r_low = r.lower()
        if self._find_node_by_path(r) is not None:
            return True  # 이미 있음 (중복 드롭)
        drv = r_low[:2] if len(r_low) >= 2 and r_low[1] == ":" else ""

        inv = self.invisibleRootItem()
        same_drive_group: Optional[QTreeWidgetItem] = None
        same_drive_root_cnt = 0
        for i in range(inv.childCount()):
            it = inv.child(i)
            p = it.data(0, self.ROLE_PATH) or ""
            if not p:
                continue
            p_low = self._norm(p).lower()
            d = p_low[:2] if len(p_low) >= 2 and p_low[1] == ":" else ""
            if d != drv:
                continue
            if it.data(0, self.ROLE_IS_GROUP):
                same_drive_group = it
            elif it.data(0, self.ROLE_IS_ROOT):
                same_drive_root_cnt += 1

        if same_drive_group is not None:
            common = self._norm(same_drive_group.data(0, self.ROLE_PATH))
            c_low = common.lower()
            if not (r_low == c_low or r_low.startswith(c_low + "\\")):
                return False  # 공통부모 축소 필요 — 폴백
            rel = r[len(common):].lstrip("\\/") or r
            node = self._make_root_node(r, display_names.get(r_low, rel), rel)
            self._insert_child_sorted(same_drive_group, node)
        elif same_drive_root_cnt >= 1:
            return False  # 그룹 신규 생성 + 기존 root 재부모 필요 — 폴백
        else:
            default_name = os.path.basename(r.rstrip("\\/")) or r
            node = self._make_root_node(r, display_names.get(r_low, default_name), default_name)
            self._insert_top_level_sorted(node)

        self._restoring = True
        self.setUpdatesEnabled(False)
        try:
            self._build_root_subtree(node, r, folders, display_names)
            self._apply_counts_recursive(node, root_counts, incomplete_counts)
            if r_low in self._user_expanded_state:
                node.setExpanded(self._user_expanded_state[r_low])
            self._apply_user_expanded(node)
            self._apply_blacklist_visibility(node)
        finally:
            self._restoring = False
            self.setUpdatesEnabled(True)
        if hasattr(self, "_count_overlay"):
            self._count_overlay.update()
        return True

    def _make_root_node(self, abs_path: str, label: str, default_name: str) -> QTreeWidgetItem:
        node = QTreeWidgetItem([label])
        node.setData(0, self.ROLE_PATH, abs_path)
        node.setData(0, self.ROLE_IS_ROOT, True)
        node.setData(0, self.ROLE_DEFAULT_NAME, default_name)
        node.setToolTip(0, abs_path)
        return node

    def _build_root_subtree(self, root_node: QTreeWidgetItem, root_abs: str,
                            folders: List[str], display_names: Dict[str, str]):
        """root_node 아래로 folders(이 root 하위 절대경로) 를 계층 생성.
        load_folders 의 폴더 매칭 루프와 동일 규칙 (단일 root 한정)."""
        rk = root_abs.replace("/", "\\").lower().rstrip("\\")
        cache: Dict[str, QTreeWidgetItem] = {"": root_node}
        for folder in folders:
            # 매칭·캐시 키는 소문자, 라벨/ROLE_PATH 는 원본 대소문자 (load_folders 와 동일)
            orig_folder = folder.replace("/", "\\").rstrip("\\")
            n_folder = orig_folder.lower()
            if n_folder == rk or not n_folder.startswith(rk + "\\"):
                continue
            rel = n_folder[len(rk):].lstrip("\\")
            parts = rel.split("\\")
            orig_parts = orig_folder[len(rk):].lstrip("\\").split("\\")
            if len(orig_parts) != len(parts):
                orig_parts = parts
            cur_rel = ""
            cur_orig = ""
            parent = root_node
            for part, orig_part in zip(parts, orig_parts):
                if not part:
                    continue
                cur_rel = f"{cur_rel}\\{part}" if cur_rel else part
                cur_orig = f"{cur_orig}\\{orig_part}" if cur_orig else orig_part
                if cur_rel in cache:
                    parent = cache[cur_rel]
                    continue
                cur_abs = f"{root_abs}\\{cur_orig}".replace("/", "\\")
                node = QTreeWidgetItem([display_names.get(cur_abs.lower(), orig_part)])
                node.setData(0, self.ROLE_PATH, cur_abs)
                node.setData(0, self.ROLE_DEFAULT_NAME, orig_part)
                node.setData(0, self.ROLE_IS_ROOT, False)
                node.setToolTip(0, cur_abs)
                parent.addChild(node)
                cache[cur_rel] = node
                parent = node

    def _insert_child_sorted(self, parent: QTreeWidgetItem, node: QTreeWidgetItem):
        key = (node.data(0, self.ROLE_PATH) or "").lower()
        for i in range(parent.childCount()):
            cp = (parent.child(i).data(0, self.ROLE_PATH) or "").lower()
            if cp > key:
                parent.insertChild(i, node)
                return
        parent.addChild(node)

    def _insert_top_level_sorted(self, node: QTreeWidgetItem):
        key = (node.data(0, self.ROLE_PATH) or "").lower()
        for i in range(self.topLevelItemCount()):
            cp = (self.topLevelItem(i).data(0, self.ROLE_PATH) or "").lower()
            if cp and cp > key:
                self.insertTopLevelItem(i, node)
                return
        self.addTopLevelItem(node)

    def _apply_counts_recursive(self, item: QTreeWidgetItem,
                                norm_counts: Dict[str, int],
                                incomplete_counts: Dict[str, int],
                                _is_root_call: bool = True):
        """카운트 셀 재귀 설정.
        _refresh_incomplete_visibility() 는 트리 전체 순회 O(N) 이라
        재귀 안에서 매 노드마다 부르면 O(N²) — 수만 폴더에서 수초~수십초 멈춤.
        반드시 최상위 호출자에서 1회만.
        """
        path = item.data(0, self.ROLE_PATH)
        if path:
            key = self._norm(path).lower()
            if key in norm_counts:
                item.setData(1, self.ROLE_COUNT, int(norm_counts[key]))
                item.setData(1, self.ROLE_INCOMPLETE, int(incomplete_counts.get(key, 0)))
                self._set_count_cell(
                    item, norm_counts[key], is_root=False,
                    incomplete=incomplete_counts.get(key, 0),
                )
        for i in range(item.childCount()):
            self._apply_counts_recursive(
                item.child(i), norm_counts, incomplete_counts, _is_root_call=False
            )
        if _is_root_call:
            self._refresh_incomplete_visibility()

    def update_counts(self, folder_counts: Optional[Dict[str, int]],
                      total_count: int,
                      incomplete_counts: Optional[Dict[str, int]] = None,
                      total_incomplete: int = 0):
        """가벼운 부분 갱신 — 트리 노드 재생성 없이 카운트 셀만 재귀로 설정.
        Phase1 진행 중 5초 주기 갱신용 — load_folders 의 clear + rebuild 가 UI 스레드
        freeze 1초+ 일으키던 회귀를 해결."""
        root = self.invisibleRootItem()
        if root.childCount() == 0:
            return
        # 0번 자식이 즐겨찾기, 1번이 "전체"
        all_item = None
        for i in range(min(2, root.childCount())):
            it = root.child(i)
            if it and it.data(0, self.ROLE_PATH) == "":
                all_item = it
                break
        if all_item is not None:
            all_item.setData(1, self.ROLE_INCOMPLETE, int(total_incomplete or 0))
            self._set_count_cell(
                all_item, int(total_count or 0), is_root=False,
                incomplete=int(total_incomplete or 0),
            )
        if folder_counts or incomplete_counts:
            for i in range(root.childCount()):
                self._apply_counts_recursive(
                    root.child(i), folder_counts or {}, incomplete_counts or {}
                )

    def update_incomplete_counts(self, incomplete_counts: Dict[str, int],
                                 total_incomplete: int = 0):
        root = self.invisibleRootItem()
        if root.childCount() > 0:
            all_item = root.child(0)
            all_item.setData(1, self.ROLE_INCOMPLETE, int(total_incomplete or 0))
        for i in range(root.childCount()):
            self._update_incomplete_item(root.child(i), incomplete_counts or {})
        self._refresh_incomplete_visibility()

    def _update_incomplete_item(self, item: QTreeWidgetItem,
                                incomplete_counts: Dict[str, int]):
        path = item.data(0, self.ROLE_PATH)
        if path:
            key = self._norm(path).lower()
            item.setData(1, self.ROLE_INCOMPLETE, int(incomplete_counts.get(key, 0)))
        for i in range(item.childCount()):
            self._update_incomplete_item(item.child(i), incomplete_counts)

    def _refresh_incomplete_visibility(self):
        root = self.invisibleRootItem()
        for i in range(root.childCount()):
            self._refresh_incomplete_item(root.child(i))

    def _refresh_incomplete_item(self, item: QTreeWidgetItem) -> bool:
        child_visible_incomplete = False
        for i in range(item.childCount()):
            child = item.child(i)
            child_has = self._refresh_incomplete_item(child)
            if item.isExpanded() and child_has:
                child_visible_incomplete = True
        own_incomplete = int(item.data(1, self.ROLE_INCOMPLETE) or 0)
        count = item.data(1, self.ROLE_COUNT)
        if count is not None:
            shown_incomplete = 0 if child_visible_incomplete else own_incomplete
            self._set_count_cell(item, int(count), is_root=False, incomplete=shown_incomplete)
        return own_incomplete > 0 or child_visible_incomplete

    # ───────────── 선택 ─────────────
    def _on_item_changed(self, current: Optional[QTreeWidgetItem], _prev):
        if current is None:
            return
        prefix = current.data(0, self.ROLE_PATH) or ""
        self.folderSelected.emit(prefix)

    def _selected_paths(self) -> List[str]:
        out = []
        for it in self.selectedItems():
            p = it.data(0, self.ROLE_PATH) or ""
            out.append(p)
        return out

    def _on_selection_changed(self):
        paths = self._selected_paths()
        self._recompute_ancestor_paths()
        self.foldersSelected.emit(paths)

    def _recompute_ancestor_paths(self):
        """선택된 모든 항목의 상위 노드 경로를 모아 조상 집합 갱신.
        부모 체인을 직접 타므로 그룹 노드/중첩 root 도 자연히 포함된다. '전체'(빈
        path)는 경로집합에 못 담으므로 _root_is_ancestor 플래그로 따로 표시 →
        drawRow 가 전체 행에도 바를 그림. 선택 항목 자신은 풀 하이라이트라 제외."""
        selected: set = set()
        ancestors: set = set()
        root_anc = False
        for it in self.selectedItems():
            p = it.data(0, self.ROLE_PATH) or ""
            if p:
                selected.add(self._norm(p).lower())
            parent = it.parent()
            while parent is not None:
                pp = parent.data(0, self.ROLE_PATH) or ""
                if pp:
                    ancestors.add(self._norm(pp).lower())
                else:
                    root_anc = True  # 빈 path = '전체' 가상 루트
                parent = parent.parent()
        ancestors -= selected
        if ancestors != self._ancestor_paths or root_anc != self._root_is_ancestor:
            self._ancestor_paths = ancestors
            self._root_is_ancestor = root_anc
            self.viewport().update()

    def _arrow_item_at(self, pos: QPoint) -> Optional[QTreeWidgetItem]:
        """포인터 아래 항목의 화살표(브랜치) 영역이면 그 항목, 아니면 None."""
        item = self.itemAt(pos)
        if item is None or item.childCount() == 0:
            return None
        left = self.visualRect(self.indexFromItem(item, 0)).left()
        return item if left - self.indentation() <= pos.x() < left else None

    def viewportEvent(self, e):
        if e.type() == QEvent.Type.ToolTip:
            e.accept()
            return True
        if e.type() in (QEvent.Type.Leave, QEvent.Type.Wheel):
            self._hide_delayed_list_tooltip()
        return super().viewportEvent(e)

    def _tooltip_text_at(self, pos: QPoint) -> str:
        index = self.indexAt(pos)
        if not index.isValid():
            return ""
        item = self.itemFromIndex(index)
        if item is None:
            return ""
        return item.toolTip(index.column()) or item.toolTip(0) or ""

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

    def _is_reorderable_root(self, item: Optional[QTreeWidgetItem]) -> bool:
        return bool(
            item is not None
            and self._tab_type == TAB_TYPE_ALL
            and item.data(0, self.ROLE_IS_ROOT)
            and (item.data(0, self.ROLE_PATH) or "")
        )

    def _item_at_full_row(self, pos: QPoint) -> Optional[QTreeWidgetItem]:
        item = self.itemAt(pos)
        if item is not None:
            return item
        root = self.invisibleRootItem()
        for i in range(root.childCount()):
            found = self._item_at_full_row_under(root.child(i), pos)
            if found is not None:
                return found
        return None

    def _item_at_full_row_under(self, item: QTreeWidgetItem,
                                pos: QPoint) -> Optional[QTreeWidgetItem]:
        if item.isHidden():
            return None
        rect = self._full_row_rect(item)
        if rect.isValid() and rect.contains(pos):
            return item
        if item.isExpanded():
            for i in range(item.childCount()):
                found = self._item_at_full_row_under(item.child(i), pos)
                if found is not None:
                    return found
        return None

    def startDrag(self, supported_actions):
        items = self.selectedItems()
        self._root_drag_item = None
        self._root_drag_parent = None
        self._root_drag_snapshot = []
        self._root_drag_committed = False
        self._root_drag_cancelled = False
        if len(items) == 1 and self._is_reorderable_root(items[0]):
            parent = items[0].parent() or self.invisibleRootItem()
            if parent.childCount() > 1:
                self._root_drag_item = items[0]
                self._root_drag_parent = parent
                self._root_drag_snapshot = [
                    parent.child(i) for i in range(parent.childCount())
                ]
                drag = QDrag(self)
                drag.setMimeData(self.mimeData(items))
                try:
                    drag.exec(
                        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                        Qt.DropAction.CopyAction,
                    )
                finally:
                    if not self._root_drag_committed:
                        self._restore_root_drag_snapshot()
                    self._root_drag_item = None
                    self._root_drag_parent = None
                    self._root_drag_snapshot = []
                    self._root_drag_committed = False
                    self._root_drag_cancelled = False
                return
        super().startDrag(supported_actions)

    def _can_accept_root_reorder_drop(self, e) -> bool:
        return (
            e.source() is self
            and not self._root_drag_cancelled
            and self._root_drag_item is not None
            and self._root_drag_parent is not None
            and e.mimeData().hasFormat(MIME_PATHS)
        )

    def dragEnterEvent(self, e):
        if self._can_accept_root_reorder_drop(e):
            e.setDropAction(Qt.DropAction.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if not self._can_accept_root_reorder_drop(e):
            e.ignore()
            return
        insert_index = self._root_insert_index_at(e.position().toPoint())
        if insert_index is None:
            e.ignore()
            return
        self._preview_root_drag_at(insert_index)
        e.setDropAction(Qt.DropAction.MoveAction)
        e.accept()

    def dragLeaveEvent(self, e):
        if self._root_drag_item is not None and not self._root_drag_committed:
            self._restore_root_drag_snapshot()
            self._root_drag_cancelled = True
            QTimer.singleShot(0, self._cancel_active_drag)
        super().dragLeaveEvent(e)

    def _cancel_active_drag(self):
        try:
            QDrag.cancel()
        except Exception:
            pass

    def dropEvent(self, e):
        if not self._can_accept_root_reorder_drop(e):
            e.ignore()
            return
        self._root_drag_committed = True
        self.rootOrderChangeRequested.emit(self._ordered_root_paths())
        e.setDropAction(Qt.DropAction.MoveAction)
        e.accept()

    def _root_insert_index_at(self, pos: QPoint) -> Optional[int]:
        parent = self._root_drag_parent
        if parent is None:
            return None
        fallback = parent.childCount()
        for i in range(parent.childCount()):
            child = parent.child(i)
            rect = self.visualItemRect(child)
            if not rect.isValid():
                continue
            if pos.y() < rect.center().y():
                return i
            fallback = i + 1
        return fallback

    def _full_row_rect(self, item: QTreeWidgetItem) -> QRect:
        rect = self.visualItemRect(item)
        if not rect.isValid():
            return rect
        return QRect(0, rect.y(), self.viewport().width(), rect.height())

    def _preview_root_drag_at(self, insert_index: int):
        item = self._root_drag_item
        parent = self._root_drag_parent
        if item is None or parent is None:
            return
        current = parent.indexOfChild(item)
        if current < 0:
            return
        insert_index = max(0, min(insert_index, parent.childCount()))
        if insert_index == current or insert_index == current + 1:
            return
        old_rows = self._capture_root_rows(parent)
        moved = parent.takeChild(current)
        if current < insert_index:
            insert_index -= 1
        parent.insertChild(insert_index, moved)
        self.setCurrentItem(moved)
        self.scrollToItem(moved, QAbstractItemView.ScrollHint.EnsureVisible)
        self.viewport().update()
        self._animate_root_reorder(old_rows, parent)

    def _restore_root_drag_snapshot(self):
        parent = self._root_drag_parent
        if parent is None or not self._root_drag_snapshot:
            return
        old_rows = self._capture_root_rows(parent)
        for target_index, child in enumerate(self._root_drag_snapshot):
            current = parent.indexOfChild(child)
            if current < 0 or current == target_index:
                continue
            moved = parent.takeChild(current)
            parent.insertChild(target_index, moved)
        if self._root_drag_item is not None:
            self.setCurrentItem(self._root_drag_item)
            self.scrollToItem(self._root_drag_item, QAbstractItemView.ScrollHint.EnsureVisible)
        self.viewport().update()
        self._animate_root_reorder(old_rows, parent)

    def _capture_root_rows(self, parent: QTreeWidgetItem) -> Dict[int, tuple]:
        rows: Dict[int, tuple] = {}
        if hasattr(self, "_count_overlay"):
            self._count_overlay.repaint()
        for i in range(parent.childCount()):
            child = parent.child(i)
            rect = self._full_row_rect(child)
            if rect.isValid():
                rows[id(child)] = (QRect(rect), self.viewport().grab(rect))
        return rows

    def _animate_root_reorder(self, old_rows: Dict[int, tuple],
                              parent: QTreeWidgetItem):
        for i in range(parent.childCount()):
            child = parent.child(i)
            old_data = old_rows.get(id(child))
            if old_data is None:
                continue
            old, pix = old_data
            new = self._full_row_rect(child)
            if not old.isValid() or not new.isValid() or old == new or pix.isNull():
                continue
            lbl = QLabel(self.viewport())
            lbl.setPixmap(pix)
            lbl.setGeometry(old)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lbl.show()
            lbl.raise_()
            anim = QPropertyAnimation(lbl, b"geometry", self)
            anim.setDuration(140)
            anim.setStartValue(old)
            anim.setEndValue(new)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._root_drag_animations.append(anim)

            def _cleanup(widget=lbl, animation=anim):
                widget.deleteLater()
                if animation in self._root_drag_animations:
                    self._root_drag_animations.remove(animation)

            anim.finished.connect(_cleanup)
            anim.start()

    def mouseMoveEvent(self, e):
        if (
            self._full_row_press_item is not None
            and self._full_row_press_pos is not None
            and e.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = e.position().toPoint() - self._full_row_press_pos
            if delta.manhattanLength() >= QApplication.startDragDistance():
                item = self._full_row_press_item
                self._full_row_press_item = None
                self._full_row_press_pos = None
                if item is not None:
                    self.setCurrentItem(item)
                    item.setSelected(True)
                    self.startDrag(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
                    e.accept()
                    return
        pos = e.position().toPoint()
        self._schedule_delayed_list_tooltip(pos)
        hit = self._arrow_item_at(pos)
        if hit is not self._hover_arrow_item:
            self._hover_arrow_item = hit
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hide_delayed_list_tooltip()
        if self._hover_arrow_item is not None:
            self._hover_arrow_item = None
            self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._full_row_press_item = None
        self._full_row_press_pos = None
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            row_item = self._item_at_full_row(pos)
            direct_item = self.itemAt(pos)
            if row_item is not None and direct_item is None:
                self.clearSelection()
                row_item.setSelected(True)
                self.setCurrentItem(row_item)
                self._full_row_press_item = row_item
                self._full_row_press_pos = pos
                e.accept()
                return
        # 우클릭은 선택/포커스를 바꾸지 않고 컨텍스트 메뉴만 띄움 (CustomContextMenu
        # 가 right-button up 에서 처리). 기존 선택/검색 결과를 보존한 채 '이 탭에서
        # 삭제' 등을 쓰기 위함 — 좌클릭/드래그는 기존대로 동작.
        if e.button() == Qt.MouseButton.RightButton:
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._full_row_press_item = None
        self._full_row_press_pos = None
        super().mouseReleaseEvent(e)

    # ───────────── 컨텍스트 메뉴 ─────────────
    def _can_move_root(self, item: QTreeWidgetItem, direction: int) -> bool:
        parent = item.parent()
        if parent is None:
            parent = self.invisibleRootItem()
        idx = parent.indexOfChild(item)
        return idx >= 0 and 0 <= idx + direction < parent.childCount()

    def _move_root(self, item: QTreeWidgetItem, direction: int):
        parent = item.parent()
        if parent is None:
            parent = self.invisibleRootItem()
        idx = parent.indexOfChild(item)
        new_idx = idx + direction
        if idx < 0 or new_idx < 0 or new_idx >= parent.childCount():
            return
        moved = parent.takeChild(idx)
        parent.insertChild(new_idx, moved)
        self.setCurrentItem(moved)
        self.rootOrderChangeRequested.emit(self._ordered_root_paths())
        self.viewport().update()

    def _ordered_root_paths(self) -> List[str]:
        ordered: List[str] = []

        def visit(parent: QTreeWidgetItem):
            for i in range(parent.childCount()):
                child = parent.child(i)
                path = child.data(0, self.ROLE_PATH) or ""
                if child.data(0, self.ROLE_IS_ROOT) and path:
                    ordered.append(path)
                visit(child)

        visit(self.invisibleRootItem())
        return ordered

    def _on_context_menu(self, pos: QPoint):
        # 우클릭은 선택을 바꾸지 않으므로(mousePressEvent), 우클릭한 항목이 현재
        # 선택에 들어있으면 선택 전체를, 아니면 우클릭한 항목만 대상으로 한다.
        clicked = self.itemAt(pos)
        sel = self.selectedItems()
        if clicked is not None and clicked not in sel:
            items = [clicked]
        elif sel:
            items = sel
        elif clicked is not None:
            items = [clicked]
        else:
            return
        menu = self._build_context_menu(items)
        if menu is not None and not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(pos))

    def open_context_menu_for_item(self, item: QTreeWidgetItem, global_pos: QPoint):
        """고정 헤더(조상 행) 우클릭 등 — 특정 항목 대상으로 본문과 동일 메뉴."""
        if item is None:
            return
        menu = self._build_context_menu([item])
        if menu is not None and not menu.isEmpty():
            menu.exec(global_pos)

    def _build_context_menu(self, items: List[QTreeWidgetItem]) -> QMenu:
        roots = []
        regulars = []
        for it in items:
            p = it.data(0, self.ROLE_PATH) or ""
            if not p:
                continue
            if it.data(0, self.ROLE_IS_ROOT):
                roots.append(p)
            else:
                regulars.append(p)
        menu = QMenu(self)
        # '모두 접기' — 모든 계층 폴더(전체 포함)에서. 우클릭한 폴더 자신과 그 아래
        # 전부 접음(방식 A). 자식 있는 항목에만 노출.
        collapsible = [it for it in items if it.childCount() > 0]
        if collapsible:
            collapse = QAction("모두 접기", self)
            collapse.triggered.connect(
                lambda _checked=False, its=list(collapsible): self._collapse_all(its))
            menu.addAction(collapse)
            menu.addSeparator()
        all_paths = roots + regulars
        if len(all_paths) >= 2:
            rescan_many = QAction(f"선택 폴더 {len(all_paths)}개 빠른 재스캔", self)
            rescan_many.triggered.connect(
                lambda: self.rescanManyRequested.emit(all_paths))
            menu.addAction(rescan_many)
            full_rescan_many = QAction(f"선택 폴더 {len(all_paths)}개 전체 재스캔", self)
            full_rescan_many.triggered.connect(
                lambda: self.fullRescanManyRequested.emit(all_paths))
            menu.addAction(full_rescan_many)
        elif all_paths:
            p = all_paths[0]
            rescan = QAction("빠른 재스캔", self)
            rescan.triggered.connect(lambda: self.rescanRequested.emit(p))
            menu.addAction(rescan)
            full_rescan = QAction("전체 재스캔", self)
            full_rescan.triggered.connect(lambda: self.fullRescanRequested.emit(p))
            menu.addAction(full_rescan)
        # 탭별 '이 탭에서 삭제' / '즐겨찾기에서 제거' — 하위 폴더 포함 모든 항목 가능.
        # (전체 DB는 전체 탭에서 보관. 사용자/즐겨찾기 탭은 가시성 차집합이라
        # 부분 폴더 제거가 의미 있음. 추가 시 부족분만 다시 보이게 처리.)
        if all_paths and self._tab_type == TAB_TYPE_USER:
            menu.addSeparator()
            label = (f"이 탭에서 삭제 ({len(all_paths)}개)"
                     if len(all_paths) >= 2 else "이 탭에서 삭제")
            act = QAction(label, self)
            act.triggered.connect(lambda: self.removeFromTabRequested.emit(list(all_paths)))
            menu.addAction(act)
        elif all_paths and self._tab_type == TAB_TYPE_FAVORITES:
            menu.addSeparator()
            label = (f"★ 즐겨찾기에서 제거 ({len(all_paths)}개)"
                     if len(all_paths) >= 2 else "★ 즐겨찾기에서 제거")
            act = QAction(label, self)
            # 즐겨찾기 탭은 favoriteRemoved 가 favorites 자체에서 제거 (root) 또는
            # 가시성 토글(excluded). LibraryTabs 가 sender 트리 컨텍스트로 판정.
            act.triggered.connect(
                lambda _checked=False, paths=list(all_paths):
                    self.removeFromTabRequested.emit(paths)
            )
            menu.addAction(act)

        # '라이브러리 제거'/'이름 변경' 은 root(라이브러리) 한정 — DB 차원 작업.
        if roots:
            if self._tab_type == TAB_TYPE_ALL and len(roots) == 1:
                root_item = items[0]
                if root_item.data(0, self.ROLE_IS_ROOT):
                    menu.addSeparator()
                    up = QAction("위로 이동", self)
                    up.setEnabled(self._can_move_root(root_item, -1))
                    up.triggered.connect(lambda _checked=False, it=root_item: self._move_root(it, -1))
                    menu.addAction(up)
                    down = QAction("아래로 이동", self)
                    down.setEnabled(self._can_move_root(root_item, 1))
                    down.triggered.connect(lambda _checked=False, it=root_item: self._move_root(it, 1))
                    menu.addAction(down)
            menu.addSeparator()
            if len(roots) >= 2:
                remove_many = QAction(f"라이브러리 {len(roots)}개 제거", self)
                remove_many.triggered.connect(
                    lambda: self.removeRootsRequested.emit(roots))
                menu.addAction(remove_many)
            else:
                remove = QAction("라이브러리 제거", self)
                remove.triggered.connect(lambda: self.removeRootRequested.emit(roots[0]))
                menu.addAction(remove)
            purge_label = ("라이브러리 완전 제거" if len(roots) == 1
                           else f"라이브러리 {len(roots)}개 완전 제거")
            self._add_danger_action(
                menu, purge_label,
                lambda: self.purgeRootsRequested.emit(roots))
            if len(roots) == 1:
                menu.addSeparator()
                rename = QAction("이름 변경", self)
                rename.triggered.connect(lambda: self._prompt_rename(roots[0]))
                menu.addAction(rename)
        if all_paths:
            menu.addSeparator()
            bl_label = (f"블랙리스트에 추가 ({len(all_paths)}개)"
                        if len(all_paths) >= 2 else "블랙리스트에 추가")
            bl = QAction(bl_label, self)
            bl.triggered.connect(
                lambda: self.addToBlacklistRequested.emit(list(all_paths)))
            menu.addAction(bl)

            # 즐겨찾기 토글 — 즐겨찾기 탭은 위에서 이미 '제거' 노출했으므로 생략.
            # favorites set 으로 정확히 토글 판정 (ROLE_IS_FAVORITE_ITEM 만 의존하면
            # 가상 root 없는 즐겨찾기 탭에서 오판).
            if len(all_paths) == 1 and self._tab_type != TAB_TYPE_FAVORITES:
                p = all_paths[0]
                key = self._norm(p).lower()
                is_fav = (key in self._favorites_set) or any(
                    it.data(0, self.ROLE_IS_FAVORITE_ITEM) for it in items)
                if is_fav:
                    rem_fav = QAction("★ 즐겨찾기에서 제거", self)
                    rem_fav.triggered.connect(lambda: self.favoriteRemoved.emit(p))
                    menu.addAction(rem_fav)
                else:
                    add_fav = QAction("★ 즐겨찾기에 추가", self)
                    add_fav.triggered.connect(lambda: self.favoriteAdded.emit(p))
                    menu.addAction(add_fav)

        return menu

    @staticmethod
    def _add_danger_action(menu: QMenu, text: str, callback):
        """빨간색 위험 액션 — QMenu 는 액션별 글자색 QSS 미지원이라 QWidgetAction
        + QLabel 로 구현. padding/font 는 theme.py 의 QMenu::item(7px 26px, 12px)
        과 동일하게 맞춰 일반 항목과 줄 맞춤."""
        act = QWidgetAction(menu)
        lbl = QLabel(text)
        # padding-left 22: 일반 항목(QSS ::item 26px)과 렌더 캡처 픽셀 대조 결과
        # 위젯 액션은 4px 더 안쪽에 배치돼 26px 이면 어긋남 (실측 보정).
        lbl.setStyleSheet(
            "QLabel { color: #e05555; padding: 7px 26px 7px 22px; font-size: 12px;"
            " background: transparent; }"
            "QLabel:hover { background: rgba(224, 85, 85, 0.16); }"
        )

        def _on_release(_ev):
            menu.close()
            callback()

        lbl.mouseReleaseEvent = _on_release
        act.setDefaultWidget(lbl)
        menu.addAction(act)
        return act

    def _prompt_rename(self, path: str):
        items = self.selectedItems()
        if not items:
            return
        item = items[0]
        default_name = item.data(0, self.ROLE_DEFAULT_NAME) or ""
        current = item.text(0) or default_name
        new_name, ok = QInputDialog.getText(
            self, "이름 변경", f"새 이름:\n{path}", text=current
        )
        if ok:
            self.renameRequested.emit(path, new_name.strip())

    def focus_path(self, path: str):
        """특정 경로로 트리를 펼치고 포커스(선택)."""
        if not path:
            return
        target_low = self._norm(path).lower()
        
        # 1단계: 대상 노드 찾기 (재귀 traverse)
        def find_item(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            p = item.data(0, self.ROLE_PATH)
            if p and self._norm(p).lower() == target_low:
                return item
            for i in range(item.childCount()):
                res = find_item(item.child(i))
                if res:
                    return res
            return None
        
        target_item = find_item(self.invisibleRootItem())
        if not target_item:
            return
        
        # 2단계: 부모들 모두 펼치기
        curr = target_item.parent()
        while curr:
            curr.setExpanded(True)
            curr = curr.parent()
            
        # 3단계: 선택 및 스크롤
        self.setCurrentItem(target_item)
        self.scrollToItem(target_item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.horizontalScrollBar().setValue(0)

    # ───────────── row/branch paint override ─────────────
    def drawRow(self, painter: QPainter, option, index):
        """포커스 사각형 제거 — QSS outline 무시하고 native delegate 가
        파란/하얀 테두리를 그리는 것을 방지."""
        option.state &= ~QStyle.StateFlag.State_HasFocus
        option.state &= ~QStyle.StateFlag.State_Selected
        # 선택 배경을 뷰포트 폭 전체로 직접 칠함 — column 이 ResizeToContents 라 네이티브
        # /QSS 에 맡기면 펼침/접힘에 따라 행 폭(=column 폭)이 달라져 배경이 들쭉날쭉.
        item = self.itemFromIndex(index)
        dragging = (
            item is self._root_drag_item
            and not self._root_drag_committed
        )
        if item is not None and item.isSelected():
            from app.ui.theme import COLORS
            painter.fillRect(
                QRect(0, option.rect.y(), self.viewport().width(), option.rect.height()),
                QColor(COLORS.get("row_selected", "#0a1e38")),
            )
        if dragging:
            painter.fillRect(
                QRect(0, option.rect.y(), self.viewport().width(), option.rect.height()),
                QColor(78, 144, 232, 42),
            )
        super().drawRow(painter, option, index)
        if dragging:
            painter.save()
            painter.setPen(QPen(QColor(126, 181, 255, 160), 1))
            r = QRect(0, option.rect.y(), self.viewport().width() - 1, option.rect.height() - 1)
            painter.drawRect(r)
            painter.fillRect(QRect(0, option.rect.y(), 3, option.rect.height()), QColor("#7eb5ff"))
            painter.restore()
        if item is not None:
            p = item.data(0, self.ROLE_PATH) or ""
            is_anc = bool(p) and self._norm(p).lower() in self._ancestor_paths
            if is_anc or item.isSelected():
                painter.save()
                painter.fillRect(
                    QRect(option.rect.x(), option.rect.y(), 3, option.rect.height()),
                    QColor(_COUNT_COLOR),
                )
                painter.restore()

    def drawBranches(self, painter: QPainter, rect, index):
        """Qt default branch paint 우회 — Fusion/native 모두 가지 세로선
        ("l l l l") 그리는 분기가 있어 QSS 로 안 잡힘. 여기서 직접 화살표만
        그리고 line 은 그리지 않음. has-children 일 때만 ▶/▼.
        rect 가 좁아도 (root level) 화살표가 짤리지 않게 cx 위치 조정.
        """
        item = self.itemFromIndex(index)
        if item is None or item.childCount() == 0:
            return
        expanded = self.isExpanded(index)
        size = 4.5
        # rect 우측 끝에서 안쪽으로 9 들어옴 — indentation=18 기준 중앙쪽.
        cx = rect.right() - 9
        cy = rect.center().y()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        if item is self._root_drag_item and not self._root_drag_committed:
            arrow_color = "#dcecff"
        else:
            arrow_color = _ARROW_COLOR_HOVER if item is self._hover_arrow_item else _ARROW_COLOR
        painter.setBrush(QColor(arrow_color))
        if expanded:
            poly = QPolygonF([
                QPointF(cx - size, cy - size * 0.4),
                QPointF(cx + size, cy - size * 0.4),
                QPointF(cx, cy + size * 0.7),
            ])
        else:
            poly = QPolygonF([
                QPointF(cx - size * 0.5, cy - size),
                QPointF(cx + size * 0.7, cy),
                QPointF(cx - size * 0.5, cy + size),
            ])
        painter.drawPolygon(poly)
        painter.restore()
