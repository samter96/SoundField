/* ── 바이노럴 채널 배치 수동 저장 (원본 app/binaural.py:LayoutOverrideStore, 107) ──
   원본은 로컬 JSON 한 개에 파일별/폴더별 지정을 담는다:
     { "version": 2,
       "files":   { normcase(normpath(파일경로)): {preset, order?, source?} },
       "folders": { normcase(normpath(폴더경로)): { "채널수": {preset, order?} } } }
   조회 규칙(get):  파일 지정이 있으면 그것, 없으면 그 파일이 든 폴더의 "그 채널 수"
                    지정. 둘 다 없으면 "".
   범위(scope):     파일 지정이면 "file", 폴더 지정이면 "folder", 없으면 "".
   토큰 모양:       preset 과 order 가 함께 있으면 "preset|order" (order 는 wave/film).
   저장 위치는 Rust layout_store_path가 정한다. 현재 앱의 로컬 데이터 폴더에
   있는 binaural_layouts.json을 읽고 쓴다. */

import { invoke } from "@tauri-apps/api/core";

export type LayoutItem = { preset: string; order?: string; source?: string };
export type LayoutData = {
  version: 2;
  /* 값이 문자열인 항목은 **예전 버전이 저장한 것**이다 (조사 B05) */
  files: Record<string, LayoutItem | string>;
  folders: Record<string, Record<string, LayoutItem | string>>;
};

export const EMPTY_LAYOUT_DATA: LayoutData = { version: 2, files: {}, folders: {} };

/* 원본 _key: os.path.normcase(os.path.normpath(path)) — 윈도우에서 소문자 + 역슬래시.

   ⚠ 앞쪽에 붙은 연속 역슬래시를 **하나로 줄이지 말 것** (조사 B04).
     `\\\\server\\share` (네트워크 경로) 가 `\\server\share` 로 바뀌어,
     원본이 저장한 네트워크 파일의 배치 지정을 PoC 가 못 찾고 PoC 가 저장한 키도
     원본이 못 찾았다. 파이썬 normpath 실측(2026-09-08):
       · 맨 앞 역슬래시 묶음은 개수 그대로 남는다 (2개든 3개든)
       · 안쪽의 연속 역슬래시만 하나로 줄인다
       · `.` 은 버리고 `..` 는 한 칸 위로 올라간다 (루트 위로는 못 간다)
       · 끝의 역슬래시는 없앤다 */
export function layoutKey(path: string) {
  const raw = String(path ?? "").replace(/\//g, "\\");
  /* 접두: 앞쪽 역슬래시 묶음(네트워크 경로) 또는 드라이브 문자 */
  let prefix = "";
  let rest = raw;
  const lead = /^\\+/.exec(rest);
  if (lead) {
    prefix = lead[0];
    rest = rest.slice(prefix.length);
  } else if (/^[a-zA-Z]:/.test(rest)) {
    prefix = rest.slice(0, 2);
    rest = rest.slice(2);
    if (rest.startsWith("\\")) {
      prefix += "\\";
      rest = rest.replace(/^\\+/, "");
    }
  }
  const rooted = prefix.endsWith("\\");
  const parts: string[] = [];
  for (const segment of rest.split("\\")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      if (parts.length && parts[parts.length - 1] !== "..") parts.pop();
      else if (!rooted) parts.push("..");
      continue;
    }
    parts.push(segment);
  }
  return (prefix + parts.join("\\")).toLowerCase();
}

function dirOf(path: string) {
  const norm = path.replace(/\//g, "\\").replace(/\\+$/, "");
  const at = norm.lastIndexOf("\\");
  return at > 0 ? norm.slice(0, at) : "";
}

/* 원본 _token — {preset, order} → "preset|order".
   ⚠ 예전 버전은 값을 **문자열 그대로**("5.1") 저장했다. 원본 _token 은 그 형태도
     그대로 돌려준다 (binaural.py:130). PoC 는 객체만 읽어서, 옛 버전에서 손으로
     골라 둔 배치가 빈 값으로 읽혔다 (조사 B05). */
function token(item?: LayoutItem | string | null) {
  if (!item) return "";
  if (typeof item === "string") return item;
  const preset = String(item.preset ?? "");
  const order = String(item.order ?? "");
  return preset && order ? `${preset}|${order}` : preset;
}

/* 원본 split_layout_override — order 는 wave/film 만 인정 */
export function splitLayoutOverride(value: string): [string, string] {
  const at = String(value ?? "").indexOf("|");
  if (at < 0) return [String(value ?? "").toLowerCase(), ""];
  const preset = value.slice(0, at).toLowerCase();
  const order = value.slice(at + 1);
  return [preset, order === "wave" || order === "film" || order === "smpte" ? order : ""];
}

export function getOverride(data: LayoutData, path: string, channels: number) {
  if (!path) return "";
  const file = data.files[layoutKey(path)];
  if (file) return token(file);
  const folder = data.folders[layoutKey(dirOf(path))];
  return folder ? token(folder[String(Math.trunc(channels || 0))]) : "";
}

/* 폴더 지정 한 칸을 가리키는 키 — 폴더와 채널 수가 함께여야 한 칸이다.
   "이번 실행에서 이 폴더는 이미 물었다" 표시도 **같은 키**로 해야 파일을 넘길 때마다
   다시 묻지 않는다. 경로 정규화 규칙(layoutKey)이 여기 있으므로 여기서 만든다. */
export function folderScopeKey(path: string, channels: number) {
  return `${layoutKey(dirOf(path))}|${Math.trunc(channels || 0)}`;
}

/* 화면에 보여 줄 폴더 이름 — 전체 경로는 길어서 마지막 한 칸만 쓴다 */
export function folderDisplayName(path: string) {
  const dir = dirOf(path);
  const at = dir.lastIndexOf("\\");
  return at >= 0 ? dir.slice(at + 1) : dir;
}

export function overrideScope(data: LayoutData, path: string,
                             channels: number): "" | "file" | "folder" {
  if (!path) return "";
  if (data.files[layoutKey(path)]) return "file";
  const folder = data.folders[layoutKey(dirOf(path))];
  if (folder && folder[String(Math.trunc(channels || 0))]) return "folder";
  return "";
}

/* 원본 set — preset 이 빈 문자열이면 그 파일 지정을 지운다.
   source="layout_prompt" 는 자동 판별 실패 안내에서 사용자가 확정한 표식이며
   한 번 붙으면 이후 저장에서도 유지된다 (원본 keep_prompt_choice). */
export function setFileOverride(data: LayoutData, path: string, preset: string,
                                promptChoice = false): LayoutData {
  const key = layoutKey(path);
  const files = { ...data.files };
  if (preset) {
    const [value, order] = splitLayoutOverride(preset);
    const previous = data.files[key];
    const keep = typeof previous === "object" && previous?.source === "layout_prompt";
    const item: LayoutItem = { preset: value };
    if (order) item.order = order;
    if (promptChoice || keep) item.source = "layout_prompt";
    files[key] = item;
  } else {
    delete files[key];
  }
  return { ...data, files };
}

/* 원본 set_folder — 폴더 + 채널 수 조합으로 저장. preset 이 비면 그 조합만 지우고
   폴더가 비면 폴더 항목 자체를 없앤다. */
export function setFolderOverride(data: LayoutData, path: string, channels: number,
                                  preset: string): LayoutData {
  const folderKey = layoutKey(dirOf(path));
  const channelKey = String(Math.trunc(channels || 0));
  const folders = { ...data.folders };
  const folder = { ...(folders[folderKey] ?? {}) };
  if (preset) {
    const [value, order] = splitLayoutOverride(preset);
    const item: LayoutItem = { preset: value };
    if (order) item.order = order;
    folder[channelKey] = item;
    folders[folderKey] = folder;
  } else {
    delete folder[channelKey];
    if (Object.keys(folder).length) folders[folderKey] = folder;
    else delete folders[folderKey];
  }
  return { ...data, folders };
}

export function isPromptChoice(data: LayoutData, path: string) {
  const item = data.files[layoutKey(path)];
  if (typeof item !== "object" || !item) return false;   /* 옛 문자열 형식엔 표식이 없다 */
  return Boolean(item.source === "layout_prompt" && item.preset);
}

export async function loadLayoutStore(): Promise<LayoutData> {
  try {
    const raw = await invoke<string>("sf_layout_store_read");
    if (!raw) return { ...EMPTY_LAYOUT_DATA };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    /* 원본: version 2 가 아니면 예전 형식(파일 지정만 담긴 평평한 dict)으로 읽는다 */
    if (parsed?.version === 2) {
      return {
        version: 2,
        files: (parsed.files as LayoutData["files"]) ?? {},
        folders: (parsed.folders as LayoutData["folders"]) ?? {},
      };
    }
    return { version: 2, files: (parsed as LayoutData["files"]) ?? {}, folders: {} };
  } catch {
    return { ...EMPTY_LAYOUT_DATA };
  }
}

export async function saveLayoutStore(data: LayoutData): Promise<boolean> {
  try {
    await invoke("sf_layout_store_write", { json: JSON.stringify(data) });
    return true;
  } catch {
    return false;
  }
}
