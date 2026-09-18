import sqlite3
import logging
import hashlib
import os
import re
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Callable, Tuple
from dataclasses import dataclass, field

from app import thesaurus

logger = logging.getLogger(__name__)

# ── 유사 사운드 검색(Phase3) 격리 스위치 ──────────────────────────────────────
# 사용자 결정 2026-09-14: 유사 검색은 개발용으로만 두고 인스톨러에는 넣지 않는다.
# 배포본은 sf_bridge.spec 의 excludes 로 app.similarity_schema 를 담지 않으므로
# 아래 import 가 실패하고, 유사도 관련 SQL 이 전부 건너뛰어진다 —
# 빈 표·컬럼조차 사용자 DB 에 생기지 않는다. 자세한 내용은 app/similarity_schema.py.
# ⚠ 이 파일에 유사도 SQL 을 직접 되돌려 넣지 말 것. 격리가 깨진다.
try:
    from app import similarity_schema as _sim
    from app.similarity_schema import (                    # noqa: F401  (재수출)
        SIMILARITY_FEATURE_VERSION, SIMILARITY_VECTOR_DIM,
        SIM_PENDING, SIM_DONE, SIM_FAILED,
    )
    SIMILARITY_ENABLED = True
except ImportError:                                        # 배포 빌드
    _sim = None
    SIMILARITY_FEATURE_VERSION = 1
    SIMILARITY_VECTOR_DIM = 87
    SIM_PENDING, SIM_DONE, SIM_FAILED = 0, 1, 2
    SIMILARITY_ENABLED = False


@dataclass
class AudioFile:
    file_id: str
    file_path: str
    file_name: str
    file_size: int
    duration: float
    sample_rate: int
    channels: int
    bit_depth: int
    codec: str
    bitrate: int
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    comments: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    source: Optional[str] = None
    modified_at: Optional[float] = None
    meta_error: Optional[str] = None


class Database:
    """
    SQLite + WAL + FTS5
    - 30명 동시 검색 지원 (WAL의 다중 reader)
    - 인덱싱 중에도 검색 가능
    - 검색 < 50ms 목표
    """

    # _init_db + _ensure_indices 는 프로세스 당 db_path 별 1회만 실행.
    # 매 Database() 생성마다 돌리면 933k 행 COUNT + write 가 누적되어
    # 인덱싱 중 WAL 락 경합 발생.
    _ensured_paths: set = set()
    _ensure_lock = threading.Lock()

    def __init__(self, db_path: str, read_only: bool = False):
        self.db_path = str(db_path)
        self.read_only = read_only
        self._folders_cache: Optional[List[str]] = None
        if not read_only:
            with Database._ensure_lock:
                if self.db_path not in Database._ensured_paths:
                    self._init_db()
                    self._ensure_indices()
                    Database._ensured_paths.add(self.db_path)

    def _ensure_indices(self):
        """기존 DB 마이그레이션 + 누락 인덱스 보강."""
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_file_path_nc ON audio_files(file_path COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_file_name_nc ON audio_files(file_name COLLATE NOCASE);
            """)
            # meta_extracted 컬럼 (Phase1 path-only / Phase2 메타완료 구분)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(audio_files)").fetchall()]
            if "meta_extracted" not in cols:
                conn.execute(
                    "ALTER TABLE audio_files ADD COLUMN meta_extracted INTEGER DEFAULT 1"
                )
            if "meta_error" not in cols:
                conn.execute(
                    "ALTER TABLE audio_files ADD COLUMN meta_error TEXT"
                )
            if "phase2_run_id" not in cols:
                conn.execute(
                    "ALTER TABLE audio_files ADD COLUMN phase2_run_id TEXT"
                )
            for col in ("description", "keywords", "category", "sub_category", "source"):
                if col not in cols:
                    conn.execute(f"ALTER TABLE audio_files ADD COLUMN {col} TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_extracted ON audio_files(meta_extracted)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_meta_phase2_run ON audio_files(meta_extracted, phase2_run_id)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blacklist_paths (
                    path TEXT PRIMARY KEY,
                    description TEXT,
                    added_at REAL DEFAULT (strftime('%s','now'))
                )
            """)
            # 중복 수리 = 검색 숨김 플래그 (1회 마이그레이션). 인덱스에서 지우지 않고
            # 행에 hidden=1 만 세움 — 재스캔이 다시 등록할 일도, FTS 삭제 비용도 없음.
            try:
                conn.execute(
                    "ALTER TABLE audio_files ADD COLUMN hidden INTEGER DEFAULT 0"
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # 이미 존재

            # 제거(soft) 플래그 — '제거'는 행을 지우지 않고 removed=1 만 세운다.
            # 숨김(hidden)과 같이 검색/카운트/목록에서 제외되지만, 차이는 갱신 동작:
            #   빠른 갱신: removed 유지(안 돌아옴) / 전체 갱신: removed 해제(다시 돌아옴).
            #   hidden: 둘 다 유지(복원으로만 해제).
            try:
                conn.execute(
                    "ALTER TABLE audio_files ADD COLUMN removed INTEGER DEFAULT 0"
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # 이미 존재

            # 중복 고아 표시 — 상대 사본이 삭제돼 더 이상 중복이 아니지만 사용자가
            # 복원을 거절한 숨김 파일 (제외 관리에서 별도 표시용).
            try:
                conn.execute(
                    "ALTER TABLE audio_files ADD COLUMN dup_orphan INTEGER DEFAULT 0"
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # 이미 존재

            # hidden=1 부분 커버링 색인 — 숨김 행의 (file_path, dup_orphan) 을 정렬
            # 상태로 상시 보관. count_hidden 과 get_hidden_paths(ORDER BY + LIMIT)
            # 둘 다 색인만 읽고 끝남 — 이전엔 24만 숨김행 풀스캔+정렬이라
            # 환경설정/제외관리 열 때마다 cold 수 초 UI 프리즈.
            # ALTER(hidden, dup_orphan) 이후에 실행돼야 함.
            # (구버전 idx_hidden_1/idx_hidden_path 는 dup_orphan 미포함 — 대체)
            root_cols = [r[1] for r in conn.execute("PRAGMA table_info(library_roots)").fetchall()]
            if "sort_order" not in root_cols:
                conn.execute("ALTER TABLE library_roots ADD COLUMN sort_order INTEGER")
                conn.execute("""
                    WITH ordered AS (
                        SELECT path, ROW_NUMBER() OVER (ORDER BY path) - 1 AS rn
                        FROM library_roots
                    )
                    UPDATE library_roots
                    SET sort_order = (SELECT rn FROM ordered WHERE ordered.path = library_roots.path)
                    WHERE sort_order IS NULL
                """)
                conn.commit()
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_library_roots_order "
                "ON library_roots(sort_order, path)"
            )

            conn.execute("DROP INDEX IF EXISTS idx_hidden_1")
            conn.execute("DROP INDEX IF EXISTS idx_hidden_path")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hidden_path_v2 "
                "ON audio_files(file_path, dup_orphan) WHERE hidden = 1"
            )

            # ─── 유사 사운드 검색 (Phase3) 스키마 ───
            # 배포 빌드에서는 _sim 이 없어 통째로 건너뛴다 (격리 — 파일 첫머리 주석)
            if SIMILARITY_ENABLED:
                _sim.ensure_similarity_schema(conn, cols)

            # FTS5 스키마에 필요한 컬럼이 포함됐는지 검사. 없으면 재생성 (1회 마이그레이션).
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='search_fts' AND type='table'"
            ).fetchone()
            fts_sql = row[0] if row else ""
            required_fts_cols = ("file_path", "file_name_norm", "description", "keywords", "category", "sub_category", "source")
            if row and any(col not in (fts_sql or "") for col in required_fts_cols):
                logger.info("FTS5 스키마 마이그레이션 — file_name_norm 포함 버전으로 재생성")
                conn.execute("DROP TABLE search_fts")
                conn.execute("""
                    CREATE VIRTUAL TABLE search_fts USING fts5(
                        file_id UNINDEXED,
                        file_name,
                        file_name_norm,
                        file_path,
                        title, artist, album, genre, comments,
                        description, keywords, category, sub_category, source,
                        tokenize='trigram'
                    )
                """)
                # rowid 명시 (id → fts_rowid 변환). SQLite SQL 만으로는 hex→int 못해서
                # Python 으로 batch fetch + transform + executemany 한다.
                # (fetchall 전체 적재는 90만행급에서 메모리 수백MB — rowid 배치로 제한)
                last_mig_rowid = 0
                while True:
                    src = conn.execute(
                        "SELECT rowid, id, file_name, file_path, "
                        "COALESCE(title,''), COALESCE(artist,''), "
                        "COALESCE(album,''), COALESCE(genre,''), "
                        "COALESCE(comments,''), COALESCE(description,''), "
                        "COALESCE(keywords,''), COALESCE(category,''), "
                        "COALESCE(sub_category,''), COALESCE(source,'') "
                        "FROM audio_files WHERE rowid > ? ORDER BY rowid LIMIT 5000",
                        (last_mig_rowid,)
                    ).fetchall()
                    if not src:
                        break
                    last_mig_rowid = src[-1][0]
                    conn.executemany(
                        "INSERT INTO search_fts "
                        "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, "
                        " comments, description, keywords, category, sub_category, source) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [Database._fts_row(r[1], r[2], r[3], r[4:]) for r in src]
                    )
                conn.commit()

            # FTS rowid 최적화 마이그레이션 (1회) — file_id UNINDEXED 라
            # DELETE WHERE file_id IN (...) 가 풀스캔. rowid = int(file_id[:15], 16) 로
            # 저장하면 DELETE WHERE rowid IN (...) 가 인덱스 사용 → 수십~수백배 빠름.
            opt_row = conn.execute(
                "SELECT value FROM index_meta WHERE key='fts_rowid_optimized'"
            ).fetchone()
            if not opt_row or opt_row[0] != '1':
                fts_existing = conn.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0]
                if fts_existing > 0:
                    import time as _t
                    t0 = _t.monotonic()
                    logger.info(
                        f"FTS rowid 최적화 마이그레이션 시작 — {fts_existing:,}행 재빌드 "
                        "(1~3분 소요, 1회만)"
                    )
                    # batch fetch + transform + insert
                    conn.execute("DELETE FROM search_fts")
                    BATCH = 5000
                    offset = 0
                    cur_m = conn.cursor()
                    while True:
                        cur_m.execute(
                            "SELECT id, file_name, file_path, "
                            "COALESCE(title,''), COALESCE(artist,''), "
                            "COALESCE(album,''), COALESCE(genre,''), "
                            "COALESCE(comments,''), COALESCE(description,''), "
                            "COALESCE(keywords,''), COALESCE(category,''), "
                            "COALESCE(sub_category,''), COALESCE(source,'') "
                            "FROM audio_files LIMIT ? OFFSET ?",
                            (BATCH, offset)
                        )
                        rows = cur_m.fetchall()
                        if not rows:
                            break
                        new_rows = [
                            Database._fts_row(r[0], r[1], r[2], r[3:]) for r in rows
                        ]
                        conn.executemany(
                            "INSERT INTO search_fts "
                            "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, "
                            "genre, comments, description, keywords, category, sub_category, source) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            new_rows
                        )
                        offset += BATCH
                        if offset % 50000 == 0:
                            logger.info(f"  FTS 마이그레이션 진행: {offset:,}/{fts_existing:,}")
                    elapsed = _t.monotonic() - t0
                    logger.info(
                        f"FTS rowid 최적화 마이그레이션 완료 — {fts_existing:,}행 / {elapsed:.1f}s"
                    )
                conn.execute(
                    "INSERT INTO index_meta (key, value) VALUES ('fts_rowid_optimized', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
                conn.commit()

            # 카운트 불일치 시 자동 재빌드 X — 플래그만 세팅, UI 에서 수동 복구.
            audio_count = conn.execute("SELECT COUNT(*) FROM audio_files").fetchone()[0]
            fts_count = conn.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0]
            fts_stale = audio_count != fts_count
            if fts_stale:
                logger.warning(
                    f"FTS5 정합성 깨짐 — audio_files={audio_count:,}, "
                    f"search_fts={fts_count:,} (자동 재빌드 비활성, UI 에서 수동 복구)"
                )
            # 값 변경 시에만 write — 매번 commit 하면 WAL 락 경합으로 인덱싱 느려짐.
            new_val = '1' if fts_stale else '0'
            cur_row = conn.execute(
                "SELECT value FROM index_meta WHERE key='fts_stale'"
            ).fetchone()
            if cur_row is None or cur_row[0] != new_val:
                conn.execute(
                    "INSERT INTO index_meta (key,value) VALUES ('fts_stale',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (new_val,)
                )
                conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        """매 호출마다 새 connection (스레드 안전성).
        read_only=True 일 땐 journal_mode/synchronous 설정 X — write 경합 회피."""
        if self.read_only:
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0)

        conn.row_factory = sqlite3.Row
        if not self.read_only:
            # WAL 모드: 동시 read 무제한, write 1개
            try:
                # journal_mode 는 파일에 기록되므로 매번 PRAGMA 호출 시 오버헤드 발생 가능.
                # 'wal' 인지 먼저 확인 후 다를 때만 설정.
                cur = conn.cursor()
                mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
                if mode.lower() != 'wal':
                    conn.execute("PRAGMA journal_mode=WAL")
                
                conn.execute("PRAGMA synchronous=NORMAL")
                # page_size 는 생성 시에만 유효하므로 매번 설정할 필요 없음 (init_db에서 처리)
                conn.execute("PRAGMA mmap_size=4294967296") # 4GB 매핑
            except sqlite3.OperationalError as e:
                logging.warning(f"WAL 모드 설정 실패 (잠금 가능성): {e}. 현재 journal 모드로 계속합니다.")
        try:
            conn.execute("PRAGMA cache_size=-128000")  # 128MB 캐시
            conn.execute("PRAGMA temp_store=MEMORY")
        except sqlite3.OperationalError as e:
            logging.warning(f"SQLite PRAGMA 설정 실패: {e}. 기본 설정으로 계속합니다.")
        return conn

    def checkpoint(self):
        """WAL 을 본 DB 에 반영하고 WAL 파일을 0 으로 truncate.
        인덱싱(대량 write) 직후 호출 — WAL 비대화(→ 새 연결 첫 접근 수초 지연/UI 프리즈) 방지.
        다른 연결이 열려 있으면 부분만 반영되지만 무해. 백그라운드 스레드에서 호출할 것."""
        if self.read_only:
            return
        try:
            conn = self._connect()
            try:
                r = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                logging.info("WAL checkpoint(TRUNCATE): %s", r)
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            logging.warning("WAL checkpoint 실패(잠금 가능성): %s", e)

    def _init_db(self):
        if self.read_only:
            return

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA page_size=65536") # 최적 I/O 크기
            cur = conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS audio_files (
                    id TEXT PRIMARY KEY,
                    file_path TEXT UNIQUE NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size INTEGER,
                    duration REAL,
                    sample_rate INTEGER,
                    channels INTEGER,
                    bit_depth INTEGER,
                    codec TEXT,
                    bitrate INTEGER,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    genre TEXT,
                    comments TEXT,
                    description TEXT,
                    keywords TEXT,
                    category TEXT,
                    sub_category TEXT,
                    source TEXT,
                    modified_at REAL,
                    indexed_at REAL DEFAULT (strftime('%s','now')),
                    meta_extracted INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_duration ON audio_files(duration);
                CREATE INDEX IF NOT EXISTS idx_sample_rate ON audio_files(sample_rate);
                CREATE INDEX IF NOT EXISTS idx_channels ON audio_files(channels);
                CREATE INDEX IF NOT EXISTS idx_modified ON audio_files(modified_at);
                -- 폴더 prefix LIKE 'X%' COLLATE NOCASE 를 인덱스 스캔으로
                CREATE INDEX IF NOT EXISTS idx_file_path_nc ON audio_files(file_path COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_file_name_nc ON audio_files(file_name COLLATE NOCASE);

                CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
                    file_id UNINDEXED,
                    file_name,
                    file_name_norm,
                    file_path,
                    title,
                    artist,
                    album,
                    genre,
                    comments,
                    description,
                    keywords,
                    category,
                    sub_category,
                    source,
                    tokenize='trigram'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS search_fts_term USING fts5(
                    file_id UNINDEXED,
                    file_name, file_name_norm, file_path,
                    title, artist, album, genre, comments,
                    description, keywords, category, sub_category, source,
                    tokenize='porter unicode61'
                );

                CREATE TABLE IF NOT EXISTS index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- 유사 사운드 검색 표(similarity_vectors)는 개발 빌드에서만
                -- app/similarity_schema.py 가 만든다 (배포 격리).

                CREATE TABLE IF NOT EXISTS library_roots (
                    path TEXT PRIMARY KEY,
                    added_at REAL DEFAULT (strftime('%s','now')),
                    last_indexed_at REAL,
                    sort_order INTEGER
                );

                CREATE TABLE IF NOT EXISTS blacklist_paths (
                    path TEXT PRIMARY KEY,
                    description TEXT,
                    added_at REAL DEFAULT (strftime('%s','now'))
                );
            """)
            conn.commit()
        finally:
            conn.close()

    # ─────────── library_roots ───────────
    def add_library_root(self, path: str):
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO library_roots (path, sort_order) "
                "VALUES (?, COALESCE((SELECT MAX(sort_order) + 1 FROM library_roots), 0)) "
                "ON CONFLICT(path) DO NOTHING",
                (path,)
            )
            conn.commit()
        finally:
            conn.close()

    def remove_library_root(self, path: str):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM library_roots WHERE path = ?", (path,))
            conn.commit()
        finally:
            conn.close()

    def get_library_roots(self) -> List[Dict]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT path, added_at, last_indexed_at, sort_order FROM library_roots "
                "ORDER BY COALESCE(sort_order, 999999999), path"
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def reorder_library_roots(self, paths: List[str]):
        conn = self._connect()
        try:
            rows = conn.execute("SELECT path FROM library_roots").fetchall()
            existing = {r["path"].lower(): r["path"] for r in rows}
            ordered: List[str] = []
            seen = set()
            for path in paths:
                key = path.lower()
                if key in existing and key not in seen:
                    ordered.append(existing[key])
                    seen.add(key)
            current = conn.execute(
                "SELECT path FROM library_roots "
                "ORDER BY COALESCE(sort_order, 999999999), path"
            ).fetchall()
            for row in current:
                key = row["path"].lower()
                if key not in seen:
                    ordered.append(row["path"])
                    seen.add(key)
            conn.executemany(
                "UPDATE library_roots SET sort_order = ? WHERE path = ?",
                [(i, path) for i, path in enumerate(ordered)]
            )
            conn.commit()
        finally:
            conn.close()

    # ─────────── blacklist_paths ───────────
    def add_blacklist_path(self, path: str, description: str = ""):
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO blacklist_paths (path, description) VALUES (?, ?) "
                "ON CONFLICT(path) DO UPDATE SET description=excluded.description",
                (path, description or "")
            )
            conn.commit()
        finally:
            conn.close()

    def remove_blacklist_path(self, path: str):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM blacklist_paths WHERE path = ?", (path,))
            conn.commit()
        finally:
            conn.close()

    def get_blacklist_paths(self) -> List[Dict]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT path, description, added_at FROM blacklist_paths ORDER BY path"
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def update_root_indexed_at(self, path: str):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE library_roots SET last_indexed_at = strftime('%s','now') WHERE path = ?",
                (path,)
            )
            conn.commit()
        finally:
            conn.close()

    def get_indexed_paths_under(self, prefix: str) -> Dict[str, tuple]:
        """주어진 prefix 이하의 인덱싱된 경로 → (mtime, meta_extracted) (부분 스캔용).
        70만 개 이상의 거대 라이브러리를 위해 Row 객체 생성을 우회하는 최적화 버전.
        """
        conn = self._connect()
        try:
            # row_factory를 잠시 끄면 튜플로 반환되어 훨씬 빠름
            old_factory = conn.row_factory
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute(
                "SELECT file_path, modified_at, meta_extracted FROM audio_files "
                "WHERE file_path LIKE ? COLLATE NOCASE",
                (f"{prefix}%",)
            )
            # dict comprehension 대신 직접 루프가 거대 데이터에서 메모리 효율적일 수 있음
            res = {}
            for path, mtime, meta in cur:
                res[path] = (mtime, meta)
            conn.row_factory = old_factory
            return res
        finally:
            conn.close()

    def delete_files_under(self, prefix: str) -> int:
        """prefix 이하의 모든 파일 삭제 (라이브러리 제거용). 반환: 삭제된 행 수.

        search_fts.file_id 는 UNINDEXED 이므로 executemany 의 N 회 DELETE 는
        N × 풀스캔 비용 (10만 파일 = 10만 × 933k 스캔) → 응답없음.
        단일 subquery DELETE 한 번으로 끝낸다: O(N+M) → O(N×M) 대비 수천 배 빠름.
        """
        self._folders_cache = None
        pat = f"{prefix}%"
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            cur.execute(
                "SELECT COUNT(*) FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE",
                (pat,)
            )
            n = cur.fetchone()[0]
            # FTS 먼저 — rowid 변환 후 IN 절. file_id UNINDEXED 풀스캔 회피.
            cur.execute(
                "SELECT id FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE",
                (pat,)
            )
            target_rowids = [Database.fts_rowid(r[0]) for r in cur.fetchall()]
            FTS_CHUNK = 500
            for i in range(0, len(target_rowids), FTS_CHUNK):
                chunk = target_rowids[i:i + FTS_CHUNK]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"DELETE FROM search_fts WHERE rowid IN ({placeholders})",
                    chunk
                )
                Database._term_fts_delete(cur, chunk)   # 단어색인 동기
            if SIMILARITY_ENABLED:
                _sim.delete_vectors_by_path_like(cur, pat)
            cur.execute(
                "DELETE FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE",
                (pat,)
            )
            conn.commit()
            return n
        finally:
            conn.close()

    def soft_remove_under(self, prefix: str) -> Tuple[int, List[str]]:
        """라이브러리 제거(빠른 방식) — 행/FTS 를 물리 삭제하지 않고 removed=1 만 세움.
        8.7GB+ DB 에서 두 FTS 색인(trigram + 단어색인) 물리 삭제가 분 단위로 느려,
        검색/카운트/목록에서 즉시 빠지는 soft-delete 로 전환(수 초 내). FTS 행은
        그대로 두되 검색의 audio_files JOIN + removed 필터가 가린다. 전체(강제)
        재스캔/재추가 시 clear_removed_under 로 되살아남.
        범위 내 숨김(hidden, 중복 검수)도 함께 해제 — 제거된 라이브러리의 항목이
        [제외 관리] 목록에 유령으로 남지 않고, 재추가 시 '전부 보이는' 상태로 복원
        (사용자 확정 정책: 재추가 = 삭제/제외/숨김 모두 초기화. 중복은 검수 재실행).
        물리 정리가 필요하면 delete_files_under 로 별도 수행.
        반환: (제거된 행 수, 고아 중복 후보 id 리스트 — 복원은 팝업 승인 후)."""
        self._folders_cache = None
        pat = f"{prefix}%"
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            # 제거될 (파일명,크기) 그룹 스냅샷 — 제거 후 고아 중복 복원 판정용
            cur.execute(
                "CREATE TEMP TABLE _dup_groups AS "
                "SELECT DISTINCT file_name, file_size FROM audio_files "
                "WHERE file_path LIKE ? COLLATE NOCASE AND file_size IS NOT NULL",
                (pat,)
            )
            cur.execute(
                "UPDATE audio_files SET hidden = 0 "
                "WHERE file_path LIKE ? COLLATE NOCASE AND IFNULL(hidden,0) = 1",
                (pat,)
            )
            cur.execute(
                "UPDATE audio_files SET removed = 1 "
                "WHERE file_path LIKE ? COLLATE NOCASE AND IFNULL(removed,0) = 0",
                (pat,)
            )
            n = cur.rowcount
            orphan_ids = self._find_orphan_dup_candidates(cur)
            cur.execute("DROP TABLE _dup_groups")
            conn.commit()
            return n, orphan_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _find_orphan_dup_candidates(cur) -> List[str]:
        """고아 중복 후보 탐지 (공용) — 호출자가 채운 temp 테이블 _dup_groups
        (file_name, file_size) 의 그룹 중 '보이는 사본'(hidden=0, removed=0)이
        하나도 안 남은 그룹에서 숨김 사본을 그룹당 1개(경로 짧은 순, 동률 사전순)
        선정해 id 리스트로 반환. **복원(unhide)은 하지 않음** — 사용자 확정 정책:
        모든 지우기 경로에서 자동 복원 금지, UI 팝업 승인 후 unhide_by_ids 로 복원.
        호출자 트랜잭션 안에서 실행."""
        # COLLATE NOCASE 필수 — 기본(BINARY) 비교는 idx_file_name_nc(NOCASE)를 못 타
        # 158만 행 풀스캔이 후보마다 반복돼 수 분 정체 (2026-07-10 실측: 47개 폴더
        # 재스캔이 몇 분씩 멈춤). NOCASE 색인 프로브로 그룹당 수 ms.
        cur.execute(
            "SELECT h.id, h.file_path, h.file_name, h.file_size "
            "FROM _dup_groups g JOIN audio_files h "
            "  ON h.file_name = g.file_name COLLATE NOCASE "
            " AND h.file_size = g.file_size "
            "WHERE IFNULL(h.hidden,0) = 1 AND IFNULL(h.removed,0) = 0 "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM audio_files v "
            "    WHERE v.file_name = h.file_name COLLATE NOCASE "
            "      AND v.file_size = h.file_size "
            "      AND IFNULL(v.hidden,0) = 0 AND IFNULL(v.removed,0) = 0)"
        )
        best: Dict[tuple, tuple] = {}
        for r in cur.fetchall():
            key = (r[2], r[3])
            cand = (len(r[1]), r[1], r[0])
            if key not in best or cand < best[key]:
                best[key] = cand
        return [v[2] for v in best.values()]

    def unhide_by_ids(self, ids: List[str],
                      progress_cb: Optional[Callable[[int, int], None]] = None) -> int:
        """고아 중복 복원 실행 — 팝업에서 사용자가 승인한 후보 id 들만 unhide.
        반환: 실제 복원된 행 수. progress_cb 는 hide_files 와 같은 규격."""
        if not ids:
            return 0
        total = len(ids)
        conn = self._connect()
        try:
            cur = conn.cursor()
            n = 0
            for i in range(0, total, 500):
                chunk = ids[i:i + 500]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"UPDATE audio_files SET hidden = 0, dup_orphan = 0 "
                    f"WHERE id IN ({ph}) AND IFNULL(hidden,0) = 1", chunk)
                n += cur.rowcount
                if progress_cb:
                    progress_cb(min(i + 500, total), total)
            conn.commit()
            return n
        finally:
            conn.close()

    def purge_files_under(self, prefix: str) -> Dict:
        """라이브러리 완전 제거 — 한 트랜잭션으로:
        1) prefix 이하 모든 행 물리 삭제 (hidden/removed/실패 구분 없이) + FTS 2종 동기
        2) 같은 범위의 blacklist_paths 항목 삭제 (유령 블랙리스트가 재추가 시
           폴더를 말없이 가리는 함정 방지)
        3) 이 삭제로 '보이는 사본'이 전부 사라진 중복 검수 숨김(hidden=1) 파일을
           (파일명,크기) 그룹당 1개 자동 복원 — 경로 짧은 순(동률 시 사전순).
           소리가 검색에서 증발하는 것 방지 (사용자 확정 정책).
        반환: {"deleted", "blacklist_removed", "unhidden"}
        """
        self._folders_cache = None
        pat = f"{prefix}%"
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            # 삭제될 (파일명,크기) 그룹 스냅샷 — 복원 판정은 삭제 후 이 그룹만 검사.
            # temp 테이블은 connection-local 이라 동시 검색과 무충돌.
            cur.execute(
                "CREATE TEMP TABLE _dup_groups AS "
                "SELECT DISTINCT file_name, file_size FROM audio_files "
                "WHERE file_path LIKE ? COLLATE NOCASE AND file_size IS NOT NULL",
                (pat,)
            )
            # FTS 먼저 — delete_files_under 와 동일한 rowid 청크 패턴.
            cur.execute(
                "SELECT id FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE",
                (pat,)
            )
            target_rowids = [Database.fts_rowid(r[0]) for r in cur.fetchall()]
            deleted = len(target_rowids)
            FTS_CHUNK = 500
            for i in range(0, len(target_rowids), FTS_CHUNK):
                chunk = target_rowids[i:i + FTS_CHUNK]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(f"DELETE FROM search_fts WHERE rowid IN ({ph})", chunk)
                Database._term_fts_delete(cur, chunk)
            if SIMILARITY_ENABLED:
                _sim.delete_vectors_by_path_like(cur, pat)
            cur.execute(
                "DELETE FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE",
                (pat,)
            )
            # 블랙리스트 — prefix 이하 + 루트 자체(구분자 유무 무관) 둘 다 제거.
            cur.execute(
                "DELETE FROM blacklist_paths WHERE path LIKE ? COLLATE NOCASE "
                "OR rtrim(path, '\\/') = ? COLLATE NOCASE",
                (pat, prefix.rstrip("\\/"))
            )
            bl_removed = cur.rowcount
            # 고아 중복 후보 — 삭제된 그룹 중 보이는 사본이 0개가 된 그룹만.
            # 복원은 UI 팝업 승인 후 (orphan_ids 반환).
            orphan_ids = self._find_orphan_dup_candidates(cur)
            cur.execute("DROP TABLE _dup_groups")
            conn.commit()
            return {"deleted": deleted, "blacklist_removed": bl_removed,
                    "orphan_ids": orphan_ids}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def iter_path_size_mtime_under(self, prefix: str, batch: int = 20000):
        """prefix 이하 (file_path, file_size, modified_at) 배치 제너레이터.
        파형 피크 캐시 키 재계산용 — 캐시 키가 md5(경로|크기|mtime|...) 라
        행 삭제 '전'에 호출해야 함. rowid 페이지네이션(OFFSET 스캔 회피)."""
        conn = self._connect()
        try:
            conn.row_factory = None
            cur = conn.cursor()
            last = 0
            while True:
                rows = cur.execute(
                    "SELECT rowid, file_path, file_size, modified_at FROM audio_files "
                    "WHERE rowid > ? AND file_path LIKE ? COLLATE NOCASE "
                    "ORDER BY rowid LIMIT ?",
                    (last, f"{prefix}%", batch)
                ).fetchall()
                if not rows:
                    break
                last = rows[-1][0]
                yield [(r[1], r[2], r[3]) for r in rows]
        finally:
            conn.close()

    def fts_auto_repair(self, progress_cb=None) -> Dict:
        """FTS 정합성 표적 수리 — 전체 재빌드 없이 어긋난 행만 처리.
        - audio_files 에 있는데 FTS 에 없는 행: audio_files 데이터로 완전 복구
          (Phase1 크래시로 유실된 '메타 없는' 행 포함 — bulk_fts_upsert_from_
           audio_files 는 메타 없는 행을 스킵하므로 재사용 불가, 전용 경로)
        - FTS 에 있는데 audio_files 에 없는 유령 행: 삭제
        search_fts / search_fts_term 둘 다 처리. 종료 시 카운트 재검증 후
        fts_stale 플래그 갱신. 반환: {"inserted", "deleted", "consistent"}

        progress_cb(done, total): 채우기 진행 보고. total=0 = 점검 중(불확정).
        """
        def _emit(done, total):
            if progress_cb:
                try:
                    progress_cb(done, total)
                except Exception:
                    pass
        _emit(0, 0)   # 점검 단계 진입 — 마퀴
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            # 기준표: 모든 audio_files 행의 (기대 rowid, id). temp = 연결 로컬.
            cur.execute(
                "CREATE TEMP TABLE _af_rid (rid INTEGER PRIMARY KEY, id TEXT)")
            last = 0
            while True:
                rows = cur.execute(
                    "SELECT rowid, id FROM audio_files WHERE rowid > ? "
                    "ORDER BY rowid LIMIT 20000", (last,)
                ).fetchall()
                if not rows:
                    break
                last = rows[-1][0]
                cur.executemany(
                    "INSERT OR IGNORE INTO _af_rid (rid, id) VALUES (?,?)",
                    [(Database.fts_rowid(r[1]), r[1]) for r in rows]
                )

            missing_ids: set = set()
            orphans: Dict[str, list] = {}
            for table in ("search_fts", "search_fts_term"):
                try:
                    cur.execute(
                        f"SELECT t.id FROM _af_rid t WHERE NOT EXISTS "
                        f"(SELECT 1 FROM {table} WHERE {table}.rowid = t.rid)")
                    missing_ids.update(r[0] for r in cur.fetchall())
                    cur.execute(
                        f"SELECT {table}.rowid FROM {table} WHERE NOT EXISTS "
                        f"(SELECT 1 FROM _af_rid a WHERE a.rid = {table}.rowid)")
                    orphans[table] = [r[0] for r in cur.fetchall()]
                except sqlite3.OperationalError:
                    orphans[table] = []  # 테이블 미존재(단어색인 빌드 전) — 스킵

            # 누락 행 복구 — 두 색인 모두 INSERT OR REPLACE (한쪽만 누락이어도 무해)
            CHUNK = 500
            mlist = list(missing_ids)
            m_total = len(mlist)
            _emit(0, m_total)   # 채우기 시작 — 확정 게이지로 전환(0/total)
            _last_emit_t = time.monotonic()
            for i in range(0, m_total, CHUNK):
                chunk = mlist[i:i + CHUNK]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    "SELECT id, file_name, file_path, "
                    "COALESCE(title,''), COALESCE(artist,''), COALESCE(album,''), "
                    "COALESCE(genre,''), COALESCE(comments,''), COALESCE(description,''), "
                    "COALESCE(keywords,''), COALESCE(category,''), "
                    "COALESCE(sub_category,''), COALESCE(source,'') "
                    f"FROM audio_files WHERE id IN ({ph})", chunk)
                fts_rows = [
                    Database._fts_row(r[0], r[1], r[2], r[3:]) for r in cur.fetchall()
                ]
                cur.executemany(
                    "INSERT OR REPLACE INTO search_fts "
                    "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, "
                    " album, genre, comments, description, keywords, category, sub_category, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fts_rows)
                self._term_fts_insert(cur, fts_rows)
                # ~0.1s 스로틀 — 수만 회 UI 갱신 churn 방지 (마지막은 아래서 보장)
                now = time.monotonic()
                if now - _last_emit_t >= 0.1:
                    _emit(min(m_total, i + CHUNK), m_total)
                    _last_emit_t = now
            _emit(m_total, m_total)   # 100% 확정

            # 유령 행 삭제
            deleted = 0
            for table, rids in orphans.items():
                for i in range(0, len(rids), CHUNK):
                    chunk = rids[i:i + CHUNK]
                    ph = ",".join(["?"] * len(chunk))
                    try:
                        cur.execute(
                            f"DELETE FROM {table} WHERE rowid IN ({ph})", chunk)
                        deleted += len(chunk)
                    except sqlite3.OperationalError:
                        break

            cur.execute("DROP TABLE _af_rid")
            # 재검증 → 플래그 갱신 (수리와 같은 트랜잭션)
            a = cur.execute("SELECT COUNT(*) FROM audio_files").fetchone()[0]
            f = cur.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0]
            consistent = (a == f)
            cur.execute(
                "INSERT INTO index_meta (key,value) VALUES ('fts_stale',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ('0' if consistent else '1',))
            conn.commit()
            logger.info(
                f"FTS 표적 수리 — 복구 {len(mlist):,}, 유령삭제 {deleted:,}, "
                f"정합 {consistent} (audio={a:,}, fts={f:,})")
            return {"inserted": len(mlist), "deleted": deleted,
                    "consistent": consistent}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def count_files_under(self, prefix: str) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM audio_files "
                "WHERE file_path LIKE ? COLLATE NOCASE AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0",
                (f"{prefix}%",)
            )
            return cur.fetchone()[0]
        finally:
            conn.close()

    def get_all_folder_counts(self) -> Dict[str, int]:
        """한 번의 쿼리로 모든 폴더의 누적 파일 수 반환.
        70만 개 데이터 대응: fetchall() 대신 커서 순회 및 O(Folders) 누적 알고리즘."""
        conn = self._connect()
        direct: Dict[str, int] = {}
        try:
            old_factory = conn.row_factory
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute("SELECT file_path FROM audio_files WHERE IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0")

            # 90만 행 순수 파이썬 루프 = GIL 독점 → 워커인데도 UI 스레드를 굶겨
            # 중앙 로더/화면이 멈춤 (freeze.log 실측). 청크마다 1ms 실제 sleep 으로
            # 강제 양보 — 총 +0.1초 수준 비용으로 UI 응답성 보장.
            while True:
                rows = cur.fetchmany(10000)
                if not rows:
                    break
                for (path,) in rows:
                    idx = max(path.rfind("\\"), path.rfind("/"))
                    if idx > 0:
                        d = path[:idx].lower()
                        direct[d] = direct.get(d, 0) + 1
                time.sleep(0.001)
            conn.row_factory = old_factory
        finally:
            conn.close()

        if not direct:
            return {}

        # 1단계: 모든 조상 폴더 경로를 파악 (카운트가 끊기지 않도록)
        all_folders = set(direct.keys())
        for i, folder in enumerate(list(all_folders)):
            p = folder
            while True:
                idx = max(p.rfind("\\"), p.rfind("/"))
                if idx <= 0: break
                p = p[:idx]
                if p in all_folders: break
                all_folders.add(p)
            if i and i % 10000 == 0:
                time.sleep(0.001)

        # 2단계: 깊은 경로(긴 경로)부터 정렬하여 부모에게 합산 전달
        sorted_folders = sorted(all_folders, key=len, reverse=True)
        cumulative = {f: direct.get(f, 0) for f in all_folders}
        
        for i, folder in enumerate(sorted_folders):
            idx = max(folder.rfind("\\"), folder.rfind("/"))
            if idx > 0:
                parent = folder[:idx]
                cumulative[parent] += cumulative[folder]
            if i and i % 10000 == 0:
                time.sleep(0.001)
        
        return cumulative

    def get_all_folder_incomplete_counts(self) -> Dict[str, int]:
        """폴더별 누적 미완료 카운트 (meta_extracted ∈ {0,2})."""
        conn = self._connect()
        direct: Dict[str, int] = {}
        try:
            old_factory = conn.row_factory
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute(
                "SELECT file_path FROM audio_files "
                "WHERE meta_extracted IN (0,2) AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0"
            )
            while True:
                rows = cur.fetchmany(10000)
                if not rows:
                    break
                for (path,) in rows:
                    idx = max(path.rfind("\\"), path.rfind("/"))
                    if idx > 0:
                        d = path[:idx].lower()
                        direct[d] = direct.get(d, 0) + 1
                time.sleep(0.001)
            conn.row_factory = old_factory
        finally:
            conn.close()

        if not direct:
            return {}

        all_folders = set(direct.keys())
        for i, folder in enumerate(list(all_folders)):
            p = folder
            while True:
                idx = max(p.rfind("\\"), p.rfind("/"))
                if idx <= 0: break
                p = p[:idx]
                if p in all_folders: break
                all_folders.add(p)
            if i and i % 10000 == 0:
                time.sleep(0.001)

        sorted_folders = sorted(all_folders, key=len, reverse=True)
        cumulative = {f: direct.get(f, 0) for f in all_folders}
        for i, folder in enumerate(sorted_folders):
            idx = max(folder.rfind("\\"), folder.rfind("/"))
            if idx > 0:
                parent = folder[:idx]
                cumulative[parent] += cumulative[folder]
            if i and i % 10000 == 0:
                time.sleep(0.001)
        return cumulative

    def count_incomplete_total(self) -> int:
        """전체 미완료(pending+failed) 카운트 — status bar 인디케이터용."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM audio_files "
                "WHERE meta_extracted IN (0,2) AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0"
            )
            return int(cur.fetchone()[0] or 0)
        finally:
            conn.close()

    def count_metadata_status_under(self, prefix: str) -> Dict[str, int]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    SUM(CASE WHEN meta_extracted = 0 THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN meta_extracted = 2 THEN 1 ELSE 0 END) AS failed
                FROM audio_files
                WHERE file_path LIKE ? COLLATE NOCASE
                  AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0
                """,
                (f"{prefix}%",)
            )
            row = cur.fetchone()
            return {
                "pending": int(row["pending"] or 0),
                "failed": int(row["failed"] or 0),
            }
        finally:
            conn.close()

    def count_scoped_meta_counts(self, prefixes: List[str]) -> Tuple[int, int]:
        """pending 이 남은 prefix 들에 한해 (전체 파일수, pending 수) 합산.

        count_metadata_status_under + count_files_under 를 루트마다 따로 부르면
        루트 N개당 연결 2N개 + 쿼리 2N개 (각각 prefix 범위를 두 번 스캔) —
        메인 스레드 호출 경로라 루트가 많으면 stall. 여기선 연결 1개를 재사용하고
        prefix 당 쿼리 1개로 COUNT 와 pending SUM 을 동시에 계산한다.
        합산 규칙은 기존 _compute_scoped_meta_counts 와 동일: pending=0 인
        루트는 분모(전체수)에서도 제외.
        """
        total = 0
        pending = 0
        if not prefixes:
            return 0, 0
        conn = self._connect()
        try:
            cur = conn.cursor()
            for prefix in prefixes:
                cur.execute(
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN meta_extracted = 0 THEN 1 ELSE 0 END) "
                    "FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE "
                    "AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0",
                    (f"{prefix}%",)
                )
                row = cur.fetchone()
                p = int(row[1] or 0)
                if p > 0:
                    pending += p
                    total += int(row[0] or 0)
        finally:
            conn.close()
        return total, pending

    def get_failed_metadata_under(self, prefix: str, limit: int = 1000) -> List[Dict]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT file_path, file_name, file_size, modified_at
                FROM audio_files
                WHERE meta_extracted = 2
                  AND file_path LIKE ? COLLATE NOCASE
                ORDER BY file_path
                LIMIT ?
                """,
                (f"{prefix}%", limit)
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def get_incomplete_metadata_under(self, prefix: str,
                                      include_pending: bool = True,
                                      include_failed: bool = True,
                                      limit: int = 5000) -> List[Dict]:
        """범위 내 미완료 파일 리스트 (pending=0 / failed=2).
        status 컬럼 포함: 'pending' | 'failed'
        """
        if not (include_pending or include_failed):
            return []
        states = []
        if include_pending:
            states.append("0")
        if include_failed:
            states.append("2")
        in_clause = ",".join(states)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT file_path, file_name, file_size, modified_at, meta_extracted, meta_error
                FROM audio_files
                WHERE meta_extracted IN ({in_clause})
                  AND file_path LIKE ? COLLATE NOCASE
                  AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0
                ORDER BY meta_extracted DESC, file_path
                LIMIT ?
                """,
                (f"{prefix}%", limit)
            )
            out = []
            for r in cur.fetchall():
                d = dict(r)
                d["status"] = "failed" if d["meta_extracted"] == 2 else "pending"
                out.append(d)
            return out
        finally:
            conn.close()

    def reset_incomplete_under(self, prefix: str,
                               include_pending: bool = False,
                               include_failed: bool = True,
                               run_id: Optional[str] = None) -> int:
        """범위(prefix) 내 미완료 항목을 한 번의 UPDATE 로 meta_extracted=0 리셋.
        표시 limit 과 무관 — 5000개 넘어가는 라이브러리의 [필터된 전체 재시도] 용.
        - include_failed: meta_extracted=2 → 0 (재추출 큐 진입)
        - include_pending: meta_extracted=0 → 0 (no-op 이지만 run_id 스탬프 대상)
        - run_id: 지정 시 phase2_run_id 도 함께 스탬프 → Phase2 를 이 범위로 한정.
        반환: 영향받은 행 수.
        """
        states = []
        if include_failed:
            states.append("2")
        if include_pending:
            states.append("0")
        if not states:
            return 0
        in_clause = ",".join(states)
        set_clause = "meta_extracted = 0"
        params: list = []
        if run_id is not None:
            set_clause += ", phase2_run_id = ?"
            params.append(run_id)
        params.append(f"{prefix}%")
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE audio_files
                SET {set_clause}
                WHERE meta_extracted IN ({in_clause})
                  AND file_path LIKE ? COLLATE NOCASE
                  AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0
                """,
                params
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def delete_incomplete_under(self, prefix: str,
                                include_pending: bool = True,
                                include_failed: bool = True) -> Tuple[int, List[str]]:
        """범위(prefix) 내 미완료 항목을 '제거'(soft) — 행을 지우지 않고 removed=1.
        검색/카운트/목록에서 빠지되, 전체 갱신 시 removed 해제로 다시 돌아온다
        (빠른 갱신은 유지). FTS 행은 그대로 두고 검색 쿼리가 removed 로 필터.
        반환: (제거 처리된 행 수, 고아 중복 후보 id — 복원은 팝업 승인 후).
        """
        states = []
        if include_failed:
            states.append("2")
        if include_pending:
            states.append("0")
        if not states:
            return 0, []
        in_clause = ",".join(states)
        self._folders_cache = None
        pat = f"{prefix}%"
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            cur.execute(
                f"CREATE TEMP TABLE _dup_groups AS "
                f"SELECT DISTINCT file_name, file_size FROM audio_files "
                f"WHERE meta_extracted IN ({in_clause}) "
                f"  AND file_path LIKE ? COLLATE NOCASE "
                f"  AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0 "
                f"  AND file_size IS NOT NULL",
                (pat,)
            )
            cur.execute(
                f"""
                UPDATE audio_files SET removed = 1
                WHERE meta_extracted IN ({in_clause})
                  AND file_path LIKE ? COLLATE NOCASE
                  AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0
                """,
                (pat,)
            )
            n = cur.rowcount
            orphan_ids = self._find_orphan_dup_candidates(cur)
            cur.execute("DROP TABLE _dup_groups")
            conn.commit()
            return n, orphan_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def count_incomplete_under(self, prefix: str,
                               include_pending: bool = True,
                               include_failed: bool = True) -> int:
        """필터된 전체 카운트 (limit 무시)."""
        states = []
        if include_failed:
            states.append("2")
        if include_pending:
            states.append("0")
        if not states:
            return 0
        in_clause = ",".join(states)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT COUNT(*) FROM audio_files
                WHERE meta_extracted IN ({in_clause})
                  AND file_path LIKE ? COLLATE NOCASE
                  AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0
                """,
                (f"{prefix}%",)
            )
            return int(cur.fetchone()[0] or 0)
        finally:
            conn.close()

    def reset_meta_for_paths(self, paths: List[str],
                             run_id: Optional[str] = None) -> int:
        """선택한 파일들(대기/실패)을 meta_extracted=0 으로 리셋 (재시도 큐 진입).
        run_id 지정 시 phase2_run_id 도 스탬프 → Phase2 를 이 선택분으로 한정.
        숨김(중복/블랙리스트) 파일은 제외. 반환: 영향받은 행 수."""
        if not paths:
            return 0
        set_clause = "meta_extracted = 0"
        prefix_params: list = []
        if run_id is not None:
            set_clause += ", phase2_run_id = ?"
            prefix_params.append(run_id)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            total = 0
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"UPDATE audio_files SET {set_clause} "
                    f"WHERE meta_extracted IN (0,2) AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0 "
                    f"AND file_path IN ({placeholders})",
                    prefix_params + chunk
                )
                total += cur.rowcount
            conn.commit()
            return total
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_files_by_paths(self, paths: List[str]) -> Tuple[int, List[str]]:
        """선택한 파일들을 '제거'(soft) — 행을 지우지 않고 removed=1 만 세움.
        검색/목록에서 빠지되 전체 갱신 시 removed 해제로 복귀(빠른 갱신은 유지).
        반환: (제거 처리된 행 수, 고아 중복 후보 id — 복원은 팝업 승인 후)."""
        if not paths:
            return 0, []
        self._folders_cache = None
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            cur.execute(
                "CREATE TEMP TABLE _dup_groups "
                "(file_name TEXT, file_size INTEGER, PRIMARY KEY(file_name, file_size)) "
                "WITHOUT ROWID"
            )
            total = 0
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                placeholders = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"INSERT OR IGNORE INTO _dup_groups "
                    f"SELECT DISTINCT file_name, file_size FROM audio_files "
                    f"WHERE file_path IN ({placeholders}) AND file_size IS NOT NULL",
                    chunk
                )
                cur.execute(
                    f"UPDATE audio_files SET removed = 1 "
                    f"WHERE file_path IN ({placeholders}) AND IFNULL(removed,0) = 0",
                    chunk
                )
                total += cur.rowcount
            orphan_ids = self._find_orphan_dup_candidates(cur)
            cur.execute("DROP TABLE _dup_groups")
            conn.commit()
            return total, orphan_ids
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def clear_removed_under(self, prefix: str) -> int:
        """전체(강제) 갱신 시 호출 — 범위 내 removed=1 을 해제해 다시 노출.
        '제거'했던 항목이 디스크에 남아 있으면 그대로 다시 인덱스에 보인다.
        (숨김 hidden 은 건드리지 않음 — 복원으로만 해제.) 반환: 해제된 행 수."""
        self._folders_cache = None
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE audio_files SET removed = 0 "
                "WHERE removed = 1 AND file_path LIKE ? COLLATE NOCASE",
                (f"{prefix}%",)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def delete_failed_metadata_under(self, prefix: str) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            ids = [
                r["id"] for r in cur.execute(
                    """
                    SELECT id FROM audio_files
                    WHERE meta_extracted = 2
                      AND file_path LIKE ? COLLATE NOCASE
                    """,
                    (f"{prefix}%",)
                ).fetchall()
            ]
            if not ids:
                return 0
            cur.execute("BEGIN")
            total = 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                placeholders = ",".join(["?"] * len(chunk))
                target_rowids = [Database.fts_rowid(fid) for fid in chunk]
                rid_ph = ",".join(["?"] * len(target_rowids))
                cur.execute(
                    f"DELETE FROM search_fts WHERE rowid IN ({rid_ph})",
                    target_rowids
                )
                if SIMILARITY_ENABLED:
                    _sim.delete_vectors_by_ids(cur, chunk)
                cur.execute(
                    f"DELETE FROM audio_files WHERE id IN ({placeholders})",
                    chunk
                )
                total += len(chunk)
            conn.commit()
            return total
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def generate_id(file_path: str) -> str:
        return hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]

    def find_duplicates(self, progress_cb=None) -> List[Dict]:
        """파일명+파일크기 둘 다 같은 항목 그룹화.
        반환: [{file_name, file_size, paths: [path1, path2, ...]}, ...]
        - 개수 내림차순, 파일명 오름차순 정렬.
        - file_size IS NULL 행은 제외 (Phase1 도중 등).
        - progress_cb(스캔한 행 수, 전체 행 수): 진행 콜백.
        IN(GROUP BY) 단일 쿼리는 실DB(113만 행) 4.5초 + 진행률 산출 불가 →
        가시 행을 잘라 읽어 파이썬 집계(실측 1.9초) 후 중복 키만 temp join.
        """
        vis = ("file_size IS NOT NULL "
               "AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0")
        conn = self._connect()
        try:
            conn.row_factory = None  # 대량 fetch 는 튜플 반환이 훨씬 빠름
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM audio_files WHERE {vis}")
            total = cur.fetchone()[0]
            counts: Dict[tuple, int] = {}
            cur.execute(f"SELECT file_name, file_size FROM audio_files WHERE {vis}")
            done = 0
            while True:
                chunk = cur.fetchmany(50000)
                if not chunk:
                    break
                for key in chunk:
                    counts[key] = counts.get(key, 0) + 1
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
            dup_keys = [k for k, v in counts.items() if v > 1]
            counts.clear()
            if not dup_keys:
                return []
            cur.execute("CREATE TEMP TABLE _dup_scan_keys "
                        "(file_name TEXT, file_size INTEGER)")
            cur.executemany("INSERT INTO _dup_scan_keys VALUES (?, ?)", dup_keys)
            cur.execute(
                "SELECT a.file_name, a.file_size, a.file_path "
                "FROM audio_files a JOIN _dup_scan_keys d "
                "  ON a.file_name = d.file_name AND a.file_size = d.file_size "
                "WHERE a.file_size IS NOT NULL "
                "  AND IFNULL(a.hidden,0) = 0 AND IFNULL(a.removed,0) = 0 "
                "ORDER BY a.file_name, a.file_size, a.file_path"
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        groups: Dict[tuple, Dict] = {}
        for name, size, path in rows:
            key = (name, size)
            g = groups.get(key)
            if g is None:
                g = {"file_name": name, "file_size": size, "paths": []}
                groups[key] = g
            g["paths"].append(path)
        return sorted(groups.values(), key=lambda g: (-len(g["paths"]), g["file_name"]))

    @staticmethod
    def fts_rowid(file_id: str) -> int:
        """file_id (sha256 hex 16자) → FTS5 rowid (60bit int, signed 64bit 안전).
        rowid 는 FTS5 의 hidden PK 라 인덱스 사용 — DELETE WHERE rowid IN (...) 빠름.
        798k 항목 충돌 확률 ~5e-7.
        """
        return int(file_id[:15], 16)

    @staticmethod
    def index_path(file_path: str) -> str:
        """검색 색인(search_fts / search_fts_term)의 file_path 컬럼에 넣을 값 —
        파일명을 뺀 폴더 부분까지만.

        파일명은 색인의 file_name / file_name_norm 컬럼에 이미 두 번 들어간다.
        경로 컬럼 끝의 파일명은 세 번째 중복이다. trigram 색인 삽입 비용은 넣는
        텍스트 양에 비례하므로(실측: 경로 평균 152자 → 폴더만 103자, -32%)
        이 중복을 빼면 신규 인덱싱 색인 삽입이 초당 289개 → 800개로 오른다.

        audio_files.file_path 원본은 손대지 않는다 — 화면 표시/재생/드래그/
        폴더 트리 필터/중복 검수는 모두 그쪽을 읽는다. 무필드 검색도 파일명은
        file_name 컬럼이 잡으므로 결과가 바뀌지 않는다.

        ⚠ 이 규칙을 바꾸면 이미 만들어진 색인과 내용이 어긋난다. 바꿀 경우
          색인 전체 재구성(rebuild_fts + build_term_index)이 필요하다.
        """
        if not file_path:
            return file_path
        cut = max(file_path.rfind("\\"), file_path.rfind("/"))
        return file_path[:cut] if cut > 0 else file_path

    @staticmethod
    def _fts_row(file_id: str, file_name: str, file_path: str, rest) -> tuple:
        """색인 15-튜플 생성 — 모든 삽입 지점 공용.
        (rowid, file_id, file_name, file_name_norm, file_path, 메타 10개)
        rest: 메타 10개 (title..source) 시퀀스.
        search_fts / search_fts_term 이 같은 튜플을 쓰므로 여기 한 곳이 두 색인의
        단일 진실 공급원이다.
        """
        return (Database.fts_rowid(file_id), file_id, file_name,
                Database.name_norm(file_name), Database.index_path(file_path),
                *rest)

    @staticmethod
    def _term_fts_insert(cur, fts_rows):
        """단어색인(search_fts_term) 미러 INSERT — search_fts 와 동일 15-튜플 재사용.
        삭제/제거/숨김은 _query_term 의 audio_files INNER JOIN + 필터가 자동 처리하므로
        INSERT 만 미러링하면 동기 유지. 테이블 없으면(빌드 전) 조용히 스킵."""
        if not fts_rows:
            return
        try:
            cur.executemany(
                "INSERT OR REPLACE INTO search_fts_term "
                "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, "
                " genre, comments, description, keywords, category, sub_category, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                fts_rows
            )
        except sqlite3.OperationalError:
            pass  # search_fts_term 미존재(최초 자동빌드 전) — 스킵, 빌드가 채움

    @staticmethod
    def _term_fts_delete(cur, rowids):
        """단어색인 미러 DELETE — search_fts 삭제와 같은 rowid 사용(orphan/count 정합)."""
        if not rowids:
            return
        try:
            ph = ",".join("?" * len(rowids))
            cur.execute(f"DELETE FROM search_fts_term WHERE rowid IN ({ph})", rowids)
        except sqlite3.OperationalError:
            pass

    # 파일명 구분자 (_ - . 공백) — file_name_norm 컬럼/검색어 변형 공용
    _NAME_SEP_RE = re.compile(r"[\s_\-.]+")

    @staticmethod
    def name_norm(s: str) -> str:
        """구분자 제거 파일명 — 'Gun_Shot_01.wav' → 'GunShot01wav'.
        trigram 은 연속 글자만 매칭하므로 붙여쓴 검색어('gunshot')가
        구분자 있는 파일명과 매칭되도록 정규화 사본을 FTS 에 같이 색인."""
        return Database._NAME_SEP_RE.sub("", s or "")

    def upsert_paths_only(self, items: List[tuple]):
        """Phase 1: 파일을 열지 않고 경로/이름/크기/mtime만 적재.
        FTS 작업 X — 매 배치 DELETE FROM search_fts WHERE file_id=? 가 UNINDEXED
        풀스캔 (배치당 500×933k=466M ops) 이라 200GB 스캔이 15분+ 걸렸음.
        FTS 등록은 Phase1 종료 시점에 bulk_fts_insert_new_paths() 로 한 번에.
        items: [(path, name, size, mtime), ...]
        """
        if not items:
            return
        self._folders_cache = None
        rows = []
        for path, name, size, mtime in items:
            fid = self.generate_id(path)
            rows.append((fid, path, name, size, mtime))
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            # 파일이 바뀌면 음향도 바뀐다 → 개발 빌드에서는 유사도 특징도 재추출 큐로.
            # 배포 빌드에는 sim_extracted 컬럼 자체가 없으므로 조각을 빼야 한다 (격리).
            sim_clause = _sim.SIM_UPSERT_CLAUSE if SIMILARITY_ENABLED else ""
            cur.executemany(f"""
                INSERT INTO audio_files
                (id, file_path, file_name, file_size, modified_at, meta_extracted)
                VALUES (?,?,?,?,?,0)
                ON CONFLICT(id) DO UPDATE SET
                    file_size=excluded.file_size,
                    modified_at=excluded.modified_at,
                    meta_extracted=CASE
                        WHEN audio_files.modified_at IS NULL
                          OR audio_files.modified_at + 2.0 < excluded.modified_at
                        THEN 0
                        ELSE audio_files.meta_extracted
                    END,{sim_clause}
                    indexed_at=strftime('%s','now')
            """, rows)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bulk_fts_insert_new_paths(self, items: List[tuple]) -> int:
        """Phase 1 종료 시 1회 호출 — 새로 발견된 파일들을 search_fts 에 일괄 INSERT.
        metadata 컬럼은 빈 값 (Phase 2 가 메타 추출 후 풀 업데이트).
        한 트랜잭션 내 chunked executemany — UNINDEXED DELETE 회피.
        items: [(path, name), ...]
        반환: 삽입된 행 수.
        """
        if not items:
            return 0
        deduped = list(dict.fromkeys(items))
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            CHUNK = 5000
            total = 0
            for i in range(0, len(deduped), CHUNK):
                batch = deduped[i:i + CHUNK]
                fts_rows = []
                for path, name in batch:
                    fid = self.generate_id(path)
                    fts_rows.append(
                        Database._fts_row(fid, name, path, ("",) * 10))
                cur.executemany("""
                    INSERT OR REPLACE INTO search_fts
                    (rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, comments,
                     description, keywords, category, sub_category, source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, fts_rows)
                self._term_fts_insert(cur, fts_rows)   # 단어색인 동기
                total += len(batch)
            conn.commit()
            return total
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_pending_metadata(self, limit: int = 100000) -> List[tuple]:
        """Phase 2 대상: meta_extracted=0 인 (path, size, mtime) 리스트.
        size/mtime 은 Phase1 에서 이미 stat 했으므로 Phase2 에서 재호출 불필요."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            # 숨김(검색 제외) 파일은 분석 대기열에서 제외 — 어차피 검색에 안 나오는
            # 파일을 NAS 에서 수십만 건 재분석하는 낭비 + "분석이 안 멈춤" 증상 방지.
            # 해제(unhide)하면 다시 대기열에 들어와 정상 분석됨.
            cur.execute(
                "SELECT file_path, file_size, modified_at FROM audio_files "
                "WHERE meta_extracted = 0 AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0 LIMIT ?",
                (limit,)
            )
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]
        finally:
            conn.close()

    def mark_metadata_failed(self, error_map: Dict[str, str], id_map: Optional[Dict[str, str]] = None):
        """추출 실패/타임아웃 파일 마킹 (meta_extracted = 2) 및 사유 저장.
        error_map: { file_path -> error_message }
        id_map: { file_path -> file_id } (제공 시 해시 재계산 스킵)
        """
        if not error_map:
            return
        
        if id_map:
            rows = [(2, msg, id_map[p]) for p, msg in error_map.items()]
        else:
            rows = [(2, msg, self.generate_id(p)) for p, msg in error_map.items()]
            
        conn = self._connect()
        try:
            cur = conn.cursor()
            # phase2_run_id 는 유지 — 재시도 결과 팝업이 run_id 로 실패 사유를 집계.
            # (성공 행은 upsert 에서 NULL 처리됨. 다음 재시도 reset 이 새 run_id 로 덮어씀.)
            cur.executemany(
                "UPDATE audio_files SET meta_extracted = ?, meta_error = ? WHERE id = ?",
                rows
            )
            conn.commit()
        finally:
            conn.close()

    # ─────────── Phase3: 유사 사운드 검색 특징 ───────────

    def get_paths_for_similarity(self, limit: int = 1000) -> List[tuple]:
        """Phase3 대상: sim_extracted=0 인 (id, path, duration) 목록.

        숨김/제거 행은 제외 — Phase2 의 get_paths_for_phase2 와 같은 기준.
        메타 추출이 안 끝난 행(meta_extracted=0)은 duration 을 모르므로 제외한다
        (긴 파일 창 분할 판단에 duration 이 필요).
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, file_path, duration FROM audio_files "
                "WHERE sim_extracted = 0 AND meta_extracted = 1 "
                "AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0 LIMIT ?",
                (limit,),
            )
            return cur.fetchall()
        finally:
            conn.close()

    _SIM_ELIGIBLE_SQL = (
        "SELECT COUNT(*) FROM audio_files WHERE meta_extracted = 1 "
        "AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0"
    )

    def refresh_similarity_total(self) -> int:
        """추출 대상 총계를 다시 세어 index_meta 에 캐시한다 (실행 시작 시 1회).

        이 집계는 실제 DB 에서 997ms 다 — 진행률 폴링마다 돌릴 수 없어
        시작 시 한 번만 재고, 이후 진행률은 (총계 − 대기 − 실패) 로 계산한다.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            total = int(cur.execute(self._SIM_ELIGIBLE_SQL).fetchone()[0] or 0)
            cur.execute(
                "INSERT INTO index_meta (key,value) VALUES ('sim_total_eligible',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(total),),
            )
            conn.commit()
            return total
        finally:
            conn.close()

    def get_similarity_progress(self) -> Dict[str, int]:
        """(대기/완료/실패/총계/벡터행) 집계. 진행률·ETA 표시용.

        실측(2026-09-10, 실제 DB 사본): 대기 COUNT 86ms(커버링 부분색인) +
        실패 COUNT 0ms. 총계는 캐시값을 쓰고, 없으면 이 자리에서 한 번 계산한다.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            pending = int(cur.execute(
                "SELECT COUNT(*) FROM audio_files WHERE sim_extracted = 0 "
                "AND meta_extracted = 1 AND IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0"
            ).fetchone()[0] or 0)
            failed = int(cur.execute(
                "SELECT COUNT(*) FROM audio_files WHERE sim_extracted = 2"
            ).fetchone()[0] or 0)
            row = cur.execute(
                "SELECT value FROM index_meta WHERE key='sim_total_eligible'"
            ).fetchone()
            if row and str(row[0]).isdigit():
                total = int(row[0])
            else:
                total = int(cur.execute(self._SIM_ELIGIBLE_SQL).fetchone()[0] or 0)
                cur.execute(
                    "INSERT INTO index_meta (key,value) VALUES ('sim_total_eligible',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(total),),
                )
                conn.commit()
            # 캐시가 오래돼 대기+실패가 총계를 넘으면 총계를 끌어올린다 (음수 방지)
            total = max(total, pending + failed)
            vectors = int(cur.execute(
                "SELECT COUNT(*) FROM similarity_vectors").fetchone()[0] or 0)
            return {"pending": pending, "done": max(0, total - pending - failed),
                    "failed": failed, "total": total, "vectors": vectors}
        finally:
            conn.close()

    def save_similarity_vectors(self, results: Dict[str, List[tuple]],
                                run_id: Optional[str] = None) -> int:
        """추출 결과 저장 (배치).

        results: { file_id -> [(win, start_sec, end_sec, vec_bytes), ...] }
        빈 목록이면 '무음 등으로 벡터 없음' 을 뜻하고 sim_extracted=1 로 완료 처리한다
        (재실행마다 같은 파일을 다시 열지 않기 위함).
        반환: 처리한 파일 수.
        """
        if not results:
            return 0
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            ids = list(results.keys())
            # 같은 파일의 옛 벡터는 통째로 교체 (창 수가 줄어드는 경우 잔여 행 방지)
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"DELETE FROM similarity_vectors WHERE file_id IN ({ph})", chunk
                )
            rows = [(fid, int(win), float(s), float(e), vec)
                    for fid, wins in results.items() for win, s, e, vec in wins]
            if rows:
                cur.executemany(
                    "INSERT INTO similarity_vectors (file_id, win, start_sec, end_sec, vec) "
                    "VALUES (?,?,?,?,?)", rows
                )
            cur.executemany(
                "UPDATE audio_files SET sim_extracted = 1, sim_error = NULL, sim_run_id = ? "
                "WHERE id = ?",
                [(run_id, fid) for fid in ids],
            )
            conn.commit()
            return len(ids)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_similarity_failed(self, error_map: Dict[str, str],
                               run_id: Optional[str] = None) -> int:
        """추출 실패 마킹 (sim_extracted = 2). error_map: { file_id -> 사유 }"""
        if not error_map:
            return 0
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.executemany(
                "UPDATE audio_files SET sim_extracted = 2, sim_error = ?, sim_run_id = ? "
                "WHERE id = ?",
                [(msg[:500], run_id, fid) for fid, msg in error_map.items()],
            )
            conn.commit()
            return len(error_map)
        finally:
            conn.close()

    def reset_similarity(self, prefix: Optional[str] = None,
                         failed_only: bool = False) -> int:
        """재추출 큐 진입 — sim_extracted=0 으로 되돌린다.

        prefix 를 주면 그 폴더 이하만. failed_only 면 실패분만.
        벡터 행은 남겨둔다 — 재추출이 파일 단위로 통째 교체하므로 불일치가 생기지 않고,
        재추출 완료 전까지 기존 검색이 계속 동작한다.
        """
        where = ["1=1"]
        params: List[object] = []
        if failed_only:
            where.append("sim_extracted = 2")
        else:
            where.append("IFNULL(sim_extracted,0) <> 0")
        if prefix:
            where.append("file_path LIKE ? COLLATE NOCASE")
            params.append(f"{prefix}%")
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE audio_files SET sim_extracted = 0, sim_error = NULL "
                f"WHERE {' AND '.join(where)}", params
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def load_similarity_matrix(self) -> Tuple[List[tuple], bytes]:
        """전체 벡터를 한 번에 읽어온다 (상주 검색 프로세스가 1회 호출).

        반환: (행 메타 [(file_id, win, start_sec, end_sec), ...], 벡터 바이트 연결본)
        바이트는 호출부에서 numpy float16 (n, DIM) 으로 reshape 한다 — 이 모듈이
        numpy 에 의존하지 않게 하기 위해 변환은 호출부 책임.

        실측(2026-09-10, 200만 행): 2.2초 / 벡터 332MB.
        숨김·제거 행 필터는 여기서 하지 않는다 — 조인 비용이 크고, 검색 시점에
        결과 필터로 걸러내는 편이 싸다.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT file_id, win, start_sec, end_sec, vec FROM similarity_vectors"
            )
            meta: List[tuple] = []
            buf = bytearray()
            for fid, win, s, e, vec in cur:
                meta.append((fid, win, s, e))
                buf += vec
            return meta, bytes(buf)
        finally:
            conn.close()

    def delete_similarity_vectors(self, file_ids: List[str]) -> int:
        """파일 삭제/제거 경로에서 벡터를 함께 지운다 (고아 행 방지)."""
        if not file_ids:
            return 0
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            for i in range(0, len(file_ids), 500):
                chunk = file_ids[i:i + 500]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"DELETE FROM similarity_vectors WHERE file_id IN ({ph})", chunk
                )
            conn.commit()
            return len(file_ids)
        finally:
            conn.close()

    def reset_failed_metadata_under(self, prefix: str) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE audio_files
                SET meta_extracted = 0
                WHERE meta_extracted = 2
                  AND file_path LIKE ? COLLATE NOCASE
                """,
                (f"{prefix}%",)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def restore_removed_failed(self) -> int:
        """메타 분석 실패로 '제거'(removed=1) 됐던 항목의 제거 표시만 해제.
        meta_extracted=2 인 행만 대상 — 정상 파일을 사용자가 직접 제거한 건은 건드리지 않음.
        (분석기 v2 전환 1회용. FTS 행은 남아 있어 removed 해제만으로 검색에 복귀.)"""
        self._folders_cache = None
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE audio_files SET removed = 0 WHERE removed = 1 AND meta_extracted = 2"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def reset_all_failed_metadata(self) -> int:
        """전체 실패분(meta_extracted=2) → 0. 메타 분석기 버전 상승 시 1회 자동 재분석용.
        사유(meta_error)도 함께 비워 이전 버전 문구가 남지 않게 한다.

        hidden/removed 는 일부러 필터하지 않고 값도 건드리지 않는다 — 표시 상태(숨김/제거)는
        그대로 유지되므로 사용자가 제거한 항목이 되살아나지 않고, 나중에 복원/전체갱신으로
        다시 보이게 될 때 새 분석기로 분석된다. (제거분을 건너뛰면 버전 마커만 올라가
        복원 후에도 영구히 재분석 안 되는 함정이 생김.)"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE audio_files SET meta_extracted = 0, meta_error = NULL "
                "WHERE meta_extracted = 2"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def count_pending_metadata(self) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM audio_files "
                "WHERE meta_extracted = 0 AND IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0"
            )
            return cur.fetchone()[0]
        finally:
            conn.close()

    def phase2_run_outcome(self, run_id: str) -> Dict:
        """범위 한정 재시도(run_id) 결과 집계 — 완료 후 팝업용.
        반환: {"success": int, "failed": int, "reasons": [(메타에러, 개수), ...]}.
        run_id 로 스탬프된 행만 보므로 그 재시도가 건드린 항목만 집계됨."""
        out = {"success": 0, "failed": 0, "reasons": []}
        if not run_id:
            return out
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM audio_files "
                "WHERE phase2_run_id = ? AND meta_extracted = 1", (run_id,)
            )
            out["success"] = int(cur.fetchone()[0] or 0)
            cur.execute(
                "SELECT IFNULL(meta_error,'알 수 없는 오류') AS e, COUNT(*) AS c "
                "FROM audio_files WHERE phase2_run_id = ? AND meta_extracted = 2 "
                "GROUP BY e ORDER BY c DESC", (run_id,)
            )
            rows = cur.fetchall()
            out["reasons"] = [(r[0], int(r[1])) for r in rows]
            out["failed"] = sum(c for _, c in out["reasons"])
            return out
        finally:
            conn.close()

    def upsert_files(self, files: List[AudioFile], defer_fts: bool = False):
        """Phase 2: 메타데이터 포함 풀 업서트. meta_extracted=1 마킹.

        defer_fts=True: search_fts INSERT 를 스킵. Phase2 처리량 향상용 — Phase2
        종료 후 `bulk_fts_upsert_from_audio_files()` 로 묶음 반영. Phase2 진행 중엔
        새 메타가 search_fts 에 반영되지 않으므로 메타 키워드 검색은 stale
        (단, file_name/file_path 는 Phase1 에서 이미 반영되어 파일명 검색은 정상).
        """
        if not files:
            return
        self._folders_cache = None

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")

            rows = [
                (
                    f.file_id, f.file_path, f.file_name, f.file_size,
                    f.duration, f.sample_rate, f.channels, f.bit_depth,
                    f.codec, f.bitrate, f.title, f.artist, f.album,
                    f.genre, f.comments, f.description, f.keywords,
                    f.category, f.sub_category, f.source, f.modified_at,
                    f.meta_error
                )
                for f in files
            ]
            cur.executemany("""
                INSERT INTO audio_files
                (id, file_path, file_name, file_size, duration, sample_rate,
                 channels, bit_depth, codec, bitrate, title, artist, album,
                 genre, comments, description, keywords, category, sub_category,
                 source, modified_at, meta_extracted, meta_error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(id) DO UPDATE SET
                    file_size=excluded.file_size,
                    duration=excluded.duration,
                    sample_rate=excluded.sample_rate,
                    channels=excluded.channels,
                    bit_depth=excluded.bit_depth,
                    codec=excluded.codec,
                    bitrate=excluded.bitrate,
                    title=excluded.title,
                    artist=excluded.artist,
                    album=excluded.album,
                    genre=excluded.genre,
                    comments=excluded.comments,
                    description=excluded.description,
                    keywords=excluded.keywords,
                    category=excluded.category,
                    sub_category=excluded.sub_category,
                    source=excluded.source,
                    modified_at=excluded.modified_at,
                    indexed_at=strftime('%s','now'),
                    meta_extracted=1,
                    phase2_run_id=NULL,
                    meta_error=excluded.meta_error
            """, rows)

            if not defer_fts:
                # FTS 업데이트 최적화: 메타데이터가 없는 파일(SFX 등)은 Phase1 에서 이미
                # 기본 정보가 등록되어 있으므로, 추가적인 FTS 업데이트를 스킵.
                fts_rows = []
                for f in files:
                    has_meta = any([
                        f.title, f.artist, f.album, f.genre, f.comments,
                        f.description, f.keywords, f.category, f.sub_category, f.source
                    ])
                    if has_meta:
                        fts_rows.append(Database._fts_row(
                            f.file_id, f.file_name, f.file_path,
                            (f.title or "", f.artist or "", f.album or "", f.genre or "",
                             f.comments or "", f.description or "", f.keywords or "",
                             f.category or "", f.sub_category or "", f.source or "")))

                if fts_rows:
                    cur.executemany("""
                        INSERT OR REPLACE INTO search_fts
                        (rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, comments,
                         description, keywords, category, sub_category, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, fts_rows)
                    self._term_fts_insert(cur, fts_rows)   # 단어색인 동기

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bulk_fts_upsert_from_audio_files(self, file_ids: List[str],
                                          chunk: int = 5000,
                                          progress_cb: Optional[Callable[[int, int], None]] = None) -> int:
        """defer_fts=True 로 누락된 search_fts 행을 audio_files 에서 끌어와 일괄 반영.
        Phase2 종료 시 1회 호출. has_meta 없는 파일은 스킵 (Phase1 entry 유지).
        반환: INSERT OR REPLACE 된 행 수.
        """
        if not file_ids:
            return 0
        
        # 1. 대상 ID들을 임시 테이블에 적재 (새 연결 사용)
        conn_tmp = self._connect()
        conn_tmp.isolation_level = None
        try:
            cur_tmp = conn_tmp.cursor()
            cur_tmp.execute("CREATE TEMP TABLE IF NOT EXISTS _phase2_touched (id TEXT PRIMARY KEY)")
            cur_tmp.execute("DELETE FROM _phase2_touched")
            cur_tmp.execute("BEGIN IMMEDIATE")
            try:
                cur_tmp.executemany("INSERT OR IGNORE INTO _phase2_touched(id) VALUES (?)",
                                    [(fid,) for fid in file_ids])
                cur_tmp.execute("COMMIT")
            except Exception:
                cur_tmp.execute("ROLLBACK")
                raise

            # 2. 데이터 읽기 전용 커서 (conn_tmp 유지)
            cur_select = conn_tmp.cursor()
            cur_select.execute("""
                SELECT af.id, af.file_name, af.file_path, af.title, af.artist,
                       af.album, af.genre, af.comments, af.description,
                       af.keywords, af.category, af.sub_category, af.source
                FROM audio_files af
                JOIN _phase2_touched t ON t.id = af.id
                WHERE af.title IS NOT NULL OR af.artist IS NOT NULL
                   OR af.album IS NOT NULL OR af.genre IS NOT NULL
                   OR af.comments IS NOT NULL OR af.description IS NOT NULL
                   OR af.keywords IS NOT NULL OR af.category IS NOT NULL
                   OR af.sub_category IS NOT NULL OR af.source IS NOT NULL
            """)

            # 3. 쓰기 전용 별도 연결 (읽기 커서 보호를 위해 필수)
            conn_write = self._connect()
            conn_write.isolation_level = None
            
            total = 0
            processed = 0
            n = len(file_ids)
            
            try:
                while True:
                    rows = cur_select.fetchmany(chunk)
                    if not rows:
                        break
                    
                    fts_rows = [
                        Database._fts_row(
                            r["id"], r["file_name"], r["file_path"],
                            (r["title"] or "", r["artist"] or "", r["album"] or "",
                             r["genre"] or "", r["comments"] or "", r["description"] or "",
                             r["keywords"] or "", r["category"] or "",
                             r["sub_category"] or "", r["source"] or ""))
                        for r in rows
                    ]

                    cur_write = conn_write.cursor()
                    cur_write.execute("BEGIN IMMEDIATE")
                    try:
                        cur_write.executemany("""
                            INSERT OR REPLACE INTO search_fts
                            (rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, comments,
                             description, keywords, category, sub_category, source)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, fts_rows)
                        self._term_fts_insert(cur_write, fts_rows)   # 단어색인 동기
                        cur_write.execute("COMMIT")
                    except Exception:
                        cur_write.execute("ROLLBACK")
                        raise
                    
                    total += len(fts_rows)
                    processed += len(rows)
                    if progress_cb:
                        progress_cb(processed, n)
            finally:
                conn_write.close()

            cur_tmp.execute("DROP TABLE _phase2_touched")
        finally:
            conn_tmp.close()
            
        return total

    def delete_files(self, file_paths: List[str],
                     progress_cb: Optional[Callable[[int, int], None]] = None):
        """파일 삭제. FTS DELETE 는 rowid 인덱스 사용.
        progress_cb(done, total): 청크 단위 진행 보고 — 수십만 건 삭제 시
        UI 진행률 표시용 (트라이그램 FTS 삭제가 느려 수 분 걸릴 수 있음).
        반환: 고아 중복 후보 id 리스트 (복원은 갱신 완료 팝업 승인 후)."""
        if not file_paths:
            return []
        self._folders_cache = None
        ids = [self.generate_id(p) for p in file_paths]
        rowids = [Database.fts_rowid(fid) for fid in ids]
        total = len(ids)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            # 삭제될 (파일명,크기) 그룹 스냅샷 — 물리 삭제라 행이 사라지기 '전'에.
            # NAS 에서 원본이 지워져 살아있던 사본이 사라질 때, 다른 라이브러리에
            # 숨겨둔 쌍둥이가 미아가 되는 것을 삭제 후 복원으로 방지.
            cur.execute(
                "CREATE TEMP TABLE _dup_groups "
                "(file_name TEXT, file_size INTEGER, PRIMARY KEY(file_name, file_size)) "
                "WITHOUT ROWID"
            )
            CHUNK = 500
            for i in range(0, total, CHUNK):
                chunk_ids = ids[i:i + CHUNK]
                chunk_rids = rowids[i:i + CHUNK]
                id_ph = ",".join(["?"] * len(chunk_ids))
                rid_ph = ",".join(["?"] * len(chunk_rids))
                cur.execute(
                    f"INSERT OR IGNORE INTO _dup_groups "
                    f"SELECT DISTINCT file_name, file_size FROM audio_files "
                    f"WHERE id IN ({id_ph}) AND file_size IS NOT NULL",
                    chunk_ids
                )
                cur.execute(
                    f"DELETE FROM search_fts WHERE rowid IN ({rid_ph})",
                    chunk_rids
                )
                Database._term_fts_delete(cur, chunk_rids)   # 단어색인 동기
                if SIMILARITY_ENABLED:
                    _sim.delete_vectors_by_ids(cur, chunk_ids)
                cur.execute(
                    f"DELETE FROM audio_files WHERE id IN ({id_ph})",
                    chunk_ids
                )
                if progress_cb:
                    progress_cb(min(i + CHUNK, total), total)
            orphan_ids = self._find_orphan_dup_candidates(cur)
            cur.execute("DROP TABLE _dup_groups")
            conn.commit()
            return orphan_ids
        finally:
            conn.close()

    # ─────────── 검색 숨김 (중복 수리) ───────────
    # 인덱스/FTS 는 그대로 두고 hidden=1 만 세움 — 검색 결과에서만 사라짐.
    # 재스캔 시 파일이 이미 인덱스에 있으므로(mtime 동일) 재등록/재분석도 없음.
    def hide_files(self, paths: List[str],
                   progress_cb: Optional[Callable[[int, int], None]] = None) -> int:
        if not paths:
            return 0
        ids = [self.generate_id(p) for p in paths]
        total = len(ids)
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN")
            CHUNK = 500
            for i in range(0, total, CHUNK):
                chunk = ids[i:i + CHUNK]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"UPDATE audio_files SET hidden = 1 WHERE id IN ({ph})", chunk
                )
                if progress_cb:
                    progress_cb(min(i + CHUNK, total), total)
            conn.commit()
            return total
        finally:
            conn.close()

    def unhide_files(self, paths: List[str],
                     progress_cb: Optional[Callable[[int, int], None]] = None) -> int:
        """검색 제외 해제. progress_cb(처리한 개수, 전체 개수) — hide_files 와 같은 규격.
        (수십만 건이 걸릴 수 있어 화면이 진행 상황을 보여줄 수 있어야 한다.)"""
        if not paths:
            return 0
        ids = [self.generate_id(p) for p in paths]
        total = len(ids)
        conn = self._connect()
        try:
            cur = conn.cursor()
            n = 0
            CHUNK = 500
            for i in range(0, total, CHUNK):
                chunk = ids[i:i + CHUNK]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"UPDATE audio_files SET hidden = 0, dup_orphan = 0 "
                    f"WHERE id IN ({ph})", chunk
                )
                n += cur.rowcount
                if progress_cb:
                    progress_cb(min(i + CHUNK, total), total)
            conn.commit()
            return n
        finally:
            conn.close()

    def unhide_all(self) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE audio_files SET hidden = 0, dup_orphan = 0 WHERE hidden = 1")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def mark_dup_orphans(self, ids: List[str]) -> int:
        """복원 팝업에서 [No] 선택 시 — '중복 아님·미복원' 표시. 제외 관리 목록에서
        태그로 노출. 복원(unhide) 시 함께 해제됨. 반환: 표시된 행 수."""
        if not ids:
            return 0
        conn = self._connect()
        try:
            cur = conn.cursor()
            n = 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"UPDATE audio_files SET dup_orphan = 1 "
                    f"WHERE id IN ({ph}) AND IFNULL(hidden,0) = 1", chunk)
                n += cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()

    def verify_dup_orphans(self, paths: List[str]) -> set:
        """제외 관리 표시 직전 검증 — dup_orphan 표시가 여전히 유효한지(보이는
        사본이 아직도 없는지) 확인. 쌍둥이가 재추가 등으로 돌아온 행은 다시 일반
        중복 숨김이므로 표시를 자동 해제 (stale 태그 방지).
        COLLATE NOCASE 프로브 — idx_file_name_nc 사용 (풀스캔 회피).
        반환: 여전히 '중복 아님' 인 path 집합."""
        if not paths:
            return set()
        conn = self._connect()
        try:
            cur = conn.cursor()
            still: set = set()
            back_to_dup: List[str] = []
            for i in range(0, len(paths), 400):
                chunk = paths[i:i + 400]
                ph = ",".join(["?"] * len(chunk))
                cur.execute(
                    f"SELECT h.file_path, "
                    f"  EXISTS(SELECT 1 FROM audio_files v "
                    f"    WHERE v.file_name = h.file_name COLLATE NOCASE "
                    f"      AND v.file_size = h.file_size "
                    f"      AND IFNULL(v.hidden,0) = 0 AND IFNULL(v.removed,0) = 0) "
                    f"FROM audio_files h WHERE h.file_path IN ({ph})", chunk)
                for p, has_visible in cur.fetchall():
                    if has_visible:
                        back_to_dup.append(p)
                    else:
                        still.add(p)
            if back_to_dup:
                for i in range(0, len(back_to_dup), 500):
                    chunk = back_to_dup[i:i + 500]
                    ph = ",".join(["?"] * len(chunk))
                    cur.execute(
                        f"UPDATE audio_files SET dup_orphan = 0 "
                        f"WHERE file_path IN ({ph})", chunk)
                conn.commit()
            return still
        finally:
            conn.close()

    @staticmethod
    def _esc_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def count_hidden(self, search: Optional[str] = None) -> int:
        """숨김 전체(또는 search 부분문자열 일치) 카운트 — 커버링 색인만 읽음."""
        conn = self._connect()
        try:
            if search:
                return conn.execute(
                    "SELECT COUNT(*) FROM audio_files WHERE hidden = 1 "
                    "AND file_path LIKE ? ESCAPE '\\'",
                    (f"%{self._esc_like(search)}%",)
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM audio_files WHERE hidden = 1"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()

    def get_hidden_paths(self, limit: Optional[int] = None,
                         search: Optional[str] = None) -> List[tuple]:
        """숨김 목록 — (file_path, dup_orphan) 튜플. 커버링 색인(idx_hidden_path_v2)
        만 읽어 cold 에도 즉시. dup_orphan=1 = 중복 아님·미복원 표시 대상.
        search: 경로/파일명 부분문자열 — 표시 상한과 무관하게 숨김 '전체'에서 검색."""
        conn = self._connect()
        try:
            sql = "SELECT file_path, IFNULL(dup_orphan, 0) FROM audio_files WHERE hidden = 1"
            params: list = []
            if search:
                sql += " AND file_path LIKE ? ESCAPE '\\'"
                params.append(f"%{self._esc_like(search)}%")
            sql += " ORDER BY file_path"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(int(limit))
            return [(r[0], r[1]) for r in conn.execute(sql, params)]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def all_paths_under(self, prefix: str) -> List[str]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT file_path FROM audio_files WHERE file_path LIKE ? COLLATE NOCASE",
                (f"{prefix}%",)
            )
            return [r["file_path"] for r in cur.fetchall()]
        finally:
            conn.close()

    # 검색 가능한 필드 (다중 매처에서 사용)
    SEARCHABLE_FIELDS = [
        "file_name", "file_path", "title", "artist", "album", "genre", "comments",
        "description", "keywords", "category", "sub_category", "source",
    ]

    @staticmethod
    def _fts_escape(s: str) -> str:
        """FTS5 phrase 안에서 안전한 토큰. trigram tokenizer는 모든 문자 포함."""
        return s.replace('"', '""')

    @classmethod
    def _token_variants(cls, tok: str) -> List[str]:
        """검색 토큰 확장 — 결과 스코프 넓히기.
        1) 구분자(_,-,.) 포함 토큰 → 제거 변형 ('gun_shot' → 'gunshot', norm 컬럼 매칭)
        2) 영어 복수형 → 단수 변형 ('doors' → 'door', 'boxes' → 'box')
        trigram 은 substring 매칭이라 단수 검색은 복수 파일명을 이미 잡음 —
        반대 방향(복수 검색 → 단수 파일명)만 변형으로 보강.
        모든 변형은 trigram 최소 길이 3 을 지켜야 함."""
        out = [tok]
        stripped = cls._NAME_SEP_RE.sub("", tok)
        if stripped != tok and len(stripped) >= 3:
            out.append(stripped)
        plural = []
        for t in out:
            low = t.lower()
            if low.endswith("es") and len(t) >= 5:
                plural.append(t[:-2])
            if low.endswith("s") and not low.endswith("ss") and len(t) >= 4:
                plural.append(t[:-1])
            # 역방향 동사 굴절 — 사용자가 'clicking/clicked' 쳐도 'click' 파일 매칭.
            # 어간 길이 ≥4 가드로 'string'→'str' 류 과도 노이즈 차단. (정방향은 trigram이 이미 커버.)
            if low.endswith("ing") and len(t) >= 7:
                stem = t[:-3]
                plural.append(stem)
                plural.append(stem + "e")   # moving→move
            elif low.endswith("ed") and len(t) >= 6:
                stem = t[:-2]
                plural.append(stem)
                plural.append(stem + "e")   # moved→move
        for p in plural:
            if len(p) >= 4 and p not in out:
                out.append(p)
        return out

    @classmethod
    def _fts_term(cls, field: str, tok: str) -> str:
        """토큰 1개 → 변형 포함 FTS5 표현식. 변형 2개 이상이면 OR 그룹."""
        parts = []
        for v in cls._token_variants(tok):
            esc = cls._fts_escape(v)
            if field == "any":
                # 무필드 phrase 는 file_name_norm 포함 전 컬럼 매칭
                parts.append(f'"{esc}"')
            elif field == "file_name":
                parts.append(f'file_name:"{esc}"')
                parts.append(f'file_name_norm:"{esc}"')
            else:
                parts.append(f'{field}:"{esc}"')
        if len(parts) == 1:
            return parts[0]
        return "(" + " OR ".join(parts) + ")"

    # AND/OR/NOT 대문자 키워드 또는 괄호 감지용 — "any" 필드 입력에서만 활성.
    _QUERY_OPS_RE = re.compile(r'(?<![A-Za-z])(?:AND|OR|NOT)(?![A-Za-z])|[()]')
    # 토크나이저 — 괄호 단독, 큰따옴표 phrase, 나머지 공백 분리 단어.
    _QUERY_TOKEN_RE = re.compile(r'\(|\)|"[^"]*"|[^\s()]+')

    @classmethod
    def _build_fts_query(cls, text: str):
        """AND/OR/NOT(대문자) + 괄호 그룹화를 FTS5 MATCH 쿼리로 변환.
        일반 토큰은 phrase 로 quote — trigram 매칭. NOT 직후 단일 토큰은 ranking 에서
        제외(점수 왜곡 방지).
        Returns: (fts_query, rank_tokens)
        """
        if not text:
            return "", []
        ops = {"AND", "OR", "NOT"}
        out = []
        rank = []
        skip_rank = False
        for tok in cls._QUERY_TOKEN_RE.findall(text):
            if tok in ("(", ")"):
                out.append(tok)
            elif tok in ops:
                out.append(tok)
                skip_rank = (tok == "NOT")
            else:
                if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
                    # 사용자가 직접 quote 한 phrase — 변형 확장 없이 그대로
                    inner = tok[1:-1]
                    expr = f'"{cls._fts_escape(inner)}"'
                else:
                    inner = tok
                    expr = (cls._fts_term("any", inner) if len(inner) >= 3
                            else f'"{cls._fts_escape(inner)}"')
                if not skip_rank:
                    rank.append(inner.lower())
                skip_rank = False
                out.append(expr)
        return " ".join(out), rank

    # 페이지 경량 정렬 가중치 — 파일명/제목 매칭을 코멘트/경로보다 우선.
    _RANK_WEIGHTS = {
        "file_name": 6, "title": 5,
        "keywords": 4, "category": 3, "sub_category": 3,
        "description": 2, "source": 2,
        "artist": 1, "album": 1, "genre": 1, "comments": 1,
        "file_path": 0.5,
    }
    _BROAD_SYNONYM_STOPWORDS = {
        "electronic", "source", "misc", "data", "motion", "advisory",
    }
    _TOKEN_DELIMS = (" ", "_", "-", ".", ",", "(", ")")

    _SYNONYM_RANK_FACTOR = 0.3   # 동의어 매칭은 원문 가중치의 30% 만 — 항상 원문 정확매칭 아래로.

    def _rank_score(self, row: Dict, tokens: List[str],
                    synonym_tokens: Optional[List[str]] = None) -> float:
        """반환된 결과 내 관련도 점수. 토큰이 우선 필드에 있을수록 높음 (substring).
        synonym_tokens: 동의어 — 결과는 넓히되 작은 점수만 줘 원문보다 아래로 정렬."""
        s = 0.0
        for f, w in self._RANK_WEIGHTS.items():
            v = row.get(f)
            if not v:
                continue
            v = v.lower()
            for t in tokens:
                if not t:
                    continue
                if v == t:
                    s += w * 3
                elif t in v:
                    s += w
                    if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", v):
                        s += w
            if synonym_tokens:
                for t in synonym_tokens:
                    if t and t in v:
                        s += w * self._SYNONYM_RANK_FACTOR
        return s

    def _query_synonyms(self, token: str) -> List[str]:
        token = (token or "").strip()
        if len(token) < 4:
            return []
        cap = 3
        raw = thesaurus.get().expand(token, cap=cap * 3, max_groups=6)
        out: List[str] = []
        seen = {token.lower()}
        for s in raw:
            sl = s.lower()
            if len(s) < 3 or sl in seen or sl in self._BROAD_SYNONYM_STOPWORDS:
                continue
            seen.add(sl)
            out.append(s)
            if len(out) >= cap:
                break
        return out

    def _short_token_like(self, column: str, token: str) -> Tuple[str, List[str]]:
        # 1~2글자(trigram 불가) 토큰은 substring 매칭 — 'GameUI'처럼 붙여쓴 것도 잡아
        # recall 확보(MediaBay 수준). 'ui'가 'Inquisitive'에 걸리는 부분문자열 false
        # positive 는 전역 랭킹 + 단어경계 보너스(_rank_score)가 아래로 내려 흡수.
        # SQL `_`/`%` 와일드카드는 escape (literal 매칭 보장).
        def esc_like(s: str) -> str:
            return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        tok = esc_like(token)
        return (f"{column} LIKE ? ESCAPE '\\' COLLATE NOCASE", [f"%{tok}%"])

    def _direct_filter_clause(self, tokens: List[str]) -> Tuple[str, List[str]]:
        tokens = [t for t in tokens[:3] if t and len(t) >= 3]
        if not tokens:
            return "", []

        def esc_like(s: str) -> str:
            return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        primary_fields = ("file_name", "title", "keywords")
        secondary_fields = ("category", "sub_category", "description", "comments")
        clauses: List[str] = []
        params: List[str] = []
        for tok in tokens:
            if len(tok) <= 2:
                for f in primary_fields:
                    clause, p = self._short_token_like(f"a.{f}", tok)
                    clauses.append(clause)
                    params.extend(p)
                for f in secondary_fields:
                    clause, p = self._short_token_like(f"a.{f}", tok)
                    clauses.append(clause)
                    params.extend(p)
            else:
                pat = f"%{esc_like(tok)}%"
                for f in primary_fields:
                    clauses.append(f"a.{f} LIKE ? ESCAPE '\\' COLLATE NOCASE")
                    params.append(pat)
                for f in secondary_fields:
                    clauses.append(f"a.{f} LIKE ? ESCAPE '\\' COLLATE NOCASE")
                    params.append(pat)
        return "(" + " OR ".join(clauses) + ")", params

    # bm25 컬럼 가중치 — search_fts_term 컬럼 순서대로(file_id UNINDEXED 포함 14개).
    # 값 클수록 그 컬럼 매칭이 더 중요(점수 우대). _RANK_WEIGHTS 와 동일 우선순위.
    _TERM_BM25_WEIGHTS = (0.0, 10.0, 10.0, 1.0, 8.0, 2.0, 2.0, 2.0, 1.0, 3.0, 6.0, 4.0, 4.0, 2.0)

    def _query_term(self, matchers=None, min_duration=0, max_duration=None,
                    sample_rate=None, channels=None, min_channels=None, path_prefix=None,
                    path_prefixes=None, exclude_prefixes=None, apply_blacklist=True,
                    cancel_check=None, batch_callback=None, batch_size=120,
                    limit=500) -> List[Dict]:
        """정확한 검색 — search_fts_term(porter unicode61) MATCH + 네이티브 bm25 전역 정렬.
        동의어/변형/짧은토큰 LIKE 없음(정밀). 단어 단위 + 어간 + `term*` 접두."""
        def term_expr(field, tok):
            tok = (tok or "").strip()
            if not tok:
                return None
            prefix = tok.endswith("*")
            core = (tok[:-1] if prefix else tok).replace('"', '""')
            if not core:
                return None
            suf = "*" if prefix else ""
            if field == "any":
                return f'"{core}"{suf}'
            if field == "file_name":
                return f'(file_name:"{core}"{suf} OR file_name_norm:"{core}"{suf})'
            if field in self.SEARCHABLE_FIELDS:
                return f'{field}:"{core}"{suf}'
            return f'"{core}"{suf}'

        fts_parts: List[str] = []
        def emit(op, expr):
            fts_parts.append(expr if not fts_parts else f"{op} {expr}")

        if matchers:
            for i, m in enumerate(matchers):
                value = (m.get("value") or "").strip()
                if not value:
                    continue
                field = m.get("field")
                op = m.get("operator")
                is_root = (op is None) or (i == 0)
                subs = [e for e in (term_expr(field, t) for t in value.split()) if e]
                if not subs:
                    continue
                expr = " AND ".join(subs)
                if len(subs) > 1:
                    expr = f"({expr})"
                emit("AND" if is_root else op, expr)
        if fts_parts and fts_parts[0].startswith("NOT "):
            fts_parts.pop(0)

        params: List = []
        if fts_parts:
            sql = ("SELECT a.* FROM audio_files a "
                   "JOIN search_fts_term ON search_fts_term.file_id = a.id "
                   "WHERE search_fts_term MATCH ?")
            params.append(" ".join(fts_parts))
        else:
            sql = "SELECT a.* FROM audio_files a WHERE 1=1"
        sql += " AND IFNULL(a.hidden,0) = 0 AND IFNULL(a.removed,0) = 0"

        if path_prefix:
            sql += " AND a.file_path LIKE ? COLLATE NOCASE"
            params.append(f"{path_prefix}%")
        if path_prefixes:
            cl = []
            for pre in path_prefixes:
                if pre:
                    cl.append("a.file_path LIKE ? COLLATE NOCASE")
                    params.append(f"{pre}%")
            if cl:
                sql += " AND (" + " OR ".join(cl) + ")"
        if min_duration and min_duration > 0:
            sql += " AND a.duration IS NOT NULL AND a.duration >= ?"; params.append(min_duration)
        if max_duration:
            sql += " AND a.duration IS NOT NULL AND a.duration <= ?"; params.append(max_duration)
        if sample_rate:
            sql += " AND a.sample_rate = ?"; params.append(sample_rate)
        if channels:
            sql += " AND a.channels = ?"; params.append(channels)
        if min_channels:
            sql += " AND a.channels IS NOT NULL AND a.channels >= ?"
            params.append(min_channels)
        for ex in (exclude_prefixes or []):
            if ex:
                sql += " AND a.file_path NOT LIKE ? COLLATE NOCASE"; params.append(f"{ex}%")
        if apply_blacklist:
            try:
                bl = [p for p in ((row.get("path") or "").rstrip("\\/")
                                  for row in self.get_blacklist_paths()) if p]
            except sqlite3.OperationalError:
                bl = []
            for p in bl:
                sql += (" AND a.file_path <> ? COLLATE NOCASE"
                        " AND a.file_path NOT LIKE ? COLLATE NOCASE")
                params.append(p); params.append(f"{p}{os.sep}%")

        if fts_parts:
            wts = ", ".join(str(w) for w in self._TERM_BM25_WEIGHTS)
            sql += f" ORDER BY bm25(search_fts_term, {wts})"
        sql += " LIMIT ?"
        params.append(int(limit))

        conn = self._connect()
        try:
            if cancel_check is not None:
                conn.set_progress_handler(lambda: 1 if cancel_check() else 0, 1000)
            cur = conn.cursor()
            cur.execute(sql, params)
            results: List[Dict] = []
            while True:
                fetched = cur.fetchmany(max(1, int(batch_size)))
                if not fetched:
                    break
                results.extend(dict(r) for r in fetched)
                if cancel_check is not None and cancel_check():
                    break
        finally:
            if cancel_check is not None:
                conn.set_progress_handler(None, 0)
            conn.close()
        # 이미 bm25 정렬 순서로 fetch — 배치로 그대로 송출(정렬 유지).
        if batch_callback is not None:
            bs = max(1, int(batch_size))
            for i in range(0, len(results), bs):
                batch_callback(results[i:i + bs])
        return results

    def query(self, matchers: List[Dict] = None,
              min_duration: float = 0, max_duration: Optional[float] = None,
              sample_rate: Optional[int] = None, channels: Optional[int] = None,
              min_channels: Optional[int] = None,
              path_prefix: Optional[str] = None,
              path_prefixes: Optional[List[str]] = None,
              exclude_prefixes: Optional[List[str]] = None,
              apply_blacklist: bool = True,
              use_thesaurus: bool = True,
              precise: bool = False,
              cancel_check: Optional[Callable[[], bool]] = None,
              batch_callback: Optional[Callable[[List[Dict]], None]] = None,
              batch_size: int = 200,
              limit: int = 500) -> List[Dict]:
        """
        통합 쿼리.
        - 텍스트 매처는 FTS5 MATCH (trigram).
        - 1~2글자는 FTS 매칭이 깨질 수 있어 LIKE 폴백.
        - use_thesaurus: "any" 필드 토큰을 UCS 동의어로 (원어 OR 동의어) 확장.
          NOT 매처에는 미적용 — 동의어까지 제외하면 과도하게 좁아짐.
        - 나머지 필터는 audio_files 컬럼.
        - precise=True: 단어색인(search_fts_term, porter)으로 정밀 검색 + bm25 정렬.
        """
        if precise:
            return self._query_term(
                matchers=matchers, min_duration=min_duration, max_duration=max_duration,
                sample_rate=sample_rate, channels=channels, min_channels=min_channels,
                path_prefix=path_prefix,
                path_prefixes=path_prefixes, exclude_prefixes=exclude_prefixes,
                apply_blacklist=apply_blacklist, cancel_check=cancel_check,
                batch_callback=batch_callback, batch_size=batch_size, limit=limit,
            )
        params: List = []
        like_clauses: List[str] = []
        like_params: List = []
        rank_tokens: List[str] = []
        synonym_tokens: List[str] = []   # 동의어 — 결과엔 포함하되 점수는 작게만(원문 우선)

        # 매처별 (operator, fts_sub_expr) 누적. 첫 매처(operator=None)는 루트,
        # 그 외엔 AND/OR/NOT 으로 join. 자식 매처는 FTS5 만 사용 (짧은 토큰 LIKE 폴백
        # 은 루트에만 적용 — LIKE 절은 SQL AND join 이라 OR/NOT 합성 곤란).
        fts_parts: List[str] = []  # 누적된 FTS 표현식 (이미 op + expr 형태로 포함)

        def _emit_to_fts(op_prefix: str, expr: str):
            if not fts_parts:
                fts_parts.append(expr)
            else:
                fts_parts.append(f"{op_prefix} {expr}")

        if matchers:
            for i, m in enumerate(matchers):
                field = m.get("field")
                value = (m.get("value") or "").strip()
                op = m.get("operator")  # 루트는 None, 자식은 "AND"/"OR"/"NOT"
                if not value:
                    continue
                is_root = (op is None) or (i == 0)

                # 1) 자체에 AND/OR/NOT/괄호 문법이 있는 "any" 입력은 FTS 통째 통과.
                if field == "any" and self._QUERY_OPS_RE.search(value):
                    fts_q, tokens = self._build_fts_query(value)
                    if fts_q:
                        wrapped = f"({fts_q})"
                        if is_root:
                            _emit_to_fts("AND", wrapped)
                        else:
                            _emit_to_fts(op, wrapped)
                        if op != "NOT":
                            rank_tokens.extend(tokens)
                    continue

                # 2) 일반 입력 — 공백 분해 후 토큰. 짧은 토큰 LIKE 폴백은 루트만.
                sub_terms: List[str] = []
                for tok in value.split():
                    if op != "NOT":
                        rank_tokens.append(tok.lower())
                    if field == "any":
                        # UCS 동의어 확장 — trigram 최소 길이(3) 미달 용어 제외
                        syns: List[str] = []
                        if use_thesaurus and op != "NOT":
                            syns = self._query_synonyms(tok)
                            synonym_tokens.extend(s.lower() for s in syns)
                        if len(tok) >= 3:
                            term = self._fts_term("any", tok)
                            if syns:
                                term = "(" + " OR ".join(
                                    [term] + [self._fts_term("any", s) for s in syns]
                                ) + ")"
                            sub_terms.append(term)
                        elif syns:
                            # 1~2글자(한글 등)는 trigram 불가 — 동의어 OR 그룹으로 검색.
                            # ('총' → gun/firearm... 영어 파일명 매칭)
                            sub_terms.append("(" + " OR ".join(
                                self._fts_term("any", s) for s in syns
                            ) + ")")
                        elif is_root:
                            short_clauses = []
                            for f in self.SEARCHABLE_FIELDS:
                                clause, p = self._short_token_like(f"a.{f}", tok)
                                short_clauses.append(clause)
                                like_params.extend(p)
                            like_clauses.append("(" + " OR ".join(short_clauses) + ")")
                    elif field in self.SEARCHABLE_FIELDS:
                        if len(tok) >= 3:
                            sub_terms.append(self._fts_term(field, tok))
                        elif is_root:
                            clause, p = self._short_token_like(f"a.{field}", tok)
                            like_clauses.append(clause)
                            like_params.extend(p)

                if sub_terms:
                    expr = " AND ".join(sub_terms)
                    if len(sub_terms) > 1:
                        expr = f"({expr})"
                    if is_root:
                        _emit_to_fts("AND", expr)
                    else:
                        _emit_to_fts(op, expr)

        # 첫 토큰이 NOT 으로 시작하면 FTS5 syntax 오류 — 무시 (단독 NOT 불가).
        if fts_parts and fts_parts[0].startswith("NOT "):
            fts_parts.pop(0)

        if fts_parts:
            sql = (
                "SELECT a.* FROM audio_files a "
                "JOIN search_fts ON search_fts.file_id = a.id "
                "WHERE search_fts MATCH ?"
            )
            params.insert(0, " ".join(fts_parts))
        else:
            sql = "SELECT a.* FROM audio_files a WHERE 1=1"

        for c in like_clauses:
            sql += f" AND {c}"
        params.extend(like_params)

        # 중복 수리로 숨긴 파일 제외 — 행당 정수 비교 1회 (성능 영향 무시 가능)
        sql += " AND IFNULL(a.hidden,0) = 0 AND IFNULL(a.removed,0) = 0"

        if path_prefix:
            sql += " AND a.file_path LIKE ? COLLATE NOCASE"
            params.append(f"{path_prefix}%")
        if path_prefixes:
            clauses = []
            for prefix in path_prefixes:
                if not prefix:
                    continue
                clauses.append("a.file_path LIKE ? COLLATE NOCASE")
                params.append(f"{prefix}%")
            if clauses:
                sql += " AND (" + " OR ".join(clauses) + ")"

        # duration 필터가 켜진 경우에는 길이를 아는 파일만 포함한다.
        # 길이를 모르는(duration=NULL) 실패/미분석 파일을 포함하면 "최대 1초" 결과에
        # 재생 불가 파일이 섞인다.
        if min_duration and min_duration > 0:
            sql += " AND a.duration IS NOT NULL AND a.duration >= ?"
            params.append(min_duration)
        if max_duration:
            sql += " AND a.duration IS NOT NULL AND a.duration <= ?"
            params.append(max_duration)
        if sample_rate:
            sql += " AND a.sample_rate = ?"
            params.append(sample_rate)
        if channels:
            sql += " AND a.channels = ?"
            params.append(channels)
        if min_channels:
            sql += " AND a.channels IS NOT NULL AND a.channels >= ?"
            params.append(min_channels)

        # 블랙리스트 prefix 제외 — 호출자가 명시한 exclude_prefixes + DB blacklist_paths 자동 합산.
        # 인덱싱은 그대로 두고 검색 결과에서만 가린다.
        excludes: List[str] = []
        if exclude_prefixes:
            excludes.extend(p for p in exclude_prefixes if p)
        for ex in excludes:
            sql += " AND a.file_path NOT LIKE ? COLLATE NOCASE"
            params.append(f"{ex}%")
        # 블랙리스트 — 폴더면 하위 전체, 파일이면 그 경로 정확히 제외 (둘 다 커버).
        # 폴더 항목엔 정확일치(=)가 어차피 파일과 안 맞아 무해, 파일 항목엔 prefix(\%)가
        # 무해 → 두 조건을 모두 걸어 파일/폴더 블랙리스트를 한 경로로 지원.
        #
        # 하이브리드 (실측 기준): 항목마다 WHERE 조건 2개를 덧붙이는 인라인 방식이
        # 가장 빠르지만 SQLite expression depth 한도(1000) 때문에 ~250개에서
        # "Expression tree is too large" 크래시. ≤100개는 인라인(속도 우선),
        # 초과 시 MATERIALIZED CTE + NOT EXISTS (크기 무관, 70k행/500개 실측
        # 1.7s vs 인라인 크래시). rtrim/패턴은 CTE 에서 1회만 계산 — 행마다
        # 재계산하는 단순 NOT EXISTS 는 인라인 대비 8배 느려서 기각.
        if apply_blacklist:
            try:
                bl_paths = [
                    p for p in (
                        (row.get("path") or "").rstrip("\\/")
                        for row in self.get_blacklist_paths()
                    ) if p
                ]
            except sqlite3.OperationalError:
                bl_paths = []  # 마이그레이션 전 DB — 테이블 없으면 무시
            if len(bl_paths) <= 100:
                for p in bl_paths:
                    sql += (" AND a.file_path <> ? COLLATE NOCASE"
                            " AND a.file_path NOT LIKE ? COLLATE NOCASE")
                    params.append(p)
                    params.append(f"{p}{os.sep}%")
            else:
                # rtrim <> '' 가드: 빈/구분자만 남는 행이 '\%' prefix 로 전체
                # (특히 UNC \\서버 경로)를 가리는 사고 방지 (인라인 경로의
                # `if p` 필터와 동일 의미).
                sql = (
                    "WITH _bl(p, pat) AS MATERIALIZED ("
                    " SELECT rtrim(path, '\\/'), rtrim(path, '\\/') || '\\%'"
                    " FROM blacklist_paths WHERE rtrim(path, '\\/') <> '') "
                ) + sql
                sql += (
                    " AND NOT EXISTS (SELECT 1 FROM _bl"
                    " WHERE a.file_path = _bl.p COLLATE NOCASE"
                    " OR a.file_path LIKE _bl.pat)"
                )

        conn = self._connect()
        try:
            if cancel_check is not None:
                conn.set_progress_handler(lambda: 1 if cancel_check() else 0, 1000)
            cur = conn.cursor()
            results: List[Dict] = []
            seen_paths = set()

            def emit_or_collect(rows: List[Dict]):
                # 전역 정렬을 위해 일단 모으기만 — 배치 단위 정렬 폐기(후보 전체에서 줄세움).
                if rows:
                    results.extend(rows)

            def run_part(part_sql: str, part_params: List, target: int):
                if target <= 0:
                    return
                cur.execute(part_sql + " LIMIT ?", part_params + [target])
                while True:
                    fetched = cur.fetchmany(max(1, int(batch_size)))
                    if not fetched:
                        break
                    rows = []
                    for r in fetched:
                        row = dict(r)
                        path = row.get("file_path")
                        if path and path in seen_paths:
                            continue
                        if path:
                            seen_paths.add(path)
                        rows.append(row)
                    emit_or_collect(rows)
                    if cancel_check is not None and cancel_check():
                        break

            direct_clause, direct_params = self._direct_filter_clause(rank_tokens)
            if direct_clause:
                run_part(sql + f" AND {direct_clause}", params + direct_params, limit)
            if cancel_check is None or not cancel_check():
                remaining = max(0, int(limit) - len(seen_paths))
                if remaining > 0:
                    run_part(sql, params, remaining + len(seen_paths))
        finally:
            if cancel_check is not None:
                conn.set_progress_handler(None, 0)
            conn.close()
        # 전역 줄세우기: 후보(LIMIT 이하) '전체'를 한 번에 관련도순 정렬한 뒤 내보낸다.
        # (이전엔 120 배치 안에서만 정렬돼 전역 최적이 위에 안 왔음.)
        # SQL ORDER BY bm25 는 매칭 전체를 점수화해 광범위 쿼리에서 40배 느려지므로 회피 —
        # 여기선 SQL 이 이미 LIMIT 로 후보를 잘랐고, 그 후보만 파이썬에서 점수화하므로 빠름.
        if rank_tokens and len(results) > 1:
            results.sort(
                key=lambda row: self._rank_score(row, rank_tokens, synonym_tokens),
                reverse=True,
            )
        # direct/일반 2단계 수집 + 파이썬 중복제거 때문에 합계가 limit 을 넘을 수 있다
        # (②의 SQL LIMIT 은 limit 이지만 seen 과 안 겹친 행은 그대로 추가됨).
        # 랭킹 정렬 후 상위 limit 개만 유지해 "최대 노출 개수" 약속을 지킨다.
        if len(results) > int(limit):
            results = results[:int(limit)]
        # 스트리밍 모드: 정렬 끝난 결과를 배치로 쪼개 UI append (한 번에 큰 갱신 방지).
        if batch_callback is not None:
            bs = max(1, int(batch_size))
            for i in range(0, len(results), bs):
                batch_callback(results[i:i + bs])
        return results

    def get_folders(self) -> List[str]:
        """인덱싱된 파일들의 부모 디렉토리 모두 (트리 빌드용). 캐시됨."""
        if self._folders_cache is not None:
            return self._folders_cache
        conn = self._connect()
        try:
            old_factory = conn.row_factory
            conn.row_factory = None
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT file_path FROM audio_files")
            folders = set()
            for (p,) in cur:
                idx = max(p.rfind("\\"), p.rfind("/"))
                if idx > 0:
                    folders.add(p[:idx])
            conn.row_factory = old_factory
            self._folders_cache = sorted(folders)
            return self._folders_cache
        finally:
            conn.close()

    def invalidate_folders_cache(self):
        self._folders_cache = None

    def get_indexed_paths(self) -> Dict[str, float]:
        """이미 인덱싱된 파일 경로 → mtime 매핑 (증분 인덱싱용)"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT file_path, modified_at FROM audio_files")
            return {r["file_path"]: r["modified_at"] for r in cur.fetchall()}
        finally:
            conn.close()

    def count_files(self) -> int:
        """UI 표시용 파일 수 — 숨김(중복 수리) 제외."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM audio_files WHERE IFNULL(hidden,0) = 0 AND IFNULL(removed,0) = 0")
            return cur.fetchone()[0]
        finally:
            conn.close()

    def is_fts_stale(self) -> bool:
        return self.get_meta("fts_stale") == "1"

    def rebuild_fts(self, progress_cb=None, cancel_event=None) -> bool:
        """search_fts 전체 재빌드. 백그라운드 스레드 호출 권장."""
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM audio_files").fetchone()[0]
            if progress_cb:
                try: progress_cb(0, total)
                except Exception: pass

            # 1. 기존 search_fts 통째로 DROP+CREATE — 거대 trigram FTS 에서
            # DELETE FROM 은 모든 행 + 토큰 인덱스 정리라 수분+. DROP/CREATE 는 수초.
            cur = conn.cursor()
            cur.execute("DROP TABLE IF EXISTS search_fts")
            cur.execute("""
                CREATE VIRTUAL TABLE search_fts USING fts5(
                    file_id UNINDEXED,
                    file_name,
                    file_name_norm,
                    file_path,
                    title, artist, album, genre, comments,
                    description, keywords, category, sub_category, source,
                    tokenize='trigram'
                )
            """)
            conn.commit()

            if progress_cb and total > 0:
                try: progress_cb(int(total * 0.01), total)
                except Exception: pass

            # 2. 데이터 재삽입
            BATCH = 5000
            last_rowid = 0
            done = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                
                rows = cur.execute(
                    "SELECT rowid, id, file_name, file_path, "
                    "COALESCE(title,''), COALESCE(artist,''), COALESCE(album,''), "
                    "COALESCE(genre,''), COALESCE(comments,''), COALESCE(description,''), "
                    "COALESCE(keywords,''), COALESCE(category,''), "
                    "COALESCE(sub_category,''), COALESCE(source,'') "
                    "FROM audio_files WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (last_rowid, BATCH)
                ).fetchall()
                if not rows:
                    break
                
                last_rowid = rows[-1][0]
                
                fts_rows = [Database._fts_row(r[1], r[2], r[3], r[4:]) for r in rows]
                cur.execute("BEGIN")
                cur.executemany(
                    "INSERT INTO search_fts "
                    "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, comments, "
                    " description, keywords, category, sub_category, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    fts_rows
                )
                Database._term_fts_insert(cur, fts_rows)   # 단어색인 동기 (FTS 재빌드 시 함께)
                conn.commit()

                done += len(rows)
                if progress_cb:
                    try: progress_cb(done, total)
                    except Exception: pass

            cur.execute("BEGIN")
            conn.execute(
                "INSERT INTO index_meta (key,value) VALUES ('fts_stale','0') "
                "ON CONFLICT(key) DO UPDATE SET value='0'"
            )
            # 경로 컬럼 규칙 표식 — 이 재빌드로 전체 행이 index_path() 규칙(폴더까지만)
            # 으로 통일됐음을 남긴다. 표식이 없으면 옛 규칙(전체 경로) 행이 섞여 있다는 뜻.
            conn.execute(
                "INSERT INTO index_meta (key,value) VALUES ('fts_path_mode','dir') "
                "ON CONFLICT(key) DO UPDATE SET value='dir'"
            )
            conn.commit()
            return True
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

    # ─────────── 단어 색인 (porter unicode61) — Phase-2 검색 R&D ───────────
    # 기존 trigram(부분문자열) 옆에 '단어 원형' 색인을 둔다. porter 어간분석으로
    # move↔moving↔moved 가 방향 무관 매칭되고, 네이티브 bm25 전역 랭킹/접두(term*)를
    # 지원. 한글/부분문자열은 trigram 이 계속 담당(라우팅은 이후 단계). 현재 검색은
    # 이 테이블을 아직 쓰지 않음(토대만).
    _TERM_FTS_COLS = (
        "file_id UNINDEXED, file_name, file_name_norm, file_path, title, artist, "
        "album, genre, comments, description, keywords, category, sub_category, source"
    )

    def term_index_count(self) -> int:
        """search_fts_term 행 수 (테이블 없으면 -1 → 빌드 필요 신호)."""
        conn = self._connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM search_fts_term").fetchone()[0]
        except sqlite3.OperationalError:
            return -1
        finally:
            conn.close()

    def build_term_index(self, progress_cb=None, cancel_event=None) -> bool:
        """search_fts_term(porter unicode61) 전체 빌드 — audio_files 에서 재삽입.
        rebuild_fts 와 동일 패턴(DROP+CREATE+배치). 백그라운드 스레드 권장."""
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM audio_files").fetchone()[0]
            if progress_cb:
                try: progress_cb(0, total)
                except Exception: pass
            cur = conn.cursor()
            cur.execute("DROP TABLE IF EXISTS search_fts_term")
            cur.execute(
                f"CREATE VIRTUAL TABLE search_fts_term USING fts5("
                f"{self._TERM_FTS_COLS}, tokenize='porter unicode61')"
            )
            conn.commit()
            BATCH = 5000
            last_rowid = 0
            done = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                rows = cur.execute(
                    "SELECT rowid, id, file_name, file_path, "
                    "COALESCE(title,''), COALESCE(artist,''), COALESCE(album,''), "
                    "COALESCE(genre,''), COALESCE(comments,''), COALESCE(description,''), "
                    "COALESCE(keywords,''), COALESCE(category,''), "
                    "COALESCE(sub_category,''), COALESCE(source,'') "
                    "FROM audio_files WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (last_rowid, BATCH)
                ).fetchall()
                if not rows:
                    break
                last_rowid = rows[-1][0]
                cur.execute("BEGIN")
                cur.executemany(
                    "INSERT INTO search_fts_term "
                    "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, "
                    " album, genre, comments, description, keywords, category, sub_category, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [Database._fts_row(r[1], r[2], r[3], r[4:]) for r in rows]
                )
                conn.commit()
                done += len(rows)
                if progress_cb:
                    try: progress_cb(done, total)
                    except Exception: pass
            # 경로 컬럼 규칙 표식 (rebuild_fts 와 같은 의미) — 단어색인 쪽 통일 완료.
            conn.execute(
                "INSERT INTO index_meta (key,value) "
                "VALUES ('term_path_mode','dir') "
                "ON CONFLICT(key) DO UPDATE SET value='dir'"
            )
            conn.commit()
            return True
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

    def backfill_missing_fts(self, progress_cb=None, cancel_event=None) -> int:
        """search_fts 에 누락된 audio_files 행만 골라 증분 삽입.
        전체 DROP+재빌드(916k행 재토큰화, 수분) 대신 누락분만 채워 빠르고 부담 적음.
        메타 없는 행도 file_name/file_path 로 등록. 갭 0 되면 fts_stale 해제.
        반환: 삽입 행 수. (취소 시 부분 반영분은 유효 — INSERT OR REPLACE)"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            # 기존 FTS 등록 file_id 집합 (1회 로드 후 메모리 비교 — UNINDEXED 조인 회피)
            existing = set(r[0] for r in cur.execute("SELECT file_id FROM search_fts"))
            total = conn.execute("SELECT COUNT(*) FROM audio_files").fetchone()[0]
            if progress_cb:
                try: progress_cb(0, total)
                except Exception: pass

            BATCH = 5000
            last_rowid = 0
            scanned = 0
            inserted = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return inserted
                rows = cur.execute(
                    "SELECT rowid, id, file_name, file_path, "
                    "COALESCE(title,''), COALESCE(artist,''), COALESCE(album,''), "
                    "COALESCE(genre,''), COALESCE(comments,''), COALESCE(description,''), "
                    "COALESCE(keywords,''), COALESCE(category,''), "
                    "COALESCE(sub_category,''), COALESCE(source,'') "
                    "FROM audio_files WHERE rowid > ? ORDER BY rowid LIMIT ?",
                    (last_rowid, BATCH)
                ).fetchall()
                if not rows:
                    break
                last_rowid = rows[-1][0]
                missing = [r for r in rows if r[1] not in existing]
                if missing:
                    fts_rows = [Database._fts_row(r[1], r[2], r[3], r[4:]) for r in missing]
                    cur.execute("BEGIN")
                    cur.executemany(
                        "INSERT OR REPLACE INTO search_fts "
                        "(rowid, file_id, file_name, file_name_norm, file_path, title, artist, album, genre, comments, "
                        " description, keywords, category, sub_category, source) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        fts_rows
                    )
                    Database._term_fts_insert(cur, fts_rows)   # 단어색인 동기
                    conn.commit()
                    inserted += len(missing)
                scanned += len(rows)
                if progress_cb:
                    try: progress_cb(scanned, total)
                    except Exception: pass

            # 갭 해소 확인 후 플래그 정리
            af = conn.execute("SELECT COUNT(*) FROM audio_files").fetchone()[0]
            ft = conn.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0]
            if af == ft:
                cur.execute("BEGIN")
                conn.execute(
                    "INSERT INTO index_meta (key,value) VALUES ('fts_stale','0') "
                    "ON CONFLICT(key) DO UPDATE SET value='0'"
                )
                conn.commit()
            return inserted
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

    def set_meta(self, key: str, value: str):
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO index_meta (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value)
            )
            conn.commit()
        finally:
            conn.close()

    def get_meta(self, key: str) -> Optional[str]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM index_meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None
        finally:
            conn.close()
