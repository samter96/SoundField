/* ── 중복 검수 결과 보관 (사용자 지시 2026-09-15) ─────────────────────────────
   창을 닫았다 다시 열면 **직전 결과와 그때 시각**이 그대로 뜬다. 다시 스캔하기
   전까지는 바뀌지 않는다. 제외를 실행해 목록이 갱신되면 갱신된 쪽으로 덮어쓴다.

   ⚠ 상한이 있다 (사용자 결정). 첫 스캔은 35만 그룹까지 나온 적이 있어 통째로
     보관하면 100MB 급 파일이 된다. 상한을 넘으면 **결과 없이 시각만** 남기고
     화면이 "결과가 너무 커서 다시 스캔이 필요하다" 고 안내한다.
     저장 위치: %LOCALAPPDATA%\SoundField\dup_scan.json */
import { invoke } from "@tauri-apps/api/core";

/** 이 개수를 넘는 결과는 보관하지 않는다 (약 14MB 선). */
export const DUP_CACHE_MAX_GROUPS = 50_000;

export type DupGroup = { name: string; size: number; paths: string[] };

export type DupCache = {
  /** 스캔이 끝난 시각 (밀리초). 제외 후 갱신되면 그 시각으로 바뀐다 */
  scannedAt: number;
  /** 상한을 넘겨 결과를 버렸으면 true — 목록은 비어 있다 */
  tooLarge: boolean;
  /** 상한을 넘겼을 때의 실제 그룹 수 (안내 문구용) */
  totalGroups: number;
  groups: DupGroup[];
};

export async function loadDupCache(): Promise<DupCache | null> {
  try {
    const raw = await invoke<string>("sf_dup_cache_read");
    if (!raw) return null;
    const data = JSON.parse(raw) as Partial<DupCache>;
    if (typeof data.scannedAt !== "number") return null;
    return {
      scannedAt: data.scannedAt,
      tooLarge: Boolean(data.tooLarge),
      totalGroups: Number(data.totalGroups ?? (data.groups?.length ?? 0)),
      groups: Array.isArray(data.groups) ? data.groups : [],
    };
  } catch {
    return null;   /* 기록이 깨졌으면 없는 셈 친다 — 스캔하면 다시 만들어진다 */
  }
}

export async function saveDupCache(groups: DupGroup[]): Promise<DupCache> {
  const tooLarge = groups.length > DUP_CACHE_MAX_GROUPS;
  const cache: DupCache = {
    scannedAt: Date.now(),
    tooLarge,
    totalGroups: groups.length,
    groups: tooLarge ? [] : groups,
  };
  try {
    await invoke("sf_dup_cache_write", { json: JSON.stringify(cache) });
  } catch {
    /* 저장에 실패해도 화면은 그대로 쓴다 — 다음에 다시 스캔하면 된다 */
  }
  return cache;
}

/** "9월 15일 10:42" 처럼 — 목록 위에 마지막 스캔 시각을 적는다 */
export function fmtScanTime(ms: number): string {
  const d = new Date(ms);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}월 ${d.getDate()}일 ${two(d.getHours())}:${two(d.getMinutes())}`;
}
