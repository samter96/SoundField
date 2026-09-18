import { useEffect, useMemo, useRef, useState } from "react";
import "./styles/app.css";

import { WindowChrome } from "./components/WindowChrome";
import { TooltipLayer } from "./components/TooltipLayer";
import { onAudioEvent, onAudioPos, onIndexEvent } from "./backend";
import { Header } from "./components/Header";
import { Sidebar } from "./components/Sidebar";
import { SearchPanel } from "./components/SearchPanel";
import { ResultsTable } from "./components/ResultsTable";
import { Player } from "./components/Player";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { Modals } from "./components/Modals";
import { ContextMenu, type Ctx } from "./components/ContextMenu";
import { CenterToast, type ToastMessage, type ToastKind } from "./components/CenterToast";
import { CenterLoader, type LoaderTask } from "./components/CenterLoader";

import { type Lib, type Row, type TreeNode } from "./data";
import { addBlacklistPath, appLoaded, audioBridge, cancelAdmin, loadFtsStale, loadHidden, setRootOrder, loadBlacklistPaths, loadFolderTree, loadIndexedCount, loadInitialRows, loadLibraryRoots, loadLibraryRootsBasic, loadRowByPath, recordHistory, runAdmin, searchRows, type AdminRequest, type SearchRequest } from "./backend";
import { applyTheme, loadTheme, type ThemeName } from "./theme";
import { CONFIG_DEFAULTS, loadUserConfig, saveUserConfig, type UserConfig } from "./config";
import { setLang, useLang } from "./i18n";
import { IcoCheck } from "./icons";
import { setDragStatusSink } from "./drag";

/* 인덱스에 없는 파일을 **경로만으로** 재생용 항목으로 만든다 (조사 H02).
   원본 _on_history_pick 은 히스토리 경로를 그대로 load_and_play 에 넘긴다 —
   인덱스에서 제거했어도 디스크에 파일이 남아 있으면 다시 들을 수 있다
   (main_window.py:5572). 메타는 없으면 없는 대로 넘긴다.

   id 는 음수 해시를 쓴다 — 실제 DB 행의 id 는 양수라 절대 겹치지 않고,
   같은 경로면 같은 값이라 재생 패널의 '같은 파일' 판정도 흔들리지 않는다. */
const HISTORY_FMTS: Record<string, Row["fmt"]> = {
  wav: "WAV", mp3: "MP3", flac: "FLAC", aif: "AIFF", aiff: "AIFF",
};

function rowFromPath(fullPath: string): Row | null {
  const clean = fullPath.replace(/\//g, "\\");
  const name = clean.split("\\").filter(Boolean).at(-1) ?? "";
  const ext = (name.split(".").at(-1) ?? "").toLowerCase();
  const fmt = HISTORY_FMTS[ext];
  if (!name || !fmt) return null;
  let hash = 0;
  for (const ch of clean.toLowerCase()) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return {
    id: -Math.abs(hash || 1),
    dur: 0, fmt, name, sr: 0, ch: 0, bitDepth: 0, codec: "", size: 0,
    path: clean.slice(0, Math.max(0, clean.length - name.length - 1)),
    fullPath: clean,
    cat: "", title: "", artist: "", album: "", genre: "", comments: "", bitrate: 0,
  };
}

/* 중복 실행 가드 문구용 작업 이름 — DB 를 쓰는 op 전부를 덮는다.
   (없는 op 는 "이 작업" 으로 떨어진다 — 새 op 를 추가하면 여기에도 넣을 것) */
const ADMIN_OP_LABEL: Record<string, string> = {
  add_index: "라이브러리 추가",
  index: "재스캔",
  remove_roots: "라이브러리 제거",
  detect: "변경 감지",
  repair_fts: "검색 색인 수리",
  backfill_fts: "검색 정리",
  ensure_term_index: "단어 색인 만들기",
  parser_upgrade: "메타 파서 업그레이드",
  retry_paths: "미완료 재시도",
  retry_scope: "미완료 재시도",
  delete_paths: "인덱스 삭제",
  delete_scope: "인덱스 삭제",
  hide_paths: "검색 제외",
  unhide_paths: "검색 제외 해제",
  unhide_all: "검색 제외 전체 해제",
  unhide_ids: "검색 제외 해제",
  mark_dup_orphans: "중복 검수 반영",
};

/* 남은 시간 표시 — 원본 _fmt_remaining 과 같은 규칙. **정밀도를 일부러 낮춘다.**

   추정 오차가 실측 ±15% 수준(참값 1시간 34분에 1:20~1:50 관측)인데 초 단위로
   보여주면 0.3초마다 숫자가 바뀌어 "널뛴다"고 느껴진다 (사용자 지적 2026-09-07).
   없는 정밀도를 없다고 표시하는 것이 맞다 → 10분 단위로 뭉갠다.

   ⚠ 여기에 초를 다시 넣지 말 것. 이동 평균으로 바꾸는 것도 답이 아니다 —
     최근 구간에 민감해져 오히려 더 튄다. 누적 평균은 진행이 쌓이면 저절로 안정된다. */
function fmtRemaining(seconds: number): string {
  const sec = Math.max(0, seconds);
  if (sec < 60) return "거의 끝남";
  let minutes = Math.round(sec / 60);
  if (minutes < 10) return `약 ${Math.max(1, minutes)}분`;
  minutes = Math.round(minutes / 10) * 10;
  if (minutes < 60) return `약 ${minutes}분`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? `약 ${h}시간` : `약 ${h}시간 ${m}분`;
}

/* ── 라이브러리 추가 (원본 _add_library, main_window.py:6155) ──
   OS 폴더 선택 후 세 갈래로 확인 대화상자를 띄운다.
     ① 같은 경로가 이미 등록됨 → "이미 인덱싱된 폴더" (다시 스캔?)
     ② 상위 라이브러리 안에 포함됨 → 같은 창, 다른 문구 (하위만 다시 스캔?)
     ③ 이 경로 아래 하위 라이브러리가 있음 → "라이브러리 통합" (상위로 통합?)
     ④ 그 외 → "라이브러리 추가" (지금 스캔?)
   아니오를 누르면 목록에 추가되지 않고 상태바에만 이유를 남긴다. */
const normPath = (value: string) => value.replace(/[\\/]+$/, "");
const isChildPath = (child: string, parent: string) => {
  const c = normPath(child).toLowerCase();
  const p2 = normPath(parent).toLowerCase();
  return c.startsWith(p2 + "\\") || c.startsWith(p2 + "/");
};

const EMPTY_TREE: TreeNode = { id: "root", label: "", path: "", count: 0, children: [] };

/* 원본 _GripSplitterHandle (main_window.py:76) 을 그대로 옮긴 손잡이.
   배경(bg_control_hi) + accent wash → 양쪽 2px 엣지 → 밴드(bg_elev, 두께 62%)
   → 가이드(border_strong, 32%) → 알약 그립(border_strong→accent_hover, 48%)
   → 호버 시 점 3개(on_accent). 폭은 원본 handle_width 그대로:
   좌우 분할 16px / 결과↔플레이어 20px / 블랙리스트 6px. */
function Grip() {
  return (
    <span className="grip-band" aria-hidden="true">
      <span className="grip-guide" />
      <span className="grip-pill">
        <i /><i /><i />
      </span>
    </span>
  );
}

/* 좌우/상하 분할 폭은 사용자가 끌어서 바꾼다. 값은 이 세션 동안만 유지. */
export default function App() {
  const [theme, setTheme] = useState<ThemeName>(loadTheme);
  /* 언어가 바뀌면 화면 전체를 다시 그린다 — 이 앱은 React.memo 를 쓰지 않으므로
     최상위에서 한 번 구독하면 자식 컴포넌트까지 새 언어로 그려진다 (i18n.ts 참고). */
  useLang();
  const [modal, setModal] = useState<string | null>(null);
  /* 재생 패널이 직접 띄운 창/메뉴 — 열려 있으면 재생 단축키를 막는다 (조사 Q10) */
  const [playerOverlay, setPlayerOverlay] = useState(false);
  /* ── 관리 창이 DB 쓰기를 도는 중인가 (조사 M01/M03) ──────────────────────────
     창 안에서 막아도 **창 밖 경로**로 닫히면 결과·개수 갱신이 유실된다:
       · Ctrl+P (환경설정 토글)  · [뒤로] 버튼  · 현재 라이브러리 행 더블클릭
     그래서 창이 여기로 상태를 올리고, 창 밖 경로는 모두 이 값을 본다.
     ⚠ 새 닫기 경로를 만들면 여기 검사를 같이 붙일 것. */
  const [modalBusy, setModalBusy] = useState(false);
  const [ctx, setCtx] = useState<Ctx>(null);
  /* 원본 library_tabs.feedbackRequested → _CenterToast.show_message */
  const [toast, setToast] = useState<ToastMessage>(null);
  /* 원본 center_loader.start(key, msg) / stop(key) — ref-count 로 여러 작업이 겹쳐도
     하나만 뜬다. 원본이 로더를 쓰는 작업: 폴더 트리 갱신, 검색 색인 마무리/재생성,
     라이브러리 제거, 인덱싱, 변경 감지. */
  const [loaderTasks, setLoaderTasks] = useState<LoaderTask[]>([]);
  /* 원본은 시작 시 SplashScreen(640x360) 을 띄우고 메인 창이 보이면 닫는다
     (main.py:279 splash.show() → 316 splash.finish(win)).
     여기서는 초기 로딩(설정 + 결과 목록)이 끝나면 걷는다. */
  /* 스플래시는 **별도의 작은 창**(splash.html)이다. 준비가 끝나면 Rust 가 메인 창을
     띄우고 스플래시 창을 닫는다 (원본 splash.finish(win) 과 같은 자리).
     예전에는 메인 창 안의 전면 오버레이라, 스플래시 뒤에 툴 창 크기의 어두운 화면이
     함께 보였다 (사용자 신고). */
  const splashSignaledRef = useRef(false);
  const finishSplash = () => {
    if (splashSignaledRef.current) return;
    splashSignaledRef.current = true;
    void appLoaded();
  };
  /* ── 스플래시 유지 조건 (사용자 지시 2026-09-02) ──────────────────────────────
     "어차피 초기 로딩이 도는 시간에 스플래시를 보여주자"는 원래 의도대로,
     **설정 · 첫 결과 목록 · 폴더 트리** 세 가지가 준비될 때까지 유지한다.
     폴더 트리는 158만 행 스캔이라 가장 오래 걸리는데, 이걸 기다리는 동안
     빈 화면 대신 스플래시가 보인다 (트리 갱신 자체는 그대로 백그라운드).
     상한 6초 — 어떤 이유로 신호가 안 와도 스플래시에 갇히지 않게 한다. */
  const treeLoadedRef = useRef(false);
  const splashNeeds = useRef({ config: false, rows: false, tree: false });
  const markSplash = (key: "config" | "rows" | "tree") => {
    splashNeeds.current[key] = true;
    const { config: c, rows: r, tree: t } = splashNeeds.current;
    if (c && r && t) finishSplash();
  };
  useEffect(() => {
    /* 상한 6초 — 어떤 이유로 신호가 안 와도 스플래시에 갇히지 않게 한다.
       (Rust 쪽에도 10초 안전망이 따로 있다) */
    const t = window.setTimeout(finishSplash, 6000);
    return () => window.clearTimeout(t);
  }, []);
  /* 지금 화면을 잠근 로더의 키. 진행 상황을 **그 키로** 보내야 게이지가 움직인다.
     ⚠ 예전에는 진행을 항상 "index" 로 보냈다. 그래서 [검색 정리]("fts" 키) 처럼
       다른 키로 띄운 로더는 게이지가 끝까지 멈춰 있었다 (조사 Q05). */
  const loaderKeyRef = useRef("index");
  const startLoader = (key: string, msg: string) => {
    loaderKeyRef.current = key;
    setLoaderTasks((tasks) => [...tasks.filter((t) => t.key !== key), { key, msg }]);
  };
  /* 원본 center_loader.set_progress(key, 문구, %) — 진행 중 로더 내용을 갱신한다.
     gauge < 0 이면 퍼센트를 모르는 구간(무한 게이지)이다. */
  const setLoaderProgress = (key: string, progress: string, gauge: number) =>
    setLoaderTasks((tasks) => tasks.map((t) =>
      t.key === key ? { ...t, progress, gauge } : t));
  const stopLoader = (key: string) =>
    setLoaderTasks((tasks) => tasks.filter((t) => t.key !== key));
  /* 엔진이 보고하는 재생 위치(초) — '이어서 재생' 이 끝에 닿았는지 판단할 때만 쓴다.
     화면 표시는 Player 가 따로 보간해서 그린다 (여기 값으로 그리지 말 것). */
  const playedSecRef = useRef(0);
  useEffect(() => onAudioPos((info) => {
    playedSecRef.current = (info.position_ms || 0) / 1000;
  }), []);

  const notify = (kind: ToastKind, text: string) =>
    setToast({ kind, text, id: Date.now() });
  const [history, setHistory] = useState(false);
  /* 원본 config "compact_results" — 결과 텍스트 크기 (보통 / 작게). 환경설정에서 저장 시 반영. */
  /* 폴더 선택으로 추가된 라이브러리 — 원본은 곧바로 인덱싱을 시작하지만
     백엔드 연결 전이라 목록에만 '대기' 상태로 표시한다. */
  const [addedLibs, setAddedLibs] = useState<string[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [libraryRoots, setLibraryRoots] = useState<Lib[]>([]);
  const [blacklistRows, setBlacklistRows] = useState<Array<{ path: string; description: string }>>([]);
  const [folderTree, setFolderTree] = useState<TreeNode>(EMPTY_TREE);
  const [indexedCount, setIndexedCount] = useState(0);
  const [searchReq, setSearchReq] = useState<SearchRequest | null>(null);
  /* 원본 앱의 설정 파일을 읽어 그대로 반영한다 (읽기 전용). */
  const [config, setConfig] = useState<UserConfig>(CONFIG_DEFAULTS);
  const [compact, setCompact] = useState(CONFIG_DEFAULTS.compactResults);

  /* 검색 기준 브레드크럼 — 원본 _do_search (main_window.py:7208) 의 scope 문자열 */
  const [scopePrefixes, setScopePrefixes] = useState<string[]>([]);
  /* 원본 revealInBrowserRequested → _on_results_reveal_in_browser → tree.focus_path(path):
     결과 우클릭으로 그 파일의 폴더를 파일 브라우저에서 펼치고 선택한다. */
  const [focusPath, setFocusPath] = useState<string | null>(null);
  /* 원본 상태바 우측 "인덱싱 집중" 체크박스 (config index_focus_mode) */
  const [focusIndex, setFocusIndex] = useState(false);

  const [sideW, setSideW] = useState(250);
  /* 원본 right_splitter.setSizes([550, 160]) + player 최소 180 → 시작 시 플레이어는
     최소값인 180 에 걸린다 (스트레치 4:1 로 나눠도 160 → 180 클램프). */
  const [playerH, setPlayerH] = useState(180);

  const [current, setCurrent] = useState<Row | null>(null);
  const [selected, setSelected] = useState<Row | null>(null);
  const [playing, setPlaying] = useState(false);
  /* 원본 ResultsTable.set_pip 상태 기계 (results_table.py:720, main_window.py:7299)
       재생 요청 순간           → loading (빨강 박동)
       실제로 재생이 시작되면   → playing (초록 박동)
       일시정지/정지            → ended   (파란 점등, 행 강조는 그대로 남는다)
     ⚠ 원본은 일시정지에도 playingChanged("") 를 보내 ended 가 된다 (_on_state). */
  const [pip, setPip] = useState<
    { path: string; state: "loading" | "playing" | "ended" | "" }>({ path: "", state: "" });

  const bodyRef = useRef<HTMLDivElement>(null);
  const promptInputRef = useRef<HTMLInputElement | null>(null);
  /* 원본 config "folder_display_names" — 표시명만 바꾸고 실제 폴더는 건드리지 않는다.
     key 는 원본과 같은 normpath.rstrip("\\/").lower() 형식이다. */
  const [displayNames, setDisplayNames] = useState<Record<string, string>>({});
  /* 블랙리스트 추가 후 패널을 펼치는 신호 (원본 add_paths 의 _set_expanded(True)) */
  const [blacklistOpenTick, setBlacklistOpenTick] = useState(0);
  /* 확인 대화상자 (원본 QMessageBox.question 대체) */
  const [confirm, setConfirm] = useState<
    { title: string; text: string; onYes: () => void; onNo?: () => void;
      /* QMessageBox.information 처럼 [확인] 하나만 필요한 안내창 */
      info?: boolean; defaultNo?: boolean } | null>(null);
  /* 원본 QInputDialog 대체 — 제목/라벨/기본값을 원본과 같게 쓴다.
       이름 변경     : ("이름 변경", "새 이름:\n{path}", 현재 표시명)
       블랙리스트 추가: ("블랙리스트 추가", "설명 (선택):\n{path}", "") */
  const [prompt, setPrompt] = useState<
    { title: string; label: string; value: string;
      onOk: (v: string) => void; onCancel?: () => void } | null>(null);
  /* 상태바에 남기는 일회성 메시지 — 원본 _set_status */
  const [statusOverride, setStatusOverride] = useState("");
  /* 드래그가 셸 확인에 걸려 막혔을 때 사유를 상태바로 받는다 (drag.ts) */
  useEffect(() => { setDragStatusSink(setStatusOverride); }, []);
  /* 원본 히스토리 버튼 문구 규칙 — 한 번이라도 토글했으면 닫힘 문구가 바뀐다 */
  const [historyToggled, setHistoryToggled] = useState(false);
  /* 원본 _check_fts_stale / _prompt_fts_repair — 색인 어긋남이면 헤더에 복구 버튼을
     띄우고 800ms 뒤 세션당 한 번 안내 팝업을 띄운다. */
  const [ftsStale, setFtsStale] = useState(false);
  /* 원본의 IndexBusy + background meta thread 상태. 전경 스캔과 자동 메타 분석은
     같은 index.db writer 를 절대 동시에 잡지 않는다. */
  const foregroundAdminRef = useRef(false);
  const backgroundAdminRef = useRef<Promise<void> | null>(null);
  const backgroundCancelReasonRef = useRef<"" | "idle" | "playback" | "foreground" | "paused">("");
  /* ── 사용자가 직접 멈춘 메타 분석 (조사 I04) ──────────────────────────────
     원본은 상태바에 일시정지/재개 버튼이 있고 (main_window.py:4746),
     멈춰 두면 **직접 재개하거나 5분간 앱을 안 쓸 때만** 다시 시작한다
     (_check_idle_and_index, main_window.py:4123).
     PoC 에는 이 버튼도 상태도 없어서, 원본에서 하던 "지금은 분석을 멈춰 두고
     작업한다" 를 할 수 없었다 (취소를 눌러도 2초 타이머가 곧 다시 시작한다). */
  const metaPausedRef = useRef(false);
  const [metaPaused, setMetaPaused] = useState(false);
  const META_AUTO_RESUME_MS = 300_000;   /* 원본 5분 */
  const lastInputAtRef = useRef(Date.now());
  const startupMetaAttemptedRef = useRef(false);
  const [backgroundMetaActive, setBackgroundMetaActive] = useState(false);
  /* ── 2단계 세션 누적 ─────────────────────────────────────────────────────
     백그라운드 메타 분석은 앱 사용/재생 양보로 **조각으로 나뉘어 재시작**되고,
     원본 update_metadata_background 가 매번 새 IndexProgress 를 만든다. 그래서
     event.indexed 는 **이번 조각**만 센다.
     세션 분모(event.total)와 세션 누적(event.meta_done)은 파이썬
     _phase2_metadata 가 index_meta.meta_session_total 을 읽어 채운다 —
     **앱을 강제 종료하고 다시 켜도 처음 기준이 이어진다** (사용자 결정 2026-09-07).
     ⚠ 여기서 다시 셈하지 말 것. 예전에 JS 로 따로 누적했다가 파이썬이 주는 값과
       두 출처가 갈렸다. 단일 출처는 파이썬이다. */
  const [metaRemaining, setMetaRemaining] = useState<number | null>(null);
  const [foregroundAdminActive, setForegroundAdminActive] = useState(false);
  const foregroundCancelRequestedRef = useRef(false);
  /* ── 취소 직후 정책 (조사 I02) ────────────────────────────────────────────
     원본 _cancel_index (main_window.py:6575) 는 취소를 누른 순간
       · 진행 폴링을 멈추고
       · 게이지를 "취소됨 — 진행 보관됨" 으로 **고정**하고
       · 취소 버튼을 비활성화한다
     그리고 _cancelling / _meta_cancelling 플래그로 **뒤늦게 도착하는 진행 신호를
     전부 무시**한다. PoC 는 요청만 보내고 화면은 계속 갱신돼, 취소가 안 먹은 것처럼
     "분석 중" 이나 "완료 100%" 로 되돌아가고 취소 버튼도 계속 눌렸다. */
  const cancellingRef = useRef(false);
  const [cancelling, setCancelling] = useState(false);
  const markCancelling = (on: boolean) => {
    cancellingRef.current = on;
    setCancelling(on);
  };
  const [startupMaintenanceDone, setStartupMaintenanceDone] = useState(false);
  const [pruneSignal, setPruneSignal] = useState<{ id: number; paths: string[] } | null>(null);
  /* ── 인덱싱 진행 표시 (원본 indexing_badge + progress_bar, main_window.py:4706/4718) ──
     원본은 같은 작업을 네 곳에 동시에 보여준다: 중앙 로더 게이지 / 상태바 진행바 /
     상태바 배지(깜빡임) / 상태 문구. PoC 에는 문구만 있었다. 위치는 원본과 같게
     (배지=상태바 좌측, 진행바=상태바 우측) 두고 표현만 다듬는다. */
  const [indexUI, setIndexUI] = useState<{
    badge: "" | "index" | "meta"; pct: number; label: string;
  }>({ badge: "", pct: -1, label: "" });
  /* 2단계 완료 팝업 조건/요약 (원본 _await_meta_completion + _rescan_summary) */
  const awaitMetaRef = useRef<{ scanned: number; newCount: number } | null>(null);
  /* 1단계 중 주기적 카운트 갱신 (원본은 5초마다 트리 카운트만 부분 갱신) */
  const lastCountRefreshRef = useRef(0);

  /* ── 전체 갱신 합치기 ────────────────────────────────────────────────────────
     이 갱신은 루트 카운트(158만 행 LIKE 스캔 ×N)와 폴더 트리(전체 스캔)를 함께 돌려
     **웜 상태에서도 3~7초** 걸린다. 그동안 디스크를 독점해서, 겹치는 검색이
     실측 6.2초까지 밀렸다 (`SEARCH 6194ms rows=196` — 평소 웜 3~7ms).
     그래서 (1) 진행 중이면 그 약속을 공유하고, (2) 끝난 직후 1.5초는 다시 돌지 않는다.
     연달아 들어오는 같은 갱신 요청을 한 번으로 접는다. */
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const refreshDoneAt = useRef(0);
  /**
   * @param force **데이터를 방금 바꾼 뒤**에는 반드시 true 로 부른다.
   *
   * ⚠ force 없이 부르면 위 두 규칙 때문에 **조용히 무시될 수 있다.**
   *   실측 2026-09-08 (사용자 신고): 미완료 항목을 "필터된 전체 제거" 로 지웠는데
   *   [현재 라이브러리]의 실패 개수가 195 로 그대로 남고 메인 화면 표시등도
   *   안 바뀌었다. 지우기 직전에 다른 갱신이 돌았어서 1.5초 규칙에 걸려 버려진
   *   것이다 — 아무 오류도 안 보여서 원인을 찾기 어려웠다.
   *   진행 중인 갱신에 편승하는 것도 위험하다: 그 갱신은 **지우기 전에 시작**해서
   *   옛 숫자를 읽어 온다. 그래서 force 는 그것을 기다린 뒤 새로 한 번 더 돈다.
   */
  const refreshIndexState = async (force = false): Promise<void> => {
    if (!force) {
      if (refreshInFlight.current) return refreshInFlight.current;
      if (Date.now() - refreshDoneAt.current < 1500) return;
    } else if (refreshInFlight.current) {
      /* 옛 숫자를 읽고 있는 갱신 — 끝나기를 기다린 뒤 새로 돈다 */
      try { await refreshInFlight.current; } catch { /* 무시 */ }
    }
    const task = (async () => { await doRefreshIndexState(); })();
    refreshInFlight.current = task;
    try {
      await task;
    } finally {
      refreshInFlight.current = null;
      refreshDoneAt.current = Date.now();
    }
  };

  const doRefreshIndexState = async () => {
    const [basic, roots, tree, count, blacklist, stale] = await Promise.all([
      loadLibraryRootsBasic(), loadLibraryRoots(), loadFolderTree(),
      loadIndexedCount(), loadBlacklistPaths(),
      /* ⚠ 색인 어긋남은 **매번 실제 플래그를 다시 읽어야 한다** (조사 I06).
         원본은 인덱싱이 끝나거나 수리가 끝날 때마다 _check_fts_stale 로 다시
         읽는다 (main_window.py:5695). PoC 는 앱을 켤 때 한 번만 읽어서,
           · 쓰다가 새로 생긴 어긋남은 [검색 정리] 버튼이 아예 안 떴고,
           · 수리가 "호출은 성공" 했지만 불일치가 남은 경우에도 버튼을 감췄다
             (원본 repair_fts 는 consistent=False 여도 success=True 를 준다). */
      loadFtsStale(),
    ]);
    if (basic) setLibraryRoots(basic);
    if (roots) setLibraryRoots(roots);
    if (tree) setFolderTree(tree);
    if (count !== null) setIndexedCount(count);
    if (blacklist) setBlacklistRows(blacklist);
    setFtsStale(Boolean(stale));
    lastQuerySig.current = "";
    if (searchReq) setSearchReq({ ...searchReq });
    else {
      const loaded = await loadInitialRows(config.searchLimit);
      if (loaded) setRows(loaded);
    }
  };

  /* op "index" 는 대상마다 결과를 배열로 돌려준다 → 재귀 합산 */
  const sumProgressNums = (value: unknown): { scanned: number; newCount: number } => {
    if (Array.isArray(value)) {
      return value.reduce((acc, v) => {
        const got = sumProgressNums(v);
        return { scanned: acc.scanned + got.scanned, newCount: acc.newCount + got.newCount };
      }, { scanned: 0, newCount: 0 });
    }
    if (!value || typeof value !== "object") return { scanned: 0, newCount: 0 };
    const obj = value as Record<string, unknown>;
    let scanned = Number(obj.scanned || 0);
    let newCount = Number(obj.new_count || 0);
    for (const v of Object.values(obj)) {
      if (v && typeof v === "object") {
        const got = sumProgressNums(v);
        scanned += got.scanned;
        newCount += got.newCount;
      }
    }
    return { scanned, newCount };
  };

  const collectOrphanIds = (value: unknown): string[] => {
    if (Array.isArray(value)) return value.flatMap(collectOrphanIds);
    if (!value || typeof value !== "object") return [];
    const obj = value as Record<string, unknown>;
    const own = Array.isArray(obj.orphan_ids) ? obj.orphan_ids.map(String) : [];
    return [...own, ...Object.values(obj).flatMap(collectOrphanIds)];
  };

  const promptOrphanRestore = (ids: string[]) => {
    const unique = [...new Set(ids.filter(Boolean))];
    if (!unique.length) return;
    /* 가드된 실행기를 쓴다 — 라이브러리 제거가 끝나면 백그라운드 메타가 재개되고,
       그 위에 이 쓰기가 겹칠 수 있다 (중복 실행 가드 정책, 2026-09-07). */
    const keepHidden = () => { void runDialogAdmin({ op: "mark_dup_orphans", paths: unique }); };
    setConfirm({
      title: "중복 아님 — 복원 확인",
      text: `방금 작업으로 더 이상 중복이 아니게 된 사운드 ${unique.length.toLocaleString()}개가 있습니다.\n`
        + "(살아있던 사본이 사라져, 중복 검수로 숨겨둔 사본만 남은 소리들)\n\n"
        + "숨김을 해제해 검색에 다시 나오게 복원할까요?\n\n"
        + `[Yes] 복원 — ${unique.length.toLocaleString()}개가 [제외 관리] 목록에서 빠지고 검색·파일 브라우저에 다시 나옵니다.\n`
        + "[No] 유지 — 검색 제외를 그대로 두고 [제외 관리]에 [중복 아님 · 미복원]으로 표시합니다 (언제든 복원 가능).",
      defaultNo: true,
      onYes: () => { void runDialogAdmin({ op: "unhide_ids", paths: unique }).then(() => refreshIndexState(true)); },
      onNo: keepHidden,
    });
  };

  const pruneRemovedReferences = (paths: string[], purge: boolean) => {
    const roots = paths.map((p) => normPath(p).toLowerCase());
    const under = (value: string) => {
      const key = normPath(value).toLowerCase();
      return roots.some((root) => key === root || key.startsWith(root + "\\") || key.startsWith(root + "/"));
    };
    setConfig((cfg) => {
      const next: UserConfig = {
        ...cfg,
        favorites: cfg.favorites.filter((path) => !under(path)),
        userTabs: cfg.userTabs.map((tab) => ({
          ...tab,
          items: tab.items.filter((path) => !under(path)),
          ...(tab.excluded ? { excluded: tab.excluded.filter((path) => !under(path)) } : {}),
        })),
        lastSelection: cfg.lastSelection && under(cfg.lastSelection.path) ? null : cfg.lastSelection,
      };
      void saveUserConfig(next);
      return next;
    });
    setPruneSignal({ id: Date.now(), paths: [...paths] });
    if (purge) setSessionPlays((items) => items.filter((path) => !under(path)));
    if (current?.fullPath && under(current.fullPath)) {
      setPlaying(false); void audioBridge.stop(); setCurrent(null); setSelected(null);
    }
  };

  /* ── Space(재생/일시정지) 한 곳 ────────────────────────────────────────────
     원본 toggle_keyboard_for_path (player_widget.py:4269) 를 그대로 옮긴 것이다.
     원본은 Space 를 창 전체 단축키 하나로만 받아, 포커스가 결과 표 안이든 밖이든
     **똑같이** 동작했다. PoC 는 표 안 Space 를 따로 처리해 정책이 갈렸다 (조사 Q09).

     순서가 중요하다:
       ① 재생 중이면 무조건 일시정지 — 다른 파일이 선택돼 있어도 곡을 바꾸지 않는다.
          (Space 로 곡이 바뀌면 "일시정지" 를 기대한 사용자가 놀란다)
       ② 멈춘 상태에서 대상이 지금 파일과 다르면 그 파일을 재생한다.
       ③ 같은 파일이면 [처음부터 다시 재생] 설정에 따라 0으로 되돌리거나 이어서 재생.
     ⚠ 선택이 없어도 지금 열린 파일이 있으면 토글해야 한다 (원본과 같음). */
  const toggleKeyboardPlayback = (target: Row | null) => {
    if (playing) { setPlaying(false); return; }
    if (target && target.fullPath !== current?.fullPath) { playRow(target, true); return; }
    if (current) {
      if (config.playbackRestartFromZero) {
        setCmdSeek((v) => v + 1);
        audioBridge.seek(0);
      } else {
        /* ── '이어서 재생' 의 뜻 (사용자 지시 2026-09-14) ──────────────────────
           멈춘 그 자리에서 이어 간다. 다만 **이미 끝까지 간 뒤**라면 이어 갈 데가
           없으므로 처음으로 돌아가 재생한다.
           ⚠ 예전에는 끝에서 다시 누르면 **이전 위치로 되돌아갔다** (사용자 신고:
             "이건 이어서 재생이 아니지 않냐"). 화면이 들고 있던 옛 위치를 엔진이
             그대로 받아써서 생긴 일이다. 끝 근처면 0 으로 확실히 되돌린다. */
        const dur = current.dur || 0;
        const posSec = playedSecRef.current;
        if (dur > 0 && posSec >= dur - 0.25) {
          setCmdSeek((v) => v + 1);
          audioBridge.seek(0);
        }
      }
      setPlaying(true);
      return;
    }
    if (target) playRow(target, true);
  };

  /* ── 중복 실행 가드 (사용자 지시 2026-09-07) ──────────────────────────────
     DB 를 쓰는 작업은 한 번에 하나만 돈다. 두 갈래로 나눈다.
       ① 전경 작업이 이미 돌고 있으면 → **거절**. 진짜 중복 실행이다.
          (예전엔 foregroundAdminRef 를 설정만 하고 검사하지 않아, 라이브러리 추가와
           재스캔을 동시에 시작할 수 있었다.)
       ② 백그라운드 메타 분석(2단계)이 돌고 있으면 → **묻고 양보**. 2단계는 158만
          개면 3~6시간이라 전면 차단하면 라이브러리 관리가 몇 시간 막힌다.
          중단해도 진행은 DB(meta_extracted)에 보관되므로 나중에 이어서 된다.
     통과하면 호출부가 flags 를 세우고 백그라운드를 취소한 뒤 작업을 돈다. */
  const guardAdmin = async (what: string): Promise<boolean> => {
    if (foregroundAdminRef.current) {
      setStatusOverride(`이미 작업이 진행 중입니다 — ${what} 은(는) 끝난 뒤에 해주세요.`);
      return false;
    }
    if (!backgroundAdminRef.current) return true;
    return await new Promise<boolean>((resolve) => {
      setConfirm({
        title: "메타 분석 중",
        text: `지금 오디오 메타 분석(2단계)이 진행 중입니다.

${what} 을(를) 하려면 분석을 잠시 중단해야 합니다.
분석 진행은 보관되므로 작업이 끝나면 이어서 자동 재개됩니다.

중단하고 진행할까요?`,
        onYes: () => resolve(true),
        onNo: () => resolve(false),
      });
    });
  };

  const runIndexAdmin = async (key: string, message: string, req: AdminRequest) => {
    /* 자동 메타 분석만 양보한다. 사용자가 명시한 라이브러리 추가/재스캔은
       전경 작업이므로 재생 때문에 중단하지 않되, 기존 background writer 는 먼저 종료. */
    /* ⚠ 중간에 예외가 나면(브리지 시작 실패, 갱신 중 오류 등) 로더와 foreground
       플래그가 남아 로더가 사라지지 않고 백그라운드 메타가 영구히 재개되지 않는다
       → 정리는 finally 에서 반드시 수행한다. */
    if (!(await guardAdmin(message.replace(/\.\.\.$/, "")))) return false;
    foregroundAdminRef.current = true;
    foregroundCancelRequestedRef.current = false;
    /* 새 작업이 시작되면 취소 고정을 푼다 (원본도 _start_index 에서 _cancelling=False) */
    markCancelling(false);
    setForegroundAdminActive(true);
    /* 원본 _start_index: 메타 재시도(phase2_only)는 **중앙 로더를 생략**한다 —
       현황 다이얼로그 배너와 진행바로 충분하고, 모달 뒤의 큰 로더가 거슬린다. */
    const skipLoader = req.op === "retry_paths" || req.op === "retry_scope";
    if (!skipLoader) startLoader(key, message);
    setIndexUI({ badge: skipLoader ? "meta" : "index", pct: -1, label: message });
    lastCountRefreshRef.current = Date.now();
    setStatusOverride(message);
    try {
    if (backgroundAdminRef.current) {
      backgroundCancelReasonRef.current = "foreground";
      await cancelAdmin();
      await backgroundAdminRef.current;
    }
    const result = await runAdmin(req);
    if (result.success === false) {
      setStatusOverride(foregroundCancelRequestedRef.current
        ? "인덱싱 중지 — 이미 스캔된 파일은 보관됐습니다. 다시 갱신하면 이어서 진행합니다."
        : String(result.message || "작업에 실패했습니다"));
      /* ⚠ 취소·실패에도 **화면을 갱신해야 한다** (조사 I03).
         원본은 취소돼도 보관된 인덱스를 다시 열고 트리·검색·카운트를 갱신한다
         (main_window.py _on_index_done). 여기서 그냥 return 하면 DB 에는 진행분이
         들어갔는데 화면에는 안 나타나, 재시작하거나 다른 작업을 해야 보였다. */
      await refreshIndexState(true);
      return false;
    }
    await refreshIndexState(true);
      /* 원본 _on_index_done: 변경분이 있으면(new_count>0) **2단계 완료 시** 요약 팝업을
         한 번 더 띄운다. 변경이 없으면 띄우지 않는다 (_await_meta_completion). */
      if (req.op === "index" || req.op === "add_index") {
        /* 원본 _on_index_done 은 `new_count > 0` 일 때만 2단계 완료 팝업을 예약한다.
           그런데 원본 library_manager.py:809 의 빠른 스캔 경로는 new_count 를
           **주기적 진행 보고 시점에만** 갱신해서, 파일이 적은 폴더는 끝까지 0으로
           남는다 (실측: 새 폴더 추가 scanned=1 new=0 인데 2단계는 1건 분석).
           → 요약은 항상 예약하고, 팝업은 **2단계가 실제로 분석한 건수**나
             new_count 중 하나라도 있을 때 띄운다. */
        const nums = sumProgressNums(result);
        awaitMetaRef.current = { scanned: nums.scanned, newCount: nums.newCount };
      }
    const orphanIds = collectOrphanIds(result);
    setStatusOverride("");
    if (req.op === "index" || req.op === "add_index") {
      setConfirm({
        title: "1단계 완료",
        text: "파일 목록 작성이 완료되었습니다.\n지금부터 검색이 가능하며, 상세 오디오 분석(2단계)은 백그라운드에서 진행됩니다.",
        info: true,
        onYes: () => promptOrphanRestore(orphanIds),
      });
    } else promptOrphanRestore(orphanIds);
    return true;
    } catch (err) {
      setStatusOverride(String(err ?? "작업에 실패했습니다"));
      return false;
    } finally {
      stopLoader(key);
      foregroundAdminRef.current = false;
      setForegroundAdminActive(false);
      /* 원본은 완료 후 3초 뒤 진행바를 숨긴다 (곧 이어질 2단계 게이지와 껌뻑임 방지).
         2단계가 이어서 돌면 그쪽 progress 가 계속 갱신한다. */
      window.setTimeout(() => {
        setIndexUI((prev) => (prev.badge === "meta" ? prev : { badge: "", pct: -1, label: "" }));
      }, 3000);
    }
  };

  const runChangeDetection = async (path = "") => {
    if (!(await guardAdmin("변경 감지"))) return;
    foregroundAdminRef.current = true;
    foregroundCancelRequestedRef.current = false;
    setForegroundAdminActive(true);
    if (backgroundAdminRef.current) {
      backgroundCancelReasonRef.current = "foreground";
      await cancelAdmin();
      await backgroundAdminRef.current;
    }
    startLoader("detect", "변경 감지 중...");
    setStatusOverride("변경 감지 중 — DB 변경 없음, 통계만 산정...");
    const result = await runAdmin({ op: "detect", ...(path ? { path } : {}) });
    stopLoader("detect");
    foregroundAdminRef.current = false;
    setForegroundAdminActive(false);
    if (result.success === false) {
      setStatusOverride(foregroundCancelRequestedRef.current
        ? "변경 감지를 취소했습니다. 인덱스는 변경되지 않았습니다."
        : String(result.message || "변경 감지에 실패했습니다"));
      return;
    }
    const added = Number(result.added || 0);
    const updated = Number(result.updated || 0);
    const deleted = Number(result.deleted || 0);
    const scanned = Number(result.scanned || 0);
    const elapsed = Number(result.elapsed || 0);
    const scope = path ? (path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path) : "전체";
    setStatusOverride(String(result.message || ""));
    if (added + updated + deleted === 0) {
      setConfirm({
        title: "변경사항 없음",
        text: `[${scope}] 스캔 완료 (${elapsed.toFixed(1)}s)\n`
          + `총 ${scanned.toLocaleString()}개 파일을 확인했으나 변경된 항목이 없습니다.`,
        info: true,
        onYes: () => {},
      });
      return;
    }
    const samples = (label: string, value: unknown) => {
      const paths = Array.isArray(value) ? value.slice(0, 10).map(String) : [];
      return paths.length ? `\n\n${label} 예시 (${paths.length}개):\n${paths.join("\n")}` : "";
    };
    setConfirm({
      title: "변경사항 검토",
      text: `[${scope}] 변경 감지 결과\n\n`
        + `스캔 ${scanned.toLocaleString()}개 · 소요 ${elapsed.toFixed(1)}s\n\n`
        + `추가  +${added.toLocaleString()}\n수정  ~${updated.toLocaleString()}\n삭제  −${deleted.toLocaleString()}`
        + samples("추가", result.added_sample)
        + samples("수정", result.updated_sample)
        + samples("삭제", result.deleted_sample)
        + "\n\n이 변경사항으로 갱신을 진행할까요?",
      onYes: () => { void runIndexAdmin("index", "빠른 갱신 중...",
        { op: "index", ...(path ? { path } : {}) }); },
    });
  };

  const runDialogAdmin = async (req: AdminRequest): Promise<Record<string, unknown>> => {
    if (!(await guardAdmin(ADMIN_OP_LABEL[req.op] ?? "이 작업"))) {
      return { success: false, message: "다른 작업이 진행 중입니다" };
    }
    foregroundAdminRef.current = true;
    foregroundCancelRequestedRef.current = false;
    setForegroundAdminActive(true);
    if (backgroundAdminRef.current) {
      backgroundCancelReasonRef.current = "foreground";
      await cancelAdmin();
      await backgroundAdminRef.current;
    }
    const result = await runAdmin(req);
    foregroundAdminRef.current = false;
    setForegroundAdminActive(false);
    /* ⚠ 예전에는 여기서 `await refreshIndexState()` 를 했다. 그 갱신이 루트 카운트 +
       폴더 트리 재스캔이라 3~7초가 걸리고, 그동안 호출한 대화상자는 **아무 피드백도
       못 받는다** — 사용자는 눌린 줄 모르고 다시 누른다 (신고: "제거해도 아무 동작
       없음 / 재시도 피드백 없음"). 결과를 먼저 돌려주고 갱신은 뒤에서 돌린다. */
    void refreshIndexState();
    promptOrphanRestore(collectOrphanIds(result));
    return result;
  };

  /* 원본 시작 순서: 메타 파서 버전이 바뀌었으면 과거 실패분을 한 번만 대기열로
     되돌린 뒤 Phase2를 시작한다. 정확검색 단어색인은 2.5초 후 조용히 확인하고,
     다른 writer가 사용 중이면 10초 뒤 다시 시도한다. */
  useEffect(() => {
    let alive = true;
    let termTimer = 0;
    let upgradeTimer = 0;
    /* ⚠ 파서 업그레이드는 Phase2(백그라운드 메타)보다 **먼저** 끝나야 한다.
       예전 코드는 실패해도 그대로 maintenance 완료로 넘겨 업그레이드가 없던 일이
       됐고, promise 가 거부되면 완료 플래그가 아예 안 세워져 메타 분석이 영구히
       시작되지 않았다.
         · 다른 writer 사용 중(이미 인덱싱) → 10초 뒤 재시도 (단어색인과 같은 정책)
         · 그 밖의 실패 → 사유를 알리고 통과 (원본도 예외를 남기고 계속 진행한다) */
    /* ⚠ 여기는 중복 실행 가드(guardAdmin)를 걸지 않는다. 시작 정비라
       startupMaintenanceDone 전이고, 백그라운드 메타는 그 플래그가 서기 전엔 시작하지
       않는다 → 겹칠 대상이 없다. 게다가 사용자 행동이 아니라 확인창을 띄울 자리도
       아니다. 다른 writer 와 겹치면 "이미 인덱싱" 거절을 받아 아래에서 재시도한다. */
    const runParserUpgrade = async () => {
      if (!alive) return;
      try {
        const result = await runAdmin({ op: "parser_upgrade" });
        if (!alive) return;
        if (result.success === false) {
          if (String(result.message || "").includes("이미 인덱싱")) {
            upgradeTimer = window.setTimeout(runParserUpgrade, 10_000);
            return;
          }
          setStatusOverride(String(result.message || "메타 분석 기능 업그레이드에 실패했습니다"));
        } else {
          const reset = Number(result.reset || 0);
          const restored = Number(result.restored || 0);
          if (reset > 0) {
            setStatusOverride(`메타 분석 기능이 개선됐습니다 — 이전 실패 ${reset.toLocaleString()}개를 다시 분석합니다`
              + (restored > 0 ? ` (제거했던 ${restored.toLocaleString()}개 복원)` : ""));
            await refreshIndexState(true);
          }
        }
      } catch {
        /* 브리지 자체가 실패 — 이번 실행에서는 넘어가고 다음 실행에서 다시 시도한다 */
      }
      if (alive) setStartupMaintenanceDone(true);
    };
    void runParserUpgrade();
    /* ⚠ 위 runParserUpgrade 와 같은 이유로 가드 없음 (시작 정비, 재시도 자체 처리). */
    const ensureTerms = async () => {
      if (!alive) return;
      const result = await runAdmin({ op: "ensure_term_index" });
      if (alive && result.success === false && String(result.message || "").includes("이미 인덱싱")) {
        termTimer = window.setTimeout(ensureTerms, 10_000);
      }
    };
    termTimer = window.setTimeout(ensureTerms, 2500);
    return () => {
      alive = false;
      window.clearTimeout(termTimer);
      window.clearTimeout(upgradeTimer);
    };
  }, []);

  const addLibrary = (picked: string) => {
    const target = normPath(picked);
    /* 원본 manager.get_roots() 기준으로 비교한다 — 실제 등록된 루트여야 한다
       (PoC 가 더미 목록 LIBS 를 보고 있어 판정이 틀렸다). */
    const existing = [...libraryRoots.map((l) => l.path), ...addedLibs].map(normPath);

    let parentRoot: string | null = null;
    for (const root of existing) {
      if (root.toLowerCase() === target.toLowerCase()) { parentRoot = root; break; }
      if (isChildPath(target, root) && (!parentRoot || root.length > parentRoot.length)) parentRoot = root;
    }
    if (parentRoot) {
      const same = parentRoot.toLowerCase() === target.toLowerCase();
      setConfirm({
        title: "이미 인덱싱된 폴더",
        text: same
          ? `[${target}]\n\n이미 등록된 라이브러리입니다.\n파일 내용이 많이 바뀌었다면 이 경로를 다시 스캔할 수 있습니다.\n\n다시 스캔할까요?`
          : `선택한 경로: ${target}\n상위 라이브러리: ${parentRoot}\n\n이미 상위 라이브러리 안에 포함된 폴더입니다.\n새 라이브러리 루트로 중복 등록하지 않고, 파일 브라우저에서는 상위 폴더 구조 안에 표시합니다.\n\n파일 내용이 많이 바뀌었다면 선택한 하위 경로만 다시 스캔할 수 있습니다.\n다시 스캔할까요?`,
        /* 원본은 여기서 재스캔만 시작하고 새 루트로 추가하지 않는다 */
        onYes: () => { void runIndexAdmin("index", `라이브러리 재스캔 중 — ${target}`,
          { op: "index", path: target }); },
        onNo: () => setStatusOverride("이미 인덱싱된 폴더라 새 라이브러리로 추가하지 않았습니다"),
      });
      return;
    }

    const children = existing.filter((root) => isChildPath(root, target));
    if (children.length) {
      const shown = children.slice(0, 12).map((c) => `  · ${c}`).join("\n");
      const more = children.length > 12 ? `\n  · ... 외 ${(children.length - 12).toLocaleString()}개` : "";
      setConfirm({
        title: "라이브러리 통합",
        text: `선택한 경로: ${target}\n\n이 경로 아래에 이미 등록된 하위 라이브러리가 있습니다:\n\n${shown}${more}\n\n하위 라이브러리는 별도 루트에서 빼고, 선택한 상위 폴더를 라이브러리로 통합합니다.\n기존 인덱스 데이터는 유지되며, 파일 브라우저에서는 상위 폴더 구조 안에 표시됩니다.\n\n상위 폴더를 다시 스캔할까요?`,
        onYes: () => { void runIndexAdmin("index", `라이브러리 통합 및 스캔 중 — ${target}`,
          { op: "add_index", path: target, paths: children }); },
        onNo: () => setStatusOverride("하위 라이브러리와 겹쳐 새 라이브러리로 추가하지 않았습니다"),
      });
      return;
    }

    setConfirm({
      title: "라이브러리 추가",  /* 신규 등록 분기 */
      text: `선택한 경로: ${target}\n\n지금 이 폴더를 스캔하여 라이브러리에 등록할까요?\n(스캔하지 않으면 목록에 추가되지 않습니다.)`,
    onYes: () => { void runIndexAdmin("index", `라이브러리 스캔 중 — ${target}`,
      { op: "add_index", path: target }); },
    });
  };

  /* 원본 7208: 선택 폴더가 2곳 이상이면 "[폴더 N개: 첫경로 외 M곳]",
     1곳이면 "[기준: 경로]", 없으면 접미사 없음. */
  const scopeSuffix = scopePrefixes.length > 1
    ? ` [폴더 ${scopePrefixes.length.toLocaleString()}개: ${scopePrefixes[0]} 외 ${scopePrefixes.length - 1}곳]`
    : scopePrefixes.length === 1
      ? ` [기준: ${scopePrefixes[0]}]`
      : "";
  const visibleRows = useMemo(() => rows.slice(0, config.searchLimit), [rows, config.searchLimit]);
  /* 원본 _update_meta_indicator 에 넘기는 값은 count_incomplete_total() —
     **대기(pending) + 실패(failed)** 합계다 (main_window.py:5930 total_incomplete).
     PoC 는 pending 만 더해 실패만 남은 상태에서 표시가 안 떴다. */
  const pendingTotal = libraryRoots.reduce(
    (sum, lib) => sum + Math.max(0, lib.pending ?? 0) + Math.max(0, lib.failed ?? 0), 0);
  const metadataPendingTotal = libraryRoots.reduce(
    (sum, lib) => sum + Math.max(0, lib.pending ?? 0), 0);

  const statusText = statusOverride || `결과 ${visibleRows.length.toLocaleString()}개${scopeSuffix}`;

  /* 단축키 → 플레이어 동작 트리거 (원본은 QShortcut 이 player 메서드 직접 호출) */
  const [cmdLoop, setCmdLoop] = useState(0);
  const [cmdSeg, setCmdSeg] = useState(0);
  const [cmdSeek, setCmdSeek] = useState(0);
  /* 바이노럴 토글 단축키 (기본 B) — 다른 커맨드와 같은 카운터 방식 */
  const [cmdBinaural, setCmdBinaural] = useState(0);

  /* 원본은 재생 시작 시 _record_history(path) 로 history.json 에 기록한다.
     PoC 는 원본 파일을 건드리지 않으므로 이 세션 재생분만 메모리에 쌓아 드로어에 얹는다. */
  const [sessionPlays, setSessionPlays] = useState<string[]>([]);
  const audioLoadedPath = useRef("");
  const audioLoadSeq = useRef(0);
  const playingRef = useRef(false);
  playingRef.current = playing;

  const startBackgroundMeta = () => {
    /* 사용자가 멈춰 뒀으면 어떤 경로로 불려도 시작하지 않는다 (조사 I04) */
    if (metaPausedRef.current) return;
    if (!startupMaintenanceDone || metadataPendingTotal <= 0 || foregroundAdminRef.current || backgroundAdminRef.current) return;
    if (playingRef.current && !focusIndex) return;
    backgroundCancelReasonRef.current = "";
    markCancelling(false);
    setBackgroundMetaActive(true);
    const task = (async () => {
      /* ⚠ 예외가 새어나가면 backgroundAdminRef 가 계속 '실행 중'으로 남아 메타 분석이
         영구히 재개되지 않고, 이 promise 를 await 하는 runIndexAdmin 까지 깨진다
         → 정리는 finally, 예외는 여기서 삼킨다 (다음 유휴에 다시 시도). */
      try {
        const result = await runAdmin({ op: "background_meta" });
        const reason = backgroundCancelReasonRef.current;
        const cancelled = reason !== "";
        if (!cancelled && result.success === false) {
          setStatusOverride(String(result.message || "메타 분석에 실패했습니다"));
        }
        /* ⚠ **재생 때문에** 양보해 멈춘 직후에는 트리·검색·카운트 전체 갱신을
           건너뛴다 (조사 Q06). 재생에 디스크를 내주려고 멈춘 직후에 158만 행을
           다시 읽으면 그게 오히려 재생을 끊는다. 원본도 이 경우만 따로 빼서
           표시만 '일시정지' 로 바꾸고 끝낸다 (_on_background_meta_done,
           main_window.py:5419). 화면은 재생이 끝나고 메타가 재개될 때 갱신된다.
           다른 사유(유휴 해제·전경 작업)는 그대로 갱신한다 — 전경 작업은 자기
           끝에서 또 갱신하지만 순서가 어긋나면 안 되므로 여기서도 남겨 둔다. */
        if (reason !== "playback") await refreshIndexState(true);
        if (!cancelled) {
          /* 원본 _on_background_meta_done (main_window.py:5449):
             상태/배지 정리 → 게이지 100% → 2.5초 후 숨김 →
             변경분이 있었으면 "갱신 완료" 요약 팝업. */
          setIndexUI({ badge: "", pct: 100, label: "2단계/2 · 메타 분석 완료 · 100%" });
          setStatusOverride("메타 분석 완료 — 갱신 완료.");
          window.setTimeout(() => setIndexUI({ badge: "", pct: -1, label: "" }), 2500);
          const summary = awaitMetaRef.current;
          const analyzedNow = Number((result as unknown as Record<string, unknown>).indexed || 0);
          if (summary && (summary.newCount > 0 || analyzedNow > 0)) {
            awaitMetaRef.current = null;
            const nums = result as unknown as Record<string, unknown>;
            const analyzed = Number(nums.indexed || 0);
            const errs = Number(nums.errors || 0);
            let hidden = 0;
            try {
              const got = await loadHidden("", 1);
              hidden = got ? got.total : 0;
            } catch { hidden = 0; }
            const lines = ["갱신이 끝났습니다 — 2단계 메타 분석까지 완료.", ""];
            if (summary.scanned) lines.push(`스캔한 파일: ${summary.scanned.toLocaleString()}개`);
            lines.push(`신규·변경: ${summary.newCount.toLocaleString()}개`);
            lines.push(`메타 분석: ${analyzed.toLocaleString()}개`);
            if (errs) lines.push(`분석 오류: ${errs.toLocaleString()}개`);
            if (hidden) lines.push(`검색 제외(중복 검수): ${hidden.toLocaleString()}개 유지됨`);
            setConfirm({
              title: "갱신 완료",
              text: lines.join("\n"),
              info: true,
              onYes: () => {},
            });
          }
        }
      } catch {
        /* 백그라운드라 사용자를 막지 않는다 */
      } finally {
        setBackgroundMetaActive(false);
        backgroundAdminRef.current = null;
      }
    })();
    backgroundAdminRef.current = task;
  };

  /* 원본 _pause_meta_analysis (main_window.py:4188) — 돌고 있으면 중단시키고
     '사용자 정지' 로 표시를 고정한다. */
  const pauseMetaAnalysis = () => {
    metaPausedRef.current = true;
    setMetaPaused(true);
    if (backgroundAdminRef.current) {
      backgroundCancelReasonRef.current = "paused";
      void cancelAdmin();
    }
    setStatusOverride("백그라운드 인덱싱 일시정지 — "
      + "재개 버튼 또는 5분간 앱 미사용 시 자동 재개");
  };

  /* 원본 _resume_meta_analysis (main_window.py:4204) — 수동 재개는 명시적 의도라
     **재생 양보 상태까지 함께 푼다** (안 그러면 양보 가드에 막혀 재개가 안 된다). */
  const resumeMetaAnalysis = (reason = "") => {
    if (!metaPausedRef.current) return;
    metaPausedRef.current = false;
    setMetaPaused(false);
    backgroundCancelReasonRef.current = "";
    setStatusOverride("백그라운드 인덱싱 재개" + (reason ? ` (${reason})` : ""));
    startBackgroundMeta();
  };

  /* 원본 eventFilter: 클릭·키 입력·휠만 사용자 활동으로 본다. 단순 마우스 이동은
     idle 해제로 보지 않는다. Idle 정책에서 분석 중 입력이 오면 즉시 양보한다. */
  useEffect(() => {
    const activity = () => {
      lastInputAtRef.current = Date.now();
      if (config.indexOnIdle && backgroundAdminRef.current) {
        backgroundCancelReasonRef.current = "idle";
        void cancelAdmin();
        setStatusOverride("앱 사용 감지 — 백그라운드 메타 분석 일시정지 (30초 미사용 시 자동 재개)");
      }
    };
    window.addEventListener("mousedown", activity, true);
    window.addEventListener("keydown", activity, true);
    window.addEventListener("wheel", activity, true);
    return () => {
      window.removeEventListener("mousedown", activity, true);
      window.removeEventListener("keydown", activity, true);
      window.removeEventListener("wheel", activity, true);
    };
  }, [config.indexOnIdle]);

  /* 원본 시작 정책: 재실행 시 남은 Phase2 큐를 한 번 바로 이어서 처리한다.
     이후 Idle 설정이 켜져 있으면 사용자 입력 시 멈추고 30초 뒤에만 재개한다. */
  useEffect(() => {
    if (startupMetaAttemptedRef.current || metadataPendingTotal <= 0) return;
    startupMetaAttemptedRef.current = true;
    const timer = window.setTimeout(startBackgroundMeta, 1000);
    return () => window.clearTimeout(timer);
  }, [metadataPendingTotal, startupMaintenanceDone]);

  /* 원본 2초 idle 타이머. 즉시 분석 정책은 남은 큐를 곧바로 이어서 처리하고,
     Idle 정책은 마지막 의도적 입력 후 30초가 지나야 재개한다. */
  useEffect(() => {
    const timer = window.setInterval(() => {
      /* 사용자가 직접 멈춘 상태 — 5분간 앱을 안 쓰면 자동 재개한다 (조사 I04).
         Idle 정책(30초)과는 별개 조건이다 (원본 _check_idle_and_index 첫 분기). */
      if (metaPausedRef.current) {
        if (Date.now() - lastInputAtRef.current >= META_AUTO_RESUME_MS)
          resumeMetaAnalysis("5분간 앱 미사용 — 자동 재개");
        return;
      }
      if (metadataPendingTotal <= 0 || foregroundAdminRef.current || backgroundAdminRef.current) return;
      if (playingRef.current && !focusIndex) return;
      if (config.indexOnIdle && Date.now() - lastInputAtRef.current <= 30_000) return;
      startBackgroundMeta();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [metadataPendingTotal, config.indexOnIdle, focusIndex, startupMaintenanceDone]);

  /* 집중 모드가 아니면 재생 시작 즉시 자동 메타 분석만 양보한다. 재생이 멈춘 뒤
     1.5초 기다렸다 재개하여 여러 소리를 연속 미리듣기할 때 중단/재시작 churn을 막는다. */
  useEffect(() => {
    if (focusIndex) {
      if (metadataPendingTotal > 0) startBackgroundMeta();
      return;
    }
    if (playing) {
      if (backgroundAdminRef.current) {
        backgroundCancelReasonRef.current = "playback";
        void cancelAdmin();
      }
      return;
    }
    const timer = window.setTimeout(() => {
      void (async () => {
        if (backgroundAdminRef.current) await backgroundAdminRef.current;
        if (!playingRef.current && !foregroundAdminRef.current) startBackgroundMeta();
      })();
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [playing, focusIndex, metadataPendingTotal]);

  const playRow = (row: Row, explicitManual = false) => {
    const same = current?.id === row.id;
    /* 이미 재생 중인 같은 파일을 다시 재생하는 경우 — 엔진 상태가 PlayingState 에서
       바뀌지 않아 playback-state 이벤트가 오지 않는다. 그래서 pip 을 "loading"
       (빨강 박동)으로 두면 **거기서 못 빠져나온다** (사용자 보고: 액션라이트 빨강).
       원본도 같은 함정을 만나 강제 동기화로 해결했다:
         "같은 파일을 이미 재생 중에 다시 호출하면 state 가 안 바뀌어 _on_state 가
          안 불림 → pip 이 loading 에서 못 빠져나오는 stuck 버그. 강제 emit 으로 동기화"
         (player_widget.py:4521)
       PoC 도 같은 규칙: 이 경우에는 곧바로 "playing" 으로 둔다. */
    const wasPlayingSame = same && playingRef.current;
    if (same) {
      /* 원본 load_and_play (player_widget.py:4408): 같은 파일이면 재생 중이든
         아니든 **0에서 다시 시작**한다 (재생 중이면 _restart_from_zero, 아니면
         setPosition(0) 후 play). 예전에는 명시 재생(더블클릭)일 때만 되돌려서,
         끝까지 재생된 파일을 다시 누르면 끝 지점에서 play 만 불려 아무 일도
         일어나지 않았다. */
      setCmdSeek((v) => v + 1);
      audioBridge.restart();
    }
    setCurrent(row);
    setPlaying(true);
    if (row.fullPath)
      setPip({ path: row.fullPath, state: wasPlayingSame ? "playing" : "loading" });
    if (row.fullPath) {
      setSessionPlays((prev) => [...prev.filter((p) => p !== row.fullPath), row.fullPath!].slice(-100));
      void recordHistory(row.fullPath);
    }
  };

  useEffect(() => {
    const seq = ++audioLoadSeq.current;
    audioLoadedPath.current = "";
    if (!current) return;
    void audioBridge.load(current).then((ok) => {
      if (!ok || seq !== audioLoadSeq.current) return;
      audioLoadedPath.current = current.fullPath ?? "";
      if (playingRef.current) void audioBridge.play();
    });
  }, [current]);

  useEffect(() => {
    if (!current) return;
    if (playing) {
      if (audioLoadedPath.current === (current.fullPath ?? "")) void audioBridge.play();
    } else {
      void audioBridge.pause();
    }
  }, [playing]);

  useEffect(() => onAudioEvent((event) => {
    if (event.type === "playback-state") {
      /* 이전 파일의 상태 이벤트가 늦게 도착하면 새 파일의 재생 표시를 잘못 바꾼다
         → 이벤트에 실린 경로가 현재 파일과 다르면 버린다 (위치 이벤트와 같은 규칙) */
      const now = current?.fullPath ?? "";
      const key = (v: string) => v.replace(/\//g, "\\").toLowerCase();
      if (event.path && now && key(event.path) !== key(now)) return;
      const active = event.state === "PlayingState";
      if (active && current?.fullPath) setPip({ path: current.fullPath, state: "playing" });
      if (!active) setPip((prev) => prev.path ? { ...prev, state: "ended" } : prev);
    } else if (event.type === "error") {
      setPlaying(false);
      setStatusOverride(`재생 오류: ${event.message}`);
    }
  }), [current?.fullPath]);

  useEffect(() => { applyTheme(theme); }, [theme]);

  /* 원본 _start_index: 창 제목에 진행 상태를 넣는다 (작업 표시줄에서도 보인다).
     `SoundField — 인덱싱 중 [폴더]` / 끝나면 `SoundField` 로 복구. */
  useEffect(() => {
    const title = indexUI.badge === "index" ? "SoundField — 인덱싱 중"
      : indexUI.badge === "meta" ? "SoundField — 메타 분석 중"
      : "SoundField";
    void import("@tauri-apps/api/window")
      .then((m) => m.getCurrentWindow().setTitle(title))
      .catch(() => { /* 권한이 없으면 제목만 그대로 둔다 */ });
  }, [indexUI.badge]);

  useEffect(() => onIndexEvent((event) => {
    /* 취소를 누른 뒤에 오는 신호는 버린다 — 위 markCancelling 주석 참고 (I02) */
    if (cancellingRef.current) return;
    const folder = (event.current_file || event.current_root || "")
      .replace(/[\\/]+$/, "").split(/[\\/]/).slice(-1)[0] || "";
    if (event.phase === "scan") {
      /* 원본 _on_index_progress 의 1단계 세 갈래 (main_window.py:6802):
           ① fts_total>0  검색 색인 작성 — 실측 %
           ② phase1_total>0 재스캔 — 기존 개수를 분모로 실측 %
           ③ 첫 인덱싱 — 총 개수를 모르므로 % 불가, "검색 색인 작성부터 % 표시" */
      let pct = -1;
      let gauge = "";
      if (event.fts_total > 0) {
        pct = Math.min(100, Math.floor(event.fts_done * 100 / event.fts_total));
        gauge = `1단계/2 · 검색 색인 작성 · ${pct}% (${event.fts_done.toLocaleString()}/${event.fts_total.toLocaleString()})`;
      } else if (event.phase1_total > 0) {
        pct = Math.min(99, Math.floor(event.scanned * 100 / event.phase1_total));
        gauge = `1단계/2 · 파일 목록 작성 ${pct}% (${event.scanned.toLocaleString()}/${event.phase1_total.toLocaleString()})`;
      } else if (event.scanned === 0) {
        gauge = "1단계/2 · 스캔 시작... · 검색 색인 작성부터 % 표시";
      } else {
        gauge = `1단계/2 · ${event.scanned.toLocaleString()}개 발견 · 검색 색인 작성부터 % 표시`;
      }
      setIndexUI({ badge: "index", pct, label: gauge });
      setLoaderProgress(loaderKeyRef.current, gauge.replace("1단계/2 · ", ""), pct);
      setStatusOverride(
        (event.current_root ? `[${event.current_root.replace(/[\\/]+$/, "").split(/[\\/]/).slice(-1)[0]}] ` : "")
        + `파일 목록 작성 중 (1단계/2) — 신규 ${event.new_count.toLocaleString()}`
        + ` · 변경없음 ${event.skipped.toLocaleString()}`
        + " · 언제든 취소 가능 — 진행 보관됨."
        + (folder ? ` · 폴더: ${folder}` : ""));
      /* 원본은 5초마다 트리 카운트만 부분 갱신한다 (전체 트리 reload 는 1초 프리즈).
         PoC 는 부분 갱신 API 가 없어 **인덱싱된 개수**만 같은 주기로 갱신한다. */
      const now = Date.now();
      if (now - lastCountRefreshRef.current >= 5000) {
        lastCountRefreshRef.current = now;
        void loadIndexedCount().then((n) => { if (n !== null) setIndexedCount(n); });
      }
    } else if (event.phase === "meta") {
      if (event.total > 0) {
        /* 분자는 세션 누적(meta_done), 분모는 세션 분모(total). 파이썬이 준 값 그대로.
           meta_done 이 없는 옛 사이드카면 base_done + 이번 조각으로 보정한다. */
        const cumulative = Math.min(
          event.total,
          event.meta_done ?? ((event.base_done ?? 0) + event.indexed + event.errors));
        const remaining = Math.max(0, event.total - cumulative);
        setMetaRemaining(remaining);
        const pct = Math.min(100, Math.floor(cumulative * 100 / event.total));
        const eta = event.eta_seconds
          ? ` · 남은 시간: ${fmtRemaining(event.eta_seconds)}`
          : " · 남은 시간: 계산 중";
        setIndexUI({
          badge: "meta", pct,
          label: `2단계/2 · 분석 중 · ${pct}% (${cumulative.toLocaleString()}/${event.total.toLocaleString()})${eta}`,
        });
        setStatusOverride(`오디오 분석 중 (2단계/2) · ${cumulative.toLocaleString()}/${event.total.toLocaleString()}`
          + ` · 오류 ${event.errors.toLocaleString()} · 언제든 취소 가능 — 진행 보관됨.`
          + (folder ? ` · 폴더: ${folder}` : ""));
      } else {
        /* 분석할 항목 없음 = 세션 종료. 분모 캐시는 파이썬이 DB 에서 지운다
           (_phase2_metadata 의 pending 0 분기) — 여기서 따로 셈할 것이 없다. */
        setMetaRemaining(null);
        setIndexUI({ badge: "meta", pct: 100, label: "분석할 항목 없음" });
        setStatusOverride(event.message || "");
      }
    } else if (event.phase === "done") {
      /* ⚠ 이 이벤트는 **조각이 끝날 때마다** 온다 — 원본 update_metadata_background 가
         매 호출 끝에 PHASE_DONE 을 보내고, 백그라운드 메타는 앱 사용/재생 양보로
         중단·재시작을 반복한다. 그래서 여기서 분모를 리셋하면 다음 조각이 새 분모를
         잡아 **모수가 계속 줄어든다** (사용자 신고 2026-09-07 — 이 함정에 걸렸다).
         분모 수명은 파이썬이 DB 키로 관리한다. 여기서 손대지 말 것. */
      setIndexUI((prev) => ({ ...prev, pct: 100, label: "완료 · 100%" }));
      if (event.message) setStatusOverride(event.message);
    } else if (event.message) {
      setStatusOverride(event.message);
    }
  }), []);

  /* ── 폴더 표시이름 (원본 _on_folder_rename_request, main_window.py:6422) ──
     key = normpath(path).rstrip("\\/").lower(), 값은 strip 결과.
     원본은 빈 문자열도 그대로 저장한다 (라벨이 빈칸이 됨) → 같은 동작을 유지. */
  const renameFolder = (path: string, displayName: string) => {
    const key = normPath(path).replace(/[\\/]+$/, "").toLowerCase();
    setDisplayNames((prev) => {
      const next = { ...prev, [key]: displayName };
      setConfig((cfg) => {
        const merged = { ...cfg, folderDisplayNames: next };
        void saveUserConfig(merged);
        return merged;
      });
      return next;
    });
  };

  /* ── 블랙리스트 추가 (원본 blacklist_panel.add_paths, blacklist_panel.py:192) ──
     경로마다 순서대로 "설명 (선택)" 을 묻는다. 이미 등록된 경로(대소문자 무시)는
     묻지 않고 건너뛴다. 하나라도 추가되면 패널을 펼치고 목록을 새로 고친다. */
  const askBlacklist = (paths: string[]) => {
    const existing = new Set(blacklistRows.map((r) =>
      normPath(r.path).replace(/[\\/]+$/, "").toLowerCase()));
    const queue = paths
      .map((p) => normPath(p).replace(/[\\/]+$/, ""))
      .filter((p) => p && !existing.has(p.toLowerCase()));
    let added = 0;

    const step = (index: number) => {
      if (index >= queue.length) {
        if (added > 0) {
          setBlacklistOpenTick((t) => t + 1);
          notify("add", added > 1 ? `블랙리스트 ${added}개 추가` : "블랙리스트에 추가");
        }
        return;
      }
      const norm = queue[index];
      setPrompt({
        title: "블랙리스트 추가",
        label: `설명 (선택):\n${norm}`,
        value: "",
        onOk: (desc) => {
          void addBlacklistPath(norm, desc.trim()).then((ok) => {
            if (ok) {
              setBlacklistRows((rows) => rows.some((r) =>
                r.path.toLowerCase() === norm.toLowerCase())
                  ? rows
                  : [...rows, { path: norm, description: desc.trim() }]);
              added += 1;
              lastQuerySig.current = "";
              if (searchReq) setSearchReq({ ...searchReq });
            }
            step(index + 1);
          });
        },
        /* 원본: 취소(ok=False)면 그 경로만 건너뛰고 다음 경로로 넘어간다 */
        onCancel: () => step(index + 1),
      });
    };
    step(0);
  };

  /* ── 라이브러리 순서 (원본 folder_tree._move_root + _on_root_order_change_requested) ──
     원본은 트리에서 항목을 위/아래로 한 칸 옮긴 뒤 전체 루트 순서를
     rootOrderChangeRequested 로 보내고, manager.reorder_roots 가
     library_roots.sort_order 를 다시 매긴다. PoC 는 오버레이에 기록한다. */
  const reorderRoots = (order: string[]) => {
    const rank = new Map(order.map((p, i) => [normPath(p).toLowerCase(), i]));
    const key = (p: string) => rank.get(normPath(p).toLowerCase()) ?? Number.MAX_SAFE_INTEGER;
    setLibraryRoots((prev) => [...prev].sort((a, b) => key(a.path) - key(b.path)));
    setFolderTree((prev) => (prev.children
      ? { ...prev, children: [...prev.children].sort(
          (a, b) => key(a.path ?? a.label) - key(b.path ?? b.label)) }
      : prev));
    void setRootOrder(order);
  };

  const moveRoot = (path: string, delta: -1 | 1) => {
    const order = (folderTree.children ?? []).map((n) => n.path ?? n.label);
    const at = order.findIndex((p) =>
      normPath(p).toLowerCase() === normPath(path).toLowerCase());
    const to = at + delta;
    if (at < 0 || to < 0 || to >= order.length) return;
    const next = [...order];
    [next[at], next[to]] = [next[to], next[at]];
    reorderRoots(next);
  };

  /* 취소 — 원본 QInputDialog 의 ok=False 경로 (다중 대상이면 다음으로 넘어간다) */
  const closePrompt = () => {
    const cancel = prompt?.onCancel;
    setPrompt(null);
    cancel?.();
  };

  /* 원본 _save_config — 1초 디바운스로 디스크에 쓴다 (main_window.py:4967).
     Tauri 버전도 같은 설정 파일에 같은 간격으로 저장한다. */
  const saveTimer = useRef<number | null>(null);
  /* 아직 디스크에 못 쓴 설정 — 종료 직전에 이걸 마지막으로 써야 한다 (조사 C02) */
  const pendingSaveRef = useRef<UserConfig | null>(null);
  const patchConfig = (patch: Partial<UserConfig>) => {
    setConfig((cfg) => {
      const merged = { ...cfg, ...patch };
      pendingSaveRef.current = merged;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        saveTimer.current = null;
        pendingSaveRef.current = null;
        void saveUserConfig(merged);
      }, 1000);
      return merged;
    });
  };

  /* 원본 closeEvent 는 _actual_save_config 를 **즉시** 부른다 (main_window.py:7349).
     PoC 는 1초 뒤에 쓰기 때문에, 설정을 바꾸고 바로 창을 닫으면 그 변경이 사라졌다
     (조사 C02). 창이 닫히기 전에 남은 저장을 끝낸다. */
  const flushConfigSave = async () => {
    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const pending = pendingSaveRef.current;
    pendingSaveRef.current = null;
    if (pending) await saveUserConfig(pending);
  };

  /* ── 종료 확인 (사용자 결정 2026-09-16) ───────────────────────────────────
     예전에는 X 를 누르면 아무것도 묻지 않고 그대로 창이 사라졌다. 실수로 눌러도
     되돌릴 길이 없어서 **언제나** 한 번 묻기로 했다.
     조사한 동종 툴(BaseHead·Soundly·Soundminer)은 어느 쪽도 묻지 않는다. 대신
     종료가 싸도록 상태를 전부 복원해 두고, 방해될 때는 끄는 대신 항상 위/독 모드로
     비켜 둔다. 우리는 확인을 넣는 쪽을 골랐다.
     최소화·트레이로 내려가는 선택지는 두지 않는다 — X 는 종료 아니면 취소다. */
  const [exitAsk, setExitAsk] = useState(false);
  /* Enter 와 [종료] 버튼 클릭이 겹쳐도 종료 절차는 한 번만 돈다 */
  const exitingRef = useRef(false);

  /* 실제 종료 — 확인창에서 [종료] 를 눌렀을 때만 여기까지 온다.
     저장을 끝낸 뒤에 창을 없앤다 (destroy — 다시 onCloseRequested 를 돌지 않는다). */
  const doExit = async () => {
    if (exitingRef.current) return;
    exitingRef.current = true;
    let win: { destroy: () => Promise<void>; close: () => Promise<void> } | null = null;
    try {
      const mod = await import("@tauri-apps/api/window");
      win = mod.getCurrentWindow();
    } catch { /* 브라우저(개발)에는 창 제어가 없다 */ }
    /* 브리지/디스크 응답이 멈춰도 종료 버튼까지 영구히 묶이면 안 된다.
       짧게 저장을 기다린 뒤 창은 반드시 없앤다. */
    try {
      await Promise.race([
        flushConfigSave(),
        new Promise<void>((resolve) => window.setTimeout(resolve, 1200)),
      ]);
    } catch { /* 저장 실패로 종료를 막지 않는다 */ }
    if (!win) { exitingRef.current = false; return; }
    try { await win.destroy(); } catch {
      /* 구버전 capability로 실행한 개발 빌드에서도 최소한 close를 다시 시도한다.
         둘 다 실패하면 창이 그대로 남는다 — 잠금을 풀어 다시 누를 수 있게 한다.
         안 풀면 확인창만 뜬 채 [종료] 가 영영 안 먹는 상태가 된다. */
      const closed = await win.close().then(() => true).catch(() => false);
      if (!closed) exitingRef.current = false;
    }
  };

  /* 닫기 버튼·Alt+F4·작업표시줄 닫기 **모두** 이 자리를 지난다. 여기서는 확인창만
     띄우고, 실제 종료는 doExit 가 한다.
     ⚠ 새 종료 경로를 만들면 doExit 를 직접 부르지 말고 setExitAsk(true) 로 이
       확인창을 지나게 할 것 — 한쪽만 물으면 정책이 조용히 갈라진다. */
  useEffect(() => {
    let stop: (() => void) | null = null;
    void (async () => {
      try {
        const mod = await import("@tauri-apps/api/window");
        const win = mod.getCurrentWindow();
        stop = await win.onCloseRequested((event) => {
          event.preventDefault();
          setExitAsk(true);
        });
      } catch { /* 브라우저(개발)에서는 창 제어가 없다 */ }
    })();
    return () => { stop?.(); };
  }, []);

  /* 확인창이 떠 있는 동안 Esc = 취소, Enter = 종료.
     [종료] 에 autoFocus 를 주지만 포커스가 다른 데 있으면 Enter 가 안 먹는다.
     keydown 에서 막아 두면 버튼이 받는 기본 클릭도 함께 막혀 두 번 돌지 않는다
     (그래도 exitingRef 로 한 번 더 막는다). */
  useEffect(() => {
    if (!exitAsk) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setExitAsk(false); }
      else if (event.key === "Enter") { event.preventDefault(); void doExit(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [exitAsk]);

  const saveConfig = (next: UserConfig) => {
    /* 원본 _show_settings: SettingsDialog.accept() 의 반환 dict 를
       _config_data 에 merge 한 뒤 results/player/shortcuts 에 동시에 재적용한다.
       PoC 도 config 를 단일 소스로 두고, 시각 반영이 필요한 theme/compact 만
       별도 state 에 즉시 동기화한다. 쓰기는 아직 하지 않는다. */
    setConfig(next);
    setTheme(next.theme);
    setCompact(next.compactResults);
    setModal(null);
    /* 원본은 _save_config 로 ~/.soundfield_config.json 에 저장한다.
       Tauri 버전이 메인 앱이 되면서 같은 파일을 읽고 쓴다. */
    void saveUserConfig(next);
  };

  useEffect(() => {
    let alive = true;
    loadUserConfig().then((loaded) => {
      if (!alive || !loaded.loadedFromFile) return;
      setConfig(loaded);
      /* 지난번에 쓰던 표시 언어를 되돌린다 (i18n.ts). 설정을 읽기 전 짧은 순간은
         기본값인 한국어로 그려지는데, 그 구간은 스플래시가 가린다. */
      setLang(loaded.lang);
      setCompact(loaded.compactResults);
      setTheme(loaded.theme);
      setHistory(loaded.historyVisible);
      markSplash("config");
      setFocusIndex(loaded.indexFocusMode);
      setDisplayNames(loaded.folderDisplayNames);
      /* 분할 위치 복원 — 원본은 Qt splitter state 를 저장한다 (PoC 는 자체 키) */
      if (loaded.sideWidth > 0) setSideW(loaded.sideWidth);
      if (loaded.playerHeight > 0) setPlayerH(loaded.playerHeight);
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    /* 원본 refresh_async 와 같은 2단계: 목록 먼저, 카운트는 도착하면 채운다 */
    loadLibraryRootsBasic().then((loaded) => {
      if (alive && loaded) setLibraryRoots(loaded);
    });
    loadLibraryRoots().then((loaded) => {
      if (alive && loaded) setLibraryRoots(loaded);
    });
    loadBlacklistPaths().then((loaded) => {
      if (alive && loaded) setBlacklistRows(loaded);
    });
    loadFtsStale().then((stale) => {
      if (!alive || !stale) return;
      setFtsStale(true);
      /* 원본 재시작 정책은 덜 만들어진 1단계 검색 색인을 자동으로 마무리한 뒤
         Phase2로 넘어간다. 별도 수동 승인 팝업은 이 시작 경로에서 억제한다. */
      window.setTimeout(() => {
        /* 끝나면 runIndexAdmin 안의 갱신이 실제 플래그를 다시 읽어 표시를 정한다
           (조사 I06) — 호출 성공만으로 표시를 끄지 않는다. */
        void runIndexAdmin("fts", "검색 색인 마무리 중...", { op: "repair_fts" });
      }, 800);
    });
    loadIndexedCount().then((loaded) => {
      if (alive && loaded !== null) setIndexedCount(loaded);
    });
    /* 원본 _reload_tree_async 는 center_loader.start("tree", "폴더 트리 갱신 중...") 로
       입력을 막고 워커 결과가 오면 stop 한다 (158만 행 스캔 + 3.7만 폴더 트리). */
    /* ⚠ 개발 모드(StrictMode)는 이 효과를 두 번 실행한다 → 스플래시가 걷힌 뒤에
       "폴더 트리 갱신 중..." 로더가 한 번 더 떴다 (사용자 보고).
       ref 로 한 번만 돌게 막는다 (production 에서도 안전). */
    if (treeLoadedRef.current) return;
    treeLoadedRef.current = true;
    /* ⚠ 여기서 중앙 로더를 띄우면 스플래시가 걷힌 **뒤에** "폴더 트리 갱신 중" 이
       한 번 더 뜬다 (사용자 신고 2회). 스플래시가 이미 트리 준비까지 기다리므로
       (markSplash("tree") → sf_loaded) 초기 로딩에는 로더를 쓰지 않는다.
       어차피 같은 시간을 기다리는 것이라 스플래시 쪽에서 기다리는 게 맞다. */
    /* ⚠ `alive` 로 막지 않는다. 개발 모드(StrictMode)는 첫 실행 직후 정리를 돌려
       alive 를 false 로 만들고, 두 번째 실행은 위 가드에 막혀 건너뛴다 →
       로딩 결과가 버려져 **파일 브라우저가 빈 채로 남았다** (실측 증상).
       앱 수명 동안 한 번만 도는 로딩이라 결과는 그대로 적용해도 안전하다. */
    loadFolderTree().then((loaded) => {
      if (loaded) setFolderTree(loaded);
      stopLoader("tree");
      markSplash("tree");
    });
    return () => { alive = false; };
  }, []);

  /* 원본 _do_search 는 요청 signature 를 만들어 직전과 같으면 아예 쿼리를 보내지 않고,
     40ms 디바운스로 연속 입력을 한 프레임으로 묶는다 (main_window.py:7198).
     PoC 는 effect 의존성만으로 재실행돼 같은 질의를 여러 번 던지고 있었다. */
  const lastQuerySig = useRef("");

  useEffect(() => {
    if (searchReq !== null) return;   // 검색 요청이 있으면 초기 목록을 읽지 않는다
    const sig = `init:${config.searchLimit}`;
    if (lastQuerySig.current === sig) return;
    lastQuerySig.current = sig;
    let alive = true;
    loadInitialRows(config.searchLimit).then((loaded) => {
      markSplash("rows");
      if (alive && loaded) setRows(loaded);
    });
    return () => { alive = false; };
  }, [config.searchLimit, searchReq]);

  useEffect(() => {
    if (!searchReq) return;
    let alive = true;
    /* 원본은 40ms 디바운스 + signature 중복 차단 (그리고 400ms 넘게 걸릴 때만 "검색 중" 표시) */
    let busy = 0;
    const timer = window.setTimeout(() => {
      const sig = "q:" + JSON.stringify(searchReq);
      if (lastQuerySig.current === sig) return;
      lastQuerySig.current = sig;
      /* 원본 _search_busy_timer: 400ms 를 넘길 때만 "검색 중..." 을 띄운다
         (평소엔 깜빡임 없이 조용히, main_window.py:_on_search_busy_timeout) */
      busy = window.setTimeout(() => setStatusOverride("검색 중..."), 400);
      searchRows(searchReq).then(({ rows: loaded, error }) => {
        window.clearTimeout(busy);
        if (!alive) return;
        if (error) {
          /* 실패 — 사유를 알리고, **이 검색을 했다는 기록을 지운다** (조사 S02).
             그래야 같은 조건으로 다시 검색할 수 있다 (원본과 같은 정책).
             결과 목록은 건드리지 않는다 — 원본도 실패 시 목록을 비우지 않는다. */
          if (lastQuerySig.current === sig) lastQuerySig.current = "";
          setStatusOverride("오류: " + error);
          return;
        }
        if (loaded) setRows(loaded);
        /* 원본은 결과 메시지로 상태를 덮어쓴다 — 일회성 메시지를 지운다 */
        setStatusOverride("");
      });
    }, 40);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      window.clearTimeout(busy);
    };
  }, [searchReq]);

  useEffect(() => {
    const preventNativeContext = (event: MouseEvent) => event.preventDefault();
    window.addEventListener("contextmenu", preventNativeContext);
    return () => window.removeEventListener("contextmenu", preventNativeContext);
  }, []);

  /* 원본 _setup_shortcuts (main_window.py:5007) — config 의 shortcuts 를 그대로 쓴다.
     기본값: 재생/일시정지 Space · 처음으로 Home · 반복 재생 R · 세그먼트 S ·
     히스토리 H · 환경설정 Ctrl+P. 원본은 ApplicationShortcut 이지만 여기서는
     입력 중(텍스트 필드 포커스)에는 발동하지 않는다 — 검색어에 공백을 넣을 수
     있어야 하므로 PoC 가 유지해 온 동작을 그대로 둔다. */
  useEffect(() => {
    const matches = (event: KeyboardEvent, seq: string) => {
      const parts = seq.split("+").map((s) => s.trim().toLowerCase()).filter(Boolean);
      const key = parts[parts.length - 1];
      const needCtrl = parts.includes("ctrl");
      const needShift = parts.includes("shift");
      const needAlt = parts.includes("alt");
      if (event.ctrlKey !== needCtrl || event.shiftKey !== needShift || event.altKey !== needAlt) return false;
      const pressed = event.key.toLowerCase();
      if (key === "space") return event.code === "Space" || pressed === " " || pressed === "space";
      if (key === "home") return pressed === "home";
      if (key === "esc" || key === "escape") return pressed === "escape";
      return pressed === key;
    };

    const onKeyDown = (event: KeyboardEvent) => {
      const sc = config.shortcuts;
      /* 환경설정 열기/닫기만 입력 중에도 동작 (원본은 대화상자 자체에도 등록) */
      if (matches(event, sc.open_settings)) {
        event.preventDefault();
        /* ⚠ 작업 중에는 닫지 않는다 — 창 안 가드를 우회하던 경로다 (조사 M01).
           열기는 막지 않는다 (닫힌 상태에서 작업이 돌 일이 없다). */
        if (modalBusy && modal === "settings") return;
        setModal((value) => value === "settings" ? null : value === null ? "settings" : value);
        return;
      }
      const target = event.target as HTMLElement | null;
      const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement || Boolean(target?.isContentEditable);
      if (editing || event.repeat) return;
      /* 아래 위젯이 이미 처리한 키는 다시 처리하지 않는다.
         예: 히스토리 목록에 포커스가 있을 때 Home 은 목록 맨 위로 가는 키다.
         그걸 여기서 또 받으면 재생 위치까지 0 으로 돌아갔다.
         (결과표는 stopPropagation 까지 해서 여기 오지도 않는다) */
      if (event.defaultPrevented) return;
      /* 창(환경설정·현재 라이브러리·확인창 등)이 열려 있으면 재생 단축키를 받지
         않는다 (조사 Q10). 원본은 창이 열리면 뒤쪽 단축키가 아예 안 먹는다.
         확인창 버튼에 포커스가 있는데 R 을 눌러 뒤쪽 반복 재생이 바뀌면 안 된다.
         위의 Ctrl+P(창 열고 닫기)는 예외로 이미 처리했다. */
      if (modal || confirm || exitAsk || playerOverlay) return;

      if (matches(event, sc.play_pause)) {
        if (!selected && !current) return;
        event.preventDefault();
        toggleKeyboardPlayback(selected);
      } else if (matches(event, sc.toggle_history)) {
        event.preventDefault();
        /* 단축키로 여닫아도 상태를 저장한다 — 버튼과 같아야 한다 (조사 C02) */
        setHistory((value) => {
          patchConfig({ historyVisible: !value });
          return !value;
        });
      } else if (matches(event, sc.toggle_loop)) {
        event.preventDefault();
        setCmdLoop((v) => v + 1);
      } else if (matches(event, sc.toggle_segments)) {
        event.preventDefault();
        setCmdSeg((v) => v + 1);
      } else if (matches(event, sc.toggle_binaural)) {
        event.preventDefault();
        setCmdBinaural((v) => v + 1);
      } else if (matches(event, sc.seek_to_start)) {
        event.preventDefault();
        setCmdSeek((v) => v + 1);
        audioBridge.seek(0);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [current, selected, playing, config.shortcuts, config.playbackRestartFromZero,
      modal, modalBusy, confirm, exitAsk, playerOverlay]);

  const openCtx = (
    e: React.MouseEvent,
    label: string,
    opts?: { isRoot?: boolean; hasChildren?: boolean; tab?: string; count?: number;
             isFavorite?: boolean; canMoveUp?: boolean; canMoveDown?: boolean; kind?: "tree" | "tab";
             path?: string; paths?: string[];
             /* 트리/탭에서 처리하는 동작 (모두 접기 / 즐겨찾기 / 이 탭에서 삭제 /
                탭 이름 변경 / 탭 삭제) */
             actions?: { collapseAll?: () => void; favoriteToggle?: (add: boolean) => void;
                         removeFromTab?: () => void; rename?: () => void;
                         remove?: () => void } },
  ) => {
    e.preventDefault();
    if (opts?.kind === "tab") {
      /* 원본 _on_tab_context 는 사용자 탭에서만 뜨고 [이름 변경]/[탭 삭제] 뿐이다 */
      const rename = opts.actions?.rename;
      const remove = opts.actions?.remove;
      setCtx({
        kind: "tab", x: e.clientX, y: e.clientY, label,
        ...(rename && remove ? { actions: { rename, remove } } : {}),
      });
      return;
    }
    const { kind: _kind, actions, ...rest } = opts ?? {};
    void _kind;
    setCtx({
      kind: "tree", x: e.clientX, y: e.clientY, label, ...rest,
      ...(actions?.collapseAll && actions.favoriteToggle && actions.removeFromTab
        ? { actions: {
              collapseAll: actions.collapseAll,
              favoriteToggle: actions.favoriteToggle,
              removeFromTab: actions.removeFromTab,
            } }
        : {}),
    });
  };

  const openResultCtx = (e: React.MouseEvent, row: Row, selectedRows: Row[]) => {
    e.preventDefault();
    setCtx({
      kind: "result",
      x: e.clientX,
      y: e.clientY,
      label: row.name,
      path: row.fullPath ?? `${row.path}\\${row.name}`,
      selectedCount: selectedRows.length,
      /* 원본 sel_files — 우클릭 행이 선택 안에 있고 2개 이상일 때만 선택분 전체 */
      paths: selectedRows.map((r) => r.fullPath ?? (r.path + "\\" + r.name)),
    });
  };

  /* 분할선 드래그 — 좌(양수) / 우(음수) 방향 */
  const drag = (
    e: React.MouseEvent,
    axis: "x" | "y",
    sign: 1 | -1,
    start: number,
    min: number,
    max: number,
    set: (v: number) => void
  ) => {
    e.preventDefault();
    const p0 = axis === "x" ? e.clientX : e.clientY;
    const el = e.currentTarget as HTMLElement;
    el.classList.add("dragging");
    let last = start;
    const move = (ev: MouseEvent) => {
      const p = axis === "x" ? ev.clientX : ev.clientY;
      last = Math.max(min, Math.min(max, start + (p - p0) * sign));
      set(last);
    };
    const up = () => {
      el.classList.remove("dragging");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      /* 원본은 splitter state 를 config 에 저장한다 (놓을 때 _save_config).
         PoC 는 같은 목적의 자체 키로 저장한다. */
      patchConfig(axis === "x" ? { sideWidth: Math.round(last) }
                               : { playerHeight: Math.round(last) });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <div className="app">
      {/* 표시 언어 토글은 창 제어줄 안에 있다 (LangToggle 주석 참고).
          바뀐 값은 다른 설정과 같은 1초 디바운스로 저장된다. */}
      <WindowChrome onLangChange={(lang) => patchConfig({ lang })} />
      {/* 힌트글이 검색 필터 열과 세로로 맞도록 왼쪽 위치를 넘긴다.
          사이드바 폭 + 분할바(11px, app.css .resizer-v) + 검색 칸 왼쪽 여백(10px,
          .search padding-left) = 첫 필터의 [전체] 드롭다운 왼쪽 끝.
          ⚠ 이 셋 중 하나를 바꾸면 여기 계산도 같이 바꿔야 한다. */}
      <Header hintLeft={sideW + 11 + 10}
              onOpen={setModal}
              onLibraryPicked={addLibrary}
              ftsStale={ftsStale}
              /* 원본 _start_fts_rebuild (main_window.py:5765): 무엇을 하는
                 버튼인지 알리고 확인을 받은 뒤에야 시작한다. 문구도 원본 그대로다.
                 작업 자체는 원본과 같은 '누락분 채우기' 이고 중간에 멈출 수 있다. */
              onFtsRebuild={() => setConfirm({
                title: "검색 정리 — 검색 데이터 다시 만들기",
                text: "이 버튼은 '검색에 쓰는 데이터'가 실제 파일 목록과 어긋났을 때만 나타납니다.\n"
                  + "보통 인덱싱(파일 정리) 도중 앱이 꺼졌을 때 생기고, 이 상태에선 일부 "
                  + "파일이 검색에 안 나올 수 있습니다.\n\n"
                  + "지금 누르면 검색용 데이터에서 빠진 부분을 채워 파일 목록과 정확히 맞춥니다.\n"
                  + "라이브러리가 크면 몇 분~수십 분 걸릴 수 있고, 채우는 동안에는 "
                  + "앱이 잠깁니다(다른 작업 불가).\n\n"
                  + "지금 채울까요?",
                /* 표시를 끄는 판단은 runIndexAdmin 안의 갱신이 실제 플래그로 한다
                   (조사 I06). 취소했으면 어긋남 표시가 그대로 남아야 한다. */
                onYes: () => { void runIndexAdmin("fts", "검색 정리 중 — 빠진 부분 채우기...",
                  { op: "backfill_fts" }); },
              })}
              indexingActive={foregroundAdminActive || backgroundMetaActive}
              /* 취소 중에는 다시 누를 수 없다 (원본 cancel_btn.setEnabled(False)) */
              cancelDisabled={cancelling}
              onCancelIndex={() => {
                if (cancellingRef.current) return;
                if (foregroundAdminActive) foregroundCancelRequestedRef.current = true;
                else backgroundCancelReasonRef.current = "idle";
                markCancelling(true);
                /* 게이지·상태를 원본 문구로 고정한다 (I02) */
                setIndexUI({ badge: "", pct: 0, label: "취소됨 — 진행 보관됨" });
                setStatusOverride("취소됨 — 스캔된 파일은 보관됨. "
                  + "[전체 갱신] 을 다시 누르면 이어서 진행합니다.");
                void cancelAdmin();
              }}
              /* 원본 _do_quick_update (main_window.py:6484): 루트가 없으면
                 "라이브러리 없음" 안내, 있으면 확인 후 변경 감지를 시작한다. */
              onQuickUpdate={() => {
                if (!libraryRoots.length && !addedLibs.length) {
                  setConfirm({
                    title: "라이브러리 없음",
                    text: "[+ 라이브러리 추가] 로 먼저 등록하세요.",
                    info: true,
                    onYes: () => {},
                  });
                  return;
                }
                setConfirm({
                  title: "빠른 갱신",
                  text: "전체 라이브러리 의 최근 변경 사항을 감지합니다.\n"
                    + "(새 파일/삭제된 파일만 처리되어 빠릅니다. "
                    + "메타데이터는 변경된 파일에만 다시 추출됩니다.)\n\n"
                    + "진행할까요?",
                  onYes: () => { void runChangeDetection(); },
                });
              }}
              /* 원본 _do_full_update (main_window.py:6508) */
              onFullUpdate={() => {
                if (!libraryRoots.length && !addedLibs.length) {
                  setConfirm({
                    title: "라이브러리 없음",
                    text: "[+ 라이브러리 추가] 로 먼저 등록하세요.",
                    info: true,
                    onYes: () => {},
                  });
                  return;
                }
                setConfirm({
                  title: "전체 갱신",
                  text: "모든 라이브러리를 강제 재스캔합니다.\n"
                    + "(라이브러리 추가와 동일한 속도. 기존 메타데이터는 보존됨.)\n\n"
                    + "⚠ '제거'했던 항목도 디스크에 파일이 남아 있으면 다시 인덱스에 돌아옵니다.\n"
                    + "   (중복 숨김 처리한 파일은 그대로 숨김 유지 — 복원은 환경설정에서.)\n\n"
                    + "진행할까요?",
                  onYes: () => { void runIndexAdmin("index", "전체 갱신 중...",
                    { op: "index", force: true }); },
                });
              }} />

      <div className="body" ref={bodyRef} style={{ position: "relative" }}>
        <div className="sidebar" style={{ width: sideW, flex: `0 0 ${sideW}px` }}>
          <Sidebar onContext={openCtx} onBlacklistDetail={() => setModal("blacklist")}
                   onNotify={notify} blacklistH={config.blacklistExpandedH}
                   tree={folderTree}
                   rootCount={libraryRoots.length + addedLibs.length}
                   blacklistEntries={blacklistRows}
                   onScope={setScopePrefixes}
                   focusPath={focusPath} onFocusHandled={() => setFocusPath(null)}
                   /* 원본 ROLE_IS_ROOT — 등록된 라이브러리 경로 목록 */
                   rootPaths={libraryRoots.map((l) => l.path)}
                   displayNames={displayNames}
                   openBlacklistTick={blacklistOpenTick}
                   onReorderRoots={reorderRoots}
                   /* 원본 config favorites / tabs / last_selection */
                   initialFavorites={config.favorites}
                   initialTabs={config.userTabs}
                   initialSelection={config.lastSelection}
                   pruneSignal={pruneSignal}
                   onFavoritesChange={(favorites) => patchConfig({ favorites })}
                   onTabsChange={(userTabs) => patchConfig({ userTabs })}
                   onSelectionChange={(lastSelection) => patchConfig({ lastSelection })}
                   onBlacklistHeightCommit={(height) =>
                     patchConfig({ blacklistExpandedH: height })}
                   onBlacklistChanged={() => { void refreshIndexState(true); }} />
        </div>
        <div className="resizer-v" onMouseDown={(e) => drag(e, "x", 1, sideW, 200, 460, setSideW)}>
          <Grip />
        </div>

        <div className="center">
          <SearchPanel
            onStatus={() => setModal("status")}
            onContext={openCtx}
            addedLibs={addedLibs}
            libraries={libraryRoots}
            searchLimit={config.searchLimit}
            pathPrefixes={scopePrefixes}
            onSearchChange={setSearchReq}
            /* 원본 _restore_filters — 재시작 후 검색어/길이/SR/채널 복원 */
            initialFilters={config.loadedFromFile ? config.filters : null}
            onFiltersChange={(next) => patchConfig({ filters: next })}
            onHistory={() => {
              /* ⚠ 열 때도 저장해야 한다 (조사 C02). 원본은 저장할 때마다
                 history_panel.is_expanded() 의 **그 순간 실제 상태**를 적으므로,
                 열어 둔 채 종료하면 다음 실행에도 열려 있다. PoC 는 닫을 때만
                 적어서, 열어 둔 채 끄면 다음 실행에 닫혀 있었다. */
              setHistory((value) => {
                patchConfig({ historyVisible: !value });
                return !value;
              });
              setHistoryToggled(true);
            }}
            historyToggled={historyToggled}
            historyOpen={history}
          />

          <div className="results-history-wrap">
            <div className="results-slot">
              <ResultsTable
                compact={compact}
                doubleClickToPlay={config.doubleClickToPlay}
                savedColumns={config.columns}
                onColumnsChange={(columns) => patchConfig({ columns })}
                rows={visibleRows}
                playing={playing ? current?.id ?? null : null}
                pip={pip}
                onSelect={setSelected}
                /* 원클릭/더블클릭 여부는 ResultsTable 이 결정한다.
                   여기로 들어온 activate 는 실제 재생 요청이다. */
                onActivate={(r) => playRow(r)}
                onPlay={(r) => playRow(r, true)}
                /* 표 안 Space 도 표 밖과 **같은 함수**를 쓴다 (조사 Q09) */
                onToggleRow={(r) => toggleKeyboardPlayback(r)}
                onContext={openResultCtx}
                /* 원본 _on_external_drag_dropped: stop_on_drag 가 켜져 있으면
                   DAW 로 드롭이 성사된 순간 재생을 정지한다 (main_window.py:4328) */
                onDragDropped={() => {
                  if (config.stopOnDrag) { setPlaying(false); audioBridge.stop(); }
                }}
              />
            </div>
            <HistoryDrawer open={history} initialWidth={config.historyWidth} sessionPlays={sessionPlays}
                           onClear={() => setSessionPlays([])}
                           onWidthCommit={(width) => patchConfig({ historyWidth: Math.round(width) })}
                           onDragDropped={() => { if (config.stopOnDrag) { setPlaying(false); audioBridge.stop(); } }}
                           onPick={(path) => {
                             /* 원본 _on_history_pick: 히스토리 항목을 그대로 재생한다.
                                결과 목록에 있으면 그 행을, 없으면 경로만으로 재생한다. */
                             const hit = rows.find((r) => r.fullPath === path);
                             if (hit) playRow(hit, true);
                             else void loadRowByPath(path).then((loaded) => {
                               if (loaded) { playRow(loaded, true); return; }
                               /* 인덱스에 없어도 파일이 남아 있으면 재생한다 (조사 H02).
                                  메타는 비운 채로 넘긴다 — 길이·채널은 재생/파형이
                                  파일에서 직접 읽는다. */
                               const bare = rowFromPath(path);
                               if (bare) {
                                 playRow(bare, true);
                                 setStatusOverride("인덱스에 없는 파일 — 경로로 재생합니다");
                               } else {
                                 setStatusOverride("재생할 수 없는 형식입니다");
                               }
                             });
                           }} />
          </div>

          <div className="resizer-h" onMouseDown={(e) => drag(e, "y", -1, playerH, 180, 620, setPlayerH)}>
            <Grip />
          </div>

          <div style={{ height: playerH, flex: `0 0 ${playerH}px`, display: "flex", minHeight: 0 }}>
            <Player
              row={current}
              playing={playing}
              onToggle={() => setPlaying((v) => !v)}
              onStop={() => { setPlaying(false); audioBridge.stop(); }}
              onSeek={(positionMs) => audioBridge.seek(positionMs)}
              /* 원본 _on_drag_region: 드롭 성사 + stop_on_drag 면 재생만 멈춘다
                 (stop_playback_only — 선택 영역/파형은 그대로 남긴다) */
              onRegionDropped={() => {
                if (config.stopOnDrag) { setPlaying(false); void audioBridge.stop(); }
              }}
              onRate={(rate) => audioBridge.rate(rate)}
              onVolume={(gain) => audioBridge.volume(gain)}
              onPersist={(patch) => patchConfig(patch)}
              onBinaural={(enabled) => audioBridge.binaural(enabled)}
              /* ⚠ setPlaying(true) 만으로는 부족하다. 이미 true 면 상태가 안 바뀌어
                 [playing] 훅이 다시 돌지 않아 **재생 명령이 나가지 않는다.**
                 자연 종료 후에도 playing 은 true 로 남아 있어서 실제로 이 함정에
                 걸렸다 (세그먼트 헤더 클릭 무반응). 명령을 직접 보낸다. */
              onForcePlay={() => { setPlaying(true); void audioBridge.play(); }}
              /* ⚠ 설정 파일에서 **읽어 온 뒤에만** 넘긴다. config 초기값이
                 CONFIG_DEFAULTS(volumePos1k=750)라 그냥 넘기면 Player 의
                 일회성 복원 가드(volInitRef)가 첫 렌더에서 750 으로 소진되고,
                 나중에 도착한 저장값은 버려진다 — 저장한 페이더 위치가 한 번도
                 돌아오지 않았다 (실측 2026-09-18: 설정에 601(-9.95dB)이 있는데
                 매 실행이 volume=1.0 만 보냄). initialFilters 와 같은 방식. */
              initialVolPos={config.loadedFromFile ? config.volumePos1k : undefined}
              initialSpeedRate={config.speedRate}
              cmdToggleLoop={cmdLoop}
              cmdToggleSegments={cmdSeg}
              onOverlayChange={setPlayerOverlay}
              cmdSeekToStart={cmdSeek}
              cmdToggleBinaural={cmdBinaural}
              /* 채널 배치 판별 중 표시는 **바이노럴 버튼 안**에서 한다
                 (원형 로딩 + "채널 배치 판별 중"). 중앙 로더로 알리던 예전 방식은
                 입력을 막아 재생까지 끊어야 했고 흐름이 불쾌했다 (사용자 지시). */
            />
          </div>
        </div>

      </div>

      {/* 원본 상태바 (main_window.py:4705) 구성 순서
            좌: 인덱싱 배지(숨김) + 상태 메시지(stretch, 잘리면 툴팁에 전문)
            우(permanent): 진행바(숨김) → "인덱싱됨  N" → 메타 스피너/인디케이터(숨김)
               → 일시정지 버튼(숨김) → "인덱싱 집중" 체크박스
          검색 결과 메시지는 `결과 N개[기준: 경로]` 형식이다. */}
      <TooltipLayer />
      <div className="statusbar" data-tip={statusText}>
        {/* 원본 indexing_badge — 상태바 **좌측**, 깜빡이는 붉은 점 + 문구
            (main_window.py:4706 sb.addWidget). 여기서는 숨쉬는 점으로 다듬었다. */}
        {indexUI.badge && (
          <span className={"index-badge " + indexUI.badge}>
            {indexUI.badge === "index" ? "인덱싱 중" : "메타 분석 중"}
          </span>
        )}
        <span className="status-message">{statusText}</span>
        <span className="grow" />
        <span className="indexed-count">인덱싱됨&nbsp;&nbsp;{indexedCount.toLocaleString()}</span>
        {/* ── 메타 분석 인디케이터 (원본 _update_meta_indicator, main_window.py:5833) ──
            분석이 돌지 않아도 **미완료 잔량이 있으면** 이 자리에 표시가 남는다:
              index_on_idle 정책 ON → "분석 일시정지  N — 앱 사용 중"
              그 외                 → "미완료  N"   (둘 다 두 칸 공백, #ffa64d)
            PoC 는 인덱싱을 돌리지 않으므로 진행률(%)/점 애니메이션 분기는 나오지 않는다. */}
        {(metaRemaining ?? pendingTotal) > 0 && (
          <span className="meta-indicator">
            {metaPaused
              /* 사용자가 직접 멈춘 상태 (원본 "분석 일시정지 … — 사용자 정지") */
              ? `분석 일시정지  ${pendingTotal.toLocaleString()} — 사용자 정지`
              : backgroundMetaActive
              /* ⚠ 분석 중에는 **누적 계산에서 나온 남은 수**를 쓴다. 따로 조회한
                 pendingTotal 을 쓰면 진행바 모수와 어긋난다 (원본은
                 _update_meta_indicator(remaining) 로 같은 값을 넘긴다). */
              ? `메타 분석 중  ${(metaRemaining ?? pendingTotal).toLocaleString()}`
              : config.indexOnIdle
              ? `분석 일시정지  ${pendingTotal.toLocaleString()} — 앱 사용 중`
              : `미완료  ${pendingTotal.toLocaleString()}`}
          </span>
        )}
        {/* 원본 progress_bar — 상태바 **우측 고정**(addPermanentWidget), 폭 360~420,
            텍스트가 게이지 안에 표시된다 (main_window.py:4718). */}
        {indexUI.label && (
          <span className="index-gauge" data-tip={indexUI.label}>
            <span className={"index-gauge-fill" + (indexUI.pct < 0 ? " indet" : "")}
                  style={indexUI.pct >= 0 ? { width: `${indexUI.pct}%` } : undefined} />
            <span className="index-gauge-label">{indexUI.label}</span>
          </span>
        )}
        {/* 백그라운드 인덱싱 일시정지 / 재개 (원본 meta_pause_btn, main_window.py:4746).
            원본과 같이 **멈출 것이 있을 때만** 보인다 — 돌고 있거나 이미 멈춰 둔 상태.
            멈춤은 확인을 받고(원본 _on_meta_pause_clicked), 재개는 바로 한다. */}
        {(backgroundMetaActive || metaPaused) && (
          <button className={"meta-pause" + (metaPaused ? " paused" : "")}
                  aria-label={metaPaused ? "백그라운드 인덱싱 재개" : "백그라운드 인덱싱 일시정지"}
                  data-tip={metaPaused
                    ? "백그라운드 인덱싱 재개\n재개 조건: 클릭 또는 5분간 앱 미사용 시 자동 재개"
                    : "백그라운드 인덱싱 일시정지\n재개 조건: 재개 버튼 클릭 또는 5분간 앱 미사용"}
                  onClick={() => {
                    if (metaPaused) { resumeMetaAnalysis("사용자가 직접 재개"); return; }
                    setConfirm({
                      title: "백그라운드 인덱싱 일시정지",
                      text: "앱 사용 성능을 위해 백그라운드 메타 인덱싱을 잠시 정지할까요?\n\n"
                        + "재개 시점:\n"
                        + "  ▶ 상태바의 재개 버튼을 직접 클릭, 또는\n"
                        + "  • 5분간 앱을 사용하지 않으면 자동 재개",
                      onYes: pauseMetaAnalysis,
                    });
                  }}>
            {metaPaused
              ? <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <path d="M2 1 L9 5 L2 9 Z" fill="currentColor" />
                </svg>
              : <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                  <rect x="2" y="1" width="2.4" height="8" fill="currentColor" />
                  <rect x="5.6" y="1" width="2.4" height="8" fill="currentColor" />
                </svg>}
          </button>
        )}
        <label className="focus-index"
               data-tip={"체크: 재생 중에도 인덱싱을 100% 속도로 — 인덱싱 빠름, 재생이 끊길 수 있음\n해제: 재생 중엔 인덱싱을 잠시 양보 — 재생이 매끄러움 (기본)"}>
          <span className={"check" + (focusIndex ? " on" : "")}
                role="checkbox" aria-checked={focusIndex}
                /* 원본 _on_focus_index_toggled (main_window.py:4231):
                   **켤 때만** 경고를 띄우고 [Yes] 가 아니면 체크를 되돌린다.
                   끄는 것은 묻지 않는다. 확정되면 config index_focus_mode 를 저장한다. */
                onClick={() => {
                  const next = !focusIndex;
                  const apply = () => {
                    setFocusIndex(next);
                    setConfig((cfg) => {
                      const merged = { ...cfg, indexFocusMode: next };
                      void saveUserConfig(merged);
                      return merged;
                    });
                  };
                  if (!next) { apply(); return; }
                  setConfirm({
                    title: "인덱싱 집중 모드",
                    text: "재생 중에도 인덱싱을 100% 속도로 진행합니다.\n"
                      + "그동안 디스크를 꽉 써서 사운드 재생이 끊기거나 버벅일 수 있습니다.\n\n"
                      + "켤까요?",
                    onYes: apply,
                  });
                }}>
            <IcoCheck size={10} />
          </span>
          인덱싱 집중
        </label>
      </div>

      {modal && <Modals id={modal}
                        /* 작업 중이면 창을 닫지 않는다 — modalBusy 주석 참고.
                           창 안에서도 막지만, 여기서 한 번 더 막아야 창 밖 경로가
                           전부 덮인다 (조사 M01/M03). */
                        onClose={() => { if (!modalBusy) setModal(null); }}
                        onBusyChange={setModalBusy}
                        onOpenFailed={(root) => setModal(`failed:${root}`)}
                        /* 미완료 항목 창에서 라이브러리 현황으로 돌아가기 */
                        onBack={() => { if (!modalBusy) setModal("status"); }}
                        libraries={libraryRoots}
                        blacklistEntries={blacklistRows}
                        config={config} onSaveConfig={saveConfig}
                        onRunAdmin={runDialogAdmin}
                        onOrphans={promptOrphanRestore}
                        /* 방금 DB 를 바꿨으므로 force — refreshIndexState 주석 참고
                           (force 없이 부르면 1.5초 규칙에 걸려 개수가 안 바뀐다) */
                        onDataChanged={() => { void refreshIndexState(true); }} />}
      {exitAsk && (
        <div className="scrim" onMouseDown={(event) => {
          /* 바깥 클릭은 취소 — 기존 확인창과 같은 규칙이고, 취소가 안전한 쪽이다 */
          if (event.target === event.currentTarget) setExitAsk(false);
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">종료 확인</span></div>
            <div className="modal-body">
              {/* 인덱싱·관리 작업이 돌고 있으면 무엇을 잃는지 먼저 알린다.
                  ⚠ 두 문구 다 i18n_en.ts 에 **덩어리 통째로** 열쇠가 있어야 한다. */}
              <div className="confirm-text pre">
                {foregroundAdminActive || backgroundMetaActive
                  ? "인덱싱이 진행 중입니다. 지금 종료하면 작업이 중단됩니다.\n\nSoundField 를 종료할까요?"
                  : "SoundField 를 종료할까요?"}
              </div>
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" autoFocus onClick={() => { void doExit(); }}>종료</button>
              <button className="btn" onClick={() => setExitAsk(false)}>취소</button>
            </div>
          </div>
        </div>
      )}
      {confirm && (
        <div className="scrim" onMouseDown={(event) => {
          /* 바깥 클릭도 같은 순서 (M04) */
          if (event.target === event.currentTarget) {
            const no = confirm.onNo; setConfirm(null); no?.();
          }
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">{confirm.title}</span></div>
            <div className="modal-body"><div className="confirm-text pre">{confirm.text}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              {/* 원본은 question(예/아니오) 과 information(확인 하나) 을 구분한다 */}
              <button className="btn btn-primary" autoFocus={!confirm.defaultNo}
                      /* ⚠ 순서 주의: **먼저 닫고** 그 다음에 onYes 를 부른다.
                         반대로 하면 onYes 안에서 띄운 **다음 확인창이 뒤이은
                         setConfirm(null) 에 즉시 지워진다** (조사 M04).
                         실제 증상: 1단계 완료 뒤 고아 중복 복원 질문이 안 보이고,
                         갱신 확인 → 메타 분석 양보 확인에서는 응답을 기다리는
                         약속이 영원히 안 풀렸다. */
                      onClick={() => { const yes = confirm.onYes; setConfirm(null); yes(); }}>
                {confirm.info ? "확인" : "예"}
              </button>
              {!confirm.info && (
                <button className="btn" autoFocus={confirm.defaultNo}
                        /* 아니오도 같은 이유로 먼저 닫는다 (M04) */
                        onClick={() => { const no = confirm.onNo; setConfirm(null); no?.(); }}>아니오</button>
              )}
            </div>
          </div>
        </div>
      )}
      <ContextMenu ctx={ctx} onClose={() => setCtx(null)}
                   onRevealInBrowser={(path) => setFocusPath(path)}
                   onRename={(path, current) => setPrompt({
                     title: "이름 변경",
                     label: `새 이름:\n${path}`,
                     value: current,
                     onOk: (next) => {
                       /* 원본 _on_folder_rename_request (main_window.py:6422):
                          key = normpath(path).rstrip("\\/").lower(), 값은 strip 결과.
                          표시명만 바꾸고 실제 폴더는 건드리지 않으며 config 에 저장한다.
                          (원본은 빈 문자열도 그대로 저장 → 라벨이 빈칸이 된다) */
                       renameFolder(path, next.trim());
                     },
                   })}
                   onBlacklistAdd={(paths) => askBlacklist(paths)}
                   onMoveRoot={moveRoot}
                   onRescan={(full) => {
                     const picked = ctx?.kind === "tree"
                       ? (ctx.paths?.length ? ctx.paths : [ctx.path ?? ctx.label]).filter(Boolean) : [];
                     /* 원본 _on_rescan_many_request: 같은 경로가 두 번 오면 한 번만
                        스캔하고, 대상이 없으면 아무것도 하지 않는다. */
                     const targets = [...new Set(picked)];
                     if (!targets.length) return;
                     const start = () => { void runIndexAdmin("index",
                       full ? "전체 재스캔 중..." : "빠른 재스캔 중...",
                       { op: "index", paths: targets, force: full }); };
                     /* 빠른 재스캔은 바로 시작하고, 전체(강제) 재스캔만 물어본다
                        (조사 Q12 — 원본 _on_full_rescan_many_request 의 확인 문구).
                        변경 여부와 관계없이 전부 다시 읽으므로 오래 걸린다. */
                     if (!full) { start(); return; }
                     setConfirm({
                       title: "강제 재스캔",
                       text: `[${targets.length.toLocaleString()}개 폴더] 강제로 다시 스캔합니다.\n`
                         + "(변경 여부와 관계없이 모든 파일을 확인합니다.)\n\n"
                         + "진행할까요?",
                       onYes: start,
                     });
                   }}
                   /* 원본 _on_remove_roots_request / _on_purge_roots_request 의
                      확인 문구를 그대로 띄우고, [예] 를 눌러도 쓰기는 하지 않는다. */
                   onRemoveRoots={(purge) => {
                     const targets = ctx?.kind === "tree"
                       ? (ctx.paths?.length ? ctx.paths : [ctx.path ?? ctx.label]) : [];
                     const shown = targets.length === 1
                       ? targets[0]
                       : targets.slice(0, 12).join("\n") + (targets.length > 12 ? "\n..." : "");
                     setConfirm(purge
                       ? {
                           title: "라이브러리 완전 제거",
                           text: `${shown}\n\n선택한 라이브러리 ${targets.length.toLocaleString()}개에 대해 `
                             + "이 도구에 저장된 모든 정보가 영구 삭제됩니다:\n"
                             + "  · 인덱스/메타데이터 전체 (숨김·제거·실패 항목 포함)\n"
                             + "  · 파형 캐시 / 재생 히스토리 / 블랙리스트 항목\n"
                             + "  · 즐겨찾기 · 사용자 탭 참조\n\n"
                             + "실제 오디오 파일은 삭제되지 않습니다.\n"
                             + "대형 라이브러리는 수 분이 걸릴 수 있습니다.\n\n"
                             + "※ 완전 제거해도 디스크 용량·검색 성능에는 사실상 차이가 없습니다.\n"
                             + "   일반적으로는 [라이브러리 제거]를 권장하며, 완전 제거는 이 도구에\n"
                             + "   남은 모든 기록을 지우고 싶을 때만 사용하세요. 계속할까요?",
                           defaultNo: true,
                           onYes: () => { void runIndexAdmin("remove", "라이브러리 완전 제거 중...",
                             { op: "remove_roots", paths: targets, purge: true })
                             .then((ok) => { if (ok) pruneRemovedReferences(targets, true); }); },
                         }
                       : {
                           title: "라이브러리 제거",
                           text: `${shown}\n\n선택한 라이브러리 ${targets.length.toLocaleString()}개와 `
                             + "인덱싱된 데이터를 삭제할까요?\n(실제 파일은 삭제되지 않습니다.)",
                           onYes: () => { void runIndexAdmin("remove", "라이브러리 제거 중...",
                             { op: "remove_roots", paths: targets, purge: false })
                             .then((ok) => { if (ok) pruneRemovedReferences(targets, false); }); },
                         });
                   }}
                   onBlacklistLibrary={(filePath) => {
                     /* 원본 _on_results_add_to_blacklist (main_window.py:4343):
                        정규화 후 **가장 긴** 매칭 루트를 찾아 그 루트를 넣고,
                        못 찾으면 그 파일의 상위 폴더를 넣는다. */
                     const target = normPath(filePath).toLowerCase();
                     const roots = [...libraryRoots.map((l) => l.path), ...addedLibs]
                       .map((r) => normPath(r))
                       .sort((a, b) => b.length - a.length);
                     const hit = roots.find((r) => {
                       const low = r.toLowerCase();
                       return target === low || target.startsWith(low + "\\");
                     });
                     askBlacklist([hit ?? filePath.replace(/[\\/][^\\/]*$/, "")]);
                   }} />
      {prompt && (
        <div className="scrim" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closePrompt();
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">{prompt.title}</span></div>
            <div className="modal-body">
              <div className="confirm-text pre">{prompt.label}</div>
              <input className="input" autoFocus defaultValue={prompt.value}
                     style={{ marginTop: 8 }}
                     onKeyDown={(event) => {
                       if (event.key === "Enter") {
                         prompt.onOk((event.target as HTMLInputElement).value);
                         setPrompt(null);
                       }
                       if (event.key === "Escape") closePrompt();
                     }}
                     ref={(node) => { if (node) promptInputRef.current = node; }} />
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary"
                      onClick={() => {
                        prompt.onOk(promptInputRef.current?.value ?? "");
                        setPrompt(null);
                      }}>확인</button>
              <button className="btn" onClick={closePrompt}>취소</button>
            </div>
          </div>
        </div>
      )}
      <CenterToast message={toast} />
      <CenterLoader tasks={loaderTasks} />
    </div>
  );
}
