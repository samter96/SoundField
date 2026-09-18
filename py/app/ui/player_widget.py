"""하단 재생 + 멀티채널 파형 + 재생 히스토리.
WAV 피크는 채널별로 백그라운드 추출, 캐시 (v2 포맷: 채널 헤더 + 슬라이스).
"""
import array
import hashlib
import json
import logging
import math
import os
import struct
import time
import wave
from collections import deque
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    import soundfile as sf  # libsndfile — float WAV / RF64 / EXTENSIBLE / FLAC 등 fallback
except ImportError:
    sf = None

logger = logging.getLogger(__name__)

PEAKS_CACHE_DIR = Path.home() / "AppData" / "Local" / "SoundField" / "peaks_cache"
QUICK_CACHE_DIR = PEAKS_CACHE_DIR / "quick"
HISTORY_FILE = Path.home() / "AppData" / "Local" / "SoundField" / "history.json"
PEAKS_N = 1024
PEAKS_VERSION = 10
# v10: 헤더 신고 길이보다 짧게 디코딩되는 파일(mp3 계열)을 **실제 길이에 맞춰 자른다**.
#      이전 세대 캐시는 신고 길이 기준으로 만들어져 꼬리가 비어 있으므로 무효.
# v9: 가로 줌 폐지 — mipmap 4단계(87,040 슬라이스) → 단일 8192 레벨,
#     캐시 float32 → int16. 파일당 1.33MB → 64KB (-95%).
# v8: 키 mtime 소수점 3자리 반올림 — 스캐너(scandir-rs)와 os.stat
#     의 float 정밀도 차이로 DB키/재생키가 어긋나 완전제거 캐시삭제가
#     빗나가던 문제 수정 (v7: 분할 없으면 전체 1개 세그먼트)
QUICK_SLICES = 16     # v3: 128→16 로 NAS seek 1/8 (11초 → ~1.4초 기대). 화면은 막대 16개 러프
QUICK_SAMPLES = 4096  # v4: 32→4096 — 지점당 ~0.04초(96k) 표본. seek 횟수(16) 불변이라
                      # NAS 추가비용 ~+10ms 수준인데 표본 128배 → 드론 등 잔잔한 소스의
                      # quick 파형이 순간 우연값에 휘둘리던 왜곡 완화 (사용자 보고)
QUICK_VERSION = 5     # v5 — samples 4096 + 5/95 백분위. 이전 캐시 자동 무효화
HISTORY_MAX = 100
ANIMATION_DIVISOR = 96.0
PLAYBACK_LOAD_WATCHDOG_MS = 8000
WAVEFORM_LOAD_DELAY_MS = 650
WAVEFORM_IMMEDIATE_MAX_BYTES = 100 * 1024 * 1024  # 이 크기 미만 파일은 파형 즉시 로드(지연 0)
WAVEFORM_QUICK_MIN_BYTES = 30 * 1024 * 1024
WAVEFORM_FULL_AFTER_QUICK_DELAY_MS = 1800
WAVEFORM_PLAYBACK_READ_CHUNK_SAMPLES = 65536
WAVEFORM_PLAYBACK_CHUNK_PAUSE_SEC = 0.018
# 파형 추출 가능 확장자 — soundfile(libsndfile 1.2) 디코딩 가능 포맷.
# quick preview(sparse)는 PCM RIFF 전용이라 .wav 만, full 디코딩은 전부 지원.
WAVEFORM_EXTS = {".wav", ".flac", ".aif", ".aiff", ".ogg", ".oga", ".w64", ".rf64", ".mp3"}
WAVEFORM_MIN_HALF_PX = 0.35
WAVEFORM_POLYGON_PEN_W = 0.45

# ─── 세그먼트 분할 튜닝 가이드 (v6 정책) ─────────────────────
# 검출 신호 = "충분히 조용했다가 다시 소리가 나는 지점". 단, 세 가지 정밀화:
#  ① 창 RMS  — 샘플 하나하나가 아니라 SEG_ENV_WINDOW_SEC 길이 창의 평균 음량으로 판정
#              (노이즈 한 톨에 안 휘둘림).
#  ② 피크 상대 임계값 — 파일의 '큰 소리' 기준(SEG_LOUD_PERCENTILE)보다
#              SEG_SILENCE_DROP_DB 이상 작아야 '무음'. 고정 dB가 아니라 파일별 상대.
#              floor×배수 방식은 사운드의 조용한 부분을 무음으로 오인해 폐기 —
#              피크 기준이라 다이내믹 큰 사운드 본문이 안전하게 소리로 유지됨.
#  ③ 최소 길이 — SEG_MIN_SEGMENT_SEC 보다 짧은 세그먼트는 이웃에 병합
#              (리버브 꼬리 dip 발 잔조각 제거).
#  + 선행/후행 무음은 세그먼트로 만들지 않음 (맨 앞 침묵이 첫 조각 되던 것 방지).
#
# MIN_SILENCE_SEC: 소리 사이가 이만큼 이상 조용해야 경계로 인정.
#   0.3~0.5 = 톤 단위 (알람/키클릭 등 잦은 분할) / 1.0 = SFX 단위 / 2.0+ = 큰 단락
# SEG_SILENCE_DROP_DB: 클수록 둔감(아주 깊은 간격만 자름=과분할 억제), 작을수록 민감.
# SEG_MIN_SEGMENT_SEC: 클수록 잔조각 병합 강함(과분할 억제).
#
# 값 변경 후 캐시는 자동 무효화됨 (cache key 에 정책 상수 포함).
# 안 되면 %LOCALAPPDATA%\SoundField\peaks_cache\ 폴더 삭제.
# ──────────────────────────────────────────────────────────
MIN_SILENCE_SEC = 0.5
SEG_ENV_WINDOW_SEC = 0.02      # ① 평균 음량 측정 창 (20ms)
SEG_LOUD_PERCENTILE = 95.0     # ② '큰 소리' 기준 백분위 (창 RMS 분포 상위)
SEG_SILENCE_DROP_DB = 30.0     # ② 무음 = 큰 소리보다 이만큼(dB) 이상 작은 구간
SEG_ABS_FLOOR_RMS = 0.003      # ② 절대 최저 무음 기준 (≈ -50dB) — 디지털 무음 파일 보호
SEG_MIN_SEGMENT_SEC = 0.30     # ③ 이보다 짧은 세그먼트는 병합
SEGMENT_HEADER_H = 20
SEG_SNAP_OFFSET_SEC = 0.1   # 스냅 클릭 시 sound 시작보다 N초 앞으로 (쾅 터지는 지점 회피)

# ─── PHASE-3E 복원: 추가 모듈 레벨 상수 (PYZ bytecode 검증) ───
TIMELINE_RULER_H = 16            # 상단 시간 눈금자 높이
# 파형 슬라이스 단계 — 가로 줌 폐지로 **단일 레벨**만 저장한다.
# 8192 = 파형 폭 2048px 까지 "픽셀당 슬라이스 4개" 품질 유지 (1배 화면 모습은 종전과 동일).
# 여러 단계였던 이유는 오직 가로 줌이었고, 그 65536 레벨 하나가 캐시 용량의 75% 였다.
# 이 값을 바꾸면 캐시 내용이 달라지므로 캐시 키에 포함해 스스로 무효화되게 했다.
LEVEL_SLICES = (8192,)

from PyQt6.QtCore import (
    Qt, QUrl, QLineF, QMimeData, QObject, QPoint, QPointF, QRectF, QRunnable, QThreadPool,
    QTimer, pyqtSignal, QEvent
)
from PyQt6.QtGui import (
    QBrush, QColor, QDrag, QFont, QKeyEvent, QLinearGradient, QMouseEvent,
    QPainter, QPaintEvent, QPen, QPixmap, QPolygonF, QPainterPath, QAction,
    QDesktopServices,
)
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QMessageBox, QSizePolicy, QSlider, QVBoxLayout, QWidget, QWidgetAction
)

from app.binaural import (
    IEM_SUITE_DOWNLOAD_URL, IEM_SUITE_VERSION, LayoutOverrideStore,
    is_iem_install_issue, resolve_layout, split_layout_override,
)
from app.region_export import crop_wav_region, render_speed_wav
from app.ui.playback import HybridPlayer
from app.ui.anim import FRAME_MS

# peaks: ndarray (slices, channels, 2)=(min,max) float32.
# segments: ndarray (n_segments, 2)=(start_ratio, end_ratio) float32.
# 캐시 v3: <u32 channels><u32 n_segments><peaks float32...><segments float32...>
PeaksArray = np.ndarray
SegmentsArray = np.ndarray


# NAS Y:\ 의 os.stat() 호출이 60~120ms 깎는 문제 — 같은 path 짧은 시간 내
# 반복 호출(selection 변경 → 재클릭) 시 메모리 TTL 캐시. 파일 수정 감지를 위해
# size/mtime 은 여전히 사용하므로 TTL 끝나면 다시 stat (정확성 보존).
_STAT_TTL_SEC = 30.0
_stat_cache: dict = {}  # path → (cached_at_monotonic, size, mtime_int)


def _coerce_stat_hint(size, mtime) -> Optional[tuple]:
    try:
        size_i = int(size)
        mtime_i = int(float(mtime))
    except (TypeError, ValueError):
        return None
    if size_i <= 0 or mtime_i <= 0:
        return None
    return size_i, mtime_i


def _prime_stat_cache(path: str, size=None, mtime=None) -> Optional[tuple]:
    hint = _coerce_stat_hint(size, mtime)
    if hint is None:
        return None
    size_i, mtime_i = hint
    _stat_cache[path] = (time.monotonic(), size_i, mtime_i)
    return size_i, mtime_i


def _stat_cached(path: str) -> Optional[tuple]:
    now = time.monotonic()
    hit = _stat_cache.get(path)
    if hit is not None and now - hit[0] < _STAT_TTL_SEC:
        return (hit[1], hit[2])
    try:
        st = os.stat(path)
        size = int(st.st_size)
        mtime = int(st.st_mtime)
        _stat_cache[path] = (now, size, mtime)
        # 캐시 항목 너무 늘어나지 않게 prune (LRU 까지는 안 가도 충분).
        if len(_stat_cache) > 4096:
            cutoff = now - _STAT_TTL_SEC
            for k in [k for k, v in _stat_cache.items() if v[0] < cutoff]:
                _stat_cache.pop(k, None)
        return (size, mtime)
    except OSError:
        return None


def _stat_for_cache(path: str, stat_hint: Optional[tuple] = None,
                    allow_stat: bool = True) -> Optional[tuple]:
    if stat_hint is not None:
        return stat_hint
    now = time.monotonic()
    hit = _stat_cache.get(path)
    if hit is not None and now - hit[0] < _STAT_TTL_SEC:
        return (hit[1], hit[2])
    if not allow_stat:
        return None
    return _stat_cached(path)


def _peaks_cache_path(path: str, stat_hint: Optional[tuple] = None,
                      allow_stat: bool = True) -> Optional[Path]:
    s = _stat_for_cache(path, stat_hint=stat_hint, allow_stat=allow_stat)
    if s is None:
        return None
    size, mtime = s
    # mtime 3자리 반올림 — DB(modified_at, scandir-rs 유래)와 os.stat 이 마지막
    # 비트에서 다른 float 를 줘도 같은 키가 나오게 (MTIME_TOL 2s 대비 충분한 정밀도).
    mtime = round(mtime, 3)
    key = hashlib.md5(
        f"{path}|{size}|{mtime}|{LEVEL_SLICES}|v{PEAKS_VERSION}|{MIN_SILENCE_SEC}"
        f"|{SEG_ENV_WINDOW_SEC}|{SEG_LOUD_PERCENTILE}|{SEG_SILENCE_DROP_DB}"
        f"|{SEG_ABS_FLOOR_RMS}|{SEG_MIN_SEGMENT_SEC}".encode("utf-8")
    ).hexdigest()
    return PEAKS_CACHE_DIR / f"{key}.bin"


def cleanup_stale_peaks_cache():
    """캐시 키 스킴 버전 교체 시 구버전 .bin 일괄 삭제 — 파일명이 불투명 해시라
    버전을 파일별로 구분 못 함 → 마커 파일(cache_version.txt)로 세대 전체 교체.
    앱 시작 시 백그라운드 스레드에서 호출 (15k 파일 삭제 = 수 초, UI 블록 금지).
    삭제 직후 재생되는 파일의 새 캐시가 지워지는 race 는 스냅샷 목록만 지워
    최소화 — 만에 하나 지워져도 다음 재생에서 재생성 (self-healing)."""
    marker = PEAKS_CACHE_DIR / "cache_version.txt"
    ver = f"v{PEAKS_VERSION}"
    try:
        if marker.exists() and marker.read_text(encoding="ascii") == ver:
            return
        stale = list(PEAKS_CACHE_DIR.glob("*.bin"))
        for f in stale:
            try:
                f.unlink()
            except OSError:
                pass
        PEAKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(ver, encoding="ascii")
        if stale:
            logger.info("구버전 파형 캐시 정리: %d개 삭제 (%s)", len(stale), ver)
    except OSError:
        pass


def _load_cached_peaks(path: str, stat_hint: Optional[tuple] = None,
                       allow_stat: bool = True):
    """v9 캐시 포맷:
        <u32 channels><u32 n_segments><u32 n_levels><u32 sample_rate><u32 n_samples>
        <u32 slice_count> × n_levels
        <peaks level_0 int16>...<peaks level_{n-1} int16>   (×32767 스케일)
        <segments float32 [n_segs, 2]>
    가로 줌 폐지로 n_levels 는 1 (LEVEL_SLICES 단일 항목). 8192 슬라이스보다 짧은
    파일은 원본 샘플 그대로 1레벨 — _extract_peaks 의 `not usable_targets` 분기.
    v8 까지는 레벨이 float32 view(zero-copy) 였으나 v9 는 int16 → float32 변환이라
    새 배열을 만든다 (단일 레벨 스테레오 128KB, 무시 가능).
    세그먼트는 시간 비율(0~1)로 정밀도가 필요해 float32 유지.
    반환: (channels, sample_rate, n_samples, peak_levels, segments)
    """
    cf = _peaks_cache_path(path, stat_hint=stat_hint, allow_stat=allow_stat)
    if cf is None or not cf.exists():
        return None
    try:
        raw = cf.read_bytes()
        if len(raw) < 20:
            return None
        channels, n_segs, n_levels, sample_rate, n_samples = struct.unpack_from("<IIIII", raw, 0)
        if channels < 1 or channels > 16 or n_levels < 1 or n_levels > 8:
            return None
        cursor = 20
        slice_counts = np.frombuffer(raw, dtype=np.uint32, count=n_levels, offset=cursor)
        cursor += n_levels * 4
        per_slice_vals = channels * 2
        levels = []
        for sc in slice_counts:
            sc = int(sc)
            count = sc * per_slice_vals
            # v9: 디스크는 int16, 그리기는 float32 — 여기서 되돌린다. zero-copy 는
            # 아니지만 단일 8192 레벨이라 스테레오 128KB 할당, 무시 가능.
            i16 = np.frombuffer(raw, dtype=np.int16, count=count, offset=cursor)
            arr = (i16.astype(np.float32) * np.float32(1.0 / 32767.0)
                   ).reshape(sc, channels, 2)
            levels.append(arr)
            cursor += count * 2
        if n_segs > 0:
            segments = np.frombuffer(raw, dtype=np.float32, count=n_segs * 2, offset=cursor).reshape(n_segs, 2)
        else:
            segments = _empty_segments()
        return channels, int(sample_rate), int(n_samples), levels, segments
    except Exception:
        return None


def _quick_cache_path(path: str, stat_hint: Optional[tuple] = None,
                      allow_stat: bool = True) -> Optional[Path]:
    s = _stat_for_cache(path, stat_hint=stat_hint, allow_stat=allow_stat)
    if s is None:
        return None
    size, mtime = s
    key = hashlib.md5(
        f"{path}|{size}|{mtime}|q{QUICK_VERSION}|{QUICK_SLICES}|{QUICK_SAMPLES}".encode("utf-8")
    ).hexdigest()
    return QUICK_CACHE_DIR / f"{key}.bin"


def _load_quick_peaks(path: str, stat_hint: Optional[tuple] = None,
                      allow_stat: bool = True):
    """quick 캐시 v2: <u32 channels><u32 sample_rate><u32 n_samples><peaks float32 [QUICK_SLICES, ch, 2]>"""
    cf = _quick_cache_path(path, stat_hint=stat_hint, allow_stat=allow_stat)
    if cf is None or not cf.exists():
        return None
    try:
        raw = cf.read_bytes()
        if len(raw) < 12:
            return None
        ch, sr, nf = struct.unpack_from("<III", raw, 0)
        if ch < 1 or ch > 16:
            return None
        count = QUICK_SLICES * ch * 2
        arr = np.frombuffer(raw, dtype=np.float32, count=count, offset=12).reshape(QUICK_SLICES, ch, 2)
        return int(ch), int(sr), int(nf), [arr], _single_segment()
    except Exception:
        return None


def _save_quick_peaks(path: str, ch: int, sr: int, nf: int, peaks: np.ndarray):
    cf = _quick_cache_path(path)
    if cf is None:
        return
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        header = struct.pack("<III", ch, sr, nf)
        cf.write_bytes(header + np.ascontiguousarray(peaks, dtype=np.float32).tobytes())
    except Exception:
        pass


def _save_cached_peaks(path: str, channels: int, sample_rate: int, n_samples: int,
                       peak_levels: List[PeaksArray], segments: SegmentsArray):
    cf = _peaks_cache_path(path)
    if cf is None:
        return
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        n_levels = len(peak_levels)
        slice_counts = np.array([int(p.shape[0]) for p in peak_levels], dtype=np.uint32)
        header = struct.pack("<IIIII", channels, int(segments.shape[0]), n_levels,
                             int(sample_rate), int(n_samples))
        chunks = [header, slice_counts.tobytes()]
        for p in peak_levels:
            # v9: float32 → int16 (용량 절반). 파형값은 -1~1 이라 int16 은 32,767 단계 —
            # 화면 픽셀 해상도보다 훨씬 촘촘해 시각 차이가 없다. 1.0 초과(오버) 값은
            # 잘리지만 그리기 단계에서 이미 채널 높이로 클리핑되므로 보이는 결과 동일.
            # (PoC 파형 사이드카는 종전부터 int16 으로 전송해 왔다 — 같은 정밀도.)
            a = np.ascontiguousarray(p, dtype=np.float32)
            chunks.append(np.clip(a * 32767.0, -32768.0, 32767.0)
                          .astype(np.int16).tobytes())
        if segments.size:
            chunks.append(np.ascontiguousarray(segments, dtype=np.float32).tobytes())
        cf.write_bytes(b''.join(chunks))
    except Exception:
        pass


def _empty_segments() -> SegmentsArray:
    return np.zeros((0, 2), dtype=np.float32)


def _single_segment() -> SegmentsArray:
    """분할 없음(=단일 사운드)일 때 파일 전체를 1개 세그먼트로. 세그먼트 ON 일 때
    단일 사운드도 '세그먼트 모드 켜짐'을 보이게 노출(ON/OFF 구분)."""
    return np.array([[0.0, 1.0]], dtype=np.float32)


def _env_window_samples(sr: int) -> int:
    """① 창 RMS 측정 창 크기(샘플). sr 기반, 최소 1."""
    return max(1, int(sr * SEG_ENV_WINDOW_SEC))


def _make_env_buffers(nf: int, env_win: int):
    """창 단위 제곱합/표본수 누적 버퍼 (스트리밍 중 채움). 메모리: nf/env_win 개 float64."""
    n_win = (nf + env_win - 1) // env_win if nf > 0 else 0
    return (np.zeros(n_win, dtype=np.float64), np.zeros(n_win, dtype=np.float64))


def _accumulate_env(sumsq: np.ndarray, count: np.ndarray, env_win: int,
                    chunk_start: int, ms: np.ndarray):
    """청크의 채널평균 제곱값(ms, 정규화)을 전역 창 버퍼에 합산. 청크 경계가 창과
    안 맞아도 전역 샘플 인덱스로 창을 정해 올바르게 합산된다."""
    n = ms.shape[0]
    if n == 0:
        return
    widx = (np.arange(chunk_start, chunk_start + n, dtype=np.int64) // env_win)
    base = int(widx[0])
    local = (widx - base).astype(np.int64)
    L = int(local[-1]) + 1
    sumsq[base:base + L] += np.bincount(local, weights=ms, minlength=L)
    count[base:base + L] += np.bincount(local, minlength=L)


def _trim_short_decode(nf: int, processed: int, top_slices: int,
                       mn_accum: np.ndarray, mx_accum: np.ndarray,
                       n_levels: int):
    """헤더가 신고한 길이보다 **실제 디코딩이 짧을 때** 실제 길이에 맞춰 자른다.

    mp3 등은 frames 가 추정값이다 (실측: Dramatic_Hit_Hard_10.mp3 신고 791,523 /
    실제 733,824 — 7.3% 부족). 자르지 않으면 피크가 신고 길이 기준으로 배치돼
    파형 뒤쪽 7.3% 가 빈 채로 그려지고, 재생 위치 표시 기준도 실제와 어긋난다.
    (사용자 결정 2026-09-07: 실제 길이에 맞춘다.)

    ⚠ 단일 레벨일 때만 자른다. 여러 레벨이면 하위 레벨이 상위에서 4배수로 유도되므로
      (_build_mipmap_cascade) 상위 슬라이스를 자르면 배수가 깨진다. 현재
      LEVEL_SLICES 는 단일 항목이라 항상 자르는 경로를 탄다.
    반환: (실제 길이, mn_accum, mx_accum)
    """
    if processed <= 0 or processed >= nf or n_levels != 1:
        return nf, mn_accum, mx_accum
    filled = int(math.ceil(processed / nf * top_slices))
    filled = max(1, min(top_slices, filled))
    return processed, mn_accum[:filled], mx_accum[:filled]


def _segments_from_envelope(sumsq: np.ndarray, count: np.ndarray,
                            env_win: int, sr: int, nf: int) -> SegmentsArray:
    """창 RMS 엔벨로프에서 세그먼트 경계 산출 (정책 ①②③).
    sumsq/count: 창 단위 누적. env_win: 창 크기(샘플). 반환: (n,2) start/end 비율."""
    if count.size == 0 or nf <= 0:
        return _empty_segments()
    env_rms = np.sqrt(sumsq / np.maximum(count, 1.0)).astype(np.float32)

    # ② 피크 상대 임계값 — 파일의 '큰 소리'(상위 백분위)보다 DROP_DB 이상 작아야 무음.
    # floor 기준이 아니라 peak 기준 → 다이내믹 큰 사운드의 조용한 박자를 무음으로
    # 오인하지 않음. 절대 최저(ABS_FLOOR)로 디지털 무음 파일도 보호.
    nz = env_rms[count > 0]
    loud = float(np.percentile(nz, SEG_LOUD_PERCENTILE)) if nz.size else 0.0
    threshold = max(float(SEG_ABS_FLOOR_RMS),
                    loud * float(10.0 ** (-SEG_SILENCE_DROP_DB / 20.0)))

    silent = env_rms <= threshold
    s = silent.astype(np.int8)
    padded = np.concatenate(([0], s, [0]))
    diff = np.diff(padded)
    run_starts = np.where(diff == 1)[0]
    run_ends = np.where(diff == -1)[0]   # exclusive (창 단위)
    lengths = run_ends - run_starts
    min_sil_win = max(1, int(round(MIN_SILENCE_SEC / SEG_ENV_WINDOW_SEC)))
    long_idx = np.where(lengths >= min_sil_win)[0]

    # 사운드 시작 = 긴 무음 run 의 끝 (창 → 샘플).
    starts = (run_ends[long_idx].astype(np.int64) * env_win)
    starts = starts[starts < nf]
    # 선행 무음 처리 — 파일이 긴 무음으로 시작하면 0 을 경계로 넣지 않는다(맨 앞
    # 침묵이 빈 세그먼트가 되던 것 방지). 시작이 소리면 0 부터 첫 세그먼트.
    starts_with_silence = (long_idx.size > 0 and run_starts[long_idx[0]] == 0)
    if not starts_with_silence and (starts.size == 0 or starts[0] > 0):
        starts = np.concatenate(([0], starts))
    if starts.size == 0:
        return _single_segment()   # 전부 무음 등 → 전체 1개

    # ③ 최소 세그먼트 길이 — 직전 경계와 너무 가까운 경계는 버려(앞 세그먼트에 병합).
    min_seg = max(1, int(SEG_MIN_SEGMENT_SEC * sr))
    kept = [int(starts[0])]
    for st in starts[1:]:
        if int(st) - kept[-1] >= min_seg:
            kept.append(int(st))
    # 마지막 세그먼트가 너무 짧으면 마지막 경계 제거.
    while len(kept) > 1 and nf - kept[-1] < min_seg:
        kept.pop()
    if len(kept) <= 1:
        return _single_segment()   # 유의미한 분할 없음 = 단일 사운드 → 전체 1개

    seg_starts = np.array(kept, dtype=np.int64)
    seg_ends = np.concatenate((seg_starts[1:], [nf]))
    return np.stack([seg_starts, seg_ends], axis=1).astype(np.float32) / np.float32(nf)


def _is_playback_throttled(cancel_check) -> bool:
    throttle_check = getattr(cancel_check, "throttle_check", None)
    if throttle_check is None:
        return False
    try:
        return bool(throttle_check())
    except Exception:
        return False


def _decode_chunk_pause(cancel_check):
    if _is_playback_throttled(cancel_check):
        time.sleep(WAVEFORM_PLAYBACK_CHUNK_PAUSE_SEC)


class _ResetSlider(QSlider):
    """우클릭 → default_value 로 리셋."""
    def __init__(self, orientation, default_value: int, parent=None):
        super().__init__(orientation, parent)
        self._default_value = default_value
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # 포커스 받지 않도록 — Qt 기본 dotted focus rect 가 슬라이더 외곽선처럼
        # 보이는(특히 아래쪽 가로 점선) 현상 방지. 휠/드래그/우클릭 리셋은 동작 유지.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, e: QMouseEvent):
        # 우클릭 = 즉시 리셋
        if e.button() == Qt.MouseButton.RightButton:
            self.setValue(self._default_value)
            e.accept()
            return
        # Ctrl + 좌클릭 = 리셋 (실수 방지용 modifier 조합)
        if (e.button() == Qt.MouseButton.LeftButton
                and (e.modifiers() & Qt.KeyboardModifier.ControlModifier)):
            self.setValue(self._default_value)
            e.accept()
            return
        super().mousePressEvent(e)


class _SingleStepSlider(_ResetSlider):
    """휠 1틱 = singleStep 1 고정 + 우클릭 리셋."""
    def wheelEvent(self, e):
        delta = 1 if e.angleDelta().y() > 0 else -1
        self.setValue(self.value() + delta)
        e.accept()


class _EditableValueLabel(QLabel):
    """더블클릭 → 인플레이스 QLineEdit 입력. 확정 시 valueCommitted 시그널."""
    valueCommitted = pyqtSignal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._edit: QLineEdit | None = None

    def mouseDoubleClickEvent(self, e):
        if self._edit:
            return
        # 부모 위젯(컨트롤 바)에 바로 붙임
        self._edit = QLineEdit(self.text(), self.parentWidget())
        self._edit.setObjectName("controlLabel")
        
        # 라벨 위치에 맞춰서 크기/위치 조정
        pos = self.mapTo(self.parentWidget(), QPoint(0, 0))
        self._edit.setGeometry(pos.x(), pos.y(), max(60, self.width()), self.height())
        self._edit.selectAll()
        self._edit.show()
        self._edit.setFocus()
        
        # 포커스를 잃거나 엔터치면 완료
        self._edit.editingFinished.connect(self._commit)
        self._edit.installEventFilter(self)

    def eventFilter(self, obj, e):
        if obj is self._edit:
            if e.type() == QEvent.Type.KeyPress:
                if e.key() == Qt.Key.Key_Escape:
                    # ESC는 반영 없이 닫기
                    self._close_edit(commit=False)
                    return True
            elif e.type() == QEvent.Type.FocusOut:
                # 포커스 아웃 시 커밋
                self._commit()
                return True
        return super().eventFilter(obj, e)

    def _commit(self):
        self._close_edit(commit=True)

    def _close_edit(self, commit: bool = True):
        if not self._edit:
            return
        edit = self._edit
        self._edit = None
        edit.removeEventFilter(self)
        
        val = edit.text().strip()
        edit.hide()
        edit.deleteLater()
        
        if commit and val:
            self.valueCommitted.emit(val)


class _ElidedLabel(QLabel):
    """공간 부족 시 텍스트 끝을 ...으로 생략하는 라벨."""
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        elided = fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), self.alignment(), elided)


class WaveformSignals(QObject):
    # path, gen, ch, sr, n_samples, peak_levels (list of arrays), segments
    done = pyqtSignal(str, int, int, int, int, object, object)
    failed = pyqtSignal(str, int, str)


class PathProbeSignals(QObject):
    done = pyqtSignal(str, int, bool)


class PathProbeRunnable(QRunnable):
    def __init__(self, path: str, generation: int):
        super().__init__()
        self.setAutoDelete(True)
        self.path = path
        self.generation = generation
        self.signals = PathProbeSignals()

    def run(self):
        try:
            ok = os.path.exists(self.path)
        except OSError:
            ok = False
        self.signals.done.emit(self.path, self.generation, ok)




class WaveformRunnable(QRunnable):
    """QThreadPool 용 — Qt 가 라이프사이클 완전 관리 (Python GC 영향 없음).

    perf_t0: 메인 스레드의 wave.load() 진입 시점 (time.perf_counter()). worker
    가 이 기준으로 단계별 [perf-wave] 로그 출력. 측정 끝나면 인자/로그 제거 가능.
    """

    def __init__(self, path: str, generation: int, perf_t0: float = 0.0,
                 is_current=None, stat_hint: Optional[tuple] = None):
        super().__init__()
        self.setAutoDelete(True)
        self.path = path
        self.generation = generation
        self.signals = WaveformSignals()
        self.perf_t0 = perf_t0
        # is_current(): 이 작업의 세대가 아직 최신인지. False 면 시작 시 스킵 + 디코딩 중단.
        self.is_current = is_current
        self.stat_hint = stat_hint

    def _ms(self) -> int:
        return int((time.perf_counter() - self.perf_t0) * 1000) if self.perf_t0 else -1

    def run(self):
        # 이미 다른 파일로 넘어갔으면 시작도 안 함 (전용 풀 큐에 쌓인 stale 즉시 폐기).
        if self.is_current is not None and not self.is_current():
            return
        fname = os.path.basename(self.path)
        logger.info("[perf-wave] run +%dms file=%s", self._ms(), fname)
        try:
            t_cache = time.perf_counter()
            cached = _load_cached_peaks(self.path, stat_hint=self.stat_hint)
            cache_ms = int((time.perf_counter() - t_cache) * 1000)
            if cached is not None:
                ch, sr, nf, peak_levels, segments = cached
                logger.info("[perf-wave] cache_HIT +%dms (load %dms) ch=%d sr=%d nf=%d",
                            self._ms(), cache_ms, ch, sr, nf)
                self.signals.done.emit(self.path, self.generation, ch, sr, nf, peak_levels, segments)
                logger.info("[perf-wave] emit +%dms", self._ms())
                return
            logger.info("[perf-wave] cache_MISS +%dms (probe %dms)", self._ms(), cache_ms)
            t_ext = time.perf_counter()
            ch, sr, nf, peak_levels, segments = _extract_peaks(self.path, self.is_current)
            extract_ms = int((time.perf_counter() - t_ext) * 1000)
            logger.info("[perf-wave] extract_done +%dms (%dms) ch=%d sr=%d nf=%d",
                        self._ms(), extract_ms, ch, sr, nf)
            _save_cached_peaks(self.path, ch, sr, nf, peak_levels, segments)
            self.signals.done.emit(self.path, self.generation, ch, sr, nf, peak_levels, segments)
            logger.info("[perf-wave] emit +%dms", self._ms())
        except _DecodeCancelled:
            logger.info("[perf-wave] cancelled (파일 전환) %s", fname)
        except Exception as e:
            logger.warning('파형 추출 실패: %s [%s] %s', self.path, type(e).__name__, e)
            self.signals.failed.emit(self.path, self.generation, str(e))


class WaveformQuickRunnable(QRunnable):
    """Quick (sparse) 전용 worker. Full read 절대 X — 별개 worker 가 full 처리.
    결과는 single-level peaks (QUICK_SLICES 슬라이스). 첫 표시용 러프 파형.
    """

    def __init__(self, path: str, generation: int, perf_t0: float = 0.0,
                 is_current=None, stat_hint: Optional[tuple] = None):
        super().__init__()
        self.setAutoDelete(True)
        self.path = path
        self.generation = generation
        self.signals = WaveformSignals()
        self.perf_t0 = perf_t0
        self.is_current = is_current
        self.stat_hint = stat_hint

    def _ms(self) -> int:
        return int((time.perf_counter() - self.perf_t0) * 1000) if self.perf_t0 else -1

    def run(self):
        if self.is_current is not None and not self.is_current():
            return
        fname = os.path.basename(self.path)
        try:
            t_c = time.perf_counter()
            cached = _load_quick_peaks(self.path, stat_hint=self.stat_hint)
            cache_ms = int((time.perf_counter() - t_c) * 1000)
            if cached is not None:
                if self.is_current is not None and not self.is_current():
                    return
                ch, sr, nf, levels, segs = cached
                logger.info("[perf-quick] cache_HIT +%dms (load %dms) %s",
                            self._ms(), cache_ms, fname)
                self.signals.done.emit(self.path, self.generation, ch, sr, nf, levels, segs)
                return
            t_e = time.perf_counter()
            ch, sr, nf, levels, segs = _extract_peaks_sparse(self.path)
            if self.is_current is not None and not self.is_current():
                return
            extract_ms = int((time.perf_counter() - t_e) * 1000)
            logger.info("[perf-quick] extract +%dms (%dms) %s",
                        self._ms(), extract_ms, fname)
            _save_quick_peaks(self.path, ch, sr, nf, levels[0])
            self.signals.done.emit(self.path, self.generation, ch, sr, nf, levels, segs)
        except Exception as e:
            logger.debug("[perf-quick] fail %s: %s", fname, e)
            self.signals.failed.emit(self.path, self.generation, str(e))


def _decode_24bit_np(raw: bytes) -> np.ndarray:
    """24-bit little-endian signed → int32 ndarray (vectorized)."""
    data = np.frombuffer(raw, dtype=np.uint8)
    ns = data.size // 3
    data = data[:ns * 3].reshape(ns, 3)
    # high byte를 int8로 cast 후 shift → 자동 sign extension
    return (data[:, 0].astype(np.int32)
            | (data[:, 1].astype(np.int32) << 8)
            | (data[:, 2].view(np.int8).astype(np.int32) << 16))


class _DecodeCancelled(Exception):
    """파일이 바뀌어(세대 변경) 진행 중 디코딩을 청크 경계에서 중단할 때 raise."""


def _extract_peaks(path: str, cancel_check=None):
    """wave 우선 (PCM RIFF, 빠름) → 실패 시 soundfile fallback (IEEE float / RF64 /
    EXTENSIBLE / FLAC 등). 둘 다 실패하면 raise → WaveformRunnable 가 failed 시그널 emit.
    cancel_check: 콜러블 — False 반환 시 청크 경계에서 _DecodeCancelled.
    반환: (channels, sample_rate, n_samples, [level_0 ... level_{n-1}], segments).
    """
    long_path = _winlong(path)
    try:
        return _extract_peaks_wave(long_path, cancel_check)
    except _DecodeCancelled:
        raise
    except Exception as e:
        if sf is None:
            raise
        logger.debug('wave 추출 실패, soundfile fallback: %s (%s)', path, e)
        return _extract_peaks_soundfile(long_path, cancel_check)


def _extract_peaks_wave(path: str, cancel_check=None):
    """stdlib wave 모듈 기반 — 표준 PCM RIFF WAV 만 처리 (int8/16/24/32).
    대용량 파일 대응: 전체를 읽지 않고 CHUNK 단위로 스트리밍 처리하여 메모리 절약.
    cancel_check: 콜러블 — False 면 청크 경계에서 _DecodeCancelled (파일 전환 시 즉시 중단).
    """
    with wave.open(path, 'rb') as w:
        sw = w.getsampwidth()
        ch = w.getnchannels()
        nf = w.getnframes()
        sr = w.getframerate() or 48000
        if nf <= 0 or ch <= 0:
            return max(1, ch), sr, max(0, nf), [_empty_peaks(ch)], _empty_segments()
        if sw == 2:
            dtype = np.int16; scale = 32768.0; offset = 0.0
        elif sw == 4:
            dtype = np.int32; scale = 2147483648.0; offset = 0.0
        elif sw == 1:
            dtype = np.uint8; scale = 128.0; offset = 128.0
        elif sw == 3:
            dtype = None; scale = 8388608.0; offset = 0.0
        else:
            raise ValueError(f'unsupported sampwidth={sw}')
        usable_targets = [L for L in LEVEL_SLICES if L <= nf]
        if not usable_targets:
            raw = w.readframes(nf)
            if sw == 3:
                arr_int = _decode_24bit_np(raw)
            else:
                arr_int = np.frombuffer(raw, dtype=dtype)
            arr_int = arr_int[:nf * ch].reshape(nf, ch)
            inv_scale = np.float32(1.0 / scale)
            arr_f = (arr_int.astype(np.float32) - np.float32(offset)) * inv_scale
            return ch, sr, nf, [np.stack([arr_f, arr_f], axis=-1)], _single_segment()
        top_slices = usable_targets[-1]
        slice_edges = np.linspace(0, nf, top_slices + 1, dtype=np.int64)
        accum_dtype = np.int32
        mn_accum = np.full((top_slices, ch), 2147483647, dtype=accum_dtype)
        mx_accum = np.full((top_slices, ch), -2147483648, dtype=accum_dtype)
        inv_scale = np.float32(1.0 / scale)
        env_win = _env_window_samples(sr)
        env_sumsq, env_count = _make_env_buffers(nf, env_win)
        READ_CHUNK_SAMPLES = (
            WAVEFORM_PLAYBACK_READ_CHUNK_SAMPLES
            if _is_playback_throttled(cancel_check) else 262144
        )
        processed_samples = 0
        while processed_samples < nf:
            if cancel_check is not None and not cancel_check():
                raise _DecodeCancelled()
            to_read = min(READ_CHUNK_SAMPLES, nf - processed_samples)
            raw = w.readframes(to_read)
            if not raw:
                break
            if sw == 3:
                chunk_arr = _decode_24bit_np(raw)
            else:
                chunk_arr = np.frombuffer(raw, dtype=dtype)
            # ⚠ 요청한 to_read 로 reshape 하면 **짧게 읽힌 경우 ValueError** 로 죽는다.
            # 실제로 돌아온 샘플 수로 맞춘다 (mp3 계열은 물론, 잘린 WAV 도 해당).
            got = int(chunk_arr.size // ch)
            if got <= 0:
                break
            chunk_arr = chunk_arr[:got * ch].reshape(got, ch)
            chunk_start = processed_samples
            chunk_end = processed_samples + got
            s_start_idx = max(0, int(np.searchsorted(slice_edges, chunk_start, side='right') - 1))
            s_end_idx = min(top_slices - 1, int(np.searchsorted(slice_edges, chunk_end - 1, side='right') - 1))
            for s_idx in range(s_start_idx, min(s_end_idx + 1, top_slices)):
                slice_sample_start = int(slice_edges[s_idx])
                slice_sample_end = int(slice_edges[s_idx + 1])
                rel_start = max(0, slice_sample_start - processed_samples)
                rel_end = min(got, slice_sample_end - processed_samples)
                if rel_end > rel_start:
                    sub = chunk_arr[rel_start:rel_end]
                    mn_accum[s_idx] = np.minimum(mn_accum[s_idx], sub.min(axis=0))
                    mx_accum[s_idx] = np.maximum(mx_accum[s_idx], sub.max(axis=0))
            # ① 창 RMS — 채널평균 제곱값을 정규화해 전역 창 버퍼에 누적.
            cf = (chunk_arr.astype(np.float32) - np.float32(offset)) * inv_scale
            ms = np.mean(cf * cf, axis=1).astype(np.float64)
            _accumulate_env(env_sumsq, env_count, env_win, processed_samples, ms)
            processed_samples += got
            _decode_chunk_pause(cancel_check)
            if got < to_read:
                break   # 파일 끝 — 신고 길이가 과대했던 경우
        # 신고 길이보다 짧게 디코딩됐으면 실제 길이에 맞춘다 (사용자 결정 2026-09-07)
        nf, mn_accum, mx_accum = _trim_short_decode(
            nf, processed_samples, top_slices, mn_accum, mx_accum, len(usable_targets))
        segments = _segments_from_envelope(env_sumsq, env_count, env_win, sr, nf)
        mn_f = (mn_accum.astype(np.float32) - np.float32(offset)) * inv_scale
        mx_f = (mx_accum.astype(np.float32) - np.float32(offset)) * inv_scale
        top_peaks = np.stack([mn_f, mx_f], axis=-1)
        levels = _build_mipmap_cascade(top_peaks, usable_targets)
        return ch, sr, nf, levels, segments


def _extract_peaks_sparse(path: str):
    """Quick sparse — stdlib wave 의 setpos + readframes. 검증된 안정 버전 (직접 RIFF parse v1 폐기).
    QUICK_SLICES 위치 균등 시킹 + 각 위치 QUICK_SAMPLES frame read → single-level peaks.
    Quick worker 전용. 동일 worker 안에서 후속 full read 절대 X (별개 worker + 별개 디스크 캐시).
    PCM RIFF 만 — 그 외 포맷은 quick 캐시 X (full 만 진행).
    """
    long_path = _winlong(path)
    with wave.open(long_path, 'rb') as w:
        sw = w.getsampwidth()
        ch = w.getnchannels()
        nf = w.getnframes()
        sr = w.getframerate() or 48000
        if nf <= 0 or ch <= 0:
            return max(1, ch), sr, max(0, nf), [_empty_peaks(ch)], _empty_segments()
        if sw == 2:
            dtype = np.int16; scale = 32768.0; offset = 0.0
        elif sw == 4:
            dtype = np.int32; scale = 2147483648.0; offset = 0.0
        elif sw == 1:
            dtype = np.uint8; scale = 128.0; offset = 128.0
        elif sw == 3:
            dtype = None; scale = 8388608.0; offset = 0.0
        else:
            raise ValueError(f'unsupported sampwidth={sw}')
        slices = min(QUICK_SLICES, nf)
        mn = np.zeros((QUICK_SLICES, ch), dtype=np.float32)
        mx = np.zeros((QUICK_SLICES, ch), dtype=np.float32)
        inv_scale = np.float32(1.0 / scale)
        off_f = np.float32(offset)
        for i in range(slices):
            seek_to = min(int(i * nf / slices), nf - 1)
            try:
                w.setpos(seek_to)
            except wave.Error:
                continue
            to_read = min(QUICK_SAMPLES, nf - seek_to)
            raw = w.readframes(to_read)
            if not raw:
                continue
            if sw == 3:
                arr_int = _decode_24bit_np(raw)
            else:
                arr_int = np.frombuffer(raw, dtype=dtype)
            n_got = arr_int.size // ch
            if n_got <= 0:
                continue
            arr_int = arr_int[:n_got * ch].reshape(n_got, ch)
            arr_f = (arr_int.astype(np.float32) - off_f) * inv_scale
            # 절대 min/max 대신 5/95 백분위 — 창 안의 고립된 미세 트랜지언트
            # (파일 헤드의 슬레이트/핸들링 노이즈 등) 하나가 슬라이스 전체를
            # 두껍게 만들어 임팩트성 사운드로 오인되던 것 방지. 지속음은
            # 95퍼센타일 ≈ 피크라 정상 표현, 진짜 임팩트는 창 대부분을 차지해 유지.
            mn[i] = np.percentile(arr_f, 5.0, axis=0)
            mx[i] = np.percentile(arr_f, 95.0, axis=0)
        peaks = np.stack([mn, mx], axis=-1)
        return ch, sr, nf, [peaks], _single_segment()


def _extract_peaks_soundfile(path: str, cancel_check=None):
    """libsndfile 기반 — IEEE float WAV / RF64 / WAVE_FORMAT_EXTENSIBLE / FLAC / MP3 등
    wave 모듈이 처리 못하는 포맷용 fallback. 대용량 파일 대응을 위해 청크 단위 처리.
    cancel_check: 콜러블 — False 면 청크 경계에서 _DecodeCancelled.
    """
    if sf is None:
        raise ImportError('soundfile library is required for this format.')
    with sf.SoundFile(path) as f:
        ch = f.channels
        sr = f.samplerate or 48000
        nf = f.frames
        if nf <= 0 or ch <= 0:
            return max(1, ch), sr, max(0, nf), [_empty_peaks(ch)], _empty_segments()
        usable_targets = [L for L in LEVEL_SLICES if L <= nf]
        if not usable_targets:
            arr = f.read(dtype='float32', always_2d=True)
            return ch, sr, nf, [np.stack([arr, arr], axis=-1).astype(np.float32, copy=False)], _single_segment()
        top_slices = usable_targets[-1]
        slice_edges = np.linspace(0, nf, top_slices + 1, dtype=np.int64)
        mn_accum = np.full((top_slices, ch), 1.0, dtype=np.float32)
        mx_accum = np.full((top_slices, ch), -1.0, dtype=np.float32)
        env_win = _env_window_samples(sr)
        env_sumsq, env_count = _make_env_buffers(nf, env_win)
        READ_CHUNK_SAMPLES = (
            WAVEFORM_PLAYBACK_READ_CHUNK_SAMPLES
            if _is_playback_throttled(cancel_check) else 262144
        )
        processed_samples = 0
        while processed_samples < nf:
            if cancel_check is not None and not cancel_check():
                raise _DecodeCancelled()
            to_read = min(READ_CHUNK_SAMPLES, nf - processed_samples)
            chunk_arr = f.read(to_read, dtype='float32', always_2d=True)
            if chunk_arr.size == 0:
                break
            # ⚠ 요청한 to_read 가 아니라 **실제로 돌아온 길이**로 인덱싱해야 한다.
            # mp3 등은 f.frames 가 추정값이라 실제 디코딩이 더 짧다 (실측:
            # Dramatic_Hit_Hard_10.mp3 신고 791,523 / 실제 733,824 — 57,699 부족).
            # to_read 로 자르면 데이터가 없는 구간의 슬라이스에서 rel_start 가 배열
            # 끝을 넘어 빈 배열이 되고 sub.min() 이 ValueError 로 죽는다
            # → 파형 전체가 "추출 실패" 가 된다. (옛 4단계 mipmap 시절부터 있던 버그.)
            got = int(chunk_arr.shape[0])
            chunk_start = processed_samples
            chunk_end = processed_samples + got
            s_start_idx = max(0, int(np.searchsorted(slice_edges, chunk_start, side='right') - 1))
            s_end_idx = min(top_slices - 1, int(np.searchsorted(slice_edges, chunk_end - 1, side='right') - 1))
            for s_idx in range(s_start_idx, min(s_end_idx + 1, top_slices)):
                slice_sample_start = int(slice_edges[s_idx])
                slice_sample_end = int(slice_edges[s_idx + 1])
                rel_start = max(0, slice_sample_start - processed_samples)
                rel_end = min(got, slice_sample_end - processed_samples)
                if rel_end > rel_start:
                    sub = chunk_arr[rel_start:rel_end]
                    mn_accum[s_idx] = np.minimum(mn_accum[s_idx], sub.min(axis=0))
                    mx_accum[s_idx] = np.maximum(mx_accum[s_idx], sub.max(axis=0))
            # ① 창 RMS — chunk_arr 는 이미 정규화 float. 채널평균 제곱을 창 버퍼에 누적.
            ms = np.mean(chunk_arr * chunk_arr, axis=1).astype(np.float64)
            _accumulate_env(env_sumsq, env_count, env_win, processed_samples, ms)
            processed_samples += got
            _decode_chunk_pause(cancel_check)
            if got < to_read:
                break   # 파일 끝 — 더 읽을 게 없다 (신고 길이가 과대했던 경우)
        # 신고 길이보다 짧게 디코딩됐으면 실제 길이에 맞춘다 (사용자 결정 2026-09-07).
        # 이걸 먼저 해야 꼬리 슬라이스가 아예 사라져 파형이 폭에 정확히 찬다.
        nf, mn_accum, mx_accum = _trim_short_decode(
            nf, processed_samples, top_slices, mn_accum, mx_accum, len(usable_targets))
        segments = _segments_from_envelope(env_sumsq, env_count, env_win, sr, nf)
        # 자른 뒤에도 데이터가 안 들어온 슬라이스가 남을 수 있다(중간 빈 구간).
        # 초기값(min=1.0, max=-1.0)을 그대로 그리면 **레인 전체를 채우는 기둥**이 된다.
        untouched = mn_accum > mx_accum
        if untouched.any():
            mn_accum[untouched] = 0.0
            mx_accum[untouched] = 0.0
        top_peaks = np.stack([mn_accum, mx_accum], axis=-1)
        levels = _build_mipmap_cascade(top_peaks, usable_targets)
        return ch, sr, nf, levels, segments


def _empty_peaks(ch: int) -> PeaksArray:
    return np.zeros((0, max(1, ch), 2), dtype=np.float32)

# ─── PHASE-3E 복원: 모듈 레벨 새 함수 (PYZ bytecode 100% 일치 검증) ───────────
def _winlong(path: str) -> str:
    """Windows 260자 우회 — open/wave/sf 호출 직전 \\\\?\\ prefix 추가."""
    if os.name != 'nt' or not path:
        return path
    if path.startswith('\\\\?\\'):
        return path
    if len(path) < 248:
        return path
    abs_path = os.path.abspath(path)
    if abs_path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + abs_path[2:]
    return '\\\\?\\' + abs_path


def _build_mipmap_cascade(top_peaks, target_levels):
    """top_peaks: (top_slices, channels, 2) float32 — 최상위 mipmap (이미 빌드됨).
    target_levels: [1024, 4096, ..., top_target] (실제로 빌드 가능한 레벨만).
    상위 레벨에서 4배수 그룹화 (min of mins, max of maxes) 로 하위 레벨 생성.
    mn/mx 는 그룹 reduction 에 합성적(composable) 이라 raw 에서 직접 빌드한 결과와
    수학적으로 동일 (단, 마지막 슬라이스 잔여분 경계 한 슬라이스에 흡수).
    Returns: low→high res 순서.
    """
    if len(target_levels) == 1:
        return [top_peaks]
    levels_desc = [top_peaks]
    current = top_peaks
    ch = current.shape[1]
    for target in reversed(target_levels[:-1]):
        cur_slices = current.shape[0]
        ratio = cur_slices // target
        if ratio < 2:
            continue
        usable = target * ratio
        truncated = current[:usable].reshape(target, ratio, ch, 2)
        new_mn = truncated[..., 0].min(axis=1)
        new_mx = truncated[..., 1].max(axis=1)
        if usable < cur_slices:
            rem = current[usable:]
            new_mn[-1] = np.minimum(new_mn[-1], rem[..., 0].min(axis=0))
            new_mx[-1] = np.maximum(new_mx[-1], rem[..., 1].max(axis=0))
        down = np.stack([new_mn, new_mx], axis=-1).astype(np.float32, copy=False)
        levels_desc.append(down)
        current = down
    levels_desc.reverse()
    return levels_desc
# ─────────────────────────────────────────────────────────────────────────


class _TransportButton(QPushButton):
    """통일된 외곽선 + 호버 애니메이션을 지원하는 플레이바 버튼.

    색은 모두 테마 dict (COLORS) 기준 — accent / text / on_accent 자동 매핑.
    active_color 인자는 명시 우회용 (보통 None → accent 폴백).
    accent_border=True 면 OFF 상태에서도 accent 외곽선 항상 표시 (세그먼트 토글 등 특별기능 시인성)."""
    def __init__(self, text: str = "", parent=None, active_color: str = None,
                 font_size: int = 15, is_filled: bool = False, accent_border: bool = False,
                 filled_when_on: bool = False, button_style: bool = False,
                 state_chip: bool = False):
        super().__init__(text, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # QSS 배경/테두리 간섭 방지
        self.setStyleSheet("background: transparent; border: none;")

        self._active = False
        self._hover_alpha = 0  # 0 to 255 for animation
        self._active_color = QColor(active_color) if active_color else None  # None → 테마 accent 폴백
        self._is_filled = is_filled
        self._accent_border = bool(accent_border)
        self._filled_when_on = filled_when_on   # 토글 ON 시 fill (luminous)
        self._button_style = bool(button_style)
        self._state_chip = bool(state_chip)
        self._status_indicator = "none"
        self._status_phase = 0.0
        
        f = self.font()
        f.setFamily("JetBrains Mono")
        if font_size and font_size > 0:
            f.setPointSize(font_size)   # 0/-1 이면 setPointSize 워닝 → 스킵(기본 크기 유지)
        f.setBold(True)
        self.setFont(f)

        # 애니메이션 타이머 (호버 시 서서히 밝아짐) — 120fps
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        # 버튼 색/상태 애니메이션은 60fps면 충분하다. 파형 120Hz와 분리해
        # 바이노럴 활성 중 작은 버튼 하나가 불필요하게 120회 repaint되지 않게 한다.
        self._anim_timer.setInterval(max(16, FRAME_MS))
        self._anim_timer.timeout.connect(self._update_anim)

    def set_active(self, active: bool):
        if self._active != active:
            self._active = active
            self.update()

    def set_status_indicator(self, mode: str):
        mode = mode if mode in {
            "none", "off", "bypass", "needs_layout", "loading", "active", "error"
        } else "none"
        if self._status_indicator == mode:
            return
        self._status_indicator = mode
        if mode in {"loading", "active"}:
            self._anim_timer.start()
        self.update()

    def enterEvent(self, e):
        self._anim_timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim_timer.start()
        super().leaveEvent(e)

    def _update_anim(self):
        target = 255 if self.underMouse() else 0
        step = 24  # 약 0.18초 hover 전환(60fps), 과도한 120fps 재도색 방지
        status_animating = self._status_indicator in {"loading", "active"}
        if status_animating:
            self._status_phase = (
                self._status_phase + self._anim_timer.interval() / 1200.0
            ) % 1.0
        if abs(self._hover_alpha - target) <= step:
            self._hover_alpha = target
            if not status_animating:
                self._anim_timer.stop()
        else:
            self._hover_alpha += step if self._hover_alpha < target else -step
        self.update()

    def paintEvent(self, e):
        # 테마 dict 를 매 paint 마다 lazy 참조 — 테마 토글 시 자동 반영
        from .theme import COLORS
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

            # 토글 버튼인 경우 isChecked() 상태를 활성 상태로 간주
            is_on = self._active or (self.isCheckable() and self.isChecked())

            # OFF/평상시는 항상 테마 accent. active_color 는 토글 ON 상태일 때만 적용.
            # (loop_btn 처럼 ON 시 보라색이어도 OFF 일 땐 다른 버튼들과 같은 테마 accent 로 보임)
            theme_accent = QColor(COLORS["accent"])
            on_accent_col = self._active_color or theme_accent
            ha = self._hover_alpha  # 0..255

            def mix(first: QColor, second: QColor, amount: float) -> QColor:
                amount = max(0.0, min(1.0, amount))
                inverse = 1.0 - amount
                return QColor(
                    round(first.red() * inverse + second.red() * amount),
                    round(first.green() * inverse + second.green() * amount),
                    round(first.blue() * inverse + second.blue() * amount),
                    round(first.alpha() * inverse + second.alpha() * amount),
                )

            # 1. 배경 및 외곽선 색상 결정 — 기본은 모두 투명, 아이콘만 테마 text 색
            bg_col = QColor(0, 0, 0, 0)
            border_col = QColor(0, 0, 0, 0)
            icon_col = QColor(COLORS["text"])

            if self._button_style:
                # 기능 버튼용: OFF도 면을 가진 일반 버튼이고, ON은 accent로
                # 확실히 채운다. hover alpha로 배경·테두리를 부드럽게 보간한다.
                hover_t = ha / 255.0
                if self._status_indicator == "loading":
                    amber = QColor("#f0a43c")
                    bg_col = QColor(amber.red(), amber.green(), amber.blue(), 38 + round(ha * 0.08))
                    border_col = amber
                    icon_col = amber
                elif self._status_indicator == "error":
                    red = QColor("#e45b64")
                    bg_col = QColor(red.red(), red.green(), red.blue(), 28 + round(ha * 0.08))
                    border_col = red
                    icon_col = QColor(COLORS["text"])
                elif self._status_indicator == "active" or is_on:
                    normal = QColor(COLORS["accent"])
                    hovered = QColor(COLORS["accent_hover"])
                    bg_col = mix(normal, hovered, hover_t)
                    border_col = bg_col
                    icon_col = QColor(COLORS["on_accent"])
                else:
                    bg_col = mix(QColor(COLORS["bg_elev"]),
                                 QColor(COLORS["bg_control_hi"]), hover_t)
                    border_col = mix(QColor(COLORS["border_strong"]),
                                     QColor(COLORS["accent_hover"]), hover_t)
                    icon_col = mix(QColor(COLORS["text_secondary"]),
                                   QColor(COLORS["text"]), hover_t)
                if not self.isEnabled():
                    bg_col = QColor(COLORS["bg_panel"])
                    border_col = QColor(COLORS["border"])
                    icon_col = QColor(COLORS["text_muted"])
                if self.isDown():
                    bg_col = QColor(COLORS["accent_pressed"])
                    border_col = bg_col
                    icon_col = QColor(COLORS["on_accent"])
            elif self._accent_border:
                # 세그먼트 토글: OFF 도 테마 accent 활성처럼, ON 은 모든 테마 초록 강조.
                if is_on:
                    green = QColor("#3ec870")
                    bg_col = QColor(green.red(), green.green(), green.blue(), 55)
                    border_col = green
                    icon_col = green
                else:
                    bg_col = QColor(theme_accent.red(), theme_accent.green(), theme_accent.blue(), 40)
                    border_col = theme_accent
                    icon_col = theme_accent
            elif self._is_filled:
                # 재생 버튼: 항상 채워진 accent fill + on_accent 아이콘.
                bg_col = theme_accent
                border_col = theme_accent
                icon_col = QColor(COLORS["on_accent"])
            elif self._filled_when_on and is_on:
                # 반복재생 ON: 보라 fill α70 + 보라 외곽선 + 보라 아이콘 (active_color 사용).
                bg_col = QColor(on_accent_col.red(), on_accent_col.green(), on_accent_col.blue(), 70)
                border_col = on_accent_col
                icon_col = on_accent_col
            else:
                # 정지/처음으로/반복재생 OFF 평상시 — 테마 accent 외곽선 + 아이콘 항상 표시.
                # (loop_btn 도 OFF 일 땐 여기로 와서 다른 버튼들과 같은 테마 accent 톤)
                border_col = QColor(theme_accent.red(), theme_accent.green(), theme_accent.blue(), max(120, ha))
                icon_col = QColor(theme_accent.red(), theme_accent.green(), theme_accent.blue(), max(220, ha))
                if is_on:
                    bg_col = QColor(theme_accent.red(), theme_accent.green(), theme_accent.blue(), 40)
                elif ha > 0:
                    bg_col = QColor(theme_accent.red(), theme_accent.green(), theme_accent.blue(), int(ha * 0.12))

            # 2. 배경/테두리 그리기
            p.setBrush(bg_col)
            p.setPen(QPen(border_col, 1))
            p.drawRoundedRect(r, 2, 2)

            # 3. 아이콘 직접 그리기 (도형)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(icon_col)
            
            cx, cy = r.center().x(), r.center().y()
            txt = self.text()
            
            if "▶" in txt:
                # 재생 (삼각형) — 더 큼직하게
                path = QPainterPath()
                sw, sh = 5, 6
                path.moveTo(cx - sw, cy - sh)
                path.lineTo(cx + sw + 1, cy)
                path.lineTo(cx - sw, cy + sh)
                path.closeSubpath()
                p.drawPath(path)
                
            elif "⏸" in txt:
                # 일시정지 (두 줄)
                w, h, g = 3, 11, 3
                p.drawRect(QRectF(cx - w - g/2, cy - h/2, w, h))
                p.drawRect(QRectF(cx + g/2, cy - h/2, w, h))
                
            elif "■" in txt:
                # 정지 (정사각형)
                s = 10
                p.drawRect(QRectF(cx - s/2, cy - s/2, s, s))
                
            elif "⏮" in txt:
                # 처음으로 (막대 + 삼각형)
                s = 5
                p.drawRect(QRectF(cx - s - 2, cy - s - 1, 2, s * 2 + 2))
                path = QPainterPath()
                path.moveTo(cx + s, cy - s - 1)
                path.lineTo(cx - s, cy)
                path.lineTo(cx + s, cy + s + 1)
                path.closeSubpath()
                p.drawPath(path)
                
            elif "▥" in txt:
                # 세그먼트 (세로줄 아이콘 - Segments 의미 강조)
                s = 10
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(icon_col, 1.2))
                p.drawRect(QRectF(cx-s/2, cy-s/2, s, s))
                p.drawLine(QPointF(cx-s/4, cy-s/2), QPointF(cx-s/4, cy+s/2))
                p.drawLine(QPointF(cx, cy-s/2), QPointF(cx, cy+s/2))
                p.drawLine(QPointF(cx+s/4, cy-s/2), QPointF(cx+s/4, cy+s/2))

            elif "↻" in txt:
                # 반복 재생 — 거의 완전한 시계방향 원호 + 시작점 화살촉
                radius = 4.8
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(icon_col, 1.3))
                arc_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
                # Qt drawArc: 1/16도 단위, 시작각/스팬각. 0° = 3시 방향, 양수 = 반시계.
                # 시계방향 290도: 시작 60° (1시쯤) → 스팬 -290°
                p.drawArc(arc_rect, 60 * 16, -290 * 16)
                # 시작점 (1시 방향) 에 화살촉 — 시계방향 진행
                import math as _m
                sx = cx + radius * _m.cos(_m.radians(60))
                sy = cy - radius * _m.sin(_m.radians(60))
                arrow = QPainterPath()
                arrow.moveTo(sx + 1.5, sy - 2.0)
                arrow.lineTo(sx - 2.5, sy - 0.5)
                arrow.lineTo(sx + 0.5, sy + 2.2)
                arrow.closeSubpath()
                p.setBrush(icon_col)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPath(arrow)

            elif "▾" in txt:
                # 분할 버튼의 드롭다운 화살표.
                arrow = QPainterPath()
                arrow.moveTo(cx - 3.5, cy - 1.5)
                arrow.lineTo(cx + 3.5, cy - 1.5)
                arrow.lineTo(cx, cy + 2.5)
                arrow.closeSubpath()
                p.drawPath(arrow)

            else:
                # 아이콘 전용 버튼이 아닌 일반 텍스트 버튼도 같은 커스텀
                # 페인터를 사용한다. 바이노럴처럼 한글 라벨인 경우 기본
                # QPushButton 페인터가 호출되지 않으므로 여기서 직접 그린다.
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(icon_col))
                p.setFont(self.font())
                text_rect = QRectF(r)
                if self._status_indicator != "none":
                    indicator_x = r.left() + 12.0
                    indicator_y = r.center().y()
                    if self._status_indicator == "loading":
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.setPen(QPen(QColor("#f0a43c"), 1.6))
                        arc = QRectF(indicator_x - 4.2, indicator_y - 4.2, 8.4, 8.4)
                        start = round(-self._status_phase * 360.0 * 16.0)
                        p.drawArc(arc, start, 250 * 16)
                    elif self._status_indicator == "active":
                        wave = (math.sin(self._status_phase * math.tau) + 1.0) * 0.5
                        halo = QColor("#55df8a")
                        halo.setAlpha(round(28 + 58 * wave))
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(halo)
                        radius = 4.5 + wave * 1.5
                        p.drawEllipse(QPointF(indicator_x, indicator_y), radius, radius)
                        p.setBrush(QColor("#55df8a"))
                        p.drawEllipse(QPointF(indicator_x, indicator_y), 2.6, 2.6)
                    elif self._status_indicator == "bypass":
                        p.setPen(QPen(QColor(COLORS["on_accent"]), 1.2))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawEllipse(QPointF(indicator_x, indicator_y), 3.2, 3.2)
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(COLORS["on_accent"]))
                        p.drawEllipse(QPointF(indicator_x, indicator_y), 1.3, 1.3)
                    elif self._status_indicator == "needs_layout":
                        amber = QColor("#f0a43c")
                        p.setPen(QPen(amber, 1.4))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawEllipse(QPointF(indicator_x, indicator_y), 3.3, 3.3)
                        p.drawLine(QPointF(indicator_x, indicator_y - 1.8),
                                   QPointF(indicator_x, indicator_y + 0.6))
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(amber)
                        p.drawEllipse(QPointF(indicator_x, indicator_y + 2.0), 0.7, 0.7)
                    elif self._status_indicator == "error":
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor("#e45b64"))
                        p.drawEllipse(QPointF(indicator_x, indicator_y), 3.0, 3.0)
                    else:
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.setPen(QPen(QColor(COLORS["text_muted"]), 1.2))
                        p.drawEllipse(QPointF(indicator_x, indicator_y), 3.0, 3.0)
                    text_rect.adjust(21, 0, -4, 0)
                if self._state_chip:
                    chip_text = (
                        "오류" if self._status_indicator == "error" else
                        "선택" if self._status_indicator == "needs_layout" else
                        "켜짐" if self.isChecked() else "꺼짐"
                    )
                    # 상태 칩은 버튼 끝이 아니라 액션 라이트 바로 오른쪽에 둔다.
                    # 시선 흐름이 [상태등][켜짐/꺼짐][출력 경로] 순서가 된다.
                    chip_left = r.left() + (
                        21 if self._status_indicator != "none" else 4)
                    chip_rect = QRectF(
                        chip_left, r.top() + 3, 33, r.height() - 6)
                    if self._status_indicator == "error":
                        chip_col = QColor("#e45b64")
                    elif self._status_indicator == "needs_layout":
                        chip_col = QColor("#f0a43c")
                    elif self.isChecked():
                        chip_col = QColor(COLORS["on_accent"])
                    else:
                        chip_col = QColor(COLORS["text_muted"])
                    fill = QColor(chip_col)
                    fill.setAlpha(48 if self.isChecked() else 18)
                    p.setBrush(fill)
                    p.setPen(QPen(chip_col, 1.0))
                    p.drawRoundedRect(chip_rect, 3, 3)
                    p.setPen(QPen(chip_col))
                    chip_font = QFont(self.font())
                    chip_font.setPointSize(max(7, self.font().pointSize() - 2))
                    p.setFont(chip_font)
                    p.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, chip_text)
                    text_rect.setLeft(chip_rect.right() + 4)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(icon_col))
                p.setFont(self.font())
                p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, txt)


class WaveformView(QWidget):
    seeked = pyqtSignal(float)
    dragRegionRequested = pyqtSignal()       # 영역 내부에서 드래그 시작 → PlayerWidget이 crop+drag
    segmentHeaderVisibilityChanged = pyqtSignal(bool)
    durationDecoded = pyqtSignal(int)
    # ─── PHASE-3D 복원 시그널 + 클래스 상수 ───

    DRAG_THRESHOLD_PX = 6
    # 가로 줌/팬은 폐지됐다 (캐시 용량 -95%). 파형은 항상 전체가 폭에 꽉 차게 그려진다.
    # 세로(진폭) 줌만 남는다 — Ctrl+Alt+휠. 그리는 배율만 바꿔 캐시와 무관하다.
    ZOOM_STEP = 1.5
    MIN_AMP_ZOOM = 1.0
    MAX_AMP_ZOOM = 80.0
    ZOOM_ANIM_T = 0.4       # 세로 줌 애니메이션 시간 (초)
    ZOOM_DONE_EPS = 0.003   # 도착 판단 epsilon

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._peaks: PeaksArray = _empty_peaks(1)
        self._segments: SegmentsArray = _empty_segments()
        self._active_segment_idx: int = -1
        self._hover_segment_idx: int = -1
        self._segments_visible: bool = True
        self._duration_ms: int = 0
        self._channels: int = 1
        self._position: float = 0.0
        self._loading = False
        self._current_path: str = ""
        self._current_gen: int = 0
        # 파형 디코딩 전용 풀 — 동시 1개로 제한. 전역 풀에 여러 디코딩이 쌓여
        # GIL/CPU 를 점유해 UI 가 프리즈되던 문제 방지. 파일 전환 시 세대 취소로
        # 진행 중/대기 중 stale 디코딩은 즉시 폐기됨.
        self._wave_pool = QThreadPool(self)
        self._wave_pool.setMaxThreadCount(1)
        self._quick_pool = QThreadPool(self)
        self._quick_pool.setMaxThreadCount(1)
        # deep zoom raw 읽기 전용 풀 — paintEvent 가 UI 스레드에서 파일 읽어 멈추던 것 방지.
        self._cached_polys: Optional[List[QPolygonF]] = None
        self._cached_w = self._cached_h = 0
        self._grad_bg: Optional[QLinearGradient] = None
        self._grad_un: Optional[QLinearGradient] = None
        self._grad_pl: Optional[QLinearGradient] = None
        # 정적 wave body 캐시 (배경+그리드+border+폴리곤+채널선). peaks/size 변경
        # 시만 재생성. 매 frame paint 는 drawPixmap (매우 빠름) + playhead +
        # selection/segments overlay 만. 120Hz 의 핵심 최적화.
        self._static_pixmap: Optional[QPixmap] = None
        self._anim_phase = 0.0
        self._playback_active = False
        self._hovered = False
        # selection state
        self._sel_start: Optional[float] = None  # ratio
        self._sel_end: Optional[float] = None
        self._drag_anchor_ratio: Optional[float] = None
        self._drag_mode: str = ""   # '', 'select', 'extdrag', 'headerdrag'
        # 세그먼트 헤더 드래그 시 export 대상 범위 (start_ratio, end_ratio).
        # PlayerWidget._on_drag_region 에서 이 값을 우선 사용한다.
        self._seg_drag_range: Optional[Tuple[float, float]] = None
        # PHASE-3L: PYZ 복구 — 헤더 상수에 연동 (16+20+70=106)
        self.setMinimumHeight(TIMELINE_RULER_H + SEGMENT_HEADER_H + 70)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # WA_OpaquePaintEvent 사용 금지 — DPR 분수배율(1.5x 등) 에서 static_pixmap
        # 가장자리 1px 가 transparent 로 남고, Qt 가 백버퍼를 지우지 않아 playhead 가
        # 지나간 자리 픽셀이 누적됨 → "재생한 영역만큼 가로 자국" 버그.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        # ─── PHASE-3B STUB (rollback recovery) — PYZ __init__ 누락 attribute 복원 ───
        # 줌/팬/루프/raw 캐시/peak mipmap 등 새 기능 의존 변수. 초기값은 PYZ bytecode 일치.
        self._peak_levels: List[PeaksArray] = [_empty_peaks(1)]
        self._header_was_visible: bool = False
        self._loop_active: bool = False
        # 줌/팬 상태 (애니메이션 보간)
        self._amp_zoom: float = 1.0
        self._target_amp_zoom: float = 1.0
        # ─── PHASE-3I: zoom 애니메이션 타이머 — 120Hz frame budget 안에서 보간 ───
        self._zoom_anim_timer: QTimer = QTimer(self)
        self._zoom_anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._zoom_anim_timer.setInterval(FRAME_MS)
        self._zoom_anim_timer.timeout.connect(self._tick_zoom_anim)
        # raw 모드 (waveform sample-level) 캐시
        self._sample_rate: int = 0
        self._n_samples: int = 0
        # ─────────────────────────────────────────────────────────────────────────

        # 파형 phase animation — 120Hz. wave body 가 pixmap 캐시 (paint cost 거의
        # 0) 라 120Hz 도 frame budget 안에 들어옴.
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._anim_timer.setInterval(FRAME_MS)
        self._anim_timer.timeout.connect(self._advance_animation)

        # PHASE-3L: PYZ 복구 — 초기 헤더 visibility 신호를 부모에게 1회 송출
        QTimer.singleShot(0, self._emit_header_visibility_if_changed)

    def resizeEvent(self, e):
        self._cached_polys = None
        self._static_pixmap = None
        self._grad_bg = None
        super().resizeEvent(e)

    def _on_seg_toggle(self, checked: bool):
        self._segments_visible = checked
        self._cached_polys = None
        self._static_pixmap = None   # wave_top 변화 → 폴리곤 재계산
        self._emit_header_visibility_if_changed()
        self.update()

    def _has_visible_segments(self) -> bool:
        # 단일 세그먼트(전체 1개)도 표시 — 세그먼트 ON 일 때 단일 사운드도 노출해
        # ON/OFF 가 구분되게 한다.
        return self._segments_visible and self._segments.shape[0] >= 1

    def _wave_top(self) -> int:
        """헤더(ruler + 세그먼트 헤더) 아래부터 파형 영역. PYZ 동작 동일."""
        return TIMELINE_RULER_H + (SEGMENT_HEADER_H if self.is_header_visible() else 0)

    def set_duration_ms(self, ms: int):
        new_ms = max(0, int(ms))
        if new_ms != self._duration_ms:
            # 초단위 격자선 간격이 duration 에 의존하므로 캐시 무효화
            self._static_pixmap = None
        self._duration_ms = new_ms

    def _update_active_segment(self):
        new_idx = -1
        if self._segments.shape[0] > 0:
            r = self._position
            ss = self._segments[:, 0]
            es = self._segments[:, 1]
            hits = np.where((ss <= r) & (r < es))[0]
            if hits.size:
                new_idx = int(hits[0])
        if new_idx != self._active_segment_idx:
            self._active_segment_idx = new_idx

    def _apply_decoded_duration(self, n_samples: int, sample_rate: int):
        if n_samples <= 0 or sample_rate <= 0:
            return
        decoded_ms = max(1, int(round(n_samples * 1000.0 / sample_rate)))
        self.set_duration_ms(decoded_ms)
        self.durationDecoded.emit(decoded_ms)

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        if self._hover_segment_idx != -1:
            self._hover_segment_idx = -1
        # headerdrag 중에는 ClosedHand 유지 (위젯을 잠시 벗어나도 드래그 가능)
        if self._drag_mode != "headerdrag":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(e)

    def _hit_test_segment(self, pos_x: float, pos_y: float) -> int:
        """세그먼트 헤더 zone (ruler 아래 ~ ruler+header) 안에서만 hit."""
        if not self._has_visible_segments():
            return -1
        if pos_y < TIMELINE_RULER_H or pos_y >= TIMELINE_RULER_H + SEGMENT_HEADER_H:
            return -1
        r = self._ratio_at(pos_x)
        ss = self._segments[:, 0]
        es = self._segments[:, 1]
        hits = np.where((ss <= r) & (r < es))[0]
        return int(hits[0]) if hits.size else -1

    def set_playing(self, playing: bool):
        if self._playback_active == playing:
            return
        self._playback_active = playing
        self._static_pixmap = None
        self._refresh_animation_timer()
        self.update()

    def _refresh_animation_timer(self):
        active = self._loading or self._playback_active
        if active and not self._anim_timer.isActive():
            self._anim_timer.start()
        elif not active and self._anim_timer.isActive():
            self._anim_timer.stop()

    def _advance_animation(self):
        self._anim_phase = (self._anim_phase + 1.0 / ANIMATION_DIVISOR) % 1.0
        self.update()

    def clear(self):
        self._current_gen += 1   # 진행 중 작업 결과 무시
        # mipmap 리스트 + 기존 _peaks 호환
        self._peak_levels = [_empty_peaks(self._channels)]
        self._peaks = self._peak_levels[0]
        self._segments = _empty_segments()
        self._active_segment_idx = -1
        self._channels = 1
        self._duration_ms = 0
        # PHASE-3G 새 attrs 초기화
        self._sample_rate = 0
        self._n_samples = 0
        self._position = 0.0
        self._loading = False
        self._current_path = ""
        self._cached_polys = None
        self._static_pixmap = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.clear_selection()
        self._file_switch_zoom()  # 줌 배율 유지, 팬만 리셋
        self._refresh_animation_timer()
        self.update()

    # ─────────── 영역 선택 ───────────
    def has_selection(self) -> bool:
        return (self._sel_start is not None and self._sel_end is not None
                and self._sel_end > self._sel_start)

    def selection_ratios(self) -> Optional[Tuple[float, float]]:
        if not self.has_selection():
            return None
        return (self._sel_start, self._sel_end)

    def pending_drag_range(self) -> Optional[Tuple[float, float]]:
        """헤더 드래그가 트리거된 경우 export 할 세그먼트 범위. 1회 소비."""
        rng = self._seg_drag_range
        self._seg_drag_range = None
        return rng

    def clear_selection(self):
        if self._sel_start is not None or self._sel_end is not None:
            self._sel_start = self._sel_end = None
            self.update()

    def _ratio_at(self, x: float) -> float:
        """화면 x → 전체 timeline 비율. 가로 줌 폐지로 항상 전체가 폭에 꽉 찬다."""
        return max(0.0, min(1.0, x / max(1, self.width())))

    def begin_loading(self, path: str):
        """파일 전환 즉시 호출 — 이전 파형을 비우고 '로딩 중' 상태로 전환.
        실제 디코딩(load)은 debounce(WAVEFORM_LOAD_DELAY_MS) 후 별도 실행.
        이전 파일 파형 위에서 새 파일 플레이바만 움직이는 desync 방지.
        NAS 접근 없는 UI-only 작업이라 재생/underrun 에 영향 없음."""
        self.clear()   # _current_path="" + gen++ → 이후 debounced load(path) 정상 실행
        if os.path.splitext(path)[1].lower() in WAVEFORM_EXTS:
            self._loading = True
            self._refresh_animation_timer()
            self.update()

    def load(self, path: str, stat_hint: Optional[tuple] = None):
        if path == self._current_path:
            return
        # [perf-wave] 기준 시점 — wave.load 진입. WaveformRunnable + _on_done 모두 동일 t0 사용.
        self._perf_wave_t0 = time.perf_counter()
        logger.info("[perf-wave] load +0ms file=%s", os.path.basename(path))
        # generation 증가 — 이전 진행 중 작업의 결과는 _on_done/_on_failed에서 무시됨
        self._current_gen += 1
        self._current_path = path
        # mipmap 리스트 초기화 (Phase 3G 신 포맷)
        self._peak_levels = [_empty_peaks(self._channels)]
        self._peaks = self._peak_levels[0]
        self._segments = _empty_segments()
        self._active_segment_idx = -1
        self._channels = 1
        self._duration_ms = 0
        self._position = 0.0
        self._cached_polys = None
        self._static_pixmap = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.clear_selection()
        # 새 파일 → 줌 배율 유지, 팬만 리셋
        self._file_switch_zoom()

        ext = os.path.splitext(path)[1].lower()
        if ext not in WAVEFORM_EXTS:
            self._loading = False
            self._refresh_animation_timer()
            self.update()
            return

        self._loading = True
        self._refresh_animation_timer()
        self.update()

        # 긴 WAV만 quick preview를 먼저 띄운다 (sparse 추출은 PCM RIFF 전용).
        # full read는 잠깐 늦춰 초기 재생 버퍼링과 NAS 경합을 피하고,
        # 짧은 파일/비 WAV 포맷은 full decode 즉시 경로.
        st = _stat_for_cache(path, stat_hint=stat_hint, allow_stat=False)
        large_file = (ext == ".wav"
                      and st is not None and st[0] >= WAVEFORM_QUICK_MIN_BYTES)
        quick_started = False
        quick = (_load_quick_peaks(path, stat_hint=st, allow_stat=False)
                 if ext == ".wav" and st is not None else None)
        if quick is not None:
            qch, qsr, qnf, qlevels, qsegs = quick
            self._peak_levels = qlevels
            self._peaks = qlevels[-1]
            self._channels = max(1, int(qch))
            self._sample_rate = int(qsr)
            self._n_samples = int(qnf)
            self._apply_decoded_duration(self._n_samples, self._sample_rate)
            self._segments = qsegs
            self._loading = False
            self._cached_polys = None
            self._static_pixmap = None
            logger.info("[perf-wave] quick_sync_HIT +%dms ch=%d",
                        int((time.perf_counter() - self._perf_wave_t0) * 1000), qch)
        elif large_file:
            gen = self._current_gen
            quick_runnable = WaveformQuickRunnable(
                path, gen, self._perf_wave_t0,
                is_current=lambda g=gen: g == self._current_gen,
                stat_hint=st
            )
            quick_runnable.signals.done.connect(self._on_quick_done)
            quick_runnable.signals.failed.connect(self._on_quick_failed)
            self._quick_pool.start(quick_runnable)
            quick_started = True

        gen = self._current_gen
        is_current = lambda g=gen: g == self._current_gen
        is_current.throttle_check = lambda: self._playback_active
        runnable = WaveformRunnable(path, gen, self._perf_wave_t0,
                                    is_current=is_current, stat_hint=st)
        # 시그널은 emit 시 인자 복사됨 — runnable이 자동 소멸되어도 안전
        runnable.signals.done.connect(self._on_done)
        runnable.signals.failed.connect(self._on_failed)
        if quick_started:
            QTimer.singleShot(
                WAVEFORM_FULL_AFTER_QUICK_DELAY_MS,
                lambda p=path, g=gen, r=runnable: self._start_full_decode(p, g, r)
            )
        else:
            self._start_full_decode(path, gen, runnable)

    def _start_full_decode(self, path: str, gen: int, runnable: Optional[WaveformRunnable] = None):
        if gen != self._current_gen or path != self._current_path:
            return
        if runnable is None:
            is_current = lambda g=gen: g == self._current_gen
            is_current.throttle_check = lambda: self._playback_active
            runnable = WaveformRunnable(
                path, gen, self._perf_wave_t0, is_current=is_current
            )
            runnable.signals.done.connect(self._on_done)
            runnable.signals.failed.connect(self._on_failed)
        self._wave_pool.start(runnable)

    def _on_done(self, path: str, gen: int, channels: int, sample_rate: int,
                 n_samples: int, peak_levels: List[PeaksArray], segments: SegmentsArray):
        if gen != self._current_gen:
            return  # 구 작업
        if path == self._current_path:
            # [perf-wave] UI 스레드 도착 시점 — worker emit 부터 여기까지가
            # Qt signal 큐 대기 시간 (보통 매우 짧음).
            if getattr(self, "_perf_wave_t0", 0):
                ms = int((time.perf_counter() - self._perf_wave_t0) * 1000)
                logger.info("[perf-wave] on_done +%dms (UI thread) ch=%d", ms, channels)
            self._peak_levels = peak_levels if peak_levels else [_empty_peaks(channels)]
            # 호환성: _peaks 는 가장 고해상도 레벨 (paintEvent 가 _peaks 사용 시)
            self._peaks = self._peak_levels[-1] if self._peak_levels else _empty_peaks(channels)
            self._segments = segments if segments is not None else _empty_segments()
            self._channels = max(1, int(channels))
            self._sample_rate = int(sample_rate)
            self._n_samples = int(n_samples)
            self._apply_decoded_duration(self._n_samples, self._sample_rate)
            self._loading = False
            self._cached_polys = None
            self._static_pixmap = None
            self._update_active_segment()
            self._refresh_animation_timer()
            self.update()

    def _on_failed(self, path: str, gen: int, _msg: str):
        if gen != self._current_gen:
            return
        if path == self._current_path:
            self._peak_levels = [_empty_peaks(self._channels)]
            self._peaks = self._peak_levels[0]
            self._loading = False
            self._refresh_animation_timer()
            self.update()

    def _on_quick_done(self, path: str, gen: int, channels: int, sample_rate: int,
                        n_samples: int, peak_levels: List[PeaksArray], segments: SegmentsArray):
        """Quick (sparse) 도착 → 러프 파형 즉시 표시. _loading=False 로 풀어 paint 통과시킴
        (paintEvent 가 _loading 시 peaks 안 그리고 텍스트만). Full 도착하면 mipmap 다중 level
        덮어쓰기 (_on_done 이 처리). Full 이 먼저 도착했으면 skip."""
        if gen != self._current_gen or path != self._current_path:
            return
        if len(self._peak_levels) > 1:
            return  # full 이미 덮음
        self._peak_levels = peak_levels if peak_levels else [_empty_peaks(channels)]
        self._peaks = self._peak_levels[-1]
        self._segments = segments if segments is not None else _empty_segments()
        self._channels = max(1, int(channels))
        self._sample_rate = int(sample_rate)
        self._n_samples = int(n_samples)
        self._apply_decoded_duration(self._n_samples, self._sample_rate)
        self._loading = False
        self._cached_polys = None
        self._static_pixmap = None
        if getattr(self, "_perf_wave_t0", 0):
            ms = int((time.perf_counter() - self._perf_wave_t0) * 1000)
            logger.info("[perf-wave] quick_done +%dms (UI) ch=%d", ms, channels)
        self._refresh_animation_timer()
        self.update()

    def _on_quick_failed(self, path: str, gen: int, _msg: str):
        # Full worker 별도 진행 — quick 실패는 무시
        pass

    def set_position_ratio(self, ratio: float):
        # 데드밴드 제거 — 1e-4 threshold 는 5분 파일 기준 30ms (~33Hz) 라 anim_timer 가
        # 120Hz 로 paint 해도 같은 _position 만 그려서 playhead 가 청크 단위로 점프함.
        # 재생 중엔 anim_timer 가 120Hz 로 update() 호출하므로 여기선 self.update() 호출 X
        # (중복 paint 방지). 정지/일시정지 중엔 즉시 반영 위해 update() 호출.
        ratio = max(0.0, min(1.0, ratio))
        if ratio != self._position:
            self._position = ratio
            self._update_active_segment()
            if not self._anim_timer.isActive():
                self.update()

    # ─── PHASE-3A 복원 메서드 (PYZ bytecode 100% 일치 검증 완료) ───────────────
    def is_header_visible(self) -> bool:
        """헤더 영역 표시 여부 — 세그먼트 토글이 ON 이면 항상 True.
        (세그먼트가 없는 사운드는 헤더가 비어있는 상태로 노출됨 — 토글 반응 시인성용)"""
        return self._segments_visible

    def _wave_bottom(self) -> int:
        return self.height()

    def _x_at_ratio(self, r: float) -> float:
        """전체 timeline 비율 → 화면 x. 가로 줌 폐지로 항상 전체가 폭에 꽉 찬다."""
        return r * max(1, self.width())

    def _start_zoom_anim(self):
        if not self._zoom_anim_timer.isActive():
            self._zoom_anim_timer.start()

    def set_loop_active(self, active: bool):
        """반복 재생 ON/OFF 시 호출 — 선택 영역 색을 보라/초록으로 토글."""
        if self._loop_active != active:
            self._loop_active = active
            if self.has_selection():
                self.update()

    def apply_theme_change(self):
        """테마 토글 시 main_window 가 호출 — 캐시된 그라데이션/pixmap 무효화."""
        self._grad_bg = None
        self._grad_un = None
        self._grad_pl = None
        self._static_pixmap = None
        self.update()

    def _header_tint(self):
        """헤더 zone 위에 얹히는 반투명 어두운 톤."""
        from app.ui.theme import current_theme
        t = current_theme()
        if t == 'light':
            return QColor(43, 40, 32, 38)
        if t == 'grey':
            return QColor(0, 0, 0, 50)
        return QColor(0, 0, 0, 60)

    # ─── PHASE-3D 복원 (PYZ bytecode 100% 일치 검증 완료) ───
    def _emit_header_visibility_if_changed(self):
        """토글 상태 변경 후 호출 — 가시성 바뀌었으면 시그널 발화.
        세그먼트 유무가 아니라 토글 ON/OFF 만 보는 게 의도된 동작."""
        now = self.is_header_visible()
        if now != self._header_was_visible:
            self._header_was_visible = now
            self._cached_polys = None
            self._static_pixmap = None
            self.segmentHeaderVisibilityChanged.emit(now)

    def _fmt_ruler_label(self, t_sec: float, step_sec: float) -> str:
        """자동 단위 — step 크기에 따라 분/초/ms 전환."""
        if step_sec >= 60:
            m = int(t_sec / 60)
            s = int(t_sec) % 60
            return f'{m}:{s:02d}'
        if step_sec >= 1:
            if t_sec >= 60:
                m = int(t_sec / 60)
                s = int(t_sec - m * 60)
                return f'{m}:{s:02d}'
            return f'{int(t_sec)}s'
        if step_sec >= 0.1:
            return f'{t_sec:.1f}s'
        if step_sec >= 0.001:
            return f'{int(t_sec * 1000)}ms'
        return f'{t_sec * 1000:.1f}ms'

    def _fill_bg_split(self, p, w, h):
        """전체를 테마 그라데이션으로 채우고, 헤더 zone 만 반투명 어두운 톤을 위에 overlay.
        wave_top == 0 (헤더 숨김) 이면 overlay 생략."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._grad_bg))
        p.drawRect(QRectF(0.0, 0.0, float(w), float(h)))
        wave_top = self._wave_top()
        if wave_top > 0:
            p.setBrush(self._header_tint())
            p.drawRect(QRectF(0.0, 0.0, float(w), float(wave_top)))

    def _file_switch_zoom(self):
        """파일 전환 시 호출 — 세로 줌을 1배로 되돌린다. 조건 없이 강제 + 즉시 재그림.
        (가로 줌 폐지 후에도 세로 줌은 파일마다 초기화 — 이전 사운드에 맞춰 키운
        배율이 음량이 다른 새 사운드에서 잘려 보이던 문제 방지.)"""
        self._zoom_anim_timer.stop()
        self._amp_zoom = 1.0
        self._target_amp_zoom = 1.0
        self._cached_polys = None
        self._static_pixmap = None
        self.update()

    def reset_zoom(self):
        """파일 변경/명시적 리셋 시 호출 — 세로 줌을 1배로. 애니메이션 타이머도 중단."""
        if self._amp_zoom != 1.0 or self._target_amp_zoom != 1.0:
            self._zoom_anim_timer.stop()
            self._amp_zoom = 1.0
            self._target_amp_zoom = 1.0
            self._cached_polys = None
            self._static_pixmap = None
            self.update()

    # ─── PHASE-3F 복원: 큰 새 메서드들 (줌/팬/룰러/raw 폴리곤) ─────
    def wheelEvent(self, e):
        """Ctrl+Alt+휠 → 세로(amplitude) 줌 (1.5x/틱). 그 외는 기본 처리.
        가로 줌(Ctrl+휠)과 가로 스크롤(Shift+휠)은 폐지 — 파형은 항상 전체가
        폭에 꽉 차게 그려진다.
        """
        mods = e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        ad = e.angleDelta()
        delta = ad.y() or ad.x()
        if ctrl and alt:
            if delta == 0:
                e.accept()
                return
            factor = self.ZOOM_STEP if delta > 0 else 1.0 / self.ZOOM_STEP
            new_amp = max(self.MIN_AMP_ZOOM, min(self.MAX_AMP_ZOOM, self._target_amp_zoom * factor))
            if abs(new_amp - self._target_amp_zoom) < 1e-06:
                e.accept()
                return
            self._target_amp_zoom = new_amp
            self._start_zoom_anim()
            e.accept()
            return
        super().wheelEvent(e)

    def _tick_zoom_anim(self):
        """매 frame 호출 — 세로(진폭) 줌 current 를 target 으로 log-space lerp
        (1.5x 단계가 같은 시각 속도로 보임). 도달하면 snap + timer 정지.
        """
        t = self.ZOOM_ANIM_T
        if abs(self._amp_zoom - self._target_amp_zoom) < self.ZOOM_DONE_EPS:
            self._amp_zoom = self._target_amp_zoom
            self._zoom_anim_timer.stop()
        else:
            l_cur = math.log(self._amp_zoom)
            l_tgt = math.log(self._target_amp_zoom)
            self._amp_zoom = math.exp(l_cur + (l_tgt - l_cur) * t)
        self._cached_polys = None
        self._static_pixmap = None
        self.update()

    def _paint_ruler(self, p, w):
        """상단 시간 눈금자 — y=0~TIMELINE_RULER_H. viewport 시간 범위 기준 라벨/tick."""
        if w <= 0:
            return
        from app.ui.theme import current_theme
        light = current_theme() == 'light'
        bg_col = QColor(43, 40, 32, 22) if light else QColor(0, 0, 0, 36)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg_col)
        p.drawRect(QRectF(0.0, 0.0, float(w), float(TIMELINE_RULER_H)))
        sep_col = QColor(43, 40, 32, 60) if light else QColor(255, 255, 255, 24)
        p.setPen(QPen(sep_col, 1))
        p.drawLine(0, TIMELINE_RULER_H - 1, w, TIMELINE_RULER_H - 1)
        if self._duration_ms <= 0:
            return
        total_sec = self._duration_ms / 1000.0
        vp_start_sec = 0.0
        vp_end_sec = total_sec
        px_per_sec = w / max(1e-06, total_sec)
        candidates = (0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600)
        step = candidates[-1]
        for s in candidates:
            if px_per_sec * s >= 59.999999:
                step = s
                break
        tick_col = QColor(43, 40, 32, 140) if light else QColor(180, 190, 205, 140)
        label_col = QColor(43, 40, 32, 220) if light else QColor(190, 200, 215, 220)
        rf = QFont()  # 시스템 기본 폰트 (결과표 폰트와 동일)
        rf.setPointSize(7)
        p.setFont(rf)
        t0 = int(vp_start_sec / step) * step
        if t0 < vp_start_sec - 1e-09:
            t0 += step
        t = float(t0)
        while t <= vp_end_sec + 1e-06:
            x = int((t - vp_start_sec) * px_per_sec)
            if 0 <= x < w:
                p.setPen(QPen(tick_col, 1))
                p.drawLine(x, TIMELINE_RULER_H - 5, x, TIMELINE_RULER_H - 1)
                p.setPen(label_col)
                label = self._fmt_ruler_label(t, step)
                p.drawText(
                    QRectF(float(x + 2), 0.0, 60.0, float(TIMELINE_RULER_H - 3)),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )
            t += step

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.RightButton:
            self.clear_selection()
            e.accept()
            return
        if e.button() != Qt.MouseButton.LeftButton or self.width() <= 0:
            return
        # 세그먼트 헤더 영역 클릭 → 해당 세그먼트 시작 - SEG_SNAP_OFFSET_SEC 로 seek
        # + 드래그 임계값 넘으면 해당 세그먼트 영역 drag-and-drop
        _seg_y = e.position().y()
        if (self._has_visible_segments()
                and TIMELINE_RULER_H <= _seg_y < TIMELINE_RULER_H + SEGMENT_HEADER_H):
            r = self._ratio_at(e.position().x())
            ss = self._segments[:, 0]
            es = self._segments[:, 1]
            hits = np.where((ss <= r) & (r < es))[0]
            if hits.size:
                idx = int(hits[0])
                seg_start = float(ss[idx])
                seg_end = float(es[idx])
                target = seg_start
                if self._duration_ms > 0:
                    offset_ratio = (SEG_SNAP_OFFSET_SEC * 1000.0) / self._duration_ms
                    target = max(0.0, seg_start - offset_ratio)
                self._position = target
                self._active_segment_idx = idx
                self.seeked.emit(target)
                # 드래그 잠재 상태 — 임계값 이상 이동 시 dragRegionRequested 발화
                self._drag_anchor_ratio = r
                self._drag_mode = "headerdrag"
                self._seg_drag_range = (seg_start, seg_end)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                e.accept()
                return
        r = self._ratio_at(e.position().x())
        self._drag_anchor_ratio = r
        # 기존 영역 내부에서 시작이면 외부 drag 모드 (확정은 mouseMove)
        if self.has_selection() and self._sel_start <= r <= self._sel_end:
            self._drag_mode = "extdrag"
        else:
            # 새 영역 시작 — release까지 임시 (드래그 임계값 미만이면 seek)
            self._drag_mode = "select"
            self.clear_selection()
        self.setFocus()

    def mouseMoveEvent(self, e: QMouseEvent):
        # 세그먼트 헤더 호버 추적 (드래그 중 아닐 때도 작동)
        new_hover = self._hit_test_segment(e.position().x(), e.position().y())
        if new_hover != self._hover_segment_idx:
            self._hover_segment_idx = new_hover
            # 드래그 중이 아닐 때만 호버 커서 갱신 (드래그 중에는 ClosedHand 유지)
            if self._drag_mode != "headerdrag":
                if new_hover != -1:
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        if self._drag_anchor_ratio is None:
            return
        if not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        r = self._ratio_at(e.position().x())
        w = max(1, self.width())
        delta_px = abs(r - self._drag_anchor_ratio) * w
        if self._drag_mode == "select":
            if delta_px >= self.DRAG_THRESHOLD_PX:
                self._sel_start = min(self._drag_anchor_ratio, r)
                self._sel_end = max(self._drag_anchor_ratio, r)
                self.update()
        elif self._drag_mode in ("extdrag", "headerdrag"):
            if delta_px >= self.DRAG_THRESHOLD_PX:
                self._drag_mode = ""
                self._drag_anchor_ratio = None
                self.dragRegionRequested.emit()
                # 드래그 emit 직후 호버 커서로 복귀
                if self._hover_segment_idx != -1:
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() != Qt.MouseButton.LeftButton or self._drag_anchor_ratio is None:
            return
        # 드래그 임계값 미만 + select 모드 → seek
        if self._drag_mode == "select" and not self.has_selection():
            self._position = self._drag_anchor_ratio
            self.seeked.emit(self._drag_anchor_ratio)
            self.update()
        # headerdrag 임계값 미만으로 끝나면 단순 클릭 → seek 만. seg_drag_range 폐기.
        if self._drag_mode == "headerdrag":
            self._seg_drag_range = None
            if self._hover_segment_idx != -1:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        self._drag_anchor_ratio = None
        self._drag_mode = ""

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Escape and self.has_selection():
            self.clear_selection()
            e.accept()
            return
        super().keyPressEvent(e)

    def _ensure_grads(self, h: int):
        """3 테마 인지 그라데이션: light(웜 크림), grey(중성), neon(다크 네이비/블루)."""
        if self._grad_bg is None:
            from app.ui.theme import current_theme
            t = current_theme()
            light = (t == 'light')
            neutral = (t == 'grey')

            # ─── 배경 그라데이션 ───
            g = QLinearGradient(0, 0, 0, h)
            if light:
                g.setColorAt(0.0, QColor(243, 238, 224))
                g.setColorAt(0.55, QColor(235, 230, 216))
                g.setColorAt(1.0, QColor(221, 214, 196))
            elif neutral:
                g.setColorAt(0.0, QColor(38, 38, 38))
                g.setColorAt(0.55, QColor(28, 28, 28))
                g.setColorAt(1.0, QColor(22, 22, 22))
            else:  # neon (다크)
                g.setColorAt(0.0, QColor(17, 22, 31))
                g.setColorAt(0.55, QColor(10, 13, 19))
                g.setColorAt(1.0, QColor(6, 8, 16))
            self._grad_bg = g

            # ─── 미재생 파형 ───
            g2 = QLinearGradient(0, 0, 0, h)
            if light:
                g2.setColorAt(0.0, QColor(70, 64, 47))
                g2.setColorAt(0.5, QColor(51, 46, 34))
                g2.setColorAt(1.0, QColor(31, 28, 20))
            elif neutral:
                g2.setColorAt(0.0, QColor(200, 200, 200))
                g2.setColorAt(0.5, QColor(176, 176, 176))
                g2.setColorAt(1.0, QColor(152, 152, 152))
            else:  # neon (블루)
                g2.setColorAt(0.0, QColor(107, 170, 245))
                g2.setColorAt(0.5, QColor(78, 144, 232))
                g2.setColorAt(1.0, QColor(100, 160, 245))
            self._grad_un = g2

            # ─── 재생완료 파형 ───
            g3 = QLinearGradient(0, 0, 0, h)
            if light:
                g3.setColorAt(0.0, QColor(192, 142, 48))
                g3.setColorAt(0.5, QColor(160, 120, 32))
                g3.setColorAt(1.0, QColor(184, 136, 44))
            elif neutral:
                g3.setColorAt(0.0, QColor(245, 245, 245))
                g3.setColorAt(0.5, QColor(220, 220, 220))
                g3.setColorAt(1.0, QColor(245, 245, 245))
            else:  # neon (amber warm)
                g3.setColorAt(0.0, QColor(255, 216, 132))
                g3.setColorAt(0.5, QColor(255, 166, 74))
                g3.setColorAt(1.0, QColor(255, 207, 110))
            self._grad_pl = g3

    def _build_polys(self, w: int, h: int) -> List[QPolygonF]:
        """vectorized 좌표 계산 — Python 루프는 QPointF 객체 생성 한 번만.
        가로 줌 폐지로 레벨은 항상 1개(LEVEL_SLICES 단일 항목), 전체를 폭에 맞춰 그린다.
        """
        polys: List[QPolygonF] = []
        peaks = self._peak_levels[0] if self._peak_levels else self._peaks
        n = peaks.shape[0] if peaks.ndim == 3 else 0
        ch = self._channels
        if n == 0 or ch <= 0 or w <= 0:
            return polys
        peak_ch = peaks.shape[1]
        wave_top = self._wave_top()
        wave_bot = self._wave_bottom()
        wave_h = max(1, wave_bot - wave_top)
        ch_h = wave_h / ch
        base_amp = max(1.0, ch_h / 2 - 4)
        amp = base_amp * self._amp_zoom
        clip_limit = max(1.0, ch_h / 2 - 1)
        # 세로 줌 애니메이션 중엔 2픽셀 step (성능). 정적일 땐 1픽셀.
        animating = self._zoom_anim_timer.isActive()
        step = 2 if animating else 1
        xs = np.arange(0, w, step, dtype=np.float32)
        ratios = xs / w
        # 슬라이스가 화면 폭 대비 희박하면 (quick 16개 등) 최근접 조회가 넓적한
        # 블록 기둥으로 보여 "강한 소리 구간"으로 오해됨 → 슬라이스 중심 간
        # 선형 보간으로 스케치답게. 밀집 mipmap(1024+)은 기존 최근접 유지.
        sparse = n * 8 < w
        n_px = xs.shape[0]
        # 픽셀당 슬라이스가 여럿이면 점샘플링은 슬라이스를 건너뛰어 짧은 트랜지언트
        # (예: 파일 끝 클릭/비프)를 통째로 누락하고, ratio 가 1.0 에 못 미쳐 마지막
        # 슬라이스가 영영 안 찍힘 → '뒷부분 잘림'. 구간 min/max(reduceat)로 축약하면
        # 모든 슬라이스가 어느 픽셀엔가 반영되고, 마지막 구간 [edges[-1], n) 이 항상
        # 끝 슬라이스를 포함 → 늘 100% 전체 표시(+ 누엔도식 정확 peak envelope).
        dense = (not sparse) and n > n_px
        if sparse:
            pos = np.clip(ratios * n - 0.5, 0.0, float(n - 1))
            i0 = np.minimum(pos.astype(np.int32), n - 1)
            i1 = np.minimum(i0 + 1, n - 1)
            fr = (pos - i0).astype(np.float32)
        elif dense:
            edges = np.clip((ratios * n).astype(np.int64), 0, n - 1)
        else:
            idx = np.minimum((ratios * n).astype(np.int32), n - 1)
            idx = np.maximum(idx, 0)
        xs_l = xs.tolist()
        # 베이스헤드 풍 envelope 부드럽게 — 5-tap Gaussian (0.1/0.2/0.4/0.2/0.1).
        # 슬라이스별 잔 noise 깎고 양 끝이 자연스럽게 줄어드는 envelope 모양 강조.
        _smooth_kernel = np.array([0.1, 0.2, 0.4, 0.2, 0.1], dtype=np.float32)
        for c in range(ch):
            mid = wave_top + (c + 0.5) * ch_h
            cc = c if c < peak_ch else 0
            if sparse:
                mns = (peaks[i0, cc, 0] * (1.0 - fr) + peaks[i1, cc, 0] * fr).astype(np.float32, copy=False)
                mxs = (peaks[i0, cc, 1] * (1.0 - fr) + peaks[i1, cc, 1] * fr).astype(np.float32, copy=False)
            elif dense:
                mns = np.minimum.reduceat(peaks[:, cc, 0], edges).astype(np.float32, copy=False)
                mxs = np.maximum.reduceat(peaks[:, cc, 1], edges).astype(np.float32, copy=False)
            else:
                mns = peaks[idx, cc, 0].astype(np.float32, copy=False)
                mxs = peaks[idx, cc, 1].astype(np.float32, copy=False)
            if (not animating) and mxs.size >= 5:
                mxs = np.convolve(mxs, _smooth_kernel, mode='same')
                mns = np.convolve(mns, _smooth_kernel, mode='same')
            top_off = np.clip(mxs * amp, -clip_limit, clip_limit)
            bot_off = np.clip(mns * amp, -clip_limit, clip_limit)
            top_y, bot_y = self._filled_envelope_ys(mid, top_off, bot_off, clip_limit)
            top_pts = list(map(QPointF, xs_l, top_y))
            bot_pts = list(map(QPointF, xs_l, bot_y))
            bot_pts.reverse()
            polys.append(QPolygonF(top_pts + bot_pts))
        return polys

    @staticmethod
    def _filled_envelope_ys(mid: float, top_off: np.ndarray, bot_off: np.ndarray, clip_limit: float):
        half = np.maximum(np.maximum(np.abs(top_off), np.abs(bot_off)), WAVEFORM_MIN_HALF_PX)
        half = np.clip(half, 0.0, clip_limit)
        top = mid - half
        bot = mid + half
        return top.tolist(), bot.tolist()

    def _build_static_pixmap(self, w: int, h: int) -> Optional[QPixmap]:
        """배경+그리드+border+폴리곤+채널선 을 한 번만 paint 해서 pixmap 캐시.
        peaks/size/hover/channels 변경 시만 재생성. 매 frame paintEvent 는 이
        pixmap blit + playhead/selection/segments overlay 만 그림 → 120Hz 안정.
        color split (재생/미재생 색 분리) 는 매 frame clip + 두 번 drawPolygon
        이 비싸서 단일 색으로 단순화 (사용자 결정).
        """
        if w <= 0 or h <= 0:
            return None
        self._ensure_grads(h)
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        wave_top = self._wave_top()
        # 줌 애니메이션 중엔 AA off (성능). 정적일 땐 AA on (퀄리티).
        aa_on = not self._zoom_anim_timer.isActive()
        with QPainter(pm) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, aa_on)
            # 배경 + 헤더 zone overlay (테마 그라데이션)
            self._fill_bg_split(p, w, h)

            # ─── 테마-인지 그리드 ───
            from app.ui.theme import current_theme
            _theme = current_theme()
            light = (_theme == 'light')
            neutral = (_theme == 'grey')
            grid_col = QColor(43, 40, 32, 36) if light else QColor(255, 255, 255, 13)
            p.setPen(QPen(grid_col, 1))
            grid_top = max(wave_top + 4, 8)
            grid_bot = self._wave_bottom() - 4
            # 시간-인지 격자: viewport 기준 px_per_sec 따라 step 자동 결정
            total_sec = self._duration_ms / 1000.0 if self._duration_ms > 0 else 0.0
            if total_sec > 0:
                vp_start_sec = 0.0
                vp_end_sec = total_sec
                px_per_sec = w / max(1e-06, total_sec)
                candidates = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600)
                step = candidates[-1]
                for s in candidates:
                    if px_per_sec * s >= 18:
                        step = s
                        break
                t0 = (int(vp_start_sec / step) + 1) * step
                t = float(t0)
                while t < vp_end_sec:
                    x = int((t - vp_start_sec) * px_per_sec)
                    if 0 <= x < w:
                        p.drawLine(x, grid_top, x, grid_bot)
                    t += step
            else:
                # duration 없으면 1/8 균등 분할
                for i in range(1, 8):
                    x = int(w * i / 8)
                    p.drawLine(x, grid_top, x, grid_bot)

            # ─── 테마-인지 폴리곤 색 ───
            # neutral 은 어두운 회색 배경 위라 더 밝은 흰 회색으로 대비 강화.
            if light:
                fill_col = QColor(70, 64, 47)
            elif neutral:
                fill_col = QColor(240, 240, 240)
            else:
                fill_col = QColor(120, 175, 245)
            polygon_pen = QPen(fill_col, WAVEFORM_POLYGON_PEN_W)

            peaks = self._peaks
            n_pk = peaks.shape[0] if peaks.ndim == 3 else 0
            if n_pk > 0:
                polys = self._build_polys(w, h)
                if polys:
                    # 채널별 vertical gradient — mid 진하게 → envelope 가장자리 옅게.
                    # 그라데이션 범위를 채널 영역 (ch_top~ch_bot) 으로 잡아 polygon 좌표 따라 자연 보간.
                    ch_total = self._channels
                    wh = max(1, self._wave_bottom() - wave_top)
                    ch_h_g = wh / max(1, ch_total)
                    # 가장자리 알파 — neutral 은 대비 위해 더 진하게.
                    edge_col = QColor(fill_col)
                    edge_col.setAlpha(160 if neutral else 95)
                    p.setPen(polygon_pen)
                    for c, poly in enumerate(polys):
                        ch_top_y = wave_top + c * ch_h_g
                        ch_bot_y = wave_top + (c + 1) * ch_h_g
                        grad = QLinearGradient(0.0, ch_top_y, 0.0, ch_bot_y)
                        if neutral:
                            # neutral: 같은 흰색 alpha 변주(약함) 대신 진짜 RGB 대비.
                            # 중앙(zero선)=밝은 화이트, 가장자리로 갈수록 어두운 메탈.
                            # 0.30/0.70 중간 스톱으로 중앙 falloff 를 가파르게 →
                            # 채널 두께의 50% 만 차는 얇은 파형도 그라데이션이 또렷.
                            grad.setColorAt(0.0, QColor(58, 61, 68))
                            grad.setColorAt(0.30, QColor(120, 124, 132))
                            grad.setColorAt(0.5, QColor(249, 250, 252))
                            grad.setColorAt(0.70, QColor(120, 124, 132))
                            grad.setColorAt(1.0, QColor(58, 61, 68))
                        else:
                            grad.setColorAt(0.0, edge_col)
                            grad.setColorAt(0.5, fill_col)
                            grad.setColorAt(1.0, edge_col)
                        p.setBrush(QBrush(grad))
                        p.drawPolygon(poly)

            # 채널 구분선 (다채널 시)
            ch = self._channels
            wave_bot = self._wave_bottom()
            wave_h_eff = max(1, wave_bot - wave_top)
            ch_h = wave_h_eff / max(1, ch)
            if ch > 1:
                p.setPen(QPen(QColor(255, 255, 255, 32), 1))
                for c in range(1, ch):
                    y = int(wave_top + c * ch_h)
                    p.drawLine(0, y, w, y)

            # ruler 는 마지막에 그려서 폴리곤 위에 보이게
            self._paint_ruler(p, w)
        return pm

    def paintEvent(self, _: QPaintEvent):
        with QPainter(self) as p:
            w, h = self.width(), self.height()

            if self._loading:
                # PHASE-3L: PYZ 복구 — scan_x 박스 제거, _fill_bg_split 사용
                self._ensure_grads(h)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                self._fill_bg_split(p, w, h)
                p.setPen(QColor(186, 198, 215))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "파형 로딩 중...")
                return

            if self._peaks.shape[0] == 0:
                # PHASE-3L: PYZ 복구 — _fill_bg_split (헤더 tint 포함) 사용
                self._ensure_grads(h)
                self._fill_bg_split(p, w, h)
                if (self._current_path
                        and os.path.splitext(self._current_path)[1].lower() not in WAVEFORM_EXTS):
                    msg = "이 포맷은 파형 미지원 (재생만 가능)"
                elif self._current_path:
                    msg = "파형 추출 실패"
                else:
                    msg = "선택된 파일 없음"
                p.setPen(QColor(135, 146, 162))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
                return

            # 캐시 무효화 — size 변경 또는 캐시 없음
            if (self._static_pixmap is None
                    or self._static_pixmap.width() != int(w * self._static_pixmap.devicePixelRatio())
                    or self._static_pixmap.height() != int(h * self._static_pixmap.devicePixelRatio())):
                self._static_pixmap = self._build_static_pixmap(w, h)

            if self._static_pixmap is not None:
                p.drawPixmap(0, 0, self._static_pixmap)

            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            # 줌/팬 반영된 playhead x 위치
            if 0.0 <= self._position <= 1.0:
                pos_x_f = self._x_at_ratio(self._position)
                pos_x = int(pos_x_f) if 0 <= pos_x_f < w else -1
            else:
                pos_x = -1

            if pos_x >= 0:
                # PHASE-3L: PYZ 복구 — playhead 라인을 fillRect 로 단순화
                glow_alpha = 60 + int(45 * abs(0.5 - self._anim_phase) * 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 184, 77, glow_alpha))
                p.drawRoundedRect(QRectF(float(pos_x - 4), 4.0, 8.0, float(h - 8)), 4, 4)
                c = QColor(255, 190, 84)
                p.fillRect(QRectF(float(pos_x - 1), 0.0, 2.0, float(h)), c)
                p.setBrush(c)
                p.drawEllipse(QPointF(float(pos_x), 8.0), 5.0, 5.0)

            # 선택 영역 (반투명 오버레이 + 경계선) — 줌/팬 반영
            if self.has_selection():
                x1 = int(self._x_at_ratio(self._sel_start))
                x2 = int(self._x_at_ratio(self._sel_end))
                # viewport 밖 클립
                x1c = max(0, x1); x2c = min(w, x2)
                if x2c > x1c:
                    # 루프 활성 시 보라, 평소 청록
                    if self._loop_active:
                        fill_col = QColor(180, 130, 230, 60)
                        edge = QColor(210, 170, 250, 220)
                    else:
                        fill_col = QColor(93, 226, 199, 58)
                        edge = QColor(151, 235, 230, 220)
                    p.fillRect(x1c, 0, x2c - x1c, h, fill_col)
                    p.setPen(QPen(edge, 1))
                    if 0 <= x1 < w:
                        p.drawLine(x1, 0, x1, h)
                    if 0 <= x2 < w:
                        p.drawLine(x2, 0, x2, h)

            # 세그먼트 분할선 + 헤더 (마지막에 overlay) — 줌/팬 반영
            if self._has_visible_segments():
                n_seg = self._segments.shape[0]
                # 헤더 박스 — Soundly 스타일 사각형 (라운딩 제거, 딱딱 붙게)
                for i in range(n_seg):
                    s = float(self._segments[i, 0])
                    e = float(self._segments[i, 1])
                    x1 = int(self._x_at_ratio(s))
                    x2 = int(self._x_at_ratio(e))
                    if x2 <= x1 or x2 < 0 or x1 > w:
                        continue

                    if i == self._active_segment_idx:
                        fill = QColor(160, 90, 230, 110)
                        border = QColor(210, 160, 255, 140)
                    elif i == self._hover_segment_idx:
                        fill = QColor(70, 200, 140, 80)
                        border = QColor(160, 255, 200, 120)
                    else:
                        fill = QColor(100, 120, 150, 45)
                        border = QColor(120, 140, 170, 70)

                    p.setBrush(fill)
                    p.setPen(QPen(border, 1))
                    # 세그먼트 헤더는 ruler(TIMELINE_RULER_H) 아래에 별도 레이어로
                    p.drawRect(QRectF(float(x1), float(TIMELINE_RULER_H),
                                      float(x2 - x1), float(SEGMENT_HEADER_H - 1)))

                # 세그먼트 번호 (헤더 안에 좌측 정렬, 5px 패딩)
                num_font = QFont()  # 시스템 기본 폰트 (결과표 폰트와 동일)
                num_font.setPointSize(8)
                num_font.setWeight(QFont.Weight.Bold)
                p.setFont(num_font)
                for i in range(n_seg):
                    s = float(self._segments[i, 0])
                    e = float(self._segments[i, 1])
                    x1 = int(self._x_at_ratio(s))
                    x2 = int(self._x_at_ratio(e))
                    if x2 - x1 < 14 or x2 < 0 or x1 > w:
                        continue
                    if i == self._active_segment_idx:
                        text_col = QColor(255, 255, 255, 240)
                    elif i == self._hover_segment_idx:
                        text_col = QColor(220, 255, 235, 220)
                    else:
                        text_col = QColor(200, 210, 225, 180)
                    p.setPen(text_col)
                    p.drawText(QRectF(float(x1 + 5), float(TIMELINE_RULER_H),
                                      float(x2 - x1 - 5), float(SEGMENT_HEADER_H - 1)),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{i + 1}")

                # 파형 영역 수직 분할선 (wave_top 부터 아래로) — 줌/팬 반영
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
                wave_top = self._wave_top()
                for i in range(1, n_seg):
                    x = int(self._x_at_ratio(float(self._segments[i, 0])))
                    if 0 <= x < w:
                        p.drawLine(x, wave_top, x, h - 2)


def _fmt_ms(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


# ─── 볼륨 dB 페이더 법칙 (DAW식 로그 테이퍼) ───
# 슬라이더 위치(0~1000) ↔ dB. 끝점: 1000=+6.02dB(진폭2배), 0=−∞(음소거), 0dB=유니티.
# 0dB(유니티)는 75% 위치 — 상단 25%가 부스트(0~+6.02dB), 하단은 컷(0~VOL_FLOOR_DB).
# 해상도 0~1000: 휠 0.25dB 단위 조절이 가능하도록 (0~100이면 컷 구간 한 칸 ≈ 0.68dB).
VOL_MAX_DB = 6.02          # 슬라이더 1000 = +6.02 dB = 게인 2.0
VOL_FLOOR_DB = -50.0       # 슬라이더 1 = 유한 최저(-50 dB). 슬라이더 0 = −∞
VOL_UNITY_POS = 750        # 0.00 dB(유니티 게인) 슬라이더 위치 = reset 기본값
VOL_TOP_POS = 1000
VOL_WHEEL_DB_STEP = 0.25   # 휠 1틱당 dB 이동량


def _vol_pos_to_db(pos: int) -> float:
    if pos <= 0:
        return float("-inf")
    if pos >= VOL_UNITY_POS:  # 부스트: 75→0dB, 100→+6.02dB (dB 선형)
        return (pos - VOL_UNITY_POS) / (VOL_TOP_POS - VOL_UNITY_POS) * VOL_MAX_DB
    # 컷: 1→VOL_FLOOR_DB, 75→0dB (dB 선형)
    return VOL_FLOOR_DB * (1.0 - (pos - 1) / (VOL_UNITY_POS - 1))


def _vol_pos_to_gain(pos: int) -> float:
    db = _vol_pos_to_db(pos)
    if db == float("-inf"):
        return 0.0
    return 10.0 ** (db / 20.0)


def _vol_db_to_pos(db: float) -> int:
    if db == float("-inf") or db <= VOL_FLOOR_DB:
        return 0
    db = min(db, VOL_MAX_DB)
    if db >= 0.0:
        pos = VOL_UNITY_POS + db / VOL_MAX_DB * (VOL_TOP_POS - VOL_UNITY_POS)
    else:
        pos = 1.0 + (1.0 - db / VOL_FLOOR_DB) * (VOL_UNITY_POS - 1)
    return int(round(max(0, min(VOL_TOP_POS, pos))))


class _VolWheelSlider(_ResetSlider):
    """휠 1틱 = VOL_WHEEL_DB_STEP(0.25dB) 단위 이동 + 우클릭 리셋."""
    def wheelEvent(self, e):
        step = VOL_WHEEL_DB_STEP if e.angleDelta().y() > 0 else -VOL_WHEEL_DB_STEP
        db = _vol_pos_to_db(self.value())
        if db == float("-inf"):
            new_db = VOL_FLOOR_DB if step > 0 else db
        else:
            new_db = round((db + step) / VOL_WHEEL_DB_STEP) * VOL_WHEEL_DB_STEP
            if new_db < VOL_FLOOR_DB:
                new_db = float("-inf")
        self.setValue(_vol_db_to_pos(new_db))
        e.accept()


def _fmt_db(pos: int) -> str:
    db = _vol_pos_to_db(pos)
    if db == float("-inf"):
        return "−∞ dB"
    if abs(db) < 0.005:
        return "0.00 dB"
    return f"{db:+.2f} dB"


class _AutomaticLayoutMenuRow(QWidget):
    """자동 판정 결과를 강조색으로 함께 보여주는 메뉴 첫 행."""

    clicked = pyqtSignal()

    def __init__(self, result: str, selected: bool, enabled: bool, parent=None):
        super().__init__(parent)
        from app.ui.theme import COLORS, current_theme

        self.setObjectName("automaticLayoutRow")
        self.setEnabled(enabled)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if enabled else Qt.CursorShape.ArrowCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 14, 7)
        row.setSpacing(14)
        self.mode_label = QLabel(
            f"{'✓  ' if selected else '   '}자동 판정", self)
        self.result_label = QLabel(result, self)
        # QWidgetAction 안의 자식 QLabel이 마우스 이벤트를 가로채면 텍스트를
        # 눌렀을 때 자동 판정 복귀가 실행되지 않는다. 행 전체가 한 버튼처럼 동작한다.
        self.mode_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.result_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.result_label.setObjectName("automaticLayoutResult")
        result_font = QFont(self.result_label.font())
        result_font.setBold(True)
        self.result_label.setFont(result_font)
        result_color = "#315f9b" if current_theme() == "light" else "#75b7ff"
        self.result_label.setStyleSheet(f"color: {result_color};")
        row.addWidget(self.mode_label)
        row.addStretch(1)
        row.addWidget(self.result_label)
        self.setMinimumWidth(270)
        self.setStyleSheet(
            f"QWidget#automaticLayoutRow:hover {{ background: {COLORS['accent_dim']}; }}")

    def mouseReleaseEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if (self.isEnabled()
                and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                    Qt.Key.Key_Space}):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _AmbisonicFormatDialog(QDialog):
    """AmbiX/FuMa를 저장 전에 같은 위치에서 번갈아 듣는 비모달 창."""

    previewRequested = pyqtSignal(str)
    saveRequested = pyqtSignal(str)

    def __init__(self, file_name: str, reason: str, parent=None):
        super().__init__(parent)
        self._preset = ""
        self.setWindowTitle("앰비소닉 규격 선택")
        self.setModal(False)
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel(f"{file_name}\n\n{reason}")
        title.setWordWrap(True)
        layout.addWidget(title)

        guide = QLabel(
            "파일명만으로 AmbiX와 FuMa를 구분할 수 없습니다. "
            "두 규격을 번갈아 들어보고 방향과 공간감이 자연스러운 쪽을 저장하세요.")
        guide.setWordWrap(True)
        layout.addWidget(guide)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(8)
        self.ambix_btn = QPushButton("AmbiX로 들어보기")
        self.fuma_btn = QPushButton("FuMa로 들어보기")
        for button, preset in ((self.ambix_btn, "ambix"),
                               (self.fuma_btn, "fuma")):
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=preset: self._choose(value))
            preview_row.addWidget(button)
        layout.addLayout(preview_row)

        self.status_label = QLabel("아직 미리 들을 규격을 선택하지 않았습니다")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #aeb4bf;")
        layout.addWidget(self.status_label)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        later_btn = QPushButton("나중에")
        later_btn.clicked.connect(self.reject)
        action_row.addWidget(later_btn)
        self.save_btn = QPushButton("현재 규격 저장")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        action_row.addWidget(self.save_btn)
        layout.addLayout(action_row)

    def _choose(self, preset: str):
        self._preset = preset
        self.ambix_btn.setChecked(preset == "ambix")
        self.fuma_btn.setChecked(preset == "fuma")
        label = "1차 AmbiX" if preset == "ambix" else "1차 FuMa"
        self.status_label.setText(f"현재 미리듣기 · {label} · 처음부터 재생 중")
        self.save_btn.setEnabled(True)
        self.previewRequested.emit(preset)

    def _save(self):
        if self._preset:
            self.saveRequested.emit(self._preset)


class _ChannelOrderDialog(QDialog):
    """기술 규격명 없이 두 스피커 채널 순서를 비교해 저장한다."""

    previewRequested = pyqtSignal(str)
    saveRequested = pyqtSignal(str)

    def __init__(self, file_name: str, preset: str,
                 current_order: str, parent=None):
        super().__init__(parent)
        self._preset = preset
        self._orders = (current_order, "film" if current_order == "wave" else "wave")
        self._selected = ""
        self.setWindowTitle("채널 배치 비교해서 듣기")
        self.setModal(False)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel(f"{file_name}\n\n두 배치를 번갈아 듣고 더 자연스러운 쪽을 저장하세요.")
        title.setWordWrap(True)
        layout.addWidget(title)
        guide = QLabel(
            "중앙 소리가 한쪽으로 치우치지 않는지, 뒤쪽 소리가 사라지거나 "
            "반대편으로 이동하지 않는지 확인하세요. 저음이 방향감 있는 일반 "
            "소리처럼 들리지 않는 쪽이 올바른 배치일 가능성이 높습니다.")
        guide.setWordWrap(True)
        layout.addWidget(guide)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.current_btn = QPushButton("현재 방식으로 들어보기")
        self.alternate_btn = QPushButton("다른 방식으로 들어보기")
        for button, order in zip(
                (self.current_btn, self.alternate_btn), self._orders):
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=order: self._choose(value))
            row.addWidget(button)
        layout.addLayout(row)

        self.status_label = QLabel("두 방식 중 하나를 눌러 처음부터 들어보세요")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #aeb4bf;")
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        later = QPushButton("나중에")
        later.clicked.connect(self.reject)
        actions.addWidget(later)
        self.save_btn = QPushButton("이 방식 사용")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        actions.addWidget(self.save_btn)
        layout.addLayout(actions)

    def _choose(self, order: str):
        self._selected = order
        self.current_btn.setChecked(order == self._orders[0])
        self.alternate_btn.setChecked(order == self._orders[1])
        label = "현재 방식" if order == self._orders[0] else "다른 방식"
        self.status_label.setText(f"{label} · 처음부터 재생 중")
        self.save_btn.setEnabled(True)
        self.previewRequested.emit(f"{self._preset}|{order}")

    def _save(self):
        if self._selected:
            self.saveRequested.emit(f"{self._preset}|{self._selected}")


class PlayerWidget(QWidget):
    """파형 + 재생 컨트롤 + 재생 히스토리(100)."""

    historyChanged = pyqtSignal()  # 히스토리 변경 시 (UI 갱신 트리거)
    playingChanged = pyqtSignal(str)  # 재생 중인 파일 경로 ("" = 정지)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self._path: str = ""
        self._dur_ms: int = 0
        self._last_known_pos_ms: int = 0
        self._last_known_pos_t: float = 0.0
        self._playhead_smoothing_ready: bool = False
        self._display_pos_ms: float = 0.0
        # ─── PHASE-3B STUB (rollback recovery) — PYZ __init__ 누락 attribute 복원 ───
        self._loop_enabled: bool = False
        self._config: dict = {"playback_restart_from_zero": True}
        self._play_load_gen: int = 0
        self._wave_load_gen: int = 0
        # 같은 path 재생 요청 디바운스 — 한 클릭이 selection 타이머 + cellClicked
        # 두 경로로 load_and_play 를 연달아 호출해 앞부분이 재시작되는 것 차단
        self._last_play_request_path: str = ""
        self._last_play_request_t: float = 0.0
        self._current_meta: dict = {}
        self._layout_overrides = LayoutOverrideStore()
        self._binaural_runtime_available: bool = False
        self._binaural_unavailable_reason: str = ""
        self._binaural_visual_state: str = "off"
        self._binaural_status_text: str = ""
        self._layout_prompted_paths: set[str] = set()
        self._layout_required_paths: set[str] = set()
        self._layout_prompt_box: Optional[QWidget] = None
        self._iem_install_prompt_box: Optional[QMessageBox] = None
        self._layout_preview_path: str = ""
        self._layout_preview_preset: str = ""
        self._runtime_binaural_layout = None
        # ─────────────────────────────────────────────────────────────────────────
        self._history: deque = deque(maxlen=HISTORY_MAX)
        self._load_history()
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(5, 3, 5, 5)
        v.setSpacing(4)

        self.wave = WaveformView()
        self.wave.seeked.connect(self._on_seek)
        self.wave.dragRegionRequested.connect(self._on_drag_region)
        self.wave.durationDecoded.connect(self._on_wave_duration_decoded)
        v.addWidget(self.wave, 1)
        # (가로 줌 폐지 — viewport 팬용 가로 스크롤바 제거됨)

        self._control_rows_box = QVBoxLayout()
        self._control_rows_box.setContentsMargins(0, 0, 0, 0)
        self._control_rows_box.setSpacing(3)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self._control_second_row = QHBoxLayout()
        self._control_second_row.setContentsMargins(0, 0, 0, 0)
        self._control_second_row.setSpacing(6)
        self._control_third_row = QHBoxLayout()
        self._control_third_row.setContentsMargins(0, 0, 0, 0)
        self._control_third_row.setSpacing(6)
        self._control_row_layouts = (
            bar, self._control_second_row, self._control_third_row)
        self._control_rows_box.addLayout(bar)
        self._control_rows_box.addLayout(self._control_second_row)
        self._control_rows_box.addLayout(self._control_third_row)
        # 재생 / 일시정지 토글 (▶ ⇄ ⏸) — 레퍼런스 스타일: 채워진 버튼
        # 재생/정지/처음으로는 아이콘이 직관적이고 자주 호버 → 툴팁 없음(반복재생만 유지).
        self.play_btn = _TransportButton("▶", is_filled=True, font_size=16)
        self.play_btn.setFixedSize(30, 26)
        self.play_btn.clicked.connect(self._toggle); bar.addWidget(self.play_btn)

        # 정지 (■)
        self.stop_btn = _TransportButton("■", font_size=14)
        self.stop_btn.setFixedSize(30, 26)
        self.stop_btn.clicked.connect(self._on_stop_clicked); bar.addWidget(self.stop_btn)

        # 처음으로 (⏮)
        self.seek_start_btn = _TransportButton("⏮", font_size=14)
        self.seek_start_btn.setFixedSize(30, 26)
        self.seek_start_btn.clicked.connect(self._on_seek_start_clicked); bar.addWidget(self.seek_start_btn)

        # ─── PHASE-3J: 반복 재생 토글 (처음으로 옆) ───
        # OFF: accent 외곽선/아이콘.  ON: 보라색 진한 fill + 보라 외곽선/아이콘 (모든 테마 동일).
        self.loop_btn = _TransportButton("↻", font_size=14,
                                         active_color="#8B3FE6", filled_when_on=True)
        self.loop_btn.setFixedSize(30, 26)
        self.loop_btn.setCheckable(True)
        self.loop_btn.setToolTip("반복 재생 — 영역 선택 시 그 부분만, 없으면 전체 (R)")
        self.loop_btn.toggled.connect(self._on_loop_toggled)
        bar.addWidget(self.loop_btn)
        # ─────────────────────────────────────────────

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setFixedWidth(120)  # 자간 0.12em 반영 — 끝자리 잘림 방지
        bar.addWidget(self.time_label)

        # 파일 정보 그룹 — 넓을 땐 한 줄(캡션+이름+메타), 좁을 땐 두 줄(윗줄 이름 / 아랫줄 메타).
        # 윗줄은 HBox + 이름 stretch(검증된 방식)라 이름이 남는 폭을 꽉 채운다.
        self._info_container = QWidget()
        # 바의 stretch 공간을 실제로 채우도록 Expanding (기본 Preferred 면 안 늘어남).
        self._info_container.setSizePolicy(QSizePolicy.Policy.Expanding,
                                           QSizePolicy.Policy.Preferred)
        self._info_vbox = QVBoxLayout(self._info_container)
        self._info_vbox.setContentsMargins(0, 0, 0, 0)
        self._info_vbox.setSpacing(0)
        self._info_top = QHBoxLayout()
        self._info_top.setContentsMargins(0, 0, 0, 0)
        self._info_top.setSpacing(6)
        # 재생 파일명 앞 캡션 — 파일이 로드됐을 때만 "현재 재생 중 :" 표시.
        self.now_playing_prefix = QLabel("")
        self.now_playing_prefix.setObjectName("nowPlayingLabel")
        self._info_top.addWidget(self.now_playing_prefix)
        self.name_label = _ElidedLabel("")
        self.name_label.setObjectName("fileLabel")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name_label.setMinimumWidth(24)
        self._info_top.addWidget(self.name_label, 1)   # 이름이 가로 확장
        # 채널·샘플레이트·비트뎁스는 재생 바에서 반복 표기하지 않는다.
        # 값은 계속 갱신해 파일명 툴팁과 다른 메타데이터 기능에서 제공한다.
        self.meta_label = QLabel("", self._info_container)
        self.meta_label.setObjectName("metaLabel")
        self.meta_label.setToolTip("채널 · 샘플레이트 · 비트뎁스")
        self.meta_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.meta_label.setVisible(False)
        self._info_vbox.addLayout(self._info_top)
        bar.addWidget(self._info_container, 1)

        # 왼쪽은 실제 출력 형식 표시 + 바이노럴 토글, 오른쪽은 채널 배치 메뉴.
        # 설정값과 실제 출력이 어긋나지 않도록 하나의 분할 컨트롤로 묶는다.
        self.binaural_control = QWidget()
        self.binaural_control.setObjectName("binauralSplitControl")
        # 메인 버튼(320)과 드롭다운(24)이 넓은 행에서 서로 떨어지지 않도록
        # 분할 컨트롤 전체 폭도 두 버튼의 합으로 고정한다.
        self.binaural_control.setFixedWidth(345)
        binaural_stack = QVBoxLayout(self.binaural_control)
        binaural_stack.setContentsMargins(0, 0, 0, 0)
        binaural_stack.setSpacing(1)
        binaural_row = QWidget(self.binaural_control)
        binaural_split = QHBoxLayout(binaural_row)
        binaural_split.setContentsMargins(0, 0, 0, 0)
        binaural_split.setSpacing(1)

        self.binaural_btn = _TransportButton(
            "출력 대기", font_size=8, filled_when_on=True, button_style=True,
            state_chip=True)
        # _TransportButton 기본 글꼴(JetBrains Mono)은 한글 글리프가 없어
        # 커스텀 페인터에서 라벨이 빈칸으로 보인다. 이 텍스트 버튼만
        # Windows 한글 UI 글꼴을 명시한다.
        binaural_font = self.binaural_btn.font()
        binaural_font.setFamily("Malgun Gothic")
        self.binaural_btn.setFont(binaural_font)
        # 입력 포맷 → 실제 출력만 간결하게 표시한다. 상세 판정 근거는
        # 드롭다운과 툴팁에서 제공하므로 컨트롤 바를 과도하게 차지하지 않는다.
        self.binaural_btn.setFixedSize(320, 22)
        # 전역 QPushButton의 세로 padding/min-height가 fixedSize보다 우선해
        # 양옆의 22px 배지·설정 버튼과 높이가 어긋나지 않게 한다.
        self.binaural_btn.setStyleSheet(
            "QPushButton { min-height: 0; padding: 0; }")
        self.binaural_btn.setCheckable(True)
        self.binaural_btn.setToolTip(
            "현재 실제 출력 형식")
        self.binaural_btn.toggled.connect(self._on_binaural_toggled)
        binaural_split.addWidget(self.binaural_btn)

        self.binaural_menu_btn = _TransportButton(
            "▾", font_size=9, button_style=True)
        self.binaural_menu_btn.setFixedSize(24, 22)
        self.binaural_menu_btn.setStyleSheet(
            "QPushButton { min-height: 0; padding: 0; }")
        self.binaural_menu_btn.setToolTip("바이노럴 입력 채널 배치 선택")
        self.binaural_menu_btn.clicked.connect(self._show_binaural_layout_menu)
        binaural_split.addWidget(self.binaural_menu_btn)
        binaural_stack.addWidget(binaural_row)

        self.binaural_layout_detail = QWidget(self.binaural_control)
        binaural_detail_row = QHBoxLayout(self.binaural_layout_detail)
        binaural_detail_row.setContentsMargins(2, 0, 1, 0)
        binaural_detail_row.setSpacing(3)
        self.binaural_layout_warning = QLabel("채널 배치 확인 필요")
        self.binaural_layout_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.binaural_layout_warning.setFixedHeight(13)
        self.binaural_layout_warning.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.binaural_layout_warning.setStyleSheet(
            "color: #e45b64; font-size: 8pt; font-weight: 700;")
        self.binaural_layout_warning.setToolTip(
            "오른쪽 화살표를 눌러 가능한 채널 배치 중 하나를 선택하세요")
        binaural_detail_row.addWidget(self.binaural_layout_warning, 1)
        self.binaural_layout_reset_btn = QPushButton("초기화")
        self.binaural_layout_reset_btn.setFixedSize(43, 13)
        self.binaural_layout_reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.binaural_layout_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.binaural_layout_reset_btn.setStyleSheet(
            "QPushButton { color: #aeb4bf; background: transparent; border: 0; "
            "font-size: 7.5pt; font-weight: 600; min-height: 0; padding: 0; }"
            "QPushButton:hover { color: #ffffff; text-decoration: underline; }"
            "QPushButton:pressed { color: #8f98a8; }")
        self.binaural_layout_reset_btn.setToolTip(
            "이 파일에 저장된 채널 배치 선택을 지우고 다시 판정합니다")
        self.binaural_layout_reset_btn.clicked.connect(
            self._reset_saved_binaural_layout)
        binaural_detail_row.addWidget(self.binaural_layout_reset_btn)
        binaural_stack.addWidget(self.binaural_layout_detail)
        self._set_layout_warning_visible(False)
        bar.addWidget(self.binaural_control)

        # 속도: 라벨·슬라이더·값을 하나의 고정 간격 그룹으로 묶는다.
        # 바깥 행의 여유 공간이 늘어나도 그룹 내부 간격은 벌어지지 않는다.
        self.speed_control = QWidget()
        self.speed_control.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        speed_row = QHBoxLayout(self.speed_control)
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.setSpacing(6)
        self.speed_label = QLabel("SPD")
        self.speed_label.setObjectName("controlLabel")
        speed_row.addWidget(self.speed_label)
        # log2 매핑: position p ∈ [-100,100], rate = 2^(p/100). p=0 → 1.0x 가 정중앙,
        # 양끝이 0.5x(2⁻¹)/2.0x(2¹) 로 대칭. 선형이면 1.0x 가 1/3 지점이라 왼쪽 치우침.
        self.speed_slider = _SingleStepSlider(Qt.Orientation.Horizontal, default_value=0)
        self.speed_slider.setRange(-100, 100)
        self.speed_slider.setValue(0)
        self.speed_slider.setFixedWidth(88)
        self.speed_slider.setToolTip("재생속도 0.5x ~ 2.0x (1.0x 중앙, 더블클릭으로 직접입력)")
        self.speed_value_label = _EditableValueLabel("1.0x")
        self.speed_value_label.setObjectName("controlLabel")
        self.speed_value_label.setFixedWidth(44)
        self.speed_value_label.setToolTip("더블클릭으로 직접 입력 (예: 1.5)")
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        self.speed_value_label.valueCommitted.connect(self._on_speed_text_input)
        speed_row.addWidget(self.speed_slider)
        speed_row.addWidget(self.speed_value_label)
        bar.addWidget(self.speed_control)

        # 볼륨도 속도와 같은 방식으로 하나의 고정 간격 그룹으로 유지한다.
        self.volume_control = QWidget()
        self.volume_control.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        volume_row = QHBoxLayout(self.volume_control)
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(6)
        self.vol_label = QLabel("VOL")
        self.vol_label.setObjectName("controlLabel")
        volume_row.addWidget(self.vol_label)
        self.vol_slider = _VolWheelSlider(Qt.Orientation.Horizontal, default_value=VOL_UNITY_POS)
        self.vol_slider.setObjectName("volumeFader")
        self.vol_slider.setRange(0, VOL_TOP_POS); self.vol_slider.setValue(VOL_UNITY_POS)
        self.vol_slider.setFixedWidth(110)
        self.vol_slider.setToolTip("볼륨 (dB) — 0 dB=유니티, 최대 +6.02 dB, 최소 −∞\n우클릭/Ctrl+클릭: 0 dB로 리셋")
        self.vol_value_label = _EditableValueLabel("0.00 dB")
        self.vol_value_label.setObjectName("controlLabel")
        self.vol_value_label.setFixedWidth(66)
        self.vol_value_label.setToolTip("더블클릭으로 dB 직접 입력 (예: 0, -6, +3.5, -inf)")
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.vol_value_label.valueCommitted.connect(self._on_volume_text_input)
        volume_row.addWidget(self.vol_slider)
        volume_row.addWidget(self.vol_value_label)
        bar.addWidget(self.volume_control)

        # 세그먼트 토글 — 별도 기능임을 알 수 있게 OFF 상태에도 accent 외곽선 유지 (테마 따라감)
        self.seg_toggle_btn = _TransportButton("▥", font_size=15, accent_border=True)
        self.seg_toggle_btn.setFixedSize(22, 22)
        self.seg_toggle_btn.setCheckable(True)
        self.seg_toggle_btn.setChecked(True)
        self.seg_toggle_btn.setToolTip("세그먼트 표시/숨기기 (S)")
        self.seg_toggle_btn.toggled.connect(self.wave._on_seg_toggle)
        bar.addWidget(self.seg_toggle_btn)

        self._control_widgets = (
            self.play_btn, self.stop_btn, self.seek_start_btn, self.loop_btn,
            self.time_label, self._info_container, self.binaural_control,
            self.speed_control, self.volume_control,
            self.seg_toggle_btn,
        )
        self._control_layout_mode = ""
        self._apply_control_layout(force=True)
        v.addLayout(self._control_rows_box)

        self._playback_rate: float = 1.0

        self.player = HybridPlayer(self)
        self.player.setVolume(_vol_pos_to_gain(VOL_UNITY_POS))  # 0 dB = 게인 1.0
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        # PHASE-3J: EOF 자연 종료 → 반복 재생 처리 (영역 선택 또는 전체)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.binauralStatusChanged.connect(self._on_binaural_status)
        self.player.binauralAvailabilityChanged.connect(self._on_binaural_availability)
        self.player.binauralLayoutResolved.connect(
            self._on_runtime_binaural_layout)
        self._binaural_runtime_available = self.player.isBinauralInstalled()
        if not self._binaural_runtime_available:
            self._binaural_unavailable_reason = "바이노럴 처리 구성 요소가 설치되지 않았습니다"
        self._sync_binaural_policy()
        self._update_output_control()

        # 재생 시간/파형 헤드 갱신 — 120Hz. wave body 가 pixmap 캐시라 매 frame
        # paint 는 drawPixmap + playhead 만 (sub-millisecond). 정지 시 _on_state 가 stop.
        self._tick = QTimer(self)
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.setInterval(FRAME_MS)
        self._tick.timeout.connect(self._on_tick)


    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_control_layout()
        # 좁은 폭에서 info 라벨이 우측 고정폭 컨트롤에 눌려 겹치던 문제 — 두 줄로 reflow.
        #   넓을 때: [캡션][이름][메타] 한 줄.
        #   좁을 때: 윗줄 [이름] (캡션 숨김, 가로 최대 확보) / 아랫줄 [메타].
        narrow = self.width() < 820
        if getattr(self, "_info_narrow", None) is narrow:
            return
        self._info_narrow = narrow
        self._apply_info_layout()

    def _apply_control_layout(self, force: bool = False):
        """창 폭에 맞춰 하단 컨트롤을 잘리지 않는 행으로 재배치한다."""
        if not hasattr(self, "_control_widgets"):
            return
        width = self.width()
        mode = "wide" if width >= 1280 else "compact" if width < 920 else "two_row"
        if not force and mode == self._control_layout_mode:
            return
        self._control_layout_mode = mode
        for layout in self._control_row_layouts:
            for widget in self._control_widgets:
                layout.removeWidget(widget)

        primary, secondary, tertiary = self._control_row_layouts
        transport = (
            self.play_btn, self.stop_btn, self.seek_start_btn, self.loop_btn,
            self.time_label)
        speed = self.speed_control
        volume = self.volume_control

        if mode == "wide":
            for widget in transport:
                primary.addWidget(widget)
            primary.addWidget(self._info_container, 1)
            primary.addWidget(self.binaural_control)
            for widget in (speed, volume, self.seg_toggle_btn):
                primary.addWidget(widget)
            return

        for widget in transport:
            primary.addWidget(widget)
        primary.addWidget(self._info_container, 1)

        # 중간·좁은 폭 모두 첫 줄은 재생과 파일 정보, 둘째 줄은 모니터링
        # 컨트롤로 역할을 나눈다. SPD와 VOL은 항상 같은 행에 유지한다.
        secondary.addWidget(self.binaural_control)
        secondary.addWidget(speed)
        secondary.addWidget(volume)
        secondary.addWidget(self.seg_toggle_btn)

    def _apply_info_layout(self):
        if not hasattr(self, "_info_vbox"):
            return
        narrow = bool(getattr(self, "_info_narrow", False))
        # 이전 레이아웃에서 메타가 배치돼 있더라도 제거한다. 값은 유지해서
        # 파일명 툴팁으로 확인할 수 있지만 재생 바의 공간은 차지하지 않는다.
        self._info_top.removeWidget(self.meta_label)
        self._info_vbox.removeWidget(self.meta_label)
        self.meta_label.setVisible(False)
        self.now_playing_prefix.setVisible(
            bool(self.now_playing_prefix.text()) and not narrow)

    def _on_volume_changed(self, value: int):
        self.player.setVolume(_vol_pos_to_gain(value))
        self.vol_value_label.setText(_fmt_db(value))

    def _on_binaural_toggled(self, checked: bool):
        if checked and not self._binaural_runtime_available:
            self.binaural_btn.blockSignals(True)
            self.binaural_btn.setChecked(False)
            self.binaural_btn.blockSignals(False)
            self.player.setBinauralEnabled(False)
            self._binaural_visual_state = "error"
            self._binaural_status_text = (
                self._binaural_unavailable_reason
                or "바이노럴 처리 구성을 사용할 수 없습니다")
            if is_iem_install_issue(self._binaural_status_text):
                self._show_iem_install_popup()
            self._sync_binaural_policy()
            self._update_output_control()
            return
        self._binaural_visual_state = "loading" if checked else "off"
        self._binaural_status_text = (
            "바이노럴 준비 중" if checked else "바이노럴 모니터링 꺼짐")
        self.player.setBinauralEnabled(checked)
        self._update_output_control()

    def _on_binaural_status(self, status: str):
        self._binaural_status_text = status
        if "재생 중" in status:
            self._binaural_visual_state = "active"
        elif "준비 중" in status:
            self._binaural_visual_state = "loading"
        elif "사용자가 바이노럴을 껐습니다" in status:
            self._binaural_visual_state = "off"
        elif "채널 배치 선택 필요" in status:
            self._binaural_visual_state = "needs_layout"
            QTimer.singleShot(
                0, lambda p=self._path, s=status: self._show_layout_required_popup(p, s))
        elif ("현재 파일은 기존 재생" in status
              or "배속 재생은 기존 방식" in status
              or "현재 파일은 변환 없이 재생" in status):
            self._binaural_visual_state = "bypass"
        elif "기존 재생" in status:
            self._binaural_visual_state = "error"
        elif "꺼짐" in status:
            self._binaural_visual_state = "off"
        else:
            self._binaural_visual_state = "off"
        self.binaural_btn.setToolTip(status)
        # 폴백 상태 문자열은 엔진 전환 직전에 오므로 다음 이벤트 루프에서 읽는다.
        QTimer.singleShot(0, self._update_output_control)

    def _on_runtime_binaural_layout(self, layout):
        self._runtime_binaural_layout = layout
        self._update_binaural_layout_control()

    def _on_binaural_availability(self, available: bool, reason: str):
        self._binaural_runtime_available = bool(available)
        self._binaural_unavailable_reason = "" if available else reason
        self._binaural_status_text = "" if available else reason
        if available:
            self._sync_binaural_policy()
            self._update_output_control()
            return
        self._binaural_visual_state = "error"
        QTimer.singleShot(0, lambda: self._force_binaural_off("error"))

    def _show_iem_install_popup(self):
        if self._iem_install_prompt_box is not None:
            self._iem_install_prompt_box.raise_()
            self._iem_install_prompt_box.activateWindow()
            return
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("IEM 플러그인 설치 필요")
        message.setText(
            self._binaural_unavailable_reason
            or "IEM MultiEncoder와 BinauralDecoder를 찾지 못했습니다.")
        message.setInformativeText(
            "MultiEncoder와 BinauralDecoder는 무료·오픈소스 "
            "IEM Plug-in Suite에 함께 포함되어 있습니다.\n\n"
            f"검증 버전: IEM Plug-in Suite v{IEM_SUITE_VERSION}\n"
            "설치를 마친 뒤 SoundField를 다시 실행하세요."
        )
        install_button = message.addButton(
            "무료 설치 페이지 열기", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("닫기", QMessageBox.ButtonRole.RejectRole)
        message.finished.connect(
            lambda _result, box=message, button=install_button:
            self._on_iem_install_prompt_finished(box, button))
        self._iem_install_prompt_box = message
        message.setModal(False)
        message.show()

    def _on_iem_install_prompt_finished(self, box: QMessageBox, install_button):
        clicked = box.clickedButton()
        if self._iem_install_prompt_box is box:
            self._iem_install_prompt_box = None
        if clicked is install_button:
            QDesktopServices.openUrl(QUrl(IEM_SUITE_DOWNLOAD_URL))
        box.deleteLater()

    def _force_binaural_off(self, final_state: str = "off"):
        if self.binaural_btn.isChecked():
            self.binaural_btn.blockSignals(True)
            self.binaural_btn.setChecked(False)
            self.binaural_btn.blockSignals(False)
            self.player.setBinauralEnabled(False)
        self._binaural_visual_state = final_state
        self._sync_binaural_policy()
        self._update_output_control()

    def _candidate_layouts(self) -> set[str]:
        if not self._path:
            return set()
        layout = resolve_layout(
            self._path, self._current_channel_count(), "", 0,
            inspect_file=False)
        return set(layout.candidates)

    def _build_binaural_layout_menu(self) -> QMenu:
        try:
            channels = int(self._current_meta.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        stored = self._layout_overrides.get(self._path, channels)
        preview = (self._layout_preview_preset
                   if self._layout_preview_path == self._path else "")
        current = stored or preview
        candidates = self._candidate_layouts()
        automatic = resolve_layout(
            self._path, channels, "", 0, inspect_file=False)
        selected_preset = split_layout_override(current)[0] or (
            automatic.preset if automatic.can_auto_play else "")
        speaker_presets = (
            ("lcr", "3채널 좌·중·우", 3),
            ("quad", "4채널 쿼드", 4),
            ("5.0", "5.0", 5),
            ("5.1", "5.1", 6),
            ("6.1", "6.1", 7),
            ("7.0", "7.0", 7),
            ("7.1", "7.1", 8),
            ("7.0.2", "7.0.2", 9),
        )
        ambisonic_presets = (
            ("ambix", "AmbiX", "square"),
            ("fuma", "FuMa", 4),
        )
        menu = QMenu(self)
        if not self._path:
            empty_action = QAction("파일을 먼저 선택하세요", menu)
            empty_action.setEnabled(False)
            menu.addAction(empty_action)
            return menu
        if candidates and not current and not automatic.can_auto_play:
            guide_text = ("AmbiX와 FuMa를 번갈아 듣고 선택하세요"
                          if candidates == {"ambix", "fuma"}
                          else "가능성이 높은 형식 중 하나를 선택하세요")
            guide = QAction(guide_text, menu)
            guide.setEnabled(False)
            menu.addAction(guide)

        if automatic.can_auto_play:
            automatic_result = self._layout_choice_label(
                automatic.preset, channels)
        elif candidates:
            automatic_result = "판정 보류"
        else:
            automatic_result = "지원 배치 없음"
        automatic_enabled = bool(current) or automatic.can_auto_play
        automatic_action = QWidgetAction(menu)
        automatic_action.setData("")
        automatic_action.setEnabled(automatic_enabled)
        automatic_row = _AutomaticLayoutMenuRow(
            automatic_result, not current, automatic_enabled, menu)
        automatic_row.clicked.connect(
            lambda: (self._apply_binaural_layout(""), menu.close()))
        automatic_action.setDefaultWidget(automatic_row)
        menu.addAction(automatic_action)
        menu.addSeparator()

        speaker_menu = menu.addMenu("스피커 채널 배치")
        ambisonic_menu = menu.addMenu("앰비소닉 규격")

        def add_presets(target: QMenu, presets):
            order = round(math.sqrt(channels)) - 1 if channels > 0 else -1
            for preset, base_label, required in presets:
                label = base_label
                if preset == "ambix" and order >= 0 and (order + 1) ** 2 == channels:
                    label = f"{order}차 AmbiX"
                elif preset == "fuma":
                    label = "1차 FuMa"
                if preset in candidates:
                    label = f"추천 · {label}"
                if selected_preset == preset:
                    label = f"{label} - 선택됨"
                action = QAction(label, target)
                action.setData(preset)
                action.setCheckable(True)
                action.setChecked(selected_preset == preset)
                if required == "square":
                    action.setEnabled(
                        order >= 0 and (order + 1) ** 2 == channels)
                else:
                    action.setEnabled(channels == required)
                if preset in candidates or selected_preset == preset:
                    candidate_font = QFont(action.font())
                    candidate_font.setBold(True)
                    action.setFont(candidate_font)
                action.triggered.connect(
                    lambda _checked=False, value=preset:
                    self._apply_binaural_layout(value))
                target.addAction(action)

        add_presets(speaker_menu, speaker_presets)
        add_presets(ambisonic_menu, ambisonic_presets)
        menu.addSeparator()
        comparison = QAction("채널 배치 비교해서 듣기", menu)
        comparison.setEnabled(selected_preset in {
            "lcr", "5.0", "5.1", "7.0", "7.1", "7.0.2"})
        comparison.triggered.connect(self._show_channel_order_comparison)
        menu.addAction(comparison)
        if current:
            folder_action = QAction(
                f"현재 폴더의 {channels}채널 파일에 적용", menu)
            folder_action.triggered.connect(
                lambda _checked=False, value=current:
                self._apply_layout_to_current_folder(value))
            menu.addAction(folder_action)
        return menu

    def _binaural_layout_menu_position(self, menu: QMenu) -> QPoint:
        menu.adjustSize()
        # 재생 바가 화면 아래쪽에 있으므로 메뉴의 아래쪽을 버튼 위쪽에 맞춘다.
        # 버튼의 오른쪽 모서리를 기준으로 정렬해 긴 메뉴가 화면 밖으로 밀리지 않는다.
        anchor = self.binaural_menu_btn.mapToGlobal(
            self.binaural_menu_btn.rect().topRight())
        size = menu.sizeHint()
        return QPoint(anchor.x() - size.width() + 1,
                      anchor.y() - size.height())

    def _show_binaural_layout_menu(self):
        menu = self._build_binaural_layout_menu()
        menu.exec(self._binaural_layout_menu_position(menu))

    def _show_channel_order_comparison(self):
        if not self._path:
            return
        channels = self._current_channel_count()
        resolved = self._resolved_binaural_layout()
        if resolved.topology != "surround" or resolved.preset not in {
                "lcr", "5.0", "5.1", "7.0", "7.1", "7.0.2"}:
            return
        stored = self._layout_overrides.get(self._path, channels)
        _, stored_order = split_layout_override(stored)
        runtime_order = str(
            getattr(self._runtime_binaural_layout, "channel_order", "") or "")
        current_order = stored_order or (
            runtime_order if runtime_order in {"wave", "film"} else "wave")
        if self._layout_prompt_box is not None:
            self._layout_prompt_box.close()
        dialog = _ChannelOrderDialog(
            Path(self._path).name, resolved.preset, current_order, self)
        dialog.previewRequested.connect(self._preview_binaural_layout)
        dialog.saveRequested.connect(
            lambda value, box=dialog:
            self._save_ambisonic_preview(box, value))
        dialog.finished.connect(
            lambda _result, box=dialog:
            self._on_ambisonic_prompt_finished(box))
        self._layout_prompt_box = dialog
        dialog.show()

    def _apply_layout_to_current_folder(self, preset: str):
        if not self._path or not preset:
            return
        channels = self._current_channel_count()
        self._layout_overrides.set_folder(self._path, channels, preset)
        self._layout_overrides.set(self._path, "")
        self._current_meta["binaural_layout"] = preset
        self.player.setMediaMetadata(self._current_meta)
        self._runtime_binaural_layout = None
        self._update_binaural_layout_control()
        self._sync_binaural_policy()
        self._update_output_control()
        if self.binaural_btn.isChecked():
            self._reload_current_layout()

    def _show_layout_required_popup(self, path: str, status: str):
        if not path or path != self._path or path in self._layout_prompted_paths:
            return
        channels = self._current_channel_count()
        candidates = self._candidate_layouts()
        if not candidates:
            return
        self._layout_prompted_paths.add(path)
        self._layout_required_paths.add(path)

        reason = status.split("채널 배치 선택 필요:", 1)[-1].strip()
        if candidates == {"ambix", "fuma"}:
            dialog = _AmbisonicFormatDialog(Path(path).name, reason, self)
            dialog.previewRequested.connect(self._preview_binaural_layout)
            dialog.saveRequested.connect(
                lambda preset, box=dialog:
                self._save_ambisonic_preview(box, preset))
            dialog.finished.connect(
                lambda _result, box=dialog:
                self._on_ambisonic_prompt_finished(box))
            self._layout_prompt_box = dialog
            dialog.show()
            return

        if channels == 9:
            choices = (
                "• 7.0.2: 일곱 개 평면 스피커와 두 개 높이 채널로 제작됐을 때 선택\n"
                "• 2차 AmbiX: ACN/SN3D 9채널 앰비소닉 파일일 때 선택"
            )
        else:
            labels = [self._layout_choice_label(preset, channels)
                      for preset in sorted(candidates)]
            choices = "\n".join(f"• {label}" for label in labels)
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("채널 배치 확인 필요")
        if "A-format" in reason:
            message.setText(
                f"{Path(path).name}\n\n"
                f"{reason}\n"
                "AMBEO 마이크 원본은 곧바로 바이노럴로 변환할 수 없습니다.\n"
                "현재는 일반 스테레오로 재생하며 바이노럴 설정은 켜진 상태로 유지됩니다."
            )
            message.setInformativeText(
                "제작 과정에서 이미 AmbiX 또는 FuMa B-format으로 변환된 파일인지 먼저 "
                "확인하세요. 변환된 파일이 확실할 때만 해당 규격을 선택해 들어보세요.\n\n"
                f"확인 가능한 선택지\n{choices}\n\n"
                "원본 A-format이라면 이 메뉴에서 임의로 배치를 지정하지 않는 것이 안전합니다."
            )
        else:
            message.setText(
                f"{Path(path).name}\n\n"
                f"{reason}\n"
                "파일 내부 채널 정보와 파일명만으로 배치를 확정하지 못했습니다.\n"
                "현재는 일반 스테레오로 재생하며 바이노럴 설정은 켜진 상태로 유지됩니다."
            )
            message.setInformativeText(
                "가능성이 높은 배치를 하나씩 선택해 들어보세요.\n\n"
                f"{choices}\n\n"
                "소리가 스피커별로 분리된 일반 멀티채널이면 스피커 배치를, "
                "앰비소닉 B-format이면 AmbiX를 선택하세요.\n"
                "둘 다 자연스럽지 않으면 임의로 확정하지 말고 스테레오 상태를 유지한 뒤 "
                "원본 제작 정보와 채널 규약을 확인하세요."
            )
        select_button = message.addButton(
            "채널 배치 선택", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        message.finished.connect(
            lambda _result, box=message, button=select_button:
            self._on_layout_prompt_finished(box, button))
        self._layout_prompt_box = message
        message.setModal(False)
        message.show()

    @staticmethod
    def _layout_choice_label(preset: str, channels: int) -> str:
        preset = split_layout_override(preset)[0]
        if preset == "quad":
            return "4채널 쿼드"
        if preset == "ambix":
            order = round(math.sqrt(channels)) - 1 if channels > 0 else -1
            return f"{order}차 AmbiX" if order >= 0 else "AmbiX"
        if preset == "fuma":
            return "1차 FuMa"
        return {
            "lcr": "3채널 좌·중·우",
            "5.0": "5.0",
            "5.1": "5.1",
            "6.1": "6.1",
            "7.0": "7.0",
            "7.1": "7.1",
            "7.0.2": "7.0.2",
        }.get(preset, preset)

    def _set_layout_warning_visible(self, visible: bool):
        visible = bool(visible)
        channels = self._current_channel_count()
        override = self._layout_overrides.get(
            self._path, channels) if self._path else ""
        scope = self._layout_overrides.scope(
            self._path, channels) if self._path else ""
        format_candidates = self._candidate_layouts() == {"ambix", "fuma"}
        show_saved = bool(
            not visible and override
        )
        show_detail = visible or show_saved
        if visible:
            self.binaural_layout_warning.setText(
                "앰비소닉 규격 확인 필요" if format_candidates
                else "채널 배치 확인 필요")
            self.binaural_layout_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.binaural_layout_warning.setStyleSheet(
                "color: #e45b64; font-size: 8pt; font-weight: 700;")
            self.binaural_layout_warning.setToolTip(
                "오른쪽 화살표를 눌러 AmbiX와 FuMa를 번갈아 들어보세요"
                if format_candidates else
                "오른쪽 화살표를 눌러 가능한 채널 배치 중 하나를 선택하세요")
        elif show_saved:
            label = self._layout_choice_label(override, self._current_channel_count())
            saved_label = "폴더 설정" if scope == "folder" else "개별 저장됨"
            self.binaural_layout_warning.setText(f"{saved_label} · {label}")
            self.binaural_layout_warning.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.binaural_layout_warning.setStyleSheet(
                "color: #9fc4ff; font-size: 8pt; font-weight: 700;")
            self.binaural_layout_warning.setToolTip(
                "현재 폴더에 저장한 채널 배치입니다" if scope == "folder" else
                "자동 판정 결과를 덮어써 이 파일에 직접 저장한 채널 배치입니다")
        self.binaural_layout_detail.setVisible(show_detail)
        self.binaural_layout_warning.setVisible(show_detail)
        self.binaural_layout_reset_btn.setVisible(show_saved)
        # 경고가 처음에는 숨겨져 있어 부모 HBox가 26px로 굳는 것을 방지한다.
        # 표시 중에는 버튼 22 + 간격 1 + 경고 13의 실제 높이를 확보한다.
        height = 36 if show_detail else 22
        self.binaural_control.setMinimumHeight(height)
        self.binaural_control.setMaximumHeight(height)
        self.binaural_control.updateGeometry()

    def _on_layout_prompt_finished(self, box: QMessageBox, select_button):
        clicked = box.clickedButton()
        if self._layout_prompt_box is box:
            self._layout_prompt_box = None
        box.deleteLater()
        if clicked is select_button and self._path:
            QTimer.singleShot(0, self._show_binaural_layout_menu)

    def _preview_binaural_layout(self, preset: str):
        value, order = split_layout_override(preset)
        valid = value in {"ambix", "fuma"} or (
            value in {"lcr", "5.0", "5.1", "7.0", "7.1", "7.0.2"}
            and order in {"wave", "film"})
        if not self._path or not valid:
            return
        self._layout_preview_path = self._path
        self._layout_preview_preset = preset
        self._runtime_binaural_layout = None
        self._current_meta["binaural_layout"] = preset
        self.player.setMediaMetadata(self._current_meta)
        self._binaural_visual_state = (
            "loading" if self.binaural_btn.isChecked() else "off")
        self._update_binaural_layout_control()
        self._sync_binaural_policy()
        self._update_output_control()
        if self.binaural_btn.isChecked():
            self._reload_current_layout(restart=True, force_play=True)

    def _save_ambisonic_preview(self, box: _AmbisonicFormatDialog,
                                preset: str):
        if box is not self._layout_prompt_box or self._path != self._layout_preview_path:
            return
        box.setProperty("layoutSaved", True)
        self._apply_binaural_layout(preset, close_prompt=False)
        box.accept()

    def _on_ambisonic_prompt_finished(self, box: _AmbisonicFormatDialog):
        saved = bool(box.property("layoutSaved"))
        if self._layout_prompt_box is box:
            self._layout_prompt_box = None
        box.deleteLater()
        if not saved:
            self._cancel_layout_preview()

    def _cancel_layout_preview(self):
        path = self._layout_preview_path
        if not path:
            return
        self._layout_preview_path = ""
        self._layout_preview_preset = ""
        self._runtime_binaural_layout = None
        if path != self._path:
            return
        stored = self._layout_overrides.get(path, self._current_channel_count())
        if stored:
            self._current_meta["binaural_layout"] = stored
        else:
            self._current_meta.pop("binaural_layout", None)
        self.player.setMediaMetadata(self._current_meta)
        self._binaural_visual_state = (
            "needs_layout" if self.binaural_btn.isChecked() else "off")
        self._update_binaural_layout_control()
        self._sync_binaural_policy()
        self._update_output_control()
        if self.binaural_btn.isChecked():
            self._reload_current_layout()

    def _reload_current_layout(self, *, restart: bool = False,
                               force_play: bool = False):
        position = 0 if restart else self.player.position()
        was_playing = force_play or (
            self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState)
        if restart:
            self._last_known_pos_ms = 0
            self._last_known_pos_t = time.monotonic()
            self._playhead_smoothing_ready = False
            self._display_pos_ms = 0.0
            self.wave.set_position_ratio(0)
        if self.player.reloadBinauralLayout(position, was_playing):
            return
        self.player.setSource(QUrl.fromLocalFile(self._path))
        self.player.setPosition(position)
        if was_playing:
            self.player.play()

    def _apply_binaural_layout(self, preset: str, *, close_prompt: bool = True):
        if not self._path:
            return
        if preset and close_prompt and self._layout_prompt_box is not None:
            self._layout_prompt_box.close()
        prompt_choice = bool(
            preset and (
                self._path in self._layout_required_paths
                or self._layout_overrides.is_prompt_choice(self._path)
            )
        )
        if not preset and self._layout_overrides.scope(
                self._path, self._current_channel_count()) == "folder":
            self._layout_overrides.set_folder(
                self._path, self._current_channel_count(), "")
        else:
            self._layout_overrides.set(
                self._path, preset, prompt_choice=prompt_choice)
        self._layout_preview_path = ""
        self._layout_preview_preset = ""
        self._runtime_binaural_layout = None
        if preset:
            self._current_meta["binaural_layout"] = preset
        else:
            self._current_meta.pop("binaural_layout", None)
        self.player.setMediaMetadata(self._current_meta)
        if self.binaural_btn.isChecked():
            self._binaural_visual_state = "loading"
        elif preset:
            self._binaural_visual_state = "off"
        self._update_binaural_layout_control()
        self._sync_binaural_policy()
        self._update_output_control()
        if not self.binaural_btn.isChecked():
            return
        self._reload_current_layout()

    def _reset_saved_binaural_layout(self):
        channels = self._current_channel_count()
        if not self._path or not self._layout_overrides.get(self._path, channels):
            return
        path = self._path
        if self._layout_overrides.scope(path, channels) == "folder":
            self._layout_overrides.set_folder(path, channels, "")
        else:
            self._layout_overrides.set(path, "")
        self._current_meta.pop("binaural_layout", None)
        self.player.setMediaMetadata(self._current_meta)
        self._layout_prompted_paths.discard(path)
        self._layout_required_paths.discard(path)
        self._layout_preview_path = ""
        self._layout_preview_preset = ""
        self._binaural_visual_state = (
            "needs_layout" if self.binaural_btn.isChecked() else "off")
        self._update_binaural_layout_control()
        self._sync_binaural_policy()
        self._update_output_control()
        if not self.binaural_btn.isChecked():
            return
        self._reload_current_layout()

    def _on_speed_changed(self, value: int):
        rate = round(2.0 ** (value / 100.0), 2)   # log2 매핑 → 0.01 단위로 반올림
        self._playback_rate = rate
        self.player.setPlaybackRate(rate)
        self.speed_value_label.setText(self._fmt_speed(rate))
        # 배속이 걸려있는 상태(1.0x 아님)를 주황 텍스트로 표시
        self.speed_value_label.setStyleSheet(
            "" if abs(rate - 1.0) < 0.005 else "color: #f28c28;")
        self._sync_binaural_policy()
        QTimer.singleShot(0, self._update_output_control)

    @staticmethod
    def _fmt_speed(rate: float) -> str:
        # 둘째 자리가 0이면 한 자리만 (1.20x→1.2x), 아니면 두 자리 (1.23x).
        return f"{rate:.1f}x" if round(rate * 100) % 10 == 0 else f"{rate:.2f}x"

    def _on_speed_text_input(self, text: str):
        try:
            v = max(0.5, min(2.0, float(text.rstrip("x").strip())))
            self.speed_slider.setValue(round(100 * math.log2(v)))
        except ValueError:
            pass

    def _on_volume_text_input(self, text: str):
        # dB 직접 입력 → 슬라이더 위치로 역변환. "-inf"/"−∞"/"mute"/"음소거" = 음소거(0).
        s = text.strip().lower().replace("db", "").replace("−", "-").strip()
        if s in ("-inf", "-infinity", "inf", "mute", "음소거", "-∞", "∞", ""):
            if s == "":
                return
            self.vol_slider.setValue(0)
            return
        try:
            db = float(s.lstrip("+"))
        except ValueError:
            return
        self.vol_slider.setValue(_vol_db_to_pos(db))

    def _restart_from_zero(self):
        restart = getattr(self.player, "restart", None)
        if callable(restart):
            restart()
        else:
            self.player.setPosition(0)
            self.player.play()
        self._last_known_pos_ms = 0
        self._last_known_pos_t = time.monotonic()
        self._playhead_smoothing_ready = False
        self._display_pos_ms = 0.0
        self.wave.set_position_ratio(0)

    # ─────────── 히스토리 ───────────
    def _load_history(self):
        try:
            if HISTORY_FILE.exists():
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for p in data[-HISTORY_MAX:]:
                        if isinstance(p, str):
                            self._history.append(p)
        except Exception:
            pass

    def _save_history(self):
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(
                json.dumps(list(self._history), ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception:
            pass

    def _record_history(self, path: str):
        if not path:
            return
        items = [p for p in self._history if p != path]
        items.append(path)
        self._history = deque(items[-HISTORY_MAX:], maxlen=HISTORY_MAX)
        self._save_history()
        self.historyChanged.emit()

    def history(self) -> List[str]:
        """오래된 → 최신 순."""
        return list(self._history)

    def clear_history(self):
        self._history.clear()
        self._save_history()
        self.historyChanged.emit()

    def remove_history_under(self, prefixes: List[str]) -> int:
        """prefix 이하 경로의 히스토리 항목 일괄 제거 — 라이브러리 완전 제거용.
        반환: 제거된 항목 수."""
        lows = [p.lower() for p in prefixes if p]
        if not lows:
            return 0
        kept = [p for p in self._history
                if not any(p.lower().startswith(l) for l in lows)]
        n = len(self._history) - len(kept)
        if n:
            self._history = deque(kept, maxlen=HISTORY_MAX)
            self._save_history()
            self.historyChanged.emit()
        return n

    def _resolve_export_path(self) -> Optional[str]:
        """우선순위: 1) 세그먼트 헤더 드래그 범위 2) 사용자 선택 영역 3) 원본 전체.
        배속이 1.0x 가 아니면 위 결과에 버리스피드를 렌더링한 임시 WAV
        (`이름_0.45x.wav`)로 내보냄 — 누엔도가 1.0x 원본과 딴 파일로 인식."""
        if not self._path:
            return None
        export = self._path
        seg = self.wave.pending_drag_range()
        rng = seg if seg is not None else self.wave.selection_ratios()
        if rng is not None:
            # 플레이어 duration 미수신(로드 직후 등)이면 파형 위젯이 디코딩한 길이로 폴백
            dur_ms = self._dur_ms if self._dur_ms > 0 else self.wave._duration_ms
            if dur_ms <= 0:
                logger.warning("[region] duration 미확정 — 영역 무시, 원본 전체 드래그: %s",
                               os.path.basename(self._path))
            else:
                start_sec = rng[0] * dur_ms / 1000.0
                end_sec = rng[1] * dur_ms / 1000.0
                cropped = crop_wav_region(self._path, start_sec, end_sec)
                if not cropped:
                    logger.warning("[region] crop 실패 — 원본 전체 드래그: %s (%.3f~%.3fs)",
                                   os.path.basename(self._path), start_sec, end_sec)
                export = cropped or self._path
        rate = self._playback_rate
        if abs(rate - 1.0) >= 0.005:
            rendered = render_speed_wav(export, rate)
            if rendered:
                return rendered
            logger.warning("[speed] 배속 렌더 실패 — 1.0x 로 드래그: %s (rate=%.2f)",
                           os.path.basename(export), rate)
        return export

    @staticmethod
    def _make_drag_pixmap(label: str) -> QPixmap:
        """drag 시 커서 옆에 따라다닐 라벨 — 어떤 파일이 끌려가는지 명시."""
        max_label = 40
        if len(label) > max_label:
            label = label[:max_label - 1] + "…"
        pm = QPixmap(260, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(40, 50, 65, 230))
        p.setPen(QPen(QColor(120, 200, 255), 1))
        p.drawRoundedRect(0, 0, 260, 32, 6, 6)
        p.setPen(QColor(230, 240, 255))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, label)
        p.end()
        return pm

    def _on_drag_region(self):
        """파형 영역 내부 드래그 시작 — crop된 임시파일로 외부 drag&drop.
        드롭 완료 시 config 기준으로 재생 자동 정지 (시각 정보는 보존)."""
        export_path = self._resolve_export_path()
        if not export_path:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(export_path)])
        drag = QDrag(self.wave)
        drag.setMimeData(mime)
        drag.setPixmap(self._make_drag_pixmap(os.path.basename(export_path)))
        drag.setHotSpot(QPoint(20, 16))
        result = drag.exec(Qt.DropAction.CopyAction)
        # 드롭이 성공적으로 완료됐고 stop_on_drag 설정이 켜져있으면 재생 정지
        if result != Qt.DropAction.IgnoreAction and self._config.get('stop_on_drag', True):
            self.stop_playback_only()

    # ─────────── 재생 ───────────
    def set_meta(self, channels: int = 0, sample_rate: int = 0, bit_depth: int = 0):
        """파일 메타 라벨 갱신 (채널 · 샘플레이트 · 비트뎁스)."""
        parts = []
        if channels:
            parts.append(f"{channels}ch")
        if sample_rate:
            parts.append(f"{sample_rate / 1000:.1f}k".replace(".0k", "k"))
        if bit_depth:
            parts.append(f"{bit_depth}b")
        self.meta_label.setText(" · ".join(parts))
        self._refresh_info_tooltip()

    def _current_channel_count(self) -> int:
        try:
            return max(0, int(self._current_meta.get("channels") or 0))
        except (TypeError, ValueError):
            return 0

    def _resolved_binaural_layout(self):
        channels = self._current_channel_count()
        if (self._runtime_binaural_layout is not None
                and self._layout_preview_path != self._path):
            return self._runtime_binaural_layout
        override = self._layout_overrides.get(
            self._path, channels) if self._path else ""
        if (not override and self._layout_preview_path == self._path
                and self._layout_preview_preset):
            override = self._layout_preview_preset
        try:
            mask = self._current_meta.get("channel_mask")
            # 이 메서드는 UI 갱신 중 호출된다. 마스크가 없을 때 None을 넘기면
            # resolve_layout()이 원격 WAV를 직접 열기 때문에 파일 선택이 멈춘다.
            # UI에서는 파일명/채널 수로만 즉시 미리 판정하고, 실제 재생 워커가
            # 별도로 WAV 헤더를 읽어 최종 배치를 검증한다.
            mask = int(mask) if mask not in (None, "") else 0
        except (TypeError, ValueError):
            mask = 0
        return resolve_layout(
            self._path, channels, override, mask, inspect_file=False)

    def _binaural_policy(self) -> tuple[bool, str]:
        channels = self._current_channel_count()
        if not self._binaural_runtime_available:
            return False, (self._binaural_unavailable_reason
                           or "바이노럴 처리 구성을 사용할 수 없습니다")
        if not self.binaural_btn.isChecked():
            return True, "바이노럴 모니터링 꺼짐 · 클릭하여 켭니다"
        if not self._path:
            return True, "바이노럴 모니터링 켜짐 · 멀티채널 파일을 기다리는 중입니다"
        if channels <= 2:
            return True, "바이노럴 모니터링 켜짐 · 모노·스테레오는 변환 없이 재생합니다"
        if Path(self._path).suffix.lower() != ".wav":
            return True, "바이노럴 모니터링 켜짐 · 현재 형식은 변환 없이 재생합니다"
        if abs(self._playback_rate - 1.0) >= 0.005:
            return True, "바이노럴 모니터링 켜짐 · 1.0배속이 아니어서 변환 없이 재생합니다"
        layout = self._resolved_binaural_layout()
        if not layout.can_auto_play:
            if set(layout.candidates) == {"ambix", "fuma"}:
                return True, "바이노럴 모니터링 켜짐 · 오른쪽 화살표에서 앰비소닉 규격을 선택하세요"
            return True, "바이노럴 모니터링 켜짐 · 오른쪽 화살표에서 채널 배치를 선택하세요"
        return True, "바이노럴 모니터링 켜짐 · 클릭하여 끕니다"

    def _sync_binaural_policy(self):
        eligible, reason = self._binaural_policy()
        install_action = (
            not self._binaural_runtime_available
            and is_iem_install_issue(self._binaural_unavailable_reason))
        if not eligible and self.binaural_btn.isChecked():
            self.binaural_btn.blockSignals(True)
            self.binaural_btn.setChecked(False)
            self.binaural_btn.blockSignals(False)
            self.player.setBinauralEnabled(False)
            if self._binaural_visual_state != "error":
                self._binaural_visual_state = "off"
        self.binaural_btn.setEnabled(eligible or install_action)
        if install_action:
            self.binaural_btn.setToolTip(
                "IEM 플러그인 설치 필요 · 클릭하여 무료 설치 페이지 안내 열기")
            return
        if (self._binaural_visual_state in {"loading", "active", "error"}
                and self._binaural_status_text):
            self.binaural_btn.setToolTip(self._binaural_status_text)
        else:
            self.binaural_btn.setToolTip(reason)

    def _update_output_control(self):
        channels = self._current_channel_count()
        resolved = self._resolved_binaural_layout() if channels > 2 else None
        if resolved is not None and resolved.preset != "unknown":
            source_format = self._layout_choice_label(resolved.preset, channels)
            source_format = {
                "4채널 쿼드": "4ch 쿼드",
                "3채널 좌·중·우": "3ch 좌·중·우",
            }.get(source_format, source_format)
        else:
            source_format = f"{channels}ch · 배치 확인 필요"
        if not self._path or channels < 1:
            text = "출력 대기"
            indicator = "off"
        elif channels == 1:
            text = "모노 1ch"
            indicator = "bypass" if self.binaural_btn.isChecked() else "off"
        elif channels == 2:
            text = "스테레오 2ch"
            indicator = "bypass" if self.binaural_btn.isChecked() else "off"
        elif (self.player.isBinauralActive()
              and self._binaural_visual_state == "loading"):
            # 준비 상태는 좌측 회전 인디케이터로 표시해 긴 문구가 잘리지 않게 한다.
            text = f"{source_format} → Binaural 2ch"
            indicator = "loading"
        elif (self.player.isBinauralActive()
              and self._binaural_visual_state == "active"):
            text = f"{source_format} → Binaural 2ch"
            indicator = "active"
        else:
            text = (source_format if resolved is None or resolved.preset == "unknown"
                    else f"{source_format} → 스테레오 2ch")
            if self._binaural_visual_state == "error":
                indicator = "error"
            elif (self._binaural_visual_state == "needs_layout"
                  or (self.binaural_btn.isChecked()
                      and not self._resolved_binaural_layout().can_auto_play)):
                indicator = "needs_layout"
            elif self.binaural_btn.isChecked():
                indicator = "bypass"
            else:
                indicator = "off"
        self.binaural_btn.setText(text)
        self.binaural_btn.set_status_indicator(indicator)
        self._set_layout_warning_visible(indicator == "needs_layout")

    def _update_binaural_layout_control(self):
        channels = self._current_channel_count()
        if not self._path or channels <= 2:
            self._set_layout_warning_visible(False)
            self.binaural_menu_btn.setEnabled(True)
            self.binaural_menu_btn.setToolTip(
                "바이노럴 입력 채널 배치 선택\n현재 파일에는 배치 설정이 필요하지 않습니다")
            return
        self.binaural_menu_btn.setEnabled(True)
        override = self._layout_overrides.get(self._path, channels)
        preview = (self._layout_preview_preset
                   if self._layout_preview_path == self._path else "")
        resolved = self._resolved_binaural_layout()
        mode = "직접" if override else "미리듣기" if preview else "자동"
        if resolved.topology == "ambisonic":
            convention = "FuMa" if resolved.preset == "fuma" else "AmbiX"
            preset = f"{resolved.source_order}차 {convention}"
        else:
            preset = resolved.preset if resolved.preset != "unknown" else "선택 필요"
        source_detail = {
            "ixml": "파일 내부 채널 정보로 자동 배치",
            "mask": "WAV 채널 정보로 자동 배치",
            "filename": "파일명 정보로 자동 배치",
            "channels": "채널 수의 기본 배치 사용",
            "manual": "사용자가 저장한 배치 사용",
        }.get(str(getattr(resolved, "source", "") or ""), "")
        detail = resolved.reason or source_detail or f"현재 판정: {preset}"
        self._set_layout_warning_visible(
            self._binaural_visual_state == "needs_layout" and not resolved.can_auto_play)
        self.binaural_menu_btn.setToolTip(
            f"채널 배치: {mode} · {preset}\n{detail}\n클릭하여 변경")

    def _refresh_info_tooltip(self):
        """잘린 상태에서 마우스 호버 시 풀 정보(파일명 + 채널·SR·비트)를 툴팁으로."""
        name = self.name_label.text()
        meta = self.meta_label.text()
        tip = f"{name}\n{meta}" if (name and meta) else (name or meta)
        for w in (self.now_playing_prefix, self.name_label, self.meta_label):
            w.setToolTip(tip)

    def _start_path_probe(self, path: str, gen: int):
        probe = PathProbeRunnable(path, gen)
        probe.signals.done.connect(self._on_path_probe_done)
        QThreadPool.globalInstance().start(probe)

    def _on_path_probe_done(self, path: str, gen: int, ok: bool):
        if gen != self._play_load_gen or path != self._path or ok:
            return
        logger.warning("[perf-play] path_probe_failed +%dms file=%s",
                       self._perf_ms(), os.path.basename(path))
        self.player.stop()
        self.playingChanged.emit("")

    def _watch_play_load(self, path: str, gen: int):
        if gen != self._play_load_gen or path != self._path:
            return
        status = self.player.mediaStatus()
        state = self.player.playbackState()
        if (state != QMediaPlayer.PlaybackState.PlayingState
                and status == QMediaPlayer.MediaStatus.LoadingMedia):
            logger.warning("[perf-play] load_watchdog_stop +%dms status=%s file=%s",
                           self._perf_ms(), getattr(status, "name", status), os.path.basename(path))
            self.player.stop()
            self.playingChanged.emit("")

    def _schedule_wave_load(self, path: str, size_hint: Optional[int] = None,
                            stat_hint: Optional[tuple] = None):
        self._wave_load_gen += 1
        gen = self._wave_load_gen
        # 작은(짧은) 파일은 디코딩이 빨라 재생과 NAS 경합이 미미 → 즉시 로드(지연 0).
        # 큰 파일만 지연을 둬 재생 시작 시 NAS 경합/underrun 을 피한다.
        # ⚠️ UI 스레드에서 os.stat 금지 — NAS 히컵 시 프리즈 (freeze.log 14:27 실측).
        # 크기는 ① 호출자가 준 DB 메타 ② 신선한 stat 캐시만 사용. 둘 다 없으면
        # '큰 파일'로 간주해 지연 로드 (stat 은 파형 디코딩 풀 스레드에서 어차피 수행).
        size = size_hint
        if size is None:
            hit = _stat_cache.get(path)
            if hit is not None and time.monotonic() - hit[0] < _STAT_TTL_SEC:
                size = hit[1]
        delay = 0 if (size is not None and size < WAVEFORM_IMMEDIATE_MAX_BYTES) else WAVEFORM_LOAD_DELAY_MS
        QTimer.singleShot(
            delay,
            lambda p=path, g=gen, s=stat_hint: self._run_scheduled_wave_load(p, g, s)
        )

    def _run_scheduled_wave_load(self, path: str, gen: int,
                                 stat_hint: Optional[tuple] = None):
        if gen != self._wave_load_gen or path != self._path:
            return
        self.wave.load(path, stat_hint=stat_hint)

    def load_and_play(self, path: str, meta: dict = None):
        if not path:
            return
        now_req = time.monotonic()
        # 동일 path 요청 디바운스 — 같은 클릭이 한 틱에 두 경로(셀렉션타이머+cellClicked)로
        # 들어오는 중복만 제거(50ms). 테이블이 _last_emitted_path 로 이미 클릭당 1회 dedup
        # 하므로 사실상 보조. 과거 0.75s startup-suppress 가드는 제거 — 재생 중 같은 파일
        # 빠른 재클릭을 무시해 restart 가 안 되고 자연 EOF→정지로 빠지는 원인이었음
        # (상용 툴처럼 클릭마다 즉시 처음부터 재시작).
        if (path == self._last_play_request_path
                and now_req - self._last_play_request_t < 0.05):
            logger.info("[perf-play] duplicate_request_ignored file=%s",
                        os.path.basename(path))
            return
        self._last_play_request_path = path
        self._last_play_request_t = now_req
        is_new_file = (path != self._path)
        was_playing_same = (not is_new_file
                            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

        # [perf-play] 기준 시점 — load_and_play 진입.
        # 모든 [perf-play] 로그는 이 t0 기준 상대 ms.
        self._perf_play_t0 = time.perf_counter()
        self._perf_play_first_pos_logged = False
        logger.info("[perf-play] enter +0ms file=%s new=%s",
                    os.path.basename(path), is_new_file)
        self._play_load_gen += 1
        play_gen = self._play_load_gen

        # ─── P0: 미디어 파이프라인 즉시 시작 — 사용자 인지 latency 최소화 ───
        # setSource/play 는 async 반환 (실제 graph 빌드는 Qt 내부 스레드).
        # UI 업데이트/wave 로드/disk write 는 모두 play() 호출 이후로 미룸.
        if is_new_file:
            # A/B 창의 finished 신호가 이전 파일을 재로드하지 않도록 먼저 미리듣기
            # 상태를 비운 뒤 창을 닫는다.
            self._layout_preview_path = ""
            self._layout_preview_preset = ""
            if self._layout_prompt_box is not None:
                self._layout_prompt_box.close()
            self._path = path
            self._start_path_probe(path, play_gen)
            self._dur_ms = 0
            self._last_known_pos_ms = 0
            self._last_known_pos_t = time.monotonic()
            self._playhead_smoothing_ready = False
            self._display_pos_ms = 0.0
            self.time_label.setText("0:00 / 0:00")
            self.wave.set_duration_ms(0)
            self.wave.set_position_ratio(0)
            self._current_meta = dict(meta or {})
            self._runtime_binaural_layout = None
            try:
                current_channels = int(self._current_meta.get("channels") or 0)
            except (TypeError, ValueError):
                current_channels = 0
            override = self._layout_overrides.get(path, current_channels)
            if override:
                self._current_meta["binaural_layout"] = override
            self.player.setMediaMetadata(self._current_meta)
            self._update_binaural_layout_control()
            self._sync_binaural_policy()
            self.player.setSource(QUrl.fromLocalFile(path))
            self._update_output_control()
            logger.info("[perf-play] setSource +%dms", self._perf_ms())
        elif was_playing_same:
            self._restart_from_zero()
        else:
            self.player.setPosition(0)
            self._last_known_pos_ms = 0
            self._last_known_pos_t = time.monotonic()
            self._playhead_smoothing_ready = False
            self._display_pos_ms = 0.0
        if not was_playing_same:
            self.player.play()
            logger.info("[perf-play] play() called +%dms", self._perf_ms())
        else:
            logger.info("[perf-play] restart called +%dms", self._perf_ms())
        QTimer.singleShot(
            PLAYBACK_LOAD_WATCHDOG_MS,
            lambda p=path, g=play_gen: self._watch_play_load(p, g)
        )
        QTimer.singleShot(80, lambda p=path, g=play_gen: self._sync_play_indicator(p, g))
        QTimer.singleShot(450, lambda p=path, g=play_gen: self._sync_play_indicator(p, g))

        # ─── P1: UI 업데이트 — play() 이미 호출됨, 사용자에겐 즉시 재생 시작 ───
        if is_new_file:
            self.now_playing_prefix.setText("현재 재생 중 :  ")
            self._apply_info_layout()   # 폭에 맞춰 캡션 표시/메타 줄배치 갱신
            self.name_label.setText(Path(path).name)
            if meta:
                self.set_meta(int(meta.get("channels") or 0),
                              int(meta.get("sample_rate") or 0),
                              int(meta.get("bit_depth") or 0))
            else:
                self.meta_label.setText("")
            self._refresh_info_tooltip()   # 메타 없는 경우 포함 — 호버 풀 정보 갱신
            # 이전 파형을 즉시 비우고 로딩 표시 — 디코딩은 _schedule_wave_load 가 지연 실행.
            self.wave.begin_loading(path)
            stat_hint = None
            try:
                size_hint = int(meta.get("file_size")) if meta and meta.get("file_size") else None
            except (TypeError, ValueError):
                size_hint = None
            if meta:
                stat_hint = _prime_stat_cache(
                    path, meta.get("file_size"), meta.get("modified_at")
                )
                if size_hint is None and stat_hint is not None:
                    size_hint = stat_hint[0]
            self._schedule_wave_load(path, size_hint=size_hint, stat_hint=stat_hint)
            # 히스토리 disk write — 이벤트 루프 다음 tick 으로 지연하여 UI 응답성 보호.
            QTimer.singleShot(0, lambda p=path: self._record_history(p))

        # 같은 파일을 이미 재생 중에 다시 호출하면 state 가 안 바뀌어 _on_state 가 안 불림 →
        # ResultsTable pip 이 loading 에서 못 빠져나오는 stuck 버그. 강제 emit 으로 동기화.
        if was_playing_same:
            self.playingChanged.emit(self._path)

    def _sync_play_indicator(self, path: str, gen: int):
        if gen != self._play_load_gen or path != self._path:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.playingChanged.emit(path)

    def stop(self):
        self._play_load_gen += 1
        self._wave_load_gen += 1
        self.player.stop()
        self.wave.clear()
        self.now_playing_prefix.setText("")
        self.name_label.setText("")
        self.meta_label.setText("")
        self._path = ""

    def stop_to_start(self):
        self.player.stop()
        self.player.setPosition(0)
        self._last_known_pos_ms = 0
        self._last_known_pos_t = time.monotonic()
        self._playhead_smoothing_ready = False
        self._display_pos_ms = 0.0
        self.wave.set_position_ratio(0)

    def toggle_for_path(self, path: str, meta: dict = None):
        # 재생 중이면 config 기준으로 정지(처음으로) vs 일시정지 선택
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if path and path != self._path:
                self.load_and_play(path, meta=meta)
                return
            if self._config.get('playback_restart_from_zero', True):
                self._restart_from_zero()
            else:
                self.player.pause()
            return
        # 미재생 — 새 파일이면 로드+재생
        if path and path != self._path:
            self.load_and_play(path, meta=meta)
            return
        # 같은 파일 — config 기준 위치 처리 후 재생
        if self._path:
            # restart_from_zero 는 _restart_from_zero 로 라우팅 — 직접 setPosition+play
            # 하면 _display_pos_ms/_playhead_smoothing_ready 가 직전 종료 위치(끝)에
            # 박혀 _on_tick 의 max() 가드 때문에 playhead 가 안 움직임(클릭 경로는
            # load_and_play 가 이 상태를 리셋해서 정상).
            if self._config.get('playback_restart_from_zero', True):
                self._restart_from_zero()
            else:
                self._last_known_pos_t = time.monotonic()
                self._playhead_smoothing_ready = False
                self._display_pos_ms = float(self.player.position())
                self.player.play()
            return
        # path 만 있고 _path 없음
        if path:
            self.load_and_play(path, meta=meta)

    def toggle_keyboard_for_path(self, path: str, meta: dict = None):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if path and path != self._path:
            self.load_and_play(path, meta=meta)
            return
        if self._path:
            if self._config.get('playback_restart_from_zero', True):
                self._restart_from_zero()
            else:
                self._last_known_pos_t = time.monotonic()
                self._playhead_smoothing_ready = False
                self._display_pos_ms = float(self.player.position())
                self.player.play()
            return
        if path:
            self.load_and_play(path, meta=meta)

    def _toggle(self):
        # 재생/일시정지 토글 — 위치 보존
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self._last_known_pos_t = time.monotonic()
            self.player.play()

    def _on_stop_clicked(self):
        # 정지 — 위치 0 + 정지
        self.player.stop()
        self.player.setPosition(0)
        self._last_known_pos_ms = 0
        self._last_known_pos_t = time.monotonic()
        self._playhead_smoothing_ready = False
        self._display_pos_ms = 0.0
        self.wave.set_position_ratio(0)

    def _on_seek_start_clicked(self):
        # 처음으로 — 위치만 0, 재생 상태 유지
        self.player.setPosition(0)
        self._last_known_pos_ms = 0
        self._last_known_pos_t = time.monotonic()
        self._playhead_smoothing_ready = False
        self._display_pos_ms = 0.0
        self.wave.set_position_ratio(0)

    def _on_seek(self, ratio: float):
        if self._dur_ms > 0:
            target = int(ratio * self._dur_ms)
            self.player.setPosition(target)
            self._last_known_pos_ms = target
            self._last_known_pos_t = time.monotonic()
            self._playhead_smoothing_ready = False
            self._display_pos_ms = float(target)

    # ─── PHASE-3A + 3C 복원 (PYZ bytecode 100% 일치) ─────
    def _on_loop_toggled(self, checked: bool):
        """반복 재생 토글 — 시각 신호(선택 영역 색)는 WaveformView 가 담당."""
        self._loop_enabled = checked
        self.wave.set_loop_active(checked)

    def _loop_range_ms(self) -> Tuple[int, int]:
        """현재 반복 재생 구간 (start_ms, end_ms).
        영역 선택 있으면 그 구간, 없으면 (0, 전체)."""
        sel = self.wave.selection_ratios()
        if sel is not None and self._dur_ms > 0:
            return (int(sel[0] * self._dur_ms), int(sel[1] * self._dur_ms))
        return (0, self._dur_ms)

    def _on_media_status(self, status):
        # [perf-play] 각 미디어 상태 변화 시점.
        if getattr(self, "_perf_play_t0", 0):
            try:
                name = status.name  # MediaStatus enum 의 short name
            except AttributeError:
                name = str(status)
            logger.info("[perf-play] media_status=%s +%dms", name, self._perf_ms())
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._play_load_gen += 1
            self._wave_load_gen += 1
            self.playingChanged.emit("")
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._loop_enabled:
            loop_start, _ = self._loop_range_ms()
            self.player.setPosition(loop_start)
            self._last_known_pos_ms = loop_start
            self._last_known_pos_t = time.monotonic()
            self._display_pos_ms = float(loop_start)
            self._playhead_smoothing_ready = False
            self.player.play()

    def _perf_ms(self) -> int:
        """[perf-play] t0 기준 경과 ms. t0 미설정이면 -1."""
        t0 = getattr(self, "_perf_play_t0", 0)
        return int((time.perf_counter() - t0) * 1000) if t0 else -1

    def _on_tick(self):
        if self._dur_ms <= 0:
            return
        real = self.player.position()
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        now = time.monotonic()

        # [perf-play] 첫 position > 0 — 실제 첫 오디오 buffer 가 device 로 나가는
        # 시점에 근접. PlayingState 보다 더 사용자 인지 latency 에 가까움.
        if (real > 0 and getattr(self, "_perf_play_t0", 0)
                and not getattr(self, "_perf_play_first_pos_logged", True)):
            logger.info("[perf-play] first_pos>0 (pos=%dms) +%dms", real, self._perf_ms())
            self._perf_play_first_pos_logged = True

        # 반복 재생 — loop_end 30ms 전부터 loop_start 로 되감기. 영역 선택 시는
        # 그 구간, 없으면 (0, duration) 전체 (전체 반복은 EndOfMedia 가 보조 처리).
        if self._loop_enabled and playing:
            loop_start, loop_end = self._loop_range_ms()
            if loop_end > loop_start and real >= loop_end - 30:
                self.player.setPosition(loop_start)
                real = loop_start
                self._last_known_pos_ms = loop_start
                self._last_known_pos_t = now
                self._display_pos_ms = float(loop_start)
                self._playhead_smoothing_ready = False

        if real != self._last_known_pos_ms:
            if real > self._last_known_pos_ms:
                self._playhead_smoothing_ready = True
            self._last_known_pos_ms = real
            self._last_known_pos_t = now
            target_f = float(real)
        elif playing and self._playhead_smoothing_ready:
            # int() 제거 — float 정밀도 유지로 sub-pixel rendering 효과 보장.
            target_f = min(
                real + (now - self._last_known_pos_t) * 1000.0 * self._playback_rate,
                float(self._dur_ms),
            )
        else:
            self._last_known_pos_t = now
            target_f = float(real)

        if playing:
            # 사이드카는 위치를 100ms마다 보고한다. 일반 엔진용 32ms 상한을
            # 그대로 적용하면 32ms 이동 후 약 68ms 정지하는 톱니형 렉이 생긴다.
            # 바이노럴 경로에서는 한 보고 주기를 충분히 덮도록 보간한다.
            lead_cap_ms = (125.0 if self.player.isBinauralActive()
                           else (12.0 if self._dur_ms < 1000 else 32.0))
            target_f = min(target_f, float(real) + lead_cap_ms, float(self._dur_ms))
            smoothed_f = max(self._display_pos_ms, target_f)
        else:
            smoothed_f = target_f
        self._display_pos_ms = smoothed_f

        self.wave.set_position_ratio(smoothed_f / self._dur_ms)
        # 텍스트는 초 단위라 1초에 1번만 실제 변경 — 변경 시에만 setText (Qt 부담 절감)
        new_time_text = f"{_fmt_ms(int(smoothed_f))} / {_fmt_ms(self._dur_ms)}"
        if new_time_text != self.time_label.text():
            self.time_label.setText(new_time_text)

    def _decoded_wave_duration_ms(self) -> int:
        sr = int(getattr(self.wave, "_sample_rate", 0) or 0)
        n = int(getattr(self.wave, "_n_samples", 0) or 0)
        if sr <= 0 or n <= 0:
            return 0
        return max(1, int(round(n * 1000.0 / sr)))

    def _apply_duration_ms(self, ms: int):
        ms = max(0, int(ms))
        self._dur_ms = ms
        self.wave.set_duration_ms(ms)
        self.time_label.setText(f"{_fmt_ms(self.player.position())} / {_fmt_ms(ms)}")

    def _on_wave_duration_decoded(self, ms: int):
        if ms > 0 and (self._dur_ms <= 0 or abs(ms - self._dur_ms) > 2):
            self._apply_duration_ms(ms)

    def _on_dur(self, ms: int):
        decoded_ms = self._decoded_wave_duration_ms()
        self._apply_duration_ms(decoded_ms if decoded_ms > 0 else ms)

    def toggle_segments(self):
        self.seg_toggle_btn.setChecked(not self.seg_toggle_btn.isChecked())

    # ─── PHASE-3K 복원: PYZ 실제 구현 (byte-exact) ────────────────────
    def seek_to_start(self):
        """재생 위치를 처음으로 이동 — 재생/일시정지/정지 무관."""
        self._on_seek_start_clicked()

    def set_config(self, config: dict):
        # dict.update — 기존 키 보존하면서 새 키 덮어쓰기
        self._config.update(config)

    def stop_playback_only(self):
        """재생만 정지 — 파형/파일명/메타/위치 모두 UI 에 그대로 유지.
        DAW 드래그 앤 드롭 완료 시 호출 (사운드 멈추되 시각 정보는 보존)."""
        self.player.stop()
        self._last_known_pos_ms = self.player.position()
        self._last_known_pos_t = time.monotonic()

    def shutdown_audio(self):
        close = getattr(self.player, "close", None)
        if callable(close):
            close()
        else:
            self.player.stop()

    def toggle_loop(self):
        """반복 재생 토글 — loop_btn 클릭과 동일."""
        self.loop_btn.toggle()
    # ─────────────────────────────────────────────────────────────────

    def _on_state(self, state):
        # [perf-play] PlayingState 진입 시점 = 사용자 인지 '재생 시작'.
        if getattr(self, "_perf_play_t0", 0):
            try:
                name = state.name
            except AttributeError:
                name = str(state)
            logger.info("[perf-play] state=%s +%dms", name, self._perf_ms())
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸" if playing else "▶")
        self.play_btn.set_active(playing)
        self.wave.set_playing(playing)
        self._last_known_pos_ms = self.player.position()
        self._last_known_pos_t = time.monotonic()
        if playing and self._last_known_pos_ms <= 0:
            self._playhead_smoothing_ready = False
        if playing:
            if not self._tick.isActive():
                self._tick.start()
        if (not playing) and self._tick.isActive():
            # 정지/일시정지 — 마지막 위치 한 번 더 그려두고 timer stop
            self._on_tick()
            self._tick.stop()
        # 외부 (ResultsTable) 에 playing path 전달
        self.playingChanged.emit(self._path if playing else "")
