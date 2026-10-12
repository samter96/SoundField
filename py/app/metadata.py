import html
import os
import logging
import re
import struct
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Optional

from mutagen.wave import WAVE
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.aiff import AIFF

from app.database import AudioFile, Database

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".wav", ".bwf", ".rf64", ".mp3", ".flac", ".aiff", ".aif"}


def _winlong(path: str) -> str:
    """Windows 260자 (MAX_PATH) 우회 — `\\\\?\\` prefix 추가.
    open/os.stat 호출 직전에 만 변환. dirname/basename 등은 원본 path 사용.
    """
    if os.name != 'nt' or not path:
        return path
    if path.startswith('\\\\?\\'):
        return path
    if len(path) < 248:  # 안전 마진 (Windows API 의 실제 한계는 ~248)
        return path
    abs_path = os.path.abspath(path)
    if abs_path.startswith('\\\\'):
        # UNC: \\server\share\... → \\?\UNC\server\share\...
        return '\\\\?\\UNC\\' + abs_path[2:]
    return '\\\\?\\' + abs_path
MAX_METADATA_CHUNK_BYTES = 4 * 1024 * 1024
RIFF_HEADER_SIZE = 12
CHUNK_HEADER_SIZE = 8
RIFF_PREFETCH_BYTES = 256 * 1024  # 256KB
# 1MB 상향 시 NAS/네트워크 환경에서 불필요한 트래픽 폭증으로 오히려 속도 저하 발생.
# 256KB 는 대다수 오디오 헤더(BWF 포함)를 한 번의 RPC로 가져오는 최적의 균형점.
WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

# 메타 분석기 버전 — 올리면 시작 시 이전 실패분(meta_extracted=2)을 1회 자동 재분석.
# v2: 확장자만 .wav 인 AIFF 내용 인식 + 잘린 data 청크 실제 크기로 계산 + 사유 세분화.
# v3 (2026-10-12): WAV 의 소리 데이터 **뒤** LIST·bext 와 iXML(앞·뒤)까지 읽는다 — 키워드·UCS 분류·제목.
#     이미 읽은 WAV 는 audio_files.meta_ver < 3 이면 '보강 대기' 로 보고 쉬는 동안 다시 읽는다
#     (LibraryManager.reparse_metadata). 성공한 파일을 실패/대기로 되돌리지 않는다.
META_PARSER_VERSION = 3

# 실패 사유 — UI(현황/재시도 결과 팝업)에 그대로 노출되므로 한글.
_KIND_AIFF = "\x00aiff"  # 사유 아님 — 내부 신호(내용이 AIFF)
ERR_EMPTY = "빈 파일 (0 바이트)"
ERR_ZERO_HEADER = "헤더가 0으로 손상됨 (파일 앞부분 유실)"
ERR_NOT_AUDIO = "오디오 파일 형식 아님 (RIFF/WAVE 표식 없음)"
ERR_TOO_SHORT = "파일이 너무 작음 (헤더 미완성)"
ERR_NO_FMT = "WAV 헤더 손상 (fmt 청크 없음)"
ERR_NO_DATA = "소리 데이터 없음 (data 청크 없음/빈 상태)"
ERR_CHUNK_BROKEN = "WAV 청크 구조 깨짐 (크기 표기 불일치)"
ERR_UNSUPPORTED_CODEC = "지원하지 않는 압축 WAV"
ERR_BAD_FMT = "fmt 청크 값 이상 (채널/샘플레이트 0)"
ERR_IO = "파일 읽기 오류"

# 진단 카운터 (GIL 덕분에 += 1 atomic, threadsafe).
_stats_fast_ok = 0
_stats_mutagen_fb = 0
_stats_wave_fb = 0
_stats_mp3 = 0
_stats_flac = 0
_stats_aiff = 0


def pop_metadata_stats() -> dict:
    """누적 진단 카운터 읽고 reset. _phase2_metadata 가 청크당 출력."""
    global _stats_fast_ok, _stats_mutagen_fb, _stats_wave_fb
    global _stats_mp3, _stats_flac, _stats_aiff
    s = {
        "fast_ok": _stats_fast_ok,
        "mutagen_fb": _stats_mutagen_fb,
        "wave_fb": _stats_wave_fb,
        "mp3": _stats_mp3,
        "flac": _stats_flac,
        "aiff": _stats_aiff,
    }
    _stats_fast_ok = _stats_mutagen_fb = _stats_wave_fb = 0
    _stats_mp3 = _stats_flac = _stats_aiff = 0
    return s


@dataclass(slots=True)
class _WavState:
    is_rf64: bool = False
    rf64_data_size: int = 0
    data_size: int = 0
    sample_rate: int = 0
    byte_rate: int = 0
    bit_depth: int = 0
    channels: int = 0
    title: Optional[str] = None
    description: Optional[str] = None
    comments: Optional[str] = None
    keywords: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    err: str = ""

    @property
    def has_required_audio_properties(self) -> bool:
        return (
            self.data_size > 0 and self.sample_rate > 0 and self.byte_rate > 0
            and self.bit_depth > 0 and self.channels > 0
        )


def is_supported(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTS


def is_supported_audio_file(path: str) -> bool:
    """Phase1 fast path — 확장자 + sidecar 제외만. 파일 open X.
    실제 컨테이너 검증은 Phase2 extract() 에서 한 번만 수행.
    """
    if is_ignored_audio_sidecar(path):
        return False
    return Path(path).suffix.lower() in SUPPORTED_EXTS


def is_ignored_audio_sidecar(path: str) -> bool:
    name = os.path.basename(path)
    parts = Path(path).parts
    return name.startswith("._") or name.startswith(".DS_Store") or "__MACOSX" in parts


def extract(file_path: str, size: Optional[int] = None,
            mtime: Optional[float] = None,
            file_id: Optional[str] = None) -> tuple[Optional[AudioFile], Optional[str]]:
    """파일 메타데이터 추출. v3.0.0 Extreme 최적화.
    WAV뿐만 아니라 MP3/FLAC/AIFF 도 메모리 프리페치 버퍼를 사용하여 I/O 최소화.
    """
    try:
        # 260자 이상 경로는 Win32 long-path API 로 우회 (open/os.stat 호출 직전 변환).
        # 원본 file_path 는 DB/UI/메타에 그대로 저장.
        io_path = _winlong(file_path)
        if size is None or mtime is None:
            st = os.stat(io_path)
            size, mtime = st.st_size, st.st_mtime

        if is_ignored_audio_sidecar(file_path):
            return None, "맥 부산물 파일 (분석 대상 아님)"

        # file_id 가 인자로 넘어오면 재계산 생략 (Phase2 최적화)
        fid = file_id or Database.generate_id(file_path)

        # os.path.splitext 보다 빠른 슬라이싱 사용
        ext = file_path[-5:].lower()
        if ext.endswith(".wav") or ext.endswith(".bwf") or ext.endswith(".rf64"):
            # WAV 고속 경로. 실패해도 wave.open 재시도는 하지 않음 (same-file 2회 SMB open
            # 비용 대비 회수율 낮음). 단, 내용이 AIFF 면 확장자 오인이므로 AIFF 경로로 넘긴다.
            fast, err = _extract_riff_fast(file_path, size, mtime, fid)
            if fast is not None:
                global _stats_fast_ok
                _stats_fast_ok += 1
                return fast, None
            if err == _KIND_AIFF:
                kind = "aiff"
            else:
                global _stats_wave_fb
                _stats_wave_fb += 1
                return None, err
        elif ext.endswith(".mp3"):
            kind = "mp3"
        elif ext.endswith(".flac"):
            kind = "flac"
        elif ext.endswith(".aiff") or ext.endswith(".aif"):
            kind = "aiff"
        else:
            return None, "Unsupported format"

        # MP3, FLAC, AIFF 등 Mutagen 기반 포맷도 프리페치 적용
        try:
            with open(io_path, "rb", buffering=0) as f:
                prefetch_size = min(size, RIFF_PREFETCH_BYTES)
                buffer = f.read(prefetch_size)
                stream = BytesIO(buffer)
                audio, codec, bit_depth = _open_mutagen(stream, kind)
        except Exception:
            # 프리페치 버퍼가 너무 작아 Mutagen이 실패한 경우 원본 파일로 재시도 (Fallback)
            audio, codec, bit_depth = _open_mutagen(io_path, kind)

        info = audio.info
        duration = float(info.length)
        sample_rate = int(getattr(info, "sample_rate", 0))
        channels = int(getattr(info, "channels", 0))
        bitrate = (int(info.bitrate // 1000) if codec == "MP3"
                   else int((size * 8) / (duration * 1000)) if duration > 0 else 0)

        tags = getattr(audio, "tags", None)
        title = _tag(tags, "TIT2", "title", "TITLE")
        artist = _tag(tags, "TPE1", "artist", "ARTIST")
        album = _tag(tags, "TALB", "album", "ALBUM")
        genre = _tag(tags, "TCON", "genre", "GENRE")
        comments = _tag(tags, "COMM", "comment", "COMMENT", "COMMENTS")
        description = _tag(tags, "TXXX:Description", "description", "DESCRIPTION", "desc", "DESC", "bext:description")
        keywords = _tag(tags, "TXXX:Keywords", "keywords", "KEYWORDS", "keyword", "KEYWORD", "tags", "TAGS")
        category = _tag(tags, "TXXX:Category", "category", "CATEGORY", "cat", "CAT")
        sub_category = _tag(tags, "TXXX:SubCategory", "subcategory", "SUBCATEGORY", "sub_category", "SUB_CATEGORY")
        source = _tag(tags, "TXXX:Source", "source", "SOURCE", "library", "LIBRARY", "manufacturer", "MANUFACTURER")

        return AudioFile(
            file_id=fid,
            file_path=file_path,
            file_name=os.path.basename(file_path),
            file_size=size, duration=duration,
            sample_rate=sample_rate, channels=channels,
            bit_depth=bit_depth, codec=codec, bitrate=bitrate,
            title=title, artist=artist, album=album,
            genre=genre, comments=comments, description=description,
            keywords=keywords, category=category, sub_category=sub_category,
            source=source, modified_at=mtime,
        ), None
    except Exception as e:
        err_msg = _korean_error(e)
        logger.debug(f"메타 실패 {os.path.basename(file_path)}: {type(e).__name__}: {e}")
        return None, err_msg


# mutagen/OS 예외 → 한글 사유 (UI 노출). 미등록 예외는 타입명을 덧붙여 진단 가능하게.
_ERR_BY_TYPE = {
    "EmptyChunk": "AIFF 청크 손상 (빈 청크)",
    "InvalidChunk": "AIFF 청크 손상 (읽을 수 없는 청크 이름)",
    "HeaderNotFoundError": "오디오 헤더를 찾을 수 없음 (형식 손상)",
    "MutagenError": "파일 형식 해석 실패",
    "FileNotFoundError": "파일이 없음 (이동/삭제됨)",
    "PermissionError": "접근 권한 없음",
    "OSError": ERR_IO,
}


def _korean_error(e: Exception) -> str:
    name = type(e).__name__
    msg = _ERR_BY_TYPE.get(name)
    if msg:
        return msg
    for base in type(e).__mro__[1:]:
        msg = _ERR_BY_TYPE.get(base.__name__)
        if msg:
            return msg
    return f"파일 분석 오류 ({name})"


def _open_mutagen(src, kind: str):
    """src = BytesIO(프리페치) 또는 파일 경로. 반환 (audio, codec, bit_depth)."""
    if kind == "mp3":
        global _stats_mp3
        _stats_mp3 += 1
        return MP3(src), "MP3", 16
    if kind == "flac":
        global _stats_flac
        _stats_flac += 1
        audio = FLAC(src)
        return audio, "FLAC", getattr(audio.info, "bits_per_sample", 16)
    global _stats_aiff
    _stats_aiff += 1
    audio = AIFF(src)
    return audio, "AIFF", getattr(audio.info, "bits_per_sample", 16)


def _tag(tags, *keys) -> Optional[str]:
    if tags is None:
        return None
    for k in keys:
        try:
            v = tags.get(k)
            if v is None:
                continue
            if isinstance(v, list):
                s = "; ".join(str(x).strip() for x in v if str(x).strip())
            else:
                s = str(v).strip()
            if s:
                return s
        except Exception:
            continue
    return None


def _extract_riff_fast(file_path: str, size: int, mtime: float,
                       file_id: str) -> tuple[Optional[AudioFile], str]:
    """반환 (AudioFile, "") 또는 (None, 실패사유). 사유가 _KIND_AIFF 면 확장자 오인."""
    state, err = _read_riff_metadata(file_path, size)
    if state is None:
        return None, err
    if not state.has_required_audio_properties:
        if state.err:
            return None, state.err
        if not state.sample_rate:
            return None, ERR_NO_FMT
        return None, ERR_NO_DATA
    duration = state.data_size / state.byte_rate if state.byte_rate > 0 else 0.0
    if duration <= 0:
        return None, ERR_NO_DATA
    bitrate = int((size * 8) / (duration * 1000)) if duration > 0 else 0
    return AudioFile(
        file_id=file_id,
        file_path=file_path,
        file_name=os.path.basename(file_path),
        file_size=size,
        duration=duration,
        sample_rate=state.sample_rate,
        channels=state.channels,
        bit_depth=state.bit_depth,
        codec="PCM",
        bitrate=bitrate,
        title=state.title,
        artist=state.artist,
        album=state.album,
        comments=state.comments or state.description,
        description=state.description,
        keywords=state.keywords,
        category=state.category,
        sub_category=state.sub_category,
        modified_at=mtime,
    ), ""


def _read_riff_metadata(file_path: str, size: int) -> tuple[Optional[_WavState], str]:
    """Prefetch 방식 — RIFF_PREFETCH_BYTES 만큼 한 번에 읽어 메모리(BytesIO) 파싱.
    헤더/메타가 버퍼를 벗어나면 실제 파일 핸들 f 로 fallback seek+read.

    명시적 `pos` 추적: BytesIO position 의존 X — 청크 데이터/위치가 버퍼 범위
    밖에서도 정확히 계속 진행. 종료 조건은 data 청크 도달 or 파일 끝.
    """
    if size <= 0:
        return None, ERR_EMPTY
    if size < RIFF_HEADER_SIZE:
        return None, ERR_TOO_SHORT
    try:
        # buffering=-1: Python 기본 버퍼 (~8KB OS 버퍼). buffering=0 raw FileIO 는
        # SMB 에서 short-read 가능성. 기본 BufferedReader 가 안전.
        with open(_winlong(file_path), "rb") as f:
            prefetch_size = min(size, RIFF_PREFETCH_BYTES)
            buffer = f.read(prefetch_size)
            if len(buffer) < RIFF_HEADER_SIZE:
                return None, ERR_TOO_SHORT

            stream = BytesIO(buffer)
            stream_len = size
            buffer_limit = len(buffer)

            header = _read_exact(stream, RIFF_HEADER_SIZE)
            riff_id = header[:4]
            if riff_id not in (b"RIFF", b"RF64") or header[8:12] != b"WAVE":
                # 확장자만 .wav 이고 내용은 AIFF — 호출부가 AIFF 경로로 재분기.
                if riff_id == b"FORM" and header[8:12] in (b"AIFF", b"AIFC"):
                    return None, _KIND_AIFF
                if header == b"\x00" * RIFF_HEADER_SIZE:
                    return None, ERR_ZERO_HEADER
                return None, ERR_NOT_AUDIO

            state = _WavState(is_rf64=riff_id == b"RF64")
            pos = RIFF_HEADER_SIZE  # 버퍼 범위와 무관한 절대 파일 오프셋

            while pos + CHUNK_HEADER_SIZE <= stream_len:
                # 청크 헤더 fetch: 버퍼 안이면 BytesIO, 아니면 실제 파일
                if pos + CHUNK_HEADER_SIZE <= buffer_limit:
                    stream.seek(pos)
                    chunk_header = stream.read(CHUNK_HEADER_SIZE)
                else:
                    try:
                        f.seek(pos)
                        chunk_header = f.read(CHUNK_HEADER_SIZE)
                    except OSError:
                        return None, ERR_IO
                if len(chunk_header) < CHUNK_HEADER_SIZE:
                    break

                chunk_id = chunk_header[:4]
                chunk_size32 = struct.unpack_from("<I", chunk_header, 4)[0]
                chunk_data_start = pos + CHUNK_HEADER_SIZE
                chunk_size = _riff_chunk_size(chunk_id, chunk_size32, state)
                avail = stream_len - chunk_data_start

                # data 청크는 size 만 필요 — 실제 데이터 읽지 않음.
                # 녹음/복사가 끊겨 선언 크기가 실제보다 큰 파일은 남은 만큼으로 잘라 계산
                # (플레이어와 동일한 관대한 해석 — 그렇지 않으면 재생되는 파일이 실패로 남음).
                if chunk_id == b"data":
                    if chunk_size < 0:
                        return None, ERR_CHUNK_BROKEN
                    state.data_size = min(chunk_size, avail)
                    # ⚠ 여기서 멈추면 안 된다 (2026-10-12). 사운드마이너·베이스헤드 등으로 메타를
                    #   고친 파일은 설명·키워드·UCS 분류를 **소리 데이터 뒤**에 붙인다 — 예전엔 여기서
                    #   break 해서 라이브러리 WAV 의 71%(표본 300)가 키워드·분류·제목을 못 읽었다.
                    #   데이터 뒤는 덤으로 읽는다: 무엇이 깨져 있어도 파일을 실패로 만들지 않는다.
                    _read_riff_tail(f, chunk_data_start + chunk_size + (chunk_size & 1), stream_len, state)
                    break

                if chunk_size < 0 or chunk_size > avail:
                    return None, ERR_CHUNK_BROKEN

                # 메타 청크 파싱: 데이터가 버퍼 안이면 BytesIO, 아니면 실제 파일
                if chunk_data_start + chunk_size <= buffer_limit:
                    stream.seek(chunk_data_start)
                    src: BinaryIO = stream
                else:
                    try:
                        f.seek(chunk_data_start)
                    except OSError:
                        return None, ERR_IO
                    src = f

                try:
                    if chunk_id == b"fmt ":
                        if not _read_riff_fmt(src, chunk_size, state):
                            return None, state.err or ERR_BAD_FMT
                    elif chunk_id == b"ds64":
                        if not _read_riff_ds64(src, chunk_size, state):
                            return None, ERR_CHUNK_BROKEN
                    elif chunk_id == b"LIST":
                        if not _read_riff_list(src, chunk_size, state):
                            return None, ERR_CHUNK_BROKEN
                    elif chunk_id == b"bext":
                        if not _read_riff_bext(src, chunk_size, state):
                            return None, ERR_CHUNK_BROKEN
                    elif chunk_id == b"iXML":
                        if chunk_size <= MAX_METADATA_CHUNK_BYTES:
                            _apply_ixml(state, _read_exact(src, chunk_size))
                    # else: 알 수 없는 청크 — 본문 skip (pos += chunk_size 로 처리)
                except (ValueError, struct.error):
                    return None, ERR_CHUNK_BROKEN

                # 다음 청크 위치 (RIFF 워드 정렬)
                pos = chunk_data_start + chunk_size
                if chunk_size & 1:
                    pos += 1

            return state, ""
    except OSError:
        return None, ERR_IO
    except (ValueError, struct.error):
        return None, ERR_CHUNK_BROKEN


_TAIL_WANT = (b"LIST", b"bext", b"iXML")
_TAIL_MAX_CHUNKS = 128          # 데이터 뒤 청크 수 상한 (깨진 파일에서 헛돌지 않게)


def _read_riff_tail(f: BinaryIO, pos: int, size: int, state: _WavState):
    """소리 데이터 뒤의 메타 청크(LIST·bext·iXML)만 읽는다. 다른 청크는 머리 8바이트만 보고 건너뛴다
    (ID3 의 앨범 그림·사운드마이너 SMED 처럼 큰 것은 본문을 읽지 않는다 — 표본 평균 2.6KB).
    무엇이 깨져 있어도 조용히 멈춘다 — 앞에서 읽은 소리 정보로 파일은 정상이다."""
    try:
        for _ in range(_TAIL_MAX_CHUNKS):
            if pos + CHUNK_HEADER_SIZE > size:
                return
            f.seek(pos)
            hd = f.read(CHUNK_HEADER_SIZE)
            if len(hd) < CHUNK_HEADER_SIZE:
                return
            cid = hd[:4]
            ln = struct.unpack_from("<I", hd, 4)[0]
            start = pos + CHUNK_HEADER_SIZE
            if ln > size - start:
                return
            if cid in _TAIL_WANT and ln <= MAX_METADATA_CHUNK_BYTES:
                body = f.read(ln)
                if len(body) == ln:
                    if cid == b"LIST":
                        _read_riff_list(BytesIO(body), ln, state)
                    elif cid == b"bext":
                        _read_riff_bext(BytesIO(body), ln, state)
                    else:
                        _apply_ixml(state, body)
            pos = start + ln + (ln & 1)
    except (OSError, ValueError, struct.error):
        return


_IXML_ATTR_RE = re.compile(
    r"<ATTR>\s*(?:<TYPE>[^<]*</TYPE>\s*)?<NAME>([^<]*)</NAME>\s*"
    r"(?:<TYPE>[^<]*</TYPE>\s*)?<VALUE>([^<]*)</VALUE>")


def _apply_ixml(state: _WavState, raw: bytes):
    """iXML → 빈 칸만 채운다. 대응은 베이스헤드가 같은 파일에서 보여 준 것을 따른다 (2026-10-12 실측):
      <USER>  DESCRIPTION·KEYWORDS·CATEGORY·SUBCATEGORY·CATEGORYFULL(분류-하위)·TRACKTITLE/FXNAME·
              ARTIST/DESIGNER·LIBRARY  (UCS·사운드마이너 표준 칸)
      <BWF_DESCRIPTION>
      <STEINBERG> 속성  MediaComment(설명)·MusicalInstrument(키워드)·MediaCategoryPost(분류)·
              MusicalCategory(하위분류)·SmfSongName(제목)·MediaArtist·MediaLibrary/MediaAlbum
    정규식으로 읽는다 — 깨진 XML 이 흔해서 XML 파서는 통째로 실패한다."""
    try:
        text = raw.decode("utf-8", "replace")
    except Exception:
        return
    user_m = re.search(r"<USER>(.*?)</USER>", text, re.S)
    user = user_m.group(1) if user_m else ""

    def tag(src: str, name: str) -> Optional[str]:
        m = re.search(rf"<{name}>([^<]*)</{name}>", src)
        v = html.unescape(m.group(1)).strip() if m else ""
        return v or None

    attrs = {}
    for name, value in _IXML_ATTR_RE.findall(text):
        value = html.unescape(value).strip()
        if value:
            attrs[name.strip()] = value
    full = tag(user, "CATEGORYFULL")
    cat_full = full.split("-", 1) if full and "-" in full else None
    state.description = state.description or tag(user, "DESCRIPTION") or tag(text, "BWF_DESCRIPTION") \
        or attrs.get("MediaComment")
    state.keywords = state.keywords or tag(user, "KEYWORDS") or attrs.get("MusicalInstrument")
    state.category = state.category or tag(user, "CATEGORY") or (cat_full[0] if cat_full else None) \
        or attrs.get("MediaCategoryPost")
    state.sub_category = state.sub_category or tag(user, "SUBCATEGORY") \
        or (cat_full[1] if cat_full else None) or attrs.get("MusicalCategory")
    state.title = state.title or tag(user, "TRACKTITLE") or tag(user, "FXNAME") or attrs.get("SmfSongName")
    state.artist = state.artist or tag(user, "ARTIST") or tag(user, "DESIGNER") or attrs.get("MediaArtist")
    state.album = state.album or tag(user, "LIBRARY") or attrs.get("MediaLibrary") or attrs.get("MediaAlbum")


def _read_riff_fmt(stream: BinaryIO, chunk_size: int, state: _WavState) -> bool:
    if chunk_size < 16 or chunk_size > MAX_METADATA_CHUNK_BYTES:
        state.err = ERR_CHUNK_BROKEN
        return False
    data = _read_exact(stream, chunk_size)
    format_tag, channels, sample_rate, byte_rate, block_align, bits = struct.unpack_from(
        "<HHIIHH", data, 0
    )
    if format_tag == WAVE_FORMAT_EXTENSIBLE:
        if len(data) < 40:
            state.err = ERR_CHUNK_BROKEN
            return False
        valid_bits = struct.unpack_from("<H", data, 18)[0]
        if valid_bits > 0:
            bits = valid_bits
        sub_format_tag = struct.unpack_from("<I", data, 24)[0]
        if sub_format_tag not in (WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT):
            state.err = f"{ERR_UNSUPPORTED_CODEC} (형식 0x{sub_format_tag:04X})"
            return False
    elif format_tag not in (WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT):
        state.err = f"{ERR_UNSUPPORTED_CODEC} (형식 0x{format_tag:04X})"
        return False
    if not channels or not sample_rate or not byte_rate or not block_align or not bits:
        state.err = ERR_BAD_FMT
        return False
    state.channels = channels
    state.sample_rate = sample_rate
    state.byte_rate = byte_rate
    state.bit_depth = bits
    return True


def _read_riff_ds64(stream: BinaryIO, chunk_size: int, state: _WavState) -> bool:
    if chunk_size < 28 or chunk_size > MAX_METADATA_CHUNK_BYTES:
        return False
    data = _read_exact(stream, chunk_size)
    state.rf64_data_size = struct.unpack_from("<Q", data, 8)[0]
    if chunk_size >= 32:
        table_length = struct.unpack_from("<I", data, 28)[0]
        offset = 32
        for _ in range(table_length):
            if offset + 12 > len(data):
                break
            chunk_id = data[offset:offset + 4]
            table_chunk_size = struct.unpack_from("<Q", data, offset + 4)[0]
            if chunk_id == b"data":
                state.rf64_data_size = table_chunk_size
            offset += 12
    return True


def _read_riff_list(stream: BinaryIO, chunk_size: int, state: _WavState) -> bool:
    if chunk_size < 4:
        stream.seek(stream.tell() + chunk_size)
        return True
    if chunk_size > MAX_METADATA_CHUNK_BYTES:
        return False
    data = _read_exact(stream, chunk_size)
    if data[:4] != b"INFO":
        return True
    offset = 4
    while offset + CHUNK_HEADER_SIZE <= len(data):
        tag_id = data[offset:offset + 4].decode("ascii", "ignore")
        value_size = struct.unpack_from("<I", data, offset + 4)[0]
        offset += CHUNK_HEADER_SIZE
        if offset + value_size > len(data):
            return False
        value = _decode_riff_text(data[offset:offset + value_size])
        if value:
            _apply_riff_info_tag(state, tag_id, value)
        offset += value_size + (value_size & 1)
    return True


def _read_riff_bext(stream: BinaryIO, chunk_size: int, state: _WavState) -> bool:
    if chunk_size > MAX_METADATA_CHUNK_BYTES:
        return False
    data = _read_exact(stream, chunk_size)
    if len(data) >= 256:
        description = _decode_riff_text(data[:256])
        if description:
            state.description = state.description or description
    if len(data) >= 288:
        originator = _decode_riff_text(data[256:288])
        if originator:
            state.artist = state.artist or originator
    return True


def _riff_chunk_size(chunk_id: bytes, chunk_size32: int, state: _WavState) -> int:
    if state.is_rf64 and chunk_id == b"data" and chunk_size32 == 0xFFFFFFFF:
        return state.rf64_data_size if state.rf64_data_size > 0 else -1
    return chunk_size32


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("short read")
    return data


def _decode_riff_text(data: bytes) -> Optional[str]:
    if b"\x00" in data:
        data = data[:data.index(b"\x00")]
    data = data.strip()
    if not data:
        return None
    try:
        return data.decode("utf-8").strip() or None
    except UnicodeDecodeError:
        return data.decode("latin-1", "replace").strip() or None


def _apply_riff_info_tag(state: _WavState, tag_id: str, value: str):
    if tag_id == "INAM":
        state.title = state.title or value
    elif tag_id == "ICMT":
        state.comments = state.comments or value
    elif tag_id == "IART":
        state.artist = state.artist or value
    elif tag_id == "IPRD":
        state.album = state.album or value
    elif tag_id in ("IGNR", "IGEN"):
        state.category = state.category or value
    elif tag_id == "ISBJ":
        state.sub_category = state.sub_category or value
        state.description = state.description or value
    elif tag_id == "IKEY":
        state.keywords = state.keywords or value


