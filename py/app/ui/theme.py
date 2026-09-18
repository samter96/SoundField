"""테마 색 토큰 + 전역 QSS — dark / light 두 세트.

[v5 multi-theme 2026-05-20] dark + light 그레이 토글 지원.
- COLORS = 런타임 활성 dict (다른 모듈은 이 dict 참조 유지)
- apply_theme("dark"|"light") 가 in-place update → setStyleSheet 재호출 시 적용
- paintEvent 위젯은 current_theme() 으로 분기
"""
from pathlib import Path
from typing import Optional

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
DOWN_ARROW_URL_DARK = (_ASSETS / "down-arrow.svg").as_posix()
DOWN_ARROW_URL_LIGHT = (_ASSETS / "down-arrow-light.svg").as_posix()
DOWN_ARROW_URL_GREY = (_ASSETS / "down-arrow-grey.svg").as_posix()
BRANCH_CLOSED_URL = (_ASSETS / "branch_closed.svg").as_posix()
BRANCH_OPEN_URL = (_ASSETS / "branch_open.svg").as_posix()
CHECK_URL_DARK = (_ASSETS / "check.svg").as_posix()
CHECK_URL_LIGHT = (_ASSETS / "check-light.svg").as_posix()
CHECK_URL_GREY = (_ASSETS / "check-grey.svg").as_posix()

COLORS_DARK = {
    # backgrounds
    "bg":            "#0b0e15",
    "bg_panel":      "#0f1219",
    "bg_elev":       "#14181f",
    "bg_header":     "#090c12",
    "bg_sidebar":    "#0f1219",
    "bg_control":    "#12161d",
    "bg_control_hi": "#181d28",

    # borders — structural only, very subtle
    "border":        "#1b2130",
    "border_strong": "#242c3a",
    "border_soft":   "#141820",

    # text — warm neutrals, clear hierarchy
    "text":           "#c4cdd8",
    "text_secondary": "#6a7688",
    "text_muted":     "#414d5c",
    "text_caps":      "#4e5a6a",

    # accent — refined blue
    "accent":        "#4278c0",
    "accent_hover":  "#5290d4",
    "accent_pressed":"#3060a4",
    "accent_dim":    "#091c38",
    "accent_warm":   "#c89840",
    "accent_teal":   "#5a8cc4",
    "accent_glow":   "rgba(66, 120, 192, 50)",

    # state colors
    "hover":         "#141a28",
    "row_alt":       "#0d1018",
    "row_hover":     "#131928",
    "row_selected":  "#0a1e38",
    "row_playing":   "#0c2244",

    "warn":   "#c89840",
    "danger": "#c44848",

    # primary 버튼 텍스트 (accent 배경 위)
    "on_accent":     "#0a0d13",
}

# Cursor 데스크톱 앱 풍 — 따뜻한 베이지/원 그레이 (노란빛 도는 차분한 톤).
COLORS_LIGHT = {
    "bg":            "#ebe6d8",   # 메인 배경
    "bg_panel":      "#e1dccd",
    "bg_elev":       "#f3eee0",   # elevated/input
    "bg_header":     "#ddd6c4",
    "bg_sidebar":    "#e1dccd",
    "bg_control":    "#f3eee0",
    "bg_control_hi": "#ebe6d8",

    "border":        "#c8c0aa",
    "border_strong": "#aea58b",
    "border_soft":   "#d5cdb8",

    "text":           "#2b2820",   # 진한 다크 브라운 — 베이지 위 가독성
    "text_secondary": "#6e6857",
    "text_muted":     "#9b9580",
    "text_caps":      "#85806e",

    # accent: 원 다크 브라운 — 검은 계열, 베이지 위에 단호한 포인트
    "accent":        "#332e22",
    "accent_hover":  "#46402f",
    "accent_pressed":"#1f1c14",
    "accent_dim":    "#ddd6c0",
    "accent_warm":   "#a07820",
    "accent_teal":   "#332e22",   # 라이트는 액센트 통일 (blue 분기 제거)
    "accent_glow":   "rgba(51, 46, 34, 45)",

    "hover":         "#e0d9c4",
    "row_alt":       "#e5dfcd",
    "row_hover":     "#dcd5be",
    "row_selected":  "#cdc4a8",
    "row_playing":   "#c2b994",

    "warn":   "#a07820",
    "danger": "#9c3535",

    "on_accent":     "#ffffff",
}

# 모노 그레이 — 무채색 그레이스케일 (스크린샷 참고: 순수 어두운 회색).
COLORS_GREY = {
    "bg":            "#1c1c1c",
    "bg_panel":      "#1f1f1f",
    "bg_elev":       "#262626",
    "bg_header":     "#161616",
    "bg_sidebar":    "#1c1c1c",
    "bg_control":    "#1a1a1a",
    "bg_control_hi": "#2a2a2a",

    "border":        "#303030",
    "border_strong": "#3a3a3a",
    "border_soft":   "#232323",

    "text":           "#c8c8c8",
    "text_secondary": "#707070",
    "text_muted":     "#4a4a4a",
    "text_caps":      "#555555",

    "accent":        "#888888",
    "accent_hover":  "#999999",
    "accent_pressed":"#777777",
    "accent_dim":    "#1f1f1f",
    "accent_warm":   "#b0b0b0",
    "accent_teal":   "#888888",
    "accent_glow":   "rgba(136, 136, 136, 50)",

    "hover":         "#232323",
    "row_alt":       "#1a1a1a",
    "row_hover":     "#232323",
    "row_selected":  "#2a2a2a",
    "row_playing":   "#303030",

    "warn":   "#b0b0b0",
    "danger": "#c44848",

    "on_accent":     "#1a1a1a",
}

# 런타임 활성 dict — 다른 모듈은 이 참조를 잡고 in-place update 로 색이 바뀜.
# 기본 테마 = 뉴트럴(grey 내부 키). 사용자 결정.
COLORS = dict(COLORS_GREY)
_CURRENT_THEME = "grey"


def current_theme() -> str:
    return _CURRENT_THEME


# 사용자 노출 이름 ↔ 내부 색 dict 매핑.
# neon=다크네이비(기존 dark), light=베이지, neutral=무채색 그레이(신규 기본).
_THEME_ALIASES = {
    "neon": "dark",      # 신규 사용자 노출 라벨 → 기존 내부 dict 키
    "neutral": "grey",
    # 호환: 옛 config 의 "dark"/"grey" 도 그대로 받음
}


def _resolve_theme(name: str) -> str:
    """legal 테마 이름으로 정규화. alias 변환 포함."""
    name = _THEME_ALIASES.get(name, name)
    return name if name in ("dark", "light", "grey") else "grey"


def apply_theme(name: str) -> str:
    """COLORS 를 in-place 교체. 호출 후 QApplication.setStyleSheet(build_qss()) 필요."""
    global _CURRENT_THEME
    name = _resolve_theme(name)
    src = {"dark": COLORS_DARK, "light": COLORS_LIGHT, "grey": COLORS_GREY}[name]
    COLORS.clear()
    COLORS.update(src)
    _CURRENT_THEME = name
    return name

# 폰트 세트 정의 — Pretendard (한글 stroke 명료) + Cascadia Code (모노)
FONT_UI = '"Pretendard", "Segoe UI Variable Text", "Segoe UI", "Malgun Gothic", sans-serif'
FONT_DATA = '"Cascadia Code", "Cascadia Mono", "Consolas", "JetBrains Mono", monospace'

def build_qss(theme: Optional[str] = None) -> str:
    if theme is not None:
        apply_theme(theme)
    C = COLORS
    # 테마별 SVG 분리 — dark(neon)=teal, light=다크브라운, grey(neutral)=무채색
    if _CURRENT_THEME == "light":
        DOWN_ARROW_URL = DOWN_ARROW_URL_LIGHT
        CHECK_URL = CHECK_URL_LIGHT
    elif _CURRENT_THEME == "grey":
        DOWN_ARROW_URL = DOWN_ARROW_URL_GREY
        CHECK_URL = CHECK_URL_GREY
    else:
        DOWN_ARROW_URL = DOWN_ARROW_URL_DARK
        CHECK_URL = CHECK_URL_DARK
    return f"""
/* ====== 기본 ======
   font-family 전역 강제 제거 — 시스템 default font 사용 (Windows 익스플로러와
   동일 path). 개별 위젯 의도된 폰트는 objectName 별 명시 유지. */
QWidget {{
    background: {C["bg"]};
    color: {C["text"]};
    font-size: 11px;
}}
QMainWindow, QDialog {{ background: {C["bg"]}; }}
QToolTip {{
    background: {C["bg_elev"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    padding: 6px 9px;
    border-radius: 4px;
}}

/* ====== 상태바 ====== */
QStatusBar {{
    background: {C["bg_header"]};
    color: {C["text_secondary"]};
    border-top: 1px solid {C["border"]};
    padding: 2px 8px;
    font-size: 10px;
}}
QStatusBar::item {{ border: none; }}

/* ====== 탭 (환경설정 등) ====== */
QTabWidget::pane {{
    border: 1px solid {C["border_strong"]};
    background: {C["bg_panel"]};
    top: -1px;
}}
QTabBar::tab {{
    background: {C["bg_control"]};
    color: {C["text"]};
    padding: 7px 16px;
    border: 1px solid {C["border_strong"]};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    margin-right: 2px;
    font-size: 12px;
}}
QTabBar::tab:hover {{
    background: {C["bg_control_hi"]};
}}
QTabBar::tab:selected {{
    background: {C["bg_panel"]};
    color: {C["text"]};
    border-top: 2px solid {C["accent"]};
}}

/* ====== 파일 브라우저 FlowTabBar (라이브러리 탭) ======
   환경설정 QTabBar 와 동일 어휘 — 위쪽만 둥근 모서리, 아래쪽은 본문과 이어진 느낌
   (border-bottom 없음). selected 시 본문과 같은 배경(bg_panel) + 위쪽 accent 라인. */
QWidget#flowTabBar {{ background: transparent; }}
QToolButton#flowTabButton {{
    background: {C["bg_control"]};
    color: {C["text_secondary"]};
    padding: 6px 14px 7px 14px;
    border: 1px solid {C["border_strong"]};
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
    margin-right: 1px;
    font-size: 11px;
    font-weight: 500;
}}
QToolButton#flowTabButton:hover {{
    background: {C["bg_control_hi"]};
    color: {C["text"]};
    border-color: {C["accent_hover"]};
    border-bottom: none;
}}
QToolButton#flowTabButton:checked {{
    background: {C["bg_panel"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    border-top: 2px solid {C["accent"]};
    border-bottom: none;
    padding-top: 5px;
    font-weight: 700;
}}
QToolButton#flowTabButton:checked:hover {{
    background: {C["bg_panel"]};
    border-top: 2px solid {C["accent_hover"]};
}}
QToolButton#flowTabAddButton {{
    background: transparent;
    color: {C["text_muted"]};
    border: 1px dashed {C["border_strong"]};
    border-radius: 7px;
    padding: 3px 10px 4px 10px;
    font-size: 14px;
    font-weight: 700;
}}
QToolButton#flowTabAddButton:hover {{
    color: {C["accent_hover"]};
    border: 1px dashed {C["accent_hover"]};
}}
/* 돋보기 버튼 — 비주얼은 paintEvent 가 그리고, 여기선 (+)버튼과 동일한 크기가
   되도록 sizeHint 계산용 폰트/패딩만 맞춘다 (테두리/배경은 paintEvent 가 덮음). */
QToolButton#flowTabSearchButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 3px 10px 4px 10px;
    font-size: 14px;
    font-weight: 700;
}}

/* ====== 버튼 ====== */
QLabel#searchCountLabel {{
    color: {C["text_muted"]};
    padding: 0 6px;
    font-size: 11px;
}}
QToolButton#searchNavButton {{
    background: {C["bg_control"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    border-radius: 4px;
    min-width: 24px;
    min-height: 22px;
    padding: 1px 6px;
    font-size: 12px;
    font-weight: 700;
}}
QToolButton#searchNavButton:hover {{
    background: {C["bg_control_hi"]};
    border-color: {C["accent_hover"]};
}}
QToolButton#searchNavButton:disabled {{
    color: {C["text_muted"]};
    background: {C["bg_panel"]};
    border-color: {C["border_soft"]};
}}
QListView#librarySearchDropdown {{
    background: {C["bg_control_hi"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    selection-background-color: {C["row_selected"]};
    selection-color: {C["text"]};
}}

QPushButton {{
    background: {C["bg_elev"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    border-radius: 2px;
    padding: 4px 10px;
    min-height: 20px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
}}
QPushButton:hover {{
    background: {C["bg_control_hi"]};
    border-color: {C["border_strong"]};
    color: {C["text"]};
}}
QPushButton:pressed {{
    background: {C["bg_control"]};
    border-color: {C["accent_pressed"]};
    padding-top: 7px;
    padding-bottom: 5px;
}}
QPushButton:focus {{ outline: none; border-color: rgba(66, 120, 192, 90); }}
QPushButton:disabled {{
    color: {C["text_muted"]};
    background: {C["bg_panel"]};
    border-color: {C["border"]};
}}

/* primary (액센트 채움 — 목업 일치) */
QPushButton#primary {{
    background: {C["accent"]};
    color: {C["on_accent"]};
    border: 1px solid {C["accent"]};
    border-radius: 2px;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.02em;
    padding: 0 12px;
    min-height: 24px;
}}
QPushButton#primary:hover {{
    background: {C["accent_hover"]};
}}
QPushButton#primary:pressed {{
    background: {C["accent_pressed"]};
}}

/* 빠른/전체 갱신 버튼은 AnimButton(paintEvent)로 그려 QSS 불필요. 복구(앰버)만 유지. */
QPushButton#ftsRecovery {{
    color: #b89040;
    background: transparent;
    border: 1px solid rgba(184, 144, 64, 55);
    padding: 0 12px;
    min-height: 24px;
    font-weight: 600;
}}
QPushButton#ftsRecovery:hover {{
    background: rgba(184, 144, 64, 18);
    border-color: rgba(184, 144, 64, 90);
}}


QPushButton#icon {{
    padding: 4px 7px;
    min-height: 20px;
    font-size: 12px;
}}
QPushButton#transport,
QPushButton#transport:default,
QPushButton#transport:flat {{
    padding: 2px 4px;
    min-width: 32px;
    min-height: 28px;
    border: 0px solid transparent;
    background-color: transparent;
    background-image: none;
    color: {C["text"]};
    font-size: 14px;
}}
QPushButton#transport:hover {{
    background-color: transparent;
    color: {C["accent_hover"]};
    border: 0px solid transparent;
}}
QPushButton#transport:pressed {{
    background-color: transparent;
    color: {C["accent"]};
    border: 0px solid transparent;
    padding: 2px 4px;
}}
QPushButton#transport:focus {{
    background-color: transparent;
    outline: none;
    border: 0px solid transparent;
}}

/* 재생 버튼 — 그라데이션. 재생 중이면 warm */
QPushButton#transportPlay {{
    padding: 2px;
    min-width: 36px;
    min-height: 28px;
    border-radius: 3px;
    font-size: 14px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {C["accent_hover"]},
                                stop:1 {C["accent"]});
    color: #001020;
    border: 1px solid {C["accent_hover"]};
    font-weight: 700;
}}
QPushButton#transportPlay:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #7ab4e8,
                                stop:1 {C["accent_hover"]});
    border-color: #7ab4e8;
}}
QPushButton#transportPlay:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {C["accent"]},
                                stop:1 {C["accent_pressed"]});
    border-color: {C["accent"]};
    padding-top: 2px; padding-bottom: 2px;
}}
QPushButton#transportPlay:focus {{ outline: none; }}
QPushButton#transportPlay[playing="true"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #d4aa60,
                                stop:1 {C["accent_warm"]});
    border-color: #d4aa60;
    color: #1a1000;
}}
QPushButton#transportPlay[playing="true"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #e0be80,
                                stop:1 #d4aa60);
    border-color: #e0be80;
}}

QPushButton#pill {{
    border-radius: 10px;
    padding: 2px 12px;
}}

/* 취소 — 빨간 테두리/아이콘 (정지 ■) */
QPushButton#cancelDanger {{
    background: transparent;
    color: {C["danger"]};
    border: 1px solid rgba(196, 72, 72, 60);
    border-radius: 2px;
    padding: 0 12px;
    font-weight: 600;
    min-height: 24px;
}}
QPushButton#cancelDanger:hover {{
    background: rgba(196, 72, 72, 20);
    color: #d06060;
    border-color: rgba(196, 72, 72, 100);
}}
QPushButton#cancelDanger:pressed {{
    background: rgba(196, 72, 72, 40);
}}
QPushButton#cancelDanger:disabled {{
    color: rgba(196, 72, 72, 70);
    border-color: rgba(196, 72, 72, 50);
    background: transparent;
}}

/* 필터 추가 */
QPushButton#rowAdd {{
    background: transparent;
    color: {C["text_secondary"]};
    border: 1px solid {C["border_strong"]};
    border-radius: 3px;
    padding: 0 12px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
}}
QPushButton#rowAdd:hover {{
    color: {C["text"]};
    border-color: {C["accent"]};
    background: rgba(66, 120, 192, 15);
}}

/* 검색 문법 도움말 ? 버튼 — 완전 원형 (22×22, radius=11), 2px 외곽선 */
QPushButton#searchHelp {{
    background: transparent;
    color: {C["text_secondary"]};
    border: 1px solid {C["border_strong"]};
    border-radius: 7px;
    padding: 0;
    font-size: 11px;
    font-weight: 700;
    min-width: 24px; min-height: 24px;
    max-width: 24px; max-height: 24px;
}}
QPushButton#searchHelp:hover {{
    color: {C["accent_hover"]};
    border-color: rgba(66, 120, 192, 125);
    background: rgba(66, 120, 192, 42);
}}
QPushButton#searchHelp:pressed {{
    color: {C["accent_pressed"]};
    border-color: {C["accent_pressed"]};
    background: rgba(66, 120, 192, 58);
}}

/* ====== 라인에디트 / 스핀 / 콤보 ====== */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {C["bg_control"]};
    color: {C["text"]};
    border: 1px solid {C["border"]};
    border-radius: 3px;
    padding: 3px 8px;
    selection-background-color: {C["accent"]};
    selection-color: {C["on_accent"]};
    font-size: 12px;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: {C["border_strong"]};
    background: {C["bg_control_hi"]};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid rgba(66, 120, 192, 90);
    background: {C["bg_control_hi"]};
}}
/* 스핀박스 내부 lineEdit 은 컨테이너(스핀박스) padding 위에 자기 padding/테두리를
   또 얹어 텍스트가 콤보보다 더 들여써지고(첫 글자 좌측 여백↑) 세로로도 커 보였다.
   내부는 0 으로 두고 스핀박스 padding 만 적용 → 콤보와 좌측 정렬·높이 동일. */
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {{
    border: none;
    padding: 0;
    background: transparent;
}}
/* 내부 lineEdit 의 기본 좌측 여백(~2px, 커서 자리)만큼 스핀박스 좌측 padding 을
   줄여 콤보 텍스트 시작점과 정렬 (콤보는 8px 유지). */
QSpinBox, QDoubleSpinBox {{
    padding-left: 6px;
}}
QLineEdit#search {{
    font-size: 13px;
    padding: 2px 12px 4px;
    border-radius: 3px;
    border-color: {C["border_strong"]};
}}
QLineEdit#search:focus {{
    border: 1px solid rgba(66, 120, 192, 135);
    background: {C["bg_control_hi"]};
}}

QComboBox {{
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: url("{DOWN_ARROW_URL}");
    width: 7px;
    height: 4px;
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {C["bg_elev"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    selection-background-color: rgba(78, 144, 232, 30);
    outline: none;
    padding: 4px 0;
    min-width: 130px;
}}
QComboBox QAbstractItemView::item {{
    padding: 8px 14px 8px 22px;
    border-radius: 2px;
    margin: 1px 4px;
    min-height: 16px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {C["hover"]};
}}
QComboBox QAbstractItemView::item:selected {{
    background: rgba(78, 144, 232, 28);
    color: {C["text"]};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent; border: none; width: 14px;
}}
QSpinBox::up-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {C["text_secondary"]};
}}
QSpinBox::down-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {C["text_secondary"]};
}}

/* ====== 라디오 버튼 & 체크박스 ====== */
QRadioButton, QCheckBox {{
    spacing: 8px;
    color: {C["text"]};
    background: transparent;
    padding: 2px 0;
}}
QRadioButton::indicator, QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1.5px solid {C["border_strong"]};
    background: {C["bg_control"]};
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator {{
    border-radius: 2px;
}}
QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
    border-color: {C["accent"]};
    background: {C["hover"]};
}}
/* checked — fill 제거, accent 경계선 + 안쪽 표식만.
   라디오: 안쪽 작은 accent 점 (fill 대신 경계선 강조).
   체크박스: accent 색 ✓ 글리프만. 까만색이 꽉 차는 시각 부담 제거.
   SVG fill/stroke 색은 accent 토큰을 직접 박는다 — 테마 스왑 시 자동 갱신. */
QRadioButton::indicator:checked {{
    background: {C["bg_control"]};
    border: 1.5px solid {C["accent"]};
    image: url("{CHECK_URL}");
}}
QCheckBox::indicator:checked {{
    background: {C["bg_control"]};
    border: 1.5px solid {C["accent"]};
    image: url("{CHECK_URL}");
}}

/* ====== 테이블 ====== */

/* 셀 텍스트는 트리(라이브러리 종류)와 동일한 FONT_UI — 한글 글리프 fallback 일치. */
QTableWidget, QTableView {{
    background: {C["bg"]};
    alternate-background-color: {C["row_alt"]};
    color: {C["text"]};
    gridline-color: transparent;
    border: 1px solid {C["border"]};
    border-radius: 0;
    outline: none;
    font-size: 12px;
}}
QTableWidget::item, QTableView::item {{
    padding: 5px 10px;
    border: none;
    border-left: 2px solid transparent;
}}
QTableWidget::item:hover, QTableView::item:hover {{
    background: {C["row_hover"]};
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {C["row_selected"]};
    color: {C["text"]};
    /* border-left 강조선은 0번 컬럼 델리게이트가 행 단위로 그림.
       여기서 셀마다 주면 컬럼 경계마다 세로선이 생김 → transparent 유지. */
    border-left: 2px solid transparent;
}}
QHeaderView::section {{
    background: {C["bg_header"]};
    color: {C["text_caps"]};
    padding: 7px 10px;
    border: none;
    border-right: 1px solid {C["border"]};
    border-bottom: 1px solid {C["border"]};
    font-weight: 800;
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}}
QHeaderView::section:hover {{
    background: {C["hover"]};
    color: {C["text"]};
}}

/* ====== 트리 (사이드바) ====== */
QTreeWidget, QTreeView {{
    background: {C["bg_sidebar"]};
    color: {C["text_secondary"]};
    border: 1px solid {C["border"]};
    border-radius: 0;
    outline: none;
    outline: 0;
    padding: 4px 0;
    show-decoration-selected: 0;
    font-size: 12px;
}}
QTreeWidget::item, QTreeView::item {{
    padding: 5px 4px;
    border-radius: 0;
    margin: 1px 4px;
    border: none;
    outline: none;
    outline: 0;
}}
QTreeWidget::item:hover, QTreeView::item:hover {{
    background: {C["hover"]};
    color: {C["text"]};
    border: none;
    outline: none;
    outline: 0;
}}
/* selected: 어떤 상황(포커스, 활성/비활성)에서도 테두리(파란색/하얀색 모두)가 나오지 않도록 완전 삭제 */
QTreeWidget::item:selected,
QTreeView::item:selected,
QTreeWidget::item:selected:active,
QTreeView::item:selected:active,
QTreeWidget::item:selected:!active,
QTreeView::item:selected:!active,
QTreeWidget::item:focus,
QTreeView::item:focus {{
    background: transparent;   /* 선택 배경은 FolderTree.drawRow 가 뷰포트 폭 전체로
                                  직접 칠함 — column ResizeToContents 라 QSS 에 맡기면
                                  펼침/접힘에 따라 폭이 달라짐. 색은 row_selected. */
    color: {C["text"]};
    border: none;
    outline: none;
    outline: 0;
}}
/* branch 전체 background 만 transparent — 화살표/line 은 FolderTree.drawBranches
   override 가 모두 처리 (QSS 만으로는 native delegate 의 line paint 차단 불가).
   branch 영역의 모든 테두리 및 포커스 사각형 제거. */
QTreeView::branch,
QTreeView::branch:selected,
QTreeView::branch:focus,
QTreeView::branch:selected:focus,
QTreeView::branch:selected:active,
QTreeView::branch:selected:!active {{
    background: transparent;
    border: none;
    outline: none;
    outline: 0;
}}
QTreeWidget QHeaderView::section {{
    background: {C["bg_sidebar"]};
    color: {C["text_caps"]};
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 0.16em;
    padding: 9px 8px;
    border: none;
    border-bottom: 1px solid {C["border"]};
}}

/* ====== 블랙리스트 패널 ====== */
QWidget#blacklistPanel {{ background: {C["bg_sidebar"]}; }}
QFrame#blacklistSep {{ background: {C["border"]}; border: none; }}
QWidget#blacklistHeader {{ background: {C["bg_sidebar"]}; }}
QPushButton#blacklistToggle {{
    background: transparent;
    color: {C["text_caps"]};
    border: none;
    padding: 2px 0;
    text-align: left;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.10em;
}}
QPushButton#blacklistToggle:hover {{ color: {C["text"]}; }}
QWidget#blacklistRows {{ background: {C["bg_sidebar"]}; }}
QWidget#blacklistRow {{ background: transparent; }}
QWidget#blacklistRow:hover {{ background: {C["hover"]}; }}
QLabel#blacklistPath {{
    color: {C["text"]};
    font-size: 11px;
    font-weight: 600;
    background: transparent;
    padding: 0;
}}
QLabel#blacklistDesc {{
    color: {C["text_secondary"]};
    font-size: 10px;
    background: transparent;
    padding: 0;
}}
QLabel#blacklistEmpty {{
    color: {C["text_muted"]};
    font-size: 11px;
    background: transparent;
}}

/* ====== 히스토리 패널 (overlay) ======
   results_wrap 안 absolute 위치 overlay. 좌측 6px historyGrip 으로 너비 조절. */
QWidget#historyPanel {{
    background: transparent;
    border: none;
}}
QWidget#historyContent {{
    background: {C["bg_sidebar"]};
}}
/* historyGrip 은 paintEvent 직접 그림 — QSS 배경 비움 */
QListWidget#historyList {{
    background: {C["bg_sidebar"]};
    color: {C["text_secondary"]};
    border: none;
    outline: 0;
    padding: 2px 0;
}}
QListWidget#historyList::item {{
    padding: 6px 10px;
    border: none;
    border-left: 2px solid transparent;
    border-bottom: 1px solid {C["border_soft"]};
}}
QListWidget#historyList::item:hover {{
    background: {C["hover"]};
    border-left-color: {C["accent"]};
    color: {C["text"]};
}}
QListWidget#historyList::item:selected {{
    background: {C["row_selected"]};
    border-left-color: {C["accent_hover"]};
    color: {C["text"]};
}}

/* ====== 스크롤바 ====== */
QScrollBar:vertical {{
    background: {C["bg_control"]};
    width: 12px;
    margin: 0;
    border-radius: 6px;
}}
QScrollBar::handle:vertical {{
    background: {C["border_strong"]};
    border: 1px solid transparent;
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C["accent"]};
    border: 1px solid transparent;
    border-radius: 5px;
    min-height: 30px;
    margin: 2px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent; border: none; height: 0;
}}
QScrollBar:horizontal {{
    background: {C["bg_control"]};
    height: 12px;
    border-radius: 6px;
}}
QScrollBar::handle:horizontal {{
    background: {C["border_strong"]};
    border: 1px solid transparent;
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C["accent"]};
    border: 1px solid transparent;
    border-radius: 5px;
    min-width: 30px;
    margin: 2px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent; border: none; width: 0;
}}

/* ====== 프로그레스바 ====== */
QProgressBar {{
    background: {C["bg_control"]};
    border: 1px solid {C["border"]};
    border-radius: 99px;
    text-align: center;
    color: {C["text"]};
    height: 18px;
    font-size: 11px;
    font-weight: 600;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {C["accent"]},
                                stop:1 {C["accent_teal"]});
    border-radius: 99px;
}}

/* ====== 스플리터 ====== */
/* base: 미세한 border 톤, hover: accent (테마별 색), pressed: 더 짙은 accent */
/* Qt QSS 는 :!disabled 미지원 → :enabled 사용 */
QSplitter::handle {{
    background: transparent;
    border: none;
}}

/* ====== 라벨 ====== */
QLabel#categoryLabel {{
    color: {C["text_secondary"]};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    background: transparent;
    padding: 0;
}}
QLabel#categoryCount {{
    color: {C["accent"]};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.05em;
    background: transparent;
    padding: 0 4px;
}}
QLabel#sectionLabel {{
    color: {C["text_secondary"]};
    font-weight: 600;
}}
QLabel#controlLabel {{
    color: {C["text_caps"]};
    background: transparent;
    font-size: 9px;
    font-weight: 800;
    letter-spacing: 0.1em;
    padding: 0 4px;
}}
QLabel#timeLabel {{
    color: {C["text"]};
    background: transparent;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.12em;
}}
QLabel#fileLabel {{
    color: {C["text"]};
    background: transparent;
    font-size: 12px;
}}
QLabel#metaLabel {{
    color: {C["text_muted"]};
    background: transparent;
    font-family: {FONT_DATA};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 0 8px 0 6px;
}}
QLabel#nowPlayingLabel {{
    color: {C["text_caps"]};
    background: transparent;
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.14em;
}}

/* kbd — 단축키 표시 (Tooltip 내부 사용은 어렵지만 별도 라벨로) */
QLabel#kbd {{
    background: {C["bg_control"]};
    color: {C["text_secondary"]};
    border: 1px solid {C["border"]};
    padding: 1px 5px;
    border-radius: 3px;
    font-family: {FONT_DATA};
    font-size: 10px;
    font-weight: 600;
}}

/* 라이브러리 상태 pill */
QLabel#libPillDot {{
    background: transparent;
    color: {C["accent_teal"]};
    font-family: {FONT_DATA};
    font-size: 13px;
    padding: 0 2px;
}}
QLabel#libPillNum {{
    background: transparent;
    color: {C["text"]};
    font-size: 11px;
    padding: 0 2px;
}}
QLabel#libPillText {{
    background: transparent;
    color: {C["text_secondary"]};
    font-size: 11px;
    padding: 0 2px;
}}

/* 결과 카운트 (statusbar) */
QLabel#countLabel {{
    color: {C["accent"]};
    background: transparent;
    padding: 2px 10px;
}}

/* WAV/FLAC/MP3 등 포맷 배지 */
QLabel#fmtBadge {{
    font-family: {FONT_DATA};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.04em;
    padding: 1px 5px;
    border-radius: 3px;
    background: {C["bg_control"]};
    border: 1px solid {C["border"]};
    color: {C["text_secondary"]};
}}
QLabel#fmtBadgeWav  {{ color: {C["accent"]};       border-color: {C["accent_dim"]}; }}
QLabel#fmtBadgeFlac {{ color: {C["accent_teal"]};  border-color: rgba(123,180,245,80); }}
QLabel#fmtBadgeMp3  {{ color: {C["accent_warm"]};  border-color: rgba(255,184,77,80); }}

/* ====== 슬라이더 ====== */
/* 클린 슬라이더 (레퍼런스): 얇은 트랙 + 솔리드 강조색 정원 핸들.
   groove 4px 무테두리, 빈 트랙(add-page)=뮤트, 채운 트랙(sub-page)=강조색. */
QSlider::groove:horizontal {{
    background: {C["border_strong"]};
    height: 2px;
    border: none;
    border-radius: 1px;
}}
QSlider::add-page:horizontal {{
    background: {C["border_strong"]};
    border-radius: 1px;
}}
/* handle: 가로 슬라이더는 height 가 무시되고 세로 크기가 (groove + 2*|margin|) 로
   정해짐 → 정원 유지하려면 width 를 그 값과 같게 (8 = 2 + 2*3, 12 = 2 + 2*5).
   평상시 8px 정원, 드래그 중엔 12px 로 커짐. 크기 변화를 :pressed 에만 두는 이유:
   드래그 동안엔 커서가 핸들을 벗어나도 슬라이더가 계속 pressed 라 크기가 안정적.
   :hover 에 크기를 걸면 빠른 드래그 때 hover 가 들락날락하며 깜빡임(드래그
   애니메이션 깨짐) → hover 는 색만 변경. */
QSlider::handle:horizontal {{
    background: {C["accent"]};
    border: none;
    width: 8px;
    margin: -3px 0;
    border-radius: 4px;
}}
QSlider::handle:horizontal:hover {{
    background: {C["accent_hover"]};
}}
QSlider::handle:horizontal:pressed {{
    background: {C["accent_hover"]};
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}
QSlider::sub-page:horizontal {{
    background: {C["accent"]};
    border-radius: 2px;
}}
QSlider#volumeFader::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {C["accent_teal"]},
                                stop:0.62 {C["accent"]},
                                stop:1 {C["accent_warm"]});
}}

/* ====== 메뉴 ====== */
QMenu {{
    background: {C["bg_elev"]};
    color: {C["text"]};
    border: 1px solid {C["border_strong"]};
    padding: 4px 0;
}}
QMenu::item {{
    padding: 7px 26px;
    font-size: 12px;
}}
QMenu::item:selected {{
    background: {C["accent_dim"]};
    color: {C["text"]};
}}
QMenu::separator {{
    height: 1px;
    background: {C["border"]};
    margin: 4px 8px;
}}
QMenuBar {{
    background: {C["bg_header"]};
    color: {C["text"]};
    border-bottom: 1px solid {C["border"]};
}}
QMenuBar::item {{ background: transparent; padding: 6px 12px; }}
QMenuBar::item:selected {{ background: {C["hover"]}; }}
"""
