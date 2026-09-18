"""파형 영역 → 임시 WAV crop.
임시 파일은 %TEMP%\\SoundField_regions\\ 에 저장, 24시간 후 자동 정리.

data 청크를 바이트 단위로 직접 잘라낸다 (wave 모듈 미사용) — Python wave 는
PCM 정수 포맷만 열 수 있어 32-bit float WAV(라이브러리에 흔함)에서 crop 이
조용히 실패하고 원본 전체가 드래그되던 버그의 원인이었음. 바이트 슬라이스는
포맷 무관(PCM/float/extensible)이고 샘플 무손실.
"""
import hashlib
import logging
import math
import os
import struct
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TEMP_DIR = Path(os.environ.get("TEMP") or "/tmp") / "SoundField_regions"
MAX_AGE_HOURS = 24
PRESERVE_CHUNKS = {b"bext", b"iXML", b"LIST", b"INFO", b"cue ", b"smpl", b"acid", b"inst", b"mark", b"cart"}
# 배속 렌더 시 보존할 청크 — 샘플 위치/템포 기반(cue/smpl/mark/acid/inst)은 배속 후 틀리므로 제외
SPEED_PRESERVE_CHUNKS = {b"bext", b"iXML", b"LIST", b"INFO", b"cart"}
# WAV 확장 헤더(fmt 태그). 이 헤더의 dwChannelMask 가 "몇 번 채널이 어느 스피커인지"를
# 담고, DAW(Pro Tools 등)는 이것을 읽어 다채널 파일을 올바른 채널에 놓는다.
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def crop_wav_region(src: str, start_sec: float, end_sec: float) -> Optional[str]:
    """[start_sec, end_sec] 구간을 잘라 임시 WAV로 저장 → 임시 파일 경로."""
    if not src or end_sec <= start_sec:
        return None
    if not src.lower().endswith(".wav"):
        return None
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = os.stat(src)
        fmt_chunk, chunks, data_off, data_size = _parse_riff(src)
        if len(fmt_chunk) < 16 or data_off <= 0 or data_size <= 0:
            logger.warning("[region] RIFF 파싱 실패 — crop 불가: %s", src)
            return None
        audio_format, _ch, sr, _byte_rate, block_align, _bits = struct.unpack(
            "<HHIIHH", fmt_chunk[:16]
        )
        if sr <= 0 or block_align <= 0:
            return None
        n_frames = data_size // block_align
        start_frame = max(0, round(start_sec * sr))
        end_frame = min(n_frames, round(end_sec * sr))
        if end_frame <= start_frame:
            return None
        with open(src, "rb") as f:
            f.seek(data_off + start_frame * block_align)
            frames = f.read((end_frame - start_frame) * block_align)

        after = os.stat(src)
        if len(frames) != (end_frame - start_frame) * block_align or (
                after.st_mtime_ns, after.st_size) != (stamp.st_mtime_ns, stamp.st_size):
            raise RuntimeError("구간 추출 도중 원본 파일이 변경되었거나 데이터가 부족합니다")
        base = os.path.splitext(os.path.basename(src))[0]
        key = hashlib.md5(
            f"{src}|{stamp.st_mtime_ns}|{stamp.st_size}|{start_frame}|{end_frame}".encode("utf-8")
        ).hexdigest()[:8]
        dest = TEMP_DIR / f"{base}_{int(start_sec*1000)}-{int(end_sec*1000)}ms_{key}.wav"
        # 비 PCM(float 등)은 fact 청크(프레임 수) 권장 — 새 길이로 재계산
        fact = (struct.pack("<I", end_frame - start_frame)
                if audio_format != 1 else None)
        _write_wav(str(dest), fmt_chunk, chunks, frames, fact)
        return str(dest)
    except Exception:
        logger.warning("[region] crop 실패: %s", src, exc_info=True)
        return None


def _parse_riff(src: str) -> Tuple[bytes, List[Tuple[bytes, bytes]], int, int]:
    """RIFF 1-pass 파싱 → (fmt 청크, 보존 청크들, data 시작 오프셋, data 크기)."""
    with open(src, "rb") as f:
        if f.read(4) != b"RIFF":
            return b"", [], 0, 0
        f.read(4)
        if f.read(4) != b"WAVE":
            return b"", [], 0, 0
        fmt_chunk = b""
        chunks: List[Tuple[bytes, bytes]] = []
        data_off = 0
        data_size = 0
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", header)
            if chunk_id == b"fmt ":
                fmt_chunk = f.read(size)
                if size % 2:
                    f.read(1)
            elif chunk_id == b"data":
                data_off = f.tell()
                data_size = size
                f.seek(size + size % 2, 1)
            elif chunk_id in PRESERVE_CHUNKS:
                data = f.read(size)
                if size % 2:
                    f.read(1)
                chunks.append((chunk_id, data))
            else:
                f.seek(size + size % 2, 1)
        return fmt_chunk, chunks, data_off, data_size


def _write_chunk(f, chunk_id: bytes, data: bytes):
    f.write(chunk_id)
    f.write(struct.pack("<I", len(data)))
    f.write(data)
    if len(data) % 2:
        f.write(b"\x00")


def _write_wav(dest: str, fmt_chunk: bytes, chunks: List[Tuple[bytes, bytes]],
               frames: bytes, fact: Optional[bytes] = None):
    """fmt(원본 그대로) + fact(비PCM) + 보존 청크 + data 로 새 WAV 작성."""
    all_chunks: List[Tuple[bytes, bytes]] = [(b"fmt ", fmt_chunk)]
    if fact is not None:
        all_chunks.append((b"fact", fact))
    all_chunks.extend(chunks)
    all_chunks.append((b"data", frames))
    riff_size = 4 + sum(8 + len(d) + len(d) % 2 for _cid, d in all_chunks)
    with open(dest, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", riff_size))
        f.write(b"WAVE")
        for chunk_id, data in all_chunks:
            _write_chunk(f, chunk_id, data)


def _speed_label(rate: float) -> str:
    # player_widget._fmt_speed 와 동일 규칙: 1.20→"1.2x", 1.25→"1.25x"
    return f"{rate:.1f}x" if round(rate * 100) % 10 == 0 else f"{rate:.2f}x"


# dest 경로 → (원본 경로, rate, mtime_ns, size). 같은 원본 버전·배속 재드래그 시
# 재렌더 없이 재사용, 다른 원본이 같은 파일명을 쓰면 _2, _3 번호로 회피.
_speed_claims: Dict[str, tuple] = {}


def render_speed_wav(src: str, rate: float) -> Optional[str]:
    """배속을 렌더링한 임시 WAV → 경로. 재생 엔진과 동일한 버리스피드(선형보간,
    음정 같이 변함). 파일명은 `원본이름_0.45x.wav` 형식. soundfile 미지원 포맷/실패
    시 None (콜러가 내보내기 중단). 출력은 원본과 같은 샘플레이트/채널/비트뎁스 WAV.

    원본 WAV 가 확장 헤더(WAVE_FORMAT_EXTENSIBLE)를 갖고 있으면 결과에도 같은
    dwChannelMask 를 남긴다. libsndfile 은 일반 "WAV" 로 쓰면 5ch 등에서 확장 헤더
    없이 써서 마스크가 사라지고, 그 파일을 Pro Tools 에 올리면 채널 순서를 알 수
    없어 R/C 가 뒤바뀔 수 있었다 (2026-09-16 사용자 신고). crop 경로(_write_wav)는
    fmt 를 원본 그대로 쓰므로 이 문제가 없다."""
    if not src or not math.isfinite(rate) or rate <= 0 or abs(rate - 1.0) < 0.005:
        return None
    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        logger.warning("[speed] soundfile/numpy 미설치 — 배속 렌더 불가")
        return None
    try:
        stamp = os.stat(src)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(src)), rate, stamp.st_mtime_ns, stamp.st_size)
    label = _speed_label(rate)
    base = os.path.splitext(os.path.basename(src))[0]
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        version = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:12]
        dest = TEMP_DIR / f"{base}_{label}_{version}.wav"
        n_suffix = 2
        while _speed_claims.get(str(dest)) not in (None, key):
            dest = TEMP_DIR / f"{base}_{label}_{version}_{n_suffix}.wav"
            n_suffix += 1
        if _speed_claims.get(str(dest)) == key and dest.exists():
            return str(dest)
        # 원본의 채널 마스크 — 있으면 결과에도 보존한다. 없으면(일반 fmt) 결과도 예전처럼
        # 일반 fmt 다. 여기서 마스크를 새로 만들어 붙이지 않는다 (판정 결과를 파일에
        # 써 넣는 것은 별개의 정책 결정이다).
        mask = _source_channel_mask(src)
        with sf.SoundFile(src) as fin:
            n, sr, ch = fin.frames, fin.samplerate, fin.channels
            if n < 2 or sr <= 0 or ch <= 0:
                return None
            subtype = fin.subtype if sf.check_format("WAV", fin.subtype) else "FLOAT"
            # WAVEX = 같은 WAV 컨테이너를 확장 헤더로 쓰는 libsndfile 포맷. 마스크 값은
            # libsndfile 이 채널 수로 추정해 넣으므로(5ch 는 0) 닫은 뒤 원본 값으로 덮는다.
            out_format = ("WAVEX" if mask is not None and sf.check_format("WAVEX", subtype)
                          else "WAV")
            out_n = max(1, int(round(n / rate)))
            OUT_CHUNK = 262144
            with sf.SoundFile(str(dest), "w", samplerate=sr, channels=ch,
                              subtype=subtype, format=out_format) as fout:
                j = 0
                while j < out_n:
                    m = min(OUT_CHUNK, out_n - j)
                    pos = (j + np.arange(m, dtype=np.float64)) * rate
                    np.minimum(pos, n - 1, out=pos)
                    a = int(pos[0])
                    fin.seek(a)
                    blk = fin.read(min(n, int(pos[-1]) + 2) - a,
                                   dtype="float32", always_2d=True)
                    rel = pos - a
                    i0 = np.minimum(rel.astype(np.int64), blk.shape[0] - 1)
                    i1 = np.minimum(i0 + 1, blk.shape[0] - 1)
                    frac = (rel - i0).astype(np.float32)[:, None]
                    fout.write(blk[i0] * (1.0 - frac) + blk[i1] * frac)
                    j += m
        if mask is not None:
            if (out_format != "WAVEX" or not _patch_channel_mask(str(dest), mask)
                    or _source_channel_mask(str(dest)) != mask):
                raise RuntimeError("채널 마스크를 보존하지 못했습니다")
        after = os.stat(src)
        if (after.st_mtime_ns, after.st_size) != (stamp.st_mtime_ns, stamp.st_size):
            raise RuntimeError("렌더 도중 원본 파일이 변경되었습니다")
        _append_speed_chunks(src, str(dest))
        _speed_claims[str(dest)] = key
        return str(dest)
    except Exception:
        logger.warning("[speed] 배속 렌더 실패: %s (rate=%.2f)", src, rate, exc_info=True)
        if "dest" in locals():
            dest.unlink(missing_ok=True)
        return None


def _append_speed_chunks(src: str, dest: str):
    """원본 WAV의 설명 메타 청크(bext/iXML 등)를 렌더된 WAV 끝에 붙이고 RIFF 크기 갱신."""
    if not src.lower().endswith(".wav"):
        return
    try:
        _fmt, chunks, _off, _size = _parse_riff(src)
        keep = [(cid, d) for cid, d in chunks if cid in SPEED_PRESERVE_CHUNKS]
        if not keep:
            return
        with open(dest, "r+b") as f:
            f.seek(0, 2)
            for cid, d in keep:
                _write_chunk(f, cid, d)
            end = f.tell()
            f.seek(4)
            f.write(struct.pack("<I", end - 8))
    except Exception:
        logger.debug("[speed] 메타 청크 보존 실패 (무시): %s", src, exc_info=True)


def _source_channel_mask(src: str) -> Optional[int]:
    """원본 WAV 의 fmt 가 확장 헤더면 dwChannelMask, 아니면 None (보존할 것이 없다)."""
    if not src.lower().endswith(".wav"):
        return None
    try:
        fmt_chunk, _chunks, _off, _size = _parse_riff(src)
    except Exception:
        logger.debug("[speed] 원본 fmt 읽기 실패 (무시): %s", src, exc_info=True)
        return None
    if (len(fmt_chunk) < 40
            or struct.unpack_from("<H", fmt_chunk, 0)[0] != WAVE_FORMAT_EXTENSIBLE):
        return None
    return int(struct.unpack_from("<I", fmt_chunk, 20)[0])


def _patch_channel_mask(dest: str, mask: int) -> bool:
    """렌더된 WAVEX 의 fmt 안 dwChannelMask 를 제자리에서 원본 값으로 덮어쓴다.
    fmt 크기(40)는 그대로라 data 를 옮길 필요가 없다. 실패하면 libsndfile 이 넣은
    추정값이 남는다 (5ch 는 0 = 순서 미지정, 곧 예전과 같은 상태)."""
    try:
        with open(dest, "r+b") as f:
            if f.read(4) != b"RIFF":
                return False
            f.read(4)
            if f.read(4) != b"WAVE":
                return False
            while True:
                header = f.read(8)
                if len(header) < 8:
                    return False
                chunk_id, size = struct.unpack("<4sI", header)
                if chunk_id != b"fmt ":
                    f.seek(size + size % 2, 1)
                    continue
                start = f.tell()
                head = f.read(24)
                if (size < 40 or len(head) < 24
                        or struct.unpack_from("<H", head, 0)[0] != WAVE_FORMAT_EXTENSIBLE):
                    logger.warning("[speed] 렌더 결과가 확장 헤더가 아니어서 채널 마스크를 "
                                   "복원하지 못함: %s", dest)
                    return False
                f.seek(start + 20)
                f.write(struct.pack("<I", mask))
                return True
    except Exception:
        logger.warning("[speed] 채널 마스크 복원 실패: %s", dest, exc_info=True)
        return False


def cleanup_temp_files():
    cutoff = time.time() - MAX_AGE_HOURS * 3600
    # 영역 crop 임시 + MAX_PATH 초과 드래그용 임시 복사본 둘 다 정리
    longpath_dir = TEMP_DIR.parent / "SoundField_longpath"
    for d, pattern in ((TEMP_DIR, "*.wav"), (longpath_dir, "*")):
        if not d.exists():
            continue
        for p in d.glob(pattern):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
