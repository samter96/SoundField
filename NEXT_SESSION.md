# 다음 세션 인계 (2026-09-03 저녁 — 사용자 퇴근, 자율 작업 중)

## 2026-09-08 Codex — 한/영 전환 완료

- 창 제어 버튼 왼쪽에 KR/EN 슬라이드 토글 추가. 선택 언어는 공용 설정의 `lang`으로 저장한다.
- 한글 원문을 번역 키로 쓰는 `src/i18n.ts`와 영문 사전을 추가했다. 직접 연결한 주요 화면과 함께,
  아직 옮기지 않은 JSX 및 Rust/Python 표시 메시지는 생성 사전 759개를 렌더링 경계에서 번역한다.
- 검색 결과 파일명·경로·메타데이터, 폴더 이름, 히스토리 경로 같은 사용자 데이터는 자동 번역에서 제외한다.
- 메인 화면, 환경설정 6개 탭, 라이브러리 상태, 블랙리스트 상세, 바이노럴 메뉴를 실제 브라우저에서
  영문 전환 및 한글 복원 확인했다. `npm run build`와 `src-tauri`의 `cargo check` 통과.
- 희귀 오류/관리 작업 문장은 자동 초벌 번역이므로 실제 기능을 밟다가 어색한 표현이 보이면
  `src/i18n_en.ts`의 사람이 다듬는 사전에 덮어쓰면 된다.
- 생성/점검 도구: `tools/build_i18n_catalog.py`, `tools/check_i18n.py`.
- `run_poc.bat`은 소스가 아니라 `app/soundfield.exe`를 실행하므로, 2026-09-08 19:04에 release를
  다시 빌드하고 `src-tauri/target/release/soundfield.exe`와 동일한 파일로 교체했다.

### 2026-09-08 후속 수정

- 배포 실행 파일은 반드시 `npm run tauri -- build --no-bundle`로 만든다. 일반 `cargo build --release`는
  개발 URL을 바라보는 실행 파일을 만들 수 있어 `localhost ERR_CONNECTION_REFUSED`가 발생했다.
- 창 닫기 회귀 수정: `core:window:allow-destroy` 권한 추가, 설정 저장은 최대 1.2초만 기다린 뒤 종료.
- 히스토리 닫은 뒤 결과표 오른쪽에 남던 포커스 외곽선 제거, 접힌 드로어 경계는 inset shadow로 변경.
- KR 선택 글자만 회색이던 CSS 선택자 오류 수정(`lang-side:nth-of-type(2)`).
- 19:23 정식 Tauri release를 `app/soundfield.exe`에 복사하고 재실행 확인.
- YSG Audio Tools 로고는 시안만 생성했으며 소스/앱에는 적용하지 않았다.

사용자 지시: "질문하지 말고 너의 판단으로 결정해, 판단들은 기록해놔.
한도 초과되면 작업 재개 설정을 걸어놓고 스스로 재개해서 작업해."
→ 이 파일이 재개 지점이다. 아래 표의 상태를 갱신하며 진행한다.

## 작업 목록과 상태

| # | 작업 | 상태 |
|---|---|---|
| 1 | 인덱스 DB 최적화 (removed 부분 인덱스 + VACUUM) | **완료 · 실제 DB 적용** |
| 2 | 트라이그램 경로 색인 → 폴더 부분만 (사용자 승인) | **보류 판단** (아래 근거) |
| 3 | 루트별 인덱싱 시간 계측 | **완료** (아래 수치) |
| 4 | 폴더 트리 안내선 — 계층 깊어지면 끊김 | **코드 완료** · 화면 미검증 |
| 5 | 검색어 강조가 라이브러리 이름까지 물들임 | **코드 완료** · 화면 미검증 |
| 6 | 더블클릭 모드에서 첫 선택도 더블클릭 요구 | **코드 완료** · 화면 미검증 |
| 7 | 세그먼트 헤더 클릭 시 정지 상태에서도 즉시 재생 | **코드 완료** · 화면 미검증 |
| 8 | 앱 시작 시 고아 사이드카 정리 | **완료 · 실측 검증됨** |

⚠ 4~7 은 타입검사·빌드까지 통과했지만 **PC 가 잠금 화면이라 눈으로 확인하지 못했다.**
다음 세션에서 화면으로 확인할 것.

## 1번 적용 결과 (실제 DB)

- 15.55GB → **12.46GB** (3.09GB 회수), VACUUM 9.0분
- `idx_removed_v1` 생성 1.01초, 계획 `USING INDEX idx_removed_v1` 확인
- 정합성: 적용 전 0/0 → 적용 후 **0/0**, `integrity_check` ok
- 옛 백업 **3.42GB 삭제** (참조 코드 없음 확인 후)
- **앱 시작 실측**: ROOTS 2,914→**562ms**, TREE 4,023→**1,333ms**

## 3번 결과 — 인덱싱 시간이 어디로 가는가 (프로덕션 로그 근거)

원본 앱 로그(`%LOCALAPPDATA%\SoundField\logs\app.log`)의 **실제 빠른 갱신 한 사이클**
(2026-08-06 10:37:52 ~ 10:42, 전체 약 4분):

| 단계 | 시간 |
|---|---|
| `D:` 기존 인덱스 로딩 (642,950행) | 11.7초 |
| `D:` 파일 열거 (scandir-rs) | 29.6초 |
| `Y:` 기존 인덱스 로딩 (934,122행) | 2.0초 |
| **`Y:` 파일 열거 (scandir-rs)** | **101.2초** |
| `Y:` FTS 신규 3,475건 삽입 | 18.3초 |
| WAL 체크포인트 ×2 | ~15초 |
| Phase2 메타 분석 3,476개 (70개/초) | ~50초 |

→ **파일 열거 131초 = 전체의 절반 이상.**

### 워커를 바꿔서 얻을 것은 없다 (측정으로 확인)

순수 훑기 시간 (DB 없음):

| 방식 | `D:\Soundlibrary` | `Y:\[Library]` |
|---|---|---|
| 단일 스레드 os.scandir | 74.1초 | 247.4초 |
| os.scandir 32스레드 | 18.0초 | 45.1초 |
| **scandir_rs (원본이 쓰는 것)** | **1.2초** | **7.9초** |

⚠ 이 표는 **측정 순서 때문에 캐시가 점점 데워진 상태**다 (단일→32스레드→rs).
그래서 표만으로 "rs 가 7배 빠르다"고 말할 수 없다. 다만 확실한 것:
scandir_rs 는 캐시가 더우면 **초당 119,007개**인데 프로덕션에서는 **초당 9,265개**였다.
13배 차이 → **워커가 느린 게 아니라 NAS 왕복이 느리다.** 워커 교체는 의미 없다.

### 남은 레버는 "더 빨리 훑기"가 아니라 "훑지 않기"

원본에 `detect_changes` 경로가 이미 있다 (library_manager.py:502~600, scandir-rs +
os.scandir 폴백). 폴더 수정 시각으로 변경된 폴더만 내려가는 방식이 가능한지,
실제로 쓰이는지, 얼마나 걸러내는지 확인이 필요하다.
**인덱싱 엔진의 구조적 변경이라 사용자 확인 없이는 손대지 않았다.**

다음 세션 액션:
1. `detect_changes` 가 빠른 갱신에서 실제로 쓰이는지 코드/로그로 확인
2. 폴더 mtime 기반 가지치기로 실제 몇 %를 건너뛸 수 있는지 측정 (읽기만)
3. 수치를 보이고 진행 여부 확인

### 부수 확인 — 원본 앱의 검색 속도

같은 로그에서 원본 PyQt 앱의 검색은 `[perf-search] elapsed=1227~1935ms` 였다.
PoC 상주 서비스는 더운 상태 38ms / 앱 로그 500~850ms. **PoC 가 더 빠르다.**
사용자가 "원본보다 느리다"고 느낀 시점은 VACUUM 이전(DB 20%가 죽은 공간)일 가능성이 크다.
지금 상태로 다시 체감 확인이 필요하다.

## 2번(트라이그램 경로 → 폴더만) 보류 판단 근거

사용자 승인은 받았지만 **지금 하지 않는 쪽으로 판단**했다. 이유:

1. **이득이 작다** — 절감 추정 약 1.4GB (경로 색인 4.2GB 중 파일명 몫만 제거).
   이미 VACUUM 으로 3.09GB + 백업 3.42GB = 6.5GB 를 회수했고 체감 속도도 3~6배 좋아졌다.
2. **비용이 크다** — 158만 행 트라이그램 **전면 재구성**이 필요하다 (수십 분, 그 동안
   검색 결과가 불완전).
3. **위험이 크다** — 원본 `app/database.py` 의 FTS 삽입 지점 6곳 이상을 바꿔야 하고,
   `file_path` 검색의 **의미가 달라진다**(폴더+파일명을 걸친 부분문자열이 안 잡힘).
   원본 PyQt 앱과 인덱스 DB 를 공유하므로 양쪽 동작에 동시 영향.
4. **인덱싱 속도에는 거의 도움이 안 된다** — 위 3번 측정대로 병목이 파일 열거다.

→ 사용자에게 이 수치를 보이고 **정말 진행할지 다시 확인**하는 것이 맞다고 판단.
   진행하기로 하면 순서: 사본(`C:\sf_perf_test`)에서 재구성 시간·검색 결과 차이를
   먼저 측정 → 보고 → 실제 적용.

7번 사용자 정책 원문: "파형 특정 위치 클릭은 지금과 같이 플레이헤드만 이동(재생 안 함),
세그먼트 헤더 클릭은 그냥 바로 재생." 추정 원인: 1회 재생의 수명이 정해져 있고
세그먼트 헤더 클릭이 그 수명을 갱신하지 않는다.

## 이번 세션에서 끝난 것 (되돌리지 말 것)

- **NAS 드래그 즉사 해결**: `drag` 크레이트를 `src-tauri/vendor/drag` 로 복사해
  `dunce::canonicalize` 제거 + `.unwrap()` 3곳을 오류 반환으로 교체.
  `Cargo.toml` 의 `[patch.crates-io]` 를 지우면 **재발한다.**
  원인: 정규화가 NAS 경로를 verbatim UNC(`\\?\UNC\...`)로 만들고 ILCreateFromPathW 가
  이를 거부 → None → unwrap 패닉이 COM 드래그 콜백을 거슬러 올라가 프로세스 즉사.
  로컬은 dunce 가 접두사를 떼주므로 무사했다 (그래서 NAS만 100% 죽었다).
- **작업 관리자 역할 표시**: `_internal` 공유 사본 4개
  (sf_query/sf_admin/sf_audio/sf_waveform.exe)에 역할별 FileDescription.
  `build_bridge.ps1` 의 `$Roles` + `tools/stamp_role_exe.py`.
- **브리지 경로 해석**: `setup()` 이 아니라 `python_bridge` 안에서 `current_exe` 기준 1회 해석.
  setup 은 창 생성보다 늦게 돌아서 오디오·파형이 개발용 python 으로 떨어지고 있었다.
- **WebView2 3단 대비**: 인스톨러 자동 설치(부트스트래퍼 1.7MB) → 실패 시 한글 안내 →
  실행 시 `ensure_webview2()` 한글 안내. 오프라인 설치본 246MB 는 일부러 미포함.
- 연속 클릭 24회 메모리 평탄 확인 (파형 60→62MB, 오디오 62→65MB).

## DB 최적화 근거 (전부 실측)

- DB 15.55GB / 158만 행. 트라이그램 색인 8.89GB(57%), 빈 페이지 2.45GB.
- 트라이그램 컬럼 비중: **file_path 47.6%**, comments 17.1%, description 17.0%, file_name 15.3%.
- `removed=1` 부분 인덱스: 라이브러리 재추가 복원 **6,452ms → 1ms**, 용량 0KB, 다른 쿼리 영향 없음.
  `CREATE INDEX idx_removed_v1 ON audio_files(file_path COLLATE NOCASE, meta_extracted) WHERE removed = 1`
- VACUUM(사본 검증): 15.55 → 12.46GB, 5.5분, **색인 누락 0 / 고아 0 변화 없음**, integrity ok.
- VACUUM 후 성능(더운 상태 중간값): Phase1 기존목록 로딩 **-56%**, 전체 개수 세기 **-89%**.
- **정합성 안전 근거**: FTS rowid 는 `fts_rowid(file_id)` 해시에서 계산하고
  `audio_files.rowid` 를 어디에도 저장하지 않는다. hidden/dup_orphan/블랙리스트/억제목록
  전부 **경로(TEXT) 키**. FTS 내부 표 전부 `INTEGER PRIMARY KEY` 또는 `WITHOUT ROWID`.
- C: = SSD, D: = HDD (실측). **성능 측정은 반드시 C: 에서** 해야 유효하다.

## 남겨둔 안전망 / 정리 예정

- `C:\sf_perf_test\index_copy.db` = VACUUM 완료 사본. **앱 정상 동작 확인 후 삭제.**
- 사용자 승인 받은 삭제 대상(적용 후 정리):
  `%LOCALAPPDATA%\SoundField\index.db.manual_reset_20260518_174407` (3.34GB) 및
  `*.restore_tmp_20260518_174200`, `*-shm.*`, `*-wal.*` 잔여물. 참조 코드 없음 확인.

## 측정 도구 (재사용)

`%TEMP%\claude\C--Users-samter96-Desktop-Sound-Search-program\<세션>\scratchpad\`:
`measure_writes.py`(SQL 문 계측), `plan_real.py`(쿼리 계획), `db_size.py`,
`col_volume.py`, `bench_index.py`, `bench_vacuum.py`, `ab_vacuum.py`, `apply_real.py`.

## 빌드 절차 (인스톨러는 매번 만들지 않는다 — 검증에 필요할 때만)

```
powershell -ExecutionPolicy Bypass -File .\build_bridge.ps1   # 파이썬 고쳤을 때만
npx tauri build --no-bundle                                    # cargo build 는 devUrl 이 박힘
copy src-tauri\target\release\soundfield.exe app\soundfield.exe
```

---

# 이전 인계 (2026-09-02) — 아래는 그때 기록, 참고용

# NEXT SESSION — Claude 인수인계 (2026-09-02)

## 이번 작업의 목표와 절대 조건

- 원본 PyQt SoundField의 **실제 기능·동작·세부 정책**을 Tauri PoC로 전부 마이그레이션한다.
- 기준 원본: `C:\Users\samter96\Desktop\Sound_Search_program`
- 작업 대상: `C:\Users\samter96\Desktop\SoundField-Tauri-PoC`
- 사용자가 이미 PoC의 디자인을 완성했으므로 **CSS, 화면 구조, 컴포넌트 배치 등 디자인은 바꾸지 않는다.** 기존 UI 안에서 동작만 연결한다.
- 원본의 회사 라이브러리 `Y:\[Library]`에는 절대 쓰지 않는다. 인덱스와 사용자 설정은 로컬 경로만 쓴다.
- PoC 폴더는 현재 Git 저장소가 아니다. `git status`로 변경 범위를 복원할 수 없으므로 아래 파일·검증 기록을 기준으로 이어간다.

## 먼저 읽을 원본 문서

원본 폴더에서 아래 순서로 읽고, 최근 다른 AI 변경과 충돌 여부를 확인한다.

1. `C:\Users\samter96\Desktop\Sound_Search_program\NEXT_SESSION.md`
2. `C:\Users\samter96\Desktop\Sound_Search_program\COLLAB_LOG.md`
3. `C:\Users\samter96\Desktop\Sound_Search_program\RULES.md`
4. `C:\Users\samter96\Desktop\Sound_Search_program\BINAURAL_CHANNEL_ORDER_SPEC.md`

## 파악된 핵심 문제

기존 PoC는 UI와 일부 상수/문구만 옮겨져 있었고, 값이 실제로 흐르는 경로가 많이 빠져 있었다.

- 오디오 재생 위치, 파일 전환, 바이노럴 레이아웃이 실제 엔진과 완전히 연결되지 않았다.
- 빠르게 다른 파일을 재생하면 새 파일의 플레이바가 이전 파일 위치에 멈췄다. 로그상 새 경로와 함께 이전 파일의 position이 전송되는 레이스였다.
- 파형 추출 요청이 직렬 처리되어 큰/NAS 파일 추출 중 다른 파일을 선택하면 새 파형이 오래 대기했다.
- 인덱싱, 재시도, 삭제, 블랙리스트, 중복 숨김, 히스토리 등 여러 기능이 가짜 데이터·로컬 UI 갱신·PoC 복사본 수준에 머물렀다.
- 원본의 2단계 인덱싱, 유휴 시 메타 분석, 재생 중 양보, FTS 복구, 파서 버전 업그레이드 같은 정책이 빠져 있었다.
- 설치본에서 Python 의존 없이 돌아갈 브리지 패키징 경로가 없었다.
- 문서와 주석 다수가 아직 `read-only`, `blocked`, `PoC copy`로 되어 있어 현재 구현과 맞지 않는다.

## 수정한 사항

### 1. 실제 바이노럴/오디오 런타임 연결

주요 파일:

- `src-tauri/python/sf_audio_service.py`
- `src-tauri/src/lib.rs`
- `src/backend.ts`
- `src/components/Player.tsx`
- `src/App.tsx`

적용 내용:

- 원본 `HybridPlayer`의 IEM 설치 여부, 상태, 레이아웃, 재생, 위치, 미디어 이벤트를 PoC로 전달한다.
- IEM 플러그인이 없을 때 기존 팝업을 실제 상태로 띄운다.
- AmbiX/FuMa A/B 버튼은 실제 엔진 레이아웃을 바꾸고 **처음부터 재생**한다. 팝업을 닫으면 이전 레이아웃을 복원한다.
- 레이아웃 저장/초기화가 실행 중 엔진에도 즉시 반영된다.
- `App.tsx`에서 load 완료 전에 play가 앞서 나가지 않도록 순서를 보장한다.
- PIP/재생 상태를 엔진 이벤트와 실제 오류로 갱신한다.
- 파일 전환 시 서비스가 stop → source 교체 → position 0 순서로 처리하고, 새 파일에서 0을 관측하기 전 50ms 초과 잔류 position을 억제한다.

직접 검증:

- 실제 WAV A를 재생하다 B를 로드한 서비스 테스트에서 B의 첫 위치가 `0`으로 보고되고 이후 `23/46...ms`로 증가했다.
- 즉, 기존처럼 새 파일이 이전 파일 위치에서 대기하는 서비스 레벨 레이스는 수정됐다.
- **Tauri UI에서 연속 클릭하는 통합 테스트는 아직 필요하다.**

### 2. 파형 요청 취소

- Rust에 `sf_waveform_cancel`을 추가했다.
- 파일 선택이 바뀌면 `Player` cleanup에서 기존 waveform 서비스 프로세스를 종료한다.
- 다음 요청은 서비스를 다시 시작하므로, 오래 걸리는 이전 파형이 새 요청을 막지 않는다.
- 브리지의 시작/종료는 검증했지만, 큰 NAS 파일 → 즉시 다른 파일 선택 실사용 테스트는 남았다.

### 3. 실제 검색·히스토리·설정 데이터

- 시작 시 가짜 행/라이브러리/블랙리스트/트리를 제거했다.
- 실제 파형 실패 시 합성 파형 대신 `파형 추출 실패`를 표시한다.
- 오래된 검색 프로세스를 PID로 취소한다.
- 결과 목록의 위/아래 방향키 및 Space 재생을 실제 동작과 연결했다.
- 히스토리는 원본과 같은 `%LOCALAPPDATA%\SoundField\history.json`에 원자적으로 저장하며 최대 100개다.
- 현재 검색 결과에 없는 히스토리 파일도 DB에서 실제 행을 불러와 재생한다.
- 원본 저장 경로를 사용한다.
  - DB: `%LOCALAPPDATA%\SoundField\index.db`
  - 설정: `~/.soundfield_config.json`
  - 바이노럴 레이아웃: `%LOCALAPPDATA%\SoundField\binaural_layouts.json`

### 4. 실제 관리/인덱싱 브리지

신규 파일: `src-tauri/python/sf_admin.py`

지원 작업:

`add_index`, `index`, `remove_roots`, `repair_fts`, `detect`, `parser_upgrade`, `ensure_term_index`, `background_meta`, `retry_paths`, `retry_scope`, `delete_paths`, `delete_scope`, `hide_paths`, `unhide_paths`, `unhide_all`, `unhide_ids`, `mark_dup_orphans`

정책:

- 원본 `LibraryManager`와 `Database`를 직접 사용한다.
- 일반 인덱싱은 원본처럼 **Phase 1 파일 목록을 먼저 완료해 즉시 검색 가능하게 한 뒤, Phase 2 메타 분석을 백그라운드로 넘긴다.**
- line 기반 progress 이벤트를 Rust `index-event`로 전달한다.
- 별도 Python 프로세스가 동시에 DB에 쓰지 않도록 Rust 전역 `AdminState`로 직렬화한다.
- 헤더 취소 버튼은 실제 foreground/background 작업을 취소한다.
- 상태바의 기존 텍스트/로더만 재사용했다. 새 UI나 디자인은 추가하지 않았다.

### 5. 원본의 백그라운드 메타 정책

`src/App.tsx`에 다음을 이식했다.

- 시작 시 남은 메타 분석 자동 재개.
- 입력으로 인정: mouse down, key down, wheel. 단순 mouse move/hover는 무시.
- 실제 입력이 오면 백그라운드 메타 작업을 즉시 취소하고, 30초 유휴 후 재개.
- 일반 모드에서는 재생 요청 시 백그라운드 메타만 취소하고, 재생 종료 1.5초 뒤 재개.
- 인덱싱 집중 모드에서는 재생 중에도 백그라운드 작업 지속.
- 사용자가 직접 시작한 foreground 라이브러리 스캔/재시도는 재생 때문에 취소하지 않는다.

주의: 원본은 cooperative cancel인데 현재 admin은 one-shot process라 `taskkill`을 사용한다. WAL/재실행으로 이어가기는 가능하지만, 테스트 DB에서 취소/재개와 `active_phase2_run_id` 정합성을 반드시 검증한다.

### 6. 시작 시 유지보수 정책

- `parser_upgrade`를 자동 백그라운드 메타보다 먼저 실행한다.
- FTS stale이면 원본처럼 시작 후 자동 복구한다.
- exact search term index를 시작 후 한 번 보장하며 admin busy이면 재시도한다.
- 파서 업그레이드 시 동일 버전 실패 초기화는 한 번만 수행하고, v2 첫 전환에서 과거 removed failure를 복원하는 원본 정책을 옮겼다.

### 7. 빠른 업데이트와 전체 업데이트

- 빠른 업데이트는 먼저 dry-run 감지 후 추가/수정/삭제 개수와 최대 10개 예시 경로를 보여주고 확인 뒤 실행한다.
- 전체 업데이트는 루트별 반복이 아니라 원본처럼 전역 `scan_path=None` 한 번으로 실행한다.

### 8. 중복 파일 및 orphan 복원 정책

- 결과의 `orphan_dup_ids`를 보존하고 중첩 결과에서 재귀 수집한다.
- 원본 문구 `중복 아님 — 복원 확인`을 사용한다.
- 기본 포커스는 `아니요`다.
- 예: `unhide_by_ids`, 아니요/바깥 클릭: `mark_dup_orphans`.
- 팝업이 겹쳐 앞 팝업이 덮어쓰지 않도록 Phase 1 완료 안내 뒤 orphan 확인을 연다.

### 9. 삭제·블랙리스트·purge

- 블랙리스트 추가/해제, 중복 숨김/복원, 실패 파일 재시도/삭제가 실제 DB를 쓴다.
- 상세 팝업의 블랙리스트 제거도 로컬 화면만 바꾸지 않고 실제 제거 후 부모를 갱신한다.
- purge는 DB/FTS/blacklist뿐 아니라 해당 루트의 peak cache와 history도 정리한다.
- App의 즐겨찾기, 사용자 탭 항목/제외 경로, 마지막 선택을 정리한다.
- 현재 메모리의 Sidebar 상태도 `pruneSignal`로 즉시 정리한다.
- 제거 대상 아래 파일이 재생 중이면 정지하고 선택을 비운다.
- purge 경고 기본 포커스는 `아니요`다.

### 10. 기타 기능 수정

- 히스토리 패널 너비 저장이 drag 시작 시점의 오래된 값을 쓰던 문제를 live ref로 수정했다.
- 시작 때 DOM을 훑고 오디오 위치를 구독하던 2.5초 진단 코드를 제거했다.
- npm/Tauri/Cargo 버전을 `1.3.0`, 앱 이름을 `soundfield`, identifier를 옛 값으로 맞췄다.
- 원본 경로 하드코딩 대신 형제 폴더의 원본을 찾도록 bridge build script를 수정했다.

## 휴대용 Python 브리지 상태

관련 파일:

- `src-tauri/python/sf_bridge.py`
- `build_bridge.ps1`
- `sf_bridge.spec`
- `src-tauri/bridge-dist/sf_bridge/`
- `src-tauri/tauri.conf.json`

현재 상태:

- PyInstaller onedir 런타임을 최신 코드로 재빌드했다.
- 위치: `C:\Users\samter96\Desktop\SoundField-Tauri-PoC\src-tauri\bridge-dist\sf_bridge`
- 약 297개 파일, 203MB.
- Qt PATH/DLL을 격리해 Perforce Qt6Core 충돌을 막았다.
- 패키지 브리지로 아래 안전 검증을 통과했다.
  - admin `hide_paths []`
  - 실제 DB query limit 1
  - audio 시작/availability/position/정상 quit
  - waveform 시작/quit

중요: `npm run tauri build`로 실제 설치본 resource 배치를 아직 확인하지 않았다. 설정은 `bridge-dist/sf_bridge/**/*` → `bridge/`인데, 실행 코드가 기대하는 `resource_dir/bridge/sf_bridge.exe`와 실제 결과가 `resource_dir/bridge/sf_bridge/sf_bridge.exe`로 어긋날 수 있다. **설치본 빌드 후 가장 먼저 확인할 것.**

## 남은 작업 — 우선순위 순

### P0. 바로 해야 할 빌드/경로 수정

1. `src-tauri/src/lib.rs`의 `sf_debug`가 `env!("CARGO_MANIFEST_DIR")/../poc_debug.log`에 기록한다. 설치 PC에서 잘못된 개발 경로이므로 `%LOCALAPPDATA%\SoundField\tauri_debug.log`로 바꾸고 부모 폴더를 만든다.
2. `src-tauri/python/sf_waveform_service.py` 약 213행 docstring의 `\S` invalid escape 경고를 raw string 또는 `\\`로 정리한다.
3. Python 파일을 바꾸면 `build_bridge.ps1`로 브리지를 다시 빌드한다.
4. 최종 정적 검증:
   - `npm run build`
   - `cargo check` (`src-tauri`에서)
5. 전체 설치본 빌드: `npm run tauri build`.
6. 설치본 resource 내부의 `sf_bridge.exe` 실제 위치를 확인하고, Rust의 packaged bridge 탐색과 맞춘다. 필요하면 후보 경로 둘 다 검사하도록 한다.

### P0. 실사용 통합 테스트

테스트는 `Y:\`를 수정하지 말고, 쓰기 가능한 TEMP 테스트 라이브러리와 운영 DB의 복사본을 사용한다.

1. 빠르게 여러 파일을 번갈아 선택해 새 파일 플레이바가 항상 0에서 시작하는지.
2. 큰 NAS 파일 파형 로딩 중 즉시 다른 파일을 선택해 새 파형이 대기하지 않는지.
3. 바이노럴 on/off, AmbiX/FuMa A/B, 레이아웃 저장/자동 복귀, seek 시 클릭/틱 여부.
4. IEM 미설치 팝업. 실제 플러그인을 삭제하지 말고 테스트용 debug override를 임시로 쓰되 배포 코드에는 남기지 않는다.
5. TEMP 루트 add → Phase 1 완료 → Phase 2 백그라운드 진행.
6. 입력 시 즉시 취소 + 30초 후 재개.
7. 일반 모드 재생 시 취소 + 종료 1.5초 후 재개.
8. 집중 모드에서는 재생 중 계속 진행.
9. 취소 후 `active_phase2_run_id`, WAL, FTS 정합성과 다음 실행 재개.
10. TEMP 루트 remove/purge, peak/history/favorite/tab/selection 정리.
11. 테스트 DB에서만 FTS stale 자동 복구, parser upgrade, exact term index 검증.
12. `repair_fts`의 별도 2인자 progress callback adapter가 여러 `index-event`를 정상 전달하는지.

### P1. 남은 정책 차이/위험

- 원본의 명시적 메타 분석 일시정지 + 5분 자동 재개 정책이 PoC에 명확히 없다. 현재 헤더 취소는 작업을 정지하지만 5분 수동-pause 상태를 따로 저장하지 않는다. **디자인 변경 없이 기존 컨트롤에 매핑할 수 있는지 먼저 확인**한다.
- Settings의 중복 숨김/복원 일부는 빠른 작업이라 raw `runAdmin`을 쓴다. 백그라운드 메타와 겹치면 전역 lock 때문에 `이미 인덱싱 중입니다`가 나올 수 있다. Settings까지 managed admin 경로로 통일할지 검토한다.
- purge가 동적으로 `app.ui.player_widget`을 import해 peak cache를 지운다. PyInstaller에 포함됐는지 설치본에서 확인한다.
- `audio playback-state` 이벤트에는 path가 없고 position 이벤트에는 있다. 빠른 전환에서 PIP 상태가 드물게 섞이면 state 이벤트에도 path를 넣고 App에서 현재 파일과 대조한다.
- `runIndexAdmin`과 background promise에서 refresh가 예외를 던지면 busy ref가 남을 수 있다. `try/finally`로 정리하는 편이 안전하다.
- startup `parser_upgrade`가 busy/failure여도 현재 maintenance 완료로 넘어갈 가능성이 있다. 실패 시 재시도 정책을 보강한다.
- 기존 디자인을 유지하기 위해 인덱싱 퍼센트 전용 UI는 추가하지 않고 기존 상태 텍스트/로더에만 연결했다. 기능상 충분한지 사용자가 확인해야 한다.

### P2. 죽은 코드와 문서 정리

기능 검증 뒤에만 수행한다.

- `src/components/Modals.tsx`: unused `BLOCKED_MSG`, 도달 불가능한 `id === "dupes"` 가짜 뷰, read-only/blocked라는 오래된 주석.
- `src/data.ts`: startup에서 더 이상 쓰지 않는 fake fixture. 타입/formatter import 여부 확인 후 제거.
- `src/App.tsx`: 실제로 set되지 않는 `addedLibs` 레거시 상태.
- `docs/WIRING_STATUS.md`, `docs/PORTING_MATRIX.md`, `docs/POLICY_CHECKLIST.md`: 현재 실제 쓰기/연결 상태로 갱신.
- `poc_dir()`는 현재 `drag.png`만 사용한다. 기능 문제는 아니며 이름 변경은 후순위다.

## 마지막으로 확인할 코드상 주의점

- `src/components/Modals.tsx`는 `onRunAdmin`을 props로 직접 구조 분해하도록 수정했다. 과거 잘못된 `arguments[0]` 접근은 제거했다. 최종 `npm run build`로 다시 확인한다.
- `runDialogAdmin`이 orphan 팝업을 열며, Modals 자체 orphan 처리는 `!onRunAdmin`일 때만 실행해 중복 팝업을 막는다.
- `collectOrphanIds`는 중첩 결과 전체를 재귀 탐색하고 Set으로 중복 제거한다.
- Phase 1 완료 팝업은 `index/add_index`에 표시된다. 우클릭 폴더 재스캔에서도 원본과 의도한 정책인지 수동 확인한다.
- 변경 감지 확인은 디자인 보존을 위해 기존 confirm modal에 텍스트로 넣었다.

## 다음 세션의 권장 첫 순서

1. 이 문서와 원본 협업 문서를 읽는다.
2. `sf_debug` 저장 경로와 waveform docstring 경고를 수정한다.
3. `npm run build`, `cargo check`를 다시 통과시킨다.
4. 브리지를 재빌드하고 `npm run tauri build`한다.
5. 설치본 resource 경로를 확인한다.
6. TEMP 테스트 라이브러리로 rapid switch → waveform cancel → indexing cancel/resume 순서로 검증한다.
7. 검증 결과를 이 문서와 원본 `COLLAB_LOG.md`에 기록한다.

## 완료로 오해하면 안 되는 것

- 기능 연결 코드는 광범위하게 이식됐지만 **전체 마이그레이션 완료 판정은 아직 아니다.**
- 서비스 단위의 새 파일 position 0과 패키지 브리지 실행은 검증했다.
- 실제 Tauri 설치본, 빠른 연속 재생, 대용량 NAS 파형 취소, 바이노럴 seek 틱, 인덱싱 취소/재개, purge는 최종 통합 검증이 남아 있다.
- 디자인은 이 세션에서 의도적으로 변경하지 않았다.
