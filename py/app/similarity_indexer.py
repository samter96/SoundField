"""Phase3 — 유사 사운드 검색용 음향 특징 배치 추출.

Phase1(파일 목록) / Phase2(메타) 에 이어지는 세 번째 단계.
Phase2 는 mutagen 으로 헤더만 읽어 오디오를 디코딩하지 않으므로 합칠 수 없다.

**비용 구조 (실측 2026-09-10)** — 3초 창 하나당 로컬 92ms / NAS 257ms 이고 그중
특징 계산은 3~9ms 다. 즉 **전부 I/O**이며 창 수를 늘리는 것이 곧 비용이다.
그래서 파일당 창 수를 최대 4개로 묶는다.

**취소 즉시 응답** (프로젝트 룰 — Phase1/Phase2 와 동일한 규칙을 지킨다)
- 작업 투입 루프와 결과 수집 루프에서 `self._cancel.is_set()` 을 매번 확인
- `ThreadPoolExecutor` 는 `with` 대신 명시적 `shutdown(wait=False, cancel_futures=True)`
- 취소 시 **이미 뽑은 것은 저장하고** 남은 것은 대기 상태로 남긴다 (재실행이 이어받음)
- 취소 시 세션 분모(`sim_total_eligible`)를 지우지 않는다 — 진행률 기준을 보존
"""
from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from app.database import Database
from app.library_manager import _set_thread_priority_below_normal
from app.similarity import (
    FEATURE_DIM, MAX_WINDOWS, WINDOW_SEC, features_from_region, is_degenerate,
)

logger = logging.getLogger(__name__)

# 이 길이를 넘으면 대목별로 따로 저장한다 (사용자 결정 2026-09-10).
# 5분 앰비언스를 한 벡터로 요약하면 여러 소리가 뭉개져 유사도가 부정확해진다.
LONG_FILE_SEC = 60.0

DEFAULT_WORKERS = 32      # 인덱싱 워커 수 — 기존 Phase1/2 와 동일 (실측 확정값)
FETCH_SIZE = 4000         # 한 번에 DB 에서 꺼내는 대기 항목 수
SAVE_BATCH = 400          # 이만큼 모이면 한 트랜잭션으로 저장
EMIT_INTERVAL = 0.5       # 진행률 콜백 최소 간격(초)


@dataclass
class SimilarityProgress:
    """진행률 표시용 상태."""
    total: int = 0             # 이번 세션 분모 (DB 캐시값)
    base_done: int = 0         # 세션 시작 시점에 이미 완료돼 있던 수
    done: int = 0              # 이번 실행에서 처리한 수
    failed: int = 0
    vectors: int = 0           # 이번 실행에서 만든 벡터 행 수
    message: str = ""
    cancelled: bool = False
    started_at: float = field(default_factory=time.monotonic)

    @property
    def processed(self) -> int:
        return self.base_done + self.done

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int(self.processed * 100 / self.total))

    @property
    def eta_seconds(self) -> Optional[float]:
        """남은 예상 시간. 이번 실행의 실제 처리 속도로만 계산한다."""
        if self.done < 20:
            return None       # 표본이 적으면 값이 튄다 — 산정 중으로 표시
        elapsed = time.monotonic() - self.started_at
        rate = self.done / elapsed if elapsed > 0 else 0
        if rate <= 0:
            return None
        remaining = max(0, self.total - self.processed)
        return remaining / rate


def _windows_for(path: str, duration: Optional[float]) -> List[Tuple[int, float, float, bytes]]:
    """파일 하나에서 저장할 행들을 만든다.

    - 60초 이하: 창 여러 개를 평균해 **한 행**(win=0, 파일 전체 구간)
    - 60초 초과: 창별로 **여러 행**(win=0..n, 각자 구간)
    무음 등으로 쓸 만한 벡터가 없으면 빈 목록 — 호출부가 '완료(벡터 없음)' 로 적는다.

    ⚠ 길이 판정에 **DB duration 을 믿지 않는다.** 실측(2026-09-10)에서 일부 mp3 의
    DB 길이가 10.9초로 적혀 있는데 실제는 62.6초였다(mutagen 오독). DB 값을 쓰면
    62초 파일이 짧은 파일로 취급돼 한 벡터로 뭉개진다. 파일에서 읽은 실제 길이를 쓴다.
    """
    res = features_from_region(path, 0.0, None,
                               window_sec=WINDOW_SEC, max_windows=MAX_WINDOWS)
    dur = float(res.file_sec or duration or 0.0)
    if res.windows.shape[0] == 0:
        return []

    if dur > LONG_FILE_SEC and res.windows.shape[0] > 1:
        rows = []
        for i, (vec, span) in enumerate(zip(res.windows, res.spans)):
            if is_degenerate(vec):
                continue
            rows.append((i, float(span[0]), float(span[1]),
                         np.asarray(vec, dtype=np.float16).tobytes()))
        return rows

    if is_degenerate(res.vector):
        return []
    end = dur if dur > 0 else (res.spans[-1][1] if res.spans else res.analyzed_sec)
    return [(0, 0.0, float(end), np.asarray(res.vector, dtype=np.float16).tobytes())]


class SimilarityIndexer:
    """대기 중인 파일들의 음향 특징을 뽑아 DB 에 채운다."""

    def __init__(self, db_path: str, workers: int = DEFAULT_WORKERS):
        self.db_path = str(db_path)
        self.workers = max(1, int(workers))
        self._cancel = Event()
        self._running = False

    def cancel(self) -> None:
        self._cancel.set()

    def is_running(self) -> bool:
        return self._running

    def run(self, progress_cb: Optional[Callable[[SimilarityProgress], None]] = None,
            max_files: Optional[int] = None) -> Dict:
        """대기 항목을 처리한다.

        max_files: 이 개수만 처리하고 멈춘다 (표본 시험용). None 이면 전부.
        반환: {처리, 실패, 벡터, 취소, 소요초}
        """
        self._cancel.clear()
        self._running = True
        db = Database(self.db_path)
        progress = SimilarityProgress()
        try:
            total = db.refresh_similarity_total()
            snapshot = db.get_similarity_progress()
            progress.total = total
            progress.base_done = snapshot["done"]
            progress.failed = 0
            progress.message = f"음향 특징 추출 시작: 대기 {snapshot['pending']:,}개"
            logger.info("Phase3 시작 — 대기 %s / 총 %s (워커 %s)",
                        f"{snapshot['pending']:,}", f"{total:,}", self.workers)
            if progress_cb:
                progress_cb(progress)
            if snapshot["pending"] == 0:
                progress.message = "추출할 파일이 없습니다"
                if progress_cb:
                    progress_cb(progress)
                return self._result(progress)

            pool = ThreadPoolExecutor(
                max_workers=self.workers,
                initializer=_set_thread_priority_below_normal,
            )
            run_id = uuid.uuid4().hex[:12]
            pending_save: Dict[str, List[tuple]] = {}
            pending_fail: Dict[str, str] = {}
            last_emit = 0.0
            try:
                while not self._cancel.is_set():
                    batch = db.get_paths_for_similarity(limit=FETCH_SIZE)
                    if not batch:
                        break
                    if max_files is not None:
                        room = max_files - progress.done
                        if room <= 0:
                            break
                        batch = batch[:room]

                    futures = {}
                    for fid, path, duration in batch:
                        if self._cancel.is_set():
                            break
                        futures[pool.submit(_windows_for, path, duration)] = (fid, path)

                    # as_completed 를 쓰지 않는다 — 취소 시 즉시 빠져나오려면
                    # 완료 여부를 짧은 간격으로 직접 확인해야 한다.
                    remaining = dict(futures)
                    while remaining:
                        if self._cancel.is_set():
                            # 취소해도 **이미 계산이 끝난 것은 걷어간다** — 버리면
                            # NAS 를 다시 읽어야 하고, 그게 이 작업의 비용 전부다.
                            for fut, (fid, _p) in list(remaining.items()):
                                if not fut.done() or fut.cancelled():
                                    continue
                                try:
                                    rows = fut.result()
                                except Exception as e:
                                    pending_fail[fid] = f"{type(e).__name__}: {e}"
                                else:
                                    pending_save[fid] = rows
                                    progress.vectors += len(rows)
                            break
                        finished = [f for f in remaining if f.done()]
                        if not finished:
                            time.sleep(0.05)
                            continue
                        for fut in finished:
                            fid, path = remaining.pop(fut)
                            try:
                                rows = fut.result()
                            except Exception as e:
                                pending_fail[fid] = f"{type(e).__name__}: {e}"
                                continue
                            pending_save[fid] = rows
                            progress.vectors += len(rows)

                        if len(pending_save) >= SAVE_BATCH:
                            progress.done += db.save_similarity_vectors(pending_save, run_id)
                            pending_save.clear()
                        if len(pending_fail) >= SAVE_BATCH:
                            progress.failed += db.mark_similarity_failed(pending_fail, run_id)
                            pending_fail.clear()

                        now = time.monotonic()
                        if progress_cb and now - last_emit >= EMIT_INTERVAL:
                            last_emit = now
                            eta = progress.eta_seconds
                            progress.message = (
                                f"음향 특징 추출 {progress.processed:,}/{progress.total:,} "
                                f"({progress.percent}%)"
                                + (f" · 남은 시간 {self._fmt_eta(eta)}" if eta else " · 남은 시간 산정 중")
                            )
                            progress_cb(progress)

                    # ⚠ 배치 경계에서 반드시 비운다. 미뤄두면 그 파일들이 아직
                    # sim_extracted=0 이라 다음 get_paths_for_similarity 가 같은 것을
                    # 또 꺼내 무한 루프가 된다 (개발 중 실제로 발생).
                    if pending_save:
                        progress.done += db.save_similarity_vectors(pending_save, run_id)
                        pending_save.clear()
                    if pending_fail:
                        progress.failed += db.mark_similarity_failed(pending_fail, run_id)
                        pending_fail.clear()

                    if max_files is not None and progress.done >= max_files:
                        break
            finally:
                # with 문 금지 — 취소 요청에 즉시 응답하려면 대기하지 않고 닫는다
                pool.shutdown(wait=False, cancel_futures=True)

            # 취소든 완료든, 이미 뽑아둔 결과는 반드시 저장한다 (재실행 비용 절약)
            if pending_save:
                progress.done += db.save_similarity_vectors(pending_save, run_id)
            if pending_fail:
                progress.failed += db.mark_similarity_failed(pending_fail, run_id)

            progress.cancelled = self._cancel.is_set()
            if progress.cancelled:
                progress.message = (
                    f"추출 취소 — {progress.done:,}개 저장됨, 나머지는 다음 실행에서 이어집니다"
                )
            else:
                progress.message = (
                    f"음향 특징 추출 완료 — {progress.done:,}개 처리"
                    + (f", {progress.failed:,}개 실패" if progress.failed else "")
                )
            logger.info("Phase3 종료 — %s", progress.message)
            if progress_cb:
                progress_cb(progress)
            return self._result(progress)
        finally:
            self._running = False

    @staticmethod
    def _fmt_eta(seconds: Optional[float]) -> str:
        if not seconds or seconds <= 0:
            return "산정 중"
        s = int(seconds)
        if s < 60:
            return f"{s}초"
        if s < 3600:
            return f"{s // 60}분"
        return f"{s // 3600}시간 {(s % 3600) // 60}분"

    @staticmethod
    def _result(p: SimilarityProgress) -> Dict:
        return {
            "processed": p.done,
            "failed": p.failed,
            "vectors": p.vectors,
            "cancelled": p.cancelled,
            "elapsed": round(time.monotonic() - p.started_at, 1),
            "message": p.message,
        }
