import json
import os
import queue
import sys
import threading
from pathlib import Path


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

from PyQt6.QtCore import QCoreApplication, QTimer, QUrl  # noqa: E402
from PyQt6.QtMultimedia import QMediaPlayer  # noqa: E402
from app.ui.playback import HybridPlayer  # noqa: E402


commands: "queue.Queue[dict]" = queue.Queue()


def stdin_reader():
    # ⚠ 부모(앱)가 죽으면 stdin 이 닫힌다(EOF). 그때 스스로 끝내지 않으면 이 프로세스가
    #   고아로 남아 오디오 출력 스트림을 계속 물고 있다 (실측: 고아 8개 누적 →
    #   새 인스턴스에서 소리가 안 남). 루프가 끝나면 종료 명령을 넣는다.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            commands.put(json.loads(line))
        except Exception as exc:
            print(json.dumps({"type": "error", "message": str(exc)}), flush=True)
    commands.put({"command": "quit"})


def main():
    # ⚠ 원본 엔진(app/audio_engine.py)은 logger 로 진단을 남기는데, 사이드카에서는
    # logging 설정이 없어서 **어디에도 기록되지 않았다.** 재생이 예상치 못하게
    # 멈추는 문제를 추적하려면 이 로그가 필요하다 (사용자 신고 2026-09-04).
    import logging
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SoundField"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(log_dir / "poc_audio.log"), filemode="a",
            level=logging.INFO, encoding="utf-8",
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger(__name__).info("오디오 사이드카 시작 pid=%d", os.getpid())
    except Exception:
        pass

    app = QCoreApplication(sys.argv)
    player = HybridPlayer()

    def emit(event_type: str, **payload):
        print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)

    # 원본 PlayerWidget이 직접 받던 런타임 신호를 프런트에도 그대로 전달한다.
    # 위치만 전달하면 IEM 미설치/바이노럴 폴백/실제 재생 시작을 UI가 알 수 없다.
    player.binauralAvailabilityChanged.connect(
        lambda available, reason: emit(
            "availability", available=bool(available), reason=str(reason or "")))
    player.binauralStatusChanged.connect(
        lambda message: emit("binaural-status", message=str(message or "")))
    player.binauralLayoutResolved.connect(
        lambda layout: emit(
            "binaural-layout",
            preset=str(getattr(layout, "preset", layout) or ""),
            topology=str(getattr(layout, "topology", "") or ""),
            source_order=int(getattr(layout, "source_order", 0) or 0)))
    # 어느 파일의 상태인지 함께 보낸다 — 빠르게 파일을 넘기면 이전 파일의 상태
    # 이벤트가 늦게 도착해 새 파일의 재생 표시(PIP)를 잘못 바꾼다.
    player.playbackStateChanged.connect(
        lambda status: emit("playback-state",
                            state=str(getattr(status, "name", status)),
                            path=state["path"]))
    player.mediaStatusChanged.connect(
        lambda status: emit("media-status", status=str(getattr(status, "name", status))))

    # BinauralBackend는 생성 시 이미 설치 여부를 판정한다. 신호 연결 이전에 발생한
    # 최초 availability도 프런트가 놓치지 않도록 명시적인 초기 스냅샷을 보낸다.
    emit("availability", available=bool(player.isBinauralInstalled()),
         reason="" if player.isBinauralInstalled()
         else "바이노럴 처리 구성 요소가 설치되지 않았습니다")

    cmd_log = logging.getLogger("sf.cmd")

    # ── 재생 로드 감시 (원본 PLAYBACK_LOAD_WATCHDOG_MS, player_widget.py:45) ──
    # ⚠ 이 감시를 지우지 말 것. 없으면 **로딩에 갇힌 재생이 영원히 그대로 남는다** —
    #   화면은 재생 중처럼 보이는데 플레이헤드가 움직이지 않는다. 원본은 UI 쪽
    #   PlayerWidget 이 이 감시를 들고 있었는데, Tauri 판은 그 UI 를 다시 만들면서
    #   같이 빠졌다 (조사 Q04). NAS 지연·사라진 파일에서 실제로 걸린다.
    #
    # 원본과 같은 판정: 8초 뒤에도 **재생 중이 아니면서 상태가 LoadingMedia** 면
    # 정지하고 표시를 정리한다. 세대(gen) 로 최신 요청만 본다 — 그 사이 다른 파일을
    # 골랐으면 옛 감시는 아무 일도 하지 않는다.
    LOAD_WATCHDOG_MS = 8000

    def watch_play_load(path: str, gen: int):
        if gen != state.get("play_gen") or path != state.get("path"):
            return
        try:
            status = getattr(player.mediaStatus(), "name", "")
            playing = getattr(player.playbackState(), "name", "") == "PlayingState"
        except Exception:                                          # noqa: BLE001
            return
        if not playing and status == "LoadingMedia":
            cmd_log.warning("로드 감시 발동 — %d초 동안 로딩에 갇혀 정지시킴 (%s)",
                            LOAD_WATCHDOG_MS // 1000, str(path)[-60:])
            try:
                player.stop()
            except Exception:                                      # noqa: BLE001
                pass
            emit("playback-state", state="StoppedState", path=path)
            emit("error", message="재생을 시작하지 못했습니다 — 파일을 여는 데 너무 오래 걸립니다."
                                  " (네트워크 경로가 느리거나 파일이 없을 수 있습니다)")

    def arm_load_watchdog():
        """재생을 시킬 때마다 새 세대로 감시를 건다."""
        state["play_gen"] = int(state.get("play_gen") or 0) + 1
        gen = state["play_gen"]
        path = state.get("path") or ""
        QTimer.singleShot(LOAD_WATCHDOG_MS, lambda: watch_play_load(path, gen))

    def probe_path_exists(path: str, gen: int):
        """파일이 실제로 있는지 배경에서 확인한다 (원본 PathProbeRunnable).
        ⚠ 여기서 os.path.exists 를 **메인 스레드에서** 부르지 말 것 — NAS 가 멈추면
          오디오 스레드가 같이 멈춘다. 그래서 별도 스레드에서 확인한다."""
        def run():
            try:
                ok = os.path.exists(path)
            except OSError:
                ok = False
            if ok or gen != state.get("play_gen") or path != state.get("path"):
                return
            cmd_log.warning("경로 확인 실패 — 파일이 없어 정지시킴 (%s)", str(path)[-60:])
            try:
                player.stop()
            except Exception:                                      # noqa: BLE001
                pass
            emit("playback-state", state="StoppedState", path=path)
            emit("error", message="파일을 찾을 수 없습니다 — 이동·삭제되었거나 경로에 접근할 수 없습니다.")
        threading.Thread(target=run, daemon=True, name="sf-path-probe").start()

    def handle(cmd: dict):
        name = cmd.get("command")
        # [진단] 앱이 보내는 명령 전부 기록 — 재생이 안 걸리는 원인 추적용.
        # position/volume 처럼 값이 중요한 것만 값을 함께 남긴다.
        try:
            extra = ""
            if name == "seek":
                extra = f" position_ms={cmd.get('position_ms')}"
            elif name == "volume":
                extra = f" volume={cmd.get('volume')}"
            elif name == "load":
                extra = f" path={str(cmd.get('path'))[-60:]}"
            cmd_log.info("명령 %s%s (엔진상태=%s)", name, extra,
                         getattr(player.playbackState(), "name", "?"))
        except Exception:
            pass
        if name == "load":
            path = cmd.get("path") or ""
            meta = cmd.get("meta") or {}
            if not path:
                return
            state["meta"] = dict(meta)
            state["path"] = path
            state["dur"] = 0
            state["awaiting_zero"] = True
            # QMediaPlayer가 새 source를 비동기로 교체하는 동안 직전 source 위치를
            # 잠깐 유지한다. 명시적으로 정지/0 seek한 뒤 새 파일을 연다.
            player.stop()
            player.setMediaMetadata(meta)
            player.setSource(QUrl.fromLocalFile(path))
            player.setPosition(0)
            # 어느 파일의 위치인지 함께 보고한다 — 프런트가 이전 파일의 잔여
            # 보고를 새 파일에 섞지 않기 위해 필요하다 (재생바가 중간에 멈추는 원인).
            # 앱 시작 직후 보낸 최초 이벤트를 프런트 리스너 등록 전에 놓쳤더라도,
            # 실제 파일을 열 때 설치 상태를 반드시 다시 동기화한다.
            emit("availability", available=bool(player.isBinauralInstalled()),
                 reason="" if player.isBinauralInstalled()
                 else "바이노럴 처리 구성 요소가 설치되지 않았습니다")
            # 파일이 실제로 있는지 배경에서 확인한다 (원본과 같은 정책)
            state["play_gen"] = int(state.get("play_gen") or 0) + 1
            probe_path_exists(path, state["play_gen"])
        elif name == "play":
            player.play()
            arm_load_watchdog()
        elif name == "restart":
            player.restart()
            arm_load_watchdog()
        elif name == "pause":
            player.pause()
        elif name == "stop":
            player.stop()
        elif name == "seek":
            player.setPosition(int(cmd.get("position_ms") or 0))
        elif name == "rate":
            player.setPlaybackRate(float(cmd.get("rate") or 1.0))
        elif name == "volume":
            player.setVolume(float(cmd.get("volume") or 0.0))
        elif name == "binaural":
            player.setBinauralEnabled(bool(cmd.get("enabled")))
        elif name == "layout":
            # 원본 _preview_binaural_layout/_apply_binaural_layout과 같은 순서:
            # 메타 교체 -> 현재 바이노럴 체인 재로딩 -> 필요 시 0초부터 재생.
            meta = dict(state.get("meta") or {})
            layout = str(cmd.get("layout") or "")
            if layout:
                meta["binaural_layout"] = layout
            else:
                meta.pop("binaural_layout", None)
            state["meta"] = meta
            player.setMediaMetadata(meta)
            restart = bool(cmd.get("restart"))
            should_play = bool(cmd.get("play"))
            position = 0 if restart else int(player.position())
            if not player.reloadBinauralLayout(position, should_play) and state.get("path"):
                player.setSource(QUrl.fromLocalFile(state["path"]))
                player.setPosition(position)
                if should_play:
                    player.play()
        elif name == "quit":
            player.close()
            app.quit()

    def drain():
        for _ in range(200):
            try:
                cmd = commands.get_nowait()
            except queue.Empty:
                break
            try:
                handle(cmd)
            except Exception as exc:
                print(json.dumps({"type": "error", "message": str(exc)}), flush=True)

    # ── 재생 위치 보고 (원본 _on_tick 이 player.position() 을 직접 폴링하는 것을
    #    파이프로 옮긴 것) ─────────────────────────────────────────────────────
    #    원본은 FRAME_MS(8ms) PreciseTimer 로 엔진 위치를 읽는다. 같은 주기로 보고한다.
    #    ⚠ 실측: 엔진 자체의 위치 갱신 단위가 23ms(1024프레임 @44.1kHz)이고 파이프
    #    지터로 최대 128ms 까지 벌어진다 → 프런트의 lead cap 은 이 간격을 실측해서
    #    맞춘다 (32ms 고정이면 32ms 가고 멈췄다 점프하는 톱니가 생긴다).
    #    ⚠ 값이 바뀌지 않으면 보내지 않는다 — 파이프/JSON 부담을 줄인다.
    last = {}
    # ⚠ HybridPlayer 에는 duration() 메서드가 없다 (durationChanged 시그널만 있다).
    #   player.duration() 을 부르면 AttributeError 로 보고가 통째로 죽는다.
    state = {"dur": 0, "path": "", "meta": {}, "awaiting_zero": False}
    player.durationChanged.connect(lambda ms: state.__setitem__("dur", int(ms or 0)))

    def report():
        try:
            pos = int(player.position())
            dur = int(state["dur"])
            playing = (player.playbackState()
                       == QMediaPlayer.PlaybackState.PlayingState)
            try:
                binaural = bool(player.isBinauralActive())
            except Exception:
                binaural = False
        except Exception as exc:
            print(json.dumps({"type": "error", "message": f"pos report: {exc}"}),
                  flush=True)
            return
        # 새 source가 직전 위치를 잠깐 돌려주는 Qt 비동기 전환 구간. 이 값을 새
        # 경로와 함께 보내면 프런트의 단조 증가 보간이 그 위치에 고정된다.
        if state.get("awaiting_zero"):
            if pos > 50:
                return
            state["awaiting_zero"] = False
        cur = (pos, dur, playing, binaural, state["path"])
        if cur == last.get("v"):
            return
        last["v"] = cur
        print(json.dumps({
            "type": "pos",
            "position_ms": pos,
            "duration_ms": dur,
            "playing": playing,
            "binaural": binaural,
            "path": state["path"],
        }), flush=True)

    threading.Thread(target=stdin_reader, daemon=True).start()
    timer = QTimer()
    timer.timeout.connect(drain)
    timer.start(15)
    pos_timer = QTimer()
    pos_timer.timeout.connect(report)
    pos_timer.start(8)   # 원본 FRAME_MS 와 동일 — 엔진 위치가 바뀌는 즉시 내보낸다
    app.aboutToQuit.connect(player.close)
    app.exec()


if __name__ == "__main__":
    main()
