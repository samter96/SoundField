import { useEffect, useMemo, useRef, useState } from "react";
import { Modal } from "./Modal";
import { fmtSize } from "../data";
import { DUP_CACHE_MAX_GROUPS, fmtScanTime, loadDupCache, saveDupCache } from "../dupCache";
import { loadHidden, onAdminOpProgress, onDupProgress, revealInExplorer, scanDuplicates,
         type AdminRequest, type HiddenResult } from "../backend";
import { SuppressedManager } from "./SuppressedManager";
import { IcoCaret } from "../icons";
import type { ThemeName } from "../theme";
import { CONFIG_DEFAULTS, SHORTCUT_DEFAULTS, type UserConfig } from "../config";
import { t } from "../i18n";

/* 원본 app/ui/main_window.py:SettingsDialog (3094~) 를 그대로 옮긴 것.
   - 창 제목 "환경설정", 크기 500x500
   - 탭 6개: 일반 / 테마 / 재생 / 인덱싱 / 중복 검수 / 단축키
   - 하단 버튼: [기본값으로 초기화] [저장] [취소]
   - 테마는 "저장" 을 눌러야 적용된다 (라디오 선택만으로 즉시 적용 아님)
   - Ctrl+P 로 열고 닫는 양방향 토글 (App 의 전역 핸들러가 담당)
   중복 검수가 별도 창이 아니라 이 창의 탭인 것도 원본 구조다. */

const TABS = ["일반", "테마", "재생", "인덱싱", "중복 검수", "단축키"] as const;

/* 중복 그룹을 가리키는 키 — 그룹은 (파일명, 크기) 쌍으로 묶이므로 **파일명만으로는
   구분되지 않는다.** 이름이 같고 크기가 다른 그룹이 실제로 여럿 있다.
   구분자로 \u0000 을 쓰는 이유: 파일명·숫자에 절대 들어갈 수 없는 글자라
   "a|1" 과 "a" + "|1" 같은 우연한 충돌이 없다. */
const dupKey = (name: string, size: number) => name + "\u0000" + size;

/* 중복 그룹에서 **어느 사본을 남길지** 정하는 정렬 — 원본과 같아야 한다.
   원본: `sorted(paths, key=lambda p: (len(p), p))`  (main_window.py:3649)
     · len(p)  = 파이썬은 **코드포인트** 개수를 센다 (JS 의 .length 는 UTF-16 단위)
     · p 비교  = 파이썬은 **코드포인트** 순서로 비교한다

   ⚠ localeCompare 를 쓰지 말 것. 한국어 로케일 비교는 대소문자·자모 순서를 다르게
     보므로 원본과 **다른 사본이 남는다** (조사 D01: C:\Z\hit.wav 와 C:\a\hit.wav 에서
     원본은 Z, PoC 는 a 를 남겼다). 실제 파일을 지우는 건 아니지만 검수 결과가 다르다. */
const cpLength = (text: string) => [...text].length;

const compareCodePoints = (a: string, b: string) => {
  const ax = [...a];
  const bx = [...b];
  const shared = Math.min(ax.length, bx.length);
  for (let i = 0; i < shared; i += 1) {
    const diff = (ax[i].codePointAt(0) ?? 0) - (bx[i].codePointAt(0) ?? 0);
    if (diff !== 0) return diff;
  }
  return ax.length - bx.length;
};

/** 원본과 같은 규칙으로 정렬한 경로 목록 — 첫 번째가 남길 사본이다. */
export const orderDupPaths = (paths: string[]) =>
  [...paths].sort((a, b) => cpLength(a) - cpLength(b) || compareCodePoints(a, b));
type TabName = (typeof TABS)[number];

/* 원본 _setup_shortcuts_tab */
const SHORTCUTS: Array<[string, string, string]> = [
  ["play_pause", "재생 / 일시정지", "Space"],
  ["seek_to_start", "처음으로", "Home"],
  ["toggle_loop", "반복 재생", "R"],
  ["toggle_segments", "세그먼트 토글", "S"],
  ["toggle_history", "히스토리 토글", "H"],
  ["toggle_binaural", "바이노럴 켜기/끄기", "B"],
  ["open_settings", "환경설정 열기/닫기", "Ctrl+P"],
];

/* 휠 단축키는 원본에서 변경 불가(dimmed) */
const WHEEL_SHORTCUTS: Array<[string, string]> = [
  ["파형 가로 확대/축소", "Ctrl+휠"],
  ["파형 세로 확대/축소", "Ctrl+Alt+휠"],
  ["가로 스크롤 (파형/파일브라우저/결과)", "Shift+휠"],
];

/* 원본 테마 라디오 라벨 ↔ 내부 키 */
const THEME_RADIOS: Array<[ThemeName, string]> = [
  ["grey", "뉴트럴 (기본)"],
  ["light", "라이트"],
  ["dark", "네온"],
];

function Radio({ checked, label, onSelect }: { checked: boolean; label: string; onSelect: () => void }) {
  return (
    <button className={"radio-row" + (checked ? " on" : "")} role="radio" aria-checked={checked}
            onClick={onSelect}>
      <span className="radio-mark" />
      <span>{label}</span>
    </button>
  );
}

function CheckRow({ checked, label, title, onToggle }:
  { checked: boolean; label: string; title?: string; onToggle: () => void }) {
  return (
    <button className={"check-row" + (checked ? " on" : "")} role="checkbox" aria-checked={checked}
            data-tip={title} onClick={onToggle}>
      <span className="check-mark" />
      <span>{label}</span>
    </button>
  );
}

type Props = {
  onClose: () => void;
  config: UserConfig;
  onSave: (config: UserConfig) => void;
  /* 중복 실행 가드가 씌워진 admin 실행기 (App.runDialogAdmin).
     backend 의 runAdmin 을 직접 부르면 가드를 우회해, 인덱싱/메타 분석 중에
     DB 쓰기가 겹쳐 들어간다. */
  runAdmin: (req: AdminRequest) => Promise<Record<string, unknown>>;
  /** 작업 중임을 App 에 알린다 — App 의 Ctrl+P/뒤로/교체 차단에 쓰인다 (조사 M01/M03) */
  onBusyChange?: (busy: boolean) => void;
};

export function Settings({ onClose, config, onSave, runAdmin, onBusyChange }: Props) {
  const [tab, setTab] = useState<TabName>("일반");

  /* 편집 중 값 — 원본과 같이 "저장" 을 눌러야 반영된다. */
  const [searchLimit, setSearchLimit] = useState(String(config.searchLimit));
  const [doubleClickToPlay, setDoubleClickToPlay] = useState(config.doubleClickToPlay);
  const [compactResults, setCompactResults] = useState(config.compactResults);
  const [themeDraft, setThemeDraft] = useState<ThemeName>(config.theme);
  const [restartFromZero, setRestartFromZero] = useState(config.playbackRestartFromZero);
  const [autoPreview, setAutoPreview] = useState(config.autoPreview);
  const [stopOnDrag, setStopOnDrag] = useState(config.stopOnDrag);
  const [indexOnIdle, setIndexOnIdle] = useState(config.indexOnIdle);
  const [shortcuts, setShortcuts] = useState<Record<string, string>>(
    { ...SHORTCUT_DEFAULTS, ...config.shortcuts },
  );

  /* 중복 검수 탭 상태 — 원본은 전체 라이브러리 단일 스캔. 백엔드 연결 전이라
     스캔 자체는 아직 동작하지 않고, 결과 트리/도구줄 구조만 원본과 맞춘다. */
  const [dupFilter, setDupFilter] = useState("");
  /* ⚠ 펼침 상태의 키는 파일명이 아니라 dupKey(파일명 + 크기)다.
     중복 그룹은 (파일명, 크기) 쌍으로 묶이므로 **이름이 같고 크기가 다른 그룹이
     여러 개 있을 수 있다.** 파일명만 키로 쓰면 그 그룹들이 한 덩어리로 취급돼
     하나를 펼치면 다른 것도 펼쳐지고, React 목록 키도 겹친다. */
  const [dupOpen, setDupOpen] = useState<Set<string>>(new Set());
  /* 진행바 — 원본 _dup_progress. null 이면 숨김, total 0 이면 준비 단계(총량 미정). */
  const [dupProg, setDupProg] = useState<{ done: number; total: number } | null>(null);
  /* 검색 제외(DB 쓰기) 진행 중 — 이 동안은 창을 닫지 못한다 (사용자 결정 2026-09-08) */
  const [dupHiding, setDupHiding] = useState(false);
  /* 가상 스크롤용 스크롤 위치·높이 (아래 dup-tree 컨테이너에서 갱신) */
  const dupTreeRef = useRef<HTMLDivElement | null>(null);
  const [dupView, setDupView] = useState({ top: 0, height: 320 });

  const resetAll = () => {
    setSearchLimit(String(CONFIG_DEFAULTS.searchLimit));
    setDoubleClickToPlay(CONFIG_DEFAULTS.doubleClickToPlay);
    setCompactResults(CONFIG_DEFAULTS.compactResults);
    setRestartFromZero(CONFIG_DEFAULTS.playbackRestartFromZero);
    setAutoPreview(CONFIG_DEFAULTS.autoPreview);
    setStopOnDrag(CONFIG_DEFAULTS.stopOnDrag);
    setIndexOnIdle(CONFIG_DEFAULTS.indexOnIdle);
    setShortcuts({ ...SHORTCUT_DEFAULTS });
    /* 원본 _on_reset 은 테마 라디오를 건드리지 않는다 */
  };

  /* 원본 _run_duplicate_scan: 버튼을 눌러야 스캔이 시작되고, 진행 중에는 버튼이
     비활성 + 진행바가 보인다. 스캔 결과가 없으면 "중복 없음". 결과 내 검색은
     파일명/경로 부분일치 (원본 _dup_filter_apply_group). */
  const [dupScanning, setDupScanning] = useState(false);
  const [dupResult, setDupResult] = useState<Array<{ name: string; size: number; n: number; paths: string[] }> | null>(null);
  /* 직전 검수 기록 — 창을 닫았다 열어도 그대로 보인다 (사용자 지시 2026-09-15).
     scannedAt=0 이면 "이번 실행에서 아직 스캔한 적 없고 기록도 없다" 는 뜻이다. */
  const [dupScannedAt, setDupScannedAt] = useState(0);
  const [dupTooLarge, setDupTooLarge] = useState({ on: false, groups: 0 });
  const [dupStatus, setDupStatus] = useState("");
  /* 원본 _dup_unsuppress_btn 라벨용 숨김 개수 (비동기 카운트, main_window.py:3410) */
  const [hidden, setHidden] = useState<HiddenResult>({ rows: [], matched: 0, total: 0 });
  const [suppressedOpen, setSuppressedOpen] = useState(false);
  /* 원본 "검색 제외 확인" 질문창 */
  const [dupConfirm, setDupConfirm] = useState<string | null>(null);
  /* 처리가 끝났음을 알리는 안내창 — 원본도 완료 시 QMessageBox.information 을 띄운다
     (main_window.py _on_dup_repair_done). 44만 건이 걸릴 수 있어 상태줄 글자만으로는
     끝난 줄 모른다 (사용자 지시 2026-09-08). */
  const [dupDone, setDupDone] = useState<string | null>(null);
  useEffect(() => { void loadHidden("", 1).then(setHidden); }, []);

  /* ── 작업 중 창 닫기 막기 (사용자 결정 2026-09-08) ────────────────────────────
     중복 검수 스캔이나 검색 제외가 도는 중에 창을 닫으면 **작업은 계속 돌지만**
     결과 표시·제외 관리 개수 갱신·자동 재스캔이 전부 유실된다. 특히 검색 제외는
     44만 건이 걸릴 수 있는데, 끝나도 화면에는 옛 목록이 남아 "제외했는데 목록에서
     사라지지 않는다" 로 보인다.
     ⚠ 닫는 길이 네 개다 — [취소] 버튼 / [X] / Esc / 바깥 클릭. Modal 은 그 넷을
       모두 onClose 로 보내므로, **여기서 한 번 막으면 넷 다 막힌다.** 개별 버튼에
       조건을 달지 말 것 (하나 빠뜨리면 그 길로 새어 나간다).
     데이터는 원래 안전하다 — 제외는 한 트랜잭션이라 중간만 적용되는 상태가 없다.
     막는 이유는 화면 상태가 어긋나는 것을 막기 위한 것이다. */
  /* 자식(제외 관리)의 복원도 "작업 중"에 포함한다 — 그 사이 창이 닫히면
     결과·개수 갱신이 유실된다 (조사 M02). */
  const [childBusy, setChildBusy] = useState(false);
  /* 중복 결과의 파일 행 우클릭 메뉴 (조사 D04) — 원본 _on_dup_context_menu */
  const [dupMenu, setDupMenu] = useState<{ x: number; y: number; path: string } | null>(null);
  const busyLabel = dupHiding ? "검색 제외" : dupScanning ? "중복 검수"
    : childBusy ? "제외 복원" : "";
  /* App 에 작업 상태를 올린다 — Ctrl+P 같은 창 밖 경로를 막기 위해 필요하다 */
  useEffect(() => { onBusyChange?.(Boolean(busyLabel)); }, [busyLabel, onBusyChange]);

  useEffect(() => {
    if (!dupMenu) return;
    const close = () => setDupMenu(null);
    /* ⚠ capture 단계로 듣지 말 것. 메뉴 항목의 onMouseDown 이 거는
       stopPropagation 은 **이미 실행된 capture 리스너를 되돌리지 못한다.**
       그래서 항목을 누르는 순간 메뉴가 먼저 사라지고, 이어질 click 이 갈 곳을
       잃어 아무 일도 일어나지 않았다 — "탐색기에서 보기를 눌러도 반응이 없다"
       (사용자 신고 2026-09-14). 잘 동작하는 결과표 메뉴(ContextMenu.tsx)는
       처음부터 capture 없이 듣는다. 같은 방식으로 맞춘다. */
    window.addEventListener("mousedown", close);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("resize", close);
    };
  }, [dupMenu]);

  const guardedClose = () => {
    if (busyLabel) {
      setDupStatus(`${busyLabel} 처리 중에는 창을 닫을 수 없습니다. 끝날 때까지 기다려 주세요.`);
      return;
    }
    onClose();
  };

  const save = () => {
    if (busyLabel) {
      setDupStatus(`${busyLabel} 처리 중에는 저장할 수 없습니다.`);
      return;
    }
    const n = Number(searchLimit || CONFIG_DEFAULTS.searchLimit);
    onSave({
      ...config,
      theme: themeDraft,
      searchLimit: Math.max(100, Math.min(5000, n)),
      doubleClickToPlay,
      compactResults,
      playbackRestartFromZero: restartFromZero,
      autoPreview,
      stopOnDrag,
      indexOnIdle,
      shortcuts: { ...SHORTCUT_DEFAULTS, ...shortcuts },
    });
  };

  /* 가상 스크롤은 보이는 높이를 알아야 몇 줄을 그릴지 정할 수 있다. onScroll 은
     스크롤이 움직일 때만 오므로, 탭을 열거나 창 크기가 바뀔 때 직접 읽는다.
     (안 읽으면 초기값 320px 만큼만 그려져 아래가 비어 보인다.) */
  useEffect(() => {
    if (tab !== "중복 검수") return;
    const box = dupTreeRef.current;
    if (!box) return;
    const read = () => setDupView({ top: box.scrollTop, height: box.clientHeight });
    read();
    const observer = new ResizeObserver(read);
    observer.observe(box);
    return () => observer.disconnect();
  }, [tab]);

  /* 남은 시간 문구 — 원본 _fmt_remain (main_window.py:3809) 과 같은 형식 */
  const fmtRemain = (seconds: number) => {
    const value = Math.max(1, Math.round(seconds));
    return value >= 60 ? `${Math.floor(value / 60)}분 ${value % 60}초` : `${value}초`;
  };

  /* 검색 제외 직후의 자동 재스캔에서는 완료 팝업을 띄우지 않는다
     (원본 _suppress_scan_popup — 제외 완료 팝업과 겹치기 때문). */
  /* 창을 열 때 직전 기록을 복원한다. 스캔은 돌리지 않는다 — 사용자가 [중복 검수]
     를 다시 누르기 전까지는 그때 본 목록과 시각이 그대로 유지돼야 한다. */
  useEffect(() => {
    let alive = true;
    void loadDupCache().then((cache) => {
      if (!alive || !cache) return;
      setDupScannedAt(cache.scannedAt);
      setDupTooLarge({ on: cache.tooLarge, groups: cache.totalGroups });
      if (cache.tooLarge) {
        setDupStatus(`직전 검수 결과 ${cache.totalGroups.toLocaleString()}개 그룹 (보관 안 됨)`);
        return;
      }
      setDupResult(cache.groups.map((g) => ({
        name: g.name, size: g.size, n: g.paths.length, paths: g.paths,
      })));
      /* 상태줄도 스캔 직후와 같은 문구로 채운다 — 비어 있으면 "안 불러온 건지
         결과가 없는 건지" 알 수 없다 */
      if (!cache.groups.length) {
        setDupStatus("중복 항목 없음.");
      } else {
        const files = cache.groups.reduce((sum, g) => sum + g.paths.length, 0);
        setDupStatus(`${cache.groups.length.toLocaleString()}개 그룹 / `
          + `${files.toLocaleString()}개 파일 — 제외 대상 `
          + `${(files - cache.groups.length).toLocaleString()}개`);
      }
    });
    return () => { alive = false; };
  }, []);

  const runDupScan = async (quiet = false) => {
    setDupScanning(true);
    /* 원본은 총량을 모르는 준비 단계 문구부터 시작한다 (main_window.py:3777) */
    setDupStatus("검사 준비 중...");
    /* ⚠ 시작할 때 **이전 결과를 반드시 비운다** (조사 D06).
       원본도 스캔 시작에서 트리와 제외 버튼을 비운다 (main_window.py:3774).
       안 비우면 재검수가 실패했을 때 옛 그룹이 그대로 남아, 최신 검수가 실패했는데도
       **오래된 결과를 대상으로 검색 제외가 실행**된다. */
    setDupResult(null);
    /* 총량이 아직 없는 준비 단계 — 원본도 setRange(0, 0) 로 시작한다. */
    setDupProg({ done: 0, total: 0 });
    /* ── 진행 문구 (조사 D02) ────────────────────────────────────────────────
       원본 _on_dup_scan_progress (main_window.py:3792) 와 같은 3단계다:
         검사 준비 중... → 검사 중... N% — 약 N초 남음 → 결과 정리 중...
       마지막 단계가 중요하다. 진행바가 100% 가 된 뒤에도 중복 키의 경로를 모으는
       작업이 남아 있는데, 예전에는 문구가 "중복 검사 중..." 그대로여서
       **왜 안 끝나는지 알 수 없었다.** */
    const base = { at: 0, t: 0 };
    const stop = onDupProgress((value) => {
      setDupProg(value);
      const total = Math.max(1, value.total);
      const pct = Math.min(100, Math.floor(value.done * 100 / total));
      if (value.done >= value.total) { setDupStatus("결과 정리 중..."); return; }
      if (!base.t) { base.t = Date.now(); base.at = value.done; }
      const seen = value.done - base.at;
      const elapsed = (Date.now() - base.t) / 1000;
      const remain = seen > 0 && elapsed > 0.2
        ? ` — 약 ${fmtRemain((value.total - value.done) * elapsed / seen)} 남음` : "";
      setDupStatus(`검사 중... ${pct}%${remain}`);
    });
    try {
      const got = await scanDuplicates();
      if (!got) {
        /* 실패 — 결과를 비운 상태로 둔다 (위 D06 주석). 제외 버튼도 자동 비활성 */
        setDupResult(null);
        setDupStatus("중복 검사를 실행할 수 없습니다");
        /* 원본은 실패도 팝업으로 알린다 (조사 D03, main_window.py:3825) */
        if (!quiet) setDupDone("검사 중 오류가 발생했습니다.\n\n"
          + "중복 검사를 실행할 수 없습니다. 잠시 후 다시 시도해 주세요.");
        return;
      }
      setDupResult(got.map((g) => ({
        name: g.file_name, size: g.file_size, n: g.paths.length, paths: g.paths,
      })));
      /* 이 결과를 기록으로 남긴다 — 제외 직후의 자동 재스캔(quiet)도 마찬가지라
         갱신된 목록과 그 시각이 그대로 보관된다 (사용자 지시 2026-09-15). */
      const saved = await saveDupCache(got.map((g) => ({
        name: g.file_name, size: g.file_size, paths: g.paths,
      })));
      setDupScannedAt(saved.scannedAt);
      setDupTooLarge({ on: saved.tooLarge, groups: saved.totalGroups });
      /* ── 완료 안내 (조사 D03) ──────────────────────────────────────────────
         원본 _on_dup_scan_done (main_window.py:3816) 은
           · 상태줄에 "N개 그룹 / N개 파일 — 제외 대상 N개"
           · 0건이면 "중복 항목이 없습니다" 팝업
           · 있으면 그룹·파일 수와 [검색 제외] 가 무엇을 하는지 팝업으로 알린다
         PoC 는 "중복 그룹 N개" 한 줄로만 끝나서, 다음에 무엇이 일어나는지
         (몇 개가 제외되고 무엇이 남는지) 알려주지 않았다. */
      if (!got.length) {
        setDupStatus("중복 항목 없음.");
        if (!quiet) setDupDone("중복 항목이 없습니다.\n"
          + "(파일명과 크기가 모두 같은 항목이 발견되지 않았습니다.)");
        return;
      }
      const totalFiles = got.reduce((sum, g) => sum + g.paths.length, 0);
      const removable = totalFiles - got.length;   /* 그룹마다 1개는 남는다 */
      setDupStatus(`${got.length.toLocaleString()}개 그룹 / `
        + `${totalFiles.toLocaleString()}개 파일 — 제외 대상 ${removable.toLocaleString()}개`);
      if (!quiet) setDupDone(
        `중복 그룹 ${got.length.toLocaleString()}개 · 파일 ${totalFiles.toLocaleString()}개를 찾았습니다.\n\n`
        + "[전체 검색 제외 처리]를 누르면 각 그룹에서 경로가 가장 짧은 1개만 남기고\n"
        + `나머지 ${removable.toLocaleString()}개를 검색에서 제외합니다.\n`
        + "(파일·인덱스는 그대로 — [제외 관리]에서 언제든 복원 가능)");
    } finally {
      stop();
      setDupScanning(false);
      setDupProg(null);
    }
  };

  const dupSource = dupResult ?? [];
  /* ── 결과 내 검색 (조사 D05) ──────────────────────────────────────────────
     원본 _dup_filter_apply_group (main_window.py:3915): 그룹 **이름**이 검색어와
     맞으면 그 그룹의 경로를 다 보여주고, 안 맞으면 **일치한 경로만** 보여준다.
     PoC 는 경로 하나만 맞아도 그 그룹의 모든 경로를 그려서, `C:\Z` 로 좁혀도
     `C:\a` 사본까지 함께 보였다. */
  const dupRows = useMemo(() => {
    const q = dupFilter.trim().toLowerCase();
    const out: Array<{ name: string; size: number; n: number; paths: string[];
                       shown: string[] }> = [];
    for (const group of dupSource) {
      if (!q) { out.push({ ...group, shown: group.paths }); continue; }
      if (group.name.toLowerCase().includes(q)) {
        out.push({ ...group, shown: group.paths });
        continue;
      }
      const hits = group.paths.filter((path) => path.toLowerCase().includes(q));
      if (hits.length) out.push({ ...group, shown: hits });
    }
    return out;
  }, [dupSource, dupFilter]);

  /* ── 목록 가상 스크롤 ─────────────────────────────────────────────────────
     상한을 없앤 뒤로 그룹이 35만 개까지 나온다(실측 354,261). 전부 DOM 에
     그리면 창이 멈춘다. 그래서 **보이는 구간만** 그리고, 위아래를 빈 공간으로
     밀어 스크롤바 길이를 맞춘다.

     ⚠ DUP_ROW_H 는 app.css 의 `.dup-head, .dup-row { height: 26px }` 와
       **반드시 같아야 한다.** 결과표에서 같은 실수를 한 적이 있다 — JS 간격과
       CSS 행 높이가 어긋나 행이 서로 겹쳤다 (app.css 의 compact 주석 참고).
       한쪽을 바꾸면 다른 쪽도 바꿀 것. */
  const DUP_ROW_H = 26;
  const DUP_OVERSCAN = 8;          // 스크롤 시 흰 줄이 보이지 않게 앞뒤로 더 그린다

  /* 그룹 행과 (펼쳐진) 경로 행을 한 줄짜리 목록으로 편다 — 높이가 모두 같아야
     보일 구간을 계산할 수 있다. */
  const dupFlat = useMemo(() => {
    const rows: Array<
      | { kind: "group"; key: string; name: string; size: number; n: number; open: boolean }
      | { kind: "child"; key: string; path: string; keep: boolean }
    > = [];
    for (const group of dupRows) {
      const key = dupKey(group.name, group.size);
      const open = dupOpen.has(key);
      rows.push({ kind: "group", key, name: group.name, size: group.size, n: group.n, open });
      if (open) {
        /* 제외를 실행하면 **살아남는 항목**을 미리 표시한다 (조사 D04).
           원본과 같은 규칙이어야 한다 — orderDupPaths 의 첫 항목이 곧 유지 대상이고,
           아래 [전체 검색 제외 처리] 도 같은 함수로 나머지를 고른다. */
        const kept = orderDupPaths(group.paths)[0] ?? "";
        for (const path of group.shown)
          rows.push({ kind: "child", key: key + "|" + path, path, keep: path === kept });
      }
    }
    return rows;
  }, [dupRows, dupOpen]);

  const first = Math.max(0, Math.floor(dupView.top / DUP_ROW_H) - DUP_OVERSCAN);
  const last = Math.min(dupFlat.length,
    Math.ceil((dupView.top + dupView.height) / DUP_ROW_H) + DUP_OVERSCAN);
  const dupWindow = dupFlat.slice(first, last);

  return (
    <Modal
      title="환경설정"
      size="settings"
      /* 작업 중에는 닫기를 막는다 — guardedClose 주석 참고.
         Modal 이 [X]/Esc/바깥클릭을 모두 이 함수로 보낸다. */
      onClose={guardedClose}
      /* 이 창 위에 뜬 확인창/제외 관리가 Esc 를 받아야 한다 (조사 M02).
         안 넘기면 자식에서 Esc 를 눌렀을 때 환경설정까지 닫힌다. */
      blockEscape={Boolean(dupConfirm || dupDone || suppressedOpen)}
      foot={
        <>
          <div className="grow" />
          <button className="btn" disabled={!!busyLabel} onClick={resetAll}>기본값으로 초기화</button>
          <button className="btn btn-primary" disabled={!!busyLabel} onClick={save}>저장</button>
          <button className="btn" disabled={!!busyLabel}
                  data-tip={busyLabel ? `${busyLabel} 처리 중에는 닫을 수 없습니다` : undefined}
                  onClick={guardedClose}>취소</button>
        </>
      }
    >
      <div className="tabstrip" role="tablist">
        {TABS.map((name) => (
          <button key={name} role="tab" aria-selected={tab === name}
                  className={"strip-tab" + (tab === name ? " active" : "")}
                  onClick={() => setTab(name)}>
            {t(name)}
          </button>
        ))}
      </div>

      {tab === "일반" && (
        <div className="form">
          <div className="form-row">
            <span className="form-label">검색 최대 노출 개수:</span>
            <input className="input input-sm num" value={searchLimit} inputMode="numeric"
                   onChange={(event) => setSearchLimit(event.target.value.replace(/[^0-9]/g, ""))}
                   onBlur={() => {
                     const n = Number(searchLimit || CONFIG_DEFAULTS.searchLimit);
                     setSearchLimit(String(Math.max(100, Math.min(5000, n))));
                   }} />
          </div>
          <div className="form-hint">
            (추천: 1000개 이하 / 최대: 5000개. 값이 클수록 검색 성능에 영향을 줄 수 있습니다.)
          </div>

          <div className="form-sep" />

          <div className="form-row">
            <span className="form-label">결과 목록 동작:</span>
            <div className="form-radios">
              <Radio checked={!doubleClickToPlay} label="원클릭으로 재생"
                     onSelect={() => {
                       setDoubleClickToPlay(false);
                       setAutoPreview(true);
                     }} />
              <Radio checked={doubleClickToPlay} label="더블클릭으로 재생"
                     onSelect={() => {
                       setDoubleClickToPlay(true);
                       setAutoPreview(false);
                     }} />
            </div>
          </div>

          <div className="form-sep" />

          <div className="form-row">
            <span className="form-label">결과 텍스트 크기:</span>
            <div className="form-radios">
              <Radio checked={!compactResults} label="보통 (기본)"
                     onSelect={() => setCompactResults(false)} />
              <Radio checked={compactResults} label="작게 (한 화면에 더 많이)"
                     onSelect={() => setCompactResults(true)} />
            </div>
          </div>
        </div>
      )}

      {tab === "테마" && (
        <div className="form">
          <div className="form-row">
            <span className="form-label">테마:</span>
            <div className="form-radios">
              {THEME_RADIOS.map(([key, label]) => (
                <Radio key={key} checked={themeDraft === key} label={label}
                       onSelect={() => setThemeDraft(key)} />
              ))}
            </div>
          </div>
          <div className="form-hint">저장 버튼을 누르면 적용됩니다.</div>
        </div>
      )}

      {tab === "재생" && (
        <div className="form">
          <div className="form-row">
            <span className="form-label">정지 후 재생 방식:</span>
            <div className="form-radios">
              <Radio checked={restartFromZero} label="처음부터 다시 재생"
                     onSelect={() => setRestartFromZero(true)} />
              <Radio checked={!restartFromZero} label="현재 위치에서 이어서 재생"
                     onSelect={() => setRestartFromZero(false)} />
            </div>
          </div>

          <div className="form-sep" />

          <div className="form-row">
            <span className="form-label">드래그 동작:</span>
            <div className="form-radios">
              <CheckRow checked={stopOnDrag} label="DAW 로 드래그 시작 시 재생 정지"
                        title={"툴에서 사운드를 DAW 로 드래그 앤 드롭하는 순간 재생을 정지합니다.\n체크 해제 시 드래그 중에도 계속 재생됩니다."}
                        onToggle={() => setStopOnDrag((v) => !v)} />
            </div>
          </div>
        </div>
      )}

      {tab === "인덱싱" && (
        <div className="form">
          <div className="form-row">
            <span className="form-label">메타 분석 정책:</span>
            <div className="form-radios">
              <Radio checked={!indexOnIdle} label="스캔 완료 후 즉시 분석 시작"
                     onSelect={() => setIndexOnIdle(false)} />
              <Radio checked={indexOnIdle} label="앱 미사용 중일 때만 분석 시작 (Idle)"
                     onSelect={() => setIndexOnIdle(true)} />
            </div>
          </div>
          <div className="form-hint">
            Idle 정책: 클릭 · 키보드 입력 · 마우스 휠 스크롤이 30초간 없으면 백그라운드 메타 분석을
            시작하고, 분석 중 동일한 입력이 감지되면 즉시 중단합니다. (단순 마우스 호버는 입력으로
            간주하지 않음. 재개는 다시 30초 idle 도달 시)
          </div>
        </div>
      )}

      {tab === "중복 검수" && (
        <div className="dup-tab">
          <div className="form-hint">
            전체 라이브러리에서 파일명과 파일 크기가 동일한 항목을 그룹으로 묶어 보여줍니다.
            <br />(파일 내용이 같더라도 이름이나 크기가 다르면 검출되지 않습니다.)
          </div>

          <div className="dup-actions">
            <button className="btn btn-primary" disabled={!!busyLabel} onClick={() => void runDupScan()}>
              중복 검수 시작
            </button>
            <span className="dup-status">{dupStatus}</span>
            {/* 마지막 검수 시각 — 지금 보이는 목록이 **언제 것인지** 알려준다.
                다시 스캔하기 전까지 목록도 이 시각도 바뀌지 않는다
                (사용자 지시 2026-09-15). */}
            {dupScannedAt > 0 && !dupScanning && (
              <span className="dup-scanned-at">
                마지막 검수 {fmtScanTime(dupScannedAt)}
              </span>
            )}
            <div className="grow" />
            {/* 원본 _dup_unsuppress_btn — 라벨에 숨김 개수를 달고, 0건이면 비활성 */}
            <button className="btn" disabled={hidden.total <= 0 || !!busyLabel}
                    data-tip={"검색에서 제외된 파일 목록을 보고, 체크/전체 복원할 수 있습니다.\n복원된 파일은 즉시 검색 결과에 다시 나타납니다."}
                    onClick={() => setSuppressedOpen(true)}>
              {hidden.total > 0 ? `제외 관리 (${hidden.total.toLocaleString()})` : "제외 관리"}
            </button>
          </div>

          {/* 진행바 — 원본 _dup_progress (main_window.py:3350). 총량이 정해지기
              전에는 값을 모르는 상태로 흐르게 두고, 정해지면 비율로 채운다. */}
          {dupProg && (
            <div className="dup-progress">
              <div className={"dup-progress-bar" + (dupProg.total > 0 ? "" : " indet")}
                   style={dupProg.total > 0
                     ? { width: `${Math.min(100, (dupProg.done / dupProg.total) * 100)}%` }
                     : undefined} />
            </div>
          )}

          <div className="dup-tools">
            <input className="input" placeholder="결과 내 검색 (파일명/경로)" value={dupFilter}
                   onChange={(event) => setDupFilter(event.target.value)} />
            <button className="btn btn-sm"
                    onClick={() => setDupOpen(
                      new Set(dupRows.map((row) => dupKey(row.name, row.size))))}>
              모두 펼치기
            </button>
            <button className="btn btn-sm" onClick={() => setDupOpen(new Set())}>모두 접기</button>
          </div>

          {dupTooLarge.on && !dupScanning && dupResult === null && (
            /* 결과가 상한을 넘어 보관하지 못한 경우 — 시각만 남아 있다 */
            <div className="form-hint dup-toolarge">
              직전 검수에서 중복 그룹이 {dupTooLarge.groups.toLocaleString()}개 나왔습니다.
              보관 상한({DUP_CACHE_MAX_GROUPS.toLocaleString()}개)을 넘어 목록은 저장하지
              않았습니다 — 목록을 보려면 [중복 검수 시작]을 다시 눌러 주세요.
            </div>
          )}

          <div className="dup-tree" ref={dupTreeRef}
               onScroll={(event) => setDupView({
                 top: event.currentTarget.scrollTop,
                 height: event.currentTarget.clientHeight,
               })}>
            {/* 원본 QTreeWidget 헤더: ["파일명 / 경로", "크기", "개수"], 폭 320/90/60.
                그리드가 4열(캐럿 칸 포함)이라 헤더도 선행 빈 칸을 둬야 열이 맞는다.
                (빈 칸 없이 두면 "파일명 / 경로" 가 14px 칸에 들어가 세로로 뭉친다) */}
            <div className="dup-head">
              <span aria-hidden="true" />
              <span>파일명 / 경로</span><span>크기</span><span>개수</span>
            </div>
            {/* 안 보이는 앞부분을 빈 높이로 대신한다 (스크롤바 길이 유지) */}
            <div style={{ height: first * DUP_ROW_H }} aria-hidden="true" />
            {dupWindow.map((row) => (
              row.kind === "group" ? (
                <button className="dup-row group" key={row.key}
                        onClick={() => setDupOpen((set) => {
                          const next = new Set(set);
                          if (next.has(row.key)) next.delete(row.key);
                          else next.add(row.key);
                          return next;
                        })}>
                  <span className={"dup-caret" + (row.open ? " open" : "")}>
                    <IcoCaret size={11} />
                  </span>
                  <span className="dup-name">{row.name}</span>
                  <span className="dup-size num">{fmtSize(row.size)}</span>
                  <span className="dup-count num">{row.n}</span>
                </button>
              ) : (
                <div className={"dup-row child" + (row.keep ? " keep" : "")}
                     key={row.key}
                     data-tip={row.keep
                       ? `${row.path}\n(제외 실행 시 이 항목이 검색에 남습니다)`
                       : `${row.path}\n(제외 실행 시 검색에서 제외 — [제외 관리]에서 복원 가능)`}
                     /* 원본은 파일 행 우클릭으로 탐색기에서 보기·경로 복사를 준다
                        (조사 D04, main_window.py:3736) */
                     onContextMenu={(event) => {
                       event.preventDefault();
                       setDupMenu({ x: event.clientX, y: event.clientY, path: row.path });
                     }}>
                  <span aria-hidden="true" />
                  <span className="dup-name">
                    {row.keep && <span className="dup-keep">✓ 유지</span>}
                    {row.path}
                  </span>
                  <span className="dup-size" />
                  <span className="dup-count" />
                </div>
              )
            ))}
            <div style={{ height: Math.max(0, (dupFlat.length - last) * DUP_ROW_H) }}
                 aria-hidden="true" />
          </div>

          {/* [검색 제외] 는 결과 목록 **아래 오른쪽**에 둔다 (사용자 지시 2026-09-08).
              위쪽 도구줄(시작·제외 관리 사이)에 있으면 되돌리기 번거로운 작업이
              평범한 버튼들 사이에 섞여 잘못 눌리기 쉽다. 결과를 다 보고 나서
              누르는 순서와도 맞고, 창 아래 [저장]/[취소] 줄 바로 위에 온다.
              글자를 위험 색으로 두는 것도 같은 이유다 (btn-danger-text).
              동작·확인 문구는 원본 _dup_repair_btn / _run_duplicate_repair 그대로. */}
          <div className="dup-foot">
            <button className="btn btn-danger-text"
                    disabled={!(dupResult?.length) || !!busyLabel}
                    data-tip={"각 중복 그룹에서 경로가 가장 짧은 항목만 남기고 나머지를 검색에서 제외합니다.\n(파일·인덱스는 그대로 — [제외 관리]에서 언제든 복원 가능)"}
                    onClick={() => {
                      const groups = dupResult ?? [];
                      const removeCount = groups.reduce(
                        (sum, g) => sum + Math.max(0, g.paths.length - 1), 0);
                      if (!removeCount) { setDupStatus("제거할 항목 없음."); return; }
                      setDupConfirm(
                        `중복 그룹 ${groups.length.toLocaleString()}개에서 경로가 가장 짧은 1개씩 유지하고\n`
                        + `나머지 ${removeCount.toLocaleString()}개를 검색에서 제외합니다.\n\n`
                        + "파일과 인덱스는 그대로 유지되며 언제든 [제외 관리]에서 복원할 수 있습니다.\n"
                        + "진행할까요?");
                    }}>
              전체 검색 제외 처리
            </button>
          </div>
        </div>
      )}

      {tab === "단축키" && (
        <div className="form">
          {SHORTCUTS.map(([key, label]) => (
            <div className="form-row" key={key}>
              <span className="form-label">{t(label)}:</span>
              <input className="input input-key" value={shortcuts[key]} readOnly
                     onKeyDown={(event) => {
                       event.preventDefault();
                       const parts: string[] = [];
                       if (event.ctrlKey) parts.push("Ctrl");
                       if (event.altKey) parts.push("Alt");
                       if (event.shiftKey) parts.push("Shift");
                       const k = event.key === " " ? "Space" : event.key.length === 1
                         ? event.key.toUpperCase() : event.key;
                       if (!["Control", "Alt", "Shift"].includes(event.key)) parts.push(k);
                       if (parts.length) setShortcuts((map) => ({ ...map, [key]: parts.join("+") }));
                     }} />
            </div>
          ))}
          {WHEEL_SHORTCUTS.map(([label, value]) => (
            <div className="form-row" key={label}>
              <span className="form-label">{t(label)} ({t("개별 설정 불가")}):</span>
              <input className="input input-key disabled-key" value={t(value)} readOnly disabled
                     data-tip="이 단축키는 변경할 수 없습니다" />
            </div>
          ))}
        </div>
      )}
      {dupDone && (
        <div className="scrim inner"
             /* 확인 버튼만 있는 안내창 — 바깥을 눌렀을 때만 닫는다 (아래 확인창과 같은 규칙) */
             onMouseDown={(event) => {
               if (event.target === event.currentTarget) setDupDone(null);
             }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">검색 제외 완료</span></div>
            <div className="modal-body"><div className="confirm-text pre">{dupDone}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" autoFocus
                      onClick={() => setDupDone(null)}>확인</button>
            </div>
          </div>
        </div>
      )}

      {dupConfirm && (
        <div className="scrim inner"
               /* ⚠ 대상 검사가 없으면 **버튼을 누르는 순간(mousedown)**
                  창이 닫혀 클릭이 성립하지 않는다 — 제거/복원 버튼이
                  전부 죽어 있었다 (로그 실측: 확인창은 열리는데
                  "확인 누름" 이 한 번도 안 찍혔다). 바깥을 눌렀을 때만 닫는다. */
               onMouseDown={(event) => {
                 if (event.target === event.currentTarget) setDupConfirm(null);
               }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">검색 제외 확인</span></div>
            <div className="modal-body"><div className="confirm-text pre">{dupConfirm}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary"
                      onClick={() => {
                        setDupConfirm(null);
                        const paths = (dupResult ?? []).flatMap((group) => {
                          /* 원본과 같은 정렬 — orderDupPaths 주석 참고 (D01) */
                          return orderDupPaths(group.paths).slice(1);
                        });
                        setDupStatus("검색 제외 처리 중...");
                        setDupHiding(true);
                        setDupProg({ done: 0, total: paths.length });
                        /* 사이드카가 500개 묶음마다 알려준다 (sf_admin hide_paths) */
                        const stopProg = onAdminOpProgress((value) => {
                          if (value.op !== "hide_paths") return;
                          setDupProg({ done: value.done, total: value.total });
                          setDupStatus(
                            `검색 제외 중... ${value.done.toLocaleString()} / `
                            + `${value.total.toLocaleString()} `
                            + `(${Math.floor(value.done * 100 / Math.max(1, value.total))}%)`);
                        });
                        void runAdmin({ op: "hide_paths", paths }).finally(() => {
                          stopProg();
                          setDupHiding(false);
                          setDupProg(null);
                        }).then((result) => {
                          if (result.success === false) {
                            setDupStatus(String(result.message || "검색 제외 실패"));
                            return;
                          }
                          setDupStatus(
                            `${paths.length.toLocaleString()}개를 검색에서 제외했습니다`
                            + " ([제외 관리]에서 복원 가능).");
                          void loadHidden("", 1).then(setHidden);
                          /* ⚠ 제외한 뒤에는 **반드시 다시 스캔**해야 한다.
                             제외는 hidden=1 로 표시하는 것이고 중복 스캔은 hidden=0
                             행만 본다. 재스캔을 안 하면 방금 제외한 그룹이 화면에
                             그대로 남아 "제외했는데 목록에서 사라지지 않는다" 가 된다
                             (사용자 신고 2026-09-07). 실측: 제외 직후 다시 세면
                             중복 그룹 0개인데 화면은 354,261개를 계속 보여줬다.
                             원본도 같은 자리에서 재스캔한다
                             (main_window.py _on_dup_repair_done → _run_duplicate_scan). */
                          setDupDone(
                            `${paths.length.toLocaleString()}개 항목을 검색에서 제외했습니다.\n\n`
                            + "파일과 인덱스는 그대로 유지됩니다.\n"
                            + "[제외 관리]에서 언제든 검색 결과로 복원할 수 있습니다.");
                          /* 자동 재스캔 — 완료 팝업은 억제한다 (위 제외 완료 팝업과 중복) */
                          void runDupScan(true);
                        });
                      }}>예</button>
              <button className="btn" onClick={() => setDupConfirm(null)}>아니오</button>
            </div>
          </div>
        </div>
      )}

      {/* 중복 결과 파일 행 우클릭 (조사 D04) — 원본 메뉴 항목 두 개 그대로 */}
      {dupMenu && (
        <div className="ctxmenu" style={{ left: dupMenu.x, top: dupMenu.y }}
             onMouseDown={(event) => event.stopPropagation()}>
          <button className="ctxitem" onClick={() => {
            const path = dupMenu.path;
            setDupMenu(null);
            void revealInExplorer(path);
          }}>탐색기에서 보기</button>
          <button className="ctxitem" onClick={() => {
            const path = dupMenu.path;
            setDupMenu(null);
            /* 클립보드가 막힌 환경(권한 없음)에서도 창이 멈추지 않게 한다 */
            void navigator.clipboard?.writeText(path).catch(() => {
              setDupStatus("경로를 복사할 수 없습니다");
            });
          }}>경로 복사</button>
        </div>
      )}

      {/* 원본 _open_suppressed_manager — 별도 창으로 뜬다 (환경설정 위에 겹침) */}
      {suppressedOpen && <SuppressedManager runAdmin={runAdmin}
                                            onBusyChange={setChildBusy}
                                            onClose={() => {
        setSuppressedOpen(false);
        /* 닫을 때 개수 갱신 — 원본도 _refresh_suppressed_label 을 다시 부른다 */
        void loadHidden("", 1).then(setHidden);
      }} />}
    </Modal>
  );
}
