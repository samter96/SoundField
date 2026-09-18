# 기능 연결 현황 (전수) — 2026-09-01 (2026-09-02 갱신)

기준: **원본과 같은 구조 + 기능 + 동작 + 정책**, 디자인(아이콘/색/애니메이션)만 개선.
아래는 "화면에는 있는데 실제로 이어져 있는가" 를 항목별로 확인한 표다.
원본 코드 위치를 함께 적어 다음 세션이 같은 근거로 이어갈 수 있게 한다.

---

## A. 연결 완료 (실제 데이터/동작)

| 기능 | 원본 근거 | 연결 방식 |
|---|---|---|
| 검색 | `Database.query` (database.py:2493) | 원본 함수를 그대로 호출하는 파이썬 브리지(`sf_query.py`). FTS5 trigram, 정확검색 단어색인, UCS 동의어, 짧은 토큰 LIKE 폴백, 블랙리스트 제외가 모두 원본 경로다. 40ms 디바운스 + signature 중복 차단(원본 `_do_search`) |
| 결과 초기 목록 | `_do_search` 초기 호출 | `audio_files` 에서 `hidden/removed` 제외 + `LIMIT`(config `search_limit`). 전역 ORDER BY 없음(원본도 없음) |
| 라이브러리 현황 | `LibraryStatusInline.refresh_async` (main_window.py:1469) / `count_files_under`, `count_metadata_status_under` | 2단계: 루트 목록 즉시 → 루트별 파일수/대기/실패는 도착 시 채움("…" 표시). 루트별 카운트는 병렬 |
| 상태바 인덱싱됨 | `_update_count_label` (5830) | `COUNT(*)` (hidden/removed 제외) |
| 파일 브라우저 트리 | `get_folders`, `get_all_folder_counts`, `FolderTree.load_folders` | 실제 폴더 3.7만개 + 누적 카운트 + 미완료(0/2) 카운트. 노드마다 절대경로를 들고 다녀 선택/드래그/스코프가 실제 경로 기준 |
| 블랙리스트 숨김 | `_apply_blacklist_visibility` | 정규화 경로 일치로 숨김(이전엔 라벨 endsWith 라 동명 폴더가 함께 숨을 수 있었다) |
| 블랙리스트 목록 | `get_blacklist_paths` (529) | DB 읽기. 0개면 0개로 표시(더미로 대체하지 않음) |
| 실제 재생 | `HybridPlayer` (playback.py) | 상주 파이썬 사이드카(`sf_audio_service.py`)가 원본 HybridPlayer 를 그대로 import. load/play/pause/stop/seek/rate/volume/binaural. 볼륨은 원본 `_vol_pos_to_gain` 곡선 |
| 파형 | `_extract_peaks`, `_load_cached_peaks`, `peaks_cache` | 상주 서비스(`sf_waveform_service.py`)가 원본 추출기/캐시를 그대로 사용. 레벨 피크(1024/4096/16384/65536)를 int16+base64 로 전달, 레벨 선택은 원본 `_visible_level_index` 규칙 |
| 샘플 단위 확대 | `_read_raw_samples` (929) | 확대해서 픽셀당 표본이 1개 이하가 되면 원본 함수로 그 구간 표본을 읽어 폴리라인으로 그린다(상한 `RAW_RENDER_MAX_SAMPLES` 200000) |
| 세그먼트 | `_extract_peaks` 의 segments, `_has_visible_segments` (1504) | 실제 검출 결과를 쓴다. 토글 ON + 1개 이상이면 헤더 표시(단일도 표시), 없으면 헤더 영역 자체가 없다 (이전엔 5등분 더미) |
| 환경설정 | `SettingsDialog.get_config` (3998) | config 를 단일 소스로 두고 저장 시 결과표/플레이어/단축키에 동시 반영(원본 `_apply_settings`) |
| 단축키 | `_setup_shortcuts` (5007) | config `shortcuts` 6종을 그대로 읽어 동작 |
| 히스토리 | `_load_history` / `set_items` (player_widget:3885, history_panel:292) | `history.json` 읽기 + 이 세션 재생분 병합(같은 경로는 뒤로 이동, 100개 상한). 최신이 위, 라벨은 파일명·툴팁은 전체 경로 |
| 탐색기에서 보기 | `results_table._reveal_in_explorer` | `explorer /select,` (없으면 상위 폴더) |
| 파일 브라우저에서 보기 | `revealInBrowserRequested` → `focus_path` | 결과 우클릭 → 트리에서 그 폴더(없으면 가장 깊은 조상) 펼치고 선택 |
| 미완료 항목 창 | `FailedPendingDialog` + `get_incomplete_metadata_under` (1125) | 실제 대기/실패 목록, 필터(전체/대기만/실패만), 상태색(대기 #7adfc4 / 실패 #ff7878) |
| DAW 드래그 아웃 | `results_table.mouseMoveEvent` (1263) / `history_panel.dragDropped` | `tauri-plugin-drag` 로 OS 드래그를 시작한다. 조건도 원본과 같다: **누른 행의 세로 띠를 벗어나야** 시작(10px 미만 흔들림 무시, 시간 조건 없음), 대상은 선택 전체, 드롭 성사 시 `stop_on_drag` 면 재생만 정지(파형/이름 유지) |
| 블랙리스트 추가·제거 | `add_blacklist_path` / `remove_blacklist_path` | PoC 오버레이 DB 에 기록 (원본 index.db 는 건드리지 않음) |
| 환경설정 저장 | `_actual_save_config` (4971) | 사본 `.soundfield_config.poc.json` 에 원본 키 이름 그대로 저장 |
| 중복 검수 스캔 | `find_duplicate_groups` (1430) | 파일명+크기 그룹. 원본과 같은 2패스(카운트 → 경로 수집), 정렬도 동일 |
| 중앙 로더 | `_CenterLoaderOverlay` (912) | 트리 갱신 중 입력 차단 + "폴더 트리 갱신 중..." (원본도 시작 시 같은 경로) |

## B. 쓰기 정책 — 사용자 승인: **사본에 먼저 붙인다**

- 설정: `~/.soundfield_config.json` 을 첫 실행 때 **`.soundfield_config.poc.json` 으로 복제**
  하고 PoC 는 사본만 읽고 쓴다. 환경설정 "저장" 이 실제로 파일에 남는다(원본 키 이름 그대로,
  PoC 가 관리하지 않는 키는 보존).
- 블랙리스트: 실제 `index.db` 대신 **오버레이 DB**
  `%LOCALAPPDATA%\SoundField\poc\overlay.db` 에 기록한다
  (`bl_add` 추가분 / `bl_hide` 가림). 읽을 때 (원본 ∪ bl_add) − bl_hide 로 합친다.
- **인덱스 DB 전체 복제는 접었다 — 실측 `index.db` 가 16.7GB 다**
  (FTS5 trigram 색인 포함). 복제에 수 분 + 16.7GB 가 들고, 사본을 쓰면 원본 앱이
  그 뒤로 인덱싱한 내용이 PoC 에 안 보인다. 그래서 읽기는 원본, 쓰기는 오버레이로 나눴다.

- 라이브러리 표시 순서: 원본은 `library_roots.sort_order` 를 UPDATE 한다 →
  오버레이 `root_order(path, ord)` 에 기록하고 루트 목록을 읽을 때 그 순서를 씌운다.
- 폴더 표시이름(이름 변경): 원본과 같이 **실제 폴더명은 건드리지 않고** config
  `folder_display_names` 에만 저장한다 (사본에).
- 채널 배치 지정: `pocinaural_layouts.json` 사본 (원본 파일 미변경).

### 아직 못 이은 것 (audio_files 자체를 고쳐야 함 → 실제 DB 쓰기 승인 필요)

- 인덱싱 전체: 라이브러리 추가 등록, 빠른 갱신, 전체 갱신, 취소, 재스캔(빠른/전체),
  라이브러리 제거 / 완전 제거
- 미완료 항목 재시도(Phase2) / 실패 항목 제거 / 실패 전체 재시도
- 중복 검수의 "검색 제외" / "제외 관리 복원" (스캔·목록은 연결됨)
- FTS 정리/재생성

⚠ 위 항목은 **버튼이 조용히 닫히지 않는다** — 원본 확인 대화상자를 문구 그대로 띄우고,
[예] 를 눌렀을 때만 "읽기 전용이라 실행하지 않습니다" 를 상태바에 남긴다.
(어떤 흐름이 원본에 있는지 화면에서 그대로 확인할 수 있게 하려는 의도)

이 계열은 원본 `LibraryManager` 를 파이썬 브리지로 그대로 돌리면 되지만,
쓰기 대상이 실제 `index.db` 라서 원본 앱과 동시 사용 시 충돌 위험이 있다.
(원본의 IndexBusy 락은 메모리에만 있어 프로세스 간 보호가 없다.)

## C. 아직 안 이어진 것 — 기술 제약/미착수 (2026-09-02 갱신)

이었음:
- 영역 내보내기 — 원본 `region_export.crop_wav_region` + `render_speed_wav` 를 파형
  서비스에서 그대로 호출한다. 파형 선택 영역/세그먼트 헤더를 끌면 잘린 임시 WAV 가
  만들어져 DAW 로 떨어진다 (배속이 1.0x 가 아니면 배리스피드 렌더까지).
- 바이노럴 채널 배치 — 저장소(파일/폴더 2층)·메뉴 전체·A/B 비교 창 2개·IEM 설치 안내·
  경고 줄(확인 필요 / 저장됨)·버튼 툴팁 8단 정책까지 이었다. 저장은 원본 파일을 건드리지
  않고 `pocinaural_layouts.json` 사본에 한다.
- 메타 인디케이터 — 분석이 돌지 않아도 **미완료 잔량**이 있으면 상태바에 표시한다
  (`미완료  N` / idle 정책이면 `분석 일시정지  N — 앱 사용 중`). 실측 220건에서 확인.
- 검색 제외 목록(제외 관리) — 목록/검색(전체 대상)/상한 2000/요약 문구/`[중복 아님 ·
  미복원]` 태그 재검증/우클릭 4종까지. 복원(쓰기)만 막혀 있다.

남은 것:
- 인덱싱 백엔드 자체 (진행바/일시정지 버튼/인덱싱 집중의 실제 효과)
- 변경 감지 리뷰 창(`ChangeReviewDialog`) — 감지 스캔은 읽기지만 인덱싱 파이프라인이
  없어 미구현. 지금은 `빠른 갱신` 확인창까지만 원본과 같다.
- 바이노럴 실제 처리(IEM 체인) — PoC 오디오 사이드카는 원본 `HybridPlayer` 를 쓰지만
  IEM 설치 여부 판정/런타임 상태 문구(`바이노럴 재생 중 · 5.1` 등)는 아직 표시하지 않는다.

---

## D. 이번에 잡은 실제 결함 3건 (전부 실측 확인)

1. **검색이 항상 빈 결과**
   파이썬 브리지의 stdout 이 콘솔 코드페이지(cp949)라 `©` 같은 문자가 든 경로에서
   `UnicodeEncodeError` 로 죽었다. 브리지 4개 모두 stdout/stdin 을 UTF-8 로 재설정.
   확인: 빈 검색 500행 0.33s, "metal" 500행. 앱 안에서도 500 → 5000행(설정값) 수신.

2. **시작 시 창이 수십 초 얼어붙음**
   DB 명령이 동기 `#[tauri::command]` 라 메인 스레드에서 순차 실행됐다.
   전체 카운트 1.2s + 루트별 카운트(4루트×2질의) + 전체 경로 1.7s + 트리 조립.
   원본은 이 작업을 전부 QThread 워커에서 돌린다 → 같은 정책으로 `spawn_blocking` 이관.
   추가로 초기 목록 질의에 붙어 있던 `ORDER BY indexed_at DESC` 제거(1.75s → 0s,
   원본 `Database.query` 도 전역 정렬을 하지 않는다).

3. **폴더 트리 조립이 O(F²)**
   폴더마다 트리를 재귀 탐색(`find_node_mut`)하고 형제 비교마다 소문자 String 을 새로
   만들어, 폴더 36,876개에서 **2분 이상 + 1.3GB**. 부모→자식 맵으로 한 번에 조립하도록
   교체(원본 `load_folders` 도 cache dict 로 O(F)).
   실측: **11초 / 39MB**.

---

## E. 이 환경의 검증 제약 (다음 세션 참고)

Tauri 창은 화면으로 검증할 수 없다.
- `PrintWindow` 는 WebView2 라 검은 화면만 캡처된다
- `SetForegroundWindow` 는 실패하고, 화면 BitBlt 도 전부 검정
- 창의 콘솔/devtools 에 접근할 수 없다

그래서 **파일 로그 채널**을 만들어 검증했다: `sf_debug(msg)` → `PoC/poc_debug.log`.
`main.tsx` 의 전역 error/unhandledrejection 핸들러가 프런트 예외를 이 파일에 남긴다(개발용).
Rust 쪽만 확인하려면 `setup()` 에서 명령을 직접 호출해 로그를 남기면 된다(이번에 사용).

빌드 순서 주의: 기본 features 로 만든 exe 는 `dist` 를 **바이너리에 굽는다**.
프런트를 고쳤으면 `npm run build` **후 반드시 `cargo build`** 를 다시 해야 반영된다.
`cargo run --no-default-features` 는 vite(1420) 를 그대로 읽어 반복 확인이 빠르다.

---

## F. 2026-09-02 회차에서 새로 이은 것 (요약)

| 항목 | 원본 근거 | 상태 |
|---|---|---|
| 폴더 이름 변경 | `folder_tree._prompt_rename` (2128) + `_on_folder_rename_request` (6422) | 라이브러리 루트 한정, 표시명만 변경, config 저장 |
| 블랙리스트 추가(설명) | `blacklist_panel.add_paths` (192) | 경로마다 순차 프롬프트, 중복 스킵, 추가되면 패널 펼침 |
| 라이브러리 순서 | `folder_tree.startDrag/_move_root` + `reorder_roots` | 메뉴 위/아래 + 드래그(미리보기 + 140ms OutCubic 슬라이드 + 취소 복원) |
| 파형 영역 드래그 아웃 | `_on_drag_region` + `_resolve_export_path` (4118) | crop + 배속 렌더, 세그먼트 헤더 드래그 포함 |
| 파형 quick 프리뷰 | `WaveformQuickRunnable` (640) | 650ms 디바운스 → quick(30MB↑) → 1800ms 후 full |
| 검색 제외 목록 | `_open_suppressed_manager` (3421) | 목록/검색/상한/태그 재검증/우클릭 4종 |
| 바이노럴 A/B 창 2개 | `_AmbisonicFormatDialog` (2908) / `_ChannelOrderDialog` (2976) | 문구·버튼·상태·저장조건 일치 |
| 채널 배치 확인 필요 안내 | `_show_layout_required_popup` (3728) | 일반/AMBEO(A-format) 두 분기 + 선택지 목록 |
| IEM 설치 안내 | `_show_iem_install_popup` (3507) | 버전 1.15.0 + 무료 설치 페이지 열기 |
| 결과 재생 표시(pip) | `set_pip` (720) | loading/playing/ended 상태 기계 |
| 긴 경로(MAX_PATH) | `_draggable_path` (326) / `_reveal_in_explorer_worker` | 드래그용 임시 복사 + 탐색기 부모 폴더 폴백 |
| 중복 실행 방지 | `main.py` QLocalServer (229) | tauri-plugin-single-instance (실측 확인) |
| 임시파일 24h 정리 | `region_export.cleanup_temp_files` (213) | 시작 시 백그라운드 |
| 설정 지속 | `_actual_save_config` (4971) | filters/favorites/tabs/last_selection/volume/speed/history/blacklist 높이/분할 위치 |
| 탭 이름 변경 | `library_tabs._prompt_rename_tab` (1015) | 중복 이름 경고 포함 |
| ⚠ 검색 정리 + 색인 어긋남 안내 | `_check_fts_stale` (5691) / `_prompt_fts_repair` | `index_meta.fts_stale` 읽기 (수리는 쓰기라 막힘) |
