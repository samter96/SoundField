# 원본 정책 대조 체크리스트

> 목적: "옮겼다고 믿을 수 없다"는 문제를 없애기 위한 문서.
> 원본 코드에서 **직접 읽어 확인한 정책**만 적는다. 각 항목은 원본 위치(줄번호)를 남기고,
> 상태는 반영 / 미반영(백엔드) / 미반영(남음) / 원본에 없음 중 하나다.
> 추측으로 채우지 않는다. 확인하지 않은 항목은 아예 쓰지 않는다.

작성: 2026-09-01

> 성능·데이터 정합성 점검은 별도 문서: **[PERF_AND_INTEGRITY.md](PERF_AND_INTEGRITY.md)**
> (2026-09-02 — 인덱싱 속도 원본 대비 실측, 검색 315ms→2.8ms 상주화,
>  정합성 결함 5건 수정, 시작 시 검은 화면 원인 2건)

---


## 0. 최상위 원칙 — 디자인은 원본을 따르지 않는다 (2026-09-02 추가)

이 PoC 의 목적은 **디자인 개선**이다. 원본에서 가져오는 것은

- 구조(레이아웃 배치), 기능, 동작, 정책, 임계값, 문구

**뿐이다.** 비주얼 값은 가져오지 않는다:

- border-radius / padding / 폭·높이 / 폰트 크기·굵기·자간
- 색·알파값, 호버·전환 효과, 그림자, 점·배지 같은 장식의 렌더링 방식

원본 `theme.py` 의 QSS 나 `paintEvent` 의 수치를 CSS 로 옮기는 것은 **금지**다.
"원본과 정확히 맞췄다"는 이유로 다듬어둔 값을 덮으면 개선분이 사라진다.

### 실제로 발생한 사고 (되돌림 완료)

2026-09-01 통독 작업에서 `theme.py` QSS 를 `app.css` **맨 끝에 통째로 덧붙여**
버튼(radius 2)·알약(→10px)·입력창·슬라이더·`?`버튼·검색 이동 버튼·우클릭 메뉴·탭·
스크롤바·결과표 행 강조·상태 점·탭 닫기 X·세그먼트 호버·줌 스크롤바·"현재 재생 중"
캡션이 전부 원본 모양으로 되돌아갔다. 2026-09-02 전부 원복했다.
부작용도 함께 따라왔다 — `.tabbar{max-height:86px}` + `.tab{max-width:106px}` 로
3줄 넘는 탭이 잘려 사라졌다 (원본 FlowLayout `max_lines 3` 정책을 옮긴 것).

**원본 코드를 읽는 목적은 "무엇을 하는가"를 알기 위한 것이고,
"어떻게 보이는가"는 이 PoC 가 새로 정한다.**

## 1. 검색 필터 (multi_search.py)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 행 높이 | 고정 **28px** (147) | 반영 |
| 행 내부 가로 간격 | **4px** (150) | 반영 |
| 루트 필드 콤보 폭 | **120px** (_FIELD_WIDTH) | 반영 |
| 자식 연산자 콤보 폭 | **64px** (_OPERATOR_WIDTH) | 반영 |
| 자식 행 좌측 들여쓰기 | **56px** (=120-64, _CHILD_INDENT) | 반영 |
| 자식 행에 필드 콤보 없음 (루트 필드 상속) | 173 / matchers() 가 전파 347 | 반영 |
| 필드 목록 | 전체 · 파일명 · 경로 · 제목 · 아티스트 · 앨범 · 장르 · 코멘트 · 설명 · 키워드 · 카테고리 · **서브카테고리** · **출처** | 반영 |
| 입력 placeholder | 검색어 입력... | 반영 |
| 행 제거 버튼 | AnimButton 28×28, accent text_secondary, glyph x, 툴팁 "이 조건 제거" (195) | 반영 |
| 최대 행 수 | **6** (MAX_ROWS), 도달 시 추가 버튼 비활성 | 반영 |
| 루트 행 삭제 불가 | 값만 비운다 (_remove_row) | 반영 |
| 마지막 1개 행도 삭제 불가 | 값만 비운다 | 반영 |
| + AND/OR/NOT | AnimButton(glyph plus) **82×28** (268) | 반영 |
| 검색어 비우기 | AnimButton accent #c44848, glyph x, 높이 **28** (283) | 반영 |
| clear_all | 모든 행 제거 후 빈 루트 1개로 재구성 (307) | 반영 |
| 도움말 ? | **hover 150ms 툴팁** (창 아님), 24×24 (_HelpButton) | 반영 |
| 도움말 본문 | _SEARCH_HELP_TEXT 전문 | 반영 |
| 정확검색 토글 | _PreciseToggle 28×28 과녁 | 반영(시각) / 미반영(검색 분기) |
| 필터 행 순서 바꾸기 | **원본에 기능 없음** (드래그/이동 코드 없음) | 원본에 없음 |
| 콤보 휠 값 변경 차단 | block_value_wheel (168) | 반영 |

## 2. 결과표 (results_table.py)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 컬럼 14개 정의 | COLUMNS (600) | 반영 |
| 기본 표시 7개 | file_name, duration, sample_rate, channels, bit_depth, codec, file_path | 반영 |
| **셀 정렬** | file_name / file_path / comments 만 좌측, **나머지 전부 가운데** (948) | 반영 |
| **헤더 정렬** | setDefaultAlignment 호출 없음 → Qt 기본 **가운데** | 반영 |
| 행 높이 | 30px / 작게 모드 **22px** (set_config) | 반영 |
| 폰트 | 셀 9pt(작게 8), 파일명 10pt(작게 9), 배지 8pt(작게 7) | 반영 |
| 포맷 배지 색 | **모듈 상수** (테마 무관) _FMT_COLOR_MAP (71) | 반영 |
| 잠금 컬럼 | file_name 은 숨길 수 없음 | 반영 |
| 헤더 X 버튼 | 12×12, 우상단(right-14, top+3), 해당 컬럼 **호버 시에만** 표시 | 반영 |
| 정렬 화살표 | X 박스와 같은 가로 중심, 세로 중앙, 7×4 삼각형 rgb(140,152,168) | 반영 |
| 헤더 폭 36px 미만이면 X/화살표 미표시 | 441 | 미반영(남음) |
| 컬럼 드래그 순서 변경 | setSectionsMovable(True) | 반영 |
| 시작 시 자동 정렬 안 함 | 헤더 클릭 후에만 정렬 | 반영 |
| 숫자 컬럼 정렬 키 | 문자열이 아니라 숫자, 빈 값은 항상 뒤 (_NumericSortItem) | 반영 |
| **원클릭 활성화** | double_click_to_play=False 면 클릭 즉시 fileActivated (703) | 반영 |
| **선택 변경 활성화** | itemSelectionChanged → **35ms** 단발 → _fire_activate (658) | 반영 |
| 중복 활성화 방지 | 마지막 emit 경로와 같으면 skip (1095) | 반영 |
| **실제 재생 게이트** | _on_file_activated 는 auto_preview 꺼져 있으면 재생 안 함 (7295) | 반영 |
| 더블클릭 | 모드/auto_preview 무관 **항상 재생** (709) | 반영 |
| 목록 툴팁 지연 | **650ms**, 드래그 중에는 표시 안 함 | 반영 |
| 재생 점 상태 | loading 빨강 박동 / playing 초록 박동(0.9초 삼각파) / ended 파란 정적 | 반영 |
| 행 우클릭 메뉴 6항목 | 840 | 반영(문구) / 미반영(동작) |
| 헤더 우클릭 컬럼 메뉴 | 정의 순서 14개, 잠금 컬럼 비활성 | 반영 |
| 스크롤바 위 휠 무시 | 682 | 반영 |
| DAW 드래그 아웃 | 5조건 + 긴 경로 임시 복사 + 라벨 pixmap | 미반영(남음) |

## 3. 파일 브라우저 탭바 (flow_tab_bar.py, library_tabs.py)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 레이아웃 | FlowLayout, 가로/세로 간격 **4px**, **최대 3줄** | 반영 |
| 코너 버튼 위치 | + / 검색 을 **우상단에 겹쳐 배치**, 첫 줄만 (폭+4) 비움 (289) | 반영 |
| 코너 버튼 사이 간격 | 4px (588) | 반영 |
| 탭 최대 폭 | "★ 즐겨찾기" 폰트 폭 + 44, 초과 시 elide + tooltip (303) | 반영 |
| 고정 탭 | 전체, ★ 즐겨찾기 — 이름 변경/삭제 불가 | 반영 |
| 사용자 탭 | 생성 순서로 뒤에 붙고, 호버 시 우상단 X(14px, 여백 3px) | 반영 |
| + 툴팁 | **"새 탭"** | 반영 |
| 새 탭 입력 | QInputDialog("새 탭", "탭 이름:") — 빈 이름 무시, 중복 시 "같은 이름의 탭이 이미 있습니다." (982) | 반영(모달로) |
| 검색 토글 | 켜면 아래 검색 입력행 + nav 행 표시, 입력 focus+selectAll | 반영 |
| 검색 placeholder | 폴더 이름으로 라이브러리 검색 | 반영 |
| 검색 매칭 | 폴더명 토큰의 **단어 시작 접두** 만 인정 | 반영 |
| 검색 드랍다운 정렬 | **파일 수 내림차순 → 경로 깊이 얕은 순 → 경로 알파벳** (folder_tree.py:1201) | 반영 |
| 드랍다운 최대 | 500 매치 | 반영 |
| 트리 루트 순서 | **등록 순서**(root_order) — 알파벳 아님 (983) | 미반영(더미 트리) |
| 트리 자식 순서 | **경로 소문자 알파벳순** (_insert_child_sorted) | 미반영(더미 트리) |
| 즐겨찾기 순서 | sorted(favorites) (929) | 미반영(더미) |
| 드랍다운 계층 연결선 / 블랙리스트 빗금 / 조상 비활성 | folder_tree | 미반영(남음) |

## 4. 라이브러리 현황 (main_window.py:LibraryStatusInline)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 배치 | 검색 영역 우측, stretch **3 : 1** (4552) | 반영 |
| 패널 여백 / 간격 | margins **(10,0,4,0)**, spacing **2** (1400) | 반영 |
| 헤더 간격 | **4px**, ".." 버튼 **26×22**, 범례 앞 **6px** (1403) | 반영 |
| 상태 색 | **테마 무관 상수** — 정상 #58d97c / 대기 #ffb84d / 실패·없음 #ff5a5a | 반영 |
| 범례 칩 | 점 14px + 간격 3px + 10px 라벨(text_secondary) (1375) | 반영 |
| 스크롤 최대 높이 | **140px**, 가로 스크롤 없음 (1437) | 반영 |
| 행 간격 / 들여쓰기 | spacing **6**, 그룹 안 들여쓰기 **12px** (1599) | 반영 |
| 행 구성 | [점 16px][이름][개수][마지막 검사] + **끝에 stretch(전부 좌측 정렬)** (1602) | 반영 |
| 경로 없음 표시 | 점을 **hollow** 로 (1602) | 반영 |
| 검사 시각 형식 | %m-%d %H:%M, 없으면 "-" (1594) | 반영(형식) / 미반영(실데이터) |
| 드라이브 그룹 접기 | "▼/▶ D:\ (N개)", 접힘 상태 영구 기억 (_collapsed_drives) | 반영(접기) / 미반영(영구 기억) |
| 상태 우선순위 | 없음 → 실패 → 대기 → 정상 (_status_color) | 미반영(실데이터) |
| ? 도움말 | hover 툴팁, 본문 4줄 (1420) | 반영(문구) |

## 5. 플레이어 / 파형 (player_widget.py)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 버튼 크기 | 재생/정지/처음/반복 **30×26**, 세그먼트 **22×22** | 반영 |
| 아이콘 도형 크기 | 재생 삼각형 11×12, 정지 10, 처음으로 약 12, 반복 원호 지름 9.6 | 반영 |
| 세그먼트 아이콘 | 원본 10×10 → **사용자 지시로 버튼 안쪽의 80%(16px)** | 지시 반영 |
| 반복 ON 색 | #8B3FE6 보라, 전 테마 공통, fill 알파 70 | 반영 |
| 세그먼트 ON 색 | #3ec870 초록, 전 테마 공통, fill 알파 55 | 반영 |
| 호버 애니메이션 | alpha step 24 @60fps 약 180ms | 반영 |
| 컨트롤 재배치 | wide 1280 이상 / two_row / compact 920 미만 | 반영 |
| 2행 모드에서 SPD·VOL·세그먼트 | **오른쪽 끝 정렬** | 반영 |
| 시간 라벨 | 고정 폭 120px | 반영 |
| 속도 | -100~100, 2^(p/100), 1.0x 중앙, 1.0x 아니면 #f28c28 | 반영 |
| 볼륨 | 0~1000, 750=0dB, 1000=+6.02dB, 1=-50dB, 0=-무한, 휠 0.25dB | 반영 |
| 슬라이더 리셋 | 우클릭 / Ctrl+클릭 | 반영 |
| 값 라벨 | 더블클릭 인플레이스 입력, Enter 확정 / Esc 취소 | 반영 |
| 눈금자 | 높이 16, 눈금선 **하단 4px만**, 간격 **60px 이상** 첫 후보, 라벨 m:ss/Ns/N.Ns/Nms 자동 | 반영 |
| 세그먼트 헤더 | ruler 아래 **20px**, 토글 ON 일 때만, 라운딩 없는 사각형 | 반영 |
| 세그먼트 색 | 활성 보라 / 호버 초록 / 평상시 회청 (전 테마 공통) | 반영 |
| 세그먼트 번호 | 8pt Bold, 좌 패딩 5px, 폭 14px 미만이면 생략 | 반영 |
| 선택 영역 | 청록 / **반복 ON 이면 보라** | 반영 |
| 드래그 임계 | **6px** (화면 픽셀 기준, zoom 보정) | 반영 |
| 세그먼트 클릭 seek | 시작보다 **0.1초 앞** | 반영 |
| 줌 | Ctrl+휠 가로(커서 앵커) / Ctrl+Alt+휠 세로(최대 80) / Shift+휠 팬(10%) | 반영 |
| 줌 범위 | 1 ~ 1000, 단계 1.5배, 동적 최대 600×dur_ms/폭 | 반영 |
| 파일 전환 시 줌 리셋 | 항상 전체 보기 | 반영 |
| **raw 모드 진입** | 최고 레벨 슬라이스 / zoom < 폭 (_is_raw_mode) | 반영 |
| **샘플 폴리라인** | 보이는 샘플이 폭보다 적으면 샘플 점을 이은 선, pen **1.25** | 반영 |
| raw min/max 폴리곤 | 그 사이 구간 | 반영 |
| 재생 중에는 raw 모드 금지 | _playback_active 가드 | 미반영(남음) |
| 파형 색/그라데이션 | 테마별 3종, grey 는 RGB 대비 | 반영 |
| 빈 상태 문구 4종 | 로딩 / 미지원 / 실패 / 없음 | 반영 2종 / 미반영 2종(실상태 필요) |
| 실제 오디오 재생 | playback.py, audio_engine.py | 미반영(백엔드) |
| 바이노럴 | binaural.py 사이드카 | 미반영(백엔드) |

## 6. 히스토리 (history_panel.py)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 제목 | **"히스토리"** (닫기 버튼 없음) | 반영 |
| 폭 | 시작 0, 펼침 **280**, 최소 180, 최대 800 | 반영 |
| 애니메이션 | **168ms** OutCubic | 반영 |
| 좌측 grip | **8px**, 드래그로 폭 조절, 호버 시 중앙 점 3개 | 반영 |
| 배치 | 결과표 **옆에** 밀려 들어옴 (오버레이 아님) | 반영 |
| 항목 표시 | 파일명만, tooltip 전체 경로, **최신이 위** | 반영 |
| 재생 | itemActivated = 더블클릭/Enter | 반영 |
| 푸터 | "N개" + "지우기"(pill) | 반영 |
| 드래그 아웃 | 파일 URL MIME | 미반영(남음) |
| 실제 기록 | %LOCALAPPDATA%\SoundField\history.json | 미반영(백엔드) |

## 7. 환경설정 (main_window.py:SettingsDialog)

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 창 | 제목 "환경설정", 500×500, Ctrl+P 양방향 | 반영 |
| 탭 6개 | 일반 / 테마 / 재생 / 인덱싱 / **중복 검수** / 단축키 | 반영 |
| 하단 버튼 | 기본값으로 초기화 / 저장 / 취소 | 반영 |
| 검색 최대 노출 | 100~5000, 기본 500, hint 전문 | 반영 |
| 결과 목록 동작 | 원클릭으로 재생 / 더블클릭으로 재생 (기본 원클릭) | 반영 |
| 결과 텍스트 크기 | 보통 (기본) / 작게 (한 화면에 더 많이) — **2택** | 반영 |
| 테마 | 뉴트럴 (기본) / 라이트 / 네온, **저장 시에만 적용** | 반영 |
| 정지 후 재생 방식 | 처음부터 / 이어서 (기본 처음부터) | 반영(UI) |
| 자동 미리보기 | 기본 꺼짐 | 반영 |
| 드래그 시 재생 정지 | 기본 켜짐 + 툴팁 전문 | 반영(UI) |
| 메타 분석 정책 | 즉시 / Idle + 설명 전문 | 반영(UI) |
| 단축키 6종 | Space / Home / R / S / H / Ctrl+P | 반영 |
| 휠 단축키 3종 | 회색 비활성, 툴팁 "이 단축키는 변경할 수 없습니다" | 반영 |
| 기본값으로 초기화 | **테마는 건드리지 않는다** | 반영 |
| 중복 검수 스캔 | 전체 라이브러리 단일 스캔 | 미반영(백엔드) |

## 8. 설정 파일 호환 (~/.soundfield_config.json)

| 정책 | 상태 |
|---|---|
| 파일 읽기 (theme, compact_results, auto_preview, double_click_to_play, search_limit, blacklist_expanded_h, history_width) | **반영** — tauri-plugin-fs, 읽기 전용 |
| 쓰기 | **의도적으로 안 함** — 실사용 설정을 PoC 가 덮어써 손상시키는 것 방지 |
| columns / tabs / geometry / filters / last_selection 복원 | 미반영(남음) |

---

## 원본에 없어서 제거한 것 (PoC 가 만들어냈던 UI)

| 제거 | 원본의 실제 모습 |
|---|---|
| 검색 도움말 모달 | ? 버튼 hover 150ms 툴팁 |
| 라이브러리 추가 모달 | OS 폴더 선택 창 |
| 중복 검수 독립 모달 | 환경설정의 탭 |
| 결과 텍스트 크기 3단(작게/보통/크게) | 2택 |
| 히스토리 패널 닫기 X 버튼 | 없음 (토글 버튼으로 닫음) |
| 상태바의 "분석 실패 / 제외 폴더" 버튼 | 없음 |
| 파형 위 "파일명 / 영역 내보내기" 행 | 없음 |

## 확인했지만 원본과 같아서 그대로 둔 것

- 검색 영역 아래 빈 여백 — 라이브러리 현황이 140px 고정이라 원본도 동일하게 남는다.
- GlowButton(젤리 글로우) — 원본 전체에 호출부가 없는 죽은 코드.
- 필터 행 순서 바꾸기 — 원본에 기능이 없다.

## 다음에 읽어야 할 원본 (아직 통독하지 않은 것)

- main_window.py 의 인덱싱 워커/진행률/취소 경로 (4700~6200)
- folder_tree.py 전체 (2044줄) — 드랍다운 렌더, 다중 선택, 드래그
- library_tabs.py 전체 (1494줄) — 탭별 포함/제외 모델, 드롭 처리
- blacklist_panel.py (316줄)
- results_table.py 의 컬럼 폭 자동 채움 로직 (_fit_columns)
- main_window.py 의 LibraryStatusDialog / FailedPendingDialog / ChangeReviewDialog 본문

---

## 2026-09-01 Codex 인계 후 — 실제 데이터/설정 연결 복구

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 설정 저장 구조 | `SettingsDialog.get_config()` dict 를 `MainWindow._config_data`에 merge 후 results/player/shortcuts 재적용 (main_window.py:4000, 4311) | 반영 |
| 환경설정 초기값 | 현재 `_config_data` 값을 창에 표시해야 함. PoC는 theme/compact 외 값을 자체 DEFAULTS로 열고 있었음 | 반영 |
| 설정 저장 반영 범위 | search_limit, double_click_to_play, compact_results, playback_restart_from_zero, auto_preview, stop_on_drag, index_on_idle, shortcuts | 반영(런타임) / 쓰기는 보류 |
| 검색 최대 노출 개수 | `_do_search()` signature와 worker request의 `limit` (main_window.py:7178) | 반영 |
| 상태바 인덱싱 집중 | `index_focus_mode` 체크 상태를 `_config_data`에 저장하고 복원 (main_window.py:4248, 4920) | 반영(런타임) / 쓰기는 보류 |
| Space 재생 정책 | 같은 파일이 정지/일시정지 상태면 `playback_restart_from_zero=True`에서 처음부터, False면 현 위치 재생 (player_widget.py:4361) | 반영 |
| 하단 재생 버튼 | `_toggle()`은 재생/일시정지만 하며 위치 보존 (player_widget.py:4397) | 반영 유지 |
| 더블클릭/수동 재생 | `manualPlayRequested → load_and_play`, auto_preview 무시 (main_window.py:4323) | 반영 |
| 실제 DB 경로 | `%LOCALAPPDATA%\SoundField\index.db` | 반영(읽기 전용) |
| 결과 초기 행 | `audio_files`에서 `hidden=0`, `removed=0`만 표시 | 반영 |
| 검색 알고리즘 | 원본 `Database.query()` 사용: FTS5 trigram, 정확검색 term index, UCS 동의어, 짧은 토큰 LIKE, 블랙리스트 자동 제외 | 반영(원본 Python 브리지) |
| 검색 요청 모양 | matchers, min_duration, max_duration, sample_rate, channels, min_channels, path_prefixes, precise, limit | 반영 |
| 검색 필드 key | 전체→any, 파일명→file_name, 경로→file_path, 제목/아티스트/앨범/장르/코멘트/설명/키워드/카테고리/서브카테고리/출처 | 반영 |
| 자식 검색 행 field | 원본 `matchers()`처럼 루트 field 상속 | 반영 |
| 라이브러리 root 순서 | `library_roots ORDER BY sort_order, path` | 반영 |
| 라이브러리 현황 count | root prefix 아래 visible 파일 count (`hidden/removed` 제외) | 반영 |
| 라이브러리 현황 pending/failed | `meta_extracted=0/2` 합계 | 반영 |
| 라이브러리 현황 상태 우선순위 | 경로 없음 → 실패 → 대기 → 정상 | 반영 |
| 마지막 검사 시각 | `%m-%d %H:%M`, 없으면 `-` | 반영 |
| 블랙리스트 목록 | `blacklist_paths ORDER BY path`, 0개면 0개로 표시 | 반영 |
| 상태바 전체 카운트 | `count_files()`와 같이 visible 파일 count (`hidden/removed` 제외) | 반영 |

**의도적 보류**

- `~/.soundfield_config.json` 쓰기: PoC가 기존 앱 실사용 설정을 덮어쓰지 않도록 아직 읽기 전용.
- 검색 브리지는 정확도를 위해 원본 Python `Database.query()`를 호출한다. 릴리즈 독립 실행형으로 만들 때는 Python/원본 모듈 동봉 또는 Rust 재구현 중 하나를 별도 결정해야 한다.
- 실제 오디오 재생, 파형 피크 추출, DAW 드래그 아웃, 인덱싱 시작/취소/재시도는 아직 미연결.

## 9. 파일 브라우저 트리 (folder_tree.py) — 2026-09-01 통독

| 정책 | 원본 값 | 상태 |
|---|---|---|
| 들여쓰기 | **setIndentation(18)** — 10 이면 root 화살표가 잘린다 (667) | 반영 |
| 폰트 | 시스템 기본 **9pt** (family 명시 안 함 — 한글 글리프 fallback 실패 방지) | 반영 |
| 행 높이 | setUniformRowHeights(True) | 반영 |
| 선택 모드 | **ExtendedSelection** (Ctrl/Shift 다중 선택) | 반영 |
| 선택 배경 | Qt Highlight 를 투명화하고 **drawRow 가 뷰포트 폭 전체**를 row_selected 로 칠함 (2185) | 반영 |
| 좌측 accent 바 | **선택 행 + 조상(ancestor) 행** 모두 3px **#4E90E8** (2204) | 반영 |
| 개수 컬럼 | column 1 은 hidden, **_CountOverlay** 가 우측 **50px** 고정 폭으로 그린다 — 가로 스크롤과 무관하게 고정 (126) | 반영(고정폭·우측정렬·구분선) / 미반영(가로 스크롤 독립) |
| 개수 색 | **#4E90E8** (테마 무관 상수 _COUNT_COLOR) | 반영 |
| 개수 정렬 | 우측 정렬, 우측 여백 6px, 좌측 1px 구분선 | 반영 |
| 미완료 표시 색 | #ffa64d (_INCOMPLETE_COLOR) | 미반영(실데이터) |
| 화살표 | 삼각형 size 4.5, cx = rect.right()-9, **가지 세로선 안 그림** (2215) | 반영 |
| 화살표 색 | **#727d8e** / 호버 **#d6deea** (테마 무관 상수) | 반영 |
| 포커스 사각형 | 제거 (drawRow 에서 State_HasFocus 해제) | 반영 |
| 블랙리스트 폴더 | **트리에서 setHidden(True)** — 빗금/색 변형이 아니라 숨김. 노드는 남아 제거 시 원위치 복원 (819) | 반영 |
| 검색 매칭 강조 | 현재 검색 위치 행의 **토큰 접두 구간만** #cf7676 + bold | 반영 |
| 토큰 접두 규칙 | 구분자로 쪼갠 토큰의 **시작**에서만 매칭. 단어 중간 제외 | 반영 |
| 펼침/접힘 상태 | 사용자가 직접 토글한 것만 저장·복원 (_user_expanded_state) | 미반영(남음) |
| **조상 고정 헤더** | **_StickyAncestorHeader** — 선택 폴더가 위로 스크롤돼 사라지면 조상들을 트리 맨 위에 고정 표시(VS Code sticky scroll). "전체" 는 항상 맨 위 고정. 클릭하면 그 조상으로 스크롤(선택/검색 범위는 안 바꿈) | **미반영(남음)** |
| 목록 툴팁 | 650ms 지연 | 미반영(남음) |
| 탭 간 드래그 | MIME application/x-soundfield-paths | 미반영(남음) |

### 2026-09-01 사용자 지적 3건 — 원인과 수정

**1. 파형 한쪽이 눌림 (위쪽이 빔)** — 실제 버그 2개였다.

- `getPeaks` 와 렌더 축약 루프가 min/max 를 **0 으로 초기화**하고 있었다. 구간이 전부
  양수(또는 전부 음수)면 반대쪽이 0 으로 고정돼 엔벌로프가 항상 0 을 포함하고 한쪽만
  자라 보인다. 원본 numpy reduceat 처럼 **첫 표본으로 초기화**하도록 고쳤다.
- 더미 콘텐츠가 **180Hz 단일 톤 위주**라 전체 보기에서 픽셀 하나에 한 주기도 안
  들어갔다. 실제 오디오는 픽셀당 수백~수천 주기가 들어가 min/max 가 대칭이 된다.
  톤을 kHz 대로 올리고 노이즈를 우세하게 바꿔 같은 성질을 만들었다. 포락선도 빠른
  어택 + 기복 2개로 실제 효과음 모양에 맞췄다.
- 실측: 이전에는 레인 중앙 기준 위 3~18px / 아래 17~36px 로 치우쳐 있었다. 이제 대칭.

**2. 컬럼 자리바꿈 안 됨** — HTML5 드래그를 쓰고 있었는데, Tauri 창 옵션
`dragDropEnabled` 가 기본 true 여서 OS 드래그를 Tauri 가 가로채 WebView 안의 HTML5
드래그 앤 드롭이 동작하지 않았다. 두 가지를 함께 처리했다.

- `dragDropEnabled: false` — OS 파일 드롭을 받지 않으므로 무해하다.
- **마우스 이벤트 기반 재배치로 재구현**. 원본 QHeaderView.setSectionsMovable 도 마우스
  기반이다. 6px 임계, 드롭 대상 강조, 재배치 직후 정렬 클릭 억제, 폭 조절 그립·닫기
  버튼에서 시작한 드래그는 각자 동작에 양보.

**3. 범례 라이트 디자인 / 정렬 / 툴팁 아이콘**

- 원본 _make_legend_chip 은 **세 칩이 모두 같은 도형**(채운 점)이고 색만 다르다.
  PoC 는 "실패" 만 hollow(테두리만)로 그리고 있었다 — hollow 는 원본에서 **행의
  경로 없음** 표시이지 범례가 아니다. 세 칩을 같은 채운 점으로 통일했다.
- 범례 묶음을 **우측 정렬**로 옮겼다 (사용자 지시).
- 도움말은 원본이 검색 도움말과 같은 **? 버튼**(_HelpButton)이다. 정보 아이콘이 아니라
  ? 로 바꿨다.

---

### 2026-09-01 (2차) 전체 통독 — 확인/수정 내역

#### 1. 컬럼 헤더 X ↔ 정렬 삼각형 겹침 (사용자 보고) — 수정 완료
원본 `ClosableHeader.paintSection` (results_table.py:438) 기준 좌표:
- X 박스 12×12, `right-2 / top+3`, 내부 선은 3px 안쪽 → **글리프 y 6~12**
- 정렬 삼각형 7×4, X 박스의 **가로 중심**에 맞추고 **세로는 헤더 중앙**
- 원본 헤더는 QSS `padding: 7px 10px` + 10px 폰트 → 약 30px → 삼각형 y 13~17 (1px 여유)

PoC 헤더는 29px 이라 삼각형이 12.5~16.5 로 올라와 X 글리프와 맞닿았다.
→ X 박스만 1px 올리고(`top: 2px`) 글리프를 원본과 같은 6px 로 맞췄다.
실측: X 글리프 y 5~11, 삼각형 y 12.5~16.5, 가로 중심 51 / 51.5 (일치).

#### 2. 현재 라이브러리 / 미완료 항목 창 — 원본 구조로 재작성
- `LibraryStatusDialog` (main_window.py:2305): 제목 **현재 라이브러리**, 920×360,
  6열 `상태 / 경로 / 파일 수 / 대기 / 실패 / 마지막 스캔`, 행 더블클릭 → 상세,
  하단 `선택 라이브러리 상세 보기… / 실패 전체 재시도 / 실패 항목 제거 / 닫기`.
  PoC 가 임의로 넣었던 진행률 패널·`목록 내보내기`는 원본에 없어 제거.
- `FailedPendingDialog` (main_window.py:1838): 제목 `미완료 항목 — {폴더명}`, 900×520,
  헤더에 전체 경로 + `대기 N`(#7adfc4) · `실패 N`(#ff7878),
  필터 콤보 `전체 (대기+실패) / 대기만 / 실패만`, `전체 선택` `선택 해제`,
  5열 `[체크] 상태 파일명 경로 사유` (폭 32/60/260/380),
  하단 `↻ 새로고침` + `선택 항목 재시도 (Phase2)` + `선택 항목 제거`.

#### 3. 탭 소속 모델 + 트리→탭 드래그 (원본 library_tabs.py) — 구현
`_tabs` 메타의 `items`(추가한 경로) / `excluded`(이 탭에서 삭제한 하위) 차집합 구조를
그대로 옮겼다. `_on_paths_dropped` (1048) 의 네 갈래 분기까지 동일:
- `added` → 토스트 `'{탭}' 에 추가`
- `dup` (같은 경로 이미 있음) → `이미 포함된 라이브러리입니다`
- `dup_child` (이미 추가된 상위 폴더 하위) → `이미 추가된 상위 폴더에\n포함되어 있습니다`
- `absorbed` (새 상위가 기존 하위를 흡수 → items 에서 제거)
- 추가한 경로와 그 하위의 `excluded` 항목은 해제 (`_strip_excluded`)
전체 탭은 드롭을 받지 않는다 (자동 구성). 탭에서는 `_resolve_roots` 처럼
`items` 가 root 로 보이고, `excluded` 하위는 그 탭에서만 감춰진다.
경로 문자열 대신 노드 id 로 상하위를 판정한다 (PoC 트리는 라벨만 있음) — 판정 결과는 동일.

#### 4. 탭 삭제 확인 대화상자
원본 `_remove_tab` (1031) 은 QMessageBox 확인을 띄운다:
`'{name}' 탭을 삭제할까요? / (라이브러리 자체는 그대로 유지됩니다)`.
PoC 는 X 클릭 즉시 삭제 + 토스트였다 → 확인 대화상자로 교체하고, 탭 생성/삭제 시
토스트를 띄우지 않도록 했다 (원본은 드롭/제거 피드백에만 토스트를 쓴다).

#### 5. 상시 헤더 (`_StickyAncestorHeader`, folder_tree.py:236) — 이식
- `전체` 는 있으면 **항상** 맨 위 고정 ("현재 범위=전체" anchor)
- 선택 항목의 조상은 **자기 슬롯**(= 이미 고정된 행 수 × 행높이)에 닿는 순간 고정
  (원본 주석: `top<0` 기준이면 헤더에 가려지는 데드존이 생긴다)
- 선택 항목 자신도 자기 슬롯에 닿으면 맨 아래 고정
- 클릭 = 그 폴더 선택(브레드크럼), 화살표 = 접기/펼치기, 우클릭 = 본문과 같은 메뉴
- 원본은 카운트 영역까지 덮어 고정 행의 카운트를 직접 그린다 → 같은 행 마크업 재사용

이식과 함께 트리 렌더를 **평평한 목록**으로 바꿨다 (본문과 헤더가 같은 목록을 쓴다).

#### 6. 필터 초기화 버튼 색 — 반대로 되어 있었다
`_FilterResetButton.paintEvent` (main_window.py:310) 은 `_GREEN(#3ec870)` 만 쓴다.
`_RED` 는 선언돼 있지만 **paint 에서 쓰이지 않는다** (docstring 의 "active=빨강" 은 낡음).
- 필터 걸림: 초록 외곽선 full + fill α55(호버 α81) + 초록 아이콘
- 필터 없음: 외곽선 border_strong α170 → 호버 accent_hover α240,
  아이콘 text_secondary → accent_hover, fill accent α24 (호버에서만)
PoC 는 반대(기본 초록 / 걸림 빨강)였다 → 코드 기준으로 교정. 호버 전이 140ms OutCubic.

#### 7. 히스토리 토글 — 열림 상태에 색 변화 없음
`AnimButton` 은 `checkable` 이지만 `paintEvent` 가 `isChecked()` 를 보지 않는다.
즉 열려 있어도 **버튼 색은 그대로**이고 텍스트만 `▶ 히스토리` 로 바뀐다.
PoC 의 accent 채움(`.history-toggle.active`)을 제거했다.
(원본은 초기 텍스트가 `히스토리 ◀`, 닫은 뒤엔 `◀ 히스토리` 로 화살표 위치가 달라지는
불일치가 있다 — PoC 는 초기 표기 `히스토리 ◀` 로 통일했다.)

#### 8. 스플리터 손잡이 (`_GripSplitterHandle`, main_window.py:76) — 이식
원본 handle_width 를 그대로 썼다: **main 16px / 결과↔플레이어 20px / 사이드 6px**
(PoC 는 5px 얇은 선이었다 — 레이아웃 폭이 달라진다).
구성: bg_control_hi 배경 + accent wash(α16→α52) → 양쪽 2px 엣지(border_strong α105→145)
→ 밴드(bg_elev α230, 두께 62%, inset 6, r3) → 가이드(border_strong α135→205, 32%, inset 12)
→ 알약 그립(border_strong→accent_hover, 48%, 길이 = 18~20% 를 [96,220]/[80,180] 클램프,
호버 시 +34/+30) → 호버 시 점 3개(on_accent, ±8). 애니메이션 34/255 per 8ms ≈ 60ms.
드래그 중에는 그립이 accent_pressed 와 반씩 섞인다.

#### 9. 단축키 — config 의 `shortcuts` 를 그대로 읽어 반영
원본 `_setup_shortcuts` (5007) 의 6개: `play_pause`(Space) / `seek_to_start`(**Home**) /
`toggle_loop`(R) / `toggle_segments`(S) / `toggle_history`(H) / `open_settings`(Ctrl+P).
사용자 실제 config 는 `seek_to_start: "1"` 이다 → 파일 값을 그대로 쓴다.
휠 단축키 3종(Ctrl+휠 가로 확대 / Ctrl+Alt+휠 세로 확대 / Shift+휠 가로 스크롤)은
원본에서도 변경 불가(dimmed)로 표시된다.

**정정(사용자 확인)**: 원본에서도 검색창에 Space 가 정상 입력된다. Qt 는 단일 키
단축키를 처리하기 전에 포커스 위젯에 ShortcutOverride 를 보내고 QLineEdit 이 이를
받아들이기 때문이다(입력 위젯이 글자 키를 먼저 가져간다). 즉 "입력 중에는 단일 키
단축키가 발동하지 않는다" 가 원본의 실제 동작이고, PoC 도 같다 — 차이가 아니다.
Ctrl+P 는 QLineEdit 이 가져가지 않으므로 입력 중에도 동작한다(PoC 도 동일).

#### 10. config 추가 반영
읽기 전용으로 `history_width` / `history_visible` / `blacklist_expanded_h` /
`volume_pos1k`(750=0dB) / `speed_rate` / `shortcuts` / `columns`(순서·표시·너비) 를 읽는다.
`columns` 는 PoC 로컬 레이아웃이 **없을 때만** 적용한다 (사용자가 PoC 에서 옮긴 배치를
원본 값이 덮어쓰지 않도록). 사용자 실제 순서는 `길이 → 파일명 → SR → PATH → CH →
BIT DEPTH → 코덱 → 크기 → 제목 → 아티스트 → (앨범/장르 숨김) → 코멘트 → (비트레이트 숨김)`.

#### 11. 상태바 구성 (원본 main_window.py:4705)
좌: 인덱싱 배지(숨김) + 상태 메시지(stretch, 잘려도 툴팁에 전문) /
우(permanent 순서): 진행바(숨김) → `인덱싱됨  N` → 메타 스피너·인디케이터(숨김) →
일시정지 버튼(숨김) → **`인덱싱 집중` 체크박스**(상시 표시, PoC 에 없었다 → 추가).
검색 결과 메시지는 `결과 N개` + 기준 접미사:
- 폴더 2곳 이상 → ` [폴더 N개: 첫경로 외 M곳]`
- 1곳 → ` [기준: 경로]`
- 없음 → 접미사 없음
`인덱싱됨  N` 은 두 칸 공백이다 (`_update_count_label`).

#### 12. 상단 헤더 (원본 `_build_ui` 4374~) 대조 결과 — 일치
central margin 6 / top row spacing 8, margin (8,0,4,0) /
브랜드 마크 16 · `SoundField` 12px·700·ls.08em · 소속 표기 3줄
8.5px·700·ls.12em(text_caps) · `V1.3.0` 8.5px·500(text_secondary) /
환경설정 버튼 20 → 글로벌 로더 → stretch → 액션 버튼 4개(높이 24).
미이식: `⚠ 검색 정리` 버튼 (FTS 정합성 깨질 때만 표시 — 백엔드 필요).

#### 13. 미이식/보류 (백엔드 없이는 의미 없음)
- `ChangeReviewDialog` (빠른 갱신 결과 검토), 인덱싱 진행/취소 경로 전체
- 콤보 팝업의 현재 항목 좌측 dot (#4E90E8, 4.5px, left+8) — 네이티브 `<select>` 로는 불가
- OS 탐색기 → 창 폴더 드롭. `dragDropEnabled: false` 가 필요해(컬럼 재배치·트리 드래그)
  OS 파일 드롭을 받을 수 없다. 라이브러리 추가는 `라이브러리 추가` 버튼(OS 폴더 선택)으로.

#### 14. 라이브러리 추가 3단 확인 (원본 `_add_library`, main_window.py:6155) — 구현
OS 폴더 선택 후 경로 관계에 따라 확인 창이 갈린다. 문구는 원본 그대로 옮겼다.
- 같은 경로가 이미 등록 → 창 제목 **이미 인덱싱된 폴더** / "다시 스캔할까요?"
- 상위 라이브러리 안에 포함 → 같은 창, `선택한 경로 / 상위 라이브러리` 를 함께 보여준다
- 이 경로 아래에 하위 라이브러리 존재 → **라이브러리 통합** (하위 12개까지 나열 + `... 외 N개`)
- 그 외 → **라이브러리 추가** / "지금 이 폴더를 스캔하여 라이브러리에 등록할까요?"
아니오 → 목록에 추가하지 않고 상태바에만 이유를 남긴다
(`이미 인덱싱된 폴더라 새 라이브러리로 추가하지 않았습니다` / `하위 라이브러리와 겹쳐 …`).

#### 15. 블랙리스트 — 제거 확인 + 상세 창
- `_on_remove` / `_remove_row` 모두 확인 창을 띄운다: **블랙리스트 제거** /
  `블랙리스트에서 제거할까요?` + 경로. PoC 는 즉시 삭제였다 → 확인 창으로 교체.
- 상세 창(`BlacklistStatusDialog`, 720×420): 본문 머리글 `등록된 블랙리스트 (N개)` 13px 700,
  힌트 `검색 결과에서만 가려집니다 (인덱스/DB 는 유지).`, 표 4열
  라이브러리(내용 맞춤)/경로(늘어남)/사유(늘어남)/`✕`(48px), 행 교대 배경, 하단 `닫기`(pill).
  제거 버튼은 트래시 아이콘이 아니라 **✕ 36×22**, 툴팁 `이 항목 제거`.
- 패널 헤더에 **추가 버튼은 없다** (`_on_add_clicked` 는 어디에도 연결되지 않은 죽은 코드).
- 데이터 버그 수정: `data.ts` 의 블랙리스트 경로가 역슬래시 1개라 JS 이스케이프로
  삼켜져 `Y:[Library]_TRASH` 처럼 보였다 → 이스케이프 교정.

#### 16. 정확한 검색 토글 (`_PreciseToggle`, multi_search.py:16)
paintEvent 가 전부 그리므로 **테두리가 없다**. 28×28, radius 5.
- OFF: 아이콘 text_secondary, 호버 시 흰색 α18 배경
- ON : 아이콘 **#e05252**(빨강), accent α40 배경
- 과녁 = 반지름 8.5 / 5.0 원(펜 1.6) + 채운 가운데 점 1.9
→ PoC 는 테두리 + accent 링 그림자 + 15px 아이콘이었다. viewBox 20 아이콘을 20px 로
그려 반지름을 원본 픽셀과 1:1 로 맞추고, 테두리/그림자를 제거했다.

#### 17. 하단 컨트롤 배치 (`_apply_control_layout`, player_widget.py:3316)
- **wide (폭 ≥ 1280)**: 한 줄 — 트랜스포트(재생/정지/처음으로/반복/시간) → 정보(stretch)
  → 바이노럴 → SPD → VOL → 세그먼트. (SPD/VOL/세그먼트가 맨 오른쪽)
- **920~1279 / <920**: 첫 줄 트랜스포트+정보, 둘째 줄 바이노럴·SPD·VOL·세그먼트.
  원본은 둘째 줄을 **좌측 정렬**한다. 사용자 지시("창 너비와 상관없이 맨 오른쪽")에 따라
  PoC 는 둘째 줄에서도 우측 정렬을 유지한다 — 명시적 개선사항이므로 그대로 둔다.
- 실측 일치: 세그먼트 22×22(아이콘 16 ≈ 73%), SPD 슬라이더 88, VOL 슬라이더 110,
  바이노럴 컨트롤 345×22.
- 볼륨/속도 매핑은 원본 함수를 그대로 옮겼다 (750=0dB, 1000=+6.02dB, 1=−50dB, 0=−∞,
  휠 0.25dB, log2 배속, 1.0x 아니면 값 라벨 #f28c28).
- **시간 표시는 `M:SS / M:SS`** (원본 `_fmt_ms` 는 초 단위만) — PoC 가 센티초까지
  보여주고 있었다 → 교정.

#### 18. 테마 색 — 원본 팔레트와 대조
`theme.py` 의 3개 팔레트(COLORS_DARK / LIGHT / GREY)를 tokens.css 와 비교했다.
사용자가 "색감은 tauri 고해상도로 업그레이드" 를 승인했으므로 밝기/대비 조정은 유지하되,
**의도가 담긴 결정은 원본으로 되돌렸다**:
- 뉴트럴(grey) 팔레트의 `warn` 은 원본이 **무채색 #b0b0b0** 이다 ("무채색 톤 유지").
  PoC 는 노란색이었다 → 무채색으로 환원. `danger` 는 원본과 같은 #c44848.
- 네온(dark) `warn #c89840` / `danger #c44848`, 라이트 `warn #a07820` / `danger #9c3535` 로 통일.
- 라이트 테마 accent 가 파랑이 아니라 **다크 브라운(#332e22)** 인 것도 원본 그대로 유지.

**정정(사용자 확인)**: 앞서 "테마와 무관한 파란색" 이라고 적은 것은 **WAV 배지가
아니다**. WAV 배지 파랑(#4E90E8)은 원본 `_FMT_COLOR_MAP` 그대로이고 PoC 도 동일하다.
내가 가리킨 것은 `FilenameCellDelegate.paint` (results_table.py:145~157) 가 **선택/재생
행의 파일명 칸 배경**에 rgba(78,168,255,46) / 파란 그라데이션을 덧칠하는 코드다.
사용자 확인상 실제 화면에서는 그렇게 보이지 않으므로(알파 18% + 무채색 배경),
PoC 는 행 배경을 테마 색으로 통일한 현재 상태를 유지한다.
재생/로딩/종료 점(초록 #58d97c / 빨강 #ff5a5a / 파랑 #4E90E8)과 포맷 배지 색은
원본처럼 테마 무관 상수다.

#### 19. 창 기본/최소 크기
원본 `self.resize(1500, 850)` + 자식 최소값(side 200 / right 600 / results 200 / player 180).
PoC 는 1480×940, minWidth 1080 이었다 → **1500×850, min 820×620** 으로 맞추고
사이드바 드래그 하한 200, 플레이어 높이 하한 180, `.center` min-width 600,
`.results` min-height 200, `.player` min-height 180 을 지정했다.
(min 1080 이면 원본의 두 줄/좁은 폭 배치를 재현할 수 없다.)

#### 20. 필터 행 라벨 줄바꿈
원본 `_control_label` 은 QLabel 이고 `setWordWrap` 을 쓰지 않는다 — 즉 줄바꿈하지 않는다.
좁은 폭에서 PoC 의 "SAMPLE RATE" 가 두 줄로 접혀 행 정렬이 어긋났다 → `white-space: nowrap`.

#### 21. 결과 셀 표시 문자열 (원본 `_item_for_value`, results_table.py:929)
- `duration` → `{:.2f}s` (예 `2.31s`)
- `file_size` → `_fmt_size`: B 는 정수, KB 이상은 소수 1자리, 단위는 공백 없이 붙임
- **그 외는 `str(val)` 그대로** — SR/비트레이트에 단위 접미사가 붙지 않는다.
  PoC 는 `192000hz` / `320kbps` 로 붙여 쓰고 있었다 → 원본대로 숫자만.
- 정렬은 표시 텍스트가 아니라 숫자 키로 한다 (`_NUMERIC_KEYS`: duration, file_size,
  sample_rate, channels, bit_depth, bitrate) — 문자열 비교면 `192000 < 44100` 이 된다.
- 셀 정렬: **파일명/경로/코멘트만 좌측**, 나머지는 중앙.

#### 22. 회귀 주의 — `.center` 클래스 충돌 (이번에 실제로 발생)
`.center` 는 레이아웃 컨테이너 이름이면서 **셀 가운데 정렬 유틸 클래스**로도 쓰인다
(`.th.center`, `.td.center` — 문서에 100개 이상). `.center { min-width: 600px }` 를 주자
모든 가운데 정렬 셀의 최소 폭이 600px 이 되어 길이 컬럼이 600px 로 벌어졌다.
→ 레이아웃 규칙은 반드시 `.body > .center` 처럼 한정한다. 실측으로 잡았다
(컬럼 폭 60/360/320/90/44/88/70 = 원본 기본값과 일치 확인).

#### 23. 시작 크기 검증 방법 (브라우저 창에서)
숨겨진 브라우저 패널은 `innerWidth/innerHeight` 가 0 이라 flex 계산이 전부 0 이 된다.
레이아웃 실측 전에는 반드시 뷰포트를 명시적으로 에뮬레이트할 것 (예 1500×850).
그 상태에서 실측한 값: 사이드바 250 / 손잡이 16 / 결과 영역 1234, 결과 표 헤더 30px,
플레이어 180(원본 시작값과 동일).

#### 24. 파일 브라우저 실제 DB 트리 연결 (Codex 추가 확인)
원본 확인 지점:
- `database.py:get_folders()` — `audio_files.file_path` 의 부모 디렉토리 `DISTINCT` 목록을 사용한다.
  이 목록 자체에는 `hidden/removed` 필터가 없다. 폴더 구조는 남기고 카운트만 활성 파일 기준으로 붙인다.
- `database.py:get_all_folder_counts()` — `hidden=0`, `removed=0` 인 파일만 직접 카운트한 뒤 모든 조상 폴더에
  누적 합산한다.
- `database.py:get_all_folder_incomplete_counts()` — `meta_extracted IN (0,2)` 이면서 `hidden=0`, `removed=0` 인
  파일만 미완료로 누적한다.
- `folder_tree.py:load_folders()` — "전체" 가상 root 아래에 라이브러리 root 를 둔다. 같은 드라이브에 여러
  top-level root 가 있으면 공통 부모 가상 그룹을 만들고, 라이브러리 root 자체는 `sort_order` 순서를 따른다.
  중첩 root 는 하위 노드로도 root 표시를 유지한다.
- `folder_tree.py:set_blacklist_paths()` — 블랙리스트는 라벨 비교가 아니라 `ROLE_PATH` 정규화 경로의 정확한
  일치로 노드를 숨긴다. 부모 노드가 숨겨지면 하위도 함께 보이지 않는다.

PoC 반영:
- `TreeNode` 에 `path` 와 `incomplete` 를 추가했다. 이제 폴더 선택/탭 드롭/검색 스코프/블랙리스트 판정은
  라벨 조합 경로가 아니라 실제 절대경로를 사용한다.
- 새 Tauri command `sf_folder_tree` 가 원본 인덱스 DB(`%LOCALAPPDATA%\SoundField\index.db`)를 읽기 전용으로 열어
  실제 폴더 트리를 만든다. `audio_files` 는 구조 생성을 위해 전체를 읽고, 표시 카운트/미완료 카운트는 원본과
  같은 `hidden/removed` 필터를 적용한다.
- `Sidebar` 는 정적 `TREE` 대신 앱 시작 시 로드한 실제 DB 트리를 받는다. 더미 트리는 로드 실패 시 fallback
  으로만 남긴다.
- `Sidebar.pathOf()` 는 더 이상 조상 라벨을 `\` 로 이어 붙이지 않고, 노드의 실제 `path` 를 반환한다.
  따라서 검색 `path_prefixes` 가 원본 DB 경로와 같은 문자열로 들어간다.
- 블랙리스트 숨김은 `endsWith(label)` 에서 `normPath(node.path) === normPath(blacklist.path)` 로 교정했다.
- 미완료 누적이 있는 카운트는 원본 의미색인 `#f0a43c` 로 표시하고 툴팁에 미완료 개수를 노출한다.

남은 주의:
- 폴더 표시명 별칭(`folder_display_names`)은 아직 설정 파일에서 읽지 않는다. 원본은 `~/.soundfield_config.json`
  의 별칭을 `display_names` 로 넘긴다.
- 사용자 탭/즐겨찾기 저장소는 아직 세션 로컬 상태다. 원본 설정 저장까지 연결하려면 쓰기 정책을 별도로
  확정해야 한다.

#### 25. 실제 오디오 재생 브리지 1차 연결 (Codex 추가 확인)
원본 확인 지점:
- `playback.py:HybridPlayer` — 실제 재생은 `AudioEngine`, `QMediaPlayer`, `BinauralBackend` 중 하나로 라우팅된다.
  파일 선택 시 `setMediaMetadata(meta)` 후 `setSource(QUrl.fromLocalFile(path))` 를 호출해야 바이노럴 후보/채널
  레이아웃 판정이 맞는다.
- `HybridPlayer.setVolume()` — 엔진은 gain 0.0~2.0(+6.02dB)을 허용하고, QMediaPlayer fallback 은 1.0으로 cap 한다.
- `player_widget.py:_on_volume_changed()` — 슬라이더 위치를 `_vol_pos_to_gain()` 으로 바꿔 `player.setVolume()` 에 넘긴다.
  슬라이더 값 750은 0dB/gain 1.0, 1000은 +6.02dB/gain 2.0, 0은 mute.
- `HybridPlayer.setPlaybackRate()` — 바이노럴 중 1.0x가 아니면 기존 방식으로 fallback 한다.
- `player_widget.py:_restart_from_zero()` / Space 정책 — 같은 파일을 수동 재생할 때 설정 `playback_restart_from_zero`
  가 켜져 있으면 0ms부터 재생한다.
- `player_widget.py:_tick()` — 반복 재생은 구간 끝 30ms 전부터 `player.setPosition(loop_start)` 로 되감는다.

PoC 반영:
- `src-tauri/python/sf_audio_service.py` 를 추가했다. 장기 실행 Python/PyQt 사이드카가 원본 `HybridPlayer` 를 그대로
  import하고 JSON 명령(`load/play/restart/pause/stop/seek/rate/volume/binaural`)을 처리한다.
- 새 Tauri command `sf_audio` 가 사이드카를 한 번 띄운 뒤 stdin 으로 명령을 보낸다. 재생 상태를 유지하기 위해
  명령마다 Python 프로세스를 새로 만들지 않는다.
- `audioBridge.load(row)` 는 `row.fullPath` 와 sample_rate/channels/bit_depth/codec/bitrate/title/artist/album/genre/comments
  메타를 함께 보낸다.
- `App` 은 `current` 변경 시 `load`, `playing` 변경 시 `play/pause`, Stop 시 `stop` 을 보낸다.
- 같은 파일을 명시 수동 재생할 때는 `restart` 를 보낸다. Space 재개에서 `playback_restart_from_zero` 가 켜져 있으면
  0ms seek 를 보낸다.
- `Player` 의 Home/처음으로/파형 클릭/세그먼트 클릭/루프 되감기 지점은 화면 position 만 바꾸지 않고 `seek(ms)` 를
  함께 보낸다.
- 속도 슬라이더는 원본의 log2 매핑 결과 rate 를, 볼륨 슬라이더는 원본 `_vol_pos_to_gain()` 과 같은 dB→gain 결과를
  브리지에 보낸다.

검증:
- `python src-tauri/python/sf_audio_service.py` 에 `{"command":"quit"}` 를 stdin 으로 보내 원본 `HybridPlayer` import,
  QCoreApplication 시작, 종료가 되는 것까지 확인했다.

남은 주의:
- 현재 브리지는 상태 이벤트를 UI로 되돌려 보내지 않는다. 원본의 loading/buffered/end/error/binauralStatusChanged 를
  UI 상태바/버튼 상태에 반영하려면 stdout 이벤트 구독을 추가해야 한다.
- 파형 피크/세그먼트는 아직 원본 peak cache/extractor 가 아니라 기존 PoC 더미 파형이다.
- 영역 내보내기/DAW drag out 은 아직 실제 `crop_wav_region`/`render_speed_wav` 경로로 연결되지 않았다.

---

### 2026-09-01 (3차) 사용자 보고 9건 + 입력 이벤트 전수 대조

#### 24. 스플리터 손잡이가 과도하게 넓어 보임
폭 자체는 원본 값이 맞다 — `_GripSplitter(orientation, handle_width)` 호출이
main 16 / 결과↔플레이어 20 / 사이드 6 이고 QSS 는 `QSplitter::handle` 에 폭을 주지 않는다.
원인은 두 가지였다.
- 원본 사이드바는 `sv.setContentsMargins(0, 0, 7, 0)` 로 **우측 7px 여백**이 있어 트리와
  손잡이가 붙지 않는다. PoC 는 여백이 없고 대신 `border-right` 를 그려 손잡이가
  내용에 딱 붙은 굵은 띠로 읽혔다 → 여백 7px 복원 + border 제거.
- 엣지선을 2px, 밴드/가이드를 진하게 그려 대비가 원본보다 높았다 → 엣지 1px,
  알파를 원본 수준(border_strong α105 ≈ 41% → 26%)으로 낮춤.

#### 25. 세그먼트 버튼 호버 애니메이션
원본 `_TransportButton` 은 호버 상태를 그리지 않는다(주석에 "세그먼트 토글은 호버에
따라 변하지 않으므로 호버 애니메이션 없음"). **사용자가 두 번 요청한 개선 사항**이라
다른 버튼과 같은 결(140ms OutCubic, 채움 α 상승 + 외곽선 밝아짐)로 추가했다.
켜진 토글은 자기 색(세그먼트 초록 / 반복 보라)을 유지하며 살짝만 밝아진다.

#### 26. 파일 브라우저 키보드 조작 (누락 → 구현)
원본은 `keyPressEvent` 를 두지 않고 **QTreeWidget 기본 동작**을 그대로 쓴다.
그 기본 정책을 옮겼다: ↓↑ 이동 / → 펼치기(이미 펼쳐졌으면 첫 자식) /
← 접기(접혀 있으면 부모) / Home·End / PageUp·PageDown(뷰포트 행 수) /
Enter 활성화 / `*` 하위 전체 펼치기 / 글자 입력 타입어헤드(1초 버퍼).
Shift 조합은 범위 선택. 선택이 화면 밖이면 스크롤(Qt scrollTo).
실측 확인: ↓ 로 Soundlibrary 선택 → → 로 4행→8행 펼침 → ↓ 로 자식(UI) 이동 → ← 로 부모 복귀.
※ 결과표는 반대로 **타입어헤드를 껐다** — 원본이 `keyboardSearch` 를 명시적으로
비활성화했다("글자 키로 행이 점프하며 사운드가 멋대로 재생되던 동작 차단").

#### 27. 라이브러리 현황 램프 — "실패" 와 "경로 없음" 은 다른 모양
원본 `_status_color` + `_GlowDot(hollow=not exists)`:
- 경로 없음 → 빨강 **속 빈 원**(링 두께 max(1.25, size*0.095))
- 실패 존재 → 빨강 **채운 원**
- 대기 존재 → 주황 채운 원 / 정상 → 초록 채운 원
PoC 는 상태가 3종(ok/busy/off)뿐이라 실패와 경로 없음이 같은 모양이었다 →
4종(ok/busy/fail/missing)으로 분리. 범례 칩은 원본대로 3개 모두 채운 원.

#### 28. 라이브러리 검색 드롭다운의 트리 도식화 (누락 → 구현)
원본 `_SearchHitDelegate` 는 매치 행만 나열하지 않고 **조상 체인까지 포함한 트리**를
그린다. 옮긴 규칙: 들여쓰기 14px/단계, 연결선 뒤 글자 간격 7px,
연결선 rgba(122,133,148,.51) 로 통과 세로선(│)+막내 └/중간 ├,
조상(비매치) 행은 #5b636f dim + 클릭 불가, 우측 개수 rgba(78,144,232,.78),
호버 130ms OutCubic. 실측: "ui" 검색 시 Soundlibrary(문맥) → UI(매치, elbow) 2행.

#### 29. 중앙 로더 디자인 + 입력 차단
디자인: 백드롭 블러, 그라데이션 패널 + 그림자, conic-gradient 링(원본과 같은 40px/3.5px/280°)
+ 글로우, 등장 페이드. 수치(300×150, radius 12, 메시지 11pt Bold)는 원본 유지.
**추가로 발견한 누락**: 원본 오버레이는 `keyPressEvent`/`wheelEvent` 를 accept 해서
**키보드와 휠까지 막는다**(main_window.py:1016). PoC 는 마우스만 막고 있어 로딩 중에도
단축키가 먹혔다 → capture 단계에서 keydown/wheel 을 삼키도록 수정.

#### 30. 트리 카운트가 빽빽함 → 원본 폰트 정책 적용
원본 `_apply_counts` (folder_tree.py:868):
- 라이브러리 루트: 9pt **굵게** `#4E90E8`
- 하위 폴더: 9pt × **0.8**, 굵기 없음 `#999999`
- 미완료 포함: `#ffa64d` (루트만 굵게) + 툴팁
PoC 는 전부 11px 파랑이라 50px 칸을 넘칠 듯 보였다 → 위 정책대로 12px/9.6px 로 분리.
실측: 하위 카운트 9.6px rgb(153,153,153).

#### 31. 히스토리 드로어 슬라이드 애니메이션
원본은 `maximumWidth` 를 168ms OutCubic(DRAWER_MS)으로 보간한다.
PoC 는 `width` 만 트랜지션하고 **flex-basis 가 즉시 튀어** 애니메이션이 없는 것처럼
보였다. 게다가 뒤쪽 `.drawer` 규칙이 `transition: width` 만 남겨 앞 규칙을 덮고 있었다.
→ width/max-width/flex-basis/opacity 를 함께 트랜지션(실측 확인), 폭 드래그 중에는 해제.

#### 32. 새 사운드를 재생해도 이전 위치에서 재생
원본 `load_and_play` 는 **새 파일**이면 `_display_pos_ms=0`,
`wave.set_position_ratio(0)`, 시간 라벨 "0:00 / 0:00" 으로 되돌린다
(같은 파일 재요청도 `setPosition(0)`, 재생 중이었으면 `_restart_from_zero`).
→ 파일 경로가 바뀌면 재생 위치/영역 선택/활성 세그먼트를 리셋.

#### 33. 입력 이벤트 전수 대조에서 추가로 잡은 것
원본 `app/ui/*.py` 의 모든 `keyPressEvent / wheelEvent / mouse*Event /
enter·leaveEvent / eventFilter / drag*Event` 를 뽑아 하나씩 대조했다.

- **트리 Shift+휠 가로 스크롤 (누락)**: 원본 트리는 column 0 이 ResizeToContents 라
  긴 폴더명이 있으면 가로 스크롤바가 생기고 Shift+휠(틱당 50px)로 읽는다.
  카운트는 `_CountOverlay` 가 뷰포트 우측에 고정 paint 되어 스크롤과 무관하다.
  PoC 는 라벨을 `text-overflow: ellipsis` 로 잘라 이 동작 자체가 없었다 →
  행을 `width: max-content` 로, 라벨은 자르지 않고, 카운트는 `position: sticky; right: 0`
  + 불투명 배경으로 고정. Shift+휠 핸들러 추가. 실측: 카운트 sticky 고정 확인.
- **결과표 Shift+휠 이동량**: 원본은 틱당 50px(`delta*50/120`), PoC 는 브라우저
  deltaY(보통 100)를 그대로 써 두 배였다 → 50px 로 맞춤.
- **파형 줌 애니메이션 (누락)**: 원본은 target/current 를 나눠 매 프레임 40%씩
  지수 보간한다(zoom·amp 는 log-space, viewport_start 는 linear, `ZOOM_ANIM_T=0.4`).
  PoC 는 즉시 점프라 휠 줌이 끊겼다 → 같은 보간을 rAF 로 구현. 스크롤바 드래그는
  원본 `set_viewport_start` 처럼 애니메이션 없이 즉시 반영.
- 이미 일치했던 것(확인만): 결과표 Space 재생 / 결과표 타입어헤드 비활성 /
  슬라이더 우클릭·Ctrl+클릭 리셋 / 필터 콤보·스핀박스 휠 차단 / 파형 Ctrl·Ctrl+Alt·Shift+휠
  분기와 한계값(1.5x, MIN/MAX_AMP 1~80, max_zoom = 600×duration_ms/w) /
  트리 전체 행 클릭 선택 / 우클릭이 선택을 바꾸지 않음 / 우클릭 대상 규칙.

---

## 34. 이번 회차 전수 대조 (원본 재통독) — 2026-09-01

### 34-1. 폴더 이름 변경 / 블랙리스트 추가 (원본 QInputDialog)
- 이름 변경: **라이브러리 루트만** 가능하다 (`folder_tree._show_context_menu` 의
  `if roots: … if len(roots) == 1`). 일반 하위 폴더에는 이 항목이 없다.
- 대화상자 제목 `이름 변경`, 라벨 `새 이름:\n{전체경로}`, 기본값 = 현재 표시명.
- 저장은 실제 폴더명을 바꾸지 않는다. config `folder_display_names` 에
  `normpath(path).rstrip("\/").lower()` 키로 넣고 트리 라벨만 바뀐다
  (`main_window._on_folder_rename_request`). 빈 문자열도 그대로 저장된다.
- 블랙리스트 추가: 경로마다 순서대로 `블랙리스트 추가` / `설명 (선택):\n{경로}` 를
  묻는다. 이미 등록된 경로는 **묻지 않고 건너뛴다**. 취소하면 그 경로만 건너뛰고
  다음 경로로 간다. 하나라도 추가되면 블랙리스트 패널이 펼쳐진다
  (`blacklist_panel.add_paths` → `_set_expanded(True)`).
- 결과표 우클릭의 두 블랙리스트 항목도 이어붙였다:
  · `이 사운드 블랙리스트에 추가` → 파일 경로 자체 (다중 선택이면 선택분 전체)
  · `이 라이브러리 블랙리스트에 추가` → 가장 긴 매칭 루트, 없으면 상위 폴더
    (`main_window._on_results_add_to_blacklist`)
- 패널 헤더의 `+` 추가 버튼은 원본에 **없다** (`_on_add_clicked` 는 연결되지 않은
  죽은 코드). "블랙리스트에 추가할 폴더 선택" 문구도 그래서 도달 불가.

### 34-2. 라이브러리 순서 바꾸기 (원본 folder_tree 재정렬)
- 조건: 전체 탭 + 루트 + 단일 선택 + 형제 2개 이상 (`_is_reorderable_root` +
  `parent.childCount() > 1`).
- 우클릭 `위로 이동`/`아래로 이동` 은 전체 탭·루트 1개일 때만 나오고
  여유가 없으면 비활성이다.
- 드래그: 끌고 다니는 동안 **실제 순서를 미리 바꿔** 보여주고(`_preview_root_drag_at`),
  삽입 위치는 각 행 중앙선 기준(`_root_insert_index_at`)이다. 자리를 옮긴 행은
  140ms OutCubic 으로 옛 위치에서 새 위치로 미끄러진다(`_animate_root_reorder`).
  트리 밖으로 나가면 스냅샷으로 되돌리고 드래그를 취소한다(`dragLeaveEvent`).
  끌고 있는 행은 배경 rgba(78,144,232,42) + 1px rgba(126,181,255,160) + 좌측 3px
  #7eb5ff (`drawRow`).
- 원본은 `manager.reorder_roots` 로 `library_roots.sort_order` 를 UPDATE 한다.
  PoC 는 index.db 가 읽기 전용이라 오버레이 `root_order(path, ord)` 에 기록하고
  루트 목록을 읽을 때 그 순서를 씌운다.

### 34-3. 라이브러리 검색 드롭다운
- `_search_matches` 는 **정렬하지 않는다** — `iter_folder_nodes()` 의 트리 순회
  순서를 그대로 쓰므로 `하나씩 찾기(↑/↓)` 도 트리 위→아래 순서로 돈다.
  (개수 내림차순 정렬은 폐기된 legacy 완성기 경로의 정책이다. PoC 가 그걸 쓰고
  있어 nav 순서가 원본과 달랐다 → 고쳤다.)
- 블랙리스트 경로와 **그 하위**는 매치에서 빠지고 `_search_blacklisted` 로 따로
  모여 드롭다운에 빗금 + #e06c75 + 전각공백 + `블랙리스트 · {사유}` 로만 보인다
  (클릭·호버 불가).
- 개수는 `매치 + 비블랙리스트` 행에만 표시한다 (조상 문맥 행은 0 → 안 그림).
- 이름은 표시명이 아니라 **실제 폴더명**(경로 마지막 칸) 기준으로 검색한다.

### 34-4. 미완료 항목 다이얼로그 (FailedPendingDialog)
원본과 어긋났던 부분을 전부 맞췄다:
- 머리글 대기/실패 수는 표에 실린 행 수가 아니라 **DB 전체 집계**다.
- 카운트 라벨: 로딩 중 `불러오는 중...`, 끝나면 total > 표시분이면
  `표시 N / 필터 전체 T — limit 초과분은 [필터된 전체] 버튼으로 처리`,
  아니면 `표시 N (전체 T)`.
- 3열은 폴더가 아니라 **전체 경로**다.
- 사유는 `meta_error` 를 ERR_MAP 으로 한글화하고 툴팁에 원문을 남긴다
  (`지원하지 않는 파일 형식`/`파일을 찾을 수 없음`/`접근 권한 없음`/
   `분석 시간 초과 (대용량/네트워크 지연)`/`무시된 사이드카 파일`/
   `알 수 없는 추출 오류`/`OS 입출력 오류`/`파일 끝(EOF) 도달 오류 (손상된 파일 가능성)`/
   `데이터 형식 오류`, 값이 없으면 대기는 `분석 대기 중`, 실패는 `알 수 없는 오류`).
- 표는 줄바꿈 없이 가로로 길게 + Shift+휠 가로 스크롤(감도 delta*1.5).
- 우클릭 → `파일 위치 열기`.
- 하단 2줄째: `필터된 전체 (표시 limit 무시):` + `필터된 전체 재시도`/`필터된 전체 제거`
  (툴팁 전문 포함).
- 선택 없이 누르면 `선택 필요` / `재시도할 항목을 선택하세요.` ·
  `제거할 항목을 선택하세요.`
- 제거 확인 문구도 원본 전문 그대로 (실제 파일은 삭제되지 않는다는 안내 + 전체 갱신
  시 다시 돌아온다는 주의).
- ⚠ 재시도·제거는 index.db 쓰기라 PoC 에서는 확인까지만 하고 안내로 끝난다.
- 아직 없는 것: 2초 주기 실시간 갱신 + `● 메타 재분석 진행 중` 배너
  (PoC 에 인덱싱 진행 상태가 없어 띄울 근거가 없다).

### 34-5. 바이노럴 채널 배치
- 저장소(`app/binaural.py:LayoutOverrideStore`)를 그대로 옮겼다: 파일별/폴더별
  두 층, 조회는 파일 → 폴더(그 채널 수) 순, `scope()` 는 `file`/`folder`/`""`,
  토큰은 `preset|order`(order 는 wave/film 만 인정). 저장 위치는 원본 파일을
  건드리지 않도록 `poc\binaural_layouts.json` 사본이다.
- 상세 줄은 두 상태만 있다 (`_set_layout_warning_visible`):
  · 확인 필요 → 후보가 {ambix,fuma} 뿐이면 `앰비소닉 규격 확인 필요`, 그 외
    `채널 배치 확인 필요`. 가운데 정렬, #e45b64. **초기화 버튼 없음.**
  · 저장됨 → `폴더 설정 · {라벨}` 또는 `개별 저장됨 · {라벨}`. 오른쪽 정렬,
    #9fc4ff. 초기화 버튼이 함께 나온다.
  (PoC 는 확인 필요 상태에 초기화를 붙이고 저장됨 상태가 아예 없었다 → 고쳤다.)
- 메뉴에 빠져 있던 것 3개를 채웠다: `6.1`(7채널) 프리셋,
  `채널 배치 비교해서 듣기`(lcr/5.0/5.1/7.0/7.1/7.0.2 선택 시만 활성),
  `현재 폴더의 N채널 파일에 적용`(지정값이 있을 때만). PoC 가 임의로 넣었던
  판정 사유 행은 원본에 없어 제거했다.
- 비모달 창 2개를 새로 만들었다 (`_AmbisonicFormatDialog` 510px /
  `_ChannelOrderDialog` 520px): 문구·버튼·상태 라벨·저장 조건 전문 일치.
  판정이 AmbiX/FuMa 로 갈리면 그 파일에서 한 번만 자동으로 뜬다
  (`_layout_prompted_paths`).
- IEM 설치 안내(`IEM 플러그인 설치 필요` + `무료 설치 페이지 열기`)도 만들었다.
  버전 `1.15.0`, 주소 `https://plugins.iem.at/download/` (원본 상수).
- 버튼 툴팁은 `_binaural_policy` 의 8단 우선순위를 그대로 옮겼다.

### 34-6. 결과표
- `BASE_COLUMNS` 순서는 원본의 **화면 순서**가 맞다: 원본도 `_setup` 에서
  `moveSection(duration → 0)` 으로 길이를 맨 앞에 보낸다(results_table.py:781).
  논리 0 이 file_name 인 것은 delegate/UserRole 매핑 때문이며 시각 순서와 무관하다.
  → 잠깐 file_name 앞으로 되돌렸다가 근거를 확인하고 원상 복구했다.
- 재생 표시 상태 기계(`set_pip`)를 이어붙였다: 재생 요청 → loading(빨강 박동),
  실제 재생 시작 → playing(초록 박동), **일시정지/정지 → ended(파란 점등)**.
  행 강조(그라데이션 + 좌측 accent 선 + 파일명 accent)는 상태와 무관하게
  pip 이 붙은 행에 남는다 (원본 `_playing_row` 가 ended 에도 그 행을 가리킨다).
- 행 배경색을 원본 값으로 고쳤다: 선택 = accent α46, 재생 = accent α38 →
  폭 60% 에서 투명해지는 좌→우 그라데이션, 둘 다 좌측 2px accent 선.
  파일명은 재생 행에서 accent + DemiBold (PoC 는 흰색 알파 + text 색이었다).

### 34-7. 라이브러리 현황 패널
- 목록이 비면 `라이브러리 없음` 한 줄 (원본 `_apply_data`).
- 툴팁은 **점에만** 붙고 `" · "` 로 잇는다: 경로 없음이면 `{경로} · 경로 없음`,
  아니면 `{경로} · 실패 N · 대기 N`, 둘 다 0이면 `{경로} · 정상`.
  이름 라벨 툴팁은 경로 하나뿐이다.

### 34-8. 확인만 하고 넘어간 것 (원본에서 도달 불가한 코드)
- `_ThemeToggleButton` (`테마 전환 → {라이트|다크} 모드`) — 어디에서도 생성하지
  않는 죽은 코드다. 테마는 환경설정에서만 바꾼다.
- `player_widget.meta_label` (`채널 · 샘플레이트 · 비트뎁스` 툴팁) —
  `setVisible(False)` 로 항상 숨겨져 있다.
- `_prompt_orphan_dup_restore` (`중복 아님 — 복원 확인`) — 인덱스 삭제 작업
  뒤에만 뜬다. PoC 는 삭제를 실행하지 않으므로 도달하지 않는다.
- `ChangeReviewDialog` (`변경사항 검토`) — 변경 감지 스캔 뒤에만 뜬다.
  스캔 자체는 읽기 작업이지만 인덱싱 파이프라인이 PoC 에 없어 미구현.

### 34-9. 재생 히스토리 드로어
- 목록 항목 드래그는 결과표와 **규칙이 다르다**: `_DraggableHistoryList` 는 행 이탈
  조건 없이 맨해튼 10px 만 넘으면 바로 드래그를 시작한다. (PoC 가 결과표 규칙을
  쓰고 있어 좌우로만 끌면 드래그가 안 걸렸다 → 별도 규칙으로 분리했다.)
- Shift+휠 = 가로 스크롤. 감도는 delta 그대로다 (결과표/트리의 50px 틱과 다름).

### 34-10. 파형 마우스 (원본 WaveformView, player_widget.py:2268)
- 임계값(6px)은 **화면 픽셀**로 재야 한다:
  `delta_px = |r - anchor| * 뷰폭 * zoom_factor`.
  (PoC 는 zoom 을 빼먹어 확대할수록 임계값이 커졌다 → 고쳤다.)
- 누른 지점이 **기존 선택 영역 안**이면 "extdrag": 임계값을 넘으면 그 영역을 잘라
  DAW 로 끌어낸다. 밖이면 "select": 넘으면 새 영역을 만들고, 못 넘고 놓으면 seek.
- 세그먼트 헤더는 누르는 즉시 `세그먼트 시작 - 0.1s` 로 seek 하고, 임계값을 넘게
  끌면 그 세그먼트 구간을 잘라 내보낸다 ("headerdrag"). 커서는 끌 때 손을 쥔 모양.
- Esc 는 선택 영역을 지운다. 우클릭도 지운다.
- 내보낼 파일은 원본 `_resolve_export_path` 규칙 그대로 만든다:
  세그먼트 범위 → 선택 영역 → 원본 전체 순으로 고르고, 배속이 1.0x 가 아니면
  `render_speed_wav` 로 배리스피드를 렌더한다 (`이름_0.45x.wav`).
  crop/렌더가 실패하면 원본 전체로 폴백한다 (원본도 경고만 남긴다).
  구현은 원본 `app/region_export.py` 를 파형 서비스에서 그대로 호출한다 —
  bext/iXML/cue 청크 보존 규칙을 다시 만들지 않는다.
- 드롭이 성사되고 `stop_on_drag` 가 켜져 있으면 **재생만** 멈춘다 (선택 영역 유지).

### 34-11. 결과표 재생 표시 / 행 강조
- `set_pip` 상태 기계: 재생 요청 → loading(빨강 박동) / 실제 재생 → playing(초록
  박동) / **일시정지·정지 → ended(파란 점등)**. 원본은 일시정지에도
  `playingChanged("")` 를 보내므로 파란 점이 맞다.
- 행 강조는 pip 상태와 무관하게 pip 이 붙은 행에 남는다 (`_playing_row`).
- 색: 선택 = accent α46 + 좌측 2px accent, 재생 = accent α38 → 폭 60% 에서
  투명해지는 좌→우 그라데이션 + 좌측 2px accent, 파일명 accent + DemiBold.

### 34-12. 트리 펼침/접힘 정책
- 기본 펼침: 전체 탭은 '전체' + **드라이브 그룹 노드**까지만, 다른 탭은 그룹
  노드만 (라이브러리 안쪽은 접힘). PoC 는 '전체' 만 펼쳐 라이브러리가 그룹 밑에
  숨어 있었다 → 고쳤다.
- 펼침 상태는 재시작 간 저장하지 않는다 (원본 명시 정책). 세션 안에서는 유지.
- **선택 폴더의 상위를 접으면 선택이 그 상위로 올라간다** (검색 범위도 따라 올라감).
  원본은 이때 스크롤 위치를 보존한다 — 같은 처리를 넣었다.
- 우클릭 `모두 접기` = 우클릭한 폴더 **자신과 그 아래 전부**.

### 34-13. 트리/결과 메뉴에서 이어붙인 나머지
- `모두 접기`, `★ 즐겨찾기에 추가/제거`, `이 탭에서 삭제`(사용자 탭은 excluded,
  즐겨찾기 탭은 목록에서 제거) — 전부 트리 상태만 바꾸는 동작이라 그대로 동작한다.
- 재스캔/라이브러리 제거/완전 제거는 index.db 쓰기라 여전히 막혀 있다.

### 34-14. 설정 저장 범위 (원본 _actual_save_config, main_window.py:4971)
원본이 저장하는 값 중 PoC 가 빠뜨렸던 것을 채웠다 — 모두 **사본**에 쓴다:
- `filters` (검색어 행 + 길이 + 샘플레이트/채널 **콤보 인덱스**) → 재시작 후 복원.
  ⚠ 콤보는 인덱스로 저장되므로 목록 순서를 바꾸면 복원이 깨진다.
- `favorites` / `tabs`(name·items·excluded) / `last_selection`(탭 + 경로)
- `volume_pos1k`(0~1000) / `speed_rate`
- `history_width`(grip 놓을 때) / `history_visible`(**닫을 때만** — 원본과 같음)
- `blacklist_expanded_h`
- 분할 위치는 원본이 Qt splitter state(불투명 base64)로 저장해 그대로 못 읽는다 →
  같은 목적의 자체 키 `poc_side_w` / `poc_player_h` 를 쓴다.
- 저장은 원본과 같이 1초 디바운스로 묶는다.

### 34-15. 상태바 / 헤더
- 검색이 400ms 를 넘길 때만 `검색 중...` 을 띄운다 (원본 `_search_busy_timer`).
  결과가 오면 결과 메시지로 덮어쓴다.
- `인덱싱 집중` 체크박스는 **켤 때만** 경고를 띄우고 [예] 가 아니면 되돌린다.
  확정되면 `index_focus_mode` 를 저장한다.
- `⚠ 검색 정리` 버튼은 `index_meta.fts_stale == "1"` 일 때만 나타난다.
  같은 조건에서 800ms 뒤 세션당 한 번 `검색 색인 어긋남` 안내를 띄운다
  (수리 자체는 쓰기라 막혀 있다).
- 히스토리 버튼 문구는 원본의 3단 규칙을 따른다:
  처음 `히스토리 ◀` / 열림 `▶ 히스토리` / 토글로 닫은 뒤 `◀ 히스토리`.

### 34-16. 현재 라이브러리 다이얼로그
- `실패 전체 재시도` / `실패 항목 제거` 의 앞단 검사 순서를 원본과 맞췄다:
  선택 없음 → `선택 필요` / `처리할 라이브러리를 선택하세요.`,
  실패 0 → `실패 없음` / `선택한 라이브러리에 실패 항목이 없습니다.`, 그다음 확인.
  실패 수는 **표에 표시된 값**을 재사용한다 (원본도 재조회하지 않는다).
- 목록이 비어 있는 동안 `…` / `라이브러리 정보 로딩 중...` 한 줄을 보여준다.

### 34-17. 검색 제외 목록 (제외 관리)
원본 `_open_suppressed_manager` 를 그대로 옮겼다 (780x480):
- 안내 3줄, 검색창 `검색 (파일명/경로 — 전체 목록에서 찾음)` — 표시 상한과 무관하게
  숨김 **전체**에서 찾고 입력 250ms 디바운스로 재조회한다.
- 표시 상한 2000건. 요약줄 문구는 검색어 유무로 갈린다
  (`일치 N건 · 표시 M건 (상한 2,000)` / `표시 M건 · 미표시 K건 (상한 2,000 — 검색은 전체에서 찾음)`).
- `[중복 아님 · 미복원]` 태그는 표시 직전에 `verify_dup_orphans` 와 같은 프로브로
  유효성을 다시 확인한다 (쌍둥이가 돌아온 행은 태그를 내린다). 색 #ffa64d.
- 우클릭 4종(탐색기에서 보기 / 경로 복사 / 이 항목만 즉시 복원 / 같은 폴더 전부 체크).
- 환경설정 `제외 관리` 버튼 라벨에 숨김 개수가 붙고 0건이면 비활성이다.
- 복원(unhide)만 쓰기라 막혀 있고, 목록·검색·체크는 전부 동작한다.

### 34-18. 라이브러리 검색 드롭다운 정렬 (재확인)
`하나씩 찾기(↑/↓)` 순서 = 트리 순회 순서. 개수 내림차순 정렬은 폐기된 legacy
완성기(`_ensure_search_completer_legacy`) 정책이므로 쓰지 않는다.

### 34-19. 긴 경로(MAX_PATH) 정책 (원본 results_table.py:325~395)
- `_LONG_PATH_LIMIT = 256`. 앱 자체는 긴 경로를 열 수 있지만 탐색기/DAW 는 못 연다.
- **DAW 드래그**: 256 이상이면 `%TEMP%\SoundField_longpath` 에 짧은 이름으로 복사해
  그 경로를 넘긴다. 이름 = `원본stem_해시8자.확장자` (해시를 뒤에 둬 이름 앞부분이
  원본과 같게 — 이름으로 재검색 가능). 같은 크기의 복사본이 있으면 재사용.
  PoC 는 sha1 대신 FNV-1a 앞 8자를 쓴다 (의존성 없이 실행 간 동일한 이름 보장 —
  표준 DefaultHasher 는 프로세스마다 시드가 달라 못 쓴다).
- **탐색기에서 보기**: 256 이상이면 `/select` 가 파일을 못 찾아 바탕화면이 뜬다 →
  한계 안에 들어오는 가장 가까운 부모 폴더를 대신 연다.

### 34-20. 시작 시 정리 / 중복 실행 (원본 app/main.py)
- `cleanup_temp_files()`: `%TEMP%\SoundField_regions` 와 `%TEMP%\SoundField_longpath`
  에서 **24시간** 지난 파일을 지운다. 두 폴더는 원본 앱과 공유하므로 규칙도 같다.
  PoC 도 시작 직후 백그라운드로 같은 정리를 한다.
- 파형 피크 캐시 세대 정리(`cleanup_stale_peaks_cache`)는 **하지 않는다** — 그건 원본이
  자기 버전 마커로 판단해 지우는 것이고, PoC 가 끼어들면 사용자 실제 캐시를 날린다.
- **중복 실행 방지**: 원본은 QLocalServer 로 두 번째 실행을 막고 기존 창을 앞으로
  올린다. PoC 도 tauri-plugin-single-instance 로 같게 만들었다 (두 번 더 실행해
  프로세스가 1개만 남는 것으로 확인). 인덱스 DB/사이드카를 두 프로세스가 동시에
  잡는 것도 이걸로 막힌다.

### 34-21. 파형 로딩 3단 (원본 begin_loading/load, player_widget.py:1650)
- 파일 전환 → 즉시 비우고 "파형 로딩 중...", 추출은 **650ms 디바운스** 후 시작.
- `.wav` + **30MB 이상**이면 quick(16 슬라이스) 러프 파형을 먼저 띄운다.
  quick 캐시가 있으면 그걸 쓰고, 없으면 sparse 추출 후 quick 캐시에 저장한다.
- quick 이 떴으면 **1800ms** 뒤에 full 추출로 바꿔치기한다 (재생 버퍼링/NAS 경합 회피).
  quick 을 건너뛴 파일은 곧바로 full 로 간다.
- 파일이 바뀌면 가로 줌·**세로(진폭) 줌**·스크롤을 전부 초기화한다
  (`_file_switch_zoom` — PoC 는 진폭 줌을 남겨 두고 있었다).

### 34-22. 그 밖에 이번에 맞춘 것
- 결과표 숫자 정렬: 값이 없는 셀은 정렬 방향과 무관하게 항상 맨 뒤
  (`_NumericSortItem.__lt__`).
- 컬럼 순서·표시·너비를 config `columns` 에도 저장 (기존엔 로컬 저장만).
- 트리 행 툴팁 = 절대경로(`setToolTip(0, abs_path)`).
- 미완료(주황) 카운트는 **보이는 가장 깊은 노드에만** 표시
  (`_refresh_incomplete_item` — 상위·하위 이중 표시 방지).
- 사이드바 `경로 폴더 N` 의 N = 등록 라이브러리 수 (드라이브 그룹 노드 수 아님).
- 라이브러리 검색: 입력창 X(clear) 버튼, 드롭다운 버튼 툴팁 "검색 결과 목록",
  `하나씩 찾기` 안내는 결과가 있을 때만.
- ↑/↓ 순차찾기는 선택하지 않고 위치만 드러내며 **화면 가운데로 스크롤**하고
  가로 스크롤을 0 으로 되돌린다. 드롭다운 클릭은 명시 선택 + 드롭다운 닫기.
- 재생선 글로우가 재생 중에만 숨쉰다 (α105↔α60, 768ms 삼각파).
- 상태바에 미완료 잔량 표시 (`미완료  N` / idle 정책이면 `분석 일시정지  N — 앱 사용 중`).
- 헤더 `_GlobalLoader` 는 내용이 없으면 자리를 차지하지 않는다 (원본도 접힘).
- 상태 점(`_GlowDot`)을 원본 픽스맵 구조대로 다시 그렸다 — 13/14px 상자,
  헤일로 그라데이션 + 6.1px 코어, 경로 없음은 링만.
- 탭 우클릭 `이름 변경` 을 이어붙였다 (중복 이름이면 "같은 이름의 탭이 이미 있습니다.").
- `_add_library` 판정이 더미 목록(LIBS)을 보고 있었다 → 실제 등록 루트 기준으로 고쳤고,
  대화상자 문구의 깨진 문자열 보간(`\n{경로}` → 실제 경로)도 고쳤다.

### 34-23. 이어붙인 쓰기-차단 흐름 (문구는 원본 그대로, 실행만 막힘)
버튼이 아무 반응 없이 닫히던 것들을 원본 확인 대화상자까지 그대로 띄우게 했다.
[예] 를 눌러도 index.db 는 건드리지 않고 상태바에 읽기 전용 안내를 남긴다:
- 헤더 `빠른 갱신` / `전체 갱신` — 루트가 없으면 `라이브러리 없음` 안내(확인 1개),
  있으면 원본 확인 문구 전문.
- 트리 우클릭 `빠른 재스캔` / `전체 재스캔` (원본은 확인 없이 바로 시작 → 안내만),
  `라이브러리 제거` / `라이브러리 완전 제거` (확인 문구 전문, 완전 제거는 경고 수위 높음).
- 환경설정 중복 검수 `검색 제외` — 스캔 결과가 있을 때만 활성, 확인 문구
  "중복 그룹 N개에서 경로가 가장 짧은 1개씩 유지하고 나머지 M개를 검색에서 제외합니다…".
- 검색 제외 목록 `전체 복원` — 확인 문구
  "검색 제외된 N건 전부를 검색 결과에 복원합니다.\n진행할까요?".
  (`체크 항목 복원` 은 원본도 확인 없이 바로 실행한다.)
- 확인창은 원본의 question(예/아니오) 과 information(확인 1개) 을 구분해 띄운다.

### 34-24. 실측으로 확인한 것 (이번 회차)
- quick 파형: 54.9MB WAV → 16 슬라이스 / 2ch / 48kHz 로 즉시 반환 (원본 QUICK_SLICES=16).
  30MB 미만 파일은 `skipped` 로 건너뛴다 → 곧바로 full 추출.
- 영역 crop: `%TEMP%\SoundField_regions` 에 실제 WAV 생성 확인 (원본 crop_wav_region 호출).
- 숨김 목록 쿼리: `idx_hidden_path_v2` 커버링 인덱스로 2000건 **2ms**.
  검색(LIKE) 은 44만 숨김 행에서 약 200ms → 250ms 디바운스와 맞는다.
- 실제 DB 상태: hidden 440,485 / failed 220 / pending 0 / fts_stale "0".
  → `제외 관리 (440,485)`, 상태바 `미완료  220`, `⚠ 검색 정리` 숨김이 정상 동작 조건.
- 중복 실행 방지: 두 번 더 실행해도 최초 프로세스 1개만 남는 것 확인.
- ⚠ 버그 하나 잡음: Rust 쪽 `ESCAPE '\'` 가 Rust 문자열 이스케이프로 먹혀 SQL 에
  빈 문자열이 들어가고 있었다 (`ESCAPE expression must be a single character`).
  숨김 목록 **검색** 경로가 통째로 실패하던 문제 → `ESCAPE '\'` 로 고쳤다.
- 상태바 미완료 합계는 pending + **failed** 여야 한다 (원본 count_incomplete_total).
  pending 만 더해 실패 220건이 표시되지 않고 있었다 → 고쳤다.
