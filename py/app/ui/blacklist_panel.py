"""블랙리스트 라이브러리 패널.

폴더 트리 하단에 고정 배치. 헤더 클릭으로 접고/펼치기 (애니메이션 없음, setVisible 토글).
펼치면 각 항목: 경로 + 사용자 설명 + 제거 버튼.
검색 query 에서 자동 제외 (Database.query 가 blacklist_paths 테이블을 읽어 prefix 제외).

검색 결과만 가리는 정책 — 인덱싱/DB 데이터는 그대로 유지.
"""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QFileDialog, QInputDialog, QMessageBox, QSizePolicy, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from app.database import Database
from app.ui.theme import COLORS


class _BlacklistRow(QWidget):
    """단일 블랙리스트 항목 — 경로(굵게) + 설명(작게) + 제거 버튼."""
    removeClicked = pyqtSignal(str)

    def __init__(self, path: str, description: str, parent=None):
        super().__init__(parent)
        self._path = path
        self.setMouseTracking(True)
        self.setObjectName("blacklistRow")

        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 6, 4)
        h.setSpacing(6)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        path_lbl = QLabel(path)
        path_lbl.setObjectName("blacklistPath")
        path_lbl.setToolTip(path)
        path_lbl.setWordWrap(False)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(path_lbl)
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setObjectName("blacklistDesc")
            desc_lbl.setWordWrap(True)
            desc_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            col.addWidget(desc_lbl)
        h.addLayout(col, 1)

        rm = QPushButton("✕")
        rm.setObjectName("icon")
        rm.setFixedWidth(22)
        rm.setFixedHeight(22)
        rm.setToolTip("블랙리스트에서 제거")
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.clicked.connect(lambda: self.removeClicked.emit(self._path))
        h.addWidget(rm, 0, Qt.AlignmentFlag.AlignTop)


class BlacklistPanel(QWidget):
    """접었다/폈다 가능한 블랙리스트 패널. 폴더 트리 하단 고정 배치용.

    헤더만 보일 때 높이 최소 (접힘 상태), 펼치면 ScrollArea 최대 220px.
    """
    changed = pyqtSignal()  # 추가/제거 발생 — 검색 갱신 트리거
    expandedChanged = pyqtSignal(bool)  # 접힘/펼침 — splitter 사이즈 조정 트리거

    DEFAULT_EXPANDED = False
    MAX_BODY_HEIGHT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setObjectName("blacklistPanel")
        self._manager = None
        self._expanded = self.DEFAULT_EXPANDED

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 상단 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("blacklistSep")
        sep.setFixedHeight(1)
        v.addWidget(sep)

        # 헤더
        hdr_wrap = QWidget()
        hdr_wrap.setObjectName("blacklistHeader")
        hdr = QHBoxLayout(hdr_wrap)
        hdr.setContentsMargins(8, 6, 6, 6)
        hdr.setSpacing(6)

        self._toggle_btn = QPushButton(self._toggle_text(0))
        self._toggle_btn.setObjectName("blacklistToggle")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setToolTip("블랙리스트 펼치기/접기")
        self._toggle_btn.clicked.connect(self._on_toggle)
        hdr.addWidget(self._toggle_btn, 1, Qt.AlignmentFlag.AlignLeft)

        self._detail_btn = QPushButton("...")
        self._detail_btn.setObjectName("icon")
        self._detail_btn.setFixedWidth(24)
        self._detail_btn.setFixedHeight(22)
        self._detail_btn.setToolTip("블랙리스트 상세 보기")
        self._detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._detail_btn.clicked.connect(self._show_detail_dialog)
        hdr.addWidget(self._detail_btn, 0)

        v.addWidget(hdr_wrap)

        # body (펼침 시만 표시)
        self._body = QScrollArea()
        self._body.setWidgetResizable(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # setMaximumHeight 제거 — splitter 가 사이즈 결정. 220px 캡이 위쪽 공백 원인.
        self._body.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._rows_container = QWidget()
        self._rows_container.setObjectName("blacklistRows")
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 2, 0, 2)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch(1)
        self._body.setWidget(self._rows_container)
        v.addWidget(self._body)

        self._empty_lbl = QLabel("등록된 블랙리스트가 없습니다.")
        self._empty_lbl.setObjectName("blacklistEmpty")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setContentsMargins(8, 12, 8, 12)
        self._rows_layout.insertWidget(0, self._empty_lbl)

        self._body.setVisible(self._expanded)

    # ─────────── public ───────────
    def set_manager(self, manager):
        """LibraryManager — db_path 로 매 호출마다 writable Database 생성."""
        self._manager = manager
        self.refresh()

    def _db(self) -> Optional[Database]:
        if self._manager is None:
            return None
        return Database(str(self._manager.db_path))

    def refresh(self):
        # rows clear (stretch 만 남김)
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.deleteLater()

        rows: List[dict] = []
        db = self._db()
        if db is not None:
            try:
                rows = db.get_blacklist_paths()
            except Exception:
                rows = []

        self._toggle_btn.setText(self._toggle_text(len(rows)))

        if not rows:
            empty = QLabel("등록된 블랙리스트가 없습니다.")
            empty.setObjectName("blacklistEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(8, 12, 8, 12)
            self._rows_layout.insertWidget(0, empty)
            return

        for i, r in enumerate(rows):
            row = _BlacklistRow(r["path"], r.get("description") or "", self._rows_container)
            row.removeClicked.connect(self._on_remove)
            self._rows_layout.insertWidget(i, row)

    def add_paths(self, paths: List[str]):
        """외부 호출 (폴더 트리 우클릭 등) — 각 path 에 대해 설명 입력 받고 등록."""
        db = self._db()
        if db is None or not paths:
            return
        existing = {r["path"].lower() for r in db.get_blacklist_paths()}
        added = 0
        for p in paths:
            norm = os.path.normpath(p).rstrip("\\/")
            if not norm:
                continue
            if norm.lower() in existing:
                continue
            desc, ok = QInputDialog.getText(
                self, "블랙리스트 추가",
                f"설명 (선택):\n{norm}",
            )
            if not ok:
                continue
            db.add_blacklist_path(norm, (desc or "").strip())
            existing.add(norm.lower())
            added += 1
        if added > 0:
            self._set_expanded(True)
            self.refresh()
            self.changed.emit()

    # ─────────── internal ───────────
    @staticmethod
    def _toggle_text(count: int) -> str:
        return f"▼ 블랙리스트  ({count})"

    def _set_expanded(self, expanded: bool):
        prev = self._expanded
        self._expanded = bool(expanded)
        self._body.setVisible(self._expanded)
        arrow = "▼" if self._expanded else "▶"
        cur = self._toggle_btn.text()
        if cur.startswith("▼") or cur.startswith("▶"):
            self._toggle_btn.setText(arrow + cur[1:])
        if prev != self._expanded:
            self.expandedChanged.emit(self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def collapsed_height(self) -> int:
        """접힘 상태 권장 높이 (sep + header). splitter 사이즈 조정용."""
        # sep(1) + header(약 34px) ≈ 36~40px
        return 40

    def _on_toggle(self):
        self._set_expanded(not self._expanded)

    def _on_add_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, "블랙리스트에 추가할 폴더 선택")
        if not folder:
            return
        self.add_paths([folder])

    def _show_detail_dialog(self):
        """블랙리스트 상세 — 라이브러리/경로/사유 테이블."""
        db = self._db()
        if db is None:
            return
        try:
            rows = db.get_blacklist_paths()
        except Exception:
            rows = []
        dlg = BlacklistStatusDialog(self, rows)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.changed.emit()

    def _on_remove(self, path: str):
        db = self._db()
        if db is None:
            return
        ret = QMessageBox.question(
            self, "블랙리스트 제거",
            f"블랙리스트에서 제거할까요?\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        db.remove_blacklist_path(path)
        self.refresh()
        self.changed.emit()


class BlacklistStatusDialog(QDialog):
    """블랙리스트 상세 — 라이브러리/경로/사유 + 제거.
    accept() 면 panel.refresh() 호출.
    """
    def __init__(self, parent: BlacklistPanel, rows: List[dict]):
        super().__init__(parent)
        self._panel = parent
        self.setWindowTitle("블랙리스트 상세")
        self.resize(720, 420)
        self._changed = False

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)

        title = QLabel(f"등록된 블랙리스트 ({len(rows)}개)")
        title.setStyleSheet(
            f"color: {COLORS['text']}; font-size: 13px; font-weight: 700;"
        )
        v.addWidget(title)

        hint = QLabel("검색 결과에서만 가려집니다 (인덱스/DB 는 유지).")
        hint.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        v.addWidget(hint)

        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["라이브러리", "경로", "사유", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 48)

        for i, r in enumerate(rows):
            p = r.get("path", "")
            desc = r.get("description") or ""
            # "라이브러리" — path 의 basename (마지막 폴더명)
            lib = os.path.basename(p.rstrip("\\/")) or p
            self.table.setItem(i, 0, QTableWidgetItem(lib))
            self.table.setItem(i, 1, QTableWidgetItem(p))
            self.table.setItem(i, 2, QTableWidgetItem(desc))
            rm = QPushButton("✕")
            rm.setObjectName("icon")
            rm.setFixedSize(36, 22)
            rm.setToolTip("이 항목 제거")
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.clicked.connect(lambda _, path=p: self._remove_row(path))
            self.table.setCellWidget(i, 3, rm)
        v.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.setObjectName("pill")
        close_btn.setFixedHeight(28)
        close_btn.clicked.connect(self._on_close)
        btns.addWidget(close_btn)
        v.addLayout(btns)

    def _remove_row(self, path: str):
        ret = QMessageBox.question(
            self, "블랙리스트 제거",
            f"블랙리스트에서 제거할까요?\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        db = self._panel._db()
        if db is None:
            return
        db.remove_blacklist_path(path)
        self._changed = True
        # 해당 행 표에서 제거
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 1)
            if it is not None and it.text() == path:
                self.table.removeRow(r)
                break

    def _on_close(self):
        if self._changed:
            self.accept()
        else:
            self.reject()
