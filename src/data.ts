/* 디자인 검토용 더미 데이터. 백엔드 연결 없음. */

export type Row = {
  id: number;
  dbId?: string;
  dur: number;
  fmt: "WAV" | "MP3" | "FLAC" | "AIFF";
  name: string;
  sr: number;
  ch: number;
  bitDepth: number;
  codec: string;
  size: number;
  path: string;
  fullPath?: string;
  cat: string;
  title: string;
  artist: string;
  album: string;
  genre: string;
  comments: string;
  bitrate: number;
};

const WORDS_A = ["Digital Futuristic Display", "User Interface Metal", "Zoom Screen Switch",
  "Impact Rock Heavy", "Whoosh Air Fast", "Footstep Concrete", "Ambience Forest Night",
  "Door Metal Latch", "Cloth Movement", "Water Splash Small", "Gun Handling Rifle",
  "Magic Spell Cast", "UI Menu Confirm", "Debris Rubble", "Fire Crackle Loop"];
const WORDS_B = ["Transition Scrape", "Alien Hi Tech Synth", "Slow Metallic Switch",
  "Layer Sweeten", "Designed Hit", "Foley Take", "Room Tone", "Sub Boom", "Tail Reverb",
  "Close Perspective", "Distant Perspective"];
const CATS = ["DESIGN", "FOLEY", "AMBIENCE", "WEAPON", "UI", "IMPACT", "VOX", "MUSIC"];
const ROOTS = [
  "D:\\Soundlibrary\\UI\\Sound_Ex_Machina_-_UI_Sounds_-_Futuristic",
  "Y:\\[Library]\\[Studio] Game Project\\Futuristic User Interfaces",
  "Y:\\[Library]\\[Studio] Game Project\\Horror_User_Interface",
  "Y:\\[Library]\\Boom Library\\Cinematic Trailers",
  "Y:\\[Library]\\10.Japan_Library\\Nature",
];
const SRS = [44100, 48000, 96000, 192000];

function rnd(seed: number) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

export function makeRows(n: number): Row[] {
  const r = rnd(20260831);
  const out: Row[] = [];
  for (let i = 0; i < n; i++) {
    const a = WORDS_A[(r() * WORDS_A.length) | 0];
    const b = WORDS_B[(r() * WORDS_B.length) | 0];
    const idx = String(((r() * 60) | 0) + 1).padStart(2, "0");
    const tag = String(((r() * 9000) | 0) + 1000);
    const fmt = (r() < 0.62 ? "WAV" : r() < 0.9 ? "MP3" : r() < 0.97 ? "FLAC" : "AIFF") as Row["fmt"];
    const dur = Math.round((0.4 + r() * 14) * 100) / 100;
    const ch = r() < 0.55 ? 2 : r() < 0.82 ? 1 : r() < 0.94 ? 6 : 9;
    const sr = SRS[(r() * SRS.length) | 0];
    out.push({
      id: i,
      dur,
      fmt,
      name: `${a} - ${b} - ${idx}  [${tag}].${fmt.toLowerCase()}`,
      sr,
      ch,
      bitDepth: fmt === "MP3" ? 0 : (r() < 0.55 ? 16 : 24),
      codec: fmt === "MP3" ? "MP3" : fmt === "FLAC" ? "FLAC" : "PCM",
      size: Math.round(dur * sr * ch * 3),
      path: ROOTS[(r() * ROOTS.length) | 0],
      cat: CATS[(r() * CATS.length) | 0],
      title: `${a} - ${b}`,
      artist: "",
      album: "",
      genre: CATS[(r() * CATS.length) | 0],
      comments: "",
      bitrate: fmt === "MP3" ? (r() < 0.5 ? 192 : 320) : 0,
    });
  }
  return out;
}

export type TreeNode = {
  id: string;
  label: string;
  path?: string;
  count?: number;
  incomplete?: number;
  children?: TreeNode[];
};

export const TREE: TreeNode = {
  id: "all",
  label: "전체",
  count: 1141600,
  children: [
    {
      id: "d", label: "Soundlibrary", count: 446954,
      children: [
        { id: "d1", label: "UI", count: 12480 },
        { id: "d2", label: "Foley", count: 38210 },
        { id: "d3", label: "Ambience", count: 21044 },
        { id: "d4", label: "Weapons", count: 64118 },
      ],
    },
    {
      id: "c", label: "C:\\Users\\samter96", count: 52,
      children: [
        { id: "c1", label: "엔진 리소스", count: 47 },
        { id: "c2", label: "fwe", count: 5 },
      ],
    },
    {
      id: "y", label: "[Library]", count: 694594,
      children: [
        { id: "y1", label: "[Studio] Game Project", count: 88231 },
        { id: "y2", label: "Boom Library", count: 141902 },
        { id: "y3", label: "10.Japan_Library", count: 42117 },
        { id: "y4", label: "99XFS CINEMATIC SOUND", count: 30288 },
      ],
    },
  ],
};

export type Lib = {
  id: string;
  name: string;
  path: string;
  count: number;
  /* 원본 _status_color + _GlowDot(hollow) 조합 (main_window.py:1450, 1602)
       missing : 경로 자체 없음 → 빨강 **속 빈 원** (hollow = not exists)
       fail    : 실패 파일 존재 → 빨강 **채운 원**
       busy    : 대기 파일 존재 → 주황 채운 원
       ok      : 완전 정상    → 초록 채운 원
     "off" 는 더미 데이터 호환용 별칭(= missing). */
  state: "ok" | "busy" | "fail" | "missing" | "off";
  lastIndexedAt?: number | null;
  sortOrder?: number | null;
  pending?: number;
  failed?: number;
  exists?: boolean;
};

export const LIBS: Lib[] = [
  { id: "l1", name: "엔진 리소스", path: "C:\\Users\\samter96\\엔진 리소스", count: 47, state: "ok" },
  { id: "l2", name: "fwe", path: "C:\\Users\\samter96\\fwe", count: 5, state: "ok" },
  { id: "l3", name: "Soundlibrary", path: "D:\\Soundlibrary", count: 446954, state: "busy" },
  { id: "l4", name: "[Library]", path: "Y:\\[Library]", count: 694594, state: "off" },
];

export const HISTORY = [
  "Piece_of_Metal_Drop_43.wav", "Piece_of_Metal_Drop_46.wav", "Piece_of_Metal_Drop_42.wav",
  "Piece_of_Metal_Drop_50.wav", "Piece_of_Metal_Drop_59.wav", "Piece_of_Metal_Drop_62.wav",
  "Piece_of_Metal_Drop_67.wav", "Piece_of_Metal_Drop_63.wav", "Piece_of_Metal_Drop_74.wav",
  "AFX_INTERFACE_SCREEN_4_DFM.WAV", "Door Metal Screen Latch Click 01.wav",
];

export const DUPES = [
  { name: "Impact_Metal_Heavy_03.wav", size: 4128422, n: 3, paths: ["D:\\Soundlibrary\\Impacts", "Y:\\[Library]\\Boom Library\\Impacts", "Y:\\[Library]\\Backup2023\\Impacts"] },
  { name: "Whoosh_Air_Fast_12.wav", size: 812004, n: 2, paths: ["D:\\Soundlibrary\\Whoosh", "Y:\\[Library]\\Boom Library\\Whoosh"] },
  { name: "AMB_Forest_Night_Loop.wav", size: 54715756, n: 4, paths: ["D:\\Soundlibrary\\Ambience", "Y:\\[Library]\\10.Japan_Library\\Nature", "Y:\\[Library]\\Old", "Y:\\[Library]\\Old2"] },
  { name: "UI_Confirm_Soft_01.wav", size: 122880, n: 2, paths: ["D:\\Soundlibrary\\UI", "Y:\\[Library]\\UI"] },
];

export const FAILED = [
  { name: "CORRUPT_Take_04.wav", path: "Y:\\[Library]\\Raw\\Session12", reason: "메타데이터 읽기 실패 (헤더 손상)" },
  { name: "LongForm_Ambience.wav", path: "Y:\\[Library]\\Ambience\\Long", reason: "파일 접근 시간 초과" },
  { name: "voice_kr_0231.mp3", path: "D:\\Soundlibrary\\VOX", reason: "지원하지 않는 태그 버전" },
];

/* 원본 블랙리스트 항목은 경로 + 사용자 설명(사유) 두 줄이다 (blacklist_panel._BlacklistRow) */
export const BLACKLIST: Array<{ path: string; description: string }> = [
  { path: "Y:\[Library]\_TRASH", description: "폐기 예정 소스 — 검색에서 제외" },
  { path: "Y:\[Library]\_Backup_2019", description: "2019 백업 사본 (중복)" },
  { path: "D:\Soundlibrary\.tmp", description: "임시 작업 폴더" },
  { path: "Y:\[Library]\Renders\Preview", description: "저품질 프리뷰 렌더" },
  { path: "D:\Soundlibrary\Bounce\old", description: "구버전 바운스" },
];

/* 원본 results_table._fmt_size (1156) 와 동일: B 는 정수, KB 이상은 소수 1자리,
   1024 로 나눠 올라가며 단위를 붙인다 (공백 없음). */
export function fmtSize(b: number) {
  let v = b;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (v < 1024) return unit === "B" ? `${Math.round(v)}${unit}` : `${v.toFixed(1)}${unit}`;
    v /= 1024;
  }
  return `${v.toFixed(1)}TB`;
}

export function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(2).padStart(5, "0")}`;
}
