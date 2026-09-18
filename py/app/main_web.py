"""WebEngine 기반 엔트리.

HTML 디자인 프로토타입(`app/web/SoundField.html`)을 그대로 띄우고,
검색/인덱싱/파형/재생 백엔드는 `app/bridge.py` 의 `AppBridge` 를 통해 연결한다.

기존 `main.py` (PyQt 위젯 버전)은 그대로 둠 — 비교/롤백용.
"""
import faulthandler
import logging
import os
import sys
import traceback
from pathlib import Path

# DevTools (앱 실행 후 chrome 에서 http://localhost:9223 접속하면 콘솔 확인 가능)
os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "9223")

from PyQt6.QtCore import QtMsgType, QUrl, qInstallMessageHandler
from PyQt6.QtGui import QFont
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow

from app.bridge import AppBridge
from app.database import Database
from app.library_manager import LibraryManager
from app.region_export import cleanup_temp_files

LOG_DIR = Path.home() / "AppData" / "Local" / "SoundField" / "logs"
DEFAULT_STORE = Path.home() / "AppData" / "Local" / "SoundField"
WEB_DIR = Path(__file__).resolve().parent / "web"

_fault_log = None


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
        where = f" ({ctx.file}:{ctx.line})" if ctx and ctx.file else ""
        logging.getLogger("qt").log(lvl, "%s%s", message, where)
    qInstallMessageHandler(_qt_msg)


class _LoggingPage(QWebEnginePage):
    """JS 콘솔 메시지 + 로드 실패를 파이썬 로거로."""
    def javaScriptConsoleMessage(self, level, message, line, source):
        try:
            lv = int(level.value) if hasattr(level, "value") else int(level)
        except Exception:
            lv = 0
        lvl = {0: logging.INFO, 1: logging.WARNING, 2: logging.ERROR}.get(lv, logging.INFO)
        logging.getLogger("web").log(lvl, "[%s:%s] %s", source, line, message)


class WebMainWindow(QMainWindow):
    def __init__(self, bridge: AppBridge):
        super().__init__()
        self.setWindowTitle("SoundField — Sound Effect Library")
        self.resize(1500, 900)

        self.view = QWebEngineView(self)
        self.page = _LoggingPage(self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)

        # file:// 에서 jsx fetch / CDN / 이미지 모두 허용
        s = self.page.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, True)

        # QWebChannel 로 bridge 객체를 JS 전역(qt.webChannelTransport)에 노출
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("bridge", bridge)
        self.page.setWebChannel(self.channel)

        # 로드 결과 로그
        self.view.loadFinished.connect(
            lambda ok: logging.getLogger("web").info("loadFinished ok=%s", ok)
        )

        # HTML 로드
        html_path = WEB_DIR / "SoundField.html"
        logging.getLogger("web").info("loading %s", html_path)
        self.view.load(QUrl.fromLocalFile(str(html_path)))


def main():
    setup_logging()
    install_crash_handlers()
    cleanup_temp_files()

    app = QApplication(sys.argv)
    app.setApplicationName("SoundField")
    app.setFont(QFont("Segoe UI", 9))

    manager = LibraryManager(DEFAULT_STORE)
    db = manager.open_db_for_search()
    bridge = AppBridge(manager, db)

    win = WebMainWindow(bridge)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
