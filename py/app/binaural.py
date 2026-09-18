"""IEM 사이드카 기반 멀티채널 바이노럴 재생.

오디오 샘플은 Python으로 옮기지 않는다. 별도 프로세스가 WAV를 직접 열고
MultiEncoder(SN3D)와 BinauralDecoder를 거쳐 오디오 장치로 출력한다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .downmix import norm_db

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
EXPECTED_PLUGINS = {"MultiEncoder": "1.0.4", "BinauralDecoder": "0.6.6"}
IEM_SUITE_VERSION = "1.15.0"
IEM_SUITE_DOWNLOAD_URL = "https://plugins.iem.at/download/"
SUPPORTED_EXTENSIONS = {".wav"}
LAYOUT_OVERRIDE_FILE = Path.home() / "AppData" / "Local" / "SoundField" / "binaural_layouts.json"

# 새로 빌드한 fixed 사이드카는 6ch 입력보다 작은 FOA(4ch) 작업 버퍼에서도
# 모든 source channel을 보존한다. 제작 문서와 같은 1차/SN3D를 사용한다.
SURROUND_ENCODE_ORDER = 1

# 사이드카는 최악 조건의 클리핑을 피하기 위해 최종 출력에 0.37(-8.64 dB)을 곱한다.
#
# 음량 정책 (2026-09-16 사용자 결정):
#   바이노럴 ON 과 OFF(일반 재생 다운믹스)가 **같은 크기**로 들려야 한다. 검수 도구라
#   켜고 끄며 비교하는데 크기가 점프하면 안 된다.
#   실측(라이브러리 40개, 볼륨 0 dB): 보정 0 이면 바이노럴이 "접기만 한 다운믹스"보다
#   파일별 중앙값 9.6 dB 작았다 (당시 다운믹스가 -4 dB 였고 그 대비 5.6 dB 작았음).
#   그래서 바이노럴 이득 = 9.6 dB + (일반 재생이 그 배치에서 낮추는 만큼).
#   일반 재생은 ffmpeg 식 정규화(downmix.norm_db: 5.1 -7.66 · 7.1 -9.89 · quad -4.65)라
#   배치별로 달라, 보정도 배치를 따라간다 → 5.1 은 +1.94 dB, 순 이득 0.37×1.25 = 0.46.
#   보호 감쇠를 전부 되돌리던 예전(+8.64, 순 이득 1.0)은 33% 가 하드 클리핑됐고,
#   0 은 6.6 dB 작아서 둘 다 기각.
#   사이드카에 리미터가 없다는 사실은 그대로지만, 순 이득이 0.46 으로 내려가
#   raw peak 2.2 까지 하드 클램프에 안 걸린다 (예전 1.42).
SIDECAR_OUTPUT_HEADROOM_GAIN = 0.37
BINAURAL_GAIN_OVER_FOLD_DB = 9.6


def sidecar_volume_db(volume: float, offset_db: float = 0.0) -> float:
    """앱 선형 볼륨을 dB로 바꾸고, 일반 재생과 크기가 맞도록 보정을 더한다.
    offset_db 는 그 배치에서 일반 재생이 낮추는 양(monitor_offset_db) — 음수."""
    if volume <= 0:
        return -100.0
    return max(-100.0, min(24.0, 20.0 * math.log10(volume) + BINAURAL_GAIN_OVER_FOLD_DB + offset_db))


def monitor_offset_db(layout: "BinauralLayout", channels: int) -> float:
    """일반 재생(audio_engine._to_stereo)이 이 배치를 접을 때 낮추는 dB — 바이노럴도 같이 낮춘다."""
    return norm_db(layout_channel_roles(layout), channels)


def friendly_ready_error(ready: Optional[dict], sidecar_installed: bool,
                         ready_received: bool) -> str:
    """사이드카의 기술 오류를 사용자에게 보여 줄 한글 정책 문구로 바꾼다."""
    raw = str((ready or {}).get("error") or "")
    if not sidecar_installed:
        return "바이노럴 처리 구성 요소가 설치되지 않았습니다"
    if not ready_received:
        return "바이노럴 준비 응답 시간이 초과되었습니다"
    if raw == "no-audio-device":
        return "사용할 수 있는 오디오 출력 장치가 없습니다"
    if "iem-plugin-check-failed" in raw:
        if "actual []" in raw:
            return ("IEM MultiEncoder와 BinauralDecoder를 찾지 못했습니다. "
                    "IEM Plug-in Suite를 설치한 뒤 SoundField를 다시 실행하세요")
        return ("설치된 IEM 플러그인 버전이 지원 대상과 다릅니다. "
                f"MultiEncoder {EXPECTED_PLUGINS['MultiEncoder']}, "
                f"BinauralDecoder {EXPECTED_PLUGINS['BinauralDecoder']}가 필요합니다")
    if raw:
        return f"바이노럴 준비에 실패했습니다: {raw}"
    return ("지원하는 IEM 플러그인 버전을 확인하지 못했습니다. "
            f"MultiEncoder {EXPECTED_PLUGINS['MultiEncoder']}, "
            f"BinauralDecoder {EXPECTED_PLUGINS['BinauralDecoder']}가 필요합니다")


def is_iem_install_issue(reason: str) -> bool:
    """공식 IEM 설치 페이지로 안내하면 해결되는 준비 실패인지 판별한다."""
    text = str(reason or "")
    return any(token in text for token in (
        "IEM MultiEncoder",
        "IEM Plug-in Suite",
        "IEM 플러그인 버전",
        "지원하는 IEM 플러그인 버전",
    ))


@dataclass(frozen=True)
class BinauralLayout:
    topology: str
    preset: str
    confidence: str
    azimuth: tuple[float, ...] = ()
    elevation: tuple[float, ...] = ()
    muted: tuple[int, ...] = ()
    source_order: int = -1
    reason: str = ""
    candidates: tuple[str, ...] = ()
    channel_order: str = ""
    source: str = ""
    channel_roles: tuple[str, ...] = ()

    @property
    def can_auto_play(self) -> bool:
        return self.confidence in {"medium", "high"}


class LayoutOverrideStore:
    """파일·폴더별 수동 배치를 로컬 JSON에 저장한다."""

    def __init__(self, path: Path = LAYOUT_OVERRIDE_FILE):
        self.path = path
        self._lock = threading.Lock()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            if isinstance(raw, dict) and raw.get("version") == 2:
                self._files = raw.get("files") if isinstance(raw.get("files"), dict) else {}
                self._folders = raw.get("folders") if isinstance(raw.get("folders"), dict) else {}
            else:
                self._files = raw if isinstance(raw, dict) else {}
                self._folders = {}
        except (OSError, ValueError):
            self._files = {}
            self._folders = {}

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    @staticmethod
    def _token(item) -> str:
        if isinstance(item, dict):
            preset = str(item.get("preset") or "")
            order = str(item.get("order") or "")
            return f"{preset}|{order}" if preset and order else preset
        return str(item or "")

    def get(self, path: str, channels: int = 0) -> str:
        with self._lock:
            item = self._files.get(self._key(path))
            if item is not None:
                return self._token(item)
            folder = self._folders.get(self._key(os.path.dirname(path)))
            if isinstance(folder, dict):
                return self._token(folder.get(str(int(channels or 0))))
            return ""

    def scope(self, path: str, channels: int = 0) -> str:
        with self._lock:
            if self._key(path) in self._files:
                return "file"
            folder = self._folders.get(self._key(os.path.dirname(path)))
            if isinstance(folder, dict) and str(int(channels or 0)) in folder:
                return "folder"
            return ""

    def is_prompt_choice(self, path: str) -> bool:
        """자동 판별 실패 안내 뒤 사용자가 확정한 파일인지 반환한다."""
        with self._lock:
            item = self._files.get(self._key(path))
            return bool(
                isinstance(item, dict)
                and item.get("source") == "layout_prompt"
                and item.get("preset")
            )

    def set(self, path: str, preset: str, *, prompt_choice: bool = False):
        key = self._key(path)
        with self._lock:
            if preset:
                value, order = split_layout_override(preset)
                previous = self._files.get(key)
                keep_prompt_choice = bool(
                    isinstance(previous, dict)
                    and previous.get("source") == "layout_prompt"
                )
                item = {"preset": value}
                if order:
                    item["order"] = order
                if prompt_choice or keep_prompt_choice:
                    item["source"] = "layout_prompt"
                self._files[key] = item
            else:
                self._files.pop(key, None)
            self._save_locked()

    def set_folder(self, path: str, channels: int, preset: str):
        folder_key = self._key(os.path.dirname(path))
        channel_key = str(int(channels or 0))
        with self._lock:
            folder = self._folders.setdefault(folder_key, {})
            if preset:
                value, order = split_layout_override(preset)
                item = {"preset": value}
                if order:
                    item["order"] = order
                folder[channel_key] = item
            else:
                folder.pop(channel_key, None)
                if not folder:
                    self._folders.pop(folder_key, None)
            self._save_locked()

    def _save_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = {"version": 2, "files": self._files, "folders": self._folders}
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)


def split_layout_override(value: str) -> tuple[str, str]:
    preset, separator, order = str(value or "").partition("|")
    order = order if separator and order in {"wave", "film", "smpte"} else ""
    return preset.lower(), order


_KNOWN_MASKS = {
    0x007: "lcr",
    0x033: "quad",
    0x037: "5.0",
    0x03F: "5.1",
    0x607: "5.0",
    0x60F: "5.1",
    0x13F: "6.1",
    0x637: "7.0",
    0x63F: "7.1",
    0x05637: "7.0.2",
}

_PRESET_CHANNELS = {
    "lcr": 3, "quad": 4, "5.0": 5, "5.1": 6,
    "6.1": 7, "7.0": 7, "7.1": 8, "7.0.2": 9,
}

_PRESET_WAVE_ORDER = {
    "lcr": ("L", "R", "C"),
    "quad": ("L", "R", "LB", "RB"),
    "5.0": ("L", "R", "C", "LB", "RB"),
    "5.1": ("L", "R", "C", "LFE", "LB", "RB"),
    "6.1": ("L", "R", "C", "LFE", "LB", "RB", "BC"),
    "7.0": ("L", "R", "C", "LB", "RB", "LS", "RS"),
    "7.1": ("L", "R", "C", "LFE", "LB", "RB", "LS", "RS"),
    "7.0.2": ("L", "R", "C", "LB", "RB", "LS", "RS", "TFL", "TFR"),
}

_PRESET_FILM_ORDER = {
    "lcr": ("L", "C", "R"),
    "5.0": ("L", "C", "R", "LB", "RB"),
    "5.1": ("L", "C", "R", "LB", "RB", "LFE"),
    "7.0": ("L", "C", "R", "LS", "RS", "LB", "RB"),
    "7.1": ("L", "C", "R", "LS", "RS", "LB", "RB", "LFE"),
    "7.0.2": ("L", "C", "R", "LS", "RS", "LB", "RB", "TFL", "TFR"),
}

_PRESET_SMPTE_ORDER = {
    "7.0": ("L", "R", "C", "LS", "RS", "LB", "RB"),
    "7.1": ("L", "R", "C", "LFE", "LS", "RS", "LB", "RB"),
}

_BASE_ANGLES = {
    "L": (30.0, 0.0), "R": (-30.0, 0.0), "C": (0.0, 0.0),
    "LFE": (0.0, 0.0), "LS": (90.0, 0.0), "RS": (-90.0, 0.0),
    "LB": (135.0, 0.0), "RB": (-135.0, 0.0), "BC": (180.0, 0.0),
    "TFL": (45.0, 35.0), "TFR": (-45.0, 35.0),
}

# 지원 포맷 안에서 채널 수만으로 기본 스피커 배치를 정하는 경우.
# 4ch는 명시된 앰비소닉 규격이 없으면 제품 정책상 Quad로 처리한다.
# 9ch는 7.0.2/2차 AmbiX가 충돌하므로 의도적으로 제외한다.
_UNIQUE_SURROUND_BY_CHANNELS = {
    3: "lcr", 4: "quad", 5: "5.0", 6: "5.1", 7: "7.0", 8: "7.1",
}

# 16/25/36ch는 현재 지원 스피커 preset과 충돌하지 않는 고차 AmbiX 수다.
# 9ch는 7.0.2와 충돌하므로 명시 표기나 사용자 선택 없이는 자동 판정하지 않는다.
_HIGH_ORDER_AMBIX_BY_CHANNELS = {16: 3, 25: 4, 36: 5}

_AMBIX_TOKEN = re.compile(r"(^|[^a-z0-9])ambix([^a-z0-9]|$)", re.I)
_FUMA_TOKEN = re.compile(r"(^|[^a-z0-9])fuma([^a-z0-9]|$)", re.I)
_GENERIC_AMBI_TOKEN = re.compile(
    r"(^|[^a-z0-9])(ambisonic|b[\s_-]*format)([^a-z0-9]|$)", re.I)
_SECOND_ORDER_AMBI_TOKEN = re.compile(
    r"(^|[^a-z0-9])(2[\s_-]*(?:oa|nd[\s_-]*order)|hoa)([^a-z0-9]|$)", re.I)
_QUAD_TOKEN = re.compile(r"(^|[^a-z0-9])quad(?:ro)?([^a-z0-9]|$)", re.I)
_AMBEO_TOKEN = re.compile(r"(^|[^a-z0-9])ambeo([^a-z0-9]|$)", re.I)
_NINE_CHANNEL_TOKEN = re.compile(
    r"(^|[^a-z0-9])9[\s_-]*(?:ch|channels?)([^a-z0-9]|$)", re.I)
_SURROUND_TOKENS = (
    (re.compile(r"(^|[^0-9])7\.0\.2(?![0-9])(?!\.[0-9])"), "7.0.2"),
    (re.compile(r"(^|[^0-9])7\.1(?![0-9])(?!\.[0-9])"), "7.1"),
    (re.compile(r"(^|[^0-9])7\.0(?![0-9])(?!\.[0-9])"), "7.0"),
    (re.compile(r"(^|[^0-9])5\.1(?![0-9])(?!\.[0-9])"), "5.1"),
    (re.compile(r"(^|[^0-9])5\.0(?![0-9])(?!\.[0-9])"), "5.0"),
    (re.compile(r"(^|_)702([_\- .]|$)"), "7.0.2"),
    (_NINE_CHANNEL_TOKEN, "7.0.2"),
    (re.compile(r"(^|_)71([_\- .]|$)"), "7.1"),
    (re.compile(r"(^|_)51([_\- .]|$)"), "5.1"),
    (_QUAD_TOKEN, "quad"),
    (re.compile(r"(^|[^a-z0-9])lcr([^a-z0-9]|$)", re.I), "lcr"),
)


def locate_sidecar() -> Optional[str]:
    override = os.environ.get("SOUNDFIELD_BINAURAL_SIDECAR", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override))
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    candidates.extend((
        Path(__file__).resolve().parents[1] / "sidecar" / "scsearch-monitor-fixed.exe",
        bundle_root / "scsearch-monitor-fixed.exe",
        bundle_root / "monitor" / "scsearch-monitor-fixed.exe",
        Path(__file__).resolve().parents[1]
        / "binaural-monitoring-handover (1)" / "sidecar" / "prebuilt"
        / "scsearch-monitor-fixed.exe",
        bundle_root / "scsearch-monitor.exe",
        bundle_root / "monitor" / "scsearch-monitor.exe",
        Path(__file__).resolve().parents[1]
        / "binaural-monitoring-handover (1)" / "sidecar" / "prebuilt"
        / "scsearch-monitor.exe",
    ))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def is_binaural_candidate(path: str, meta: Optional[dict]) -> bool:
    if Path(path).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    try:
        return int((meta or {}).get("channels") or 0) > 2
    except (TypeError, ValueError):
        return False


_CHANNEL_ALIASES = {
    "L": "L", "LEFT": "L", "FL": "L", "FRONTLEFT": "L",
    "R": "R", "RIGHT": "R", "FR": "R", "FRONTRIGHT": "R",
    "C": "C", "CENTER": "C", "CENTRE": "C", "FC": "C",
    "FRONTCENTER": "C", "FRONTCENTRE": "C",
    "LFE": "LFE", "SUB": "LFE", "SUBWOOFER": "LFE",
    "LOWFREQUENCY": "LFE", "LOWFREQUENCYEFFECTS": "LFE",
    "LS": "LS", "SL": "LS", "LSS": "LS", "LEFTSIDE": "LS",
    "LEFTSURROUND": "LS", "SURROUNDLEFT": "LS",
    "RS": "RS", "SR": "RS", "RSS": "RS", "RIGHTSIDE": "RS",
    "RIGHTSURROUND": "RS", "SURROUNDRIGHT": "RS",
    "LB": "LB", "BL": "LB", "LSR": "LB", "LEFTBACK": "LB",
    "BACKLEFT": "LB", "LEFTREAR": "LB", "REARLEFT": "LB",
    "RB": "RB", "BR": "RB", "RSR": "RB", "RIGHTBACK": "RB",
    "BACKRIGHT": "RB", "RIGHTREAR": "RB", "REARRIGHT": "RB",
    "BC": "BC", "BACKCENTER": "BC", "BACKCENTRE": "BC",
    "REARCENTER": "BC", "REARCENTRE": "BC",
    "TFL": "TFL", "LTF": "TFL", "TOPFRONTLEFT": "TFL",
    "LEFTTOPFRONT": "TFL", "TFR": "TFR", "RTF": "TFR",
    "TOPFRONTRIGHT": "TFR", "RIGHTTOPFRONT": "TFR",
}


def _normalize_channel_name(value: str) -> str:
    value = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", str(value or ""))
    return _CHANNEL_ALIASES.get(re.sub(r"[^A-Z0-9]", "", value.upper()), "")


def _local_tag(element) -> str:
    return str(element.tag).rsplit("}", 1)[-1].upper()


def _parse_ixml_tracks(data: bytes, channels: int) -> tuple[str, ...]:
    try:
        root = ET.fromstring(data.rstrip(b"\0"))
    except (ET.ParseError, ValueError):
        return ()
    track_list = next((item for item in root.iter()
                       if _local_tag(item) == "TRACK_LIST"), None)
    if track_list is None:
        return ()
    indexed = {}
    for track in track_list:
        if _local_tag(track) != "TRACK":
            continue
        values = {}
        for item in track.iter():
            if item is not track:
                values[_local_tag(item)] = str(item.text or "").strip()
        name = values.get("NAME", "")
        role = _normalize_channel_name(values.get("FUNCTION", ""))
        if not role:
            role = _normalize_channel_name(name)
        raw_index = values.get("INTERLEAVE_INDEX", "")
        if not raw_index:
            numbered = re.search(r"\(\s*(\d+)\s*\)\s*$", name)
            raw_index = numbered.group(1) if numbered else ""
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            return ()
        if not role or index < 1 or index > channels or index in indexed:
            return ()
        indexed[index] = role
    if len(indexed) != channels:
        return ()
    return tuple(indexed[index] for index in range(1, channels + 1))


@lru_cache(maxsize=256)
def _read_wave_layout_cached(path: str, size: int, modified_ns: int,
                             channels: int) -> tuple[Optional[int], tuple[str, ...]]:
    del size, modified_ns
    mask = None
    ixml = b""
    data_size_64 = None
    try:
        with open(path, "rb") as stream:
            header = stream.read(12)
            if (len(header) != 12 or header[:4] not in {b"RIFF", b"RF64", b"BW64"}
                    or header[8:] != b"WAVE"):
                return None, ()
            while True:
                chunk = stream.read(8)
                if len(chunk) != 8:
                    break
                chunk_id, raw_size = struct.unpack("<4sI", chunk)
                chunk_size = raw_size
                if chunk_id == b"ds64":
                    data = stream.read(min(chunk_size, 64))
                    if len(data) >= 16:
                        data_size_64 = int(struct.unpack_from("<Q", data, 8)[0])
                    stream.seek(max(0, chunk_size - len(data)), 1)
                elif chunk_id == b"fmt ":
                    data = stream.read(min(chunk_size, 64))
                    if (len(data) >= 24
                            and struct.unpack_from("<H", data, 0)[0] == 0xFFFE):
                        mask = int(struct.unpack_from("<I", data, 20)[0])
                    stream.seek(max(0, chunk_size - len(data)), 1)
                elif chunk_id.lower() == b"ixml":
                    if chunk_size > 8 * 1024 * 1024:
                        return mask, ()
                    ixml = stream.read(chunk_size)
                else:
                    if chunk_id == b"data" and raw_size == 0xFFFFFFFF:
                        if data_size_64 is None:
                            break
                        chunk_size = data_size_64
                    if chunk_size >= 0x7FFFFFFFFFFFFFF0:
                        break
                    stream.seek(chunk_size, 1)
                if chunk_size & 1:
                    stream.seek(1, 1)
    except (OSError, OverflowError):
        return None, ()
    return mask, _parse_ixml_tracks(ixml, channels) if channels > 0 else ()


def read_wave_layout_metadata(path: str, channels: int = 0
                              ) -> tuple[Optional[int], tuple[str, ...]]:
    try:
        stat = os.stat(path)
    except OSError:
        return None, ()
    mask, roles = _read_wave_layout_cached(
        os.path.normcase(os.path.normpath(path)), stat.st_size, stat.st_mtime_ns,
        int(channels or 0))
    if channels and len(roles) != channels:
        roles = ()
    return mask, roles


def read_wave_channel_mask(path: str) -> Optional[int]:
    return read_wave_layout_metadata(path)[0]


# 측면 스피커가 없는 배치 — 서라운드 한 쌍이 ±110 에 놓인다 (ITU-R BS.775 5.1 권고).
# 6.1 은 5.1 + 뒤 센터라 서라운드 조건이 5.1 과 같다. 7.x 는 측면(±90)·후방(±135)이 따로 있어 제외.
_NO_SIDE_PRESETS = {"quad", "5.0", "5.1", "6.1"}


def _roles_for_preset(roles: tuple[str, ...], preset: str) -> tuple[str, ...]:
    if preset in _NO_SIDE_PRESETS:
        roles = tuple("LB" if role == "LS" else "RB" if role == "RS" else role
                      for role in roles)
    expected = _PRESET_WAVE_ORDER.get(preset, ())
    return roles if len(roles) == len(expected) and set(roles) == set(expected) else ()


def _surround_layout(preset: str, confidence: str, order_names=None,
                     channel_order: str = "wave", source: str = "") -> BinauralLayout:
    roles = tuple(order_names or _PRESET_WAVE_ORDER[preset])
    azimuth = []
    elevation = []
    muted = []
    for role in roles:
        az, el = _BASE_ANGLES[role]
        if preset in _NO_SIDE_PRESETS and role in {"LB", "RB"}:
            az = 110.0 if role == "LB" else -110.0
        if role == "LFE":
            muted.append(len(azimuth))
        azimuth.append(az)
        elevation.append(el)
    return BinauralLayout(
        "surround", preset, confidence, tuple(azimuth), tuple(elevation),
        tuple(muted), channel_order=channel_order, source=source, channel_roles=roles)


def layout_channel_roles(layout: BinauralLayout) -> tuple[str, ...]:
    """판정 결과에서 **파일 채널 순서대로의 역할 이름**을 돌려준다.

    일반 재생 다운믹스(audio_engine)가 바이노럴과 **같은 판정**을 쓰도록 하는 통로다.
    표(_PRESET_WAVE_ORDER/_PRESET_FILM_ORDER)는 이 모듈이 정본이고, 쓰는 쪽에서
    복제하면 두 경로가 어긋난다.
    스피커 배치가 아니면(앰비소닉·판정 실패) 빈 튜플 — 호출자가 손대지 말라는 뜻이다.
    """
    if layout.topology != "surround" or not layout.preset:
        return ()
    if layout.channel_roles:
        return layout.channel_roles
    table = _PRESET_FILM_ORDER if layout.channel_order == "film" else _PRESET_WAVE_ORDER
    return tuple(table.get(layout.preset) or _PRESET_WAVE_ORDER.get(layout.preset) or ())


def _order_name(preset: str, roles: tuple[str, ...]) -> str:
    if roles == _PRESET_WAVE_ORDER.get(preset):
        return "wave"
    if roles == _PRESET_FILM_ORDER.get(preset):
        return "film"
    if roles == _PRESET_SMPTE_ORDER.get(preset):
        return "smpte"
    return "custom"


def resolve_layout(path: str, channels: int, override: str = "",
                   channel_mask: Optional[int] = None,
                   inspect_file: bool = True) -> BinauralLayout:
    if channels < 1:
        return BinauralLayout("unknown", "unknown", "none",
                              reason="채널 수가 올바르지 않습니다")

    forced_preset, forced_order = split_layout_override(override)
    if forced_preset in {"ambix", "fuma"}:
        order = round(math.sqrt(channels)) - 1
        if (order >= 0 and (order + 1) ** 2 == channels
                and (forced_preset != "fuma" or channels == 4)):
            return BinauralLayout(
                "ambisonic", forced_preset, "high", source_order=order,
                source="manual")
    if (_PRESET_CHANNELS.get(forced_preset) != channels):
        forced_preset = ""
        forced_order = ""
    if forced_preset and forced_order:
        roles = (_PRESET_WAVE_ORDER[forced_preset] if forced_order == "wave"
                 else (_PRESET_FILM_ORDER if forced_order == "film" else _PRESET_SMPTE_ORDER).get(forced_preset))
        if roles:
            return _surround_layout(
                forced_preset, "high", roles, forced_order, "manual")
        return BinauralLayout("unknown", "unknown", "none",
                              reason=f"{forced_preset}에서 {forced_order} 순서는 지원하지 않습니다",
                              candidates=(forced_preset,))

    filename = Path(path).name
    if channels == 4 and not forced_preset:
        exact = []
        if _AMBIX_TOKEN.search(filename):
            exact.append("ambix")
        if _FUMA_TOKEN.search(filename):
            exact.append("fuma")
        if _QUAD_TOKEN.search(filename):
            exact.append("quad")
        exact = list(dict.fromkeys(exact))
        if len(exact) > 1:
            return BinauralLayout(
                "unknown", "unknown", "low",
                reason="파일명에 서로 다른 4채널 형식이 함께 표시되어 있습니다",
                candidates=tuple(exact))
        if exact == ["ambix"]:
            return BinauralLayout(
                "ambisonic", "ambix", "medium", source_order=1,
                source="filename")
        if exact == ["fuma"]:
            return BinauralLayout(
                "ambisonic", "fuma", "medium", source_order=1,
                source="filename")
        if exact == ["quad"]:
            return _surround_layout("quad", "medium", source="filename")
        if _GENERIC_AMBI_TOKEN.search(filename):
            return BinauralLayout(
                "unknown", "unknown", "low",
                reason="앰비소닉 파일이지만 AmbiX와 FuMa 중 규격을 확인할 수 없습니다",
                candidates=("ambix", "fuma"))
        if _AMBEO_TOKEN.search(filename):
            return BinauralLayout(
                "unknown", "unknown", "low",
                reason="AMBEO A-format 원본일 수 있어 현재 방식으로 변환하지 않습니다",
                candidates=("quad", "ambix", "fuma"))

    mask = channel_mask
    ixml_roles = ()
    if inspect_file:
        file_mask, ixml_roles = read_wave_layout_metadata(path, channels)
        if not mask:
            mask = file_mask

    if forced_preset:
        roles = _roles_for_preset(ixml_roles, forced_preset)
        if roles:
            order_name = _order_name(forced_preset, roles)
            return _surround_layout(
                forced_preset, "high", roles, order_name, "ixml")
        return _surround_layout(forced_preset, "high", source="manual")

    if ixml_roles:
        matching = []
        for preset, count in _PRESET_CHANNELS.items():
            if count == channels:
                roles = _roles_for_preset(ixml_roles, preset)
                if roles:
                    matching.append((preset, roles))
        if len(matching) == 1:
            preset, roles = matching[0]
            order_name = _order_name(preset, roles)
            return _surround_layout(
                preset, "high", roles, order_name, "ixml")

    if mask and mask.bit_count() == channels:
        preset = _KNOWN_MASKS.get(mask)
        if preset:
            return _surround_layout(preset, "high", source="mask")
        return BinauralLayout(
            "unknown", "unknown", "none",
            reason=f"지원하지 않는 채널 배치(0x{mask:X})입니다")

    explicit_ambisonic = bool(
        _AMBIX_TOKEN.search(filename) or _GENERIC_AMBI_TOKEN.search(filename)
        or (channels == 9 and _SECOND_ORDER_AMBI_TOKEN.search(filename)))
    if explicit_ambisonic:
        order = round(math.sqrt(channels)) - 1
        if order >= 0 and (order + 1) ** 2 == channels:
            return BinauralLayout(
                "ambisonic", "ambix", "medium", source_order=order,
                source="filename")
        return BinauralLayout(
            "unknown", "unknown", "none",
            reason="앰비소닉 파일명과 채널 수가 맞지 않습니다")

    for token, preset in _SURROUND_TOKENS:
        if token.search(filename) and _PRESET_CHANNELS[preset] == channels:
            return _surround_layout(preset, "medium", source="filename")

    inferred_preset = _UNIQUE_SURROUND_BY_CHANNELS.get(channels)
    if inferred_preset:
        return _surround_layout(inferred_preset, "medium", source="channels")

    inferred_order = _HIGH_ORDER_AMBIX_BY_CHANNELS.get(channels)
    if inferred_order is not None:
        # 16·25·36 채널은 스피커 배치와 겹치지 않아 앰비소닉이 확정이고, 우리가 디코딩할 수
        # 있는 규격은 AmbiX 하나다 (FuMa 변환은 사이드카가 1차만 지원, 25·36은 FuMa 규격 자체가
        # 없음). 후보가 하나뿐인데 "확인 필요"로 멈춰 고르게 하던 것을 자동 확정으로 바꿨다
        # (사용자 결정 2026-09-18). 9채널은 7.0.2 와 겹치므로 여기 오지 않고 아래에서 묻는다.
        return BinauralLayout(
            "ambisonic", "ambix", "medium", source_order=inferred_order,
            source="channels")

    candidates = ("7.0.2", "ambix") if channels == 9 else ()
    return BinauralLayout(
        "unknown", "unknown", "low",
        reason=f"{channels}채널은 채널 배치 선택이 필요합니다",
        candidates=candidates)


def build_load_payload(path: str, layout: BinauralLayout, volume: float,
                       start_position_ms: int, start_playing: bool, channels: int = 0) -> dict:
    """사이드카 load 본문. 이산 surround는 1차 FOA를 사용한다."""
    db = sidecar_volume_db(volume, monitor_offset_db(layout, channels))
    payload = {
        "path": path,
        "startPosition": max(0, int(start_position_ms)) / 1000.0,
        "volumeDb": db,
        "startPlaying": bool(start_playing),
        "topology": layout.topology,
    }
    if layout.topology == "ambisonic":
        payload["sourceAmbisonicOrder"] = layout.source_order
        payload["sourceConvention"] = layout.preset
    else:
        payload["encodeOrder"] = SURROUND_ENCODE_ORDER
        payload["layout"] = {
            "azimuth": list(layout.azimuth),
            "elevation": list(layout.elevation),
            "mute": list(layout.muted),
        }
    return payload


class BinauralBackend(QObject):
    """JSONL 사이드카를 비동기로 제어하는 재생 백엔드."""

    loaded = pyqtSignal(int, int)          # session, duration_ms
    stateChanged = pyqtSignal(int, str)    # session, playing|paused|stopped
    ended = pyqtSignal(int)
    failed = pyqtSignal(int, str)
    layoutResolved = pyqtSignal(int, object)  # session, BinauralLayout
    availabilityChanged = pyqtSignal(bool, str)  # 사용 가능 여부, 사용자용 사유

    def __init__(self, parent=None, executable: Optional[str] = None):
        super().__init__(parent)
        self.executable = executable or locate_sidecar()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._commands: queue.Queue = queue.Queue()
        # _drop_repeats 의 한 칸 미리보기 — 큐에 되돌려 넣으면 순서가 뒤바뀐다
        self._peeked = None
        self._process: Optional[subprocess.Popen] = None
        self._ready_event = threading.Event()
        self._ready: Optional[dict] = None
        self._closing = False
        self._epoch = 0
        self._session = 0
        self._monitor_offset_db = 0.0   # 현재 파일 배치의 다운믹스 감쇠(dB) — _do_load 가 채움
        self._next_id = 0
        self._pending: dict[int, tuple[str, int]] = {}
        self._loaded = False
        self._loading = False
        self._state = "stopped"
        self._position_ms = 0
        self._duration_ms = 0
        self._desired_playing = False
        self._after_load = ""
        self._seek_after_load = False
        self._stderr_lines: list[str] = []
        self._worker = threading.Thread(target=self._worker_loop, daemon=True,
                                        name="SoundFieldBinaural")
        self._worker.start()

    @property
    def is_installed(self) -> bool:
        return bool(self.executable and Path(self.executable).is_file())

    def load(self, path: str, channels: int, volume: float, start_position_ms: int,
             start_playing: bool, override: str = "", channel_mask: Optional[int] = None) -> int:
        with self._lock:
            self._session += 1
            session = self._session
            self._loaded = False
            self._loading = True
            self._state = "stopped"
            self._position_ms = max(0, int(start_position_ms))
            self._duration_ms = 0
            self._desired_playing = bool(start_playing)
            self._after_load = ""
            self._seek_after_load = False
        self._commands.put(("load", session, path, int(channels), float(volume),
                            override, channel_mask))
        return session

    def warm_up(self, path: str, channels: int, override: str = "",
                channel_mask: Optional[int] = None) -> int:
        """첫 실제 ON 전에 프로세스·VST3를 무음으로 준비한다."""
        return self.load(path, channels, 0.0, 0, False, override, channel_mask)

    def play(self):
        with self._lock:
            self._desired_playing = True
            if self._loading:
                self._after_load = "play"
                return
            session = self._session
        self._commands.put(("simple", session, "play"))

    def pause(self):
        with self._lock:
            self._desired_playing = False
            self._state = "paused"
            if self._loading:
                self._after_load = "pause"
                return
            session = self._session
        self.stateChanged.emit(session, "paused")
        self._commands.put(("simple", session, "pause"))

    def stop(self):
        with self._lock:
            self._desired_playing = False
            self._state = "stopped"
            if self._loading:
                self._after_load = "stop"
                return
            session = self._session
        self.stateChanged.emit(session, "stopped")
        self._commands.put(("simple", session, "stop"))

    def seek(self, position_ms: int):
        with self._lock:
            self._position_ms = max(0, int(position_ms))
            if self._loading:
                self._seek_after_load = True
                return
            session = self._session
        self._commands.put(("seek", session, self._position_ms / 1000.0))

    def set_volume(self, volume: float):
        db = sidecar_volume_db(volume, self._monitor_offset_db)
        with self._lock:
            if not self._loaded:
                return
            session = self._session
        self._commands.put(("volume", session, db))

    def position_ms(self) -> int:
        with self._lock:
            return self._position_ms

    def duration_ms(self) -> int:
        with self._lock:
            return self._duration_ms

    def state(self) -> str:
        with self._lock:
            return self._state

    def wants_playback(self) -> bool:
        with self._lock:
            return self._desired_playing

    def is_loading(self) -> bool:
        with self._lock:
            return self._loading

    def is_loaded(self) -> bool:
        with self._lock:
            return self._loaded

    def _worker_loop(self):
        while True:
            if self._peeked is not None:
                command, self._peeked = self._peeked, None
            else:
                command = self._commands.get()
            command = self._drop_repeats(command)
            if command[0] == "close":
                self._shutdown_process()
                return
            try:
                if command[0] == "load":
                    self._do_load(*command[1:])
                elif command[0] == "simple":
                    _, session, kind = command
                    self._send_command(session, kind)
                elif command[0] == "seek":
                    _, session, seconds = command
                    self._send_command(session, "seek", sec=seconds)
                elif command[0] == "volume":
                    _, session, db = command
                    self._send_command(session, "volume", track=False, db=db)
            except Exception as exc:
                logger.exception("[binaural] 명령 처리 실패")
                self._fail(self._session, f"바이노럴 처리 오류: {exc}")

    def _drop_repeats(self, command):
        """큐 맨 앞에 **똑같은 명령**이 연달아 있으면 마지막 것만 남긴다.

        같은 동작을 두 번 보내면 모니터가 앞의 것을 "superseded" 로 거부하고 경고를
        남긴다. 실측 2026-09-07: 바이노럴을 켜고 재생할 때 restart 가 내부에서
        seek(0)+play 를 보내는데(playback.py restart), 화면 쪽에서 seek·play 를
        **한 번 더** 보내 같은 쌍이 두 번 갔다 — 로그에 ('seek', N)/('play', N)
        거부 경고가 9건 쌓였다. 똑같은 명령이므로 마지막 하나만 보내도 결과가 같고,
        거부 경고가 사라져 진짜 문제를 찾을 때 로그가 가려지지 않는다.

        ⚠ **완전히 같은 명령만** 합친다. 종류가 다르면(play 뒤 pause 등) 순서를
          바꾸면 결과가 달라지므로 손대지 않는다. load/close 도 합치지 않는다.
        ⚠ 다른 명령을 만나면 큐에 **되돌려 넣지 말 것** — Queue.put 은 맨 뒤에
          붙어서 뒤에 대기 중인 명령들과 순서가 뒤바뀐다. 한 칸 미리보기
          (_peeked) 에 담아 두고 다음 회차에 그것부터 처리한다.
        """
        if command[0] not in ("simple", "seek", "volume"):
            return command
        while True:
            try:
                nxt = self._commands.get_nowait()
            except queue.Empty:
                return command
            if nxt != command:
                self._peeked = nxt
                return command
            command = nxt

    def _do_load(self, session: int, path: str, channels: int, volume: float,
                 override: str, channel_mask: Optional[int]):
        layout = resolve_layout(path, channels, override, channel_mask)
        if not layout.can_auto_play:
            self._fail(session, f"채널 배치 선택 필요: {layout.reason}")
            return
        if not self._ensure_started():
            self._fail(session, self._ready_error())
            return

        ready = self._ready or {}
        capabilities = ready.get("capabilities") or {}
        # 지원 목록 검사는 앰비소닉에만 건다. 스피커 배치는 사이드카가 preset 이름을
        # 보지 않고 azimuth/elevation/mute 배열을 그대로 받으므로 목록에 없는 preset 도
        # 동작한다 — 사이드카 capabilities.layouts 에 6.1 이 없지만 실제 load 가 ack 됨을
        # 2026-09-16 확인했다(86개 파일). 사이드카를 다시 빌드할 때 목록에 6.1 을 추가할 것.
        if (layout.topology == "ambisonic"
                and layout.preset not in set(capabilities.get("layouts") or [])):
            self._fail(session, f"사이드카가 {layout.preset} 배치를 지원하지 않습니다")
            return

        with self._lock:
            if session != self._session:
                return
            start_playing = self._desired_playing
            start_position = self._position_ms / 1000.0
        # 볼륨 슬라이더가 바뀔 때(set_volume) 같은 보정을 쓰도록 배치별 오프셋을 기억한다
        self._monitor_offset_db = monitor_offset_db(layout, channels)
        extra = build_load_payload(path, layout, volume,
                                   int(round(start_position * 1000)), start_playing, channels)
        self.layoutResolved.emit(session, layout)
        if not self._send_command(session, "load", **extra):
            self._fail(session, "바이노럴 프로세스에 파일을 전달하지 못했습니다")

    def probe_availability(self) -> bool:
        """설치 상태를 **미리** 판정한다 (파일 없이).

        모니터 프로세스를 띄워 ready 응답을 받고 IEM 플러그인 유무/버전을 확인한 뒤
        availabilityChanged 를 내보낸다. 판정 결과를 그대로 돌려준다.

        왜 필요한가 (사용자 지시 2026-09-08):
          설치 판정(is_installed)은 **모니터 exe 파일이 있는지**만 본다. Tauri 판은
          그 exe 를 번들에 넣으므로 항상 참이라, IEM 플러그인이 없어도 시작 시점에는
          "사용 가능"으로 보였다. 실제 판정은 _ensure_started 안에서 일어나는데 그건
          **재생을 시도할 때만** 불려서, IEM 이 없는 PC 에서는 한 번 실패한 뒤에야
          설치 안내가 떴다 (원본은 exe 자체가 없어 처음부터 불가로 잡혔다).
          시작 직후 이 함수를 한 번 불러 두면 켜기 전에 이미 정확한 상태가 된다.

        ⚠ 프로세스를 띄우고 ready 를 최대 7초 기다린다 — **반드시 배경 스레드에서**
          부를 것. 여기서 띄운 프로세스는 그대로 재사용되므로 첫 바이노럴 켜기도
          빨라진다 (_ensure_started 가 살아 있는 프로세스를 그대로 쓴다).
        """
        try:
            return self._ensure_started()
        except Exception as exc:                                   # noqa: BLE001
            logger.warning("[binaural] 사전 판정 실패: %s", exc)
            return False

    def _ensure_started(self) -> bool:
        process = self._process
        if process is not None and process.poll() is None and self._ready is not None:
            available = not self._ready.get("error")
            self.availabilityChanged.emit(
                available, "" if available else self._ready_error())
            return available
        if not self.is_installed:
            self.availabilityChanged.emit(False, self._ready_error())
            return False

        self._ready_event.clear()
        self._ready = None
        self._epoch = (os.getpid() ^ threading.get_native_id() ^ int.from_bytes(os.urandom(4), "little")) & 0xFFFFFFFF
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                [self.executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=flags,
            )
        except OSError as exc:
            logger.warning("[binaural] 사이드카 시작 실패: %s", exc)
            return False
        threading.Thread(target=self._pump_stdout, daemon=True,
                         name="SoundFieldBinauralOut").start()
        threading.Thread(target=self._pump_stderr, daemon=True,
                         name="SoundFieldBinauralErr").start()
        self._send_command(0, "hello", track=False)
        if not self._ready_event.wait(7.0):
            self.availabilityChanged.emit(False, self._ready_error())
            return False
        ready = self._ready or {}
        if ready.get("error"):
            self.availabilityChanged.emit(False, self._ready_error())
            return False
        reported = {p.get("name"): p.get("version") for p in ready.get("plugins") or []}
        available = all(
            reported.get(name) == version for name, version in EXPECTED_PLUGINS.items())
        self.availabilityChanged.emit(
            available, "" if available else self._ready_error())
        return available

    def _next_command_id(self) -> int:
        with self._lock:
            self._next_id += 1
            return self._next_id

    def _send_command(self, session: int, kind: str, track: bool = True, **extra) -> bool:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            return False
        command_id = self._next_command_id()
        message = {"protocol": PROTOCOL_VERSION, "pe": self._epoch,
                   "session": int(session), "id": command_id, "type": kind}
        message.update(extra)
        try:
            line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
            if track:
                with self._lock:
                    self._pending[command_id] = (kind, session)
            with self._send_lock:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            return True
        except (OSError, ValueError) as exc:
            with self._lock:
                self._pending.pop(command_id, None)
            logger.warning("[binaural] 사이드카 전송 실패: %s", exc)
            return False

    def _pump_stdout(self):
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if self._closing:
                    return
                self._handle_line(line)
        except (OSError, ValueError):
            pass
        if not self._closing and process is self._process:
            self._fail(self._session, "바이노럴 프로세스가 종료되었습니다")

    def _pump_stderr(self):
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                text = line.rstrip()
                if not text:
                    continue
                logger.debug("[binaural-sidecar] %s", text)
                with self._lock:
                    self._stderr_lines.append(text)
                    if len(self._stderr_lines) > 200:
                        del self._stderr_lines[:50]
        except (OSError, ValueError):
            pass

    def _handle_line(self, line: str):
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("[binaural] 잘못된 응답: %s", line[:300])
            return
        if message.get("protocol") != PROTOCOL_VERSION or message.get("pe") != self._epoch:
            return
        kind = message.get("type")
        if kind == "ready":
            self._ready = message
            self._ready_event.set()
            return

        session = int(message.get("session") or 0)
        with self._lock:
            if session != self._session:
                return
        if kind == "ack":
            command_id = int(message.get("id") or 0)
            with self._lock:
                pending = self._pending.pop(command_id, None)
            if pending and pending[0] == "load":
                duration_ms = max(0, int(round(float(message.get("duration") or 0) * 1000)))
                with self._lock:
                    self._loading = False
                    self._loaded = True
                    self._duration_ms = duration_ms
                    after_load = self._after_load
                    self._after_load = ""
                    seek_after_load = self._seek_after_load
                    self._seek_after_load = False
                    seek_seconds = self._position_ms / 1000.0
                self.loaded.emit(session, duration_ms)
                if seek_after_load:
                    self._commands.put(("seek", session, seek_seconds))
                if after_load:
                    self._commands.put(("simple", session, after_load))
            elif pending and pending[0] in {"play", "pause", "stop"}:
                # 최초 load 재생에는 started 이벤트가 오지만, pause 뒤 resume은
                # play ACK만 오므로 이를 반영하지 않으면 UI가 Paused에 고정된다.
                state = {
                    "play": "playing",
                    "pause": "paused",
                    "stop": "stopped",
                }[pending[0]]
                with self._lock:
                    changed = self._state != state
                    self._state = state
                if changed:
                    self.stateChanged.emit(session, state)
            return
        if kind == "nack":
            command_id = int(message.get("id") or 0)
            with self._lock:
                pending = self._pending.pop(command_id, None)
            reason = str(message.get("reason") or "명령이 거부되었습니다")
            if pending and pending[0] == "load":
                self._fail(session, f"바이노럴 로드 실패: {reason}")
            else:
                logger.warning("[binaural] %s 명령 거부: %s", pending, reason)
            return
        if kind == "started":
            with self._lock:
                self._state = "playing"
            self.stateChanged.emit(session, "playing")
        elif kind == "pos":
            try:
                position = max(0, int(round(float(message.get("sec") or 0) * 1000)))
            except (TypeError, ValueError):
                return
            with self._lock:
                self._position_ms = position
        elif kind == "ended":
            with self._lock:
                self._state = "stopped"
                self._position_ms = 0
            self.stateChanged.emit(session, "stopped")
            self.ended.emit(session)
        elif kind == "error":
            self._fail(session, str(message.get("reason") or "바이노럴 장치 오류"))

    def _ready_error(self) -> str:
        return friendly_ready_error(
            self._ready, self.is_installed, self._ready_event.is_set())

    def _fail(self, session: int, reason: str):
        with self._lock:
            if session != self._session:
                return
            self._loading = False
            self._loaded = False
            self._state = "stopped"
        logger.warning("[binaural] %s", reason)
        self.failed.emit(session, reason)

    def _shutdown_process(self):
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                self._send_command(0, "shutdown", track=False)
                if process.stdin:
                    process.stdin.close()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
        except (OSError, ValueError):
            pass
        finally:
            if process is self._process:
                self._process = None

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._commands.put(("close",))
        self._worker.join(timeout=3.0)
