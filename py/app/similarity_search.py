"""유사 사운드 검색 질의 경로 — 벡터 상주 + 상위 N 조회.

**상주 방식 (사용자 결정 2026-09-10)**: float16 으로 들고 있는다.
실측 200만 행 기준 float16 상주 332MB·질의 213ms / float32 상주 664MB·질의 21ms 였고,
버튼 한 번에 0.2초는 체감상 짧은 대기라 메모리를 아끼는 쪽을 골랐다.

**적재 시 z-정규화 + L2 정규화까지 끝내둔다.** 원값끼리 코사인을 재면 스펙트럴
센트로이드(수천 Hz 단위)가 값을 독점해 톤과 잡음도 0.999 가 나온다.
정규화를 질의마다 하면 낭비이므로 적재 시 한 번만 한다.

**순위 = 음향 유사도 + 카테고리 결합.** 음향만 쓰면 "UI 클릭 ↔ 총성" 처럼
짧은 어택·광대역이라는 이유로 의미가 다른 소리가 붙는다 (실측 2026-09-10).
후보 상위 K개만 DB 에서 메타데이터를 읽어 재정렬하므로 비용이 거의 없다.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

from app.similarity import FEATURE_DIM, Normalizer

logger = logging.getLogger(__name__)

# 가중치 — **잠정값이다.** 귀로 듣고 조정해야 한다 (계획서 §8).
# 합이 1.0 이 되게 유지할 것. 음향 쪽을 0 으로 두면 그냥 카테고리 검색이 된다.
W_ACOUSTIC = 0.75
W_META = 0.25

# 카테고리 결합 세부 배점 (meta 점수는 0~1 로 정규화된다)
META_CATEGORY = 0.5       # UCS 카테고리 일치
META_SUB_CATEGORY = 0.3   # 서브 카테고리 일치
META_KEYWORD = 0.2        # 키워드/설명 낱말 교집합

CANDIDATE_K = 3000        # 음향 점수로 먼저 좁히는 후보 수
CONVERT_BLOCK = 500_000   # float16 → float32 변환 블록 (메모리 첨두 억제)


@dataclass(frozen=True)
class SimilarHit:
    file_id: str
    score: float              # 최종 점수 (0~1 로 눌러 표시용)
    acoustic: float           # 음향 유사도 (-1~1)
    meta: float               # 카테고리 결합 점수 (0~1)
    start_sec: float          # 이 파일에서 실제로 맞은 대목
    end_sec: float
    win: int


def _tokens(*texts: Optional[str]) -> set:
    """낱말 집합 — 카테고리 결합의 키워드 비교용."""
    out = set()
    for t in texts:
        if not t:
            continue
        for raw in str(t).replace("_", " ").replace("-", " ").replace(",", " ").split():
            w = raw.strip().lower()
            if len(w) >= 2:
                out.add(w)
    return out


class SimilarityIndex:
    """DB 의 벡터를 메모리에 올려두고 질의를 받는다.

    상주 프로세스(sf_query)에서 한 번 만들어 계속 쓴다. `load()` 는 스레드 안전하고
    이미 올라와 있으면 아무것도 하지 않는다.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._matrix: Optional[np.ndarray] = None      # (n, DIM) float16, 정규화 완료
        self._file_ids: List[str] = []
        self._wins: Optional[np.ndarray] = None
        self._starts: Optional[np.ndarray] = None
        self._ends: Optional[np.ndarray] = None
        self._normalizer: Optional[Normalizer] = None
        self._loaded_at: float = 0.0

    # ------------------------------------------------------------ 적재

    @property
    def is_loaded(self) -> bool:
        return self._matrix is not None

    @property
    def row_count(self) -> int:
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    def unload(self) -> None:
        with self._lock:
            self._matrix = None
            self._file_ids = []
            self._wins = self._starts = self._ends = None
            self._normalizer = None

    def load(self, force: bool = False) -> Dict:
        """벡터를 전부 읽어 정규화까지 마친 상태로 상주시킨다.

        실측(200만 행): DB 읽기 2.2초. 정규화 통계는 이 자리에서 계산한다 —
        캐시해두면 말뭉치가 바뀔 때 낡아서 점수가 어긋나는 실패 모드가 생긴다.
        """
        with self._lock:
            if self._matrix is not None and not force:
                return {"rows": self.row_count, "reused": True}
            t0 = time.perf_counter()
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT file_id, win, start_sec, end_sec, vec FROM similarity_vectors"
                ).fetchall()
            finally:
                conn.close()
            t_read = time.perf_counter() - t0

            if not rows:
                self._matrix = np.zeros((0, FEATURE_DIM), dtype=np.float16)
                self._file_ids = []
                self._wins = np.zeros(0, dtype=np.int32)
                self._starts = np.zeros(0, dtype=np.float32)
                self._ends = np.zeros(0, dtype=np.float32)
                self._normalizer = None
                self._loaded_at = time.time()
                return {"rows": 0, "reused": False, "read_sec": round(t_read, 2)}

            raw = np.frombuffer(b"".join(r[4] for r in rows),
                                dtype=np.float16).reshape(-1, FEATURE_DIM)
            self._file_ids = [r[0] for r in rows]
            self._wins = np.fromiter((r[1] for r in rows), dtype=np.int32, count=len(rows))
            self._starts = np.fromiter((r[2] for r in rows), dtype=np.float32, count=len(rows))
            self._ends = np.fromiter((r[3] for r in rows), dtype=np.float32, count=len(rows))

            # 정규화 통계는 float32 로 계산 (float16 누적은 오차가 크다)
            f32 = raw.astype(np.float32)
            self._normalizer = Normalizer(f32.mean(axis=0), f32.std(axis=0))
            z = self._normalizer.apply(f32)
            z /= (np.linalg.norm(z, axis=1, keepdims=True) + 1e-9)
            self._matrix = z.astype(np.float16)
            del f32, z
            self._loaded_at = time.time()
            took = time.perf_counter() - t0
            logger.info("유사도 벡터 적재 — %s행 %.0fMB (%.1f초, 읽기 %.1f초)",
                        f"{len(rows):,}", self._matrix.nbytes / 2 ** 20, took, t_read)
            return {"rows": len(rows), "reused": False,
                    "read_sec": round(t_read, 2), "total_sec": round(took, 2),
                    "bytes": int(self._matrix.nbytes)}

    # ------------------------------------------------------------ 질의

    def _acoustic_scores(self, query_vec: np.ndarray) -> np.ndarray:
        """정규화된 질의 1개 vs 전체 — float16 상주분을 블록 단위로 변환해 곱한다."""
        assert self._matrix is not None and self._normalizer is not None
        q = self._normalizer.apply(np.asarray(query_vec, dtype=np.float32).reshape(1, -1))[0]
        q /= (np.linalg.norm(q) + 1e-9)
        n = self._matrix.shape[0]
        out = np.empty(n, dtype=np.float32)
        for s in range(0, n, CONVERT_BLOCK):
            block = self._matrix[s:s + CONVERT_BLOCK]
            out[s:s + block.shape[0]] = block.astype(np.float32) @ q
        return out

    def search(self, query_vec: np.ndarray, *, limit: int = 200,
               exclude_file_id: Optional[str] = None,
               query_meta: Optional[Dict[str, Optional[str]]] = None,
               weights: Optional[Tuple[float, float]] = None,
               candidate_k: int = CANDIDATE_K) -> List[SimilarHit]:
        """유사한 파일을 점수 순으로 돌려준다.

        query_vec: 사용자가 팝업에서 고른 영역의 87차원 벡터 (원값 — 정규화는 여기서)
        exclude_file_id: 질의로 쓴 파일 자체는 결과에서 뺀다 (기본 동작)
        query_meta: {category, sub_category, keywords, description} — 카테고리 결합용.
                    없으면 음향 점수만으로 정렬한다.
        weights: (음향, 메타) 가중치. 기본 (0.75, 0.25) — **귀 튜닝 필요한 잠정값**
        """
        if not self.is_loaded:
            self.load()
        if self.row_count == 0:
            return []

        w_ac, w_meta = weights if weights else (W_ACOUSTIC, W_META)
        if not query_meta:
            w_ac, w_meta = 1.0, 0.0

        scores = self._acoustic_scores(query_vec)

        # 파일마다 가장 잘 맞는 대목 하나만 남긴다 (긴 파일은 여러 행이 있다)
        best: Dict[str, int] = {}
        k = min(len(scores), max(candidate_k, limit * 4))
        order = np.argpartition(-scores, k - 1)[:k] if k < len(scores) else np.arange(len(scores))
        order = order[np.argsort(-scores[order])]
        for i in order:
            fid = self._file_ids[i]
            if fid == exclude_file_id:
                continue
            if fid not in best:
                best[fid] = int(i)

        if not best:
            return []

        # 메타 조회는 가중치와 무관하게 항상 한다 — 숨김·제거된 행도 벡터를 갖고
        # 있어서(삭제 경로에서만 지운다) 이 조회로 걸러내야 결과에 새지 않는다.
        meta_map = self._fetch_meta(best.keys())
        q_tokens = _tokens(query_meta.get("keywords"), query_meta.get("description")) \
            if query_meta else set()
        q_cat = (query_meta or {}).get("category") or ""
        q_sub = (query_meta or {}).get("sub_category") or ""

        hits: List[SimilarHit] = []
        for fid, idx in best.items():
            row = meta_map.get(fid)
            if row is None:
                # 숨김·제거·삭제된 파일. 벡터 행은 남아 있으므로(삭제 경로에서만
                # 지운다) 이 조회로 걸러내지 않으면 결과에 새어 나온다.
                continue
            ac = float(scores[idx])
            m = 0.0
            if w_meta > 0:
                if q_cat and row.get("category") and \
                        str(row["category"]).lower() == q_cat.lower():
                    m += META_CATEGORY
                if q_sub and row.get("sub_category") and \
                        str(row["sub_category"]).lower() == q_sub.lower():
                    m += META_SUB_CATEGORY
                if q_tokens:
                    other = _tokens(row.get("keywords"), row.get("description"),
                                    row.get("file_name"))
                    if other:
                        overlap = len(q_tokens & other) / len(q_tokens)
                        m += META_KEYWORD * min(1.0, overlap)
            # 음향 점수를 0~1 로 눌러 메타 점수와 같은 자에서 섞는다
            ac01 = (ac + 1.0) * 0.5
            hits.append(SimilarHit(
                file_id=fid, score=w_ac * ac01 + w_meta * m, acoustic=ac, meta=m,
                start_sec=float(self._starts[idx]), end_sec=float(self._ends[idx]),
                win=int(self._wins[idx]),
            ))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def search_region(self, path: str, start_sec: float = 0.0,
                      end_sec: Optional[float] = None, **kwargs) -> List[SimilarHit]:
        """파일의 특정 영역을 질의로 삼아 검색한다 — **질의 경로는 이걸 쓴다.**

        `features_from_region_full` 로 영역을 빠짐없이 듣는다. 색인용
        `features_from_region`(창 4개 표본)을 질의에 쓰면 결과가 달라진다 —
        실측(2026-09-10) 150초 이상 영역에서 상위 5개 중 55% 만 일치했다.
        그래서 올바른 경로를 기본으로 묶어 둔다.

        end_sec=None 은 팝업의 `전체 영역 선택` 버튼에 해당한다.
        """
        from app.similarity import features_from_region_full

        res = features_from_region_full(path, start_sec, end_sec)
        if res.windows.shape[0] == 0:
            return []
        return self.search(res.vector, **kwargs)

    def _fetch_meta(self, file_ids: Iterable[str]) -> Dict[str, Dict]:
        ids = list(file_ids)
        out: Dict[str, Dict] = {}
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                ph = ",".join(["?"] * len(chunk))
                for row in conn.execute(
                    f"SELECT id, file_name, category, sub_category, keywords, description "
                    f"FROM audio_files WHERE id IN ({ph}) "
                    f"AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0", chunk
                ):
                    out[row[0]] = {"file_name": row[1], "category": row[2],
                                   "sub_category": row[3], "keywords": row[4],
                                   "description": row[5]}
        finally:
            conn.close()
        return out
