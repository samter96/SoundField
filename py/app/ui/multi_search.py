from typing import List, Dict

from PyQt6.QtCore import Qt, QEvent, QTimer, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QToolTip,
)

from app.database import Database
from app.ui.anim import block_value_wheel
from app.ui.anim_button import AnimButton
from app.ui.theme import COLORS


class _PreciseToggle(QPushButton):
    """정확한 검색 토글 — 과녁(bullseye) 아이콘. ON: 단어색인(정밀), OFF: 글자조각(넓게)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(
            "정확한 검색\n"
            "\n"
            "단어를 끝까지 입력해야 매칭됩니다. 일부만 치면 안 나옵니다.\n"
            "단어 단위라 결과가 더 정확합니다.\n"
            "\n"
            "예) 'door' → door · doors 함께 나옴\n"
            "    'doo' (일부만) → 결과 없음\n"
            "\n"
            "끄면(넓게 찾기): 글자 조각으로 찾아 'doo'만 쳐도 나옵니다."
        )

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        r = self.rect()
        accent = QColor(COLORS.get("accent", "#5dd0d8"))
        col = QColor("#e05252") if on else QColor(COLORS.get("text_secondary", "#8a8f98"))
        if on:
            bg = QColor(accent); bg.setAlpha(40)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(bg)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 5, 5)
        elif self.underMouse():
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255, 255, 255, 18))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 5, 5)
        cx, cy = r.center().x() + 0.5, r.center().y() + 0.5
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rad in (8.5, 5.0):
            p.setPen(QPen(col, 1.6))
            p.drawEllipse(QPointF(cx, cy), rad, rad)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), 1.9, 1.9)


ANY_FIELD = "any"


_SEARCH_HELP_TEXT = (
    "필터 단축키\n"
    "  • Tab/Enter : 필터 추가\n"
    "  • 빈 검색창 Backspace : 위 필터로 이동 + 제거\n"
    "  • 최소 1개 필터는 유지\n\n"
    "검색 문법\n\n"
    "기본\n"
    "  • 공백 = AND (모두 매칭)\n"
    "  • 예: dark magic → 두 단어 모두 어딘가 있는 결과\n\n"
    "연산자 (대문자만 인식)\n"
    "  • AND : 둘 다 매칭          예) dark AND magic\n"
    "  • OR  : 둘 중 하나          예) sword OR knife\n"
    "  • NOT : 제외                예) footstep NOT rain\n\n"
    "그룹화\n"
    "  • 괄호로 우선순위 지정\n"
    "  • 예) (sword OR knife) AND fight\n\n"
    "따옴표 phrase\n"
    "  • \"dark magic\" → 그 순서로 붙은 결과만\n"
    "  • 단어 1개에는 따옴표 의미 없음\n\n"
    "비고\n"
    "  • 1-2글자 짧은 토큰은 매칭 잘 안 됨 (3글자 이상 권장)\n"
    "  • 위 문법은 “전체” 필드 검색에서만 적용"
)


class _HelpButton(QPushButton):
    """검색 문법 도움말 버튼 — hover 후 빠르게 QToolTip.showText 로 표시.
    Qt 기본 hover delay + Windows 페이드 인 애니메이션 회피용 커스텀 처리.
    전역 툴팁(100ms)과 동일 체감이 되도록 지연 일치."""

    def __init__(self, parent=None):
        super().__init__("?", parent)
        self.setObjectName("searchHelp")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(24, 24)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tip_text = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._show_tip)

    def setHelpText(self, text: str):
        self._tip_text = text

    def enterEvent(self, e):
        self._timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._timer.stop()
        QToolTip.hideText()
        super().leaveEvent(e)

    def _show_tip(self):
        if not (self.underMouse() and self._tip_text):
            return
        pos = self.mapToGlobal(self.rect().bottomLeft())
        QToolTip.showText(pos, self._tip_text, self)


FIELD_LABELS = {
    ANY_FIELD: "전체",
    "file_name": "파일명",
    "file_path": "경로",
    "title": "제목",
    "artist": "아티스트",
    "album": "앨범",
    "genre": "장르",
    "comments": "코멘트",
    "description": "설명",
    "keywords": "키워드",
    "category": "카테고리",
    "sub_category": "서브카테고리",
    "source": "출처",
}


_FIELD_WIDTH = 120
_OPERATOR_WIDTH = 64
_CHILD_INDENT = _FIELD_WIDTH - _OPERATOR_WIDTH  # 56 — 자식 행 좌측 들여쓰기 폭


class MatcherRow(QWidget):
    changed = pyqtSignal()
    removeRequested = pyqtSignal(object)

    def __init__(self, default_field: str = ANY_FIELD, operator: str = None):
        super().__init__()
        self.setFixedHeight(28)  # 행 높이를 버튼과 동일하게 28px로 고정
        h = QHBoxLayout(self)
        h.setSpacing(4)  # 가로 간격은 약간 여유 있게 4px로 복구

        if operator is None:
            # 루트 행 — 필드 콤보가 검색 전체의 컬럼 범위를 결정
            h.setContentsMargins(0, 0, 0, 0)
            self.operator_combo = None

            self.field = QComboBox()
            self.field.addItem(FIELD_LABELS[ANY_FIELD], ANY_FIELD)
            for key in Database.SEARCHABLE_FIELDS:
                self.field.addItem(FIELD_LABELS.get(key, key), key)
            idx = self.field.findData(default_field)
            if idx >= 0:
                self.field.setCurrentIndex(idx)
            self.field.setFixedWidth(_FIELD_WIDTH)
            block_value_wheel(self.field)   # 휠로 검색 필드 실수 변경 방지
            h.addWidget(self.field)
            self.field.currentIndexChanged.connect(self.changed)
        else:
            # 자식 행 — 좌측 _CHILD_INDENT 들여쓰기 + 연산자 콤보. 필드 콤보 없음
            # (루트 필드를 그대로 사용). lineedit 좌측 정렬은 루트와 동일.
            h.setContentsMargins(_CHILD_INDENT, 0, 0, 0)
            self.field = None

            self.operator_combo = QComboBox()
            self.operator_combo.setObjectName("matcherOp")
            for op in ("AND", "OR", "NOT"):
                self.operator_combo.addItem(op)
            idx = self.operator_combo.findText(operator)
            if idx >= 0:
                self.operator_combo.setCurrentIndex(idx)
            self.operator_combo.setFixedWidth(_OPERATOR_WIDTH)
            block_value_wheel(self.operator_combo)   # 휠로 연산자 실수 변경 방지
            self.operator_combo.currentIndexChanged.connect(self.changed)
            h.addWidget(self.operator_combo)

        self.value = QLineEdit()
        self.value.setObjectName("search")
        self.value.setPlaceholderText("검색어 입력...")
        h.addWidget(self.value, 1)

        self.remove_btn = AnimButton(
            "", accent="text_secondary", glyph="x", glyph_color="text_secondary")
        self.remove_btn.setFixedSize(28, 28)
        self.remove_btn.setToolTip("이 조건 제거")
        self.remove_btn.clicked.connect(lambda: self.removeRequested.emit(self))
        h.addWidget(self.remove_btn)

        self.value.textChanged.connect(self.changed)
        self.value.returnPressed.connect(self.changed)

    def operator(self):
        """루트 행: None / 자식 행: 'AND'/'OR'/'NOT'."""
        if self.operator_combo is None:
            return None
        return self.operator_combo.currentText()

    def matcher(self) -> Dict:
        # 자식 행의 field 는 None — MultiSearch.matchers() 가 루트 field 로 전파.
        field = self.field.currentData() if self.field is not None else None
        return {
            "field": field,
            "value": self.value.text(),
            "operator": self.operator(),
        }


class MultiSearch(QWidget):
    MAX_ROWS = 6  # 검색어 필터 최대 개수 — 끝없이 늘어나는 것 방지

    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self._rows: List[MatcherRow] = []

        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(5)

        # 키보드 단축 안내 + 검색 문법 도움말 ? — hint_label 텍스트 바로 옆에 ? 부착,
        # 나머지 우측은 stretch 로 비움 (? 가 텍스트 끝에 자연스럽게 붙음).
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 5, 0, 0)
        hint_row.setSpacing(2)  # ? 버튼을 안내 텍스트에 바짝 붙임

        self.hint_label = QLabel(
            "Tab/Enter 필터 추가 · Backspace 이동/제거"
        )
        self.hint_label.setObjectName("metaLabel")
        # #metaLabel 의 오른쪽 8px 패딩만 제거 — ? 버튼을 텍스트에 바짝 붙임.
        self.hint_label.setStyleSheet("QLabel#metaLabel { padding-right: 0px; }")
        self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hint_row.addWidget(self.hint_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.help_btn = _HelpButton()
        self.help_btn.setHelpText(_SEARCH_HELP_TEXT)
        hint_row.addWidget(self.help_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        hint_row.addStretch(1)
        self._v.addLayout(hint_row)

        self._root_glow_gap = QWidget()
        self._root_glow_gap.setFixedHeight(4)
        self._v.addWidget(self._root_glow_gap)

        # + 필터 추가 버튼 — AND/OR/NOT 3개 분리. 각 버튼 누르면 해당 연산자의
        # 자식 행이 추가됨 (들여쓰기된 트리 형태).
        add_row_layout = QHBoxLayout()
        add_row_layout.setContentsMargins(0, 0, 0, 0)
        add_row_layout.setSpacing(6)
        self._add_btns = {}
        for op_name in ("AND", "OR", "NOT"):
            btn = AnimButton(op_name, glyph="plus")
            btn.setFixedWidth(82)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda _checked=False, o=op_name: self._add_with_focus(o))
            add_row_layout.addWidget(btn)
            self._add_btns[op_name] = btn
        # 정확한 검색 토글 (과녁) — AND/OR/NOT 옆. ON 시 단어색인(정밀) 검색.
        self.precise_btn = _PreciseToggle()
        self.precise_btn.toggled.connect(lambda _on: self.precise_btn.update())
        self.precise_btn.toggled.connect(self.changed)
        add_row_layout.addSpacing(4)
        add_row_layout.addWidget(self.precise_btn)
        add_row_layout.addStretch(1)
        # 검색어 전체 삭제 — +필터 추가의 반대편(우측). per-item ✕(작고 조용함)와
        # 구분되는 danger 라벨 버튼(외곽선+호버, AnimButton) → 시인성↑.
        self.clear_all_btn = AnimButton("검색어 비우기", accent="#c44848", glyph="x")
        self.clear_all_btn.setFixedHeight(28)
        self.clear_all_btn.setToolTip("검색어 전체 삭제 (모든 입력 행 비우기)")
        self.clear_all_btn.clicked.connect(self.clear_all)
        add_row_layout.addWidget(self.clear_all_btn)
        self._v.addLayout(add_row_layout)

        # 첫 행 = 루트 (operator=None)
        self.add_row(ANY_FIELD)

    def _add_with_focus(self, operator: str):
        row = self.add_row(ANY_FIELD, operator=operator)
        if row is not None:
            row.value.setFocus()

    def add_row(self, field: str = ANY_FIELD, operator: str = None):
        # MAX_ROWS 도달 시 추가 없이 마지막 행 반환 (caller 가 focus 만 옮길 수 있게)
        if len(self._rows) >= self.MAX_ROWS:
            self._set_add_enabled(False)
            return self._rows[-1] if self._rows else None
        # 첫 행은 항상 루트 — operator=None 강제
        if not self._rows:
            operator = None
        row = MatcherRow(field, operator=operator)
        row.changed.connect(self.changed)
        row.removeRequested.connect(self._remove_row)
        row.value.installEventFilter(self)
        self._rows.append(row)
        self._v.insertWidget(self._v.count() - 1, row)
        if len(self._rows) >= self.MAX_ROWS:
            self._set_add_enabled(False)
        self.changed.emit()
        return row

    def clear_all(self):
        """검색어 입력 행을 몇 개든 한 번에 비움 — 단일 빈 루트로 재구성.
        add_row 가 changed 를 emit 하므로 검색이 자동 갱신된다."""
        for r in list(self._rows):
            r.setParent(None)
            r.deleteLater()
        self._rows.clear()
        self._set_add_enabled(True)
        self.add_row(ANY_FIELD)

    def _set_add_enabled(self, enabled: bool):
        for btn in self._add_btns.values():
            btn.setEnabled(enabled)

    def _remove_row(self, row: MatcherRow):
        if len(self._rows) <= 1:
            row.value.clear()
            return
        if row is self._rows[0]:
            # 루트는 제거 안 함 — 검색 시작점 유지 (값만 비움).
            row.value.clear()
            return
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        if len(self._rows) < self.MAX_ROWS:
            self._set_add_enabled(True)
        self.changed.emit()

    def matchers(self) -> List[Dict]:
        out = [r.matcher() for r in self._rows]
        if out:
            # 루트 행의 필드를 자식 행들에 전파 — 자식 행은 자체 필드 콤보가 없음.
            root_field = out[0].get("field") or ANY_FIELD
            for m in out[1:]:
                if not m.get("field"):
                    m["field"] = root_field
        return out

    def get_state(self) -> List[Dict]:
        """현재 검색어 행들을 직렬화 — 재시작 후 복원용. matcher() 와 동일 형식."""
        return [r.matcher() for r in self._rows]

    def is_precise(self) -> bool:
        """정확한 검색(단어색인) 토글 상태."""
        return self.precise_btn.isChecked()

    def set_state(self, rows: List[Dict]):
        """저장된 검색어 행들을 복원. 기존 행을 모두 제거하고 재구성한다.
        루트(첫 행)는 operator=None 강제, 나머지는 저장된 연산자 사용."""
        if not isinstance(rows, list) or not rows:
            return
        for r in list(self._rows):
            r.setParent(None)
            r.deleteLater()
        self._rows.clear()
        self._set_add_enabled(True)
        for i, item in enumerate(rows[:self.MAX_ROWS]):
            if not isinstance(item, dict):
                continue
            operator = None if i == 0 else (item.get("operator") or "AND")
            field = item.get("field") or ANY_FIELD
            row = self.add_row(field, operator=operator)
            if row is not None:
                row.value.setText(item.get("value") or "")
        # 복원 결과가 비어 있으면 기본 루트 행 보장 (최소 1개 유지 불변).
        if not self._rows:
            self.add_row(ANY_FIELD)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if (isinstance(obj, QLineEdit)
                    and event.button() == Qt.MouseButton.LeftButton
                    and obj.text()
                    and obj.selectedText() != obj.text()):
                obj.setFocus(Qt.FocusReason.MouseFocusReason)
                obj.selectAll()
                return True

        if event.type() == QEvent.Type.KeyPress:
            # Tab/Enter: 다음 검색어 입력으로 (없으면 새 행 추가). Shift+Tab은 기본 동작 유지.
            if (event.key() == Qt.Key.Key_Tab
                    and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)) \
                    or event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                for i, row in enumerate(self._rows):
                    if obj is row.value:
                        if i + 1 < len(self._rows):
                            self._rows[i + 1].value.setFocus()
                        else:
                            new_row = self.add_row(ANY_FIELD, operator="AND")
                            if new_row is not None:
                                new_row.value.setFocus()
                        return True
            # Backspace: 빈 필드면 현재 행 삭제, 이전 행으로 포커스 이동 (최소 1개 유지)
            elif event.key() == Qt.Key.Key_Backspace:
                for i, row in enumerate(self._rows):
                    if obj is row.value and row.value.text() == "":
                        # 행이 2개 이상이고 현재 행이 첫 번째가 아닐 때만 삭제
                        if len(self._rows) > 1 and i > 0:
                            prev_row = self._rows[i - 1]
                            prev_row.value.setFocus()
                            prev_row.value.setCursorPosition(len(prev_row.value.text()))
                            self._remove_row(row)
                            return True
        return super().eventFilter(obj, event)
