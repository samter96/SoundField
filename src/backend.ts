import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import type { Lib, Row, TreeNode } from "./data";
import { getOverride, loadLayoutStore } from "./layoutStore";

type DbRow = {
  id: string;
  file_path: string;
  file_name: string;
  file_size: number;
  duration: number;
  sample_rate: number;
  channels: number;
  bit_depth: number;
  codec: string | null;
  bitrate: number;
  title: string | null;
  artist: string | null;
  album: string | null;
  genre: string | null;
  comments: string | null;
};

type DbRoot = {
  path: string;
  last_indexed_at?: number | null;
  sort_order?: number | null;
  count: number;
  pending: number;
  failed: number;
  exists: boolean;
};

type DbBlacklist = {
  path: string;
  description: string;
};

export type SearchRequest = {
  matchers: Array<{ field: string; operator: string | null; value: string }>;
  min_duration: number;
  max_duration: number | null;
  sample_rate: number | null;
  channels: number | null;
  min_channels: number | null;
  path_prefixes: string[] | null;
  precise: boolean;
  limit: number;
};

const extToFmt = (name: string): Row["fmt"] => {
  const ext = name.split(".").pop()?.toUpperCase();
  if (ext === "MP3") return "MP3";
  if (ext === "FLAC") return "FLAC";
  if (ext === "AIF" || ext === "AIFF") return "AIFF";
  return "WAV";
};

const dirname = (path: string) => {
  const i = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return i >= 0 ? path.slice(0, i) : "";
};

function toRow(row: DbRow, index: number): Row {
  return {
    id: index,
    dbId: row.id,
    dur: Number(row.duration) || 0,
    fmt: extToFmt(row.file_name),
    name: row.file_name,
    sr: Number(row.sample_rate) || 0,
    ch: Number(row.channels) || 0,
    bitDepth: Number(row.bit_depth) || 0,
    codec: row.codec ?? "",
    size: Number(row.file_size) || 0,
    path: dirname(row.file_path),
    fullPath: row.file_path,
    cat: row.genre || row.title || "",
    title: row.title ?? "",
    artist: row.artist ?? "",
    album: row.album ?? "",
    genre: row.genre ?? "",
    comments: row.comments ?? "",
    bitrate: Number(row.bitrate) || 0,
  };
}

export async function loadInitialRows(limit: number): Promise<Row[] | null> {
  try {
    const rows = await invoke<DbRow[]>("sf_initial_rows", { limit });
    return rows.map(toRow);
  } catch {
    return null;
  }
}

/* ⚠ 실패를 조용히 삼키지 말 것 (조사 S02). 원본은 오류 문구를 상태줄에 그대로
   띄우고, 실패한 검색은 '했다' 고 기록하지 않아 같은 조건으로 다시 시도한다
   (main_window.py:7291). 예전에는 null 만 돌려줘서 화면에는 옛 결과가 새 결과처럼
   남고, 브리지가 잠깐 죽었다 살아나도 같은 검색이 계속 건너뛰어졌다. */
export async function searchRows(req: SearchRequest):
    Promise<{ rows: Row[] | null; error: string }> {
  try {
    const rows = await invoke<DbRow[]>("sf_search_rows", { req });
    return { rows: rows.map(toRow), error: "" };
  } catch (err) {
    return { rows: null, error: String((err as { message?: string })?.message ?? err) };
  }
}

function mapRoots(roots: DbRoot[]): Lib[] {
  return roots.map((root, index) => {
    const trimmed = root.path.replace(/[\\/]+$/, "");
    const name = trimmed.split(/[\\/]/).filter(Boolean).at(-1) || root.path;
    /* count 가 -1 이면 아직 계산 전(2단계 로딩) — 화면에서 "…" 로 표시한다.
       상태 우선순위는 원본 _status_color: 경로 없음 -> 실패 -> 대기 -> 정상 */
    const counting = Number(root.count) < 0;
    return {
      id: `db-root-${index}`,
      name,
      path: root.path,
      count: counting ? -1 : Number(root.count) || 0,
      /* 원본 _status_color 우선순위: 경로 없음 → 실패 → 대기 → 정상.
         경로 없음만 hollow(속 빈 원)이고 실패는 채운 원이다 (_GlowDot hollow=not exists). */
      state: (!root.exists ? "missing" : root.failed > 0 ? "fail"
              : root.pending > 0 ? "busy" : "ok") as Lib["state"],
      lastIndexedAt: root.last_indexed_at ?? null,
      sortOrder: root.sort_order ?? index,
      pending: counting ? -1 : Number(root.pending) || 0,
      failed: counting ? -1 : Number(root.failed) || 0,
      exists: root.exists,
    };
  });
}

/** 루트 목록만 (즉시) — 원본 refresh_async 1단계 */
export async function loadLibraryRootsBasic(): Promise<Lib[] | null> {
  try {
    return mapRoots(await invoke<DbRoot[]>("sf_library_roots_basic"));
  } catch {
    return null;
  }
}

/* ── 무거운 조회 합치기 ───────────────────────────────────────────────────────
   루트 카운트와 폴더 트리는 158만 행을 훑어 각각 **수 초**가 걸린다
   (실측: 릴리스에서 루트 4.6초 / 트리 4.7초). 시작 직후 서로 다른 경로에서
   같은 조회가 겹쳐 들어오면 그 스캔이 두 번 돌아 디스크만 두 배로 먹는다
   (실측: 시작 로그에 ROOTS 가 두 번 찍혔다).
   진행 중인 같은 조회가 있으면 **그 약속을 그대로 공유**한다 — 두 호출자 모두
   같은 결과를 받고 스캔은 한 번만 돈다. 끝나면 캐시를 비워 다음 갱신은 새로 읽는다. */
const inFlight = new Map<string, Promise<unknown>>();

function shared<T>(key: string, run: () => Promise<T>): Promise<T> {
  const running = inFlight.get(key) as Promise<T> | undefined;
  if (running) return running;
  const started = run().finally(() => { inFlight.delete(key); });
  inFlight.set(key, started);
  return started;
}

/** 루트별 파일 수/대기/실패까지 (수초) — 원본 LibraryStatusWorker 2단계 */
export async function loadLibraryRoots(): Promise<Lib[] | null> {
  return shared("roots", async () => {
    try {
      return mapRoots(await invoke<DbRoot[]>("sf_library_roots"));
    } catch {
      return null;
    }
  });
}

export async function loadBlacklistPaths(): Promise<Array<{ path: string; description: string }> | null> {
  try {
    return await invoke<DbBlacklist[]>("sf_blacklist_paths");
  } catch {
    return null;
  }
}

export async function loadIndexedCount(): Promise<number | null> {
  try {
    return await invoke<number>("sf_indexed_count");
  } catch {
    return null;
  }
}

export async function loadFolderTree(): Promise<TreeNode | null> {
  return shared("tree", async () => {
    try {
      return await invoke<TreeNode>("sf_folder_tree");
    } catch {
      return null;
    }
  });
}

type AudioCommand = {
  command: "load" | "play" | "restart" | "pause" | "stop" | "seek" | "rate" | "volume" | "binaural" | "layout" | "quit";
  path?: string;
  meta?: Record<string, unknown>;
  position_ms?: number;
  rate?: number;
  volume?: number;
  enabled?: boolean;
  layout?: string;
  restart?: boolean;
  play?: boolean;
};

async function audio(cmd: AudioCommand): Promise<boolean> {
  try {
    await invoke("sf_audio", { cmd });
    return true;
  } catch (error) {
    console.error("오디오 명령 실패", error);
    return false;
  }
}

export type AdminRequest = {
  op: "add_index" | "index" | "remove_roots" | "repair_fts" | "backfill_fts" | "detect" | "parser_upgrade" |
      "ensure_term_index" | "background_meta" | "retry_paths" |
      "retry_scope" | "delete_paths" | "delete_scope" | "hide_paths" |
      "unhide_paths" | "unhide_all" | "unhide_ids" | "mark_dup_orphans";
  path?: string;
  paths?: string[];
  force?: boolean;
  purge?: boolean;
  include_pending?: boolean;
  include_failed?: boolean;
};

export async function runAdmin(req: AdminRequest): Promise<Record<string, unknown>> {
  try { return await invoke<Record<string, unknown>>("sf_admin", { req }); }
  catch (error) { return { success: false, message: String(error) }; }
}

export async function cancelAdmin(): Promise<boolean> {
  try { return await invoke<boolean>("sf_admin_cancel"); }
  catch { return false; }
}

export type IndexEvent = {
  phase: string; total: number; scanned: number; indexed: number; skipped: number;
  errors: number; current_file: string; current_root: string; message: string;
  new_count: number; phase1_total: number; fts_total: number; fts_done: number;
  percent: number; eta_seconds: number | null;
  /** 2단계 세션 누적 — indexed 는 이번 조각만, meta_done 은 세션 전체 */
  base_done?: number; meta_done?: number;
};

export function onIndexEvent(cb: (info: IndexEvent) => void): () => void {
  let stop: (() => void) | null = null;
  let dead = false;
  void listen<IndexEvent>("index-event", (event) => cb(event.payload))
    .then((un) => { if (dead) un(); else stop = un; });
  return () => { dead = true; stop?.(); };
}

/* ── 엔진 위치 보고 (원본 _on_tick 이 player.position() 을 폴링하는 것에 대응) ──
   사이드카가 16ms 마다 보내는 {position_ms, duration_ms, playing, binaural} 을
   그대로 넘긴다. 값이 바뀔 때만 온다. */
export type AudioPos = {
  position_ms: number;
  duration_ms: number;
  playing: boolean;
  binaural: boolean;
  /** 이 위치가 어느 파일의 것인지 — 프런트가 이전 파일의 잔여 보고를 걸러낸다 */
  path: string;
};

export function onAudioPos(cb: (info: AudioPos) => void): () => void {
  let stop: (() => void) | null = null;
  let dead = false;
  void listen<AudioPos>("audio-pos", (event) => cb(event.payload))
    .then((un) => { if (dead) un(); else stop = un; });
  return () => { dead = true; stop?.(); };
}

export type AudioEvent =
  | { type: "availability"; available: boolean; reason: string }
  | { type: "binaural-status"; message: string }
  | { type: "binaural-layout"; preset: string; topology: string; source_order: number }
  | { type: "playback-state"; state: string; path?: string }
  | { type: "media-status"; status: string }
  | { type: "error"; message: string };

export function onAudioEvent(cb: (info: AudioEvent) => void): () => void {
  let stop: (() => void) | null = null;
  let dead = false;
  void listen<AudioEvent>("audio-event", (event) => cb(event.payload))
    .then((un) => { if (dead) un(); else stop = un; });
  return () => { dead = true; stop?.(); };
}

export const audioBridge = {
  async load(row: Row): Promise<boolean> {
    if (!row.fullPath) return false;
    const layout = getOverride(await loadLayoutStore(), row.fullPath, row.ch);
    return audio({
      command: "load",
      path: row.fullPath,
      meta: {
        sample_rate: row.sr,
        channels: row.ch,
        bit_depth: row.bitDepth,
        codec: row.codec,
        bitrate: row.bitrate,
        title: row.title,
        artist: row.artist,
        album: row.album,
        genre: row.genre,
        comments: row.comments,
        ...(layout ? { binaural_layout: layout } : {}),
      },
    });
  },
  play: () => audio({ command: "play" }),
  restart: () => audio({ command: "restart" }),
  pause: () => audio({ command: "pause" }),
  stop: () => audio({ command: "stop" }),
  seek: (positionMs: number) => audio({ command: "seek", position_ms: Math.max(0, Math.round(positionMs)) }),
  rate: (rate: number) => audio({ command: "rate", rate }),
  volume: (gain: number) => audio({ command: "volume", volume: Math.max(0, Math.min(2, gain)) }),
  binaural: (enabled: boolean) => audio({ command: "binaural", enabled }),
  layout: (layout: string, restart = false, play = false) =>
    audio({ command: "layout", layout, restart, play }),
};

/* ── 파형 데이터 (원본 peaks_cache / _extract_peaks / _read_raw_samples) ──
   levels[i] 는 원본 PeaksArray 와 같은 (slices, channels, 2) = (min, max) 구조를
   int16(x32767) 로 눌러 base64 로 받은 것이다. 인덱스 = (slice * channels + ch) * 2.
   원본이 쓰는 레벨 후보는 LEVEL_SLICES = (1024, 4096, 16384, 65536) 이고,
   파일이 짧으면 큰 레벨은 생략된다. */
export type WaveLevel = { slices: number; data: Int16Array };

export type WavePeaks = {
  channels: number;
  sampleRate: number;
  nSamples: number;
  /** 원본 세그먼트 검출 결과 (초 단위 [시작, 끝]) */
  segments: Array<[number, number]>;
  levels: WaveLevel[];
};

function b64ToInt16(b64: string): Int16Array {
  if (!b64) return new Int16Array(0);
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer, 0, bytes.length >> 1);
}

type RawWavePeaks = {
  channels: number;
  sample_rate: number;
  n_samples: number;
  segments: Array<[number, number]>;
  levels: Array<{ slices: number; data: string }>;
};

/* peaks / quick 공통 디코더 — 응답 모양이 같다 */
function decodeWavePeaks(raw: unknown): WavePeaks | null {
  const d = raw as RawWavePeaks | null;
  if (!d || !d.levels?.length) return null;
  return {
    channels: Number(d.channels) || 1,
    sampleRate: Number(d.sample_rate) || 0,
    nSamples: Number(d.n_samples) || 0,
    segments: (d.segments || []) as Array<[number, number]>,
    levels: d.levels.map((lv) => ({ slices: Number(lv.slices) || 0, data: b64ToInt16(lv.data) })),
  };
}

export async function loadWavePeaks(path: string): Promise<WavePeaks | null> {
  try {
    return decodeWavePeaks(
      await invoke<RawWavePeaks>("sf_waveform", { req: { path, mode: "peaks" } }));
  } catch {
    return null;
  }
}

/* 개발용 진단 — %TEMP%\soundfield_poc_debug.log. 이 환경에서 Tauri 창을 화면으로
   검증할 수 없어(캡처 검정/포커스 실패) 데이터 연결 확인용 통로로 쓴다. */
/* 초기 데이터가 준비됐다고 알린다 — Rust 가 메인 창을 띄우고 스플래시 창을 닫는다
   (원본 splash.finish(win) 과 같은 자리). */
export async function appLoaded(): Promise<void> {
  try {
    await invoke("sf_loaded");
  } catch {
    /* Rust 쪽 10초 안전망이 대신 띄운다 */
  }
}

export function debugLog(msg: string) {
  invoke("sf_debug", { msg }).catch(() => {});
}

/* ── 탐색기에서 보기 (원본 results_table._reveal_in_explorer) ── */
export async function revealInExplorer(path: string) {
  try {
    await invoke("sf_reveal", { path });
    return true;
  } catch {
    return false;
  }
}

/* ── 재생 히스토리 (원본 %LOCALAPPDATA%\SoundField\history.json) ──
   "오래된 -> 최신" 순으로 오고, 화면에서는 최신이 위로 간다.
   PoC 는 원본 파일을 덮어쓰지 않는다 (읽기만). */
export async function loadHistory(): Promise<string[] | null> {
  try {
    return await invoke<string[]>("sf_history");
  } catch {
    return null;
  }
}

export async function cancelWaveform(): Promise<boolean> {
  try { return await invoke<boolean>("sf_waveform_cancel"); }
  catch { return false; }
}

export async function recordHistory(path: string): Promise<string[] | null> {
  try { return await invoke<string[]>("sf_history_record", { path }); }
  catch { return null; }
}

export async function clearHistory(): Promise<boolean> {
  try { await invoke("sf_history_clear"); return true; }
  catch { return false; }
}

export async function loadRowByPath(path: string): Promise<Row | null> {
  try {
    const row = await invoke<DbRow | null>("sf_row_by_path", { path });
    return row ? toRow(row, 0) : null;
  } catch { return null; }
}

/* 원본 QDesktopServices.openUrl — IEM 설치 안내의 "무료 설치 페이지 열기" */
export async function openExternal(url: string): Promise<boolean> {
  try {
    await invoke("sf_open_url", { url });
    return true;
  } catch {
    return false;
  }
}

/* ── 검색 제외(숨김) 목록 (원본 get_hidden_paths / count_hidden) ──
   rows 는 표시 상한까지, matched 는 검색어 일치 전체, total 은 숨김 전체.
   orphan=true 면 [중복 아님 · 미복원] 태그 (표시 직전 유효성 재확인까지 백엔드에서 한다). */
export type HiddenRow = { path: string; orphan: boolean };
export type HiddenResult = { rows: HiddenRow[]; matched: number; total: number };

export async function loadHidden(search = "", limit = 2000): Promise<HiddenResult> {
  try {
    return await invoke<HiddenResult>("sf_hidden", { search, limit });
  } catch {
    return { rows: [], matched: 0, total: 0 };
  }
}

/* 검색 색인 어긋남 여부 — 원본 _check_fts_stale 이 이 값으로 "⚠ 검색 정리" 버튼과
   세션당 1회 안내 팝업을 띄운다. */
export async function loadFtsStale(): Promise<boolean> {
  try {
    return await invoke<boolean>("sf_fts_stale");
  } catch {
    return false;
  }
}

/* ── 미완료 항목 (원본 Database.get_incomplete_metadata_under) ── */
export type IncompleteRow = {
  file_path: string;
  file_name: string;
  status: "pending" | "failed";
  reason: string;
};

/* 원본 _FetchIncompleteWorker 는 (rows, total) 을 함께 돌려준다 — limit(5000) 초과분은
   "표시 N / 필터 전체 T" 로 안내하고 [필터된 전체] 버튼으로 처리하기 때문이다.
   pending/failed 는 다이얼로그 머리글에 쓰는 라이브러리 전체 대기/실패 수. */
export type IncompleteResult = {
  rows: IncompleteRow[];
  total: number;
  pending: number;
  failed: number;
};

export async function loadIncomplete(root: string, includePending: boolean,
                                     includeFailed: boolean,
                                     limit = 5000): Promise<IncompleteResult | null> {
  try {
    return await invoke<IncompleteResult>("sf_incomplete", {
      root, includePending, includeFailed, limit,
    });
  } catch {
    return null;
  }
}

/* ── 블랙리스트 쓰기 ──
   원본 Database.add_blacklist_path / remove_blacklist_path 와 같은 동작이지만,
   실제 index.db 대신 PoC 오버레이 DB(%LOCALAPPDATA%\SoundField\poc\overlay.db)에
   기록한다. 사용자 승인 정책: 원본 파일은 건드리지 않는다. */
/* 라이브러리 표시 순서 저장 — 원본 manager.reorder_roots (library_roots.sort_order).
   PoC 는 index.db 가 읽기 전용이라 오버레이(root_order)에 기록한다. */
export async function setRootOrder(paths: string[]): Promise<boolean> {
  try {
    await invoke("sf_root_order_set", { paths });
    return true;
  } catch {
    return false;
  }
}

export async function addBlacklistPath(path: string, description: string) {
  try {
    await invoke("sf_blacklist_add", { path, description });
    return true;
  } catch {
    return false;
  }
}

export async function removeBlacklistPath(path: string) {
  try {
    await invoke("sf_blacklist_remove", { path });
    return true;
  } catch {
    return false;
  }
}

/* 파일의 크기·수정시각 도장 — 파형 캐시가 바뀐 파일을 재사용하지 않게 한다 (Q15).
   파일이 없거나 읽을 수 없으면 빈 문자열이다 (그때는 검사하지 않는다). */
export async function fileStamp(path: string): Promise<string> {
  try {
    return await invoke<string>("sf_file_stamp", { path });
  } catch {
    return "";
  }
}

/* ── 설정 읽기/쓰기 (~/.soundfield_config.json) ── */
export async function readConfigJson(): Promise<string | null> {
  try {
    const text = await invoke<string>("sf_config_read");
    return text || null;
  } catch {
    return null;
  }
}

export async function writeConfigJson(json: string) {
  try {
    await invoke("sf_config_write", { json });
    return true;
  } catch {
    return false;
  }
}

/* ── 중복 검수 (원본 Database.find_duplicate_groups) ──
   파일명 + 파일 크기가 같은 항목 그룹. 스캔은 읽기 전용이라 그대로 옮겼다.
   "검색 제외"/"제외 관리" 는 DB 쓰기라 아직 연결하지 않았다. */
export type DupGroupRow = { file_name: string; file_size: number; paths: string[] };

/* ⚠ 개수 상한을 넘기지 않는다 — 원본 find_duplicates 에도 상한이 없다.
   예전에는 5,000 개만 받아서, 실측 354,261 개 중 98.6% 가 안 보였다. */
export async function scanDuplicates(): Promise<DupGroupRow[] | null> {
  try {
    return await invoke<DupGroupRow[]>("sf_duplicates");
  } catch {
    return null;
  }
}

/* 관리 작업(검색 제외 등) 진행 상황 — 인덱싱 진행(index-event)과 **다른 통로**다.
   섞으면 메인 창의 인덱싱 표시가 잘못 켜진다 (sf_admin.emit_op_progress 주석). */
export type AdminOpProgress = { op: string; done: number; total: number };

export function onAdminOpProgress(cb: (value: AdminOpProgress) => void): () => void {
  let stop = () => {};
  void listen<AdminOpProgress>("admin-op-progress", (event) => cb(event.payload))
    .then((unlisten) => { stop = unlisten; });
  return () => stop();
}

/* 중복 검수 진행 상황 — 원본 progress_cb(done, total) 를 그대로 받는다.
   훑는 행이 158만 개라 5만 행마다 한 번씩 온다 (Rust 쪽에서 간격을 정한다). */
export type DupProgress = { done: number; total: number };

export function onDupProgress(cb: (value: DupProgress) => void): () => void {
  let stop = () => {};
  void listen<DupProgress>("dup-progress", (event) => cb(event.payload))
    .then((unlisten) => { stop = unlisten; });
  return () => stop();
}

/* ── 바이노럴 채널 배치 판정 (원본 app.binaural.resolve_layout) ──
   원본 메뉴(_build_binaural_layout_menu)가 쓰는 값만 받아온다.
   판정 로직을 다시 구현하지 않고 원본 함수를 그대로 호출한다. */
export type LayoutInfo = {
  channels: number;
  channel_mask: number;
  automatic_preset: string;
  automatic_topology: string;
  /* 채널 **순서** (wave/film) — 채널 수와 별개 정보. 핸드오버 문서 4.1 */
  automatic_channel_order: string;
  automatic_source: string;
  can_auto_play: boolean;
  candidates: string[];
  reason: string;
  applied_preset: string;
  applied_can_play: boolean;
  /* 원본 _update_binaural_layout_control 의 툴팁 재료 (적용된 판정 기준) */
  applied_topology: string;
  applied_channel_order: string;
  applied_source: string;
  applied_order: number;
  applied_reason: string;
  /* 이 프리셋이 가질 수 있는 채널 순서들 — 팝업이 실제 순서를 밝히는 데 쓴다.
     두 순서가 다를 때만 온다 (quad·6.1 은 빈 배열). 표는 파이썬이 정본이다. */
  order_choices?: { order: string; name: string; roles: string[] }[];
};

/* 빠른 러프 파형 (원본 WaveformQuickRunnable) — .wav 이고 30MB 이상일 때만 값이 온다.
   건너뛸 파일이면 null 이 온다 (그때는 곧바로 full 추출로 간다). */
export async function loadQuickPeaks(path: string): Promise<WavePeaks | null> {
  try {
    const raw = await invoke<Record<string, unknown>>("sf_waveform", {
      req: { path, mode: "quick" },
    });
    if (!raw || raw.skipped) return null;
    return decodeWavePeaks(raw);
  } catch {
    return null;
  }
}

/* ── DAW 로 끌어낼 파일 만들기 (원본 _resolve_export_path, player_widget.py:4118) ──
   영역이 있으면 crop_wav_region 으로 잘라 임시 WAV 를 만들고, 배속이 1.0x 가 아니면
   그 결과에 render_speed_wav 로 배리스피드를 렌더한 파일 경로를 돌려준다.
   실패하면 오류를 전달한다. 요청과 다른 원본 파일로 대체하지 않는다. */
export async function exportRegion(path: string, startSec: number | null,
                                   endSec: number | null,
                                   rate = 1): Promise<string> {
  const out = await invoke<{ path: string }>("sf_waveform", {
    req: { path, mode: "region", start_sec: startSec, end_sec: endSec, rate },
  });
  if (!out?.path) throw new Error("내보낼 파일을 만들지 못했습니다");
  return out.path;
}

export async function loadLayoutInfo(path: string, channels: number,
                                     override = ""): Promise<LayoutInfo | null> {
  try {
    return await invoke<LayoutInfo>("sf_waveform", {
      req: { path, mode: "layout", channels, override },
    });
  } catch {
    return null;
  }
}
