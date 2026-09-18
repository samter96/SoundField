"""SoundField 검색 브리지 — **상주 서비스**.

한 줄에 요청 하나(JSON), 응답도 한 줄. 요청에는 `id` 가 실려 있고, 응답은
**받은 순서 그대로 같은 id** 를 돌려준다 (호출자가 순서로 짝을 맞춘다).

왜 상주인가 — 실측 (실DB 16.7GB / 158만 행, 같은 질의 5회):
    요청마다 새 프로세스: 315ms   (python 시작 + app.database import + Database 생성)
    상주 프로세스        :  23ms
    상주 + 연결 유지     :   2.8ms
  검색은 입력 40ms 디바운스마다 도는 가장 뜨거운 경로다. 타이핑마다 315ms 를
  내던 것이 앱 전체가 느리게 느껴지던 큰 원인이었다.

연결을 유지하는 이유 — 원본 Database._connect 는 **호출마다 새 연결**을 만든다
(스레드 안전). 그러면 연결별 128MB 페이지 캐시가 매번 버려진다. 질의 5종 평균으로
새 연결 26.9ms vs 연결 유지 2.8ms (9.6배). 정책(SQL 생성·숨김/블랙리스트 필터)은
원본 Database.query 를 그대로 쓰고 **연결만** 유지한다.

원본은 **읽기 전용 연결에 mmap 을 걸지 않는다** (쓰기 분기에만 있다,
database.py:322). 읽기 측에도 걸면 FTS 검색이 162.9ms → 28.3ms (5.8배)였다.
읽기 매핑은 쓰기 경합과 무관해 안전하다.

⚠ 오래 살아있는 읽기 연결은 `wal_checkpoint(TRUNCATE)` 를 막는다 (인덱싱 직후
원본이 호출한다 — WAL 비대화 방지). 그래서 **15초 유휴면 연결을 닫는다.**
다시 여는 비용은 5~13ms 로 체감되지 않는다.

⚠ 리더는 `os.read(0, ...)` 를 쓴다. `for line in sys.stdin` 은 블로킹 중
TextIOWrapper 락을 잡고 있어, 다른 스레드의 import/입력이 함께 멈춘다
(관리 브리지에서 실측: 0.89초 → 55초).
"""
import json
import os
import sqlite3
import sys
from pathlib import Path
import threading
import time
from collections import deque

try:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 원본 모듈 위치 ─────────────────────────────────────────────────────────────
# ⚠ **포장된 빌드에서는 원본 소스 트리를 sys.path 에 넣지 않는다.**
#   넣으면 번들 안 사본이 아니라 개발 PC 의 소스 트리에서 app.* 을 읽는다.
#   그러면 "번들에 파일이 빠졌다" 같은 결함이 개발 PC 에서만 가려진다 —
#   실측 2026-09-07: UCS 동의어 사전이 번들에서 빠졌는데도 이 PC 에서는 소스
#   트리에서 읽혀 정상으로 보였고, 다른 PC 에서만 검색이 좁아졌다.
#   개발(비포장) 실행에서는 소스 트리가 필요하므로 그때만 넣는다.
#   SOUNDFIELD_PY_ROOT 로 명시하면 포장 빌드에서도 그 경로를 쓴다(진단용).
_EXPLICIT_ROOT = os.environ.get("SOUNDFIELD_PY_ROOT")
if _EXPLICIT_ROOT:
    ROOT = _EXPLICIT_ROOT
elif getattr(sys, "frozen", False):
    ROOT = getattr(sys, "_MEIPASS", ".")
else:
    ROOT = str(Path(__file__).resolve().parents[2] / "py")
if not getattr(sys, "frozen", False) or _EXPLICIT_ROOT:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

from app.database import Database  # noqa: E402

DB_PATH = os.path.join(os.environ["LOCALAPPDATA"], "SoundField", "index.db")
IDLE_CLOSE_SECONDS = 15.0

_state = {
    "latest_id": 0,      # 리더가 받은 가장 최신 요청 id
    "running_id": 0,     # 지금 실행 중인 질의의 id (0 = 없음)
    "last_used": 0.0,
}
_lock = threading.Lock()
_queue: deque = deque()
_wake = threading.Event()


class _KeepOpen:
    """close() 를 무시하는 연결 래퍼 — 원본 Database.query 는 finally 에서 닫는다."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass

    def really_close(self):
        try:
            self._conn.close()
        except Exception:
            pass


_db = Database(DB_PATH, read_only=True)
_new_connection = _db._connect        # 원본 구현 (연결 방식/PRAGMA 그대로)
_shared = {"conn": None}


def _progress_handler():
    """실행 중인 질의가 더 새 요청에 밀렸으면 SQLite 를 중단시킨다.
    (0 이 아닌 값을 돌려주면 sqlite3 가 그 질의를 취소한다)"""
    return 1 if 0 < _state["running_id"] < _state["latest_id"] else 0


def _connection():
    conn = _shared.get("conn")
    if conn is None:
        raw = _new_connection()
        try:
            raw.execute("PRAGMA mmap_size=4294967296")
        except sqlite3.OperationalError:
            pass
        # 5만 VM 단계마다 취소 여부를 본다 — 너무 잦으면 질의 자체가 느려진다.
        try:
            raw.set_progress_handler(_progress_handler, 50_000)
        except Exception:
            pass
        conn = _KeepOpen(raw)
        _shared["conn"] = conn
    _state["last_used"] = time.monotonic()
    return conn


_db._connect = _connection


def _idle_closer():
    """유휴 시 연결을 닫아 WAL 체크포인트(TRUNCATE)를 막지 않는다."""
    while True:
        time.sleep(3.0)
        conn = _shared.get("conn")
        if conn is None:
            continue
        with _lock:
            busy = bool(_queue) or _state["running_id"] > 0
        if busy:
            continue
        if time.monotonic() - _state["last_used"] >= IDLE_CLOSE_SECONDS:
            _shared["conn"] = None
            conn.really_close()


def _run_query(req):
    return _db.query(
        matchers=req.get("matchers") or [],
        min_duration=float(req.get("min_duration") or 0),
        max_duration=req.get("max_duration"),
        sample_rate=req.get("sample_rate"),
        channels=req.get("channels"),
        min_channels=req.get("min_channels"),
        path_prefixes=req.get("path_prefixes") or None,
        precise=bool(req.get("precise")),
        limit=int(req.get("limit") or 500),
    )


def _respond(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _worker():
    """요청을 **받은 순서대로** 처리하고 응답한다.
    이미 더 새 요청이 와 있으면 실행하지 않고 곧바로 취소 응답한다 —
    타이핑 중 중간 요청을 통째로 건너뛰고 최신 것만 계산한다."""
    while True:
        with _lock:
            item = _queue.popleft() if _queue else None
        if item is None:
            _wake.wait(0.5)
            _wake.clear()
            continue
        req_id = int(item.get("id") or 0)
        if req_id < _state["latest_id"]:
            _respond({"id": req_id, "aborted": True})
            continue
        _state["running_id"] = req_id
        try:
            rows = _run_query(item)
            if req_id < _state["latest_id"]:
                _respond({"id": req_id, "aborted": True})
            else:
                _respond({"id": req_id, "rows": rows})
        except sqlite3.OperationalError as exc:
            # 취소(progress handler)도 OperationalError("interrupted") 로 온다
            if "interrupt" in str(exc).lower() or req_id < _state["latest_id"]:
                _respond({"id": req_id, "aborted": True})
            else:
                _respond({"id": req_id, "error": str(exc)})
        except Exception as exc:                       # noqa: BLE001
            _respond({"id": req_id, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            _state["running_id"] = 0


def main() -> int:
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_idle_closer, daemon=True).start()

    buffer = b""
    while True:
        try:
            chunk = os.read(0, 65536)
        except (OSError, ValueError):
            break
        if not chunk:
            break                          # 파이프 닫힘 = 앱 종료
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                req = json.loads(text)
            except json.JSONDecodeError:
                continue
            req_id = int(req.get("id") or 0)
            with _lock:
                _state["latest_id"] = max(_state["latest_id"], req_id)
                _queue.append(req)
            _wake.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
