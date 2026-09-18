"""시작 스플래시 — YSG Audio Labs 브랜드 스플래시.

레이아웃(고정):
- 좌측 상단: YSG Audio Labs 리뉴얼 헤더 로고.
- 우측 하단: 제품명 SoundField(번들 디스플레이 폰트 Aldrich, 무채색 그라데이션 + 미세 그림자)
  + 버전 + 로딩(우측정렬, 작게).

배경: 하단 가중 라디얼 + 좌하단 사운드웨이브 동심원 + 필름 그레인 + sheen + 비네팅.
폰트: SoundField = 번들 Aldrich(app/assets/fonts), 그 외 = 시스템 기본(가장 선명).
asset: app/assets/ysg_labs_header_splash.png. QSplashScreen, 화면 중앙 + devicePixelRatio.
"""
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPen, QPixmap,
    QLinearGradient, QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QSplashScreen

APP_VERSION = "v1.3.0"

_W, _H = 640, 360
_ACCENT = QColor("#4E90E8")
_SUB_COL = QColor("#94a0b4")
_ASSETS = Path(__file__).resolve().parent.parent / "assets"

_LOGO_W = 340
_MX = 44

_HERO_FAMILY = None


def _hero_family() -> str:
    """번들 디스플레이 폰트(Aldrich) 로드 — 1회. 실패 시 시스템 폴백."""
    global _HERO_FAMILY
    if _HERO_FAMILY is None:
        fid = QFontDatabase.addApplicationFont(str(_ASSETS / "fonts" / "Aldrich.ttf"))
        fams = QFontDatabase.applicationFontFamilies(fid)
        _HERO_FAMILY = fams[0] if fams else "Segoe UI Semibold"
    return _HERO_FAMILY


def _font(size: int, weight=QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    """시스템 기본 폰트(앱 헤더와 동일, 가장 선명)."""
    f = QFont()
    f.setPointSize(size)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    return f


def _grain(w: int, h: int, alpha: int) -> QPixmap:
    """필름 그레인 — 그라데이션 밴딩 제거 + 매트 텍스처. 시드 고정."""
    rng = np.random.RandomState(7)
    n = rng.randint(40, 216, (h, w), dtype=np.uint8)
    buf = np.empty((h, w, 4), dtype=np.uint8)
    buf[..., 0] = n; buf[..., 1] = n; buf[..., 2] = n
    buf[..., 3] = alpha
    return QPixmap.fromImage(QImage(buf.tobytes(), w, h, QImage.Format.Format_ARGB32).copy())


class SplashScreen(QSplashScreen):
    def __init__(self):
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0
        _hero_family()

        canvas = QPixmap(int(_W * dpr), int(_H * dpr))
        canvas.setDevicePixelRatio(dpr)
        self._paint_base(canvas, dpr)

        super().__init__(canvas)
        # '항상 위' 해제 + 일반 Window 타입(작업표시줄 버튼 생성). 다른 창을 클릭하면
        # 뒤로 내려가고, 작업표시줄의 SoundField 를 클릭하면 다시 앞으로 올라온다.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("SoundField")
        self.setWindowIcon(QIcon(str(_ASSETS / "icon.ico")))
        self._center_on_screen(screen)

    def mousePressEvent(self, e):
        # 아무 동작도 하지 않는다. 기본 QSplashScreen 은 클릭 시 숨고, raise_/
        # activateWindow 를 걸면 로딩으로 바쁜 메인 스레드에 이벤트가 쌓여 '응답 없음'
        # 이 뜬다. 앞으로 올리는 건 작업표시줄 클릭으로 충분.
        pass

    def _paint_base(self, canvas: QPixmap, dpr: float):
        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        full = QRectF(0, 0, _W, _H)

        # 배경 — 하단 가중 라디얼(우상단 로고는 어둡게)
        p.fillRect(full, QColor("#090b0f"))
        g = QRadialGradient(QPointF(_W * 0.46, _H * 0.86), _W * 0.82)
        g.setColorAt(0.0, QColor("#2a323d")); g.setColorAt(0.5, QColor("#151920")); g.setColorAt(1.0, QColor("#080a0e"))
        p.fillRect(full, g)

        # 좌하단 사운드웨이브 동심원 텍스처
        p.save(); p.setClipRect(full)
        origin = QPointF(26, _H - 22)
        for i in range(1, 22):
            p.setPen(QPen(QColor(130, 160, 210, max(0, 20 - i)), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(origin, i * 28.0, i * 28.0)
        p.restore()

        # 그레인
        grain = _grain(int(_W * dpr), int(_H * dpr), 9)
        grain.setDevicePixelRatio(dpr)
        p.drawPixmap(0, 0, grain)

        # sheen + 비네팅 + 상단 하이라이트 엣지
        sh = QLinearGradient(0, 0, _W, _H)
        sh.setColorAt(0.0, QColor(255, 255, 255, 13)); sh.setColorAt(0.42, QColor(255, 255, 255, 0))
        p.fillRect(full, sh)
        vig = QRadialGradient(QPointF(_W / 2, _H / 2), _W * 0.62)
        vig.setColorAt(0.0, QColor(0, 0, 0, 0)); vig.setColorAt(0.82, QColor(0, 0, 0, 0)); vig.setColorAt(1.0, QColor(0, 0, 0, 90))
        p.fillRect(full, vig)
        p.fillRect(QRectF(0, 0, _W, 1), QColor(255, 255, 255, 26))

        # 좌측 상단 — 리뉴얼된 YSG Audio Labs 헤더 로고.
        bar = QLinearGradient(0, 36, 0, 110)
        bar.setColorAt(0.0, _ACCENT); bar.setColorAt(1.0, QColor(78, 144, 232, 40))
        p.fillRect(QRectF(_MX, 36, 3, 82), bar)
        labs_logo = QPixmap(str(_ASSETS / "ysg_labs_header_splash.png"))
        if not labs_logo.isNull():
            sc = labs_logo.scaled(int(_LOGO_W * dpr), int(96 * dpr),
                                  Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            sc.setDevicePixelRatio(dpr)
            logo_y = 50 - sc.height() / (2 * dpr)
            p.drawPixmap(int(_MX + 18), int(logo_y), sc)

        p.setFont(_font(9, QFont.Weight.DemiBold, spacing=112))
        p.setPen(_SUB_COL)
        p.drawText(QRectF(_MX + 20, 98, 320, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "SOUND LIBRARY MANAGER")

        # 우측 하단 — 제품명(Aldrich, 무채색 그라데이션 + 미세 그림자)
        rx = _W - _MX
        align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        nr = QRectF(180, 228, _W - 180 - _MX, 54)
        hf = QFont(_hero_family()); hf.setPointSize(40); hf.setWeight(QFont.Weight.DemiBold)
        p.setFont(hf)
        p.setPen(QColor(0, 0, 0, 115))
        p.drawText(nr.translated(1.3, 1.7), align, "SoundField")
        grad = QLinearGradient(0, 234, 0, 282)
        grad.setColorAt(0.0, QColor("#d8dee7")); grad.setColorAt(1.0, QColor("#8b95a4"))
        pen = QPen(); pen.setBrush(QBrush(grad)); p.setPen(pen)
        p.drawText(nr, align, "SoundField")

        line = QLinearGradient(rx - 130, 0, rx, 0)
        line.setColorAt(0.0, QColor(78, 144, 232, 0)); line.setColorAt(1.0, _ACCENT)
        p.fillRect(QRectF(rx - 130, 290, 130, 2.5), line)
        p.setFont(_font(9))
        p.setPen(_SUB_COL)
        p.drawText(QRectF(220, 300, _W - 220 - _MX, 16), align, APP_VERSION)
        p.drawText(QRectF(220, 320, _W - 220 - _MX, 16), align, "라이브러리 로딩 중…")

        # 하단 풀폭 액센트 라인
        bottom = QLinearGradient(0, 0, _W, 0)
        bottom.setColorAt(0.0, QColor(78, 144, 232, 0))
        bottom.setColorAt(0.5, QColor(78, 144, 232, 120))
        bottom.setColorAt(1.0, QColor(78, 144, 232, 0))
        p.fillRect(QRectF(0, _H - 2, _W, 2), bottom)
        p.end()

    def _center_on_screen(self, screen):
        if screen is None:
            return
        geo = screen.geometry()
        self.move(geo.center().x() - _W // 2, geo.center().y() - _H // 2)
