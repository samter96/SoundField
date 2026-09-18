"""
각자 로컬 인덱스 모드.
- 인덱스 DB는 사용자 PC 로컬 (%LOCALAPPDATA%\\SoundField\\index.db)
- 네트워크 라이브러리는 읽기만
- 인덱싱 충돌은 같은 프로세스 안에서만 발생하므로 메모리 플래그로 충분
"""
from pathlib import Path
from threading import Lock


class LocalStore:
    """로컬 인덱스 DB 위치 관리."""

    def __init__(self, store_dir: Path):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store_dir / "index.db"


class IndexBusy:
    """프로세스 내 인덱싱 중복 실행 방지."""

    def __init__(self):
        self._lock = Lock()
        self._busy = False

    def acquire(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            return True

    def release(self):
        with self._lock:
            self._busy = False

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy
