# SoundField (YSG Audio Labs)

사운드 라이브러리 검색·오디션 도구. Tauri 2 + React 19 화면 + Rust 코어 + 파이썬 브리지.

## ⚠ 먼저 읽을 것

- **이 저장소는 공개다** (`samter96/SoundField`). 공개해도 되는 문구·이미지·경로만 넣는다 —
  더미 데이터의 경로, 앱 식별자, 작업표시줄 기본값, 안내 문구까지 전부.
- 화면(`src\*.tsx`)은 exe 안에 구워져 있어서 소스만 고치면 `run_dev` 에는 안 나온다 —
  `npx tauri build --no-bundle` 을 돌려야 고친 것이 실제로 돈다.
- **데이터 경로** — 인덱스·설정·히스토리는 `%LOCALAPPDATA%\SoundField`.

`Desktop\Sound_Search_program` 은 **옛 자료 보관용**이다 (문서·소개 페이지·복구 자료).
작업 폴더가 아니다 — 거기 있는 소스를 고치지 말 것.

## 폴더 구조

```
src\            React 화면 (TypeScript)
src-tauri\      Rust 코어 + 창·프로세스 관리
  python\       브리지 진입점 (sf_query / sf_admin / sf_audio_service / sf_waveform_service)
py\app\         **파이썬 코어** — 검색·인덱싱·재생·파형·바이노럴 (원래 PyQt 앱에서 온 것)
py\sidecar\     바이노럴 모니터 실행 파일
public\         화면 자산 (로고·글꼴)
tools\          빌드·자산 생성 스크립트
app\            **설치 포장용 스테이징** (soundfield.exe + bridge\) — py\ 와 다른 것
```

⚠ `py\` 와 `app\` 은 이름이 비슷하지만 역할이 다르다. 파이썬 소스는 `py\app\` 이다.

## 빌드

```
npm run installer        설치본까지 (프런트 → 실행 파일 → ISCC)
.\build_bridge.ps1       파이썬 브리지를 다시 만들 때만
```

- `ISCC.exe` 를 직접 돌리지 말 것. 프런트 빌드·작업표시줄 식별자·제작사가 어긋나도
  **오류 없이** 포장된다. `build_release.ps1` 이 단계마다 검사한다.
- 릴리스는 반드시 `tauri build` 로. `cargo build --release` 는 개발 URL(localhost)을
  바라보는 실행 파일을 만들어 빈 창이 뜬다.
- 파이썬 코어 경로를 잡는 곳은 **여섯 군데뿐**이다 — `build_bridge.ps1`(`$PyRoot`),
  `sf_bridge.spec`(pathex/binaries/datas), `src-tauri\python\sf_*.py` 넷(`PY_ROOT`).
  진단용 환경변수는 `SOUNDFIELD_PY_ROOT`.
- ⚠ `sf_bridge.spec` 은 **실제 빌드에 쓰이지 않는다** (명령줄 빌드다). 여기에 설정을
  적어도 반영되지 않는다 — 2026-09-15 에 유사검색 격리를 이 파일에 적어 둔 탓에
  격리가 걸린 적이 없었고, 그대로 배포까지 나갔다.

## 유사 사운드 검색 — 배포 격리

개발용으로만 둔다. **설치본에는 기능도, DB 흔적도 들어가지 않는다** (사용자 결정 2026-09-14).

- 스키마는 `py/app/similarity_schema.py` 에만 있고, `database.py` 가 `try/except` 로
  불러 `SIMILARITY_ENABLED` 를 정한다. 못 부르면 관련 SQL 을 전부 건너뛴다.
- 제외는 `build_bridge.ps1` 의 `--exclude-module` 네 줄이 하고, 같은 스크립트가
  번들 목록(`PYZ-00.toc`)에 `app.similarity*` 가 없는지 확인한다.
- ⚠ 파일 **이름**만 보는 검사로는 못 잡는다. 모듈은 exe 안 PYZ 로 압축돼 들어간다.

## 비자명한 결정·함정 (깨트리지 말 것)

- **모든 UI 텍스트 한글.** 영어 문구를 새로 넣지 말 것 (영문은 `src/i18n_en.ts` 사전).
- **라이브러리 쓰기 금지** — 대상 라이브러리에는 쓰기 권한이 없다고 가정한다.
- **창을 닫으면 항상 묻는다** (사용자 결정 2026-09-16). X·Alt+F4·작업표시줄 닫기는
  모두 `src/App.tsx` 의 `onCloseRequested` 한 자리로 모여 확인창만 띄우고, 실제
  종료는 `doExit` 가 한다. **최소화도 트레이도 두지 않는다** — X 는 종료 아니면 취소다.
  ⚠ 새 종료 경로를 만들면 `doExit` 를 직접 부르지 말고 `setExitAsk(true)` 를 쓸 것.
  한쪽만 물으면 정책이 조용히 갈라진다.
  조사해 보면 동종 툴(BaseHead·Soundly·Soundminer)은 **어느 쪽도 종료를 묻지 않는다**
  (Soundminer 가 Mac 에서 안 꺼지는 건 macOS 관례이지 그쪽 정책이 아니다).
  "원래 안 묻는 게 맞다" 며 되돌리지 말 것 — 알고서 다르게 간 것이다.
- **시작 화면은 두 벌이다** — 네이티브(`splash.html` + `vite.config.ts`)와
  React(`src/components/Splash.tsx`). 한쪽만 고치면 시작할 때 로고가 떴다가 바뀐다.
  (지금 화면에 뜨는 건 네이티브 쪽뿐이지만 둘 다 살아 있다.)
- **브랜드 자산은 손으로 만들지 말고 생성기를 돌린다.**
  `python tools/build_ysg_emblem.py` → `public/ysg-emblem-2026.png` (HUB 버튼)
  `python tools/build_ysg_lockup.py` → `public/ysg-lockup-2026.png` (시작 화면 락업)
  `python tools/build_ysg_splash_bg.py` → `public/ysg-splash-bg.jpg` (시작 화면 배경)
  ⚠ 완성된 락업 파일을 줄여 쓰지 말 것 — 이미 축소된 합성본이라 엠블럼만 뭉갠다.
  ⚠ 밝은 배경용 원본(`리뉴얼로고_헤더용.png`)에서 알파를 뽑지 말 것 — 속이 뚫린다.
- **폴더 이름을 바꾸면 `src-tauri\target` 을 지우고 다시 빌드한다.** 캐시에 옛 절대
  경로가 박혀 있어 `failed to read plugin permissions ... (os error 3)` 로 멈춘다.
- **파워셸 스크립트를 셸 경유 파이썬으로 고치지 말 것.** 백슬래시가 한 겹 먹혀
  `"dist\assets"` 의 `\a` 가 제어문자로 바뀐다. 구문 검사는 **통과**하고 런타임에
  경로만 조용히 틀린다. 스크립트 파일로 저장해서 실행할 것.
- `build_release.ps1` 은 **UTF-8 BOM** 으로 저장한다. BOM 이 없으면 PowerShell 5.1 이
  ANSI 로 읽어 한글이 깨지고 ParserError 로 죽는다.

## 검색 필드

`Database.SEARCHABLE_FIELDS`: file_name, file_path, title, artist, album, genre,
comments, description, keywords, category, sub_category, source.
새 필드를 더하면 `_ensure_indices()` 가 FTS5 스키마를 자동으로 다시 만든다.

⚠ UCS 동의어 사전(`py/app/data/ucs_thesaurus.json`)이 번들에서 빠지면 **아무 오류 없이
검색 결과만 크게 줄어든다.** `build_bridge.ps1` 이 매번 확인한다 — 그 검사를 지우지 말 것.

## 데이터 경로

- 인덱스 DB: `%LOCALAPPDATA%\SoundField\index.db`
- 파형 피크 캐시: `%LOCALAPPDATA%\SoundField\peaks_cache\`
- 재생 히스토리: `%LOCALAPPDATA%\SoundField\history.json`
- 컬럼 레이아웃: `~/.soundfield_config.json`
- 영역 crop 임시: `%TEMP%\SoundField_regions\` (24h 자동 정리)
