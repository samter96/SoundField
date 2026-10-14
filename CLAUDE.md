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


## 검색 엔진 (2026-10-07 개편 — 사용자 확정)

- **동의어는 예전 엔진과 같은 규칙** — UCS 사전에서 단어마다 최대 3개(4글자 이상), 글자 일부,
  '전체' 필드만, 정확한 검색·따옴표·빼기엔 없음 (`_query_synonyms`, 원래 단어와 trigram OR).
  2026-10-07 같은 날 '같은 뜻' 새 사전(`sfx_synonyms.json`) + UCS 전부(2단계) + UCS 분류 약어
  (`ucs_codes.json`) + 검색 줄 아래 칩을 써 봤으나 **결과가 덜 직관적이라 되돌렸다** (사용자 결정).
  이유: 좁은 검색도 늘 5,000개로 채워지고, 숨은 말로 결과가 들어와 이유를 알 수 없었다.
  그 코드는 **보관만 하고 설치본에서 뺀다**: 엔진 연결부 `py/app/synonym_lab.py`, 사전·약어
  `app/synonyms.py`·`app/ucs_codes.py`·`data/sfx_synonyms.json`·`data/ucs_codes.json`, 칩 화면
  `parked/synonym_chips/README.md`(src 밖이라 빌드 안 됨). `build_bridge.ps1` 이 세 모듈을
  `--exclude-module` 하고 PYZ 목록·데이터 폴더에 없는지 검사한다 — 그 검사를 지우지 말 것.
  ⚠ 공개 사전(WordNet)으로 자동 생성도 시험했다 — 엄격하면 wind–gust·slam–bang 이 빠지고,
  느슨하면 wind–fart 가 들어온다. 사운드용 동의어는 사람이 골라야 한다.
- 문법은 `app/search_query.py`: 띄어쓰기 AND, `OR` (대문자만), `NOT`/`-` 빼기, 따옴표, 괄호.
  **쉼표는 띄어쓰기와 같다** — 2026-10-08 쉼표 OR 폐지 (사용자 결정 "OR 는 OR 로만"). 쉼표 든
  파일명('1009_59-3 DOOR, WOOD OPEN, SQUEAK')을 붙여 넣으면 엉뚱한 5,000개가 나왔다.
  '경로' 필드만 문법 없이 띄어쓰기 AND. 우선순위 NOT > AND > OR (필터 줄 조합도 같다).
  ⚠ 빼기는 **단어에 붙은** `-`(`-slam`, `-(...)`)만이다. 띄어 쓴 ` - ` 는 버린다 — 파일명에 흔해서
  ('Toyed - Weapon') 빼기로 읽으면 붙여 넣은 파일명 검색이 0건이 된다 (2026-10-08 신고).
  글자·숫자가 없는 토큰(`-`, `&`, `/`)은 검색어로 쓰지 않는다.
- 단어색인 `file_name_norm` 칸에는 **쪼갠 파일명**도 들어간다 (`GlassSmash_05` → Glass Smash 05).
  규칙을 바꾸면 `Database.TERM_NAME_MODE` 를 올릴 것 — 시작 정비가 표식을 보고 단어색인을
  새로 만든다 (158만 행 실측 5.4분, 그동안 정확한 검색 결과 일부 빠짐).
  표식은 만들기 **시작할 때 지우고 끝날 때 남긴다** — 도중에 꺼져도 다음 실행이 다시 만든다.
- ⚠ 새 엔진의 검색 SQL 은 **CROSS JOIN 으로 처리 순서를 고정**한다 (검색 결과 → 색인 → audio_files).
  그냥 JOIN 이면 채널·샘플레이트·길이처럼 색인 있는 필터가 걸릴 때 SQLite 가 audio_files 를
  바깥으로 골라 행마다 결과 목록을 다시 훑었다 — 2.1.4 설치본 'rain AND storm' + STEREO 가
  2분 넘게 '검색 중...' (2026-10-07 신고). 빼기만 있는 검색('-slam')도 행 훑기 길로 보낸다.
  고친 뒤 검색어 11종 × 필터 9종 × 넓게/정확 = 198회 모두 1.9초 이내.
- ⚠ 짧은 단어 LIKE 는 `IFNULL(a.칸,'')` 로 건다. 빈 칸(NULL)이 섞이면 OR 묶음이 NULL 이 되고
  빼기(NOT)로 뒤집으면 모든 행이 탈락했다 ('ui -hd'·'laser -hd' 0건, 2026-10-08 검수).
- 짧은 단어로만 된 **묶음**('(ui OR fx) laser')도 다른 단어로 좁힌 행 안에서 거른다(_like_pred).
- 1~2글자 단어만으로 된 검색('ui', 'a')은 집합으로 모으면 멈춘다(실측 60초+). `_needs_scan`
  이 행을 훑다 limit 에서 멈추는 길로 보낸다 — 지우지 말 것.
- 정확한 검색은 **순위를 단어색인 안에서 먼저 매기고** 위쪽 후보(limit×3 → ×30)의 행만 읽는다.
  예전처럼 audio_files 를 붙인 뒤 정렬하면 거의 모든 파일에 든 말('wav' 153만 건)에서 15초+.
  후보로 못 채우면(드문 필터: 5.1·7채널 이상) 필터 통과 id 를 모아 전체 순위에서 골라낸다
  ('wav'+5.1 15.5→4.0초). 결과·순서는 어느 단계든 예전과 같다.
  ⚠ 단어색인에 `rowid IN (통과 목록)` 을 거는 길은 442초 — 쓰지 말 것.
  ⚠ 행은 `a.id IN (...)` 로 읽지 말 것 — 채널 필터가 있으면 idx_channels 로 빠져 묶음마다 1초.
    `json_each(?) j CROSS JOIN audio_files a ON a.id = j.value` 로 id 키를 강제한다.
  ⚠ 후보를 CTE 안에서 `SELECT file_id, bm25(...) ORDER BY 2` 로 만들면 1.3초가 3.1초가 된다(실측).
- 검색 하나가 30초를 넘으면 `sf_query.py` 가 끊고 `{"timeout":30}` 으로 답한다 → 상태줄 안내.
- 화면은 상한(환경설정, 최대 2만)보다 **한 줄 더** 요청해 그 한 줄이 오면 "결과 N개 — 더 있음".
- 넓게 찾기의 '이름·제목 등에 원래 단어가 있는 행 먼저' 단계(direct pass)는 trigram 색인 칸 지정
  MATCH(`_direct_fts`)로 먼저 좁힌 뒤 예전 LIKE 를 그대로 다시 건다 — 폴더 이름에 흔한 말
  ('library') 12.6→2.7초, 결과·순서 예전과 같음(25검색어×필터4 = 100건). ⚠ INTERSECT 로 좁히면
  순서가 바뀌어 limit 경계가 달라진다 — `r IN (...)` 로 hit 순서를 지킨다.
  ⚠ 이 단계는 색인이 audio_files 와 맞아야 예전과 같다 → 아래 색인 동기.
- **색인 메타 동기** (2026-10-08): 2단계가 색인 쓰기를 끝에 한 번만 해서, 도중에 끊기면 새 메타가
  색인에 영영 안 들어갔다 (실측 넓게 찾기 색인 19.2만 행, 보이는 파일 6.8만의 설명·코멘트·아티스트
  누락 — 설명 속 단어로 못 찾음). 이제 2만 개마다 반영(`LibraryManager.PHASE2_FTS_FLUSH`) +
  반영 안 된 구간 시작 시각을 표식 `fts_sync_since` 에 남기고, 시작 정비(sf_admin
  `ensure_term_index`)가 표식이 있으면 그 뒤 행을 다시 넣는다(`recover_fts_sync`). 1회 전수 대조
  `sync_fts_meta`(표식 `fts_meta_sync`)가 옛 누락을 고친다 — 실측 대조 21초 + 19.2만 행 107초, +0.52GB.
  취소 시엔 마지막 반영을 건너뛴다(취소 즉시 응답) — 표식이 남아 다음 시작에 다시 넣는다.
  ⚠ 기존 '색인 어긋남' 검사(fts_stale)는 **개수만** 본다 — 내용 누락은 못 잡는다.
- **정확한 검색 = 4글자 이상은 앞부분 일치** (2026-10-12 사용자 결정): `apocalyp` → apocalypse ·
  apocalyptic. 3글자 이하(car·cat·ui)는 그대로(cartoon·catch 안 섞임), `*` 를 붙이면 앞부분.
  따옴표·여러 조각 말('sci-fi')은 그대로. 빼기(-단어)도 같은 규칙.
  ⚠ 단어색인은 porter 줄기만 저장해 `"p"*` 만으로는 앞부분 입력의 10.5% 를 놓친다(explosi→explosion).
  그래서 **원래 단어 목록 표 `term_words(word, stem)`** 를 둔다 (`_prefix_fts`). 줄기가 p 그대로면
  `"p"*` + 빠진 꼴, 바뀌면(acce→acc 는 accordion 까지 잡음) 목록 단어만 OR.
  숫자가 들어 있고 줄기가 그대로인 말은 목록에서 뺀다(사용자 결정 ①: 163만→42만 단어, +97→+25MB).
  시작 정비(`ensure_term_index`)가 표식 `term_words` 를 보고 만든다(49초), 이후 `_term_fts_insert`
  가 새 단어를 더한다(처음부터 만든 목록과 같음 확인).
  ⚠ `_porter_stems` 는 임시 fts5 표로 SQLite porter 를 직접 쓴다 — 파이썬으로 흉내 내지 말 것.
    자동 확정 연결에서 묶지 않으면 103초(묶으면 7초). 상주 검색 연결에 트랜잭션을 남기지 않는다.
- **OR 는 바로 옆 단어끼리** (2026-10-12, 베이스헤드와 같게): `sword OR knife metal` = (sword OR knife)
  AND metal. 필터 줄 조합(combine_rows)도 같다 — OR 줄은 앞 줄과 한 묶음, NOT 줄은 전체에서 뺀다.
  예전 FTS5 규칙(NOT > AND > OR)으로 되돌리지 말 것. `|` 기호는 쓰지 않는다 (사용자 결정 — 다중 필터).
- **동의어 = UCS 보완 규칙, 넓게·정확 모두** (`thesaurus.related`): 그 단어가 묶음 이름이고 그 이름의
  묶음이 하나뿐이면 묶음 전체, 묶음 안의 말이면 묶음 이름만(4개 이상 대분류에 되풀이되는 MISC·IMPACT 등
  은 빼고, 6개 넘는 묶음에 걸친 말은 안 붙임). 그냥 펼치면 door → Helicopter (UCS 묶음은 '그 분류 소리를
  설명하는 말' 이지 동의어 사전이 아니다). **원래 친 단어로 찾은 파일이 항상 먼저** — 두 모드 모두 1단계
  동의어 없이 → 상한을 못 채우면 2단계 동의어 포함으로 채운다 (with_syns). 한 식으로 섞으면 동의어 파일이
  앞서고, 넓게 찾기는 동의어 60개 OR 로 3~5초가 걸렸다.
- **2.1.7 부터 동의어 사전 = 게임 사운드 사전** (`py/app/data/game_sfx_thesaurus.tsv`, 베이스헤드 형식, 1,822묶음).
  UCS 사전은 철자 제안 단어 목록으로만 쓴다 (사용자 결정 2026-10-13: UCS 묶음은 '그 분류를 설명하는 말'이라
  small→Fairy·CREATURES, splash→WATER 같은 혼입이 끝없이 나왔다). 만드는 법·출처·혼입 거르기는
  `tools/game_thesaurus/README.md`. 규칙: 친 말이 묶음의 말과 **글자까지 같으면** 묶음 나머지 전부 (`thesaurus.BaseheadThesaurus`),
  UCS 시절 규제(4글자 이상·제외어) 없음. 되돌리기: env `SOUNDFIELD_THESAURUS=ucs`. 다른 사전 시험:
  `SOUNDFIELD_BASEHEAD_THESAURUS=<경로>` 를 정하고 `run_dev.bat` (run_basehead/run_gamedict.bat 은 2026-10-14 삭제). ⚠ 사전 파일이 번들에 빠지면 동의어만 조용히 사라진다 — `build_bridge.ps1` 이 확인.
  알려진 한계: 많이 쓰는 단어 300개 중 126개는 동의어 0개(뜻이 여럿인 흔한 말은 일부러 뺐다), `guns` 처럼 꼴이 다르면 안 붙음.
- **순위 점수 v3** (2026-10-14 사용자 결정, `Database._rank_score_v3`): 친 단어마다 **가장 좋은 자리 한 곳**만
  친다 — 이름 10 · 제목 8 · 키워드 5 · 분류 4 · 설명 3 · 코멘트 2 · 아티스트 등 1 · 폴더 0.5, 단어 중간에 걸리면 절반.
  친 단어들이 그 순서로 붙어 있으면 이름·제목 +12 / 설명·키워드·코멘트 +5 (붙여 쓴 MagicSpell 도 붙은 것).
  v2 는 칸마다 더해서 설명을 다 채운 팩이 위를 독차지했다(넓게 찾기 위 100개 채운 칸 평균 8.0/8, footstep 위 100개 중
  한 팩 99개) → v3 로 31종·최대 28개. 'magic spell' 이름이 딱 그 말인 파일이 위 50에 0개(첫 88위) → 50개(1위).
  정확한 검색도 같은 점수로 다시 줄 세운다 (원래 단어 먼저·동의어 뒤 유지).
  ⚠ 동의어 점수는 원래 단어가 안 걸린 파일에만 — 더하면 키워드에 'Whoosh, Woosh, Swish…' 를 통째로 붙인 팩이 다시 독차지했다.
- **동점은 파일 고유번호 순서** (2026-10-14 사용자 결정, 두 모드): 경로에서 계산한 번호라 어느 팩에도 유리하지 않다.
  정확한 검색은 예전에 bm25 순서였는데 정보가 빈 짧은 파일 = 한 팩이 위를 차지했다
  (whoosh 위 100개 팩 8종·한 팩 57개 → 30종·23개, laser 10종·76개 → 48종·17개).
- **UCS 분류 ID = 그 단어가 이름에 있는 것** (2026-10-14 사용자 결정): 이름이 `WEAPSwrd_` 로 시작하면 sword 를
  이름 점수(10)로 친다. 대응표 `py/app/data/ucs_catid.json` (`tools/build_ucs_catid.py`, ARCHIVED 제외) —
  ⚠ 보관 중인 `ucs_codes.json` 과 다른 파일이고 **설치본에 들어가야 한다** (`build_bridge.ps1` 이 확인).
  ⚠ 점수를 따로 더하지 말 것 — +6 을 더하니 위쪽이 UCS 이름을 쓰는 몇 팩으로 다시 몰렸다
  (위 100개 팩 종류 whoosh 67→26, laser 51→16, magic 49→10).
- **상한을 넘는 넓게 찾기는 이름에 든 파일부터 담는다** (2026-10-14 사용자 결정, `_DIRECT_STAGES`):
  이름 → 제목·키워드·분류 → 설명·코멘트. 5000개 중 이름에 단어가 든 비율 footstep 44→100%, magic 43→100%.
  ⚠ 단계를 따로 돌리지 말 것 — 칸을 좁힌 trigram MATCH 도 단어당 0.9초라 'library' 가 2.3→6.3초였다.
  hit·dhit 는 한 번만 만들고 단계는 LIKE 로 나눈 UNION ALL 한 문장 (앞 단계 행은 `NOT IFNULL(…,0)` 으로 뺀다).
- **동의어 사전 복수형** (2026-10-14 사용자 결정): 붙는 말이 없으면 복수형만 떼서 다시 본다 (guns → gun 묶음 + gun).
  ies→y·es·s, ss 는 안 뗀다. 다른 꼬리(-ing, -ed)는 떼지 않는다 — 뜻이 바뀌는 묶음이 섞인다.
  알려진 부작용: glasses(안경) → glass(유리) 묶음.
- **흔한 말 동의어 보강** (2026-10-14 사용자 승인 3묶음: alert·notification / reverberant·reverb·reverberation /
  sequence·seq). 25쌍 시험 중 나머지는 혼입으로 뺐다 — 목록과 이유는 `tools/game_thesaurus/사전_전체목록.md`.
  ⚠ **2글자 동의어 주의**: PSE 파일 이름 끝 무작위 ID(`eBgXt`)가 대소문자로 쪼개져 색인되므로 'bg' 같은 짧은
  말이 엉뚱한 파일에 걸린다 (background↔bg 시험에서 발견).
- **형식 배지 = 실제 형식** (2026-10-14 사용자 결정, `backend.ts codecToFmt`): 메타 분석의 codec(PCM→WAV, AIFF, MP3, FLAC)으로
  정한다. 이름은 .wav 인데 속은 AIFF 인 파일이 2,550개 있다. 분석 전(codec 없음)이면 배지를 비운다.
  ⚠ 파형을 그릴지는 `row.fmt` 로 정하지 말 것 — 확장자(`Player.tsx waveExt`)로 정한다. fmt 로 정하면 분석 전 파일의 파형이 꺼진다.
- **들어 본 소리 노랑** (2026-10-14 사용자 결정): 재생이 시작되면 그 줄의 모든 칸 글자를 노랗게(`--row-played`,
  밝은 테마는 진한 황토색). 메모리에만 두어 앱을 다시 켜면 초기화. 히스토리(100개 상한·[비우기])와 따로 둔다.
  표시는 `playRow` 한 곳에서만 넣는다 — current 를 바꾸는 곳이 거기뿐이다.
- **블랙리스트는 탭마다 완전히 따로** (2026-10-14 사용자 결정): '전체' 탭 목록 = 예전 `blacklist_paths` 그대로,
  즐겨찾기(`fav`)·개인 탭(`user:이름`) 목록 = `blacklist_tab_paths(tab, path, …)`. 검색은 요청의 `blacklist_tab` 으로
  그 탭 목록만 뺀다 (`Database._blacklist_tab_of` — `apply_blacklist` 에 탭 이름 문자열). 트리 숨김·라이브러리 검색·
  패널·상세 창도 지금 탭 목록만. 개인 탭 이름 변경 = 목록도 옮김, 탭 삭제 = 목록도 삭제 (남기면 같은 이름 새 탭에 되살아남).
  ⚠ 100개 넘을 때 쓰는 CTE 는 **인자를 받지 않는 자리**라 탭 이름을 SQL 글자로 넣는다(`_blacklist_source_sql`, 따옴표 이스케이프).
  ⚠ 탭별 표는 처음 추가할 때 만든다 — 읽는 쪽은 표가 없으면 빈 목록으로 본다.
- **블랙리스트 패널에 끌어다 놓기** (2026-10-14): 트리·라이브러리 검색 행 = HTML 드래그(`application/x-soundfield-paths`).
  결과 표 행 = 윈도우 드래그라 우리 창이 받지 못한다 → **놓은 순간 마우스 위치로 판단**(사용자 선택, `drag.ts announceInAppDrop`).
  끄는 동안 강조 없음·커서 '금지' 모양, Esc 취소도 같은 결과라 패널 위 Esc 면 추가 대화상자가 뜬다(취소하면 됨).
- **라이브러리 검색 행**: Ctrl/Shift 클릭 = 여러 개 고르기(트리 선택은 안 바뀜), 끌어서 기존 탭·블랙리스트 패널에 놓기.
  상위·하위를 같이 고르면 상위만 간다(`topmostOnly`). **클릭 = 고르기만, 더블클릭 = 그 폴더로 이동 + 트리 가운데로 스크롤**
  (사용자 결정 2026-10-14 — 클릭 이동이면 범위로 고르려고 첫 행을 누르는 순간 이동해 버렸다).
  ⚠ 바깥 클릭에 닫히는 건 **목록뿐**, 검색 행은 돋보기·Esc 로만 닫힌다 ("검색창이 닫히지 않게" = 검색 행만 남기라는 뜻).
  ⚠ 드롭다운은 다른 키 입력에 닫히는데 Ctrl/Shift/Alt/Meta 는 예외 — 빼면 Ctrl 클릭 전에 목록이 닫힌다.
- **모노로 합치기 (Sum to Mono, 2026-10-14 사용자 결정 — Soundminer 기능 이식)**: 재생 패널 바이노럴 버튼 왼쪽 `MONO`,
  단축키 M. 듣기와 DAW 로 끌어내기(결과표·히스토리·파형 영역·세그먼트 헤더) **모두** 모노. 켜지면 파형 오른쪽 위 `MONO` 표시.
  언제 꺼지는지 = 환경설정 › 재생 `config.monoKeep`: manual(직접) / **drag(DAW 로 한 번 보내면, 기본)** / play(다른 사운드 재생 시).
  앱을 켜면 늘 꺼진 상태. **모노·바이노럴은 둘 중 하나만** (사용자 결정 2026-10-14, Player.tsx): 모노를 켜면 바이노럴을
  끄고 기억 → 모노가 꺼지면(직접·자동 모두) 바이노럴 복귀. 바이노럴을 켜면 모노를 끈다. 바뀔 때 가운데 안내.
  채널 배치 비교 창의 '○○ 방식으로 듣기' 는 바이노럴이 꺼져 있으면 켜고(모노 끔) 바로 재생한다 —
  예전엔 판정만 바꾸고 재생을 안 해 고장처럼 보였다. 엔진 명령은 모노 끔 → 바이노럴 켬 → 배치 순으로 직접 보낸다.
  합치는 규칙(업계 표준, `downmix.mono_weights` 가 정본 — 재생·내보내기 같은 표): 스테레오 (L+R)/2,
  5.1·7.1 = ITU-R BS.775 (L·R 0.707, C 1, 서라운드 0.5, **LFE 버림**) 가중 평균, 앰비소닉 = W 채널만(FuMa ×√2),
  배치 모름 = 평균. 채널 배치는 바이노럴과 같은 판정(resolve_layout + 사용자 지정 override).
  내보내기: `%TEMP%\SoundField_regions\mono\<키>\이름_mono.wav` (이름은 깔끔하게, 겹침은 폴더로 피함), 24시간 정리.
  WAV 면 bext·iXML·cue 등 보존 + iXML TRACK_LIST 1트랙으로. MP3·FLAC·AIFF 는 소리만 모노 WAV. 순서 = 자르기 → 모노 → 배속.
  ⚠ 만들기 실패 시 드래그를 멈춘다 — 원본 스테레오를 몰래 대신 보내지 않는다.
- **넓게 찾기의 동의어는 단어로 찾는다** (2026-10-13 사용자 결정, `_orig_term_sql`): 원래 친 단어는 글자 일부
  (search_fts), 동의어는 단어색인 뿌리 꼴(search_fts_term) — 두 집합 UNION. 글자 일부로 찾으면 짧은 동의어가
  남의 단어 속에 걸렸다(spark 의 ARC → search·march 37만 행) + 그 단계가 2~5초. 뿌리 꼴이라 뜻이 바뀌는 묶음
  (shorting→short, rocking→rock)이 섞이는 건 감수("혼입이 누락보다 낫다"), 쓰인 꼴 그대로는 복수형을 잃어
  기각(같은 동의어 목록에 다른 꼴이 함께 있는 건 5%뿐). 붙여 쓴 이름 속 동의어(Lightsaber)는 못 찾는다.
  실측 196경우: 59 빨라짐·느려짐 0 (spark+en 4.7→0.6초, spark+energy+sci-fi 2.0→0.7초), 원래 단어 결과 같음.
- **검색 요청은 앞 검색이 끝나기 전에 브리지로 들어가야 한다** (`lib.rs` `ensure_search_service`): 살아 있으면
  출력 잠금을 잡지 말 것. 잡으면 앞 검색이 응답을 기다리는 동안 새 요청이 못 들어가 취소가 안 되고 검색이
  줄줄이 쌓인다(2026-10-13 실측 타이핑 중 4→13초, 재현 18.5초 → 고친 뒤 0.6초).
- 넓게 찾기 정렬 3단: 원래 단어가 파일 정보에 → 폴더 이름에만 → 동의어로만 (order_key).
- **업데이트 정비 화면**: sf_admin `maintenance_plan` → 할 일이 있을 때만 `maintenance_run` (단어색인·
  단어 목록·색인 누락 채우기·끊긴 색인 반영). 몇 분짜리만 여기 넣는다. 제목줄은 덮지 않는다(닫기 가능,
  다음 실행에 이어서). 새로 DB 를 고쳐 쓰는 작업이 생기면 `maintenance_steps` 에 단계를 더할 것.
- **파일 정보 보강 (메타 분석기 v3)**: WAV 는 소리 데이터 **뒤** LIST·bext 와 iXML 까지 읽는다
  (`metadata._read_riff_tail`·`_apply_ixml`, 빈 칸만 채움, 베이스헤드의 칸 대응을 따름). 예전엔 data 에서
  멈춰 표본의 72% 가 키워드·UCS 분류·제목을 못 읽었다. 이미 읽은 WAV 는 `audio_files.meta_ver < 3` 이면
  보강 대기 → 앱이 **쉴 때만**(30초 무입력·재생 없음, 설정과 무관) `reparse_meta` 로 다시 읽고, 만지면 바로
  멈춘다. 다시 읽다 실패해도 실패로 바꾸지 않는다(`set_meta_ver`). 진행 분모는 `meta_reparse_total`.
  실측 이 PC 111만 개, 시험 608개 6.4초. 파일당 +14ms(처음 여는 기준).
- 되돌리기: 환경변수 `SOUNDFIELD_SEARCH_ENGINE=legacy` 면 옛 엔진(`_query_legacy`)이 돈다.

⚠ UCS 사전(`ucs_thesaurus.json`)이 번들에서 빠지면 **아무 오류
없이** 동의어·철자 제안만 사라진다. `build_bridge.ps1` 이 매번 확인한다 — 그 검사를 지우지 말 것.

## 데이터 경로

- 인덱스 DB: `%LOCALAPPDATA%\SoundField\index.db`
- 파형 피크 캐시: `%LOCALAPPDATA%\SoundField\peaks_cache\`
- 재생 히스토리: `%LOCALAPPDATA%\SoundField\history.json`
- 컬럼 레이아웃: `~/.soundfield_config.json`
- 영역 crop 임시: `%TEMP%\SoundField_regions\` (24h 자동 정리)
