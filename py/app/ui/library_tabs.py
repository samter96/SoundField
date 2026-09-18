"""파일 브라우저 탭 컨테이너 — 전체/즐겨찾기/사용자 탭 + 탭바 드롭.

- 시스템 탭 ('전체', '즐겨찾기') 는 항상 존재, 수정/삭제 불가.
- 사용자 탭은 라이브러리/폴더 경로 리스트(`items`)만 보관. DB 는 손 안 댐.
- 탭마다 별도 FolderTree 인스턴스 (펼침 상태 자연 보존, 탭 전환 즉시).
- FolderTree 에서 드래그한 경로를 탭바 위에 drop 하면 해당 탭 items 에 추가.
  같은 탭에 nested 경로 들어와도 FolderTree.load_folders 의 nested 로직이
  자동으로 부모/자식 정리.
"""
import html
import json
import os
import re
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    Qt, pyqtSignal, QPoint, QPointF, QModelIndex, QSize, QEvent,
    QTimer, QRectF, QVariantAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QAction, QStandardItemModel, QStandardItem, QTextDocument,
    QAbstractTextDocumentLayout, QPalette, QColor, QPainter, QPen,
    QPolygonF, QFontMetrics,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget, QToolButton,
    QInputDialog, QMessageBox, QMenu, QLineEdit, QCompleter, QListView,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication, QLabel,
)

from app.ui.folder_tree import FolderTree, MIME_PATHS
from app.ui.flow_tab_bar import FlowTabBar


# 검색어 강조 색 (categoryCount/액센트와 동일 blue).
_SEARCH_HL_COLOR = "#4E90E8"
# 결과 드랍다운 계층 들여쓰기(레벨당 px) — 좁은 창에서 이름이 안 밀리게 얕게.
_DROPDOWN_INDENT_PX = 14
# 연결선 끝과 텍스트 사이 이격(px) — 선이 글자에 딱 붙지 않게.
_DROPDOWN_TEXT_GAP = 7
# 드랍다운 트리 연결선 색(은은하게).
_DROPDOWN_GUIDE_COLOR = QColor(122, 133, 148, 130)
# 드랍다운 행 메타 역할 — 계층 depth / 막내여부 / 조상연속 플래그 / 사운드개수.
_ROLE_DEPTH = Qt.ItemDataRole.UserRole + 2
_ROLE_IS_LAST = Qt.ItemDataRole.UserRole + 3
_ROLE_CONT = Qt.ItemDataRole.UserRole + 4
_ROLE_COUNT_NUM = Qt.ItemDataRole.UserRole + 5
# 블랙리스트 사유 — 값이 있으면(빈 문자열 포함) 블랙리스트 행, None 이면 일반 행.
_ROLE_BLACKLIST = Qt.ItemDataRole.UserRole + 6
# 드랍다운 우측 사운드 개수 색(은은한 blue 액센트).
_DROPDOWN_COUNT_COLOR = QColor(78, 144, 232, 200)
# 블랙리스트 행 텍스트 색(빨강).
_DROPDOWN_BLACKLIST_COLOR = QColor("#e06c75")
_DROPDOWN_MAX_MATCHES = 500
# 제안 목록 최대 개수 — 너무 많으면 팝업/렌더 비용↑. 폴더 단위라 이 정도면 충분.
_SEARCH_MAX_RESULTS = 200


def _tokenize(name: str) -> List[str]:
    """폴더 이름을 표준 단어 구분 기호(언더바/공백/-/.등)로 쪼갠 소문자 토큰."""
    return [t for t in re.split(r"[\W_]+", name.lower()) if t]


def _tokens_match_query(tokens: List[str], query_tokens: List[str]) -> bool:
    if not query_tokens:
        return False
    qi = 0
    for token in tokens:
        if token.startswith(query_tokens[qi]):
            qi += 1
            if qi >= len(query_tokens):
                return True
    return False


def _match_ranges(text: str, query: str) -> List[tuple]:
    """표시 문자열에서 검색어가 '토큰 시작 + 접두'로 들어맞는 (시작, 길이) 구간.
    토큰 매칭과 동일 규칙 — 구분기호 직후(또는 맨 앞)에서 query 로 시작하는 곳만."""
    query_tokens = _tokenize(query or "")
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


def _highlight_html(text: str, query: str) -> str:
    ranges = _match_ranges(text, query)
    if not ranges:
        return html.escape(text)
    parts: List[str] = []
    pos = 0
    for s, length in ranges:
        parts.append(html.escape(text[pos:s]))
        parts.append(
            f'<span style="color:{_SEARCH_HL_COLOR};font-weight:bold">'
            f"{html.escape(text[s:s + length])}</span>"
        )
        pos = s + length
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class _AnimIconButton(QToolButton):
    """돋보기 토글 — 호버 색 페이드 + 눌림 스케일 바운스를 직접 그린다.
    (Qt QSS 는 상태 전환 애니메이션 미지원 → paintEvent 로 구현)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = 0.0
        self._scale = 1.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.valueChanged.connect(self._set_hover)
        self._scale_anim = QVariantAnimation(self)
        self._scale_anim.valueChanged.connect(self._set_scale)

    def _set_hover(self, v):
        self._hover = float(v); self.update()

    def _set_scale(self, v):
        self._scale = float(v); self.update()

    @staticmethod
    def _run(anim, start, end, dur, easing):
        anim.stop(); anim.setDuration(dur); anim.setEasingCurve(easing)
        anim.setStartValue(float(start)); anim.setEndValue(float(end)); anim.start()

    def enterEvent(self, e):
        self._run(self._hover_anim, self._hover, 1.0, 140, QEasingCurve.Type.OutCubic)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._run(self._hover_anim, self._hover, 0.0, 160, QEasingCurve.Type.OutCubic)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._run(self._scale_anim, self._scale, 0.84, 90, QEasingCurve.Type.OutCubic)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._run(self._scale_anim, self._scale, 1.0, 220, QEasingCurve.Type.OutBack)
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        from app.ui.theme import COLORS
        active = max(self._hover, 1.0 if self.isChecked() else 0.0)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        c = r.center()
        p.translate(c); p.scale(self._scale, self._scale); p.translate(-c)
        # 배경 — 호버/체크에 따라 채움.
        bg = QColor(COLORS.get("bg_control_hi", "#222a38"))
        bg.setAlphaF(active * (0.9 if self.isChecked() else 0.5))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg)
        p.drawRoundedRect(r, 7, 7)
        # 테두리.
        border = _lerp_color(QColor(COLORS.get("border_strong", "#2b3242")),
                             QColor(COLORS.get("accent_hover", "#5aa0ff")), active)
        p.setPen(QPen(border, 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 7, 7)
        # 아이콘.
        txt = _lerp_color(QColor(COLORS.get("text_muted", "#8a93a3")),
                          QColor(COLORS.get("accent_hover", "#5aa0ff")), active)
        p.setPen(txt); p.setFont(self.font())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self.text())
        p.end()


class _SuggestPopup(QListView):
    """검색 제안 팝업 — Shift+휠 가로 스크롤 + 행 호버 페이드(델리게이트가 그림).
    클릭 선택은 QCompleter 시그널이 슬롯에 안 닿는 경우가 있어 mouseReleaseEvent 에서
    직접 잡아 pickRequested 로 알린다."""

    pickRequested = pyqtSignal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._delegate = None

    def attach_hover(self, delegate):
        self._delegate = delegate
        delegate.set_view(self)
        self.entered.connect(delegate.set_hover)

    def leaveEvent(self, e):
        if self._delegate is not None:
            self._delegate.set_hover(QModelIndex())
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        super().mouseReleaseEvent(e)
        if idx.isValid():
            self.pickRequested.emit(idx)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            d = e.angleDelta().y()
            if d:
                hb = self.horizontalScrollBar()
                hb.setValue(hb.value() - int(d * 50 / 120))
                e.accept()
                return
        super().wheelEvent(e)


class _TriToggleButton(QToolButton):
    """검색 결과 드랍다운 토글 — 트리뷰 화살표처럼 박스 없이 삼각형만 그리고,
    호버 시 색 페이드 + 살짝 확대. 펼침이면 ▲, 닫힘이면 ▼."""

    # folder_tree 의 화살표 색과 동일 톤.
    _COL = "#727d8e"
    _COL_HOVER = "#d6deea"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = 0.0
        self._open = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_hover)

    def _set_hover(self, v):
        self._hover = float(v); self.update()

    def set_open(self, on: bool):
        on = bool(on)
        if on != self._open:
            self._open = on
            self.update()

    def enterEvent(self, e):
        self._anim.stop(); self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0); self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop(); self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0); self._anim.start()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        col = _lerp_color(QColor(self._COL), QColor(self._COL_HOVER), self._hover)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
        r = self.rect()
        cx = r.center().x() + 0.5
        cy = r.center().y() + 0.5
        s = 4.5 + 1.0 * self._hover   # 호버 시 살짝 확대
        if self._open:
            poly = QPolygonF([
                QPointF(cx - s, cy + s * 0.4),
                QPointF(cx + s, cy + s * 0.4),
                QPointF(cx, cy - s * 0.7),
            ])
        else:
            poly = QPolygonF([
                QPointF(cx - s, cy - s * 0.4),
                QPointF(cx + s, cy - s * 0.4),
                QPointF(cx, cy + s * 0.7),
            ])
        p.drawPolygon(poly)
        p.end()


class _HighlightDelegate(QStyledItemDelegate):
    """검색 제안 팝업 — 매칭된 검색어 구간을 강조해 그린다. 텍스트 엘리드 없이
    전체 폭으로 sizeHint 를 돌려줘 가로 스크롤이 끝까지 가능하게 한다.
    행 호버는 QSS 즉시 전환 대신 직접 알파 페이드로 그린다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        # 호버 대상은 행 번호가 아니라 인덱스로 추적 — 트리(QTreeView)에서는 같은 row
        # 번호가 부모마다 중복돼 row 비교가 오작동하기 때문.
        self._hover_index = QModelIndex()
        self._view = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(0.0); self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._repaint)

    def set_view(self, v):
        self._view = v

    def _repaint(self, *_):
        if self._view is not None:
            self._view.viewport().update()

    def set_hover(self, index):
        if not isinstance(index, QModelIndex):
            index = QModelIndex()
        if index == self._hover_index:
            return
        self._hover_index = QModelIndex(index)
        self._anim.stop()
        if index.isValid():
            self._anim.start()
        self._repaint()

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        text = opt.text
        # 호버는 직접 애니메이션 → QSS 즉시 호버 전환 끔.
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        # 매치(검색어 포함) 라이브러리만 동작 가능 — 나머지(조상 컨텍스트)는 dim + 비호버.
        # is_match 미설정(legacy 완성기 등)은 True 취급(기존 동작 유지).
        mflag = index.data(Qt.ItemDataRole.UserRole + 1)
        is_match = True if mflag is None else bool(mflag)
        bl_reason = index.data(_ROLE_BLACKLIST)
        blacklisted = bl_reason is not None
        opt.text = ""  # 배경/선택만 기본 스타일로 그리고 텍스트는 직접 리치 렌더.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        # 호버 페이드 오버레이 (선택 안 된 + 동작 가능한 행만 — 블랙리스트 제외).
        if index == self._hover_index and not selected and is_match and not blacklisted:
            from app.ui.theme import COLORS
            prog = self._anim.currentValue()
            prog = float(prog) if prog is not None else 1.0
            hov = QColor(COLORS.get("hover", "#1b2130"))
            hov.setAlphaF(0.85 * prog)
            painter.fillRect(opt.rect, hov)
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        if blacklisted:
            r = (bl_reason or "").strip()
            label = "블랙리스트" + (f" · {html.escape(r)}" if r else "")
            doc.setHtml(
                f'<span style="text-decoration: line-through;">{html.escape(text)}</span>'
                f'<span>　{label}</span>')
        else:
            doc.setHtml(_highlight_html(text, self.query))
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        # 계층 — depth 만큼 들여쓰고, 트리 연결선(│ ├ └)을 그려 구조를 한눈에.
        depth = int(index.data(_ROLE_DEPTH) or 0)
        base_x = opt.rect.left() + 4
        if depth > 0:
            self._paint_guides(
                painter, opt.rect, base_x, depth,
                index.data(_ROLE_CONT) or (), bool(index.data(_ROLE_IS_LAST)))
        # 다크 테마 — 문서 기본 글자색을 테마 색으로 명시(QSS 전용이라 팔레트가
        # 검정일 수 있음). 강조 span 은 인라인 색이 우선이라 그대로 유지.
        from app.ui.theme import COLORS
        ctx = QAbstractTextDocumentLayout.PaintContext()
        # 블랙리스트는 빨강, 매치는 정상 글자색, 컨텍스트(비매치)는 dim.
        if blacklisted:
            text_col = _DROPDOWN_BLACKLIST_COLOR
        elif is_match:
            text_col = QColor(COLORS.get("text", "#c4cdd8"))
        else:
            text_col = QColor("#5b636f")
        ctx.palette.setColor(QPalette.ColorRole.Text, text_col)
        painter.save()
        y = rect.top() + max(0, (rect.height() - int(doc.size().height())) // 2)
        text_x = base_x + depth * _DROPDOWN_INDENT_PX
        if depth > 0:
            text_x += _DROPDOWN_TEXT_GAP   # 연결선과 글자 사이 이격
        painter.translate(text_x, y)
        doc.documentLayout().draw(painter, ctx)
        painter.restore()
        # 우측에 사운드 개수(은은한 blue) — 메인 트리 카운트와 동일 정보.
        cnt = index.data(_ROLE_COUNT_NUM)
        if cnt:
            painter.save()
            painter.setPen(_DROPDOWN_COUNT_COLOR)
            painter.setFont(opt.font)
            painter.drawText(
                QRectF(opt.rect).adjusted(0, 0, -8, 0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{int(cnt):,}")
            painter.restore()

    def _paint_guides(self, painter, rect, base_x, depth, cont, is_last):
        """트리 연결선 — pass-through 세로선(│) + 막내 elbow(└)/중간 elbow(├)."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(_DROPDOWN_GUIDE_COLOR, 1.0))
        ind = _DROPDOWN_INDENT_PX
        top = float(rect.top())
        bot = float(rect.bottom()) + 1.0
        mid = (rect.top() + rect.bottom()) / 2.0
        # 조상 연속 세로선 — column k(0..depth-2) 는 cont[k+1] 일 때만.
        for k in range(depth - 1):
            if k + 1 < len(cont) and cont[k + 1]:
                cx = base_x + k * ind + ind / 2.0
                painter.drawLine(QPointF(cx, top), QPointF(cx, bot))
        # 자기 elbow — column depth-1.
        cxe = base_x + (depth - 1) * ind + ind / 2.0
        painter.drawLine(QPointF(cxe, top), QPointF(cxe, mid))
        if not is_last:
            painter.drawLine(QPointF(cxe, mid), QPointF(cxe, bot))
        painter.drawLine(QPointF(cxe, mid), QPointF(base_x + depth * ind, mid))
        painter.restore()

    def sizeHint(self, option, index):
        # 가볍게 — QTextDocument 생성 없이 폰트메트릭으로만(키 입력당 전 행 호출되므로).
        # 강조는 굵게여서 약간 넓어지나 +18 패딩으로 흡수.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        fm = opt.fontMetrics
        return QSize(fm.horizontalAdvance(opt.text) + 18, fm.height() + 8)


TAB_ALL = "all"
TAB_FAVORITES = "favorites"
TAB_USER = "user"

DEFAULT_TAB_NAMES = {TAB_ALL: "전체", TAB_FAVORITES: "★ 즐겨찾기"}


def _norm(p: str) -> str:
    return os.path.normpath(p).rstrip("\\/")


def _norm_key(p: str) -> str:
    return p.replace("/", "\\").lower().rstrip("\\")


def _filter_folders(all_folders: List[str], included: List[str],
                    excluded: Optional[List[str]] = None) -> List[str]:
    """included 의 하위에 속하면서 excluded 의 하위가 아닌 폴더만."""
    if not included:
        return []
    inc = [_norm_key(r) for r in included if r]
    exc = [_norm_key(r) for r in (excluded or []) if r]
    out: List[str] = []
    for f in all_folders:
        nf = _norm_key(f)
        if not any(nf == r or nf.startswith(r + "\\") for r in inc):
            continue
        if exc and any(nf == e or nf.startswith(e + "\\") for e in exc):
            continue
        out.append(f)
    return out


def _resolve_roots(included: List[str], excluded: List[str]) -> List[str]:
    """트리에 root 로 보일 항목 = included 중 자신 또는 부모가 excluded 에
    의해 가려지지 않은 것. excluded 가 included 의 root 자체와 정확히 일치하면
    그 root 도 빠짐."""
    if not included:
        return []
    exc = [_norm_key(e) for e in (excluded or []) if e]
    out: List[str] = []
    for r in included:
        nr = _norm_key(r)
        if any(nr == e or nr.startswith(e + "\\") for e in exc):
            continue
        out.append(r)
    return out


class LibraryTabs(QWidget):
    """탭바 + (+)버튼 + 탭별 FolderTree (QStackedWidget)."""

    # FolderTree 와 동일한 시그널을 외부(main_window)로 전달.
    folderSelected = pyqtSignal(str)
    foldersSelected = pyqtSignal(list)
    favoriteAdded = pyqtSignal(str)
    favoriteRemoved = pyqtSignal(str)
    rescanRequested = pyqtSignal(str)
    rescanManyRequested = pyqtSignal(list)
    fullRescanRequested = pyqtSignal(str)
    fullRescanManyRequested = pyqtSignal(list)
    removeRootRequested = pyqtSignal(str)
    removeRootsRequested = pyqtSignal(list)
    purgeRootsRequested = pyqtSignal(list)
    renameRequested = pyqtSignal(str, str)
    addToBlacklistRequested = pyqtSignal(list)
    feedbackRequested = pyqtSignal(str, str)  # (kind: add|remove|info, message) — 중앙 토스트

    # 탭 자체 변화 — config 저장 + 검색 자동 필터 갱신 trigger.
    tabsChanged = pyqtSignal()
    activeTabChanged = pyqtSignal()
    userExpandedStateChanged = pyqtSignal()
    rootOrderChangeRequested = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 탭 메타 — index 와 1:1.
        # items   = 사용자가 명시적으로 '이 탭에 추가' 한 root 경로 (TAB_USER, TAB_FAVORITES).
        # excluded = '이 탭에서 삭제' 로 가려진 하위 폴더 경로 (가시성 차집합).
        #            전체 탭은 사용 안 함. 즐겨찾기 탭은 세션 한정.
        self._tabs: List[Dict] = [
            {"name": DEFAULT_TAB_NAMES[TAB_ALL], "type": TAB_ALL, "items": [], "excluded": []},
            {"name": DEFAULT_TAB_NAMES[TAB_FAVORITES], "type": TAB_FAVORITES, "items": [], "excluded": []},
        ]
        self._trees: List[FolderTree] = []
        # 마지막 set_data 입력 — 새 탭 생성 시 즉시 트리 채우기 위해 보관.
        self._last_data: Optional[Dict] = None
        self._blacklist_paths: List = []  # [{"path","description"}] (사유 동반)
        # 라이브러리 검색 — 인덱스(폴더명 토큰)는 _dirty 일 때만(데이터/탭 변경) 재구성.
        # 키 입력마다 이 Python 인덱스에서 매칭해 상위 N개만 작은 모델에 채운다
        # (프록시 전체훑기/행마다 QTextDocument 제거 → 타이핑 렉 해소).
        self._search_completer: Optional[QCompleter] = None
        self._search_model: Optional[QStandardItemModel] = None
        self._search_delegate: Optional[_HighlightDelegate] = None
        self._search_index: List[tuple] = []  # (display, name, path, tokens)
        self._search_rows: List[str] = []     # 현재 표시 행 → 절대경로 (행 번호로 조회)
        self._search_matches: List[str] = []
        self._search_blacklisted: List[str] = []  # 검색어 매칭된 블랙리스트 경로(비활성 표시)
        self._search_match_index = -1
        self._search_node_cache: List[tuple] = []
        self._search_node_meta: Dict[str, tuple] = {}
        self._search_dirty = True
        self._selecting = False               # 선택 처리 재진입 가드
        # 결과 드랍다운(선택 아님 — 클릭 시 reveal). 화살표 순차이동의 대안으로
        # 전체 목록을 한 번에 스크롤 확인. 검색필드 우측 ▾ 버튼으로 토글.
        self._dropdown: Optional[_SuggestPopup] = None
        self._dropdown_model: Optional[QStandardItemModel] = None
        self._dropdown_delegate: Optional[_HighlightDelegate] = None
        # 모델 갱신 디바운스 — 연타 렉 완화 + 클릭 활성화 도중 모델이 비워져
        # activated 인덱스가 무효화되는 문제 방지(활성화 시 타이머 stop).
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(90)
        self._search_timer.timeout.connect(self._apply_search)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        self.tab_bar = FlowTabBar(self, max_lines=3, max_width_sample="★ 즐겨찾기")
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.pathsDropped.connect(self._on_paths_dropped)
        self.tab_bar.contextRequested.connect(self._on_tab_context)
        self.tab_bar.closeRequested.connect(self._remove_tab)  # 호버 X 버튼 → 삭제(확인 후)

        self.add_btn = QToolButton(self)
        self.add_btn.setObjectName("flowTabAddButton")
        self.add_btn.setText("+")
        self.add_btn.setToolTip("새 탭")
        self.add_btn.setAutoRaise(False)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self._prompt_new_tab)

        # 라이브러리 검색 돋보기 토글 — 켜면 아래 한 줄 검색창이 나타난다.
        # 호버/눌림 애니메이션을 위해 직접 그리는 _AnimIconButton 사용.
        self.search_btn = _AnimIconButton(self)
        # 크기는 (+)버튼과 동일하게 — 비주얼은 paintEvent 가 그리고, QSS 는 sizeHint
        # 계산용 폰트/패딩만 제공(테두리/배경은 paintEvent 가 덮음).
        self.search_btn.setObjectName("flowTabSearchButton")
        self.search_btn.setText("🔍")
        self.search_btn.setToolTip("라이브러리 검색")
        self.search_btn.setCheckable(True)
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.toggled.connect(self._toggle_search)

        # +/🔍 버튼은 탭바 우상단에 겹쳐 배치(코너 위젯). 탭바가 패널 전체 폭을 받고,
        # 첫 줄만 버튼 자리를 비우므로 버튼 아래 2·3번째 줄은 전체 폭으로 탭을 채운다.
        corner = QWidget(self)
        corner_l = QHBoxLayout(corner)
        corner_l.setContentsMargins(0, 0, 0, 0)
        corner_l.setSpacing(4)
        corner_l.addWidget(self.add_btn)
        corner_l.addWidget(self.search_btn)
        self.tab_bar.set_corner_widget(corner)
        v.addWidget(self.tab_bar)

        # 검색창 한 줄 — 기본 숨김. 입력하면 현재 활성 탭 트리의 폴더를 폴더 이름으로
        # 실시간 제안(QCompleter 팝업). 고르면 그 폴더가 트리에서 선택된다.
        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("search")
        self.search_edit.setPlaceholderText("폴더 이름으로 라이브러리 검색")
        self.search_edit.setClearButtonEnabled(True)
        # textEdited(사용자 입력만) — 프로그램(QCompleter 클릭 삽입)으로 텍스트가
        # 바뀔 땐 발생 안 함 → 클릭 도중 모델이 비워져 선택이 깨지던 문제 해결.
        self.search_edit.textEdited.connect(self._on_search_text)
        # 비우기(X 버튼/프로그램 clear)는 textChanged 로만 와서 팝업만 닫는다.
        self.search_edit.textChanged.connect(self._on_search_text_cleared)
        self.search_edit.returnPressed.connect(self._search_next)
        self.search_edit.installEventFilter(self)   # ↑/↓ 키 순차찾기
        # 결과 드랍다운 토글 — 필드 오른쪽. 화살표 순차이동 대신 전체 목록을 계층으로
        # 한 번에 스크롤 확인. 트리뷰 화살표처럼 깔끔 + 호버 애니메이션.
        self.search_dropdown_btn = _TriToggleButton(self)
        self.search_dropdown_btn.setObjectName("searchDropdownButton")
        self.search_dropdown_btn.setFixedSize(22, 24)
        self.search_dropdown_btn.setToolTip("검색 결과 목록")
        self.search_dropdown_btn.clicked.connect(self._toggle_search_dropdown)
        self.search_row = QWidget(self)
        _srow = QHBoxLayout(self.search_row)
        _srow.setContentsMargins(0, 0, 0, 0)
        _srow.setSpacing(4)
        _srow.addWidget(self.search_edit, 1)
        _srow.addWidget(self.search_dropdown_btn, 0)
        self.search_row.hide()
        v.addWidget(self.search_row)

        self.search_nav_row = QWidget(self)
        search_nav_layout = QHBoxLayout(self.search_nav_row)
        search_nav_layout.setContentsMargins(0, 0, 0, 0)
        search_nav_layout.setSpacing(4)

        self.search_count_label = QLabel("0/0", self.search_nav_row)
        self.search_count_label.setObjectName("searchCountLabel")
        self.search_count_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        search_nav_layout.addWidget(self.search_count_label, 1)

        # '하나씩 찾기' 안내 — ↑↓ 버튼 바로 왼쪽(버튼이 곧 화살표이므로 텍스트엔 화살표 X).
        self.search_seq_hint = QLabel("하나씩 찾기", self.search_nav_row)
        self.search_seq_hint.setStyleSheet("color:#6b7686;")
        self.search_seq_hint.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        search_nav_layout.addWidget(self.search_seq_hint, 0)

        self.search_prev_btn = QToolButton(self.search_nav_row)
        self.search_prev_btn.setObjectName("searchNavButton")
        self.search_prev_btn.setText("↑")
        self.search_prev_btn.setToolTip("이전 검색 결과")
        self.search_prev_btn.clicked.connect(self._search_prev)
        search_nav_layout.addWidget(self.search_prev_btn, 0)

        self.search_next_btn = QToolButton(self.search_nav_row)
        self.search_next_btn.setObjectName("searchNavButton")
        self.search_next_btn.setText("↓")
        self.search_next_btn.setToolTip("다음 검색 결과")
        self.search_next_btn.clicked.connect(self._search_next)
        search_nav_layout.addWidget(self.search_next_btn, 0)

        self.search_nav_row.hide()
        self._update_search_controls()
        v.addWidget(self.search_nav_row)

        self.stack = QStackedWidget(self)
        v.addWidget(self.stack, 1)

        # 시스템 탭 2개 트리 생성.
        for meta in self._tabs:
            self._build_tree_for(meta)
        # 탭바 라벨 동기화.
        for meta in self._tabs:
            idx = self.tab_bar.addTab(meta["name"])
            self.tab_bar.setTabClosable(idx, meta["type"] == TAB_USER)

    # ───────────── 트리 생성/시그널 ─────────────
    def _build_tree_for(self, meta: Dict) -> FolderTree:
        tree = FolderTree()
        tree.setMinimumWidth(150)
        tree.set_tab_context(meta["type"])
        tree.folderSelected.connect(self._reemit_folder_selected)
        tree.foldersSelected.connect(self._reemit_folders_selected)
        tree.favoriteAdded.connect(self.favoriteAdded.emit)
        tree.favoriteRemoved.connect(self.favoriteRemoved.emit)
        tree.removeFromTabRequested.connect(self._on_remove_from_tab_requested)
        tree.rescanRequested.connect(self.rescanRequested.emit)
        tree.rescanManyRequested.connect(self.rescanManyRequested.emit)
        tree.fullRescanRequested.connect(self.fullRescanRequested.emit)
        tree.fullRescanManyRequested.connect(self.fullRescanManyRequested.emit)
        tree.removeRootRequested.connect(self.removeRootRequested.emit)
        tree.removeRootsRequested.connect(self.removeRootsRequested.emit)
        tree.purgeRootsRequested.connect(self.purgeRootsRequested.emit)
        tree.renameRequested.connect(self.renameRequested.emit)
        tree.addToBlacklistRequested.connect(self.addToBlacklistRequested.emit)
        tree.userExpandedStateChanged.connect(self.userExpandedStateChanged.emit)
        tree.rootOrderChangeRequested.connect(self.rootOrderChangeRequested.emit)
        self._trees.append(tree)
        self.stack.addWidget(tree)
        return tree

    def _on_remove_from_tab_requested(self, paths: list):
        """'이 탭에서 삭제' / '즐겨찾기에서 제거'. 두 경우 처리:
        - 사용자 탭: items 에 정확히 있으면 items 에서 제거, 아니면 excluded 에 추가.
        - 즐겨찾기 탭: items 가 favorites 자체와 매칭 — 정확히 있으면 favoriteRemoved
          시그널로 main_window 에 위임 (영구). 아니면 excluded 에 추가 (가시성).
        """
        sender_tree = self.sender()
        try:
            idx = self._trees.index(sender_tree)
        except ValueError:
            return
        meta = self._tabs[idx]
        if meta["type"] == TAB_ALL:
            return

        if meta["type"] == TAB_FAVORITES:
            favs = list((self._last_data or {}).get("favorites", []) or [])
            favs_set = {_norm_key(p) for p in favs}
            excluded = list(meta.get("excluded", []))
            exc_set = {_norm_key(e) for e in excluded}
            changed = False
            removed = []
            for raw in paths:
                key = _norm_key(raw)
                if key in favs_set:
                    # 즐겨찾기 root 자체 — 영구 제거.
                    self.favoriteRemoved.emit(_norm(raw))
                    removed.append(_norm(raw))
                else:
                    # 즐겨찾기의 하위 폴더 — 세션 가시성 차집합.
                    if key not in exc_set:
                        excluded.append(_norm(raw))
                        exc_set.add(key)
                        removed.append(_norm(raw))
                        changed = True
            if changed:
                meta["excluded"] = excluded
                self._refresh_tree(idx, meta)
            self._emit_remove_feedback(meta["name"], removed)
            return

        # TAB_USER
        items = list(meta.get("items", []))
        items_lower = [_norm_key(p) for p in items]
        excluded = list(meta.get("excluded", []))
        exc_set = {_norm_key(e) for e in excluded}
        changed = False
        removed = []
        for raw in paths:
            key = _norm_key(raw)
            if key in items_lower:
                pos = items_lower.index(key)
                items.pop(pos); items_lower.pop(pos)
                removed.append(_norm(raw))
                changed = True
            elif key not in exc_set:
                excluded.append(_norm(raw))
                exc_set.add(key)
                removed.append(_norm(raw))
                changed = True
        if changed:
            meta["items"] = items
            meta["excluded"] = excluded
            # 증분 제거 시도 — 노드만 takeChild. 그룹 해체 필요하면 full rebuild 폴백.
            if not self._try_incremental_remove(idx, removed):
                self._refresh_tree(idx, meta)
            self.tabsChanged.emit()
        self._emit_remove_feedback(meta["name"], removed)

    def _reemit_folder_selected(self, prefix: str):
        # 활성 탭의 트리에서 온 시그널만 전달 (비활성 트리는 시그널 발생 자체가 없음).
        sender = self.sender()
        if sender is self._trees[self.stack.currentIndex()]:
            self.folderSelected.emit(prefix)

    def _reemit_folders_selected(self, prefixes: list):
        sender = self.sender()
        if sender is self._trees[self.stack.currentIndex()]:
            self.foldersSelected.emit(prefixes)

    # ───────────── 외부 API ─────────────
    def current_tree(self) -> FolderTree:
        return self._trees[self.stack.currentIndex()]

    def active_tab(self) -> Dict:
        return self._tabs[self.stack.currentIndex()]

    def select_saved(self, tab_name: str, path: str) -> bool:
        """재시작 복원 — tab_name 탭을 활성화하고 그 트리에서 path 를 선택.
        해당 노드가 아직 없으면(데이터 미로드/제거) False → 호출부가 재시도."""
        if not path:
            return False
        idx = next((i for i, m in enumerate(self._tabs)
                    if m.get("name") == tab_name), None)
        if idx is None:
            idx = self.stack.currentIndex()  # 탭 못 찾으면 현재 트리에서 시도
        elif idx != self.stack.currentIndex():
            self.tab_bar.setCurrentIndex(idx)
        return self._trees[idx].select_folder(path)

    def active_tab_prefixes(self) -> List[str]:
        """활성 탭이 사용자/즐겨찾기 탭이면 (included - excluded) root 들 반환.
        전체 탭이면 [] (검색 prefix 제한 없음). 검색 path_prefixes 가 OR 매칭이라
        excluded 는 root 자체 제외만 자연 처리 — 하위 폴더 excluded 는 검색 측에서
        반영 불가 (DB query 한계). 추후 NOT prefix 지원 시 확장."""
        meta = self.active_tab()
        if meta["type"] == TAB_USER:
            included = list(meta.get("items", []))
        elif meta["type"] == TAB_FAVORITES:
            included = list((self._last_data or {}).get("favorites", []) or [])
        else:
            return []
        return _resolve_roots(included, list(meta.get("excluded", [])))

    def set_data(self, folders: List[str], roots: List[str],
                 root_counts: Optional[Dict[str, int]] = None,
                 total_count: Optional[int] = None,
                 incomplete_counts: Optional[Dict[str, int]] = None,
                 total_incomplete: Optional[int] = None,
                 display_names: Optional[Dict[str, str]] = None,
                 favorites: Optional[List[str]] = None):
        """_on_tree_reload_done 가 호출. 각 탭에 데이터 분배."""
        self._last_data = {
            "folders": folders, "roots": roots,
            "root_counts": root_counts or {}, "total_count": total_count,
            "incomplete_counts": incomplete_counts or {},
            "total_incomplete": total_incomplete or 0,
            "display_names": display_names or {}, "favorites": favorites or [],
        }
        # 즐겨찾기 set 은 모든 탭의 토글 메뉴 라벨 정확성에 필요.
        favs_list = favorites or []
        for tree in self._trees:
            tree.set_favorites(favs_list)
        for i, meta in enumerate(self._tabs):
            self._refresh_tree(i, meta)
        # 트리 내용이 갱신됐으니 검색 캐시 무효화. 열려 있으면 지연 재빌드.
        self._search_dirty = True
        if self.search_edit.isVisible():
            QTimer.singleShot(0, self._apply_search)

    def prune_removed_roots(self, removed: List[str]) -> bool:
        """앱에서 제거된 라이브러리 경로(및 그 하위)를 모든 사용자 탭 items/excluded
        에서 제거. 제거된 root 의 상위를 가리키는 item 은 유지(자연 필터). 변경 시 True.
        실제 트리 갱신은 호출부(set_data → _refresh_tree)가 수행하므로 여기선 메타만 정리."""
        rkeys = [_norm_key(p) for p in removed if p]
        if not rkeys:
            return False

        def under(p: str) -> bool:
            k = _norm_key(p)
            return any(k == r or k.startswith(r + "\\") for r in rkeys)

        changed = False
        for meta in self._tabs:
            if meta["type"] != TAB_USER:
                continue
            items = [p for p in meta.get("items", []) if not under(p)]
            excluded = [p for p in meta.get("excluded", []) if not under(p)]
            if (len(items) != len(meta.get("items", []))
                    or len(excluded) != len(meta.get("excluded", []))):
                meta["items"] = items
                meta["excluded"] = excluded
                changed = True
        return changed

    # ───────────── 증분 갱신 (full rebuild 회피) ─────────────
    def _try_incremental_add(self, idx: int, added: List[str],
                             absorbed: List[str]) -> bool:
        """드롭으로 새로 추가된 root 들을 증분 삽입. 흡수(재부모) 발생 또는 빈
        목록이면 False (=호출자 full rebuild). FolderTree 가 그룹 경계 변동을
        만나면 그 시점에 False 를 돌려도 동일하게 폴백."""
        if absorbed or not added:
            return False
        data = self._last_data or {}
        tree = self._trees[idx]
        excluded = self._tabs[idx].get("excluded", [])
        all_folders = data.get("folders", [])
        rc = data.get("root_counts") or {}
        ic = data.get("incomplete_counts") or {}
        dn = data.get("display_names") or {}
        for path in added:
            sub = _filter_folders(all_folders, [path], excluded)
            if not tree.insert_root_subtree(path, sub, root_counts=rc,
                                            incomplete_counts=ic, display_names=dn):
                return False
        return True

    def _try_incremental_remove(self, idx: int, removed: List[str]) -> bool:
        if not removed:
            return False
        tree = self._trees[idx]
        for path in removed:
            if not tree.remove_path_node(path):
                return False
        return True

    def _refresh_tree(self, idx: int, meta: Dict):
        data = self._last_data or {}
        tree = self._trees[idx]
        common = dict(
            root_counts=data.get("root_counts"),
            incomplete_counts=data.get("incomplete_counts"),
            display_names=data.get("display_names"),
        )
        if meta["type"] == TAB_ALL:
            tree.load_folders(
                data.get("folders", []), data.get("roots", []),
                total_count=data.get("total_count"),
                total_incomplete=data.get("total_incomplete"),
                favorites=data.get("favorites"),
                show_favorites_root=False, show_all_root=True,
                **common,
            )
        else:  # TAB_FAVORITES or TAB_USER — included/excluded 모델 공통.
            if meta["type"] == TAB_FAVORITES:
                included = list(data.get("favorites") or [])
            else:
                included = list(meta.get("items", []))
            excluded = list(meta.get("excluded", []))
            sub_roots = _resolve_roots(included, excluded)
            sub_folders = _filter_folders(data.get("folders", []), included, excluded)
            tree.load_folders(
                sub_folders, sub_roots,
                show_favorites_root=False, show_all_root=False,
                **common,
            )
        tree.set_blacklist_paths(self._blacklist_paths)

    def update_counts(self, folder_counts: Optional[Dict[str, int]],
                      total_count: int,
                      incomplete_counts: Optional[Dict[str, int]] = None,
                      total_incomplete: int = 0):
        if self._last_data is not None:
            self._last_data["root_counts"] = folder_counts or {}
            self._last_data["total_count"] = total_count
            self._last_data["incomplete_counts"] = incomplete_counts or {}
            self._last_data["total_incomplete"] = total_incomplete
        for tree in self._trees:
            tree.update_counts(folder_counts, total_count, incomplete_counts, total_incomplete)

    def update_incomplete_counts(self, incomplete_counts: Dict[str, int],
                                 total_incomplete: int = 0):
        for tree in self._trees:
            tree.update_incomplete_counts(incomplete_counts, total_incomplete)

    def set_blacklist_paths(self, entries: List):
        self._blacklist_paths = list(entries or [])
        for tree in self._trees:
            tree.set_blacklist_paths(self._blacklist_paths)
        # 블랙리스트 변경 → 검색 캐시 무효화(블랙리스트 행 표시/제외 즉시 반영).
        self._search_dirty = True
        if self.search_edit.isVisible():
            QTimer.singleShot(0, self._apply_search)

    # ───────────── 탭 config persist ─────────────
    def get_user_tabs_for_save(self) -> List[Dict]:
        """사용자 탭만 저장 (시스템 탭은 항상 재생성). items 와 excluded 모두 보존."""
        return [
            {"name": m["name"], "items": list(m.get("items", [])),
             "excluded": list(m.get("excluded", []))}
            for m in self._tabs if m["type"] == TAB_USER
        ]

    def load_user_tabs(self, saved: List[Dict]):
        """저장된 사용자 탭 복원 — 시스템 탭 2개는 그대로 두고 뒤에 append."""
        if not saved:
            return
        for entry in saved:
            try:
                name = str(entry.get("name", "")).strip() or "탭"
                items = [str(p) for p in (entry.get("items") or []) if str(p).strip()]
                excluded = [str(p) for p in (entry.get("excluded") or []) if str(p).strip()]
            except Exception:
                continue
            meta = {"name": name, "type": TAB_USER, "items": items, "excluded": excluded}
            self._tabs.append(meta)
            self._build_tree_for(meta)
            idx = self.tab_bar.addTab(name)
            self.tab_bar.setTabClosable(idx, True)
        if self._last_data is not None:
            for i, m in enumerate(self._tabs):
                if m["type"] == TAB_USER:
                    self._refresh_tree(i, m)

    # ───────────── 탭 추가/이름변경/삭제 ─────────────
    def _prompt_new_tab(self):
        name, ok = QInputDialog.getText(self, "새 탭", "탭 이름:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if any(m["name"] == name for m in self._tabs):
            QMessageBox.warning(self, "중복", "같은 이름의 탭이 이미 있습니다.")
            return
        meta = {"name": name, "type": TAB_USER, "items": []}
        self._tabs.append(meta)
        self._build_tree_for(meta)
        idx = self.tab_bar.addTab(name)
        self.tab_bar.setTabClosable(idx, True)
        if self._last_data is not None:
            self._refresh_tree(len(self._tabs) - 1, meta)
        self.tab_bar.setCurrentIndex(len(self._tabs) - 1)
        self.tabsChanged.emit()

    def _on_tab_context(self, idx: int, global_pos: QPoint):
        if idx < 0 or idx >= len(self._tabs):
            return
        meta = self._tabs[idx]
        if meta["type"] != TAB_USER:
            return  # 시스템 탭 — 메뉴 없음.
        menu = QMenu(self)
        rename = QAction("이름 변경", self)
        rename.triggered.connect(lambda: self._prompt_rename_tab(idx))
        menu.addAction(rename)
        remove = QAction("탭 삭제", self)
        remove.triggered.connect(lambda: self._remove_tab(idx))
        menu.addAction(remove)
        menu.exec(global_pos)

    def _prompt_rename_tab(self, idx: int):
        meta = self._tabs[idx]
        name, ok = QInputDialog.getText(self, "이름 변경", "새 이름:", text=meta["name"])
        if not ok:
            return
        name = name.strip()
        if not name or name == meta["name"]:
            return
        if any(m["name"] == name for m in self._tabs):
            QMessageBox.warning(self, "중복", "같은 이름의 탭이 이미 있습니다.")
            return
        meta["name"] = name
        self.tab_bar.setTabText(idx, name)
        self.tabsChanged.emit()

    def _remove_tab(self, idx: int):
        meta = self._tabs[idx]
        if meta["type"] != TAB_USER:
            return
        ret = QMessageBox.question(
            self, "탭 삭제", f"'{meta['name']}' 탭을 삭제할까요?\n(라이브러리 자체는 그대로 유지됩니다)",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        tree = self._trees.pop(idx)
        self._tabs.pop(idx)
        self.stack.removeWidget(tree)
        tree.deleteLater()
        self.tab_bar.removeTab(idx)
        self.tabsChanged.emit()

    # ───────────── 드롭 처리 ─────────────
    def _on_paths_dropped(self, idx: int, paths: List[str]):
        if idx < 0 or idx >= len(self._tabs):
            return
        meta = self._tabs[idx]
        if meta["type"] == TAB_ALL:
            return  # 전체 탭은 자동, 사용자 추가 불가.

        if meta["type"] == TAB_FAVORITES:
            # 즐겨찾기 자체에 추가 — favoriteAdded 시그널로 main_window 위임.
            # 동시에 excluded 중 이 path 와 하위인 것 제거 (다시 보이게).
            favs_key = {_norm_key(p) for p in ((self._last_data or {}).get("favorites") or [])}
            excluded = list(meta.get("excluded", []))
            added, dup = [], []
            for raw in paths:
                np = _norm(raw)
                if _norm_key(np) in favs_key:
                    dup.append(np)
                    continue
                self.favoriteAdded.emit(np)
                favs_key.add(_norm_key(np))
                added.append(np)
            new_exc = self._strip_excluded(excluded, paths)
            if new_exc is not None:
                meta["excluded"] = new_exc
                # 즐겨찾기 추가 자체가 _reload_tree_async 트리거 — 거기서 set_data
                # 가 다시 _refresh_tree 호출. 별도 갱신 불필요.
            self._emit_drop_feedback(meta["name"], added, dup, [])
            return

        # TAB_USER — items 에 추가 (정규화) + excluded 차집합 갱신.
        items = list(meta.get("items", []))
        items_lower = [_norm_key(p) for p in items]
        excluded = list(meta.get("excluded", []))
        exc_lower = [_norm_key(e) for e in excluded]
        added, dup, dup_child, absorbed = [], [], [], []
        changed = False
        for raw in paths:
            np = _norm(raw); key = _norm_key(np)
            # 동일 path 가 이미 있으면 '이미 포함된 라이브러리'.
            if any(key == r for r in items_lower):
                dup.append(np)
                continue
            # 더 큰 상위 폴더가 이미 items 에 있는 경우 — 상위가 이 하위를 커버.
            if any(key.startswith(r + "\\") for r in items_lower):
                # 단, 이전에 '이 탭에서 삭제'로 excluded 된 폴더(자신 또는 하위)면
                # 이번 드롭으로 제외가 해제되어 다시 보이게 됨 → 재추가로 인식.
                if any(ek == key or ek.startswith(key + "\\") for ek in exc_lower):
                    added.append(np)   # _strip_excluded 가 제외 해제 → 상위 트리에 다시 노출
                else:
                    dup_child.append(np)
                continue
            # 새 path 가 기존 하위 items 를 포함하면 그 하위는 흡수 → items 에서 제거.
            keep, keep_lower = [], []
            for it, il in zip(items, items_lower):
                if il.startswith(key + "\\"):
                    absorbed.append(it)
                else:
                    keep.append(it); keep_lower.append(il)
            items, items_lower = keep, keep_lower
            items.append(np); items_lower.append(key)
            added.append(np)
            changed = True
        new_exc = self._strip_excluded(excluded, paths)
        if new_exc is not None:
            excluded = new_exc
            changed = True
        if changed:
            meta["items"] = items
            meta["excluded"] = excluded
            # 증분 삽입 시도 — excluded 해제(하위 재노출)/흡수 없는 단순 추가만.
            # 안 되면 그 탭 full rebuild 폴백.
            if not (new_exc is None and self._try_incremental_add(idx, added, absorbed)):
                self._refresh_tree(idx, meta)
            self.tabsChanged.emit()
        self._emit_drop_feedback(meta["name"], added, dup, absorbed, dup_child)

    @staticmethod
    def _strip_excluded(excluded: List[str], paths: List[str]) -> Optional[List[str]]:
        """추가된 경로 P 와 그 하위에 해당하는 excluded 항목 제거. 변경 있으면
        새 리스트, 없으면 None."""
        if not excluded or not paths:
            return None
        targets = [_norm_key(p) for p in paths if p]
        new_exc = []
        removed = False
        for e in excluded:
            ek = _norm_key(e)
            if any(ek == t or ek.startswith(t + "\\") for t in targets):
                removed = True; continue
            new_exc.append(e)
        return new_exc if removed else None

    # ───────────── 중앙 토스트 피드백 ─────────────
    # + / − 는 타겟 탭만, ! 는 문제 상황만 간결하게 (라이브러리명은 생략).
    def _emit_drop_feedback(self, tabname: str, added: List[str],
                            dup: List[str], absorbed: List[str],
                            dup_child: Optional[List[str]] = None):
        dup_child = dup_child or []
        if not (added or dup or absorbed or dup_child):
            return
        if added:
            self.feedbackRequested.emit("add", f"'{tabname}' 에 추가")
        elif dup_child and not dup:
            self.feedbackRequested.emit(
                "info", "이미 추가된 상위 폴더에\n포함되어 있습니다")
        else:
            self.feedbackRequested.emit("info", "이미 포함된 라이브러리입니다")

    def _emit_remove_feedback(self, tabname: str, removed: List[str]):
        if not removed:
            return
        self.feedbackRequested.emit("remove", f"'{tabname}' 에서 삭제")

    def _on_tab_changed(self, idx: int):
        if 0 <= idx < len(self._trees):
            self.stack.setCurrentIndex(idx)
            # 탭이 바뀌면 검색 범위(트리)도 바뀐다 — 캐시 무효화. 열려 있으면 지연 재빌드.
            self._search_dirty = True
            if self.search_edit.isVisible():
                QTimer.singleShot(0, self._apply_search)
            self.activeTabChanged.emit()

    # ───────────── 라이브러리 검색 (돋보기 토글) ─────────────
    def _toggle_search(self, on: bool):
        if on:
            # 열기 — 이전 검색어는 기억(지우지 않음). 텍스트 있으면 재검색해 결과/강조
            # 복원, 없으면 빈 상태. (검색어 삭제는 오직 X 버튼만.)
            self.search_row.show()
            self.search_nav_row.show()
            self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.search_edit.selectAll()
            QTimer.singleShot(0, self._ensure_search_node_cache)
            self._apply_search()   # 빈 텍스트면 내부에서 clear 처리
        else:
            # 닫기(돋보기) — 행만 숨기고 검색어/매치는 메모리에 유지. 트리의 빨강 강조만
            # 시각적으로 해제(검색 UI 안 보이므로). 다시 열면 _apply_search 가 복원.
            self.search_row.hide()
            self.search_nav_row.hide()
            self._hide_dropdown()
            self.current_tree().clear_search_highlight()
            if self._search_completer is not None:
                self._search_completer.popup().hide()

    def _ensure_search_completer_legacy(self):
        """현재 활성 탭 트리의 폴더 노드로 검색 인덱스 + (빈) 완성기 모델을 준비.
        무거운 부분(트리 전체 스캔 + 토큰화)은 여기서 _dirty 일 때만 1회. 표시는
        UnfilteredPopupCompletion + _HighlightDelegate. 행은 _on_search_text 가 매칭
        상위 N개만 채운다. DisplayRole='폴더명  경로', EditRole=폴더명, UserRole=경로."""
        if self._search_completer is not None and not self._search_dirty:
            return
        # 내용물이 많은 폴더 우선. 동률이면 얕은 깊이, 그다음 경로 알파벳순.
        # 인덱스를 미리 정렬해 두면 매칭을 앞에서부터 끊어도 결과가 원하는 순서가 된다.
        nodes = self.current_tree().iter_folder_nodes()
        nodes.sort(key=lambda np: (-int(np[2] or 0), np[1].count("\\"), np[1].lower()))
        self._search_index = [
            (f"{name}    {path}", name, path, _tokenize(name))
            for name, path, _count, _reason in nodes
        ]
        if self._search_model is None:
            model = QStandardItemModel(self)
            comp = QCompleter(model, self)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
            comp.setMaxVisibleItems(12)
            # 팝업 — 긴 이름 엘리드 끔 + 가로 스크롤(Shift+휠) + 강조/호버 델리게이트.
            popup = _SuggestPopup()
            comp.setPopup(popup)
            popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            popup.setTextElideMode(Qt.TextElideMode.ElideNone)
            popup.setWordWrap(False)
            delegate = _HighlightDelegate(popup)
            popup.setItemDelegate(delegate)
            popup.attach_hover(delegate)
            # 선택은 두 경로로 잡는다 — 마우스 클릭은 popup.clicked(타이밍 무관),
            # 키보드 Enter 는 completer.activated. 둘 다 행 번호로 경로를 조회.
            popup.pickRequested.connect(self._select_by_index)
            popup.clicked.connect(self._select_by_index)
            comp.activated[QModelIndex].connect(self._select_by_index)
            self.search_edit.setCompleter(comp)
            self._search_model = model
            self._search_completer = comp  # GC 방지 + 수명 유지
            self._search_delegate = delegate
        self._search_dirty = False
        # 인덱스가 (재)구성됐으니 현재 입력으로 즉시 목록 갱신.
        if self.search_edit.isVisible():
            self._apply_search()

    def _on_search_text_legacy(self, text: str):
        if self._search_completer is None or self._search_model is None:
            return  # 빌드 전(지연) — singleShot 빌드 후 반영.
        # 디바운스 — 연타 렉 완화.
        self._search_timer.start()

    def _on_search_text_cleared_legacy(self, text: str):
        # 텍스트가 비면(X 버튼/clear) 팝업만 닫는다. 비어있지 않은 변경(완성기 클릭
        # 삽입 등)은 무시 — 모델을 건드리지 않아 클릭 선택이 안전.
        if not text and self._search_completer is not None:
            self._search_timer.stop()
            if self._search_model is not None:
                self._search_model.setRowCount(0)
                self._search_rows = []
            self._search_completer.popup().hide()

    def _apply_search_legacy(self):
        comp = self._search_completer
        model = self._search_model
        if comp is None or model is None:
            return
        text = self.search_edit.text()
        self._search_delegate.query = text
        q = text.strip().lower()
        model.setRowCount(0)
        if len(q) >= 2 and self._search_index:
            rows = []
            for disp, name, path, tokens in self._search_index:
                if any(t.startswith(q) for t in tokens):
                    rows.append((disp, name, path))
                    if len(rows) >= _SEARCH_MAX_RESULTS:
                        break
            self._search_rows = [path for _disp, _name, path in rows]
            for disp, name, path in rows:
                it = QStandardItem()
                it.setData(disp, Qt.ItemDataRole.DisplayRole)
                it.setData(name, Qt.ItemDataRole.EditRole)
                it.setData(path, Qt.ItemDataRole.UserRole)
                it.setEditable(False)
                model.appendRow(it)
        else:
            self._search_rows = []
        if model.rowCount() > 0:
            comp.complete()
        else:
            comp.popup().hide()

    def _on_search_text(self, text: str):
        self._search_timer.start()

    def _on_search_text_cleared(self, text: str):
        if not text:
            self._search_timer.stop()
            self._clear_search_matches()
            if self._search_completer is not None:
                self._search_completer.popup().hide()

    def _clear_search_matches(self):
        self._search_matches = []
        self._search_match_index = -1
        self._update_search_controls()
        self._hide_dropdown()
        self.current_tree().clear_search_highlight()

    def _update_search_controls(self):
        total = len(self._search_matches)
        cur = self._search_match_index + 1 if total and self._search_match_index >= 0 else 0
        if hasattr(self, "search_count_label"):
            self.search_count_label.setText(f"{cur}/{total}")
        enabled = total > 0
        # 순차찾기 안내는 결과 있을 때만(↑↓ 버튼 옆).
        if hasattr(self, "search_seq_hint"):
            self.search_seq_hint.setVisible(enabled)
        if hasattr(self, "search_prev_btn"):
            self.search_prev_btn.setEnabled(enabled)
        if hasattr(self, "search_next_btn"):
            self.search_next_btn.setEnabled(enabled)

    def _search_prev(self):
        self._move_search_match(-1)

    def _search_next(self):
        self._move_search_match(1)

    def _move_search_match(self, step: int):
        if not self._search_matches:
            self._apply_search()
        if not self._search_matches:
            return
        # 순차찾기 모드로 전환 — 드랍다운을 닫고 트리에서 하나씩 reveal.
        self._hide_dropdown()
        self._search_match_index = (
            self._search_match_index + step
        ) % len(self._search_matches)
        self._select_search_match(self._search_match_index)

    def _select_search_match(self, idx: int):
        if not (0 <= idx < len(self._search_matches)):
            self._update_search_controls()
            return
        # 선택/검색범위 지정(folderSelected) 없이 트리에서 위치만 드러내고 검색어를
        # 빨강 강조 — Ctrl+F식 찾기. 실제 필터링은 사용자가 직접 클릭할 때만.
        query = self.search_edit.text().strip()
        self._selecting = True
        try:
            self.current_tree().reveal_folder(self._search_matches[idx], query)
        finally:
            self._selecting = False
        self._update_search_controls()
        if self._search_completer is not None:
            self._search_completer.popup().hide()

    # ── 결과 드랍다운 (계층형 평목록 — 클릭 시 reveal, 선택 아님) ──
    def _build_dropdown(self):
        if self._dropdown is not None:
            return
        pop = _SuggestPopup(self)
        pop.setObjectName("librarySearchDropdown")
        # Qt.Popup 대신 grab 안 하는 frameless Tool — 입력을 가로채지 않아 토글 버튼
        # 호버/애니메이션이 정상 동작하고, 바깥클릭이 트리로 새어 폴더선택→결과표
        # 리로드(프리즈)되는 것을 막는다(바깥클릭은 앱 이벤트필터가 먹고 닫음).
        pop.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        pop.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        pop.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        pop.setTextElideMode(Qt.TextElideMode.ElideNone)
        pop.setWordWrap(False)
        pop.setUniformItemSizes(True)
        pop.installEventFilter(self)   # Hide 감지 → 토글 화살표 복귀 + 앱필터 해제
        model = QStandardItemModel(self)
        pop.setModel(model)
        deleg = _HighlightDelegate(pop)
        pop.setItemDelegate(deleg)
        pop.attach_hover(deleg)
        pop.pickRequested.connect(self._dropdown_pick)
        self._dropdown = pop
        self._dropdown_model = model
        self._dropdown_delegate = deleg

    def eventFilter(self, obj, ev):
        et = ev.type()
        # 검색 입력행에서 ↑/↓ 키 → 순차찾기(드랍다운 닫고 트리에서 하나씩). 단일행
        # QLineEdit 은 ↑↓ 가 기본 동작이 없어 가로채도 안전.
        if obj is self.search_edit and et == QEvent.Type.KeyPress:
            k = ev.key()
            if k == Qt.Key.Key_Down:
                self._search_next(); return True
            if k == Qt.Key.Key_Up:
                self._search_prev(); return True
        if obj is self._dropdown and et == QEvent.Type.Hide:
            self.search_dropdown_btn.set_open(False)
            QApplication.instance().removeEventFilter(self)
            return False
        # 드랍다운이 열린 동안의 바깥 클릭 — 닫고 그 클릭은 먹는다(트리 폴더선택→
        # 결과표 리로드 프리즈 방지). 단, 검색 UI(입력행·nav행=↑↓/카운트)와 팝업 안
        # 클릭은 통과시켜 ↑↓ 하나씩 찾기 전환·▾토글·타이핑이 정상 동작하게 한다.
        if (self._dropdown is not None and self._dropdown.isVisible()
                and et == QEvent.Type.MouseButtonPress):
            gp = ev.globalPosition().toPoint()
            if self._dropdown.geometry().contains(gp):
                return False
            for w in (self.search_row, self.search_nav_row):
                if w.isVisible():
                    r = w.rect().translated(w.mapToGlobal(QPoint(0, 0)))
                    if r.contains(gp):
                        return False
            self._dropdown.hide()
            return True
        return super().eventFilter(obj, ev)

    def _show_dropdown(self):
        """매치+조상을 트리 1회 순회로 모은 평평한 계층 목록을 표시.
        분기선 없이 depth 만큼만 얕게 들여쓴다(델리게이트)."""
        self._dropdown_delegate.query = self.search_edit.text().strip()
        m = self._dropdown_model
        m.setRowCount(0)
        cur_path = (self._search_matches[self._search_match_index]
                    if 0 <= self._search_match_index < len(self._search_matches)
                    else None)
        cur_row = -1
        pop = self._dropdown
        fm = QFontMetrics(pop.font())
        content_w = 0
        display_matches = self._search_matches[:_DROPDOWN_MAX_MATCHES]
        bl_display = self._search_blacklisted[:_DROPDOWN_MAX_MATCHES]
        rows = (self._dropdown_rows_for_matches(display_matches, bl_display)
                if (display_matches or bl_display) else [])
        for r, (depth, name, path, is_match, is_last, cont, cnt, reason) in enumerate(rows):
            blacklisted = reason is not None
            it = QStandardItem()
            it.setData(name, Qt.ItemDataRole.DisplayRole)
            it.setData(path, Qt.ItemDataRole.UserRole)
            it.setData(bool(is_match), Qt.ItemDataRole.UserRole + 1)
            it.setData(depth, _ROLE_DEPTH)
            it.setData(bool(is_last), _ROLE_IS_LAST)
            it.setData(cont, _ROLE_CONT)
            # 매치 라이브러리만 개수 표시 + 선택/호버 가능. 블랙리스트/조상(컨텍스트)은 비활성.
            it.setData(int(cnt) if (is_match and not blacklisted) else 0, _ROLE_COUNT_NUM)
            if blacklisted:
                it.setData(reason, _ROLE_BLACKLIST)  # 빗금+빨강+사유, 클릭 불가
            it.setEditable(False)
            if is_match and not blacklisted:
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            else:
                it.setFlags(Qt.ItemFlag.NoItemFlags)
            m.appendRow(it)
            # 행의 실제 콘텐츠 너비(들여쓰기 + 텍스트 + 우측 개수/사유). 강조 bold 여유분 가산.
            cnt_w = (fm.horizontalAdvance(f"{cnt:,}") + 18) if (is_match and cnt and not blacklisted) else 0
            bl_w = fm.horizontalAdvance(f"  블랙리스트 · {reason or ''}") if blacklisted else 0
            w = (4 + depth * _DROPDOWN_INDENT_PX + _DROPDOWN_TEXT_GAP
                 + fm.horizontalAdvance(name) + bl_w + 28 + cnt_w)
            if w > content_w:
                content_w = w
            if path == cur_path:
                cur_row = r
        if len(self._search_matches) > len(display_matches):
            more = QStandardItem()
            more.setData(
                f"상위 {len(display_matches):,}개만 표시 중",
                Qt.ItemDataRole.DisplayRole,
            )
            more.setData(False, Qt.ItemDataRole.UserRole + 1)
            more.setData(0, _ROLE_DEPTH)
            more.setData(0, _ROLE_COUNT_NUM)
            more.setEditable(False)
            more.setFlags(Qt.ItemFlag.NoItemFlags)
            m.appendRow(more)
        if m.rowCount() == 0:
            # 결과 없음 — 안내 행 1개(비활성·dim). 드랍다운은 내려오되 선택 불가.
            ph = QStandardItem()
            ph.setData("검색 결과 없음", Qt.ItemDataRole.DisplayRole)
            ph.setData(False, Qt.ItemDataRole.UserRole + 1)
            ph.setData(0, _ROLE_DEPTH)
            ph.setData(0, _ROLE_COUNT_NUM)
            ph.setEditable(False)
            ph.setFlags(Qt.ItemFlag.NoItemFlags)
            m.appendRow(ph)
            content_w = max(content_w, self.width())
        rh = pop.sizeHintForRow(0) if m.rowCount() else 22
        rh = rh if rh > 0 else 22
        h = rh * min(max(m.rowCount(), 1), 14) + 4
        left = self.mapToGlobal(QPoint(0, 0)).x()
        # nav행(카운트+↑↓) '아래'에 붙인다 — nav행을 가리지 않으므로 드랍다운이 열린
        # 채로도 ↑↓(하나씩 찾기 전환)을 바로 누를 수 있다. nav행은 항상 표시되어
        # 위치가 정확(붕 뜸 없음) + 닫아도 레이아웃 안 흔들림.
        top = self.search_nav_row.mapToGlobal(
            QPoint(0, self.search_nav_row.height())).y()
        # 너비 — 긴 이름이 안 잘리게 콘텐츠에 맞춰 확장(최소=패널폭, 최대=화면 안전선).
        scr = self.screen() or QApplication.primaryScreen()
        safe_right = scr.availableGeometry().right() - 8 if scr else left + self.width()
        max_w = max(self.width(), safe_right - left)
        width = max(self.width(), min(content_w, max_w))
        pop.setGeometry(left, top, width, h)
        if cur_row >= 0:
            idx = m.index(cur_row, 0)
            pop.setCurrentIndex(idx)
            pop.scrollTo(idx)
        pop.show()
        self.search_dropdown_btn.set_open(True)
        # 바깥클릭 감시 — 재호출(_refresh) 시 중복 설치 방지 위해 먼저 제거.
        app = QApplication.instance()
        app.removeEventFilter(self)
        app.installEventFilter(self)

    def _toggle_search_dropdown(self):
        if self._dropdown is not None and self._dropdown.isVisible():
            self._dropdown.hide()
            return
        if not self._search_matches:
            self._apply_search()
        if not self._search_matches:
            return
        self._build_dropdown()
        self._show_dropdown()

    def _dropdown_pick(self, index: QModelIndex):
        # 검색어 포함 라이브러리(매치)만 선택/동작. 조상(컨텍스트) 행 클릭은 무시
        # (드랍다운도 닫지 않아 계속 살펴볼 수 있게).
        if not index.data(Qt.ItemDataRole.UserRole + 1):
            return
        path = index.data(Qt.ItemDataRole.UserRole)
        if self._dropdown is not None:
            self._dropdown.hide()
        if not path:
            return
        if path in self._search_matches:
            self._search_match_index = self._search_matches.index(path)
        # 드랍다운 클릭 = 명시 선택 → select=True (folderSelected 까지, legacy 선택 동작).
        self._selecting = True
        try:
            self.current_tree().reveal_folder(
                path, self.search_edit.text().strip(), select=True)
        finally:
            self._selecting = False
        self._update_search_controls()

    def _hide_dropdown(self):
        if self._dropdown is not None and self._dropdown.isVisible():
            self._dropdown.hide()

    def _ensure_search_node_cache(self) -> List[tuple]:
        if self._search_dirty or not self._search_node_cache:
            self._search_node_cache = [
                (name, path, count, reason, _tokenize(name))
                for name, path, count, reason in self.current_tree().iter_folder_nodes()
            ]
            self._search_node_meta = {
                path: (name, count, reason)
                for name, path, count, reason, _tokens in self._search_node_cache
            }
            self._search_dirty = False
        return self._search_node_cache

    def _dropdown_rows_for_matches(self, paths: List[str],
                                   blacklisted: List[str]) -> List[tuple]:
        meta = self._search_node_meta
        matched = set(paths)
        by_path: Dict[str, list] = {}
        roots: List[list] = []

        def ensure(path: str):
            if path in by_path:
                return by_path[path]
            name, count, reason = meta.get(
                path, (os.path.basename(path.rstrip("\\/")) or path, 0, None))
            node = [name, path, path in matched, int(count or 0), reason, []]
            by_path[path] = node
            parent = os.path.dirname(path.rstrip("\\/")).replace("/", "\\")
            if parent and parent != path and parent in meta:
                ensure(parent)[5].append(node)
            else:
                roots.append(node)
            return node

        for path in list(paths) + list(blacklisted):
            cur = path
            chain: List[str] = []
            while cur and cur in meta:
                chain.append(cur)
                parent = os.path.dirname(cur.rstrip("\\/")).replace("/", "\\")
                if not parent or parent == cur:
                    break
                cur = parent
            for p in reversed(chain):
                ensure(p)

        out: List[tuple] = []

        def flatten(nodes: List[list], cont: List[bool]):
            depth = len(cont)
            last_i = len(nodes) - 1
            for i, (name, path, is_match, cnt, reason, kids) in enumerate(nodes):
                is_last = i == last_i
                out.append((depth, name, path, is_match, is_last, tuple(cont), cnt, reason))
                flatten(kids, cont + [not is_last])

        flatten(roots, [])
        return out

    def _apply_search(self):
        q = self.search_edit.text().strip().lower()
        if not q:
            self._clear_search_matches()
            return
        query_tokens = _tokenize(q)
        # 토큰 접두 매칭 — 폴더명을 구분자로 쪼갠 '단어 시작'에서 검색어로 시작하는
        # 것만. 단어 중간 매칭 제외("ui"≠eq[ui]pment), 접두라 부족분 자동 포함
        # ("equip"→equipment, "click"→clicked). 강조(_token_prefix_ranges)와 동일 규칙.
        matches: List[str] = []
        blacklisted: List[str] = []
        for _name, path, _count, reason, tokens in self._ensure_search_node_cache():
            if _tokens_match_query(tokens, query_tokens):
                (blacklisted if reason is not None else matches).append(path)
        self._search_matches = matches
        self._search_blacklisted = blacklisted
        self._search_match_index = 0 if matches else -1
        self._update_search_controls()
        if not matches:
            # 검색어는 있는데 결과 0 → 드랍다운에 '검색 결과 없음' 표시(숨기지 않음).
            self.current_tree().clear_search_highlight()
            if self._search_completer is not None:
                self._search_completer.popup().hide()
        # 기본 동작 = 드랍다운(계층 목록) 자동 표시. 결과 0이면 안내 행만. 순차찾기는 ↑↓.
        self._build_dropdown()
        self._show_dropdown()

    def _select_by_index(self, index: QModelIndex):
        """검색 제안 선택 → 해당 폴더를 트리에서 선택. completionModel 행번호는
        소스 모델 행과 항상 1:1이 아니므로 mapToSource 로 정확히 경로를 가져온다
        (일부 라이브러리만 선택 안 되던 원인)."""
        if self._selecting or index is None:
            return
        self._search_timer.stop()  # 대기 중 재빌드 취소 → 모델/인덱스 일관 유지.
        path = self._index_path(index)
        if path:
            self._selecting = True
            try:
                self.current_tree().select_folder(path)
            finally:
                self._selecting = False
        # 선택 완료 → 검색창 닫음 (토글 off 가 정리/숨김 처리).
        self.search_btn.setChecked(False)

    def _index_path(self, index: QModelIndex) -> Optional[str]:
        """제안 팝업 인덱스(completionModel) → 소스 모델 경로. 매핑 우선, 그다음
        직접 data, 마지막으로 행 번호 폴백."""
        comp = self._search_completer
        if comp is not None and self._search_model is not None:
            try:
                si = comp.completionModel().mapToSource(index)
                if si.isValid():
                    p = self._search_model.data(si, Qt.ItemDataRole.UserRole)
                    if p:
                        return p
            except Exception:
                pass
        p = index.data(Qt.ItemDataRole.UserRole)
        if p:
            return p
        r = index.row()
        if 0 <= r < len(self._search_rows):
            return self._search_rows[r]
        return None
