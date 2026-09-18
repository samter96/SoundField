"""HybridPlayer — QMediaPlayer 호환 facade.

soundfile 지원 포맷은 AudioEngine(클릭 없는 sounddevice 재생), 그 외 포맷(일부 MP3 등)은
QMediaPlayer 로 폴백. PlayerWidget 의 기존 self.player 호출부를 거의 그대로 쓰도록 QMediaPlayer
의 사용 메서드/시그널 부분집합을 동일 시그니처로 노출한다. playbackState/mediaStatus 는
QMediaPlayer 의 enum 을 그대로 반환하여 호출부의 비교문이 그대로 동작한다.

엔진 load 는 비동기 (2026-06-12):
- engine.load() 안의 sf.SoundFile(NAS 경로) open 이 UI 스레드를 막아 NAS 히컵 시
  UI 전체 프리즈 (freeze.log 12:34 실측). load 전체를 상주 워커 스레드로 분리.
- UI 쪽은 _load_pending 게이트 — 로드 중엔 엔진 API 를 직접 부르지 않고
  pending_play/pending_position 으로 기록해뒀다가 로드 완료 시그널에서 적용.
  (엔진 내부를 두 스레드가 동시에 만지는 race 방지. 엔진 코드는 무변경.)
- 요청 세대(gen): 빠른 연속 클릭 시 워커가 최신 요청만 실행, 늦게 도착한
  옛 결과는 무시. path="" 요청 = 엔진 정지 (QMediaPlayer 폴백 전환도 직렬화).
"""
import logging

from PyQt6.QtCore import QObject, QThread, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from app.audio_engine import AudioEngine, engine_can_play
from app.binaural import (BinauralBackend, is_binaural_candidate,
                          layout_channel_roles, resolve_layout)

logger = logging.getLogger(__name__)

PS = QMediaPlayer.PlaybackState
MS = QMediaPlayer.MediaStatus
_ENG_TO_PS = {"playing": PS.PlayingState, "paused": PS.PausedState, "stopped": PS.StoppedState}
_RELEASE_OUTPUT = "::soundfield-release-output::"


class _GenBox:
    """로드 요청 세대 — UI 가 올리고 워커가 읽음 (GIL 로 int 접근 원자적)."""
    __slots__ = ("value",)

    def __init__(self):
        self.value = 0


class _EngineLoadWorker(QObject):
    """엔진 load (NAS 파일 열기 포함) 전용 상주 워커 — UI 프리즈 방지.

    ⚠️ @pyqtSlot 필수 — 없으면 moveToThread 후에도 슬롯이 메인 스레드에서
    실행됨 (PyQt 함정, _SearchWorker 에서 실측으로 확인된 사례).
    """
    loaded = pyqtSignal(int, bool)   # (gen, ok) — 워커 → UI
    released = pyqtSignal(int)       # 바이노럴 전환용 장치 반납 완료

    def __init__(self, eng: AudioEngine, gen_box: _GenBox):
        super().__init__()
        self._eng = eng
        self._gen_box = gen_box

    @pyqtSlot(int, str, str, int)
    def do_load(self, gen: int, path: str, override: str = "", channels: int = 0):
        if gen != self._gen_box.value:
            return  # 더 새로운 요청이 큐에 대기 — 이 요청은 스킵
        if path == _RELEASE_OUTPUT:
            try:
                self._eng.release_source()
            except Exception:
                logger.exception("[playback] 일반 재생 소스 반납 실패")
            self.released.emit(gen)
            return
        if not path:
            # 엔진 정지 요청 (QMediaPlayer 폴백 포맷으로 전환 시).
            # UI 스레드가 아닌 여기서 실행 — 진행 중이던 load 와 직렬화.
            try:
                self._eng.stop()
            except Exception:
                logger.exception("[playback] 엔진 정지 실패")
            return
        # 다운믹스가 쓸 채널 역할을 **바이노럴과 같은 판정**으로 구해 넣는다.
        # 파일을 열어 보는 판정이라 UI 스레드가 아닌 여기(워커)에서 한다.
        try:
            roles = (layout_channel_roles(resolve_layout(path, int(channels or 0), override))
                     if channels and channels > 2 else ())
        except Exception:
            logger.exception("[playback] 채널 역할 판정 실패: %s", path)
            roles = ()
        self._eng.set_channel_roles(roles)
        try:
            ok = self._eng.load(path)
        except Exception:
            logger.exception("[playback] 비동기 load 실패: %s", path)
            ok = False
        self.loaded.emit(gen, ok)


class HybridPlayer(QObject):
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(object)   # QMediaPlayer.PlaybackState
    mediaStatusChanged = pyqtSignal(object)      # QMediaPlayer.MediaStatus
    binauralStatusChanged = pyqtSignal(str)      # 바이노럴 버튼 표시/툴팁용 한글 상태
    binauralAvailabilityChanged = pyqtSignal(bool, str)
    binauralLayoutResolved = pyqtSignal(object)

    _load_request = pyqtSignal(int, str, str, int)   # (gen, path, override, channels)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._qt = QMediaPlayer(self)
        self._qt_out = QAudioOutput(self)
        self._qt.setAudioOutput(self._qt_out)
        self._eng = AudioEngine(self)
        self._binaural = BinauralBackend(self)
        self._mode = "qt"      # 현재 파일 처리 엔진: 'engine' | 'qt' | 'binaural'
        self._path = ""
        self._media_meta = {}
        self._binaural_enabled = False
        self._binaural_session = 0
        self._binaural_layout = ""
        self._volume = 0.8
        self._rate = 1.0
        self._pending_position_ms = 0
        self._qt_out.setVolume(self._volume)
        self._eng.set_volume(self._volume)

        # 비동기 엔진 로드 상태 (UI 스레드에서만 읽고 씀)
        self._load_gen = _GenBox()
        self._load_pending = False   # 워커가 load 실행 중 — 엔진 API 직접 호출 금지
        self._pending_play = False   # 로드 완료 시 play() 호출 예약

        self._load_thread = QThread(self)
        self._load_worker = _EngineLoadWorker(self._eng, self._load_gen)
        self._load_worker.moveToThread(self._load_thread)
        # connect 는 moveToThread 이후 + 슬롯에 @pyqtSlot — 둘 다 지켜야
        # 워커 스레드에서 실행됨 (어느 한쪽만으론 메인 실행 사례 있음)
        self._load_request.connect(self._load_worker.do_load)
        self._load_worker.loaded.connect(self._on_engine_loaded)
        self._load_worker.released.connect(self._on_engine_released)
        self._load_thread.start()

        self._qt.durationChanged.connect(self._on_qt_duration)
        self._qt.playbackStateChanged.connect(self._on_qt_state)
        self._qt.mediaStatusChanged.connect(self._on_qt_status)
        self._eng.stateChanged.connect(self._on_eng_state)
        self._eng.ended.connect(self._on_eng_ended)
        self._binaural.loaded.connect(self._on_binaural_loaded)
        self._binaural.stateChanged.connect(self._on_binaural_state)
        self._binaural.ended.connect(self._on_binaural_ended)
        self._binaural.failed.connect(self._on_binaural_failed)
        self._binaural.layoutResolved.connect(self._on_binaural_layout)
        self._binaural.availabilityChanged.connect(self.binauralAvailabilityChanged.emit)

    # ── 시그널 중계 (활성 엔진 것만) ──
    def _on_qt_duration(self, ms):
        if self._mode == "qt":
            self.durationChanged.emit(ms)

    def _on_qt_state(self, st):
        if self._mode == "qt":
            self.playbackStateChanged.emit(st)

    def _on_qt_status(self, s):
        if self._mode == "qt":
            self.mediaStatusChanged.emit(s)

    def _on_eng_state(self, s):
        if self._mode == "engine":
            self.playbackStateChanged.emit(_ENG_TO_PS[s])

    def _on_eng_ended(self):
        if self._mode == "engine":
            self.mediaStatusChanged.emit(MS.EndOfMedia)

    def _on_binaural_loaded(self, session: int, duration_ms: int):
        if self._mode != "binaural" or session != self._binaural_session:
            return
        self._load_pending = False
        self.durationChanged.emit(duration_ms)
        self.mediaStatusChanged.emit(MS.BufferedMedia)
        self.binauralStatusChanged.emit(
            f"바이노럴 재생 중 · {self._binaural_layout or '배치 확인'}")

    def _on_binaural_state(self, session: int, state: str):
        if self._mode == "binaural" and session == self._binaural_session:
            self.playbackStateChanged.emit(_ENG_TO_PS[state])

    def _on_binaural_ended(self, session: int):
        if self._mode == "binaural" and session == self._binaural_session:
            self.mediaStatusChanged.emit(MS.EndOfMedia)

    def _on_binaural_layout(self, session: int, layout):
        if self._mode != "binaural" or session != self._binaural_session:
            return
        preset = getattr(layout, "preset", layout)
        if preset in {"ambix", "fuma"}:
            channels = int(self._media_meta.get("channels") or 0)
            order = round(channels ** 0.5) - 1
            convention = "FuMa" if preset == "fuma" else "AmbiX"
            self._binaural_layout = f"{order}차 {convention}"
        else:
            self._binaural_layout = f"{preset} · 1차 FOA"
        self.binauralLayoutResolved.emit(layout)
        self.binauralStatusChanged.emit(
            f"바이노럴 준비 중 · {self._binaural_layout}")

    def _on_binaural_failed(self, session: int, reason: str):
        if self._mode != "binaural" or session != self._binaural_session:
            return
        self._fallback_from_binaural(reason)

    # ── 비동기 로드 ──
    def _request_engine_load(self, path: str):
        self._load_gen.value += 1
        self._load_pending = True
        # 다운믹스 채널 역할 판정에 필요한 재료를 함께 넘긴다 (판정은 워커에서).
        try:
            channels = int(self._media_meta.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        override = str(self._media_meta.get("binaural_layout") or "")
        self._load_request.emit(self._load_gen.value, path, override, channels)

    def _request_engine_release(self):
        self._load_gen.value += 1
        self._load_pending = True
        self._load_request.emit(self._load_gen.value, _RELEASE_OUTPUT, "", 0)

    def _on_engine_released(self, gen: int):
        if gen != self._load_gen.value or self._mode != "binaural":
            return
        try:
            channels = int(self._media_meta.get("channels") or 0)
            mask = self._media_meta.get("channel_mask")
            mask = int(mask) if mask not in (None, "") else None
        except (TypeError, ValueError):
            channels, mask = 0, None
        self._binaural_session = self._binaural.load(
            self._path, channels, self._volume, self._pending_position_ms,
            self._pending_play,
            str(self._media_meta.get("binaural_layout") or ""), mask)
        self._pending_play = False

    def _on_engine_loaded(self, gen: int, ok: bool):
        """워커 load 완료 (UI 스레드 수신). 최신 세대만 반영."""
        if gen != self._load_gen.value:
            return  # 그 사이 새 파일 요청됨 — 옛 결과 폐기
        self._load_pending = False
        if not ok:
            logger.warning("[playback] engine load 실패 → QMediaPlayer 폴백: %s", self._path)
            self._mode = "qt"
            self._qt.setSource(QUrl.fromLocalFile(self._path))
            if self._pending_play:
                self._pending_play = False
                self._qt.play()
            return
        self._eng.set_volume(self._volume)
        self._eng.set_rate(self._rate)
        self.durationChanged.emit(self._eng.duration_ms())
        self._apply_pending_engine_position()
        if self._pending_play:
            self._pending_play = False
            self._eng.play()

    def _fallback_from_binaural(self, reason: str):
        if self._mode != "binaural":
            return
        position = self._binaural.position_ms()
        should_play = self._binaural.wants_playback() or self._pending_play
        self._binaural.stop()
        self._pending_position_ms = position
        self._pending_play = should_play
        self._load_pending = False
        self.binauralStatusChanged.emit(f"바이노럴 켜짐 · 기존 재생 사용: {reason}")
        logger.warning("[playback] 바이노럴 폴백: %s", reason)
        if engine_can_play(self._path):
            self._mode = "engine"
            self._eng.begin_switch()
            self._request_engine_load(self._path)
        else:
            self._mode = "qt"
            self._qt.setSource(QUrl.fromLocalFile(self._path))
            self._qt.setPosition(position)
            if should_play:
                self._qt.play()

    def _clamp_position_ms(self, ms: int) -> int:
        try:
            pos = int(ms)
        except (TypeError, ValueError):
            pos = 0
        pos = max(0, pos)
        if self._mode == "engine":
            # is_open: 생산자 종료 후(끝부분)에도 duration 은 유효
            dur = self._eng.duration_ms() if self._eng.is_open() else 0
        elif self._mode == "binaural":
            dur = self._binaural.duration_ms()
        else:
            dur = self._qt.duration()
        if dur > 0:
            pos = min(pos, dur)
        return pos

    def _apply_pending_engine_position(self):
        if self._pending_position_ms > 0 and self._eng.is_open():
            self._eng.seek(self._pending_position_ms)

    # ── QMediaPlayer 호환 API ──
    def setMediaMetadata(self, meta: dict = None):
        self._media_meta = dict(meta or {})

    def isBinauralInstalled(self) -> bool:
        """모니터 **exe 파일이 있는지**만 본다.

        ⚠ 이것만으로 "바이노럴을 쓸 수 있다" 고 판단하지 말 것. IEM 플러그인 유무는
          모니터의 ready 응답으로만 알 수 있다 (Tauri 판은 모니터 exe 를 번들에 넣어
          이 값이 항상 참이다). 정확한 상태는 probeBinauralAvailability() 로 받는다.
        """
        return self._binaural.is_installed

    def probeBinauralAvailability(self) -> bool:
        """설치 상태를 미리 판정한다 — binaural.probe_availability 주석 참고.
        ⚠ 최대 7초 걸리므로 배경 스레드에서 부를 것."""
        return self._binaural.probe_availability()

    def isBinauralActive(self) -> bool:
        return self._mode == "binaural"

    def reloadBinauralLayout(self, position_ms: int, should_play: bool) -> bool:
        """현재 바이노럴 소스를 끊지 않고 새 채널 배치로 교체한다."""
        if (self._mode != "binaural" or not self._path
                or not self._should_use_binaural(self._path)):
            return False

        position_ms = self._clamp_position_ms(position_ms)
        try:
            channels = int(self._media_meta.get("channels") or 0)
            mask = self._media_meta.get("channel_mask")
            mask = int(mask) if mask not in (None, "") else None
        except (TypeError, ValueError):
            channels, mask = 0, None

        self._pending_position_ms = position_ms
        self._pending_play = False
        self._binaural_layout = ""
        self._load_pending = True
        self.mediaStatusChanged.emit(MS.LoadingMedia)
        self.binauralStatusChanged.emit("바이노럴 준비 중 · 배치 확인")
        self._binaural_session = self._binaural.load(
            self._path, channels, self._volume, position_ms,
            bool(should_play),
            str(self._media_meta.get("binaural_layout") or ""), mask)
        logger.info(
            "[playback] route=binaural reload-layout path=%s position_ms=%d playing=%s",
            self._path, position_ms, bool(should_play))
        return True

    def setBinauralEnabled(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self._binaural_enabled:
            return

        # 현재 파일의 재생 위치/상태를 먼저 보존한다. OFF 전환은 기존에도
        # 즉시 일어났지만 ON 전환은 다음 setSource() 때까지 미뤄져서,
        # 같은 파일에서 OFF→ON을 비교하면 계속 일반 엔진 소리만 났다.
        position = self.position() if self._path else 0
        should_play = bool(
            self._pending_play or self.playbackState() == PS.PlayingState
        ) if self._path else False
        self._binaural_enabled = enabled

        if not enabled and self._mode == "binaural":
            self._fallback_from_binaural("사용자가 바이노럴을 껐습니다")
            logger.info("[playback] route=engine toggle=off path=%s position_ms=%d",
                        self._path, position)
            self.binauralStatusChanged.emit("바이노럴 꺼짐")
            return

        if enabled and self._path and self._should_use_binaural(self._path):
            path = self._path
            logger.info("[playback] route=binaural toggle=on path=%s position_ms=%d playing=%s",
                        path, position, should_play)
            self.setSource(QUrl.fromLocalFile(path))
            # setSource()는 새 파일 선택을 전제로 pending 값을 초기화한다.
            # 토글 전환에서는 같은 파일이므로 캡처한 값을 다시 넘긴다.
            self._pending_position_ms = position
            self._pending_play = should_play
            return

        if enabled and self._path:
            status = "바이노럴 켜짐 · 현재 파일은 변환 없이 재생"
        elif enabled:
            status = "바이노럴 켜짐 · 멀티채널 대기"
        else:
            status = "바이노럴 꺼짐"
        self.binauralStatusChanged.emit(status)

    def _should_use_binaural(self, path: str) -> bool:
        return (self._binaural_enabled and self._binaural.is_installed
                and abs(self._rate - 1.0) < 0.005
                and is_binaural_candidate(path, self._media_meta))

    def setSource(self, url):
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        if self._mode == "binaural":
            self._binaural.stop()
        self._path = path
        self._pending_position_ms = 0
        self._pending_play = False
        self._binaural_layout = ""
        if self._should_use_binaural(path):
            logger.info("[playback] route=binaural source=%s", path)
            self._qt.stop()
            self._mode = "binaural"
            self._eng.begin_switch()
            self.mediaStatusChanged.emit(MS.LoadingMedia)
            self.binauralStatusChanged.emit("바이노럴 준비 중 · 배치 확인")
            self._request_engine_release()
            return
        if engine_can_play(path):
            logger.info("[playback] route=engine source=%s binaural_enabled=%s",
                        path, self._binaural_enabled)
            self._qt.stop()
            self._mode = "engine"
            self._eng.begin_switch()
            self._request_engine_load(path)
            if self._binaural_enabled:
                self.binauralStatusChanged.emit(
                    "바이노럴 켜짐 · 현재 파일은 변환 없이 재생")
            return
        # QMediaPlayer 폴백 — 엔진 정지도 워커로 (진행 중 load 와 직렬화)
        self._load_gen.value += 1
        self._load_pending = False
        self._eng.begin_switch()
        self._load_request.emit(self._load_gen.value, "", "", 0)
        self._mode = "qt"
        self._qt.setSource(url if isinstance(url, QUrl) else QUrl.fromLocalFile(path))
        if self._binaural_enabled:
            self.binauralStatusChanged.emit(
                "바이노럴 켜짐 · 현재 파일은 변환 없이 재생")

    def play(self):
        if self._mode == "binaural":
            if self._load_pending:
                self._pending_play = True
                return
            self._binaural.play()
        elif self._mode == "engine":
            if self._load_pending:
                self._pending_play = True
                return
            # is_open 기준 — 파일이 열려 있으면 엔진이 처리 (생산자는 엔진이 되살림).
            # 닫혀 있을 때(자연종료 teardown 후)만 비동기 재로드.
            if not self._eng.is_open():
                if not self._path:
                    return
                self._pending_play = True
                self._request_engine_load(self._path)
                return
            self._eng.play()
        else:
            self._qt.play()

    def restart(self):
        self._pending_position_ms = 0
        if self._mode == "binaural":
            if self._load_pending:
                self._pending_play = True
                return
            self._binaural.seek(0)
            self._binaural.play()
        elif self._mode == "engine":
            if self._load_pending:
                self._pending_play = True
                return
            if not self._eng.is_open():
                if not self._path:
                    return
                self._pending_play = True
                self._request_engine_load(self._path)
                return
            self._eng.restart()
        else:
            self._qt.setPosition(0)
            self._qt.play()

    def pause(self):
        if self._mode == "binaural":
            if self._load_pending:
                self._pending_play = False
            self._binaural.pause()
        elif self._mode == "engine":
            if self._load_pending:
                self._pending_play = False
                return
            self._eng.pause()
        else:
            self._qt.pause()

    def stop(self):
        if self._mode == "binaural":
            self._pending_play = False
            self._binaural.stop()
        elif self._mode == "engine":
            self._pending_play = False
            if self._load_pending:
                return  # 로드 완료돼도 재생 안 함 — 정지 상태로 끝
            self._eng.stop()
        else:
            self._qt.stop()

    def setPosition(self, ms: int):
        ms = self._clamp_position_ms(ms)
        self._pending_position_ms = ms
        if self._mode == "binaural":
            self._binaural.seek(ms)
        elif self._mode == "engine":
            # is_open 기준 — is_loaded(생산자 생존) 로 게이트하면 파일 끝 ~2초
            # 구간(생산자 종료 후)에서 seek 이 엔진에 전달조차 안 됨 (세그먼트
            # 헤더/파형 클릭 무반응의 실제 원인). 생산자는 eng.seek 이 되살림.
            if not self._load_pending and self._eng.is_open():
                self._eng.seek(ms)
        else:
            self._qt.setPosition(ms)

    def position(self) -> int:
        if self._mode == "binaural":
            return self._pending_position_ms if self._load_pending else self._binaural.position_ms()
        if self._mode == "engine":
            if self._load_pending:
                return self._pending_position_ms
            return self._eng.position_ms()
        return self._qt.position()

    def playbackState(self):
        if self._mode == "binaural":
            return _ENG_TO_PS[self._binaural.state()]
        return _ENG_TO_PS[self._eng.state()] if self._mode == "engine" else self._qt.playbackState()

    def mediaStatus(self):
        if self._mode == "binaural":
            if self._load_pending or self._binaural.is_loading():
                return MS.LoadingMedia
            return MS.BufferedMedia if self._binaural.is_loaded() else MS.NoMedia
        if self._mode == "engine":
            if self._load_pending:
                # 로드 중 — PlayerWidget 의 load watchdog(8s) 이 NAS 멈춤을 감지 가능
                return MS.LoadingMedia
            return MS.BufferedMedia if self._eng.is_loaded() else MS.NoMedia
        return self._qt.mediaStatus()

    def setPlaybackRate(self, r: float):
        previous_rate = self._rate
        self._rate = r
        if self._mode == "binaural" and abs(r - 1.0) >= 0.005:
            self._fallback_from_binaural("배속 재생은 기존 방식으로 처리합니다")
        self._eng.set_rate(r)
        self._qt.setPlaybackRate(r)
        if (abs(previous_rate - 1.0) >= 0.005 and abs(r - 1.0) < 0.005
                and self._mode != "binaural" and self._path
                and self._should_use_binaural(self._path)):
            position = self.position()
            should_play = self._pending_play or self.playbackState() == PS.PlayingState
            self.setSource(QUrl.fromLocalFile(self._path))
            self._pending_position_ms = position
            self._pending_play = bool(should_play)

    def setVolume(self, v: float):
        # 엔진은 +6.02 dB(게인 2.0)까지 부스트 허용. QMediaPlayer 폴백은 부스트 불가라 1.0 cap.
        self._volume = max(0.0, min(2.0, v))
        self._eng.set_volume(self._volume)
        self._qt_out.setVolume(min(1.0, self._volume))
        self._binaural.set_volume(self._volume)

    def close(self):
        # 진행 중 로드 무효화 후 워커 종료 대기 (NAS 멈춤 시 최대 2초)
        self._load_gen.value += 1
        self._load_thread.quit()
        self._load_thread.wait(2000)
        self._binaural.close()
        self._eng.close()
        self._qt.stop()
