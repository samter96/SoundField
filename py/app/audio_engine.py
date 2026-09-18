"""sounddevice 기반 재생 엔진 — QMediaPlayer 대체 (클릭/팝 제거 + 네이티브 스트리밍).

설계 요점:
- 출력: WASAPI 공유 + auto_convert (기본 장치, 저지연). 실패 시 MME 폴백.
- NAS 언더런 방지: 오디오 콜백은 NAS 를 절대 안 읽는다. 생산자 스레드가 soundfile 로
  블록을 읽어 큐(링버퍼)에 채우고, 콜백은 큐에서만 꺼낸다. 큐가 비면 무음 출력하고
  위치는 전진 안 함.
- 클릭 제거: 콜백이 per-sample 게인 램프(current→target)를 적용.
    · 시작/재개 = 0→볼륨 페이드인
    · 일시정지/정지 = 볼륨→0 페이드아웃 후 '동결'(consume·전진 중단)
    · seek = 볼륨→0 페이드아웃 + 큐 플러시·탐색 + (약 45ms 뒤) 0→볼륨 페이드인
- 버리스피드: rate!=1.0 이면 생산자에서 선형보간 리샘플(음정도 변함). rate==1.0 패스스루.
- 위치: 콜백이 실제 출력한 블록의 프레임만 source 위치로 환산 → 들리는 소리와 동기.

QMediaPlayer 가 못 여는 포맷(일부 MP3/특수코덱)은 이 엔진을 안 쓴다(상위에서 폴백).
"""
import logging
import math
import os
import queue
import threading
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .downmix import DOWNMIX_L, DOWNMIX_R, norm_gain

logger = logging.getLogger(__name__)

BLOCKSIZE = 1024          # 콜백당 출력 프레임
QUEUE_MAX = 96            # 링버퍼 깊이(블록) — 44.1k≈2.2s/48k≈2.0s/96k≈1.0s 쿠션.
                          # 폴더 트리 갱신 등 UI 스레드(GIL)+NAS read 가 ~1s 점유해도
                          # 생산자가 미리 쌓아둔 큐로 콜백 언더런(무음 버벅) 방지.
                          # 스킵 시 최대 이 깊이만큼 선행 read (메모리 ~0.8MB, 무시 가능).
START_PREBUFFER_BLOCKS = 5
RAMP_MS = 18.0            # 게인 램프 시간(시작/seek 팝 방지)
START_RAMP_MS = 18.0
START_RAMP_HEAD_MS = 0.0  # 파일 머리(0프레임)는 원음 보존을 위해 자동 페이드인하지 않는다.
                          # 18ms 램프는 UI클릭 등 트랜지언트(에너지가 첫 수 ms 집중)를
                          # 최대 -17dB 깎아 DAW/foobar 대비 작게 들리게 했음 (실측).
                          # 머리는 자연스럽게 무음/저레벨에서 시작하므로 팝 위험 없음.
SEEK_FADE_MS = 5.0
STREAM_WARMUP_MS = 90.0   # 새로 열린 출력 스트림 안정화 대기
SWITCH_FADE_MS = 55.0     # 다른 파일로 바꾸기 전 장치 버퍼를 무음으로 비움
OUTPUT_LATENCY_SEC = 0.055
STREAM_RETIRE_DELAY_SEC = 0.35
OUT_CHANNELS = 2          # 항상 스테레오 출력(모노 복제, 다채널은 앞 2채널)

# ── 다채널 → 스테레오 다운믹스 — 계수·레벨 규칙은 downmix.py 가 정본 (ffmpeg 와 동일).
# 여기서는 표를 가져다 쓰기만 한다. 바이노럴 음량 보정(binaural.py)도 같은 표를 본다.
# 2026-09-16 이전의 "-4 dB 고정 + tanh 리미터" 는 표준이 아니어서 폐기했다.

# soundfile(libsndfile) 가 여는 포맷만 이 엔진 담당. 그 외는 폴백.
SUPPORTED_EXTS = {".wav", ".flac", ".aif", ".aiff", ".ogg", ".oga", ".w64", ".rf64"}


def engine_can_play(path: str) -> bool:
    """이 엔진(soundfile)으로 열 수 있을 가능성이 높은 포맷인지(확장자 1차 판정)."""
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXTS


class AudioEngine(QObject):
    """위치/상태는 폴링(메서드)로 노출. 자연종료/상태전이만 Qt 스레드 타이머로 emit
    (콜백 스레드에서 직접 Qt emit 금지)."""

    ended = pyqtSignal()                 # 자연 EOF (loop 아닐 때)
    stateChanged = pyqtSignal(str)       # 'playing' | 'paused' | 'stopped'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream: Optional[sd.OutputStream] = None
        self._samplerate = 0
        self._stream_gen = 0

        self._sf: Optional[sf.SoundFile] = None
        self._sf_lock = threading.Lock()

        self._q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        self._producer: Optional[threading.Thread] = None
        self._producer_stop = threading.Event()
        self._seek_req: Optional[int] = None
        self._seek_lock = threading.Lock()
        self._eof = False
        self._gen = 0

        # 콜백 공유 상태 (GIL 하 단순 대입은 안전)
        self._cur_gain = 0.0
        self._target_gain = 0.0
        self._switch_silent = threading.Event()
        self._switch_silent.set()
        self._user_volume = 0.8
        self._stream_warm_until = 0.0
        self._last_callback_status_log = 0.0
        self._rate = 1.0
        self._played_src = 0.0     # seek 기준점 이후 출력한 source 프레임 누적
        self._seek_base = 0        # 현재 구간 시작 source 프레임
        self._state = "stopped"
        self._switching = False
        self._drained = False      # 큐 소진 + EOF (콜백이 set)
        self._frozen = False       # 콜백 무음 + consume·전진 중단 (일시정지/정지/seek대기)
        self._after = None         # 게인 0 도달 시 동작: 'pause' | 'stop' | 'seek'
        self._stop_ready = False   # 콜백이 정지 페이드 완료 표시 → poll 이 teardown
        # seek 핸드셰이크: 페이드아웃(콜백)→게인0→생산자 플러시·탐색→재충전→페이드인.
        self._pending_seek = None  # seek() 가 저장, 콜백이 게인0 에서 생산자로 넘김
        self._seek_wait = False    # 콜백 동결 + 생산자 탐색 완료 대기 중
        self._seek_done = False    # 생산자가 플러시·탐색 끝냄 → 콜백 페이드인 재개

        # loop
        self._loop = False
        self._loop_start = 0
        self._loop_end = 0

        # 메타
        self._total_frames = 0
        self._file_sr = 0
        self._path = ""
        # 다운믹스용 채널 역할("L","C","LFE"…). 파일마다 load 직전에 넣어 준다.
        # 비어 있으면 _to_stereo 가 예전 동작으로 돌아간다 (임의 해석 금지).
        self._channel_roles: tuple = ()

        # seek 핸드셰이크 안전장치 — 생산자 응답이 없을 때 강제 페이드인(영구 무음 방지)
        self._seek_fallback = QTimer(self)
        self._seek_fallback.setSingleShot(True)
        self._seek_fallback.setInterval(250)
        self._seek_fallback.timeout.connect(self._on_seek_fallback)

        # Qt 스레드 폴링 — 자연종료/정지완료 처리
        self._poll = QTimer(self)
        self._poll.setInterval(30)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()

    # ───────── 장치/스트림 ─────────
    def _ensure_stream(self, samplerate: int):
        if self._stream is not None and self._samplerate == samplerate:
            return
        self._retire_stream()
        self._samplerate = samplerate
        self._stream_gen += 1
        stream_gen = self._stream_gen
        wd = self._wasapi_default()
        attempts = []
        if wd is not None:
            attempts.append((wd, sd.WasapiSettings(auto_convert=True), "WASAPI/auto_convert"))
        attempts.append((self._mme_default(), None, "MME"))
        attempts.append((None, None, "default"))
        last_err = None
        for dev, extra, label in attempts:
            try:
                st = sd.OutputStream(
                    samplerate=samplerate, device=dev, channels=OUT_CHANNELS,
                    dtype="float32", blocksize=BLOCKSIZE, latency=OUTPUT_LATENCY_SEC,
                    extra_settings=extra,
                    callback=lambda outdata, frames, time_info, status, g=stream_gen:
                        self._callback(outdata, frames, time_info, status, g),
                )
                st.start()
                self._stream = st
                self._stream_warm_until = time.monotonic() + (STREAM_WARMUP_MS / 1000.0)
                logger.info("[audio] stream open via %s sr=%d latency=%.1fms",
                            label, samplerate, st.latency * 1000)
                return
            except Exception as e:
                last_err = e
                logger.warning("[audio] stream open fail (%s): %s", label, e)
        raise RuntimeError(f"오디오 스트림 개방 실패: {last_err}")

    def _close_stream(self):
        if self._stream is not None:
            self._stream_gen += 1
            stream = self._stream
            self._stream = None
            self._samplerate = 0
            try:
                stream.stop(); stream.close()
            except Exception:
                pass

    def _retire_stream(self):
        if self._stream is None:
            return
        old_stream = self._stream
        old_sr = self._samplerate
        self._stream_gen += 1
        self._stream = None
        self._samplerate = 0
        logger.info("[audio] retire stream sr=%d delay=%.0fms",
                    old_sr, STREAM_RETIRE_DELAY_SEC * 1000)
        threading.Thread(
            target=self._close_retired_stream,
            args=(old_stream,),
            daemon=True,
        ).start()

    @staticmethod
    def _close_retired_stream(stream):
        try:
            time.sleep(STREAM_RETIRE_DELAY_SEC)
            stream.stop(); stream.close()
        except Exception:
            pass

    @staticmethod
    def _wasapi_default():
        for h in sd.query_hostapis():
            if "wasapi" in h["name"].lower():
                d = h["default_output_device"]
                return d if d >= 0 else None
        return None

    @staticmethod
    def _mme_default():
        for h in sd.query_hostapis():
            if h["name"].lower() == "mme":
                d = h["default_output_device"]
                return d if d >= 0 else None
        return None

    # ───────── 콜백 (실시간 스레드 — 블로킹 금지) ─────────
    def _callback(self, outdata, frames, time_info, status, stream_gen=None):
        if stream_gen is not None and stream_gen != self._stream_gen:
            outdata.fill(0.0)
            return
        if status:
            now = time.monotonic()
            if now - self._last_callback_status_log > 0.5:
                self._last_callback_status_log = now
                logger.warning("[audio] callback status=%s", status)
        if self._frozen:
            outdata.fill(0.0)
            if self._target_gain == 0.0:
                self._switch_silent.set()
            # seek 핸드셰이크: 생산자가 새 위치 채움 끝 → 동결 풀고 페이드인
            if self._seek_wait and self._seek_done:
                self._seek_wait = False
                self._seek_done = False
                self._frozen = False
                self._target_gain = self._user_volume
            return
        try:
            block = self._q.get_nowait()
        except queue.Empty:
            block = None
        if block is None:
            outdata.fill(0.0)
            if self._eof:
                self._drained = True
            self._maybe_transition()
            if self._target_gain == 0.0 or self._cur_gain <= 1e-3:
                self._switch_silent.set()
            return

        n = block.shape[0]
        if n < frames:
            outdata[n:].fill(0.0)
        g0 = self._cur_gain
        g1 = self._target_gain
        if abs(g1 - g0) < 1e-4:
            outdata[:n] = block[:n] * g1
            self._cur_gain = g1
        else:
            ramp_ms = self._ramp_ms(g0, g1)
            if ramp_ms <= 0.0:
                outdata[:n] = block[:n] * g1
                self._cur_gain = g1
            else:
                ramp = max(1, int(self._samplerate * ramp_ms / 1000.0))
                step = (g1 - g0) / ramp
                gain = g0 + step * np.arange(1, n + 1, dtype=np.float32)
                np.clip(gain, min(g0, g1), max(g0, g1), out=gain)
                outdata[:n] = block[:n] * gain[:, None]
                self._cur_gain = float(gain[-1])
        # +dB 부스트(게인>1.0)로 샘플이 [-1,1]을 넘으면 wrap/잡음 방지 위해 하드 클립.
        # 게인이 1.0 이하인 평소엔 비용 0 (조건 통과 X).
        if g0 > 1.0 or g1 > 1.0:
            np.clip(outdata[:n], -1.0, 1.0, out=outdata[:n])
        self._played_src += n * self._rate
        self._maybe_transition()
        if self._target_gain == 0.0 and self._cur_gain <= 1e-3:
            self._switch_silent.set()

    def _ramp_ms(self, g0: float, g1: float) -> float:
        if g1 > g0:
            # 파일 처음부터 시작하는 페이드인만 초단축 (트랜지언트 보존).
            # seek/일시정지 복귀 등 파일 중간 지점은 기존 18ms 유지.
            if self._seek_base == 0 and self._played_src < self._file_sr * 0.005:
                return START_RAMP_HEAD_MS
            return START_RAMP_MS
        if self._after == "seek" and g1 < g0:
            return SEEK_FADE_MS
        return RAMP_MS

    def _maybe_transition(self):
        if self._after and self._cur_gain <= 1e-3:
            act = self._after
            self._after = None
            self._cur_gain = 0.0
            if act == "pause":
                self._frozen = True
            elif act == "stop":
                self._frozen = True
                self._stop_ready = True
            elif act == "seek":
                # 페이드아웃 완료 → 이제서야 생산자에 플러시·탐색 지시 + 동결 대기
                with self._seek_lock:
                    self._seek_req = self._pending_seek
                self._pending_seek = None
                self._seek_done = False
                self._seek_wait = True
                self._frozen = True

    # ───────── 생산자 스레드 ─────────
    def _producer_run(self, gen: int):
        frac = 0.0
        carry = np.zeros((0, OUT_CHANNELS), dtype=np.float32)
        while not self._producer_stop.is_set() and gen == self._gen:
            with self._seek_lock:
                sreq = self._seek_req
                self._seek_req = None
            if sreq is not None:
                self._seek_done = False   # 작업 시작 — 진행 중엔 완료신호 내림
                with self._sf_lock:
                    # 지역 참조로 고정 — _teardown 이 잠금 없이 self._sf=None 을
                    # 세워서, 체크와 사용 사이에 None 이 되는 경합(AttributeError) 방지.
                    f = self._sf
                    if f is not None:
                        try:
                            f.seek(max(0, min(sreq, self._total_frames)))
                        except Exception:
                            pass
                self._flush_queue()
                frac = 0.0
                carry = np.zeros((0, OUT_CHANNELS), dtype=np.float32)
                self._eof = False
                self._drained = False
                # 처리 직후 더 새로운 seek 가 대기 중이면 완료신호 보류(다음 루프에서 처리).
                # 대기 없을 때만 켜서, 콜백이 '최종 위치' 도달 후에만 페이드인하게 한다.
                with self._seek_lock:
                    still_pending = self._seek_req is not None
                if not still_pending:
                    self._seek_done = True   # 콜백 동결 해제 → 페이드인 재개

            rate = self._rate
            need = int(math.ceil(frac + BLOCKSIZE * max(rate, 1e-6))) + 2
            with self._sf_lock:
                # 지역 참조 f 로 고정 — _teardown(파일 전환) 이 잠금 없이
                # self._sf=None 을 세우므로, None 체크 후 self._sf 를 다시 읽으면
                # 그 틈에 'NoneType has no read' 로 스레드가 죽는 경합이 있었음
                # (2026-07-10 사용자 로그). 파일 close 는 생산자 join 후라 f 는 안전.
                f = self._sf
                if f is None or gen != self._gen:
                    return
                pos = f.tell()
                limit = self._loop_end if (self._loop and self._loop_end > 0) else self._total_frames
                to_read = need - carry.shape[0]
                data = np.zeros((0, OUT_CHANNELS), dtype=np.float32)
                if to_read > 0 and pos < limit:
                    raw = f.read(min(to_read, limit - pos), dtype="float32", always_2d=True)
                    data = self._to_stereo(raw)
            if gen != self._gen:
                return

            if data.shape[0] > 0:
                carry = np.concatenate([carry, data], axis=0) if carry.shape[0] else data
            else:
                if self._loop:
                    with self._sf_lock:
                        f = self._sf
                        if f is not None:
                            f.seek(self._loop_start)
                    frac = 0.0
                    carry = np.zeros((0, OUT_CHANNELS), dtype=np.float32)
                    continue
                self._eof = True
                if carry.shape[0] > 0:
                    self._put_block(carry[:BLOCKSIZE])
                # seek 처리 중이면 종료하지 않고 대기 — EOF 도달 후 seek 핸드셰이크가
                # 생산자 사망으로 미완(영구 무응답 → fallback 이 꼬리 재생)되는 것 방지.
                if (self._pending_seek is not None or self._seek_wait
                        or self._seek_req is not None):
                    time.sleep(0.01)
                    continue
                return

            if abs(rate - 1.0) < 1e-6:
                out = carry[:BLOCKSIZE]
                carry = carry[out.shape[0]:]
            else:
                positions = frac + rate * np.arange(BLOCKSIZE)
                if positions[-1] + 1 >= carry.shape[0]:
                    continue
                idx = positions.astype(np.int64)
                fr = (positions - idx).astype(np.float32)[:, None]
                out = (carry[idx] * (1 - fr) + carry[idx + 1] * fr).astype(np.float32)
                consumed = int(math.floor(positions[-1]))
                frac = positions[-1] - consumed
                carry = carry[consumed:]

            if not self._put_block(out):
                return

    def _put_block(self, block: np.ndarray) -> bool:
        block = np.ascontiguousarray(block, dtype=np.float32)
        current = threading.current_thread()
        while not self._producer_stop.is_set() and self._producer is current:
            if self._seek_req is not None:
                return True   # seek 대기 — put 포기, 루프 상단에서 플러시·탐색
            try:
                self._q.put(block, timeout=0.05)
                return True
            except queue.Full:
                continue
        return False

    def _flush_queue(self):
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _to_stereo(self, raw: np.ndarray) -> np.ndarray:
        if raw.shape[1] == OUT_CHANNELS:
            return raw
        if raw.shape[1] == 1:
            return np.repeat(raw, OUT_CHANNELS, axis=1)
        if raw.shape[1] > OUT_CHANNELS:
            ch = raw.shape[1]
            roles = self._channel_roles if len(self._channel_roles) == ch else ()
            if roles:
                # 채널이 실제로 어느 스피커인지 알 때 — 표준 계수를 역할별로 건다.
                left = np.zeros(raw.shape[0], dtype=np.float64)
                right = np.zeros(raw.shape[0], dtype=np.float64)
                for index, role in enumerate(roles):
                    gain_l = DOWNMIX_L.get(role)
                    gain_r = DOWNMIX_R.get(role)
                    if gain_l:
                        left += raw[:, index] * gain_l
                    if gain_r:
                        right += raw[:, index] * gain_r
            else:
                # 역할을 모를 때(앰비소닉·판정 실패)는 **예전 동작 그대로** 둔다.
                # 여기서 임의로 바꾸면 W/X/Y/Z 같은 비(非)스피커 채널을 스피커로
                # 잘못 해석하게 된다. 레벨 정책만 아래에서 똑같이 적용한다.
                left = raw[:, 0].astype(np.float64).copy()
                right = raw[:, 1].astype(np.float64).copy()
                if ch == 4:
                    left += raw[:, 2] * 0.707
                    right += raw[:, 3] * 0.707
                elif ch >= 6:
                    center = raw[:, 2] * 0.707
                    left += center
                    right += center
                    left += raw[:, 4] * 0.707
                    right += raw[:, 5] * 0.707
                elif ch >= 3:
                    center = raw[:, 2] * 0.707
                    left += center
                    right += center
            out = np.column_stack((left, right))
            # ffmpeg normalize: 계수 합(5.1 = 2.414)으로 나눈다. 입력이 풀스케일이어도
            # 결과가 1.0 을 못 넘으므로 리미터가 없고, 이득이 파일·블록에 무관하게
            # 고정이라 펌핑도 없다 (블록 단위 peak 정규화는 여전히 금지).
            out *= norm_gain(roles, ch)
            out = out.astype(np.float32, copy=False)
            return np.ascontiguousarray(out)
        pad = np.zeros((raw.shape[0], OUT_CHANNELS - raw.shape[1]), dtype=np.float32)
        return np.concatenate([raw, pad], axis=1)

    # ───────── 공개 API ─────────
    def set_channel_roles(self, roles) -> None:
        """다음 load 에 쓸 채널 역할을 넣는다. **load 보다 먼저** 부를 것.

        엔진은 채널 순서를 스스로 판정하지 않는다 — 바이노럴과 **같은 판정**
        (app.binaural.resolve_layout)을 쓰려고 호출자가 넘겨 준다. 두 경로가 각자
        판정하면 서로 어긋난다 (2026-09-15 검수에서 확인한 사고 유형).
        """
        self._channel_roles = tuple(roles or ())

    def load(self, path: str) -> bool:
        self._fade_out_for_switch()
        self._teardown(block=False)
        self._set_state("stopped")
        try:
            f = sf.SoundFile(path)
        except Exception as e:
            logger.warning("[audio] open fail %s: %s", path, e)
            return False
        self._sf = f
        self._path = path
        self._file_sr = f.samplerate or 48000
        self._total_frames = f.frames if f.frames > 0 else 0
        self._seek_base = 0
        self._played_src = 0.0
        self._loop = False
        self._loop_start = self._loop_end = 0
        try:
            self._ensure_stream(self._file_sr)
        except Exception as e:
            logger.warning("[audio] stream fail %s: %s", path, e)
            self._sf = None
            return False
        logger.info("[audio] load file_sr=%d path=%s",
                    self._file_sr, os.path.basename(path))
        self._gen += 1
        self._producer_stop.clear()
        self._producer = threading.Thread(target=self._producer_run, args=(self._gen,), daemon=True)
        self._producer.start()
        return True

    def play(self):
        # 재생 요청이 **조용히 무시되는** 두 경우를 기록으로 남긴다.
        # 증상은 "재생을 눌렀는데 플레이헤드가 안 움직이고 소리도 없다" 인데,
        # 지금까지 로그에 아무 흔적이 없어 원인을 좁힐 수 없었다
        # (사용자 신고 2026-09-07: 사운드를 번갈아 틀 때 갑자기 멈춘다).
        # 재생이 실제로 시작되면 _poll_tick 이 [audio-dbg] unfreeze 를 찍으므로,
        # 그게 없는 play 를 아래 두 줄로 구분할 수 있다.
        if self._sf is None:
            logger.warning("[audio] play 무시 — 열린 파일 없음 (state=%s)", self._state)
            return
        if self._state == "playing":
            logger.info("[audio-dbg] play 무시 — 이미 playing (frozen=%s target=%.3f cur=%.3f q=%d)",
                        self._frozen, self._target_gain, self._cur_gain, self._q.qsize())
            return
        if self._state == "paused":
            self.resume()
            return
        # idle 진입한 WASAPI 장치는 깨어나는 동안 프레임0 앞부분이 잘릴 수 있다.
        # 원음 우선: 빠른 응답보다 첫 블록 보존을 우선해 매 재생 시작마다 warmup을 보장한다.
        self._stream_warm_until = time.monotonic() + (STREAM_WARMUP_MS / 1000.0)
        self._frozen = True
        self._after = None
        self._target_gain = 0.0
        self._set_state("playing")

    def restart(self):
        if self._state == "playing":
            self._restart_now()
            return
        self.seek(0)
        if self._state != "playing":
            self.play()

    def _restart_now(self):
        if self._sf is None or self._file_sr <= 0:
            return
        self._seek_base = 0
        self._played_src = 0.0
        self._drained = False
        self._eof = False
        self._after = None
        self._pending_seek = None
        self._target_gain = 0.0
        self._cur_gain = 0.0
        self._frozen = True
        self._seek_wait = True
        self._seek_done = False
        self._flush_queue()
        with self._seek_lock:
            self._seek_req = 0
        self._ensure_producer()
        self._seek_fallback.start()

    def _ensure_producer(self):
        """seek/restart 시 생산자 생존 보장 — 파일 전체를 큐에 다 읽으면 생산자가
        종료하므로, 마지막 ~2초(큐 깊이) 구간 재생 중 seek 하면 처리 주체가 없어
        탐색이 무시되던 버그(세그먼트 헤더 클릭 무반응)의 핵심 수정."""
        if self._producer is not None and self._producer.is_alive():
            return
        if self._sf is None:
            return
        self._producer_stop.clear()
        self._producer = threading.Thread(
            target=self._producer_run, args=(self._gen,), daemon=True
        )
        self._producer.start()

    def pause(self):
        if self._state != "playing":
            return
        self._target_gain = 0.0
        self._after = "pause"
        self._set_state("paused")

    def resume(self):
        if self._state != "paused":
            return
        self._frozen = False
        self._after = None
        self._target_gain = self._user_volume
        self._set_state("playing")

    def stop(self):
        if self._sf is None and self._state == "stopped":
            return
        if self._frozen or self._cur_gain <= 1e-3:
            self._teardown()
            self._set_state("stopped")
        else:
            self._target_gain = 0.0
            self._after = "stop"

    def seek(self, ms: int):
        if self._sf is None or self._file_sr <= 0:
            # [진단] 소스가 닫혀 seek 이 버려지는 경우 — 헤더 클릭 무반응의 한 원인
            logger.info("[audio-dbg] seek 무시 (소스 닫힘) ms=%d state=%s", ms, self._state)
            return
        logger.info("[audio-dbg] seek ms=%d state=%s eof=%s drained=%s q=%d",
                    ms, self._state, self._eof, self._drained, self._q.qsize())
        frame = max(0, min(int(ms / 1000.0 * self._file_sr), self._total_frames))
        self._seek_base = frame
        self._played_src = 0.0
        self._drained = False
        if self._after == "stop":
            # PlayerWidget still has QMediaPlayer-era stop(); setPosition(0)
            # call sites. Keep the pending stop fade instead of turning it
            # into a seek/restart transition.
            with self._seek_lock:
                self._seek_req = frame
            return
        if self._state != "playing":
            # 일시정지/정지: 페이드 불필요(이미 무음). 생산자에만 탐색 지시.
            with self._seek_lock:
                self._seek_req = frame
            self._ensure_producer()
            return
        if self._frozen or self._seek_wait or self._cur_gain <= 1e-3:
            self._after = None
            self._pending_seek = None
            self._target_gain = 0.0
            self._cur_gain = 0.0
            self._frozen = True
            self._seek_wait = True
            self._seek_done = False
            self._flush_queue()
            with self._seek_lock:
                self._seek_req = frame
            self._ensure_producer()
            self._seek_fallback.start()
            return
        self._pending_seek = frame
        self._target_gain = 0.0
        self._after = "seek"
        self._ensure_producer()
        self._seek_fallback.start()

    def _on_seek_fallback(self):
        # 생산자 응답 지연/누락 시 강제 페이드인 (영구 무음 방지)
        if self._seek_wait or self._frozen:
            self._seek_wait = False
            self._seek_done = False
            self._frozen = False
            # ⚠ 탐색 **직전**의 '소진' 표시를 함께 지운다. 지우지 않으면:
            #   끝부분/짧은 파일에서 _eof=True 인 상태로 탐색 → seek 이 큐를 비움 →
            #   콜백이 _drained=True (아직 _eof 가 안 내려간 시점) → 이 타이머가
            #   _seek_wait 만 풀어줌 → _poll_tick 이 자연종료로 오판해 teardown.
            #   결과: **세그먼트 헤더를 이리저리 클릭하면 재생이 멈춘다** (사용자 신고).
            #   NAS 는 탐색 왕복이 이 타이머(250ms)보다 오래 걸려 특히 잘 걸린다.
            # 진짜 끝이었다면 생산자가 새 위치에서 다시 _eof 를 세우고 콜백이
            # _drained 를 다시 올리므로, 자연종료 처리가 사라지지 않는다(한 tick 지연).
            self._drained = False
            if self._state == "playing":
                self._target_gain = self._user_volume

    def set_volume(self, vol: float):
        # 상한 2.0 = +6.02 dB(진폭 2배). dB 페이더 부스트 허용.
        self._user_volume = max(0.0, min(2.0, vol))
        if self._state == "playing" and self._after is None and not self._seek_wait and not self._frozen:
            self._target_gain = self._user_volume

    def set_rate(self, rate: float):
        self._rate = max(0.1, min(4.0, rate))

    def set_loop(self, enabled: bool, start_ms: int = 0, end_ms: int = 0):
        self._loop = bool(enabled)
        self._loop_start = int(start_ms / 1000.0 * self._file_sr) if self._file_sr else 0
        self._loop_end = int(end_ms / 1000.0 * self._file_sr) if (self._file_sr and end_ms > 0) else 0

    def position_ms(self) -> int:
        if self._file_sr <= 0:
            return 0
        cur = max(0, min(self._seek_base + self._played_src, self._total_frames))
        return int(cur / self._file_sr * 1000.0)

    def duration_ms(self) -> int:
        if self._file_sr <= 0:
            return 0
        return int(self._total_frames / self._file_sr * 1000.0)

    def state(self) -> str:
        return self._state

    def is_playing(self) -> bool:
        return self._state == "playing"

    def is_loaded(self) -> bool:
        """재생 가능한 파일이 열려있고 생산자가 살아있는지 (자연종료/정지 후엔 False)."""
        return (self._sf is not None and self._producer is not None
                and self._producer.is_alive())

    def is_open(self) -> bool:
        """파일이 열려 있는지 — 생산자 생존 무관. seek/restart 가능 여부 판단용.
        생산자는 파일 전체를 큐에 채우면 종료하므로(마지막 ~2초 구간) is_loaded() 로
        seek 을 게이트하면 끝부분에서 탐색이 무시됨. seek() 이 생산자를 되살린다."""
        return self._sf is not None

    def close(self):
        self._poll.stop()
        self._teardown(block=True)
        self._close_stream()

    def release_output(self):
        """다른 재생 백엔드가 오디오 장치를 쓰기 전에 파일과 출력 스트림을 반납한다.

        close()와 달리 상태 폴링 타이머는 유지하므로 이후 일반 재생으로 안전하게
        돌아올 수 있다. 비동기 로드 워커에서 호출되어 NAS 파일 정리도 UI를 막지 않는다.
        """
        self._teardown(block=True)
        self._close_stream()
        self._set_state("stopped")

    def release_source(self):
        """바이노럴 공유 장치 전환용: 파일만 닫고 WASAPI 스트림은 유지한다.

        SoundField 출력은 WASAPI shared/auto_convert이고 사이드카도 같은 기본
        endpoint의 공유 모드라 동시에 열 수 있다. 스트림까지 닫으면 RME에서
        약 0.5~0.7초가 추가되므로, normal 출력은 begin_switch()로 무음 고정한
        뒤 파일/생산자만 정리한다.
        """
        self._teardown(block=True)
        self._set_state("stopped")

    # ───────── 내부 ─────────
    def _teardown(self, block: bool = False):
        """동기 하드 정지 — 생산자 종료 + 큐 비움 + 파일 닫기. 스트림은 유지(전환 팝 방지)."""
        self._gen += 1
        self._producer_stop.set()
        old_producer = self._producer
        old_sf = self._sf
        self._producer = None
        self._sf = None
        self._flush_queue()
        if block:
            if old_producer is not None:
                old_producer.join(timeout=0.5)
            if old_sf is not None:
                try:
                    old_sf.close()
                except Exception:
                    pass
        elif old_producer is not None or old_sf is not None:
            threading.Thread(
                target=self._close_old_source,
                args=(old_producer, old_sf),
                daemon=True,
            ).start()
        self._cur_gain = 0.0
        self._target_gain = 0.0
        self._switch_silent.set()
        self._frozen = False
        self._switching = False
        self._after = None
        self._stop_ready = False
        self._pending_seek = None
        self._seek_wait = False
        self._seek_done = False
        with self._seek_lock:
            self._seek_req = None
        self._eof = False
        self._drained = False
        self._played_src = 0.0
        self._seek_base = 0

    @staticmethod
    def _close_old_source(producer, sf_obj):
        if producer is not None:
            try:
                producer.join(timeout=2.0)
            except Exception:
                pass
        if sf_obj is not None:
            try:
                sf_obj.close()
            except Exception:
                pass

    def begin_switch(self):
        self._switching = True
        self._target_gain = 0.0
        self._after = None
        self._pending_seek = None
        if self._cur_gain <= 1e-3:
            self._frozen = True
            self._switch_silent.set()

    def _fade_out_for_switch(self):
        self._switching = True
        if self._stream is None or self._cur_gain <= 1e-3:
            return
        self._switch_silent.clear()
        self._target_gain = 0.0
        wait_s = SWITCH_FADE_MS / 1000.0
        if self._samplerate:
            wait_s = max(wait_s, 4.0 * BLOCKSIZE / float(self._samplerate))
        if not self._switch_silent.wait(timeout=wait_s + 0.025):
            logger.warning("[audio] switch fade timeout gain=%.4f q=%d", self._cur_gain, self._q.qsize())
        self._frozen = True

    def _set_state(self, st: str):
        if self._state != st:
            self._state = st
            self.stateChanged.emit(st)

    def _poll_tick(self):
        prebuffer_ready = self._q.qsize() >= START_PREBUFFER_BLOCKS or self._eof
        if (self._state == "playing" and self._target_gain == 0.0
                and self._after is None and not self._seek_wait
                and not self._switching
                and time.monotonic() >= self._stream_warm_until
                and prebuffer_ready):
            self._frozen = False
            self._target_gain = self._user_volume
            # [진단] 첫 출력(unfreeze) 시점 — 시작 위치/프리버퍼/warmup 잔여 측정.
            # played_src=0 + q>=5 면 엔진은 0부터 정상 → 잘림은 device 지연.
            logger.info("[audio-dbg] unfreeze played_src=%.0f seek_base=%d q=%d "
                        "warm_remain=%.0fms sr=%d",
                        self._played_src, self._seek_base, self._q.qsize(),
                        max(0.0, (self._stream_warm_until - time.monotonic()) * 1000.0),
                        self._file_sr)
        if self._stop_ready:
            self._stop_ready = False
            self._teardown()
            self._set_state("stopped")
            return
        # 생산자에게 전달만 되고 아직 처리되지 않은 탐색 요청도 확인한다.
        # 이게 남아 있으면 지금의 '소진' 은 옛 위치 기준이라 믿을 수 없다.
        with self._seek_lock:
            seek_outstanding = self._seek_req is not None
        if (self._drained and self._state == "playing"
                # seek 진행 중엔 자연종료 처리 보류 — 끝부분 재생 중 seek 시
                # 큐가 잠깐 비는 순간을 EOF 로 오판해 teardown 하면 탐색이 사라짐
                and self._after is None and not self._seek_wait
                and self._pending_seek is None
                and not seek_outstanding):
            self._drained = False
            # [진단] 왜 자연종료로 판단했는지 남긴다 — 세그먼트 헤더 클릭 중
            # 예상치 못한 정지를 추적하기 위한 로그 (사용자 신고 2026-09-04).
            logger.info("[audio-dbg] 자연종료 판단 pos=%d/%d eof=%s q=%d "
                        "played_src=%.0f seek_base=%d frozen=%s",
                        int(self._seek_base + self._played_src), self._total_frames,
                        self._eof, self._q.qsize(), self._played_src,
                        self._seek_base, self._frozen)
            self._teardown()                       # 생산자 종료/파일 닫기 → is_loaded()=False
            self._seek_base = self._total_frames   # playhead 는 끝에 유지(QMediaPlayer 동작 모사)
            self._set_state("stopped")
            self.ended.emit()
