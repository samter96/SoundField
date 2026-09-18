import os
import logging
import time
from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from threading import Event, Lock

try:
    from scandir_rs import Scandir as RustScandir
except ImportError:
    RustScandir = None

from app.database import Database, AudioFile
from app.sync import LocalStore, IndexBusy
from app.metadata import extract, SUPPORTED_EXTS

logger = logging.getLogger(__name__)

MTIME_TOL = 2.0
# 2단계 세션 분모를 담는 index_meta 키. 앱을 강제 종료하고 다시 켜도 진행률 기준이
# 이어지게 하려고 DB 에 남긴다 (사용자 결정 2026-09-07).
# 세션이 자연 완료되면(대기 0) 지운다. 라이브러리 제거/완전삭제도 지운다 —
# 대기수가 줄어드는데 분모가 남으면 완료 수가 실제보다 커 보인다.
META_SESSION_TOTAL_KEY = "meta_session_total"


def _norm(p: str) -> str:
    return os.path.normpath(p).rstrip("\\/")


def _prefix(p: str) -> str:
    return _norm(p) + os.sep


class IndexProgress:
    PHASE_IDLE = "idle"
    PHASE_SCAN = "scan"
    PHASE_META = "meta"
    PHASE_DONE = "done"
    PHASE_INDEX = "meta"  # backward compat

    def __init__(self):
        self.phase = self.PHASE_IDLE
        self.total = 0
        self.scanned = 0
        self.indexed = 0
        self.skipped = 0
        self.errors = 0
        self.current_file = ""
        self.current_root = ""    # 지금 스캔 중인 라이브러리/폴더
        self.message = ""
        self.new_count = 0
        self.dirs_done = 0
        self.queue_size = 0
        # Phase1 진행 표시용. phase1_total: % 분모(재스캔=이전 개수).
        # 첫 인덱싱은 전체 개수를 알기 위해 사전 스캔하지 않는다. fts_* 는
        # 신규 경로 검색색인 작성 진행이며, 이 단계부터 정확한 % 를 표시한다.
        self.phase1_total = 0
        self.fts_total = 0
        self.fts_done = 0
        # legacy UI guard. 사전 개수 파악 패스는 제거됨.
        self.phase1_counting = False
        self.count_seen = 0
        self.start_time = 0.0
        self.meta_start_time = 0.0
        self.phase2_file_ids: List[str] = []
        self.phase2_restrict_to_run = False
        self.phase2_run_id = ""
        self.phase2_use_active_run = False
        # 2단계 세션 누적 — total 은 **세션 분모**(줄지 않음), base_done 은 이번
        # 조각 이전까지 이미 처리한 수. 백그라운드 메타 분석은 앱 사용/재생 양보로
        # 중단·재시작을 반복하고 매번 새 IndexProgress 를 만들기 때문에, 이 두 값이
        # 없으면 분모가 조각마다 줄고 %가 조각 기준이 된다 (사용자 신고 2026-09-07).
        # 분모는 index_meta.meta_session_total 에 저장돼 **앱 재시작 후에도** 이어진다.
        self.base_done = 0
        # 스캔 중 '디스크에서 사라진 파일' 정리로 생긴 고아 중복 후보 —
        # 갱신 완료 시 UI 가 팝업으로 복원 여부를 물음 (자동 복원 금지 정책).
        self.orphan_dup_ids: List[str] = []

    @property
    def meta_done(self) -> int:
        """세션 누적 완료 수 = 이전 조각까지의 처리분 + 이번 조각 처리분.
        (오류도 '처리 시도 완료' 로 센다 — 다시 집어들지 않으므로 진행에 포함해야
        진행률이 멈춘 것처럼 보이지 않는다. 원본 run_done 정의와 동일.)"""
        return min(self.total, self.base_done + self.indexed + self.errors) \
            if self.total > 0 else (self.base_done + self.indexed + self.errors)

    @property
    def percent(self) -> int:
        if self.phase == self.PHASE_META and self.total > 0:
            return min(100, int(self.meta_done * 100 / self.total))
        if self.phase == self.PHASE_DONE:
            return 100
        return 0

    @property
    def eta_seconds(self) -> Optional[float]:
        """남은 시간(초). 남은 양은 **세션 기준**, 속도는 **이번 조각 기준**으로 잰다.
        (원본 _on_background_meta_progress 와 같은 계산: remaining / (run_done/elapsed).
        속도를 세션 기준으로 재면 중단돼 있던 시간이 섞여 실제보다 느리게 나온다.)
        """
        if self.phase == self.PHASE_META and self.total > 0 and self.meta_start_time > 0:
            run_done = self.indexed + self.errors
            if run_done < 20:
                return None
            elapsed = time.monotonic() - self.meta_start_time
            if elapsed < 1:
                return None
            rate = run_done / elapsed
            remaining = max(0, self.total - self.meta_done)
            return remaining / rate if rate > 0 else None
        return None


def _set_thread_priority_below_normal() -> None:
    """현재 스레드의 OS 우선순위를 BELOW_NORMAL 로 — UI 스레드 starvation 방지.
    워커/DB writer 진입 시 1회만 호출 (per-file 호출 X). Windows 한정, 그 외 no-op.
    실패해도 silent — 성능에 영향 없음.
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThread.restype = wintypes.HANDLE
        kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.SetThreadPriority.restype = wintypes.BOOL
        THREAD_PRIORITY_BELOW_NORMAL = -1
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception:
        pass


class LibraryManager:
    """다중 라이브러리 + 부분 스캔.
    - 등록된 최상위 폴더(roots) 는 library_roots 테이블에 저장
    - update_index(scan_path) 로 임의 서브트리만 부분 스캔 가능
    - 삭제는 스캔된 서브트리 안에서만 발생 (다른 폴더 인덱스는 보존)
    """

    def __init__(self, store_dir: Path,
                 worker_threads: int = 32, batch_size: int = 5000,
                 scan_threads: int = 32):
        self.store = LocalStore(store_dir)
        self.worker_threads = worker_threads
        self.scan_threads = scan_threads
        self.batch_size = batch_size
        self._cancel = Event()
        self._busy = IndexBusy()
        self.current_progress: Optional["IndexProgress"] = None
        # 라이브러리 단위 부분 취소 — Phase2 워커가 file 처리 직전 체크.
        # 빈 tuple 일 땐 attribute lookup + bool 체크 ~150ns/파일 → 인덱싱 hot path 영향 없음.
        # 원자적 swap (Python tuple 할당은 GIL 보호) → reader 락 불필요.
        self._cancelled_snapshot: tuple = ()
        self._cancelled_lock = Lock()
        # 1회 — DB 파일 생성 + 스키마 마이그레이션 + FTS 정합성 플래그 세팅.
        # 이후의 open_db_for_search 는 read_only 라 스키마 작업을 하지 않으므로
        # 여기서 미리 마이그레이션을 끝내둬야 한다.
        self._bootstrap_db()

    def _bootstrap_db(self):
        Database(str(self.db_path))  # writable, _ensure_indices 1회 실행

    @property
    def db_path(self) -> Path:
        return self.store.db_path

    def open_db_for_search(self) -> Database:
        """검색/UI 용 read-only connection.
        스키마 마이그레이션·index_meta write 안 함 → 인덱싱 워커와 락 경합 X."""
        return Database(str(self.db_path), read_only=True)

    def cancel(self):
        self._cancel.set()

    def is_indexing(self) -> bool:
        return self._busy.is_busy()

    # ─────────── 라이브러리 단위 부분 취소 (백그라운드 Phase2 즉시 skip) ───────────
    def cancel_prefix(self, path: str) -> None:
        """해당 path 이하의 모든 파일을 Phase2 워커가 즉시 skip 하도록 등록.
        호출 직후 새 file pop 부터 즉시 효과. 이미 extract 중인 in-flight 1개는
        consumer recheck 가 잡아냄. RemoveLibraryWorker 가 remove_root 직전에 호출.
        """
        prefix = _norm(path).lower() + os.sep
        with self._cancelled_lock:
            current = self._cancelled_snapshot
            if prefix in current:
                return
            # 새 tuple 로 원자적 swap — reader 는 락 없이 안전하게 읽음
            self._cancelled_snapshot = current + (prefix,)

    def reset_cancelled_prefixes(self) -> None:
        """새 Phase2 시작 시 호출 — 이전 잔재 제거."""
        with self._cancelled_lock:
            self._cancelled_snapshot = ()

    @staticmethod
    def _is_path_cancelled(path: str, snapshot: tuple) -> bool:
        """snapshot 은 호출자가 미리 self._cancelled_snapshot 로컬 캐시 권장 (attribute lookup 절약).
        snapshot 비어있을 땐 호출자에서 if snap: 로 단락 — 이 함수는 진입 시 이미 non-empty 가정.
        """
        plow = path.lower()
        for pre in snapshot:
            if plow.startswith(pre):
                return True
        return False

    # ─────────── roots API ───────────
    def get_roots(self) -> List[Dict]:
        return Database(str(self.db_path)).get_library_roots()

    def reorder_roots(self, paths: List[str]):
        Database(str(self.db_path)).reorder_library_roots([_norm(p) for p in paths])

    def add_root(self, path: str):
        """라이브러리 등록. 이전에 제거(soft, removed=1)했던 경로를 다시 추가하면
        그 이하 removed 를 해제 — 재추가 = 복원(빠른 인덱싱만 해도 검색에 다시 노출).
        soft 제거로 바꾼 뒤, 재추가해도 removed 가 유지돼 라이브러리가 안 돌아오던
        회귀 수정. (개별 파일 삭제분은 add_root 를 안 거치므로 영향 없음.)"""
        db = Database(str(self.db_path))
        db.add_library_root(_norm(path))
        db.clear_removed_under(_prefix(path))

    def remove_root(self, path: str) -> Tuple[int, List[str]]:
        """라이브러리 제거 + 그 이하 모든 파일 인덱스 제거(soft, removed=1 — 즉시).
        물리 삭제(delete_files_under)는 8.7GB+ DB 의 두 FTS 색인 재작성으로 분 단위라
        soft-delete 로 전환 — 검색/목록에서 즉시 빠지고, 전체 재스캔 시 복원.
        반환: (제거 행 수, 고아 중복 후보 id — UI 팝업 승인 후 복원)."""
        db = Database(str(self.db_path))
        n, orphan_ids = db.soft_remove_under(_prefix(path))
        db.remove_library_root(_norm(path))
        # 대기수가 줄어드는데 세션 분모가 남으면 완료 수가 실제보다 커 보인다 → 무효화
        db.set_meta(META_SESSION_TOTAL_KEY, "")
        return n, orphan_ids

    def purge_root(self, path: str) -> Dict:
        """라이브러리 완전 제거 — 인덱스/FTS/블랙리스트 물리 삭제 + 루트 등록 해제
        + 고아 중복 숨김 복원(그룹당 1개). 파형 캐시/히스토리는 UI 워커가 처리
        (캐시 키 재계산에 UI 모듈 상수 필요). 반환: purge 통계 dict."""
        db = Database(str(self.db_path))
        stats = db.purge_files_under(_prefix(path))
        db.remove_library_root(_norm(path))
        db.set_meta(META_SESSION_TOTAL_KEY, "")   # 위와 같은 이유
        return stats

    def repair_fts(self, progress_cb=None) -> Dict:
        """FTS 표적 수리 — 인덱싱과 같은 IndexBusy 락 공유 (동시 쓰기 방지).
        인덱싱 중이면 즉시 거절 (UI 가 다음 기회에 다시 안내).
        progress_cb(done, total): 채우기 진행 보고."""
        if not self._busy.acquire():
            return {"success": False, "message": "이미 인덱싱 중입니다"}
        try:
            stats = Database(str(self.db_path)).fts_auto_repair(progress_cb=progress_cb)
            return {"success": True, **stats}
        finally:
            self._busy.release()

    def remove_root_only(self, path: str) -> None:
        """라이브러리 루트 등록만 제거 — 파일 인덱스는 유지.
        부모 루트로 통합될 때 자식 루트 제거용.
        """
        Database(str(self.db_path)).remove_library_root(_norm(path))

    # ─────────── 인덱싱 ───────────
    @staticmethod
    def _dedupe_nested_targets(paths: List[str]) -> List[str]:
        result: List[str] = []
        sep = chr(92)
        for path in sorted(set(_norm(p) for p in paths), key=len):
            low = path.lower()
            nested = False
            for parent in result:
                p_low = parent.lower().rstrip(sep + "/")
                if low.startswith(p_low + sep) or low.startswith(p_low + "/"):
                    nested = True
                    break
            if not nested:
                result.append(path)
        return result

    def update_index(self,
                     scan_path: Optional[str] = None,
                     progress_cb: Optional[Callable[[IndexProgress], None]] = None,
                     phase1_done_cb: Optional[Callable[[], None]] = None,
                     force_rescan: bool = False,
                     phase2_only: bool = False,
                     reset_scope_prefix: Optional[str] = None,
                     reset_scope_paths: Optional[list] = None,
                     reset_include_pending: bool = False,
                     reset_include_failed: bool = True,
                     run_phase2: bool = True) -> Dict:
        """scan_path 가 None 이면 등록된 모든 root, 아니면 그 경로만 부분 스캔.

        force_rescan=True (전체 갱신 모드): existing dict 로딩과 mtime 비교를
        우회 — 라이브러리 추가와 동일한 hot path 로 모든 파일을 재처리.
        ON CONFLICT 분기 덕분에 mtime 동일하면 meta_extracted 유지 (Phase2 안 돌림).
        deleted 감지는 안 함 (다음 빠른 갱신에서 정리).

        phase2_only=True: Phase1(파일 목록 작성/존재 검증) 스킵. DB 에 meta_extracted=0
        으로 표시된 파일만 추출 — 실패 재시도 워크플로.

        reset_scope_prefix / reset_scope_paths: phase2_only 와 함께 쓰는 범위 한정 재시도.
        Phase2 시작 전 대상(prefix+필터 또는 paths)을 meta=0 으로 reset 하면서 새 번호표
        (phase2_run_id)를 스탬프하고, Phase2 를 그 번호표로 한정 → 그 범위만 분석.
        (스탬프 안 하면 _phase2_metadata 가 DB 전체 meta=0 을 드레인함.)
        reset_include_pending/failed: prefix 범위에서 리셋할 상태 선택.
        """
        self._cancel.clear()
        progress = IndexProgress()
        self.current_progress = progress

        if not self._busy.acquire():
            return {"success": False, "message": "이미 인덱싱 중입니다", "progress": progress}

        try:
            db = Database(str(self.db_path))

            if phase2_only:
                # 범위 한정 재시도: 번호표(run_id) 발급 → 리셋 대상에만 스탬프 →
                # Phase2 를 그 번호표로 한정 (정규 인덱싱과 동일한 스코프 메커니즘).
                # 번호표 없이 돌리면 _phase2_metadata 가 DB 전체 meta=0 을 드레인함.
                if reset_scope_prefix or reset_scope_paths:
                    run_id = f"{int(time.time() * 1000)}-{os.getpid()}"
                    progress.phase = IndexProgress.PHASE_SCAN
                    progress.phase2_run_id = run_id
                    progress.phase2_restrict_to_run = True
                    db.set_meta("active_phase2_run_id", run_id)
                    progress.message = "재시도 대상 reset 중..."
                    if progress_cb:
                        progress_cb(progress)
                    t0 = time.monotonic()
                    if reset_scope_paths:
                        n_reset = db.reset_meta_for_paths(reset_scope_paths, run_id=run_id)
                    else:
                        n_reset = db.reset_incomplete_under(
                            reset_scope_prefix,
                            include_pending=reset_include_pending,
                            include_failed=reset_include_failed,
                            run_id=run_id,
                        )
                    logger.info(
                        f"retry reset {n_reset:,} ({time.monotonic()-t0:.1f}s) run={run_id}"
                    )
                    progress.message = f"{n_reset:,}개 재시도 큐 등록 — Phase2 시작"
                    if progress_cb:
                        progress_cb(progress)
                if phase1_done_cb:
                    try:
                        phase1_done_cb()
                    except Exception:
                        logger.exception("phase1_done_cb 오류")
                self._phase2_metadata(db, progress, progress_cb)
                db.set_meta("last_indexed_at", str(time.time()))
                progress.phase = IndexProgress.PHASE_DONE
                progress.message = (
                    f"메타 분석 완료 — 추가/갱신 {progress.indexed:,}, "
                    f"스킵 {progress.skipped:,}, 오류 {progress.errors:,}"
                )
                if progress_cb:
                    progress_cb(progress)
                return {"success": True, "message": progress.message, "progress": progress}

            if scan_path:
                targets = [_norm(scan_path)]
            else:
                targets = [_norm(r["path"]) for r in db.get_library_roots()]

            if not targets:
                return {"success": False,
                        "message": "등록된 라이브러리가 없습니다. [+ 라이브러리 추가] 를 누르세요",
                        "progress": progress}

            valid = [t for t in targets if Path(t).exists()]
            missing = [t for t in targets if t not in valid]
            for m in missing:
                logger.warning(f"경로 없음, 스킵: {m}")

            if not scan_path:
                valid = self._dedupe_nested_targets(valid)
            progress.phase2_restrict_to_run = True
            progress.phase2_run_id = f"{int(time.time() * 1000)}-{os.getpid()}"
            db.set_meta("active_phase2_run_id", progress.phase2_run_id)

            for t in valid:
                if self._cancel.is_set():
                    break
                progress.current_root = t
                self._phase1_enumerate(db, t, progress, progress_cb,
                                       force_rescan=force_rescan)
                if not self._cancel.is_set():
                    db.update_root_indexed_at(t)

            if self._cancel.is_set():
                return {"success": False, "message": "취소됨", "progress": progress}

            if phase1_done_cb:
                try:
                    phase1_done_cb()
                except Exception:
                    logger.exception("phase1_done_cb 오류")

            if not run_phase2:
                db.set_meta("last_indexed_at", str(time.time()))
                progress.phase = IndexProgress.PHASE_DONE
                tag = f" [{os.path.basename(scan_path) or scan_path}]" if scan_path else ""
                progress.message = (
                    f"파일 목록 완료{tag} — 검색 가능. "
                    f"메타데이터는 백그라운드에서 분석됩니다."
                )
                if progress_cb:
                    progress_cb(progress)
                return {"success": True, "message": progress.message, "progress": progress}

            self._phase2_metadata(db, progress, progress_cb)

            db.set_meta("last_indexed_at", str(time.time()))

            progress.phase = IndexProgress.PHASE_DONE
            tag = f" [{os.path.basename(scan_path) or scan_path}]" if scan_path else ""
            progress.message = (
                f"완료{tag} - 추가/갱신 {progress.indexed:,}, "
                f"스킵 {progress.skipped:,}, 오류 {progress.errors:,}"
            )
            if progress_cb:
                progress_cb(progress)

            return {"success": True, "message": progress.message, "progress": progress}

        except Exception as e:
            logger.exception("인덱싱 실패")
            return {"success": False, "message": f"오류: {e}", "progress": progress}
        finally:
            # 대량 write 직후 WAL 을 truncate — WAL 비대화로 새 연결이 수초씩 걸려
            # 검색/필터에서 UI 프리즈 나던 문제 방지. 백그라운드 스레드라 UI 영향 없음.
            try:
                if 'db' in locals():
                    db.checkpoint()
            except Exception:
                logger.exception("WAL checkpoint 실패")
            self._busy.release()

    def update_metadata_background(self,
                                   progress_cb: Optional[Callable[[IndexProgress], None]] = None) -> Dict:
        self._cancel.clear()
        progress = IndexProgress()
        progress.phase2_use_active_run = True
        self.current_progress = progress
        try:
            db = Database(str(self.db_path))
            self._phase2_metadata(db, progress, progress_cb)
            progress.phase = IndexProgress.PHASE_DONE
            progress.message = (
                f"백그라운드 메타 분석 완료 — 처리 {progress.indexed:,}, "
                f"오류 {progress.errors:,}"
            )
            if progress_cb:
                progress_cb(progress)
            return {"success": True, "message": progress.message, "progress": progress}
        except Exception as e:
            logger.exception("백그라운드 메타 분석 실패")
            return {"success": False, "message": f"메타 분석 오류: {e}", "progress": progress}

    # ─────────── 빠른 변경 감지 (dry-run Phase1) ───────────
    def detect_changes(self,
                       scan_path: Optional[str] = None,
                       progress_cb: Optional[Callable[[IndexProgress], None]] = None,
                       sample_limit: int = 10) -> Dict:
        """DB write 없이 변경사항만 빠르게 감지.
        반환:
          {
            "success": bool, "message": str,
            "added": int, "updated": int, "deleted": int,
            "scanned": int, "elapsed": float,
            "added_sample": [path...], "updated_sample": [path...],
            "deleted_sample": [path...],
          }
        """
        self._cancel.clear()
        progress = IndexProgress()
        self.current_progress = progress
        result = {
            "success": False, "message": "", "added": 0, "updated": 0,
            "deleted": 0, "scanned": 0, "elapsed": 0.0,
            "added_sample": [], "updated_sample": [], "deleted_sample": [],
        }

        if not self._busy.acquire():
            result["message"] = "이미 인덱싱 중입니다"
            return result

        try:
            db = Database(str(self.db_path))
            if scan_path:
                targets = [_norm(scan_path)]
            else:
                targets = [_norm(r["path"]) for r in db.get_library_roots()]
            if not targets:
                result["message"] = "등록된 라이브러리가 없습니다"
                return result
            valid = [t for t in targets if Path(t).exists()]
            if not scan_path:
                valid = self._dedupe_nested_targets(valid)

            t0 = time.monotonic()
            progress.phase = IndexProgress.PHASE_SCAN
            progress.start_time = t0

            added_sample: List[str] = []
            updated_sample: List[str] = []
            added_n = 0
            updated_n = 0

            EMIT_INTERVAL = 0.15
            last_emit = 0.0
            last_yield = 0.0
            YIELD_INTERVAL = 0.03   # ~30ms 마다 GIL 양보 → 로더 ~30fps 매끄럽게

            for root in valid:
                if self._cancel.is_set():
                    break
                progress.current_root = root
                scope_prefix = _prefix(root)
                existing = db.get_indexed_paths_under(scope_prefix)

                # Rust walker 우선, 실패 시 os.scandir.
                used_rust = False
                if RustScandir is not None:
                    try:
                        root_prefix = root.rstrip("\\/") + os.sep
                        audio_globs = [f"*{ext}" for ext in SUPPORTED_EXTS]
                        for entry in RustScandir(root, file_include=audio_globs,
                                                 case_sensitive=False):
                            if self._cancel.is_set():
                                break
                            if not entry.is_file:
                                continue
                            rel_path = entry.path
                            if "__MACOSX" in rel_path:
                                continue
                            slash = rel_path.rfind(os.sep)
                            name = rel_path[slash + 1:] if slash >= 0 else rel_path
                            if name[:1] == "." and (name.startswith("._") or name == ".DS_Store"):
                                continue
                            path = root_prefix + rel_path
                            mtime = entry.st_mtime.timestamp()
                            progress.scanned += 1
                            cached = existing.pop(path, None)
                            # get_indexed_paths_under 가 (mtime, meta_extracted) 튜플 반환 —
                            # mtime 만 비교에 사용. 스칼라 비교하면 tuple+float TypeError.
                            if cached is None:
                                added_n += 1
                                if len(added_sample) < sample_limit:
                                    added_sample.append(path)
                            elif cached[0] + MTIME_TOL < mtime:
                                updated_n += 1
                                if len(updated_sample) < sample_limit:
                                    updated_sample.append(path)

                            if progress.scanned % 64 == 0:
                                # 시간 기준 GIL 양보 — 개수 기준(200개)은 처리속도에 따라
                                # 0.1~0.2s 간격이라 로더가 뚝뚝 끊겼음. ~30ms 마다 1ms 실제
                                # 블록으로 메인이 ~30fps 로 스케줄돼 매끄럽게 돈다.
                                now = time.monotonic()
                                if now - last_yield >= YIELD_INTERVAL:
                                    time.sleep(0.001)
                                    last_yield = now
                                if progress_cb and now - last_emit >= EMIT_INTERVAL:
                                    progress.new_count = added_n + updated_n
                                    progress.message = (
                                        f"변경 감지 중... 스캔 {progress.scanned:,} / "
                                        f"+{added_n} ~{updated_n}"
                                    )
                                    progress_cb(progress)
                                    last_emit = now
                        used_rust = True
                    except Exception as e:
                        logger.warning(f"detect_changes scandir-rs 실패: {e}")
                        used_rust = False

                if not used_rust:
                    # fallback: os.scandir 재귀
                    stack = [root]
                    while stack and not self._cancel.is_set():
                        d = stack.pop()
                        try:
                            with os.scandir(d) as it:
                                for entry in it:
                                    if self._cancel.is_set():
                                        break
                                    name = entry.name
                                    if name[:1] == "." and (name.startswith("._")
                                                             or name == ".DS_Store"):
                                        continue
                                    try:
                                        if entry.is_dir(follow_symlinks=False):
                                            if name != "__MACOSX":
                                                stack.append(entry.path)
                                            continue
                                        dot = name.rfind(".")
                                        if dot < 1:
                                            continue
                                        if name[dot:].lower() not in SUPPORTED_EXTS:
                                            continue
                                        if not entry.is_file(follow_symlinks=False):
                                            continue
                                        st = entry.stat()
                                        path = entry.path
                                        mtime = st.st_mtime
                                        progress.scanned += 1
                                        cached = existing.pop(path, None)
                                        # cached = (mtime, meta_extracted) 튜플 — mtime 만 비교용
                                        if cached is None:
                                            added_n += 1
                                            if len(added_sample) < sample_limit:
                                                added_sample.append(path)
                                        elif cached[0] + MTIME_TOL < mtime:
                                            updated_n += 1
                                            if len(updated_sample) < sample_limit:
                                                updated_sample.append(path)
                                    except OSError:
                                        pass
                        except OSError as e:
                            logger.warning(f"detect scandir 실패 {d}: {e}")

                # existing 에 남은 키 = 사라진 파일
                if existing:
                    deleted_paths = list(existing.keys())
                    result["deleted"] += len(deleted_paths)
                    for p in deleted_paths:
                        if len(result["deleted_sample"]) < sample_limit:
                            result["deleted_sample"].append(p)

            elapsed = time.monotonic() - t0
            if self._cancel.is_set():
                result["message"] = "취소됨"
                return result

            result["success"] = True
            result["added"] = added_n
            result["updated"] = updated_n
            result["scanned"] = progress.scanned
            result["elapsed"] = elapsed
            result["added_sample"] = added_sample
            result["updated_sample"] = updated_sample
            total = added_n + updated_n + result["deleted"]
            result["message"] = (
                f"감지 완료 ({elapsed:.1f}s) — "
                f"추가 {added_n:,} / 수정 {updated_n:,} / "
                f"삭제 {result['deleted']:,} (스캔 {progress.scanned:,})"
                if total > 0 else
                f"변경사항 없음 ({elapsed:.1f}s, 스캔 {progress.scanned:,})"
            )
            return result
        except Exception as e:
            logger.exception("변경 감지 실패")
            result["message"] = f"오류: {e}"
            return result
        finally:
            self._busy.release()

    # ───────────────────────── 스캔 ─────────────────────────
    def _phase1_enumerate(self, db: Database, scan_root: str,
                          progress: IndexProgress,
                          progress_cb: Optional[Callable],
                          force_rescan: bool = False):
        progress.phase = IndexProgress.PHASE_SCAN
        progress.message = f"파일 목록 작성 시작: {scan_root}"
        if progress_cb:
            progress_cb(progress)

        logger.info(
            f"스캔 시작: {scan_root}{' (force_rescan)' if force_rescan else ''}"
        )
        scope_prefix = _prefix(scan_root)

        # 전체(강제) 갱신: '제거'(removed=1) 해제 — 디스크에 남은 파일이 다시 보이게.
        # 빠른 갱신(force_rescan=False)에서는 removed 를 유지 → 제거분이 안 돌아온다.
        if force_rescan:
            n_restored = db.clear_removed_under(scope_prefix)
            if n_restored:
                logger.info(f"removed 해제(전체 갱신): {n_restored:,} ({scan_root})")

        # 전체 갱신(force_rescan) 모드에서도 DB 데이터를 로딩하여 '변하지 않은 파일'은
        # DB 쓰기를 건너뛰어야 합니다. 70만 개를 매번 DB에 쓰는 것이 병목의 원인입니다.
        t_existing = time.monotonic()
        existing = db.get_indexed_paths_under(scope_prefix)
        # Phase1 % 는 "이미 한 번 완주한 동일 루트"를 다시 스캔할 때만 신뢰할 수 있다.
        # 새 상위 루트로 통합하는 경우엔 기존 하위 루트 파일만 DB에 있어 len(existing)이
        # 실제 전체 파일 수보다 작다. 그 값을 분모로 쓰면 99%까지 튄 뒤 오래 세는 UI가 된다.
        progress.phase1_total = 0
        scan_key = _norm(scan_root).lower()
        try:
            for root in db.get_library_roots():
                root_key = _norm(root["path"]).lower()
                if root_key == scan_key and root.get("last_indexed_at"):
                    progress.phase1_total = len(existing)
                    break
        except Exception:
            logger.exception("Phase1 진행률 분모 판정 실패")
        logger.info(
            f"기존 인덱스 로딩 완료: {len(existing):,} 경로 ({time.monotonic()-t_existing:.2f}s)"
        )

        new_or_changed_count = 0
        new_paths_for_fts: List[tuple] = []  # (fid, path, name)
        flush_batch: List[tuple] = []

        if progress.start_time == 0.0:
            progress.start_time = time.monotonic()
        last_flush_t = time.monotonic()
        last_emit_t = 0.0
        FLUSH_INTERVAL = 10.0
        EMIT_INTERVAL = 0.12

        def consume_file(path: str, name: str, size: int, mtime: float):
            nonlocal new_or_changed_count
            progress.scanned += 1
            # meta_extracted 상태도 함께 가져오도록 get_indexed_paths_under 가 수정되어야 함
            # 여기서는 편의상 mtime 대조 후, force_rescan 모드라면 상태를 확인하는 로직 추가
            entry = existing.pop(path, None)
            
            is_new = entry is None
            cached_mtime = entry[0] if entry else None
            cached_meta = entry[1] if entry else 1 # 기본값 성공으로 간주
            
            is_updated = (cached_mtime is not None and
                          cached_mtime + MTIME_TOL < mtime)
            
            # 실패했던 파일(meta=2)은 force_rescan 시 무조건 다시 시도
            is_failed = (force_rescan and cached_meta == 2)
            
            if is_new or is_updated or is_failed:
                fid = Database.generate_id(path)
                flush_batch.append((fid, path, name, size, mtime, progress.phase2_run_id))
                progress.phase2_file_ids.append(fid)
                new_or_changed_count += 1
                if is_new:
                    new_paths_for_fts.append((fid, path, name))
            else:
                progress.skipped += 1

        def walk_dir(d: str):
            # scandir-rs 실패/부재 시 fallback 워커. (indexing engine v3 에서
            # 정의가 누락된 채 호출부만 남아 NameError 크래시 — v1.0.0 본문 복원)
            # Hot loop — Path() / basename / parts 같은 객체 생성 X.
            # 확장자 string slice 로 직접 검사 + sidecar 빠른 제외.
            files, dirs = [], []
            if self._cancel.is_set():
                return d, files, dirs
            try:
                with os.scandir(d) as it:
                    for entry in it:
                        if self._cancel.is_set():
                            break
                        name = entry.name
                        # 숨김/sidecar 제외 (확장자 검사 전 짧게)
                        if name[:1] == "." and (name.startswith("._")
                                                or name == ".DS_Store"):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if name != "__MACOSX":
                                    dirs.append(entry.path)
                                continue
                            # 확장자 인라인 — Path(name).suffix.lower() 보다 훨씬 빠름
                            dot = name.rfind(".")
                            if dot < 1:
                                continue
                            if name[dot:].lower() not in SUPPORTED_EXTS:
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            st = entry.stat()  # Windows scandir 에선 캐시값 (네트워크 호출 X)
                            files.append((entry.path, name,
                                          st.st_size, st.st_mtime))
                        except OSError:
                            pass
            except OSError as e:
                logger.warning(f"scandir 실패 {d}: {e}")
            return d, files, dirs

        def walk_rust(root: str) -> bool:
            if RustScandir is None:
                return False
            t0 = time.monotonic()
            last_emit = 0.0
            last_yield = 0.0
            YIELD_INTERVAL = 0.03   # ~30ms 마다 GIL 양보 → 로더 ~30fps
            last_flush = last_flush_t
            root_prefix = root.rstrip("\\/") + os.sep
            audio_globs = [f"*{ext}" for ext in SUPPORTED_EXTS]
            try:
                seen_since_emit = 0
                for entry in RustScandir(root, file_include=audio_globs,
                                         case_sensitive=False):
                    if self._cancel.is_set():
                        break
                    if not entry.is_file:
                        continue
                    rel_path = entry.path
                    if "__MACOSX" in rel_path:
                        continue
                    slash = rel_path.rfind(os.sep)
                    name = rel_path[slash + 1:] if slash >= 0 else rel_path
                    if name[:1] == "." and (name.startswith("._") or name == ".DS_Store"):
                        continue
                    path = root_prefix + rel_path
                    mtime = entry.st_mtime.timestamp()
                    consume_file(path, name, entry.st_size, mtime)
                    seen_since_emit += 1

                    if len(flush_batch) >= self.batch_size:
                        # 이미 fid가 포함된 상태이므로 커스텀 UPSERT 로직 호출 (또는 upsert_paths_only 수정)
                        _db_upsert_batch(db, flush_batch)
                        flush_batch.clear()
                        last_flush = time.monotonic()

                    if seen_since_emit < 50:
                        continue

                    now = time.monotonic()
                    # 시간 기준 GIL 양보 — ~30ms 마다 1ms 실제 블록 → 로더 매끄럽게.
                    if now - last_yield >= YIELD_INTERVAL:
                        time.sleep(0.001)
                        last_yield = now
                    if flush_batch and now - last_flush >= FLUSH_INTERVAL:
                        _db_upsert_batch(db, flush_batch)
                        flush_batch.clear()
                        last_flush = now
                    if progress_cb and now - last_emit >= EMIT_INTERVAL:
                        progress.current_file = os.path.dirname(path)
                        progress.new_count = new_or_changed_count
                        progress_cb(progress)
                        last_emit = now
                    seen_since_emit = 0
                logger.info(f"scandir-rs 스캔 완료 ({time.monotonic()-t0:.1f}s)")
                return True
            except Exception as e:
                logger.warning(f"scandir-rs 실패, os.scandir fallback: {e}")
                return False

        def _db_upsert_batch(db_obj: Database, batch: List[tuple]):
            """id가 포함된 배치를 효율적으로 UPSERT"""
            if not batch: return
            conn = db_obj._connect()
            try:
                cur = conn.cursor()
                cur.execute("BEGIN")
                cur.executemany("""
                    INSERT INTO audio_files
                    (id, file_path, file_name, file_size, modified_at, meta_extracted, phase2_run_id)
                    VALUES (?,?,?,?,?,0,?)
                    ON CONFLICT(id) DO UPDATE SET
                        file_size=excluded.file_size,
                        modified_at=excluded.modified_at,
                        meta_extracted=0,
                        phase2_run_id=excluded.phase2_run_id,
                        indexed_at=strftime('%s','now')
                """, batch)
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Phase1 배지 업서트 실패: {e}")
            finally:
                conn.close()

        used_rust_walk = walk_rust(scan_root)
        ex = None
        try:
            if not used_rust_walk:
                ex = ThreadPoolExecutor(max_workers=self.scan_threads,
                                        thread_name_prefix="walk")
                futures = {ex.submit(walk_dir, scan_root)}
                while futures and not self._cancel.is_set():
                    done, futures = wait(futures, timeout=EMIT_INTERVAL,
                                         return_when=FIRST_COMPLETED)
                    for fut in done:
                        if self._cancel.is_set():
                            break
                        d, files, dirs = fut.result()
                        progress.dirs_done += 1
                        progress.current_file = d
                        for sub in dirs:
                            if self._cancel.is_set():
                                break
                            futures.add(ex.submit(walk_dir, sub))
                        for path, name, size, mtime in files:
                            consume_file(path, name, size, mtime)

                    progress.new_count = new_or_changed_count
                    progress.queue_size = len(futures)

                    now = time.monotonic()
                    if (len(flush_batch) >= self.batch_size or
                            (flush_batch and now - last_flush_t >= FLUSH_INTERVAL)):
                        _db_upsert_batch(db, flush_batch)
                        flush_batch = []
                        last_flush_t = now

                    if progress_cb and now - last_emit_t >= EMIT_INTERVAL:
                        progress_cb(progress)
                        last_emit_t = now
        finally:
            if ex is not None:
                ex.shutdown(wait=False, cancel_futures=True)

        if flush_batch:
            _db_upsert_batch(db, flush_batch)

        # FTS bulk insert 최적화 버전 (ID 직접 전달)
        if new_paths_for_fts:
            t0 = time.monotonic()
            fts_total = len(new_paths_for_fts)
            progress.fts_total = fts_total
            progress.fts_done = 0
            conn = db._connect()
            try:
                cur = conn.cursor()
                CHUNK = 5000
                fts_yield = 0.0
                for i in range(0, fts_total, CHUNK):
                    batch = new_paths_for_fts[i:i + CHUNK]
                    fts_rows = [
                        Database._fts_row(fid, name, path, ("",) * 10)
                        for fid, path, name in batch
                    ]
                    # 청크 단위 트랜잭션 — 중간에 앱이 강제 종료돼도 커밋된 청크는
                    # 보존된다(이전엔 단일 BEGIN…commit 이라 도중 종료 시 작성분 전체
                    # 롤백 → search_fts 통째 누락 + fts_stale). 5000행 묶음이라 커밋
                    # 빈도가 낮아 WAL 락 경합 영향도 미미.
                    cur.execute("BEGIN")
                    cur.executemany("""
                        INSERT OR REPLACE INTO search_fts
                        (rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, comments,
                         description, keywords, category, sub_category, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, fts_rows)
                    Database._term_fts_insert(cur, fts_rows)   # 단어색인 동기 (Phase1 신규 경로)
                    conn.commit()
                    # 대량(400k+) 검색색인 작성은 원래 양보/진행 없이 수 초 블로킹 →
                    # 로더 프리즈. 청크마다 GIL 양보 + 진행 emit 으로 UI 매끄럽게.
                    progress.fts_done = min(fts_total, i + CHUNK)
                    now = time.monotonic()
                    if now - fts_yield >= 0.03:
                        time.sleep(0.001)
                        fts_yield = now
                    if progress_cb:
                        progress_cb(progress)
                progress.fts_done = progress.fts_total = 0  # 완료 — 표시 해제
                logger.info(f"FTS bulk insert {len(new_paths_for_fts):,} 신규 경로 ({time.monotonic()-t0:.1f}s)")
            except Exception as e:
                try:
                    conn.rollback()   # 진행 중이던 청크만 롤백 — 이전 커밋분은 유지
                except Exception:
                    pass
                logger.error(f"FTS bulk insert 실패: {e}")
            finally:
                conn.close()

        # 취소 시 deleted/reset 처리 스킵
        if self._cancel.is_set():
            progress.message = (
                f"스캔 취소됨 [{scan_root}]: {progress.scanned:,} 발견까지 보관됨"
            )
            logger.info(progress.message)
            if progress_cb:
                progress_cb(progress)
            return

        # 범위 내에서 사라진 파일만 삭제
        deleted = list(existing.keys())
        if deleted:
            orphans = db.delete_files(deleted)
            if orphans:
                # 고아 중복 후보 축적 — 갱신 완료 시 UI 팝업으로 복원 여부 확인
                progress.orphan_dup_ids.extend(orphans)
            logger.info(f"삭제된 파일 {len(deleted)}개 제거 (범위: {scan_root})")
        
        progress.message = (
            f"스캔 완료 [{scan_root}]: {progress.scanned:,} 발견 / "
            f"새 파일 {new_or_changed_count:,} / 스킵 {progress.skipped:,} / "
            f"삭제 {len(deleted):,}"
        )
        logger.info(progress.message)
        if progress_cb:
            progress_cb(progress)

    def _phase2_metadata(self, db: Database, progress: IndexProgress,
                         progress_cb: Optional[Callable]):
        progress.phase = IndexProgress.PHASE_META

        # 이전 Phase2 잔재 prefix 제거 — 새로 시작하는 작업은 깨끗하게.
        # (이미 remove 된 라이브러리의 prefix 는 audio_files 에서 행이 없어 work_pool 에 안 들어옴)
        self.reset_cancelled_prefixes()

        # 1. 분석 대기 항목 로드 (Single-Fetch)
        t_fetch = time.monotonic()
        conn = db._connect()
        try:
            cur = conn.cursor()
            target_ids = list(dict.fromkeys(getattr(progress, "phase2_file_ids", []) or []))
            run_id = getattr(progress, "phase2_run_id", "") or ""
            if not run_id and getattr(progress, "phase2_use_active_run", False):
                run_id = db.get_meta("active_phase2_run_id") or ""
                progress.phase2_run_id = run_id
            # 대량(전량/첫 인덱싱) 또는 표적 없음 → run_id 단일 쿼리.
            # 복합 인덱스 idx_meta_phase2_run(meta_extracted, phase2_run_id) 를 타서 한 번에
            # 스캔. 이전엔 target_ids 를 900개씩 수백~천 번 id IN 조회 → 대형 DB 에서 랜덤
            # PK 조회가 몰려 5~9분 걸리던 병목. Phase1 이 이 run_id 를 신규/변경 행에 스탬프
            # 하므로 결과 집합은 id IN 방식과 동일.
            LARGE_TARGET = 5000
            if run_id and (not target_ids or len(target_ids) > LARGE_TARGET):
                cur.execute(
                    "SELECT id, file_path, file_size, modified_at "
                    "FROM audio_files WHERE meta_extracted = 0 "
                    "AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0 AND phase2_run_id = ?",
                    (run_id,),
                )
                work_pool = [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
            elif target_ids:
                # 소규모 표적(범위 한정 재시도 등) — id IN 청크 (소량이라 빠름)
                work_pool = []
                CHUNK_IDS = 900
                for i in range(0, len(target_ids), CHUNK_IDS):
                    chunk = target_ids[i:i + CHUNK_IDS]
                    placeholders = ",".join("?" for _ in chunk)
                    cur.execute(
                        "SELECT id, file_path, file_size, modified_at "
                        "FROM audio_files WHERE meta_extracted = 0 "
                        f"AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0 AND id IN ({placeholders})",
                        chunk,
                    )
                    work_pool.extend((r[0], r[1], r[2], r[3]) for r in cur.fetchall())
            elif getattr(progress, "phase2_restrict_to_run", False):
                work_pool = []
            else:
                cur.execute(
                    "SELECT id, file_path, file_size, modified_at "
                    "FROM audio_files WHERE meta_extracted = 0 AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0"
                )
                work_pool = [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]
        finally:
            conn.close()

        pending_total = len(work_pool)
        # ── 세션 분모 (index_meta.meta_session_total) ──────────────────────────
        # 백그라운드 메타 분석은 조각으로 나뉘어 중단·재시작을 반복하고, 매번 이
        # 함수가 새로 불린다. 분모를 그때그때 pending 으로 잡으면 **분모가 계속
        # 줄고 %가 조각 기준**이 된다 (사용자 신고 2026-09-07).
        # 원본 정책(main_window.py:5337) 을 그대로 쓰되 **DB 에 저장**해 앱을 강제
        # 종료하고 다시 켜도 처음 기준이 이어지게 한다 (사용자 결정 2026-09-07).
        #   · 대기수가 저장된 분모 이하 → 분모 유지 (진행이므로 줄지 않는다)
        #   · 대기수가 분모를 넘음(파일 추가/미완료 리셋) → 새 대기수로 갱신
        # ⚠ 라이브러리를 제거하면 대기수가 줄어드는데 분모는 그대로여서 완료 수가
        #   실제보다 커 보인다. 제거/완전삭제 경로에서 이 키를 지운다
        #   (clear_meta_session_total).
        try:
            saved_total = int(db.get_meta(META_SESSION_TOTAL_KEY) or 0)
        except (TypeError, ValueError):
            saved_total = 0
        if saved_total > 0 and pending_total <= saved_total:
            session_total = saved_total
        else:
            session_total = pending_total
            db.set_meta(META_SESSION_TOTAL_KEY, str(session_total))
        progress.total = session_total
        progress.base_done = max(0, session_total - pending_total)
        progress.indexed = 0
        progress.meta_start_time = time.monotonic()
        progress.message = f"오디오 분석 시작: {pending_total:,}개"
        logger.info(f"Phase2 시작 — pending_total={pending_total:,} (로드: {time.monotonic()-t_fetch:.2f}s)")

        if progress_cb:
            progress_cb(progress)
        if pending_total == 0:
            run_id = getattr(progress, "phase2_run_id", "") or ""
            if run_id and (db.get_meta("active_phase2_run_id") or "") == run_id:
                db.set_meta("active_phase2_run_id", "")
            # 대기 0 = 세션 자연 완료 → 다음 세션이 옛 분모를 물려받지 않게 지운다
            db.set_meta(META_SESSION_TOTAL_KEY, "")
            logger.info("Phase2 즉시 종료 — pending 0")
            return

        from queue import Queue, Empty
        from threading import Thread

        # Phase2 동안 search_fts 쓰기를 defer — 종료 시 1회 bulk INSERT.
        # FTS5 trigram INSERT OR REPLACE 가 일반 INSERT 대비 10~30배 느려서
        # 5000 묶음 매번 실행 시 누적 비용이 큼. 단점: Phase2 진행 중 메타
        # 키워드 검색 stale (파일명 검색은 Phase1 entry 로 정상).
        touched_ids: list = []  # FTS bulk 반영 대상 (메타 있는 파일만)

        # DB Writer (Consumer) — 5000 묶음 단일 트랜잭션, FTS 는 defer
        db_queue = Queue(maxsize=8)
        def db_writer():
            # OS thread priority BELOW_NORMAL — WAL fsync 가 UI reader 굶기는 거 완화
            _set_thread_priority_below_normal()
            while True:
                item = db_queue.get()
                if item is None:
                    break
                batch_files, failed_map, failed_id_map = item
                try:
                    if failed_map:
                        db.mark_metadata_failed(failed_map, id_map=failed_id_map)
                    if batch_files:
                        db.upsert_files(batch_files, defer_fts=True)
                except Exception as e:
                    logger.error(f"DB Writer 오류: {e}")
                db_queue.task_done()

        writer_thread = Thread(target=db_writer, daemon=True)
        writer_thread.start()

        # Phase2 = SMB I/O bound. 워커 스레드로 네트워크 레이턴시를 은폐한다.
        # 단일 클라이언트 실측(Sound Morph NAS, 12k 표본): 처리량 곡선이 32스레드에서
        # 평평해짐 — 16→32 +12%, 32→64→128 은 이득 0(128은 오히려 미세 저하).
        # 30명 동시 환경에선 128×30=3,840 동시요청이 NAS 과부하/타임아웃 유발 →
        # 32 로 낮춰 동시요청 960 으로 줄임(개인 처리량 손실 ~0). 상한 128 유지.
        WORKERS = max(8, min(self.worker_threads, 128))
        CHUNK_FLUSH = 5000

        logger.info(
            f"Phase2: ThreadPool {WORKERS}t streaming (flush {CHUNK_FLUSH}, prefetch 1MB)"
        )

        work_q = Queue(maxsize=WORKERS * 4)
        result_q = Queue(maxsize=WORKERS * 8)
        SENTINEL = (None, None, None, None)
        # 라이브러리 삭제 시 워커가 즉시 skip 한 결과를 표시하는 마커.
        # consumer 에서 processed +=1 만 하고 batch/통계엔 안 넣음.
        CANCELLED_MARKER = object()

        def worker():
            # OS thread priority BELOW_NORMAL — UI starvation 방지.
            # I/O bound (open + read) 라 throughput 영향 거의 없음.
            _set_thread_priority_below_normal()
            # 항목 1개 = 결과 1개 보장이 consumer 종료 조건(processed == total)의
            # 전제. 예외로 결과가 유실되면 consumer 가 영원히 대기하므로, 항목
            # 처리 전 구간(취소 pre-check 포함)을 try 로 감싸 어떤 예외든 오류
            # 결과로 변환한다. 스레드 자체가 죽는 최후의 경우는 바깥 try 가
            # 로그를 남기고, consumer 의 생존 점검이 무한 대기를 막는다.
            try:
                while True:
                    item = work_q.get()
                    if item is SENTINEL:
                        work_q.task_done()
                        break
                    fid, p, sz, mt = item
                    try:
                        # 라이브러리 단위 cancel pre-check — snapshot 비어있으면 ~150ns 단축
                        snap = self._cancelled_snapshot
                        if snap:
                            plow = p.lower()
                            cancelled = False
                            for pre in snap:
                                if plow.startswith(pre):
                                    cancelled = True
                                    break
                            if cancelled:
                                result_q.put((fid, p, CANCELLED_MARKER, None))
                                continue
                        af, err = extract(p, sz, mt, fid)
                        result_q.put((fid, p, af, err)) # fid 도 함께 반환하여 재계산 방지
                    except Exception as e:
                        try:
                            result_q.put((fid, p, None, f"{type(e).__name__}: {e}"))
                        except Exception:
                            pass  # put 마저 실패(메모리 고갈 등) — 생존 점검에 맡김
                    finally:
                        work_q.task_done()
            except Exception:
                logger.exception("Phase2 워커 스레드 비정상 종료")

        # 워커 시작
        worker_threads_list = []
        for _ in range(WORKERS):
            t = Thread(target=worker, daemon=True)
            t.start()
            worker_threads_list.append(t)

        # Feeder — work_pool 을 work_q 로 펌프 (자체 스레드)
        # feeder_stop: 비정상 중단(워커 전멸 등) 시 cancel 과 별개로 펌프를 멈추는
        # 플래그 — 소비자 없는 큐에 계속 put 하다 영구 블록되는 것 방지.
        feeder_stop = False

        def feeder():
            for item in work_pool:
                if self._cancel.is_set() or feeder_stop:
                    break
                work_q.put(item)
            # 종료 sentinel
            for _ in range(WORKERS):
                work_q.put(SENTINEL)

        feeder_thread = Thread(target=feeder, daemon=True)
        feeder_thread.start()

        # 메인 consumer 루프
        current_batch = []
        current_failed = {}
        current_failed_ids = {} # { path -> fid }
        processed = 0
        last_emit = 0.0
        last_log = time.monotonic()
        last_log_count = 0

        aborted = False
        last_result_t = time.monotonic()
        try:
            while processed < pending_total:
                if self._cancel.is_set():
                    break
                try:
                    fid, p, af, err = result_q.get(timeout=0.5)
                except Empty:
                    # 진행률/로그만 갱신
                    now = time.monotonic()
                    if now - last_log >= 2.0:
                        rate = (processed - last_log_count) / (now - last_log)
                        logger.info(f"Phase2: {processed:,}/{pending_total:,} ({rate:.0f} f/s)")
                        last_log = now
                        last_log_count = processed
                    # ── 무한 대기 방지 (0.5s 폴링 시에만 검사 — 정상 경로 비용 0) ──
                    # 종료 조건이 processed == total 이라, 워커가 결과를 못 남기고
                    # 죽으면 이 루프는 영원히 안 끝남. 두 경우만 안전하게 중단:
                    # (1) 워커 전멸 + 결과 큐 비어있음 → 더 올 결과가 물리적으로 없음.
                    #     (정상 완주 시엔 total 도달로 루프를 먼저 빠져나가므로,
                    #      여기 걸렸다 = 항목이 유실됐다는 뜻. 오탐 없음)
                    if not any(t.is_alive() for t in worker_threads_list):
                        if result_q.empty():
                            logger.error(
                                f"Phase2 중단 — 워커 스레드 전멸 "
                                f"(처리 {processed:,}/{pending_total:,}). "
                                "남은 파일은 pending 유지 → 다음 분석에서 재시도."
                            )
                            aborted = True
                            break
                    # (2) 결과 정체 300s + 피더 완료 + 양쪽 큐 비어있음 → 결과 유실
                    #     추정. 300s 인 이유: 초대형 파일의 SMB 풀리드(수분)가
                    #     정당하게 결과 공백을 만들 수 있어 보수적으로. 오탐이어도
                    #     해당 파일들은 pending 유지라 비파괴적.
                    elif (now - last_result_t >= 300.0
                          and not feeder_thread.is_alive()
                          and work_q.empty() and result_q.empty()):
                        logger.error(
                            f"Phase2 중단 — 결과 300s 정체 "
                            f"(처리 {processed:,}/{pending_total:,}, 유실 추정). "
                            "남은 파일은 pending 유지 → 다음 분석에서 재시도."
                        )
                        aborted = True
                        break
                    continue

                last_result_t = time.monotonic()
                processed += 1
                # 1) 워커가 이미 skip 한 cancelled — 통계 X
                if af is CANCELLED_MARKER:
                    continue
                # 2) race: 워커 pre-check 이후 cancel_prefix 등록된 in-flight 결과 격리
                #    (대부분 빈 snapshot → 1회 attribute lookup + bool 만)
                snap = self._cancelled_snapshot
                if snap:
                    plow = p.lower()
                    cancelled_late = False
                    for pre in snap:
                        if plow.startswith(pre):
                            cancelled_late = True
                            break
                    if cancelled_late:
                        continue
                if af is None:
                    progress.errors += 1
                    current_failed[p] = err or "알 수 없는 오류"
                    current_failed_ids[p] = fid
                else:
                    current_batch.append(af)
                    progress.indexed += 1
                    # 메타 있는 파일만 FTS bulk 대상에 추가 (메타 없는 SFX 류는 Phase1 entry 유지)
                    if af.title or af.artist or af.album or af.genre or af.comments \
                            or af.description or af.keywords or af.category \
                            or af.sub_category or af.source:
                        touched_ids.append(af.file_id)

                if len(current_batch) + len(current_failed) >= CHUNK_FLUSH:
                    db_queue.put((current_batch, current_failed, current_failed_ids))
                    current_batch = []
                    current_failed = {}
                    current_failed_ids = {}

                now = time.monotonic()
                if progress_cb and now - last_emit >= 0.3:
                    progress.message = (
                        f"분석: {progress.indexed + progress.errors:,}/"
                        f"{progress.total:,} (오류 {progress.errors:,})"
                    )
                    progress_cb(progress)
                    last_emit = now
                if now - last_log >= 2.0:
                    rate = (processed - last_log_count) / (now - last_log)
                    logger.info(f"Phase2: {processed:,}/{pending_total:,} ({rate:.0f} f/s)")
                    last_log = now
                    last_log_count = processed

            if current_batch or current_failed:
                db_queue.put((current_batch, current_failed, current_failed_ids))


        finally:
            # cancel/비정상 중단 시 워커들이 빠르게 종료되도록 큐 비우고 sentinel 주입.
            # aborted 시 feeder_stop 으로 피더도 멈춤 (드레인이 공간을 만들어 블록 해제).
            if self._cancel.is_set() or aborted:
                feeder_stop = True
                if aborted and progress_cb:
                    progress.message = "분석 중단(워커 오류) — 다음 실행에서 이어서 진행"
                    progress_cb(progress)
                # work_q 비우기 (feeder 가 막혔을 수 있음)
                try:
                    while True:
                        work_q.get_nowait()
                        work_q.task_done()
                except Empty:
                    pass
                for _ in range(WORKERS):
                    try:
                        work_q.put_nowait(SENTINEL)
                    except Exception:
                        pass
            db_queue.put(None)
            writer_thread.join(timeout=30)
            elapsed = time.monotonic() - progress.meta_start_time
            rate = progress.indexed / elapsed if elapsed > 0 else 0
            logger.info(
                f"Phase2 종료 — 처리 {progress.indexed:,}, "
                f"오류 {progress.errors:,} ({elapsed:.1f}s, {rate:.0f} f/s)"
            )
            # defer 된 FTS bulk 반영 — cancel 여부와 무관, 이미 audio_files 에는
            # 메타가 들어가 있으므로 검색 정합성 회복 필요.
            if touched_ids:
                t0 = time.monotonic()
                progress.message = f"검색 인덱스 갱신 중: {len(touched_ids):,}개"
                if progress_cb:
                    progress_cb(progress)
                try:
                    def _fts_cb(done, total):
                        progress.message = f"검색 인덱스 갱신 {done:,}/{total:,}"
                        if progress_cb:
                            progress_cb(progress)
                    n_fts = db.bulk_fts_upsert_from_audio_files(
                        touched_ids, chunk=5000, progress_cb=_fts_cb
                    )
                    logger.info(
                        f"Phase2 FTS bulk 반영 완료: {n_fts:,}행 "
                        f"({time.monotonic()-t0:.1f}s)"
                    )
                except Exception as e:
                    logger.exception(f"FTS bulk 반영 실패: {e}")
            run_id = getattr(progress, "phase2_run_id", "") or ""
            if run_id and not self._cancel.is_set():
                conn = db._connect()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM audio_files "
                        "WHERE meta_extracted = 0 AND phase2_run_id = ?",
                        (run_id,),
                    )
                    remaining_for_run = int(cur.fetchone()[0] or 0)
                finally:
                    conn.close()
                if remaining_for_run == 0 and (db.get_meta("active_phase2_run_id") or "") == run_id:
                    db.set_meta("active_phase2_run_id", "")
                    # 이 번호표의 작업이 다 끝났다 → 세션 분모도 정리.
                    # (다른 번호표의 미완료가 남아 있으면 다음 세션이 새 분모를 잡는다)
                    db.set_meta(META_SESSION_TOTAL_KEY, "")
