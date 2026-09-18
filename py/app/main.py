import faulthandler
import json
import logging
import sys
import threading
import traceback
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QtMsgType, qInstallMessageHandler, QEvent, QObject, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleFactory, QWidget
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

class _NoFocusRectStyle(QProxyStyle):
    """앱 전역 dotted focus rect 제거 및 툴팁 속도/지속시간 조절."""
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            return
        super().drawPrimitive(element, option, painter, widget)

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
            return 150  # 호버 후 빠르게 노출 (100ms 는 살짝 빨라 스칠 때 깜빡임)
        if hint == QStyle.StyleHint.SH_ToolTip_FallAsleepDelay:
            return 10000  # 한 번 뜬 후 다음 툴팁이 즉시 뜨는 유효 기간을 10초로 연장
        return super().styleHint(hint, option, widget, returnData)


class _GlobalTooltipEventFilter(QObject):
    """
    모든 위젯에 대해 툴팁이 설정되어 있으면 WA_AlwaysShowToolTips 속성을 활성화하여
    윈도우가 비활성 상태이거나 클릭되지 않은 상태에서도 호버 시 툴팁이 즉시 나오도록 강제함.
    """
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTipChange:
            if isinstance(obj, QWidget) and obj.toolTip():
                obj.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        elif event.type() in (QEvent.Type.ChildAdded, QEvent.Type.Show):
            # 새로운 자식 위젯이 추가되거나 위젯이 처음 보일 때도 체크
            target = event.child() if event.type() == QEvent.Type.ChildAdded else obj
            if isinstance(target, QWidget) and target.toolTip():
                target.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        return super().eventFilter(obj, event)

def _migrate_legacy_storage():
    """v1.1.x → v1.2.0 SoundField 이전: %LOCALAPPDATA%\\SoundSearch\\ → \\SoundField\\.
    30명 사용자 인덱스/히스토리/캐시 무중단 보존. 다른 모듈 import 전에 호출돼야 함.

    주의: 새 SoundField 폴더가 이미 있어도, '비어있거나 index.db 가 매우 작으면'
    빈 폴더로 간주하고 옛 폴더로 덮어씀. 빈 새 빌드가 한 번 실행돼 빈 폴더가
    생기는 경우에도 사용자 데이터 우선. 양쪽 다 의미있는 db 가 있으면 사용자에게
    맡기고 noop (충돌 안 일으킴)."""
    base = Path.home() / "AppData" / "Local"
    legacy = base / "SoundSearch"
    new = base / "SoundField"
    if legacy.exists():
        legacy_db = legacy / "index.db"
        new_db = new / "index.db"
        new_is_empty = (
            not new.exists()
            or not new_db.exists()
            or new_db.stat().st_size < 1024 * 1024  # 1MB 미만 = 사실상 빈 새 db
        )
        legacy_has_data = legacy_db.exists() and legacy_db.stat().st_size > 1024 * 1024
        if legacy_has_data and new_is_empty:
            try:
                if new.exists():
                    import shutil
                    shutil.rmtree(new, ignore_errors=True)
                legacy.rename(new)
            except OSError:
                pass
    legacy_cfg = Path.home() / ".sound_search_config.json"
    new_cfg = Path.home() / ".soundfield_config.json"
    if legacy_cfg.exists() and not new_cfg.exists():
        try:
            legacy_cfg.rename(new_cfg)
        except OSError:
            pass


_migrate_legacy_storage()

from app.ui.main_window import MainWindow
from app.ui.splash import SplashScreen
from app.ui.theme import build_qss
from app.region_export import cleanup_temp_files

LOG_DIR = Path.home() / "AppData" / "Local" / "SoundField" / "logs"
_CONFIG_FILE = Path.home() / ".soundfield_config.json"
_fault_log = None  # faulthandler가 살아있는 동안 열린 채 유지
_SINGLE_INSTANCE_KEY = "SoundField_SingleInstance_Lock"


def _load_theme_pref() -> str:
    """MainWindow 생성 전에 마지막 테마 선택을 읽어 첫 paint 부터 적용.
    사용자 노출 alias (neon/neutral) 또는 내부 키 (dark/light/grey) 둘 다 인식.
    기본값: neutral (= grey).
    """
    try:
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            t = data.get("theme")
            if t in ("dark", "light", "grey", "neon", "neutral"):
                return t
    except Exception:
        pass
    return "neutral"


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def install_crash_handlers():
    """네이티브 segfault, 미처리 파이썬 예외, Qt 메시지 전부 파일로."""
    global _fault_log
    _fault_log = open(LOG_DIR / "faulthandler.log", "a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=_fault_log, all_threads=True)

    logger = logging.getLogger("crash")

    def _excepthook(exc_type, exc, tb):
        logger.error("Unhandled exception\n%s",
                     "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = _excepthook

    def _qt_msg(mode, ctx, message):
        lvl = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }.get(mode, logging.INFO)
        # QMediaPlayer FFmpeg backend 의 알려진 무해한 정리 경고 — DEBUG 강등
        if "QFFmpeg::" in message and "wildcard call disconnects from destroyed signal" in message:
            lvl = logging.DEBUG
        where = f" ({ctx.file}:{ctx.line})" if ctx and ctx.file else ""
        logging.getLogger("qt").log(lvl, "%s%s", message, where)
    qInstallMessageHandler(_qt_msg)


def _boost_ui_thread_priority():
    """메인 UI 스레드 OS 우선순위 ABOVE_NORMAL — 인덱싱 워커 (BELOW_NORMAL) 와
    격차를 벌려 paint/timer 콜백이 즉시 스케줄됨. 120Hz playhead 부드러움 보장.
    Windows 한정, 실패 silent.
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThread.restype = wintypes.HANDLE
        kernel32.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        kernel32.SetThreadPriority.restype = wintypes.BOOL
        THREAD_PRIORITY_ABOVE_NORMAL = 1
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL)
    except Exception:
        pass


def install_freeze_watchdog(app, threshold_sec: float = 2.0):
    """진단 전용 — UI 스레드가 threshold 이상 멈추면 전 스레드 스택을 freeze.log 에 덤프.
    UI 하트비트 타이머(0.5s) + 데몬 감시 스레드. 재생/로직은 건드리지 않는다.
    300MB+ 파일 재생 시 프리즈가 '어느 함수에서' 멈추는지 추측 없이 확정하기 위함."""
    import threading
    import time

    freeze_log = open(LOG_DIR / "freeze.log", "a", encoding="utf-8", buffering=1)
    state = {"beat": time.monotonic(), "dumped": False}

    hb = QTimer(app)
    hb.setInterval(500)

    def _beat():
        state["beat"] = time.monotonic()
        state["dumped"] = False   # 회복되면 다음 프리즈도 다시 덤프
    hb.timeout.connect(_beat)
    hb.start()

    def _watch():
        while True:
            time.sleep(0.5)
            stale = time.monotonic() - state["beat"]
            if stale > threshold_sec and not state["dumped"]:
                state["dumped"] = True
                freeze_log.write(
                    f"\n===== UI FREEZE {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(UI 스레드 {stale:.1f}s 무응답) =====\n")
                freeze_log.flush()
                faulthandler.dump_traceback(file=freeze_log, all_threads=True)
                freeze_log.flush()
    threading.Thread(target=_watch, daemon=True, name="freeze-watchdog").start()
    logging.info("freeze watchdog 설치됨 (threshold=%.1fs) → %s", threshold_sec, LOG_DIR / "freeze.log")
    return hb


def main():
    setup_logging()
    install_crash_handlers()
    cleanup_temp_files()
    # 파형 캐시 버전 교체 정리 — 15k+ 파일 삭제 가능성이 있어 백그라운드로.
    from app.ui.player_widget import cleanup_stale_peaks_cache
    threading.Thread(target=cleanup_stale_peaks_cache,
                     daemon=True, name="peaks-cache-gc").start()
    _boost_ui_thread_priority()

    # GIL 핸드오프 주기 단축(기본 5ms → 1ms). 인덱싱/감지 워커가 CPU 바운드
    # 파이썬 루프로 GIL 을 오래 잡으면 UI 스레드(이벤트루프/로더)가 굶어 멈춘다
    # (freeze.log 로 확정 — detect_changes 단독으로 8.6s UI 무응답). 더 자주
    # 양보하게 해 UI 가 끼어들 틈을 준다. sleep(0) 은 GIL 을 실제로 안 놔줘 무효였음.
    sys.setswitchinterval(0.001)

    app = QApplication(sys.argv)
    # UI 스레드가 임계 이상 멈추면 전 스레드 스택을 freeze.log 에 덤프.
    _freeze_hb = install_freeze_watchdog(app, threshold_sec=2.0)

    # 중복 실행 방지 및 기존 인스턴스 활성화
    socket = QLocalSocket()
    socket.connectToServer(_SINGLE_INSTANCE_KEY)
    if socket.waitForConnected(500):
        # 이미 실행 중인 인스턴스가 있음
        socket.write(b"ACTIVATE")
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        logging.info("Existing instance found. Sent activation signal.")
        return # 현재 인스턴스 종료
    
    # 첫 번째 인스턴스: 서버 시작
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_KEY):
        # 이름이 이미 잡힘 = 동시 실행 경쟁에서 짐(아침 콜드 부팅 시 첫 앱이
        # listen 등록 직전 찰나에 두 번째 실행이 끼어든 경우). 다시 접속 시도.
        retry = QLocalSocket()
        retry.connectToServer(_SINGLE_INSTANCE_KEY)
        if retry.waitForConnected(1000):
            # 진짜 다른 인스턴스가 살아있음 → 활성화 신호 보내고 종료
            retry.write(b"ACTIVATE")
            retry.waitForBytesWritten(500)
            retry.disconnectFromServer()
            logging.info("동시 실행 감지 — 기존 인스턴스 활성화 후 종료.")
            return
        # 접속 안 됨 = 죽은 인스턴스의 잔여 소켓 → 정리 후 1회 재시도
        server.removeServer(_SINGLE_INSTANCE_KEY)
        if not server.listen(_SINGLE_INSTANCE_KEY):
            logging.error(f"Failed to start local server: {server.errorString()}")

    # DPI policy 는 Qt 기본값(Round) 사용 — Windows 파일탐색기/메모장 등 native
    # 앱과 동일한 동작 (1.25x → 1x, 1.5x → 2x 정수 픽셀 grid). PassThrough 강제는
    # fractional 픽셀 위치에 그리기 → 폰트 sub-pixel offset → 깨짐 주범.
    # (4K/QHD 1.5x → 2x 다운스케일 흐릿함은 별개. 1.25x 환경에선 Round 가 정답.)
            
    # 전역 툴팁 이벤트 필터 설치 (비활성 윈도우에서도 툴팁 노출 강제)
    tooltip_filter = _GlobalTooltipEventFilter(app)
    app.installEventFilter(tooltip_filter)
            
    # windows11 (또는 시스템 default) native style — 텍스트 ClearType subpixel
    # rendering 활성화. Fusion 강제는 Qt raster engine grayscale AA 라 텍스트
    # 깨져 보임. 마우스 reactivity 는 Windows 11 + 현대 PC 에서 native style 도
    # 충분히 빠름.
    _style_name = "windows11" if "windows11" in QStyleFactory.keys() else "windowsvista"
    app.setStyle(_NoFocusRectStyle(_style_name))
    app.setApplicationName("SoundField")
    # 폰트 override 완전 제거 — Qt 가 시스템 default font 그대로 사용.
    # (Windows 파일탐색기/메모장 과 동일 동작 시도)
    app.setStyleSheet(build_qss(theme=_load_theme_pref()))
    # 스플래시 — MainWindow 빌드(무거움) 전에 즉시 표시. 이벤트 처리 1회로 paint 보장.
    splash = SplashScreen()
    splash.show()
    app.processEvents()
    win = MainWindow()

    # 메시지 수신 시 윈도우 활성화 핸들러
    def handle_connection():
        client = server.nextPendingConnection()
        if client:
            if client.waitForReadyRead(1000):
                msg = client.readAll().data().decode()
                if msg == "ACTIVATE":
                    logging.info("Activation signal received. Bringing window to front.")
                    if win.isMinimized():
                        win.showNormal()
                    win.raise_()
                    win.activateWindow()
                    if sys.platform == "win32":
                        import ctypes
                        # SetForegroundWindow를 사용하여 윈도우를 맨 앞으로 가져옴
                        ctypes.windll.user32.SetForegroundWindow(win.winId())
            client.close()

    server.newConnection.connect(handle_connection)
    
    # 강제로 현재 툴팁이 있는 모든 위젯에 속성 적용 (초기 위젯들을 위해)
    for widget in app.allWidgets():
        if widget.toolTip():
            widget.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
            
    # 메인 창을 먼저 실제로 그려서 띄운 뒤 스플래시를 닫는다. 이렇게 순서를
    # 확정하지 않으면(예전엔 finish→show), splash.finish 가 win 의 첫 paint 에
    # 걸려 지연되는 사이, 시작 직후 뜨는 팝업/중앙 로더가 '항상 위' 스플래시 위에
    # 얹혀 "스플래시만 남고 메인창이 안 뜬" 것처럼 보였다 (인덱싱 중 종료 후 재시작
    # 시 특히 — 사용자 보고). show → processEvents(즉시 paint) → finish 로 확정.
    win.show()
    app.processEvents()
    splash.finish(win)   # 메인 창이 이미 표시됨 → 스플래시 즉시 닫힘
    sys.exit(app.exec())



if __name__ == "__main__":
    main()
