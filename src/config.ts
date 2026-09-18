/* 기존 PyQt 앱과 같은 설정 파일을 읽고 쓴다 (~/.soundfield_config.json).
   Tauri 버전이 메인 앱 역할을 하면서 원본 키는 그대로 공유하고,
   Tauri 전용 값만 poc_* 키에 담는다. 기본값은 원본 main_window.py:_config_data 와 맞춘다. */

/* 원본 main_window.py:_setup_shortcuts (5007) + _setup_shortcuts_tab (3940).
   6개만 변경 가능하고, 휠 단축키(Ctrl+휠 가로 확대 / Ctrl+Alt+휠 세로 확대 /
   Shift+휠 가로 스크롤)는 변경 불가로 고정 표시된다. */
export type Shortcuts = {
  play_pause: string;
  seek_to_start: string;
  toggle_loop: string;
  toggle_segments: string;
  toggle_history: string;
  /* 바이노럴 모니터링 on/off — 원본에는 없던 신규 단축키 (사용자 요청 2026-09-03).
     기본값 B(Binaural): 이미 쓰는 단일 키(Space/Home/R/S/H)와 겹치지 않고,
     검색 입력 중에는 단축키가 무시되므로 타이핑을 방해하지 않는다. */
  toggle_binaural: string;
  open_settings: string;
};

export const SHORTCUT_DEFAULTS: Shortcuts = {
  play_pause: "Space",
  seek_to_start: "Home",
  toggle_loop: "R",
  toggle_segments: "S",
  toggle_history: "H",
  toggle_binaural: "B",
  open_settings: "Ctrl+P",
};

export type ColumnPref = { key: string; visible: boolean; width: number };

/** 화면 표시 언어 — 원본에는 없던 항목(원본은 한글 전용)이라 PoC 전용 키에 담는다
 *  (poc_side_w / poc_player_h 와 같은 방식). */
export type UiLang = "ko" | "en";

export type UserConfig = {
  theme: "grey" | "dark" | "light";
  searchLimit: number;
  doubleClickToPlay: boolean;
  compactResults: boolean;
  playbackRestartFromZero: boolean;
  autoPreview: boolean;
  stopOnDrag: boolean;
  indexOnIdle: boolean;
  indexFocusMode: boolean;
  blacklistExpandedH: number;
  historyWidth: number;
  historyVisible: boolean;
  /* 원본 player_widget: 볼륨 슬라이더 위치(0~1000, 750=0dB) / 재생 속도 */
  volumePos1k: number;
  speedRate: number;
  shortcuts: Shortcuts;
  /* 원본 results_table._save_layout — 컬럼 순서·표시·너비 (순서 그대로가 사용자 레이아웃) */
  columns: ColumnPref[] | null;
  /* 원본 config "folder_display_names" — key 는 normpath.rstrip.lower */
  folderDisplayNames: Record<string, string>;
  /* 원본 config "filters" — 재시작 후 검색어/길이/샘플레이트/채널을 그대로 복원한다
     (_restore_filters, main_window.py:4942). sample_rate/channels 는 콤보 **인덱스**다. */
  filters: {
    matchers: Array<{ field?: string | null; operator?: string | null; value?: string }>;
    minDur: number;
    maxDur: number;
    sampleRateIndex: number;
    channelsIndex: number;
  } | null;
  /* 원본 config "favorites" / "tabs" / "last_selection" */
  favorites: string[];
  userTabs: Array<{ name: string; items: string[]; excluded?: string[] }>;
  lastSelection: { tab: string; path: string } | null;
  /* 분할 위치 — 원본은 Qt splitter state(불투명 base64)를 저장해 그대로 못 읽는다.
     PoC 는 같은 목적의 자체 키(poc_side_w / poc_player_h)를 쓴다. */
  sideWidth: number;
  playerHeight: number;
  /* 표시 언어 (기본 한국어) — 창 제어줄의 슬라이드 토글로 바꾼다 */
  lang: UiLang;
  loadedFromFile: boolean;
};

export const CONFIG_DEFAULTS: UserConfig = {
  theme: "grey",
  searchLimit: 500,
  doubleClickToPlay: false,
  compactResults: false,
  playbackRestartFromZero: true,
  autoPreview: false,
  stopOnDrag: true,
  indexOnIdle: false,
  indexFocusMode: false,
  blacklistExpandedH: 220,
  historyWidth: 280,
  historyVisible: false,
  volumePos1k: 750,
  speedRate: 1.0,
  shortcuts: SHORTCUT_DEFAULTS,
  columns: null,
  folderDisplayNames: {},
  filters: null,
  favorites: [],
  userTabs: [],
  lastSelection: null,
  sideWidth: 250,
  playerHeight: 180,
  lang: "ko",
  loadedFromFile: false,
};

/* 원본 theme.py 의 _THEME_ALIASES 대응 — 저장값은 alias 일 수 있다. */
function normalizeTheme(value: unknown): UserConfig["theme"] {
  const v = String(value ?? "").toLowerCase();
  if (v === "light") return "light";
  if (v === "neon" || v === "dark") return "dark";
  if (v === "neutral" || v === "grey" || v === "gray") return "grey";
  return CONFIG_DEFAULTS.theme;
}

/* 설정은 Rust 명령으로 ~/.soundfield_config.json 을 읽고 쓴다. */
export async function loadUserConfig(): Promise<UserConfig> {
  try {
    const { readConfigJson } = await import("./backend");
    const raw = await readConfigJson();
    if (!raw) return { ...CONFIG_DEFAULTS };
    const d = JSON.parse(raw) as Record<string, unknown>;
    return {
      theme: normalizeTheme(d.theme),
      searchLimit: Number(d.search_limit ?? CONFIG_DEFAULTS.searchLimit),
      doubleClickToPlay: Boolean(d.double_click_to_play ?? CONFIG_DEFAULTS.doubleClickToPlay),
      compactResults: Boolean(d.compact_results ?? CONFIG_DEFAULTS.compactResults),
      playbackRestartFromZero: Boolean(d.playback_restart_from_zero ?? CONFIG_DEFAULTS.playbackRestartFromZero),
      autoPreview: Boolean(d.auto_preview ?? CONFIG_DEFAULTS.autoPreview),
      stopOnDrag: Boolean(d.stop_on_drag ?? CONFIG_DEFAULTS.stopOnDrag),
      indexOnIdle: Boolean(d.index_on_idle ?? CONFIG_DEFAULTS.indexOnIdle),
      indexFocusMode: Boolean(d.index_focus_mode ?? CONFIG_DEFAULTS.indexFocusMode),
      blacklistExpandedH: Number(d.blacklist_expanded_h ?? CONFIG_DEFAULTS.blacklistExpandedH),
      historyWidth: Number(d.history_width ?? CONFIG_DEFAULTS.historyWidth),
      historyVisible: Boolean(d.history_visible ?? CONFIG_DEFAULTS.historyVisible),
      volumePos1k: Number(d.volume_pos1k ?? CONFIG_DEFAULTS.volumePos1k),
      speedRate: Number(d.speed_rate ?? CONFIG_DEFAULTS.speedRate),
      shortcuts: { ...SHORTCUT_DEFAULTS, ...((d.shortcuts as object) ?? {}) } as Shortcuts,
      columns: Array.isArray(d.columns)
        ? (d.columns as Array<Record<string, unknown>>)
            .filter((c) => typeof c?.key === "string")
            .map((c) => ({ key: String(c.key), visible: Boolean(c.visible), width: Number(c.width) || 0 }))
        : null,
      folderDisplayNames: (d.folder_display_names && typeof d.folder_display_names === "object")
        ? (d.folder_display_names as Record<string, string>)
        : {},
      filters: (() => {
        const f = d.filters as Record<string, unknown> | undefined;
        if (!f || typeof f !== "object") return null;
        return {
          matchers: Array.isArray(f.matchers)
            ? (f.matchers as Array<Record<string, unknown>>).map((m) => ({
                field: (m.field as string) ?? null,
                operator: (m.operator as string) ?? null,
                value: String(m.value ?? ""),
              }))
            : [],
          /* 원본은 0~36000 으로 클램프한다 */
          minDur: Math.max(0, Math.min(36000, Number(f.min_dur) || 0)),
          maxDur: Math.max(0, Math.min(36000, Number(f.max_dur) || 0)),
          sampleRateIndex: Number.isInteger(f.sample_rate) ? Number(f.sample_rate) : 0,
          channelsIndex: Number.isInteger(f.channels) ? Number(f.channels) : 0,
        };
      })(),
      favorites: Array.isArray(d.favorites) ? (d.favorites as string[]) : [],
      userTabs: Array.isArray(d.tabs)
        ? (d.tabs as Array<Record<string, unknown>>)
            .filter((t) => typeof t?.name === "string")
            .map((t) => ({
              name: String(t.name),
              items: Array.isArray(t.items) ? (t.items as string[]) : [],
              excluded: Array.isArray(t.excluded) ? (t.excluded as string[]) : [],
            }))
        : [],
      lastSelection: (() => {
        const sel = d.last_selection as Record<string, unknown> | undefined;
        if (!sel || typeof sel !== "object" || !sel.path) return null;
        return { tab: String(sel.tab ?? ""), path: String(sel.path) };
      })(),
      sideWidth: Number(d.poc_side_w) || CONFIG_DEFAULTS.sideWidth,
      playerHeight: Number(d.poc_player_h) || CONFIG_DEFAULTS.playerHeight,
      lang: d.poc_lang === "en" ? "en" : "ko",
      loadedFromFile: true,
    };
  } catch {
    /* Tauri 밖(브라우저)이거나 설정 파일을 못 읽으면 기본값을 쓴다. */
    return { ...CONFIG_DEFAULTS };
  }
}

/* 원본 키 이름 그대로 저장한다 (원본 _actual_save_config, main_window.py:4971).
   원본이 쓰는 키 중 Tauri 가 관리하지 않는 값(geometry/state/
   splitter/tabs/favorites 등)은 읽은 원본 JSON 을 보존해 덮어쓰지 않는다. */
export async function saveUserConfig(config: UserConfig,
                                     extra?: Record<string, unknown>): Promise<boolean> {
  try {
    const { readConfigJson, writeConfigJson } = await import("./backend");
    const raw = await readConfigJson();
    const base = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    const next: Record<string, unknown> = {
      ...base,
      theme: config.theme,
      search_limit: config.searchLimit,
      double_click_to_play: config.doubleClickToPlay,
      compact_results: config.compactResults,
      playback_restart_from_zero: config.playbackRestartFromZero,
      auto_preview: config.autoPreview,
      stop_on_drag: config.stopOnDrag,
      index_on_idle: config.indexOnIdle,
      index_focus_mode: config.indexFocusMode,
      blacklist_expanded_h: config.blacklistExpandedH,
      history_width: config.historyWidth,
      history_visible: config.historyVisible,
      volume_pos1k: config.volumePos1k,
      speed_rate: config.speedRate,
      shortcuts: config.shortcuts,
      ...(config.columns ? { columns: config.columns } : {}),
      folder_display_names: config.folderDisplayNames,
      ...(config.filters ? {
        filters: {
          matchers: config.filters.matchers,
          min_dur: config.filters.minDur,
          max_dur: config.filters.maxDur,
          sample_rate: config.filters.sampleRateIndex,
          channels: config.filters.channelsIndex,
        },
      } : {}),
      favorites: config.favorites,
      tabs: config.userTabs,
      /* ⚠ 마지막 선택은 **없어도 적어야 한다** (조사 C01). 예전에는 값이 있을
         때만 덮어써서, 라이브러리를 제거해 선택을 비워도 파일에는 옛 경로가 남고
         다음 실행에서 사라진 폴더를 다시 펼치려 했다. 원본은 경로가 없으면 빈
         문자열로 명시 저장한다 (main_window.py:4989). */
      last_selection: config.lastSelection ?? { tab: "", path: "" },
      poc_side_w: config.sideWidth,
      poc_player_h: config.playerHeight,
      poc_lang: config.lang,
      ...(extra ?? {}),
    };
    return await writeConfigJson(JSON.stringify(next, null, 2));
  } catch {
    return false;
  }
}
