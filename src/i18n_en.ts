import { GENERATED_EN } from "./i18n_generated_en";

/* ── 영문 사전 ────────────────────────────────────────────────────────────────
   열쇠는 **화면에 쓰는 한글 원문 그대로**다 (i18n.ts 주석 참고).

   용어는 소개 페이지 영어판과 맞춘다 — 두 곳이 달라지면 사용자가 헷갈린다:
     파형 waveform · 세그먼트 segment · 구간 region · 색인 index
     검색 제외 exclude from search · 라이브러리 library · 갱신 update
   버튼·라벨은 문장부호 없이 짧게, 안내문은 완전한 문장으로 쓴다.

   ⚠ 여기에 없는 문장은 영어 모드에서도 한글로 나온다.
     `python tools/check_i18n.py` 로 빠진 문장을 찾을 수 있다. */

const CURATED_EN: Record<string, string> = {
  /* ── 종료 확인창 (App.tsx) ──
     ⚠ 열쇠는 화면에 그려지는 **한 덩어리 전체**여야 한다. i18n.ts 는 텍스트 노드를
       통째로 맞춰 보고 문장 단위로 쪼개 찾지 않는다 — 줄바꿈까지 그대로 적을 것. */
  "종료 확인": "Confirm quit",
  "종료": "Quit",
  "SoundField 를 종료할까요?": "Quit SoundField?",
  "인덱싱이 진행 중입니다. 지금 종료하면 작업이 중단됩니다.\n\nSoundField 를 종료할까요?":
    "Indexing is in progress. Quitting now will stop it.\n\nQuit SoundField?",

  /* ── 자동 업데이트 창 (components/UpdateDialog.tsx) ── */
  "업데이트": "Update",
  "새 버전이 있습니다.": "A new version is available.",
  "지금 {0} → 새 버전 {1}": "Current {0} → New {1}",
  "바뀐 파일 {0}개 · 받을 크기 {1} MB": "{0} changed files · {1} MB to download",
  "다 받으면 SoundField 가 다시 시작됩니다.": "SoundField will restart when the download finishes.",
  "받는 중… {0} / {1} MB": "Downloading… {0} / {1} MB",
  "다시 시작하는 중…": "Restarting…",
  "이전 크기로": "Restore down",
  "검색{0}": "Search {0}",
  "이 검색 탭 닫기": "Close this search tab",
  "검색 탭 추가": "Add search tab",
  "검색 탭 추가 (최대 3개)": "Add search tab (up to 3)",
  "동의어로 찾음": "Found via synonym",
  "업데이트 확인·내역 보기": "Check for updates · release notes",
  "업데이트 (개발자 배포)": "Update (developer build)",
  "개발자 배포를 받고 있습니다 — 이 PC 의 dev.cfg 를 지우면 정식 배포로 돌아갑니다":
    "Receiving developer builds — delete dev.cfg on this PC to return to official releases",
  "새 버전을 확인하는 중…": "Checking for a new version…",
  "지금 최신 버전입니다 ({0}).": "You have the latest version ({0}).",
  "확인": "OK",
  "업데이트를 받지 못했습니다. 지금 버전으로 계속 사용합니다.":
    "Couldn't download the update. You can keep using the current version.",

  /* ── 공통 짧은 문구 / 자동 번역 보정 ── */
  "PC 탐색기에서 보기": "Show in File Explorer",
  "새 탭": "New tab",
  "파일 브라우저": "File browser",
  "경로 폴더": "Path folders",
  "블랙리스트": "Blacklist",
  "블랙리스트 (": "Blacklist (",
  "인덱싱됨": "Indexed",
  "정상": "Ready",
  "대기": "Pending",
  "실패": "Failed",
  "정상 대기 실패": "Ready Pending Failed",
  "재생": "Play",
  "정지": "Stop",
  "처음으로": "Go to start",
  "반복 재생": "Loop playback",
  "바이노럴 입력 채널 배치 선택": "Choose binaural input channel layout",
  "길이": "Duration",
  "코덱": "Codec",
  "일반": "General",
  "테마": "Theme",
  "인덱싱": "Indexing",
  "중복 검수": "Duplicate review",
  "단축키": "Shortcuts",
  "저장": "Save",
  "히스토리": "History",
  "지우기": "Clear",
  "라이트": "Light",
  "네온": "Neon",
  "크기": "Size",
  "개수": "Count",
  "중복 검수 시작": "Start duplicate review",
  "제외 관리": "Manage exclusions",
  "인덱싱 집중": "Prioritize indexing",
  "세그먼트 토글": "Toggle segments",
  "히스토리 토글": "Toggle history",
  "바이노럴 켜기/끄기": "Toggle binaural audio",
  "환경설정 열기/닫기": "Open or close preferences",
  "파형 가로 확대/축소": "Waveform horizontal zoom",
  "파형 세로 확대/축소": "Waveform vertical zoom",
  "가로 스크롤 (파형/파일브라우저/결과)": "Horizontal scroll (waveform/file browser/results)",
  "개별 설정 불가": "Cannot be customized",
  "Ctrl+휠": "Ctrl+Wheel",
  "Ctrl+Alt+휠": "Ctrl+Alt+Wheel",
  "Shift+휠": "Shift+Wheel",
  "등록된 블랙리스트 ({0}개)": "Registered blacklist entries ({0})",
  "라이브러리": "Library",
  "사유": "Reason",
  "현재 라이브러리": "Current libraries",
  "상태": "Status",
  "파일 수": "Files",
  "마지막 스캔": "Last scan",
  "라이브러리 정보 불러오는 중...": "Loading library information…",
  "선택한 라이브러리 상세 보기…": "View selected library details…",
  "실패 전체 재시도": "Retry all failures",
  "검색 최대 노출 개수:": "Maximum results per search:",
  "결과 목록 동작:": "Result list behavior:",
  "결과 텍스트 크기:": "Result text size:",
  "저장 버튼을 누르면 적용됩니다.": "Changes apply when you click Save.",
  "메타 분석 정책:": "Metadata analysis:",
  "파일명 / 경로": "File name / path",
  "검색 결과 내 찾기 (파일명/경로)": "Search results (file name/path)",
  "{0}차 AmbiX": "{0}th-order AmbiX",
  "1차 AmbiX": "First-order AmbiX",
  "1차 FuMa": "First-order FuMa",
  "IEM 플러그인 설치 필요": "IEM plug-ins required",
  "IEM MultiEncoder와 BinauralDecoder를 찾지 못했습니다.": "IEM MultiEncoder and BinauralDecoder were not found.",
  "MultiEncoder와 BinauralDecoder는 무료·오픈소스 IEM Plug-in Suite에 함께 포함되어 있습니다.":
    "MultiEncoder and BinauralDecoder are included in the free, open-source IEM Plug-in Suite.",
  "검증 버전: IEM Plug-in Suite v{0}": "Verified version: IEM Plug-in Suite v{0}",
  "설치를 마친 뒤 SoundField를 다시 실행하세요.": "After installation, restart SoundField.",
  "무료 설치 페이지 열기": "Open free download page",

  /* ── 창 제어 (WindowChrome) ── */
  "최소화": "Minimise",
  "최대화": "Maximise",
  "닫기": "Close",
  "표시 언어 전환 (한국어 / English)": "Switch display language (Korean / English)",
  "YSG Audio Labs 허브 열기": "Open the YSG Audio Labs hub",

  /* ── 앱 헤더 (Header) ── */
  "환경설정": "Preferences",
  "환경설정 (Ctrl+P)": "Preferences (Ctrl+P)",
  "Tab/Enter 필터 추가 · Backspace 이동/제거": "Tab/Enter adds a filter · Backspace moves/removes",
  "검색 도움말": "Search help",
  "라이브러리 추가": "Add library",
  "새로운 폴더 경로를 라이브러리로 등록합니다.": "Registers a new folder path as a library.",
  "빠른 갱신": "Quick update",
  "최근 변경된 파일만 빠르게 감지합니다.\n새 파일/삭제된 파일만 처리되어 속도가 빠릅니다.\n메타데이터는 변경된 파일에만 다시 추출됩니다.":
    "Detects only recently changed files.\nOnly new and deleted files are processed, so it is fast.\nMetadata is re-extracted only for changed files.",
  "전체 갱신": "Full update",
  "기존 인덱스 보존 + 모든 파일 강제 재스캔.\n라이브러리 추가와 동일한 속도. mtime 같으면 메타는 보존.":
    "Keeps the existing index and force-rescans every file.\nAs fast as adding a library. Metadata is kept when the modified time is unchanged.",
  "취소": "Cancel",
  "인덱싱 중지. 이미 스캔된 파일은 보관됨 — [전체 인덱스 업데이트] 다시 클릭하면 이어서 진행.":
    "Stops indexing. Already scanned files are kept — click [Full update] again to continue.",
  "⚠ 검색 정리": "⚠ Repair search",
  "검색 데이터가 실제 파일 목록과 어긋나 이 버튼이 나타났습니다.\n(보통 인덱싱 도중 앱이 꺼진 경우 — 일부 파일이 검색에 안 나올 수 있음)\n누르면 검색 데이터를 다시 만들어 파일 목록과 맞춥니다.":
    "This button appears when the search data no longer matches the actual file list.\n(Usually after the app was closed during indexing — some files may be missing from search.)\nClicking it rebuilds the search data to match the file list.",

  /* ── 검색 필드 이름 (SearchPanel) ──
     ⚠ 한글 쪽은 필터의 **식별자**로도 쓰인다 (FIELD_KEYS). 표시만 바꾼다. */
  "전체": "All",
  "파일명": "File name",
  "경로": "Path",
  "제목": "Title",
  "아티스트": "Artist",
  "앨범": "Album",
  "장르": "Genre",
  "코멘트": "Comment",
  "설명": "Description",
  "키워드": "Keywords",
  "카테고리": "Category",
  "서브카테고리": "Sub-category",
  "출처": "Source",
  "7채널 이상": "7 channels or more",

  /* ── 검색 입력 (SearchPanel) ── */
  "검색어 입력...": "Enter a search term…",
  "이 조건 제거": "Remove this condition",
  "검색어 전체 삭제 (모든 입력 행 비우기)": "Clear every search term (empty all filter rows)",
  "검색어 비우기": "Clear search",
  "최소": "Min",
  "최대": "Max",
  "샘플레이트 필터": "Sample rate filter",
  "채널 필터": "Channel filter",
  "필터 초기화": "Reset filters",
  "필터 초기화\n길이/샘플레이트/채널 필터를 모두 기본값(전체)으로 복원합니다.":
    "Reset filters\nRestores the length, sample rate, and channel filters to their default (All).",
  "정확한 검색": "Exact search",
  "▶ 히스토리": "▶ History",
  "◀ 히스토리": "◀ History",
  "히스토리 ◀": "History ◀",
  "재생 히스토리 열기/닫기\n단축키: H": "Show or hide playback history\nShortcut: H",

  /* 정확한 검색 툴팁 — 원문이 여러 줄을 join 한 것이라 통째로 한 항목이다 */
  "정확한 검색\n\n단어 단위로 찾습니다. 4글자 이상은 앞부분만 쳐도\n그 말로 시작하는 단어를 찾습니다.\n\n예) 'apocalyp' → apocalypse · apocalyptic\n    'door' → door · doors · doorbell\n    'car' (3글자 이하) → car · cars 만 (cartoon 은 안 나옴)\n\n3글자 이하도 앞부분으로 찾으려면 끝에 * 를 붙이세요. 예) 'car*'\n\n끄면(넓게 찾기): 단어 중간의 글자 조각으로도 찾습니다.\n\n동의어도 붙습니다 — 원래 친 단어로 찾은 파일이 먼저 나옵니다.":
    "Exact search\n\nMatches whole words. For words of 4+ letters, typing the start\nfinds every word that begins with it.\n\ne.g. 'apocalyp' → apocalypse, apocalyptic\n     'door' → door, doors, doorbell\n     'car' (3 letters or fewer) → car, cars only (no cartoon)\n\nFor 3 letters or fewer, add * to match the start. e.g. 'car*'\n\nOff (broad search): also matches letter fragments inside words.\n\nSynonyms are added too — files found by the word you typed come first.",

  /* 검색 상한 · 시간 제한 (2026-10-08) */
  "결과 {0}개 — 더 있음{1}": "{0} results — more available{1}",
  "검색이 {0}초 안에 끝나지 않아 멈췄습니다 — 검색어나 필터를 좁혀 보세요":
    "The search did not finish within {0} seconds and was stopped — narrow the search or filters",
  "(추천: 5000개 / 최대: 20000개. 값이 클수록 검색 성능에 영향을 줄 수 있습니다.)":
    "(Recommended: 5000 / Maximum: 20000. Larger values may affect search performance.)",

  /* 업데이트 정비 화면 · 파일 정보 보강 (2026-10-12) */
  "업데이트 마무리 중": "Finishing the update",
  "검색 데이터를 새 버전에 맞게 정리하고 있습니다. 끝나면 자동으로 닫힙니다.": "Updating the search data for the new version. This closes automatically when done.",
  "단계 {0}/{1}": "Step {0}/{1}",
  "정확한 검색 단어색인 다시 만들기": "Rebuilding the exact-search word index",
  "단어 목록 만들기": "Building the word list",
  "검색 색인 누락 채우기": "Filling gaps in the search index",
  "지난번 끊긴 검색 색인 이어서 반영": "Resuming the interrupted search index update",
  "앱을 닫아도 다음 실행 때 이어서 합니다.": "If you close the app, it continues next time.",
  "파일 정보 보강 중 {0}%": "Enhancing file info {0}%",
  "· 남은 약 {0}시간 {1}분": "· about {0}h {1}m left",
  "· 남은 약 {0}분": "· about {0}m left",
  "파일 정보 보강 {0}% — 쉬는 동안 진행": "Enhancing file info {0}% — runs while idle",
  "파일 정보 보강 — 새 분석기로 WAV 파일의 키워드·분류·제목을 다시 읽습니다.\n앱을 쓰지 않을 때(30초 이상 입력 없음)만 진행하고, 만지거나 재생하면 바로 멈춥니다.": "File info enhancement — re-reads keywords, categories and titles of WAV files with the new reader.\nRuns only while the app is idle (no input for 30+ seconds) and stops as soon as you use it or play a sound.",

  /* 철자 제안 · 환경설정 동의어 (2026-10-07) */
  "혹시 이 단어를 찾으셨나요?": "Did you mean this word?",
  "동의어:": "Synonyms:",
  "동의어도 함께 찾기": "Also search synonyms",
  "검색어와 비슷한 말(게임 사운드 동의어 사전)이 든 파일도 함께 찾습니다. 원래 단어로 찾은 파일이 먼저 나옵니다.\n따옴표로 감싼 말에는 붙지 않습니다.": "Also finds files containing similar words (game SFX thesaurus). Files found by the word you typed come first.\nQuoted words get no synonyms.",

  /* 검색 문법 도움말 (Header 의 ? 버튼) — 원본 multi_search.py:_SEARCH_HELP_TEXT */
  "필터 단축키\n  • Tab/Enter : 필터 추가\n  • 빈 검색창 Backspace : 위 필터로 이동 + 제거\n  • 최소 1개 필터는 유지\n\n검색 문법\n\n기본\n  • 공백 = AND (모두 매칭)\n  • 예: dark magic → 두 단어 모두 어딘가 있는 결과\n\n연산자 (대문자만 인식)\n  • AND : 둘 다 매칭          예) dark AND magic\n  • OR  : 둘 중 하나          예) sword OR knife\n  • OR 는 바로 옆 단어끼리     예) sword OR knife metal → 금속인 칼이나 검\n  • 쉼표는 띄어쓰기와 같음    예) door, wood = door wood\n  • NOT : 제외                예) footstep NOT rain\n  • 앞에 - 를 붙여도 제외     예) door -slam\n\n그룹화\n  • 괄호로 우선순위 지정\n  • 예) (sword OR knife) AND fight\n\n따옴표 phrase\n  • \"dark magic\" → 그 순서로 붙은 결과만\n  • 따옴표 안의 말에는 동의어를 붙이지 않음\n\n동의어\n  • 비슷한 말도 함께 찾음 (게임 사운드 동의어 사전)  예) sword → katana · machete · dagger\n  • 원래 친 단어로 찾은 파일이 먼저, 비슷한 말로 찾은 파일은 그 뒤\n  • 넓게·정확 모두 적용\n  • 환경설정에서 끌 수 있음\n\n비고\n  • 1-2글자 짧은 토큰은 매칭 잘 안 됨 (3글자 이상 권장)\n  • 위 문법은 “경로” 필드를 뺀 모든 필드에서 적용":
    "Filter shortcuts\n  • Tab/Enter : add a filter\n  • Backspace in an empty box : move to the filter above and remove it\n  • At least one filter is always kept\n\nSearch syntax\n\nBasics\n  • A space means AND (all terms must match)\n  • e.g. dark magic → results containing both words somewhere\n\nOperators (uppercase only)\n  • AND : both match            e.g. dark AND magic\n  • OR  : either one matches    e.g. sword OR knife\n  • OR binds only its neighbours e.g. sword OR knife metal → a metal sword or knife\n  • A comma is just a space    e.g. door, wood = door wood\n  • NOT : exclude               e.g. footstep NOT rain\n  • A leading - also excludes  e.g. door -slam\n\nGrouping\n  • Parentheses set precedence\n  • e.g. (sword OR knife) AND fight\n\nQuoted phrase\n  • \"dark magic\" → only results with the words adjacent in that order\n  • Words in quotes get no synonyms\n\nSynonyms\n  • Similar words are searched too (game SFX thesaurus)  e.g. sword → katana · machete · dagger\n  • Files found by the word you typed come first, then files found by similar words\n  • Applies to broad and exact search\n  • Can be turned off in Settings\n\nNotes\n  • One or two character tokens match poorly (three or more recommended)\n  • This syntax applies to every field except “Path”",
  /* ── 바이노럴 채널 순서 팝업 (LayoutDialogs.ChannelOrderDialog, 2026-09-16) ──
     본문은 한 텍스트 노드라 파일명·순서 이름을 {0}{1} 자리표로 받는 통짜 열쇠가 필요하다. */
  "채널 순서 확인 필요": "Channel order needs confirmation",
  "{0} 이 파일에는 채널 이름 정보가 없어 순서를 확인할 수 없습니다. 지금은 {1} 순서로 재생 중이며, 맞는지 확인되지 않았습니다.": "{0}\n\nThis file has no channel name information, so the order cannot be confirmed.\nIt is currently playing in {1} order, which has not been verified.",
  "{0} 이 파일에는 채널 이름 정보가 없어 순서를 확인할 수 없습니다.": "{0}\n\nThis file has no channel name information, so the order cannot be confirmed.",
  "지금은 {0} 순서로 재생 중이며, 맞는지 확인되지 않았습니다.": "It is currently playing in {0} order, which has not been verified.",
  "{0} 방식으로 듣기 ({1})": "Listen in {0} order ({1})",
  "{0} 방식으로 듣기": "Listen in {0} order",
  "· 지금 이 방식": "· playing now",
  "두 방식을 눌러 번갈아 들어보세요": "Press each option to compare them",
  "{0} 방식 · 현재 위치에서 비교 중": "{0} order · comparing from the current position",
  "적용 범위": "Apply to",
  "이 파일만": "This file only",
  "이 폴더 ({0})": "This folder ({0})",
  "폴더를 고르면 같은 폴더 안에서 채널 수가 같은 파일에만 적용됩니다": "Choosing the folder applies it only to files in the same folder with the same channel count",
  "나중에": "Later",
  "WAV 규격": "WAV spec",

  /* ── 바이노럴 버튼 아래 상세 줄 (Player.tsx) — 판정 근거·저장 상태·LFE 꼬리표 ── */
  "파일 내부 채널 정보": "Channel info in file",
  "WAV 채널 마스크": "WAV channel mask",
  "사용자 지정": "Manual",
  "파일명 추정": "Guessed from file name",
  "채널 수 추정": "Guessed from channel count",
  "배치 확인 필요": "Layout needs confirmation",
  "· LFE 제외": "· LFE excluded",
  "{0} · LFE 제외": "{0} · LFE excluded",
  "개별 저장됨 · {0}": "Saved for this file · {0}",
  "폴더 설정 · {0}": "Folder setting · {0}",
  "개별 저장됨 · {0} · LFE 제외": "Saved for this file · {0} · LFE excluded",
  "폴더 설정 · {0} · LFE 제외": "Folder setting · {0} · LFE excluded",
  "초기화": "Reset",
  "자동 판정 근거입니다": "Basis of the automatic detection",
  "· 바이노럴 모니터링 설정 (원본 파일은 바꾸지 않음)": "· Binaural monitoring setting (the original file is not changed)",
  "{0} · 바이노럴 모니터링 설정 (원본 파일은 바꾸지 않음)": "{0} · Binaural monitoring setting (the original file is not changed)",
  "채널 배치를 저장하지 못했습니다. 다시 시도하세요.": "Could not save the channel layout. Please try again.",

  /* ── DAW 내보내기 배너 — 파이썬(sf_waveform_service / region_export)이 보내는 문장 포함 ── */
  "내보내기를 중단했습니다:": "Export cancelled:",
  "내보내기를 중단했습니다: {0}": "Export cancelled: {0}",
  "내보낼 파일을 만들지 못했습니다": "Could not create the export file",
  "선택 구간을 잘라내지 못했습니다": "Could not cut the selected region",
  "선택 구간의 시작과 끝이 필요합니다": "The selected region needs a start and an end",
  "선택 구간이 올바르지 않습니다": "The selected region is invalid",
  "배속 값이 올바르지 않습니다": "Invalid playback speed value",
  "배속 파일을 만들지 못했습니다 (자세한 원인은 로그의 [speed] 항목)": "Could not create the speed-changed file (see the [speed] entry in the log)",
  "채널 마스크를 보존하지 못했습니다": "Could not preserve the channel mask",

  /* ── 그 외 사전에 빠져 있던 문장 (tools/build_i18n_catalog 판정, 2026-09-16) ── */
  "'{0}' 을(를) '★ 즐겨찾기' 에 추가": "Add '{0}' to '★ Favorites'",
  "'{0}' 을(를) '★ 즐겨찾기' 에서 제거": "Remove '{0}' from '★ Favorites'",
  "이미 '★ 즐겨찾기' 에 있습니다": "Already in '★ Favorites'",
  "{0}개 파일 — 제외 대상": "{0} files — to be excluded",
  "{0}월 {1}일 {2}:{3}": "{0}/{1} {2}:{3}",
  "다시 스캔이 필요합니다": "A rescan is needed",
  "대기 중인 항목이 없습니다": "No pending items",
  "실패한 항목이 없습니다": "No failed items",
  "미완료 항목이 없습니다 — 이 라이브러리는 분석이 모두 끝났습니다": "No unfinished items — analysis of this library is complete",
  "중복 검수 기록을 읽을 수 없습니다: {0}": "Could not read the duplicate review records: {0}",
  "중복 검수 기록을 저장할 수 없습니다: {0}": "Could not save the duplicate review records: {0}",
  "직전 검수 결과 {0}개 그룹 (보관 안 됨)": "Last review result: {0} groups (not kept)",
};

/* 자동 수집 사전은 아직 t()로 옮기지 않은 화면과 백엔드 표시 메시지를 맡는다.
   사람이 다듬은 표현이 항상 우선한다. */
export const EN: Record<string, string> = { ...GENERATED_EN, ...CURATED_EN };
