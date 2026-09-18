"""Python ↔ JavaScript 브리지.

HTML 디자인 프로토타입(`app/web/`) 안에서 JS 가 호출하는 메서드/시그널을
파이썬 백엔드(DB, 인덱싱, 파형 추출, 재생)에 연결한다.

QWebChannel 로 `bridge` 객체를 JS 전역에 노출:
  await window.bridge.search(JSON.stringify({matchers:[...], min_dur:0, ...}))
  bridge.indexProgress.connect(p => ...)
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from app.database import Database
from app.library_manager import IndexProgress, LibraryManager


def _fmt_time(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


class _IndexWorker(QObject):
    progress = pyqtSignal(object)
    phase1_done = pyqtSignal()
    finished = pyqtSignal(dict)

    def __init__(self, manager: LibraryManager, scan_path: Optional[str] = None):
        super().__init__()
        self.manager = manager
        self.scan_path = scan_path

    def run(self):
        result = self.manager.update_index(
            scan_path=self.scan_path,
            progress_cb=lambda p: self.progress.emit(p),
            phase1_done_cb=lambda: self.phase1_done.emit(),
        )
        self.finished.emit(result)

logger = logging.getLogger(__name__)


class AppBridge(QObject):
    # JS 쪽에서 connect 할 시그널들
    indexProgress = pyqtSignal(str)     # JSON: { phase, scanned, total, percent, eta_sec, message }
    indexFinished = pyqtSignal(str)     # JSON: { message, ok }
    statusChanged = pyqtSignal(str)
    libraryChanged = pyqtSignal(str)    # JSON: 등록된 라이브러리 목록 변화
    peaksReady = pyqtSignal(str, str)   # path, JSON peaks+segments (base64 or json)

    def __init__(self, manager: LibraryManager, db: Database):
        super().__init__()
        self.manager = manager
        self.db = db
        self._index_worker: Optional[_IndexWorker] = None
        self._index_thread: Optional[QThread] = None
        self._index_cancelling = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_progress)

    # ─────────── 검색 ───────────
    @pyqtSlot(str, result=str)
    def search(self, params_json: str) -> str:
        try:
            params = json.loads(params_json)
            rows = self.db.query(
                matchers=params.get("matchers") or [],
                min_duration=float(params.get("min_duration") or 0),
                max_duration=(float(params["max_duration"])
                              if params.get("max_duration") else None),
                sample_rate=(int(params["sample_rate"])
                             if params.get("sample_rate") else None),
                channels=(int(params["channels"])
                          if params.get("channels") else None),
                path_prefix=params.get("path_prefix") or None,
                limit=int(params.get("limit") or 500),
            )
            # jsx 가 기대하는 필드명으로 정규화 (mock SF_DATA.FILES 형식)
            out = []
            for r in rows:
                fn = r.get("file_name") or ""
                fp = r.get("file_path") or ""
                ext = (fn.rsplit(".", 1)[-1] if "." in fn else "").lower()
                fp_fwd = fp.replace("\\", "/")
                folder = fp_fwd.rsplit("/", 1)[0] if "/" in fp_fwd else ""
                size_bytes = int(r.get("file_size") or 0)
                out.append({
                    "id": fp,                                      # path 가 unique key
                    "file_name": fn,
                    "file_path": fp,
                    "folder": folder,
                    "duration_sec": float(r.get("duration") or 0),
                    "sample_rate": int(r.get("sample_rate") or 0),
                    "channels": int(r.get("channels") or 0),
                    "bit_depth": int(r.get("bit_depth") or 0),
                    "codec": r.get("codec") or ext.upper(),
                    "format": ext,
                    "file_size": size_bytes,
                    "size_mb": round(size_bytes / (1024 * 1024), 1),
                    "title": r.get("title") or "",
                    "artist": r.get("artist") or "",
                    "album": r.get("album") or "",
                    "genre": r.get("genre") or "",
                    "description": r.get("description") or "",
                    "keywords": r.get("keywords") or "",
                    "comments": r.get("comments") or "",
                    "category": r.get("category") or "",
                    "sub_category": r.get("sub_category") or "",
                    "bitrate": int(r.get("bitrate") or 0),
                })
            return json.dumps({"ok": True, "rows": out})
        except Exception as e:
            logger.exception("search failed")
            return json.dumps({"ok": False, "error": str(e)})

    # ─────────── 폴더 트리 (DB 의 file_path 들에서 빌드) ───────────
    @pyqtSlot(result=str)
    def buildTree(self) -> str:
        """
        SF_DATA.TREE 형식과 동일한 트리 구조 반환:
          { path, name, count, children: [...] }
        """
        try:
            folders = self.db.get_folders()
            roots = self.manager.get_roots()
            root_paths = [r["path"].rstrip("\\/") for r in roots]

            # 가상 루트
            tree = {"path": "Y:/[Library]", "name": "[Library]", "count": 0, "children": []}
            # 경로 → node 매핑
            cache = {tree["path"]: tree}

            def _norm(p: str) -> str:
                return p.replace("\\", "/").rstrip("/")

            for root in root_paths:
                rn = _norm(root)
                node = {"path": rn, "name": os.path.basename(rn) or rn,
                        "count": 0, "children": [], "isRoot": True}
                tree["children"].append(node)
                cache[rn] = node

            for folder in folders:
                fn = _norm(folder)
                parts = fn.split("/")
                cur_path = ""
                parent = tree
                for i, part in enumerate(parts):
                    cur_path = part if i == 0 else cur_path + "/" + part
                    if cur_path in cache:
                        parent = cache[cur_path]
                        continue
                    # cur_path 가 등록된 라이브러리 root 의 하위인지 확인
                    in_root = any(cur_path == r or cur_path.startswith(r + "/") for r in root_paths)
                    if not in_root:
                        # 라이브러리 등록 외 경로는 무시
                        break
                    node = {"path": cur_path, "name": part, "count": 0, "children": []}
                    parent["children"].append(node)
                    cache[cur_path] = node
                    parent = node

            # count 채우기 (DB count_files_under)
            for node_path, node in cache.items():
                if node_path == tree["path"]:
                    continue
                prefix = os.path.normpath(node_path).rstrip("\\/") + os.sep
                try:
                    node["count"] = int(self.db.count_files_under(prefix))
                except Exception:
                    node["count"] = 0
            tree["count"] = sum(c.get("count", 0) for c in tree["children"])
            return json.dumps(tree)
        except Exception as e:
            logger.exception("buildTree failed")
            return json.dumps({"path": "Y:/[Library]", "name": "[Library]",
                               "count": 0, "children": [], "error": str(e)})

    # ─────────── 라이브러리 ───────────
    @pyqtSlot(result=str)
    def listLibraries(self) -> str:
        roots = self.manager.get_roots()
        out = []
        for root in roots:
            p = root["path"]
            prefix = os.path.normpath(p).rstrip("\\/") + os.sep
            out.append({
                "path": p,
                "name": os.path.basename(p.rstrip("\\/")) or p,
                "exists": Path(p).exists(),
                "count": self.db.count_files_under(prefix),
                "last_indexed_at": root.get("last_indexed_at"),
            })
        return json.dumps(out)

    @pyqtSlot(str)
    def addLibrary(self, path: str):
        if not path or not Path(path).exists():
            return
        self.manager.add_root(path)
        self.libraryChanged.emit(self.listLibraries())

    @pyqtSlot(result=str)
    def pickAndAddLibrary(self) -> str:
        """폴더 선택 다이얼로그 → add_root → 자동 인덱싱 시작.
        선택한 경로(또는 빈 문자열) 반환."""
        from PyQt6.QtWidgets import QFileDialog
        p = QFileDialog.getExistingDirectory(None, "라이브러리 폴더 선택")
        if not p or not Path(p).exists():
            return ""
        self.manager.add_root(p)
        self.libraryChanged.emit(self.listLibraries())
        # 자동 스캔 (PyQt 버전은 confirm 후 시작, 여기선 바로 시작)
        self.startIndex(p)
        return p

    @pyqtSlot(str)
    def removeLibrary(self, path: str):
        self.manager.remove_root(path)
        self.libraryChanged.emit(self.listLibraries())

    # ─────────── 폴더 트리 ───────────
    @pyqtSlot(result=str)
    def listFolders(self) -> str:
        folders = self.db.get_folders()
        return json.dumps(folders)

    # ─────────── 인덱싱 ───────────
    @pyqtSlot(str)
    def startIndex(self, scan_path: str):
        if self.manager.is_indexing():
            return
        self._index_cancelling = False
        sp = scan_path or None

        self._index_worker = _IndexWorker(self.manager, scan_path=sp)
        self._index_thread = QThread()
        self._index_worker.moveToThread(self._index_thread)
        self._index_thread.started.connect(self._index_worker.run)
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.phase1_done.connect(self._on_phase1_done)
        self._index_worker.finished.connect(self._on_index_finished)
        self._index_worker.finished.connect(self._index_thread.quit)
        self._index_thread.start()
        self._poll_timer.start()

        label = os.path.basename(sp.rstrip("\\/")) if sp else "전체"
        self.statusChanged.emit(f"인덱싱 시작 [{label}] — 1단계는 파일 목록 작성 (퍼센트 불가)")

    @pyqtSlot()
    def cancelIndex(self):
        if not self.manager:
            return
        self._index_cancelling = True
        self.manager.cancel()
        self._poll_timer.stop()
        self.statusChanged.emit("취소됨 — 스캔된 파일은 보관됨. [인덱스 갱신] 으로 이어서 진행.")

    def _poll_progress(self):
        if self.manager and self.manager.current_progress is not None:
            self._on_index_progress(self.manager.current_progress)

    def _on_phase1_done(self):
        # phase1 끝나면 DB 핸들 재오픈 (메인 PyQt 흐름과 동일)
        if self.manager and self.manager.db_path.exists():
            self.db = self.manager.open_db_for_search()
        self.statusChanged.emit("파일 목록 완료 — 검색 가능. 오디오 분석은 백그라운드 진행 중...")
        self.libraryChanged.emit(self.listLibraries())

    def _on_index_progress(self, p: IndexProgress):
        if self._index_cancelling:
            return
        folder = ""
        if p.current_file:
            d = p.current_file if os.path.isdir(p.current_file) else os.path.dirname(p.current_file)
            folder = os.path.basename(d) or d
        elapsed = time.monotonic() - p.start_time if p.start_time else 0

        payload = {
            "phase": p.phase,
            "scanned": p.scanned,
            "total": p.total,
            "indexed": p.indexed,
            "errors": p.errors,
            "new_count": p.new_count,
            "skipped": p.skipped,
            "percent": p.percent,
            "eta_sec": p.eta_seconds,
            "eta_text": _fmt_time(p.eta_seconds) if p.eta_seconds else None,
            "elapsed_text": _fmt_time(elapsed) if elapsed else "",
            "folder": folder,
            "current_root": p.current_root or "",
            "message": p.message or "",
        }
        self.indexProgress.emit(json.dumps(payload))

    def _on_index_finished(self, result: dict):
        self._poll_timer.stop()
        # phase1_done 이후에도 phase2 가 데이터 추가 → DB snapshot 갱신
        if self.manager and self.manager.db_path.exists():
            self.db = self.manager.open_db_for_search()
        self.indexFinished.emit(json.dumps({"ok": True, **(result or {})}))
        self.libraryChanged.emit(self.listLibraries())

    # ─────────── 파형 / 재생 ───────────
    @pyqtSlot(str, result=str)
    def loadPeaks(self, path: str) -> str:
        """peaks + segments 를 JS 에 JSON 으로 반환.
        파형은 JS Canvas (player.jsx) 에서 직접 그림.
        peaks 는 flat float32 array: 인덱스 (i*ch + c)*2 + [0=min, 1=max]."""
        try:
            from app.ui.player_widget import (
                _load_cached_peaks, _extract_peaks, _save_cached_peaks
            )
            cached = _load_cached_peaks(path)
            if cached is not None:
                ch, sr, nf, peak_levels, segments = cached
            else:
                ch, sr, nf, peak_levels, segments = _extract_peaks(path)
                _save_cached_peaks(path, ch, sr, nf, peak_levels, segments)
            # Web 버전은 mipmap 미지원 — 최저해상도(1024) 만 JS Canvas 로 전달.
            peaks = peak_levels[0] if peak_levels else None
            return json.dumps({
                "ok": True,
                "channels": int(ch),
                "sampleRate": int(sr),
                "nSamples": int(nf),
                "peaks": peaks.reshape(-1).tolist() if peaks is not None else [],
                "segments": segments.tolist(),
                "mediaUrl": Path(path).as_uri(),
            })
        except Exception as e:
            logger.exception("loadPeaks failed: %s", path)
            return json.dumps({"ok": False, "error": str(e)})

    # ─────────── 유틸 ───────────
    @pyqtSlot(str)
    def revealInExplorer(self, path: str):
        import subprocess
        p = Path(path)
        if not p.exists():
            if p.parent.exists():
                os.startfile(str(p.parent))
            return
        try:
            subprocess.Popen(["explorer", "/select,", str(p)])
        except OSError:
            os.startfile(str(p.parent))
