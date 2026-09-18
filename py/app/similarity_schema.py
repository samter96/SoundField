"""유사 사운드 검색(Phase3)용 DB 스키마 — **배포 빌드에서 격리되는 모듈.**

사용자 결정 2026-09-14: 유사 검색은 개발용으로만 만들고 인스톨러에는 넣지 않는다.
기능은 물론이고 **DB 에 생기는 빈 표·컬럼조차 배포본에 두지 않는다.**

격리가 작동하는 방식
--------------------
`database.py` 는 이 모듈을 `try/except ImportError` 로 불러온다. 불러오지 못하면
`SIMILARITY_ENABLED = False` 가 되어 유사도 관련 SQL 을 **전부 건너뛴다**
(표 생성·컬럼 추가·색인·삭제 동기·upsert 의 재추출 표시).

배포본에서 import 가 실패하는 이유는 `sf_bridge.spec` 의 `excludes` 에
이 모듈과 `app.similarity*` 가 들어 있어 PyInstaller 가 실행 파일에 담지 않기 때문이다.
개발 중 소스 실행에서는 파일이 그대로 있으므로 정상 동작한다.

⚠ 여기 있는 코드는 **배포본에서 절대 실행되지 않는다**는 전제다.
  유사 검색과 무관한 스키마 변경을 이 파일에 넣지 말 것 — 배포본에 반영되지 않는다.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Iterable, List, Sequence

logger = logging.getLogger(__name__)

# 유사도 특징 규격 버전 — app/similarity.py 의 FEATURE_VERSION 과 반드시 같아야 한다.
# (여기서 import 하지 않는 이유: 이 모듈은 numpy 없이도 로드돼야 한다.
#  두 값이 어긋나면 tests/test_similarity_db.py 가 실패한다.)
SIMILARITY_FEATURE_VERSION = 1
SIMILARITY_VECTOR_DIM = 87
SIM_PENDING, SIM_DONE, SIM_FAILED = 0, 1, 2

# 60초 초과 파일은 대목별로 여러 행(win 0..n) — 5분 앰비언스의 특정 대목이
# 걸리게 하기 위한 것. 짧은 파일은 win=0 한 행.
# WITHOUT ROWID = file_id 로 뭉쳐 저장돼 파일 단위 삭제/교체가 싸다.
SIM_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS similarity_vectors (
        file_id TEXT NOT NULL,
        win INTEGER NOT NULL,
        start_sec REAL NOT NULL,
        end_sec REAL NOT NULL,
        vec BLOB NOT NULL,
        PRIMARY KEY (file_id, win)
    ) WITHOUT ROWID
"""

# audio_files 의 upsert(ON CONFLICT DO UPDATE) 에 끼워 넣는 조각.
# 파일이 바뀌면 음향도 바뀐다 → 유사도 특징도 재추출 큐로 되돌린다.
# 배포본에서는 이 조각이 통째로 빠져 컬럼 없는 DB 에서도 upsert 가 성립한다.
SIM_UPSERT_CLAUSE = """
                    sim_extracted=CASE
                        WHEN audio_files.modified_at IS NULL
                          OR audio_files.modified_at + 2.0 < excluded.modified_at
                        THEN 0
                        ELSE IFNULL(audio_files.sim_extracted, 0)
                    END,"""


def ensure_similarity_schema(conn: sqlite3.Connection, cols: Iterable[str]) -> None:
    """기존 DB 에 표·컬럼·색인을 만든다 (Phase2 의 meta_extracted 관례를 따른다).

    cols: audio_files 의 현재 컬럼 이름 모음 — 호출부가 이미 조회해둔 것을 받는다.
    sim_extracted 0=대기 / 1=완료 / 2=실패.
    """
    cols = set(cols)
    conn.execute(SIM_TABLE_SQL)
    for col, decl in (("sim_extracted", "INTEGER DEFAULT 0"),
                      ("sim_error", "TEXT"),
                      ("sim_run_id", "TEXT")):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE audio_files ADD COLUMN {col} {decl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # 이미 존재

    # 대기분만 담는 부분 색인 — 추출이 끝나면 거의 비어 158만 행 풀스캔을 막는다.
    # 필터에 쓰는 4개 컬럼을 모두 담아 COVERING INDEX 가 되게 한다.
    # 실측(2026-09-10, 실제 DB 사본 1,582,033행): 진행 집계가
    # 1,079ms(idx_meta_extracted 경유 + 테이블 조회) → 86ms(커버링).
    # 생성 1.6초 / DB +18MB.
    conn.execute("DROP INDEX IF EXISTS idx_sim_pending_v1")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_pending_v2 "
        "ON audio_files(sim_extracted, meta_extracted, hidden, removed) "
        "WHERE sim_extracted = 0"
    )
    # 실패분은 소수 — 별도 부분 색인으로 COUNT 0ms.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_failed_v1 "
        "ON audio_files(sim_extracted) WHERE sim_extracted = 2"
    )

    # 특징 규격이 바뀌면 저장된 벡터는 서로 비교할 수 없다 → 전부 버리고 재추출.
    try:
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key='sim_feature_version'"
        ).fetchone()
        stored = int(row[0]) if row and str(row[0]).isdigit() else 0
        if stored != SIMILARITY_FEATURE_VERSION:
            if stored:
                conn.execute("DELETE FROM similarity_vectors")
                conn.execute(
                    "UPDATE audio_files SET sim_extracted = 0, sim_error = NULL "
                    "WHERE IFNULL(sim_extracted,0) <> 0"
                )
                logger.info(
                    "유사도 특징 규격 %s → %s: 저장된 벡터 폐기 후 재추출 대기",
                    stored, SIMILARITY_FEATURE_VERSION,
                )
            conn.execute(
                "INSERT INTO index_meta (key,value) VALUES ('sim_feature_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SIMILARITY_FEATURE_VERSION),),
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def delete_vectors_by_path_like(cur: sqlite3.Cursor, pattern: str) -> None:
    """경로 접두사로 지우는 경로(라이브러리 제거 등)에서 벡터도 같이 지운다."""
    cur.execute(
        "DELETE FROM similarity_vectors WHERE file_id IN ("
        "  SELECT id FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE)",
        (pattern,)
    )


def delete_vectors_by_ids(cur: sqlite3.Cursor, file_ids: Sequence[str]) -> None:
    """파일 id 묶음으로 지우는 경로에서 벡터도 같이 지운다."""
    ids: List[str] = list(file_ids)
    if not ids:
        return
    ph = ",".join(["?"] * len(ids))
    cur.execute(f"DELETE FROM similarity_vectors WHERE file_id IN ({ph})", ids)
