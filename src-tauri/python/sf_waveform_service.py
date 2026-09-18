"""파형 서비스 — 상주 프로세스. stdin 한 줄 = 요청 JSON, stdout 한 줄 = 응답 JSON.

sf_waveform.py 를 매 요청마다 새로 띄우면 player_widget import 만 1초씩 든다
(PyQt6 + numpy). 파일을 연이어 클릭할 때 매번 그 부담을 물지 않게 상주로 둔다.
추출 자체는 원본 함수를 그대로 쓴다 — 정책/캐시가 원본과 같아야 한다.

요청
  {"id": 1, "command": "peaks", "path": "..."}
  {"id": 3, "command": "quit"}
응답
  {"id": 1, "ok": true, "data": {...}} | {"id": 1, "ok": false, "error": "..."}

peaks.data = {channels, sample_rate, n_samples, segments:[[s,e]...],
              levels:[{slices, data}]}   data = int16 base64, (slices, ch, 2) C-order
가로 줌 폐지로 levels 는 1개다 (원본 LEVEL_SLICES 단일 항목). 샘플 단위 raw 창
읽기 명령("raw")도 함께 제거했다 — 그 확대 단계가 없어졌다.
float 를 그대로 JSON 에 넣으면 수 MB 가 되므로 int16(x32767) + base64 로 보낸다.
"""

import base64
import json
import math
import os
import sys
import threading
from pathlib import Path

import numpy as np

# stdout 은 기본적으로 콘솔 코드페이지(cp949)라 ©, 유럽어 문자, 이모지가 섞인 경로에서
# UnicodeEncodeError 로 죽는다 (실측: 검색 결과 전송 중 '©' 에서 실패).
# 브리지 프로토콜은 UTF-8 JSON 이므로 명시적으로 재설정한다.
try:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8")
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
    PY_ROOT = Path(_EXPLICIT_ROOT)
elif getattr(sys, "frozen", False):
    PY_ROOT = Path(getattr(sys, "_MEIPASS", "."))
else:
    PY_ROOT = Path(__file__).resolve().parents[2] / "py"
if not getattr(sys, "frozen", False) or _EXPLICIT_ROOT:
    if str(PY_ROOT) not in sys.path:
        sys.path.insert(0, str(PY_ROOT))

from app.ui.player_widget import (  # noqa: E402
    WAVEFORM_QUICK_MIN_BYTES,
    cleanup_stale_peaks_cache,
    _extract_peaks,
    _extract_peaks_sparse,
    _load_cached_peaks,
    _load_quick_peaks,
    _save_cached_peaks,
    _save_quick_peaks,
)
from app.binaural import (  # noqa: E402
    _PRESET_FILM_ORDER, _PRESET_WAVE_ORDER, _PRESET_SMPTE_ORDER, read_wave_channel_mask, resolve_layout)
from app.region_export import crop_wav_region, render_speed_wav  # noqa: E402

# 최근 파일 하나의 추출 결과만 붙잡아 둔다 (원본도 위젯이 현재 파일만 들고 있다).
# ⚠ 경로만으로 판정하지 말 것 (조사 Q15). 같은 경로의 파일이 바뀌면 옛 파형을
#   돌려주게 된다 — 원본은 디스크 캐시 이름에 크기·수정시각을 넣어 걸러낸다.
_last = {"key": None, "value": None}


def _stamp(path: str) -> str:
    """파일의 크기·수정시각. 못 읽으면 빈 문자열 (그때는 캐시를 쓰지 않는다)."""
    try:
        st = os.stat(path)
        return "%d|%.3f" % (st.st_size, st.st_mtime)
    except OSError:
        return ""


def _to_i16_b64(arr) -> str:
    a = np.ascontiguousarray(arr, dtype=np.float32)
    scaled = np.clip(a * 32767.0, -32768.0, 32767.0).astype(np.int16)
    return base64.b64encode(scaled.tobytes()).decode("ascii")


def _peaks(path: str) -> dict:
    stamp = _stamp(path)
    key = (path, stamp) if stamp else None
    if key is not None and _last["key"] == key and _last["value"] is not None:
        ch, sr, n_samples, peak_levels, segments = _last["value"]
    else:
        cached = _load_cached_peaks(path)
        if cached is None:
            ch, sr, n_samples, peak_levels, segments = _extract_peaks(path, lambda: True)
            # 원본과 같은 peaks_cache 에 기록 -> 원본 앱에서도 재사용된다.
            _save_cached_peaks(path, ch, sr, n_samples, peak_levels, segments)
        else:
            ch, sr, n_samples, peak_levels, segments = cached
        _last["key"] = key
        _last["value"] = (ch, sr, n_samples, peak_levels, segments)

    levels = []
    for level in (peak_levels or []):
        shape = getattr(level, "shape", None)
        if not shape or shape[0] <= 0:
            continue
        levels.append({"slices": int(shape[0]), "data": _to_i16_b64(level)})

    seg_list = []
    if getattr(segments, "size", 0):
        seg_list = [[float(a), float(b)]
                    for a, b in np.asarray(segments).reshape(-1, 2)]

    return {
        "channels": int(ch),
        "sample_rate": int(sr),
        "n_samples": int(n_samples),
        "segments": seg_list,
        "levels": levels,
    }


def _pack(ch, sr, n_samples, peak_levels, segments) -> dict:
    """peaks/quick 공통 직렬화 — 원본 PeaksArray (slices, ch, 2) 를 int16+base64 로."""
    levels = []
    for level in (peak_levels or []):
        shape = getattr(level, "shape", None)
        if not shape or shape[0] <= 0:
            continue
        levels.append({"slices": int(shape[0]), "data": _to_i16_b64(level)})
    seg_list = []
    if getattr(segments, "size", 0):
        seg_list = [[float(a), float(b)]
                    for a, b in np.asarray(segments).reshape(-1, 2)]
    return {
        "channels": int(ch),
        "sample_rate": int(sr),
        "n_samples": int(n_samples),
        "segments": seg_list,
        "levels": levels,
    }


def _quick(path: str) -> dict:
    """빠른 러프 파형 (원본 WaveformQuickRunnable, player_widget.py:640).
    원본 정책 그대로:
      · **.wav 이고 30MB 이상**일 때만 쓴다 (WAVEFORM_QUICK_MIN_BYTES).
        작은 파일은 full decode 가 이미 충분히 빠르므로 건너뛴다.
      · quick 캐시가 있으면 그걸 쓰고, 없으면 _extract_peaks_sparse (16 슬라이스)
        로 뽑아 quick 캐시에 저장한다. full read 는 절대 하지 않는다.
    건너뛸 상황이면 {"skipped": true} 만 돌려준다."""
    if os.path.splitext(path)[1].lower() != ".wav":
        return {"skipped": True}
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"skipped": True}
    if size < WAVEFORM_QUICK_MIN_BYTES:
        return {"skipped": True}
    cached = _load_quick_peaks(path)
    if cached is not None:
        ch, sr, nf, levels, segs = cached
        return _pack(ch, sr, nf, levels, segs)
    ch, sr, nf, levels, segs = _extract_peaks_sparse(path)
    try:
        _save_quick_peaks(path, ch, sr, nf, levels[0])
    except Exception:
        pass   # 캐시 저장 실패는 표시에 영향 없음 (원본도 무시한다)
    return _pack(ch, sr, nf, levels, segs)


# 내부 역할 이름(LB/RB/LS/RS)은 프리셋에 따라 가리키는 스피커가 다르다.
# 5.x 는 LB/RB 가 ±110 서라운드(Ls/Rs), 7.x 는 LB/RB 가 ±135 후방(Lsr/Rsr)이고
# LS/RS 가 ±90 측면(Lss/Rss)이다. 사용자에게는 이 이름으로 보여 줘야 한다.
_FIVE_POINT = {"quad", "5.0", "5.1", "6.1"}   # 6.1 도 서라운드 쌍은 5.1 과 같은 Ls/Rs
_DISPLAY_ROLE = {"BC": "Cs", "TFL": "Ltf", "TFR": "Rtf"}

# wave(WAV 컨테이너 규격)와 SMPTE 는 5.1 까지만 같다. 7.1 부터는 측면/후방 순서가
# 뒤집혀 다르다 (Avid KB "Working with SMPTE-ordered multichannel files in Pro Tools").
# 그래서 7.x 를 SMPTE 라고 부르면 안 된다.
_SMPTE_EQUALS_WAVE = {"lcr", "quad", "5.0", "5.1"}


def _display_roles(preset: str, roles) -> list:
    out = []
    for role in roles:
        if role in {"LB", "RB"}:
            rear = role == "LB"
            out.append(("Ls" if rear else "Rs") if preset in _FIVE_POINT
                       else ("Lsr" if rear else "Rsr"))
        elif role in {"LS", "RS"}:
            out.append("Lss" if role == "LS" else "Rss")
        else:
            out.append(_DISPLAY_ROLE.get(role, role))
    return out


def _order_choices(preset: str) -> list:
    """이 프리셋이 가질 수 있는 채널 순서들 — 팝업 문구의 재료.
    두 순서가 실제로 다를 때만 돌려준다 (quad·6.1 은 film 규약이 없다)."""
    wave = _PRESET_WAVE_ORDER.get(preset, ())
    film = _PRESET_FILM_ORDER.get(preset, ())
    if not wave or not film or wave == film:
        return []
    choices = [
        {"order": "wave", "roles": _display_roles(preset, wave),
         "name": "SMPTE" if preset in _SMPTE_EQUALS_WAVE else "WAV 규격"},
        {"order": "film", "roles": _display_roles(preset, film), "name": "Film"},
    ]

    if preset in _PRESET_SMPTE_ORDER:
        choices.append({"order": "smpte", "roles": _display_roles(preset, _PRESET_SMPTE_ORDER[preset]), "name": "SMPTE"})
    return choices


def _layout(path: str, channels: int, override: str) -> dict:
    """바이노럴 채널 배치 판정 — 원본 app.binaural.resolve_layout 을 그대로 호출한다.
    원본 메뉴(_build_binaural_layout_menu)가 쓰는 값만 추려 돌려준다:
      automatic_preset / can_auto_play / candidates / reason
    (원본은 채널 마스크도 읽어 판정에 쓴다 — read_wave_channel_mask)"""
    mask = None
    try:
        mask = read_wave_channel_mask(path)
    except Exception:
        mask = None
    auto = resolve_layout(path, int(channels or 0), "", mask or 0)
    applied = resolve_layout(path, int(channels or 0), override or "", mask or 0)
    return {
        "channels": int(channels or 0),
        "channel_mask": int(mask or 0),
        "automatic_preset": auto.preset,
        "automatic_topology": auto.topology,
        # BINAURAL_CHANNEL_ORDER_HANDOVER 4.1 이 요구하는 필드.
        # 채널 **수**와 채널 **순서**는 다른 정보다 (5.1 도 wave/film 두 순서가 있다).
        # 이 값이 없으면 UI 가 "현재 방식"을 항상 wave 로 가정해, iXML/마스크로
        # film 이 판정된 파일에서 A/B 비교의 기준이 뒤바뀐다.
        "automatic_channel_order": str(getattr(auto, "channel_order", "") or ""),
        "automatic_source": str(getattr(auto, "source", "") or ""),
        "can_auto_play": bool(auto.can_auto_play),
        "candidates": sorted(auto.candidates or ()),
        "reason": auto.reason or "",
        "applied_preset": applied.preset,
        "applied_can_play": bool(applied.can_auto_play),
        # 원본 _update_binaural_layout_control 이 쓰는 값들 — 적용된 판정 기준으로
        # 툴팁("채널 배치: {모드} · {프리셋}")과 상세 문구를 만든다.
        "applied_topology": applied.topology,
        "applied_channel_order": str(getattr(applied, "channel_order", "") or ""),
        "applied_source": str(getattr(applied, "source", "") or ""),
        "applied_order": int(getattr(applied, "source_order", 0) or 0),
        "applied_reason": applied.reason or "",
        # 팝업이 "SMPTE 방식으로 듣기 (L R C Ls Rs)" 처럼 실제 순서를 밝히도록
        # 프리셋의 두 순서를 그대로 내려 보낸다. 테이블은 app.binaural 이 정본이다.
        "order_choices": _order_choices(applied.preset or auto.preset),
    }


def _respond(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _region(path: str, start_sec, end_sec, rate) -> dict:
    r"""DAW 로 끌어낼 파일 경로를 만든다 — 원본 _resolve_export_path 규칙 그대로.
      1) 영역(세그먼트 헤더 드래그 범위 또는 사용자 선택)이 있으면 crop_wav_region
         으로 잘라 임시 WAV 를 만든다 (bext/iXML/cue 청크 보존).
         crop 실패 시 오류를 반환하고 드래그를 중단한다.
      2) 배속이 1.0x 가 아니면 그 결과에 render_speed_wav 로 배리스피드를 렌더한다
         (`이름_0.45x.wav`) — 누엔도가 1.0x 원본과 다른 파일로 인식하게.
         렌더 실패 시 오류를 반환한다.
    임시 파일은 원본과 같은 %TEMP%\SoundField_regions 에 쌓이고 24시간 뒤 정리된다."""
    export = path
    if start_sec is not None or end_sec is not None:
        if start_sec is None or end_sec is None:
            raise ValueError("선택 구간의 시작과 끝이 필요합니다")
        start, end = float(start_sec), float(end_sec)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("선택 구간이 올바르지 않습니다")
        cropped = crop_wav_region(path, start, end)
        if not cropped:
            # 프런트가 "내보내기를 중단했습니다: " 를 앞에 붙이므로 여기선 원인만 적는다
            raise RuntimeError("선택 구간을 잘라내지 못했습니다")
        export = cropped
    rate = float(1.0 if rate is None else rate)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("배속 값이 올바르지 않습니다")
    if abs(rate - 1.0) >= 0.005:
        rendered = render_speed_wav(export, rate)
        if not rendered:
            raise RuntimeError("배속 파일을 만들지 못했습니다 (자세한 원인은 로그의 [speed] 항목)")
        export = rendered
    return {"path": export}


def main():
    # 파형 캐시 세대 정리 — 원본 main.py 가 시작 시 백그라운드로 돌리는 것과 같은 일.
    # PoC 만 쓰는 사용자는 이걸 호출할 곳이 없어서, 캐시 포맷이 바뀌어도(v8 → v9)
    # 옛 세대 .bin 이 영구히 남는다 (실측 17,875개 / 17.58GB). 여기서 대신 돌린다.
    # 데몬 스레드 — 수만 개 삭제가 첫 파형 응답을 막지 않게 한다 (원본과 동일).
    threading.Thread(target=cleanup_stale_peaks_cache,
                     daemon=True, name="peaks-cache-gc").start()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            _respond({"id": None, "ok": False, "error": f"JSON 파싱 실패: {exc}"})
            continue
        rid = req.get("id")
        command = (req.get("command") or "").lower()
        if command == "quit":
            _respond({"id": rid, "ok": True, "data": {}})
            return
        try:
            path = req.get("path") or ""
            if not path:
                raise ValueError("path required")
            if command == "quick":
                data = _quick(path)
            elif command == "region":
                data = _region(path, req.get("start_sec"), req.get("end_sec"),
                               req.get("rate"))
            elif command == "layout":
                data = _layout(path, req.get("channels") or 0, req.get("override") or "")
            else:
                data = _peaks(path)
            _respond({"id": rid, "ok": True, "data": data})
        except Exception as exc:
            _respond({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
