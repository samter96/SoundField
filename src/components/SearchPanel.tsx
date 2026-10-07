import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { IcoPlus, IcoRefresh, IcoTarget, IcoX } from "../icons";
import { LibraryPanel } from "./LibraryPanel";
import { Dropdown } from "./Dropdown";
import type { Lib } from "../data";
import type { SearchRequest, SynonymInfo } from "../backend";
import { t } from "../i18n";

type Logic = "AND" | "OR" | "NOT";
type Filter = { id: number; logic: Logic | null; field: string; text: string };

const MAX_ROWS = 6;
const FIELDS = [
  "전체", "파일명", "경로", "제목", "아티스트", "앨범", "장르",
  "코멘트", "설명", "키워드", "카테고리", "서브카테고리", "출처",
];

/* 원본 콤보 항목 순서 — 저장은 인덱스이므로 순서를 바꾸면 복원이 깨진다
   (main_window.py:4580 / 4586). */
const SR_OPTIONS = ["전체", "44.1K", "48K", "88.2K", "96K", "192K"];
const CH_OPTIONS = ["전체", "MONO", "STEREO", "4CH", "5.1 / 6CH", "7채널 이상"];

const FIELD_KEYS: Record<string, string> = {
  "전체": "any",
  "파일명": "file_name",
  "경로": "file_path",
  "제목": "title",
  "아티스트": "artist",
  "앨범": "album",
  "장르": "genre",
  "코멘트": "comments",
  "설명": "description",
  "키워드": "keywords",
  "카테고리": "category",
  "서브카테고리": "sub_category",
  "출처": "source",
};

/* DB 필드 키 → 화면 라벨 (복원용 역방향 표) */
const FIELD_LABEL_BY_KEY: Record<string, string> = Object.fromEntries(
  Object.entries(FIELD_KEYS).map(([label, key]) => [key, label]));

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/* 단어 경계로 포함 여부 — 'glass breaking' 안의 'break' 는 아니고 'glass break' 는 맞다 */
const hasWord = (text: string, word: string) =>
  new RegExp(`(^|[^a-z0-9])${escapeRe(word)}([^a-z0-9]|$)`).test(text);

/* 원본 multi_search.py:_SEARCH_HELP_TEXT — 2026-10-07 쉼표·빼기·동의어 항목 추가 */
export const SEARCH_HELP = `필터 단축키
  • Tab/Enter : 필터 추가
  • 빈 검색창 Backspace : 위 필터로 이동 + 제거
  • 최소 1개 필터는 유지

검색 문법

기본
  • 공백 = AND (모두 매칭)
  • 예: dark magic → 두 단어 모두 어딘가 있는 결과

연산자 (대문자만 인식)
  • AND : 둘 다 매칭          예) dark AND magic
  • OR  : 둘 중 하나          예) sword OR knife
  • 쉼표도 OR                 예) sword, knife
  • NOT : 제외                예) footstep NOT rain
  • 앞에 - 를 붙여도 제외     예) door -slam

그룹화
  • 괄호로 우선순위 지정
  • 예) (sword OR knife) AND fight

따옴표 phrase
  • "dark magic" → 그 순서로 붙은 결과만
  • 따옴표 안의 말에는 동의어를 붙이지 않음

동의어
  • 같은 뜻의 말도 함께 찾음   예) break → shatter, smash
  • 붙은 말은 검색 줄 아래에서 빼거나 더할 수 있음
  • 환경설정에서 끌 수 있음

비고
  • 1-2글자 짧은 토큰은 매칭 잘 안 됨 (3글자 이상 권장)
  • 위 문법은 “경로” 필드를 뺀 모든 필드에서 적용`;

const PRECISE_HELP = [
  "정확한 검색",
  "",
  "단어를 끝까지 입력해야 매칭됩니다. 일부만 치면 안 나옵니다.",
  "단어 단위라 결과가 더 정확합니다.",
  "",
  "예) 'door' → door · doors 함께 나옴",
  "    'doo' (일부만) → 결과 없음",
  "",
  "끄면(넓게 찾기): 글자 조각으로 찾아 'doo'만 쳐도 나옵니다.",
  "",
  "동의어도 단어 단위로 함께 찾습니다 (따옴표로 감싸면 그 말만).",
].join("\n");

/* 필터 행 번호를 **지금 있는 행들에서** 뽑는다.

   ⚠ 모듈 전역 카운터(`let nextId = 2`)로 매기지 말 것 — 번호가 겹친다.
   저장된 필터를 복원할 때는 `index + 1` 로 따로 매기는데(1, 2, ...) 전역 카운터는
   그걸 모른다. 실측 2026-09-07 (사용자 신고 "필터 2개 걸고 3번째를 추가해서
   입력하면 2번째 필터가 그 글자를 따라 쓴다"):
     필터 2개가 저장된 상태로 앱을 켜면 행 번호 1, 2 / 카운터는 그대로 2
     → 3번째 추가 시 번호가 **2로 겹침**
     → 값 변경 함수(update)가 번호로 행을 찾으므로 **두 행이 같이 바뀜**
   포커스 대상(inputs Map)과 React key 도 번호로 잡으므로 같이 어긋난다.
   필터를 저장해 둔 사용자만 겪어서 "가끔 이상하다" 로 보였다.

   원본은 필터 행이 각각 위젯이라 이런 번호가 아예 없었다 — PoC 에서 새로 만든
   구조이므로, 번호를 다른 곳에서 또 매기지 말고 항상 이 함수를 쓸 것. */
const nextFilterId = (rows: Array<{ id: number }>) =>
  rows.reduce((max, row) => Math.max(max, row.id), 0) + 1;

function durationNumber(value: string) {
  const text = value.trim().replace(/[sS]$/, "").trim();
  if (!text) return 0;
  const n = Number.parseFloat(text.replace(",", "."));
  return Number.isFinite(n) ? Math.max(0, Math.min(36000, n)) : 0;
}

function durationEditText(value: string) {
  const n = durationNumber(value);
  if (n <= 0) return "";
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

function durationCommitText(value: string) {
  const n = Math.round(durationNumber(value) * 10) / 10;
  return n > 0 ? `${n.toFixed(1)}s` : "";
}

type Props = {
  onStatus: () => void;
  addedLibs: string[];
  libraries: Lib[];
  onContext: (e: React.MouseEvent, label: string) => void;
  onHistory: () => void;
  historyOpen: boolean;
  /* 원본: 한 번이라도 토글했는지 — 닫힘 문구가 "◀ 히스토리" 로 바뀐다 */
  historyToggled?: boolean;
  searchLimit: number;
  pathPrefixes: string[];
  onSearchChange: (req: SearchRequest) => void;
  /** 환경설정 '동의어도 함께 찾기' */
  synonymsEnabled: boolean;
  /** 마지막 검색에 붙은 동의어 — 검색 줄 아래 칩으로 보인다 */
  synonymInfo: SynonymInfo[];
  /** 철자 제안 클릭 — 검색창의 from 단어를 to 로 바꾼다 (n 이 바뀔 때마다 한 번) */
  replaceSignal: { from: string; to: string; n: number } | null;
  /* 원본 config "filters" 복원 (_restore_filters, main_window.py:4942).
     콤보는 **인덱스**로 저장되므로 목록 순서로 되돌린다. 복원 뒤 검색을 한 번 돌린다. */
  initialFilters?: {
    matchers: Array<{ field?: string | null; operator?: string | null; value?: string }>;
    minDur: number;
    maxDur: number;
    sampleRateIndex: number;
    channelsIndex: number;
  } | null;
  /* 필터가 바뀔 때마다 상위에 알려 config 에 저장한다 (원본은 _save_config 디바운스) */
  onFiltersChange?: (next: {
    matchers: Array<{ field?: string | null; operator?: string | null; value?: string }>;
    minDur: number;
    maxDur: number;
    sampleRateIndex: number;
    channelsIndex: number;
  }) => void;
};

export function SearchPanel({ onStatus, addedLibs, libraries, onContext, onHistory, historyOpen,
                             historyToggled,
                              searchLimit, pathPrefixes, onSearchChange,
                             synonymsEnabled, synonymInfo, replaceSignal,
                             initialFilters, onFiltersChange }: Props) {
  const [filters, setFilters] = useState<Filter[]>([
    { id: 1, logic: null, field: "전체", text: "" },
  ]);
  const [exact, setExact] = useState(false);
  /* 동의어 칩에서 뺀/더한 말 — **이번 검색에만** (사용자 결정 2026-10-07).
     검색창에서 그 단어가 사라지면 같이 지운다 (아래 effect). 사전은 바꾸지 않는다. */
  const [synOverrides, setSynOverrides] = useState<Record<string, { off: string[]; add: string[] }>>({});
  const [synAdding, setSynAdding] = useState<string | null>(null);
  const [synAddText, setSynAddText] = useState("");
  const [minDur, setMinDur] = useState("");
  const [maxDur, setMaxDur] = useState("");
  const [sampleRate, setSampleRate] = useState("전체");
  const [channels, setChannels] = useState("전체");
  const [focusRequest, setFocusRequest] = useState<{ id: number; end?: boolean } | null>(null);
  const inputs = useRef(new Map<number, HTMLInputElement>());
  /* ── 저장된 필터 복원 (원본 _restore_filters) ──
     원본은 위젯 신호를 막고 값만 넣은 뒤 마지막에 한 번만 검색을 돌린다.
     여기서도 한 번만 적용하고(restoredRef), 그 다음부터 사용자 입력으로 취급한다. */
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current || !initialFilters) return;
    restoredRef.current = true;
    const rows: Filter[] = initialFilters.matchers.length
      ? initialFilters.matchers.map((m, index) => ({
          id: index + 1,
          /* 첫 행은 항상 루트 (operator 없음) — 원본 set_state 와 같은 규칙 */
          logic: index === 0 ? null : ((m.operator || "AND") as Logic),
          field: FIELD_LABEL_BY_KEY[String(m.field ?? "any")] ?? "전체",
          text: String(m.value ?? ""),
        })).slice(0, MAX_ROWS)
      : [{ id: 1, logic: null, field: "전체", text: "" }];
    setFilters(rows);
    setMinDur(initialFilters.minDur > 0 ? String(initialFilters.minDur) : "");
    setMaxDur(initialFilters.maxDur > 0 ? String(initialFilters.maxDur) : "");
    setSampleRate(SR_OPTIONS[initialFilters.sampleRateIndex] ?? "전체");
    setChannels(CH_OPTIONS[initialFilters.channelsIndex] ?? "전체");
  }, [initialFilters]);

  /* 값이 바뀌면 상위에 알려 저장한다 (복원 직후 1회는 같은 값이라 무해) */
  useEffect(() => {
    onFiltersChange?.({
      matchers: filters.map((row, index) => ({
        field: FIELD_KEYS[row.field] ?? "any",
        operator: index === 0 ? null : row.logic,
        value: row.text,
      })),
      minDur: durationNumber(minDur),
      maxDur: durationNumber(maxDur),
      sampleRateIndex: Math.max(0, SR_OPTIONS.indexOf(sampleRate)),
      channelsIndex: Math.max(0, CH_OPTIONS.indexOf(channels)),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, minDur, maxDur, sampleRate, channels]);

  useLayoutEffect(() => {
    if (!focusRequest) return;
    const input = inputs.current.get(focusRequest.id);
    if (input) {
      input.focus();
      if (focusRequest.end) input.setSelectionRange(input.value.length, input.value.length);
    }
    setFocusRequest(null);
  }, [filters, focusRequest]);

  const add = (logic: Logic) => {
    if (filters.length >= MAX_ROWS) {
      setFocusRequest({ id: filters[filters.length - 1].id });
      return;
    }
    /* 번호는 **현재 행 목록**을 보고 정한다 (nextFilterId 주석의 함정 참고).
       아래 포커스 요청에도 같은 번호를 써야 하므로 여기서 한 번만 뽑는다. */
    const id = nextFilterId(filters);
    setFilters((rows) => [...rows, { id, logic, field: "전체", text: "" }]);
    setFocusRequest({ id });
  };

  const remove = (id: number) => {
    if (id === filters[0].id) {
      setFilters((rows) => rows.map((row, index) => index === 0 ? { ...row, text: "" } : row));
      return;
    }
    setFilters((rows) => rows.filter((row) => row.id !== id));
  };

  const update = (id: number, patch: Partial<Filter>) =>
    setFilters((rows) => rows.map((row) => row.id === id ? { ...row, ...patch } : row));

  const handleInputKey = (event: React.KeyboardEvent<HTMLInputElement>, index: number) => {
    const isForward = (event.key === "Tab" && !event.shiftKey) || event.key === "Enter";
    if (isForward) {
      event.preventDefault();
      const next = filters[index + 1];
      if (next) setFocusRequest({ id: next.id });
      else add("AND");
      return;
    }
    if (event.key === "Backspace" && event.currentTarget.value === "" && index > 0) {
      event.preventDefault();
      const previous = filters[index - 1];
      setFilters((rows) => rows.filter((_, rowIndex) => rowIndex !== index));
      setFocusRequest({ id: previous.id, end: true });
    }
  };

  /* 검색어 칸을 클릭하면 **첫 클릭엔 전체 선택**, 같은 칸을 한 번 더 클릭하면
     선택을 풀고 커서를 놓는다.

     ⚠ "이미 전체 선택돼 있으면 건너뛴다" 로만 판정하면 **간헐적으로 안 먹는다.**
       onMouseDown 시점의 selectionStart/End 는 그 칸이 **예전에** 갖고 있던 값이다.
       칸 A 에서 전체 선택한 뒤 B 로 갔다가 A 로 돌아오면, A 의 선택 범위가 그대로
       남아 있어 "두 번째 클릭" 으로 오인하고 선택을 건너뛴다
       (사용자 신고 2026-09-07: 필터를 번갈아 클릭하면 간헐적으로 안 먹는다).
     그래서 **마지막으로 누른 칸이 무엇이었는지**를 함께 본다. 다른 칸이면 언제나
     첫 클릭이다. 같은 칸일 때만 기존 선택 상태를 보고 건너뛴다. */
  const lastClickedInput = useRef<number | null>(null);

  const selectAllOnFirstClick = (event: React.MouseEvent<HTMLInputElement>, id: number) => {
    const input = event.currentTarget;
    const sameAsBefore = lastClickedInput.current === id;
    lastClickedInput.current = id;
    if (!input.value) return;
    if (sameAsBefore
        && input.selectionStart === 0
        && input.selectionEnd === input.value.length) return;
    event.preventDefault();
    input.focus();
    input.select();
  };

  const clearAll = () => {
    /* 행이 하나만 남으므로 번호는 1 로 되돌린다 — 처음 상태와 같게 둔다. */
    setFilters([{ id: 1, logic: null, field: "전체", text: "" }]);
  };

  const resetActive = durationNumber(minDur) > 0 || durationNumber(maxDur) > 0 || sampleRate !== "전체" || channels !== "전체";
  const resetMetaFilters = () => {
    setMinDur("");
    setMaxDur("");
    setSampleRate("전체");
    setChannels("전체");
  };

  useEffect(() => {
    const sr = sampleRate === "44.1K" ? 44100
      : sampleRate === "48K" ? 48000
      : sampleRate === "88.2K" ? 88200
      : sampleRate === "96K" ? 96000
      : sampleRate === "192K" ? 192000
      : null;
    const ch = channels === "MONO" ? 1
      : channels === "STEREO" ? 2
      : channels === "4CH" ? 4
      : channels === "5.1 / 6CH" ? 6
      : null;
    onSearchChange({
      matchers: filters.map((filter, index) => ({
        field: index === 0 ? (FIELD_KEYS[filter.field] ?? "any") : (FIELD_KEYS[filters[0].field] ?? "any"),
        operator: index === 0 ? null : filter.logic,
        value: filter.text,
      })),
      min_duration: durationNumber(minDur),
      max_duration: durationNumber(maxDur) > 0 ? durationNumber(maxDur) : null,
      sample_rate: sr,
      channels: ch,
      min_channels: channels === "7채널 이상" ? 7 : null,
      path_prefixes: pathPrefixes.length ? pathPrefixes : null,
      precise: exact,
      limit: searchLimit,
      synonyms: synonymsEnabled,
      synonym_overrides: Object.keys(synOverrides).length ? synOverrides : null,
    });
  }, [filters, exact, minDur, maxDur, sampleRate, channels, pathPrefixes, searchLimit, onSearchChange,
      synonymsEnabled, synOverrides]);

  /* 검색창에서 사라진 단어의 칩 편집은 버린다 — 같은 단어를 다시 치면 사전대로 시작한다 */
  useEffect(() => {
    const text = filters.map((f) => f.text.toLowerCase()).join("\n");
    setSynOverrides((prev) => {
      const keep = Object.entries(prev).filter(([term]) => hasWord(text, term));
      return keep.length === Object.keys(prev).length ? prev : Object.fromEntries(keep);
    });
  }, [filters]);

  /* 철자 제안 클릭 — 그 단어만 바꾼다 (단어 경계 기준, 대소문자 무시) */
  const lastReplace = useRef(0);
  useEffect(() => {
    if (!replaceSignal || replaceSignal.n === lastReplace.current) return;
    lastReplace.current = replaceSignal.n;
    const rx = new RegExp(`(^|[^A-Za-z0-9])${escapeRe(replaceSignal.from)}(?=[^A-Za-z0-9]|$)`, "gi");
    setFilters((rows) => rows.map((f) => ({ ...f, text: f.text.replace(rx, (_m, pre) => pre + replaceSignal.to) })));
  }, [replaceSignal]);

  const setSynWord = (term: string, word: string, on: boolean) => setSynOverrides((prev) => {
    const cur = prev[term] ?? { off: [], add: [] };
    let off = cur.off.filter((w) => w !== word);
    let add = cur.add;
    if (!on) {
      if (add.includes(word)) add = add.filter((w) => w !== word);   // 내가 더한 말 → 그냥 지움
      else off = [...off, word];                                     // 사전의 말 → 이번 검색에서 뺌
    }
    const next = { ...prev, [term]: { off, add } };
    if (!off.length && !add.length) delete next[term];
    return next;
  });
  const addSynWord = (term: string, raw: string) => {
    const word = raw.trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ");
    setSynAdding(null);
    if (!word || word === term) return;
    setSynOverrides((prev) => {
      const cur = prev[term] ?? { off: [], add: [] };
      return { ...prev, [term]: { off: cur.off.filter((w) => w !== word),
                                  add: cur.add.includes(word) ? cur.add : [...cur.add, word] } };
    });
  };

  return (
    <div className="search">
      <div className="search-top">
        <div className="search-main">
          {/* 힌트글(Tab/Enter…)은 **앱 헤더 줄**로 올렸다 (사용자 지시 2026-09-08).
              여기 두면 힌트 한 줄(29px) + 여백(4px) 때문에 첫 필터 행이 밀려
              사이드바의 [파일 브라우저] 헤더와 줄이 어긋났다. 이제 첫 필터 행이
              이 칸의 맨 위라 두 줄이 나란히 맞는다 (Header.tsx header-hint). */}
          {filters.map((filter, index) => (
            <div className={"filter-row" + (filter.logic ? " child" : "")} key={filter.id}>
              {filter.logic ? (
                <Dropdown className="op-select" value={filter.logic}
                          options={["AND", "OR", "NOT"]}
                          onChange={(next) => update(filter.id, { logic: next as Logic })} />
              ) : (
                /* ⚠ options/value 는 한글 그대로 둔다 — FIELD_KEYS 의 열쇠이자
                   설정에 저장되는 식별자다. 보이는 글자만 label 로 바꾼다. */
                <Dropdown className="field-select" value={filter.field}
                          options={FIELDS} label={t}
                          onChange={(next) => update(filter.id, { field: next })} />
              )}
              <input ref={(node) => { if (node) inputs.current.set(filter.id, node); else inputs.current.delete(filter.id); }}
                     className="input" placeholder={t("검색어 입력...")} value={filter.text}
                     onMouseDown={(event) => selectAllOnFirstClick(event, filter.id)}
                     onKeyDown={(event) => handleInputKey(event, index)}
                     onChange={(event) => update(filter.id, { text: event.target.value })} />
              <button className="abtn icon-only row-remove" data-tip={t("이 조건 제거")} aria-label={t("이 조건 제거")}
                      onClick={() => remove(filter.id)}>
                <IcoX size={13} />
              </button>
            </div>
          ))}

          <div className="logic-row">
            {(["AND", "OR", "NOT"] as const).map((logic) => (
              <button key={logic} className="abtn add-filter" disabled={filters.length >= MAX_ROWS}
                      onClick={() => add(logic)}>
                <IcoPlus size={13} /> {logic}
              </button>
            ))}
            <span className="logic-gap" />
            <button className={"target-toggle" + (exact ? " on" : "")}
                    data-tip={t(PRECISE_HELP)} aria-label={t("정확한 검색")}
                    onClick={() => setExact((value) => !value)}>
              <IcoTarget size={20} />
            </button>
            <div className="header-spacer" />
            <button className="abtn danger clear-search" data-tip={t("검색어 전체 삭제 (모든 입력 행 비우기)")}
                    onClick={clearAll}>
              <IcoX size={13} /> {t("검색어 비우기")}
            </button>
          </div>

          {/* 동의어 칩 — 이번 검색에 붙은 '같은 뜻' 말. × 로 빼고, 흐린 칩을 누르면
              다시 넣고, + 로 더한다 (BaseHead 의 T-Blocks 와 같은 역할, 사용자 결정 2026-10-07). */}
          {synonymsEnabled && synonymInfo.length > 0 && (
            <div className="syn-row">
              <span className="syn-label">함께 찾는 말</span>
              {synonymInfo.map((info) => {
                const removed = info.available.filter((w) => !info.used.includes(w));
                return (
                  <span className="syn-group" key={info.term}>
                    <span className="syn-term">{info.term}</span>
                    {info.used.map((word) => (
                      <span className="syn-chip" key={word}>
                        {word}
                        <button className="syn-x" data-tip={t("이 말 빼기")} aria-label={t("이 말 빼기")}
                                onClick={() => setSynWord(info.term, word, false)}>
                          <IcoX size={9} />
                        </button>
                      </span>
                    ))}
                    {removed.map((word) => (
                      <button className="syn-chip off" key={word} data-tip={t("다시 넣기")}
                              onClick={() => setSynWord(info.term, word, true)}>
                        {word}
                      </button>
                    ))}
                    {synAdding === info.term ? (
                      <input className="syn-input" autoFocus value={synAddText}
                             placeholder={t("말 입력 후 Enter")}
                             onChange={(event) => setSynAddText(event.target.value)}
                             onKeyDown={(event) => {
                               if (event.key === "Enter") addSynWord(info.term, synAddText);
                               else if (event.key === "Escape") setSynAdding(null);
                             }}
                             onBlur={() => setSynAdding(null)} />
                    ) : (
                      <button className="syn-add" data-tip={t("이 검색에 동의어 더하기")}
                              aria-label={t("이 검색에 동의어 더하기")}
                              onClick={() => { setSynAdding(info.term); setSynAddText(""); }}>
                        <IcoPlus size={10} />
                      </button>
                    )}
                  </span>
                );
              })}
            </div>
          )}
        </div>

        <LibraryPanel onStatus={onStatus} onContext={onContext} addedLibs={addedLibs} libraries={libraries} />
      </div>


      <div className="meta-row">
        <span className="caps">Length</span>
        <input className={"input input-sm" + (durationNumber(minDur) > 0 ? " active-filter" : "")}
               inputMode="decimal" placeholder={t("최소")} value={minDur}
               onFocus={() => setMinDur((value) => durationEditText(value))}
               onBlur={() => setMinDur((value) => durationCommitText(value))}
               onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
               onWheel={(event) => event.preventDefault()} onChange={(event) => setMinDur(event.target.value)} />
        <span className="meta-sep">~</span>
        <input className={"input input-sm" + (durationNumber(maxDur) > 0 ? " active-filter" : "")}
               inputMode="decimal" placeholder={t("최대")} value={maxDur}
               onFocus={() => setMaxDur((value) => durationEditText(value))}
               onBlur={() => setMaxDur((value) => durationCommitText(value))}
               onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
               onWheel={(event) => event.preventDefault()} onChange={(event) => setMaxDur(event.target.value)} />
        <span className="caps meta-label-gap">Sample Rate</span>
        <Dropdown className={"meta-select" + (sampleRate !== "전체" ? " active-filter" : "")}
                  value={sampleRate} ariaLabel={t("샘플레이트 필터")} label={t}
                  options={["전체", "44.1K", "48K", "88.2K", "96K", "192K"]}
                  onChange={setSampleRate} />
        <span className="caps meta-label-gap">Channels</span>
        <Dropdown className={"meta-select channels-select" + (channels !== "전체" ? " active-filter" : "")}
                  value={channels} ariaLabel={t("채널 필터")} label={t}
                  options={["전체", "MONO", "STEREO", "4CH", "5.1 / 6CH", "7채널 이상"]}
                  onChange={setChannels} />
        <button className={"filter-reset" + (resetActive ? " active" : "")} onClick={resetMetaFilters}
                data-tip={t("필터 초기화\n길이/샘플레이트/채널 필터를 모두 기본값(전체)으로 복원합니다.")}
                aria-label={t("필터 초기화")}>
          <IcoRefresh size={14} />
        </button>
        <div className="header-spacer" />
        <button className={"abtn history-toggle" + (historyOpen ? " active" : "")}
                data-tip={t("재생 히스토리 열기/닫기\n단축키: H")} onClick={onHistory}>
          {/* 원본 문구를 그대로 따른다 (main_window.py:4628 / 5527 / 5533):
                 처음(한 번도 토글 안 함) "히스토리 ◀"
                 열림                     "▶ 히스토리"
                 닫힘(토글로 닫은 뒤)     "◀ 히스토리"   ← 화살표가 앞으로 온다 */}
          {historyOpen ? t("▶ 히스토리") : historyToggled ? t("◀ 히스토리") : t("히스토리 ◀")}
        </button>
      </div>
    </div>
  );
}
