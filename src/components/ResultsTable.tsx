import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { fmtSize, type Row } from "../data";
import { IcoCheck, IcoSortTri, IcoX } from "../icons";
import { watchRowDrag } from "../drag";
import { makeRowDragImage } from "../dragImage";

type ColumnKey =
  | "duration" | "file_name" | "file_path" | "sample_rate" | "channels" | "bit_depth" | "codec"
  | "file_size" | "title" | "artist" | "album" | "genre" | "comments" | "bitrate";

type Column = {
  key: ColumnKey;
  label: string;
  width: number;
  min: number;
  visible: boolean;
  align?: "left" | "center" | "right";
  locked?: boolean;
};

type SortState = { key: ColumnKey; dir: "asc" | "desc" } | null;

const STORE_KEY = "soundfield.results.columns.v1";
const ROW_H_NORMAL = 30;
const ROW_H_COMPACT = 22;   /* 원본 set_config: 작게 모드 22px */
/* 원본 results_table.py:_DENSE_LIST_TOOLTIP_DELAY_MS — 목록이 촘촘해서 즉시 툴팁이
   뜨면 이동 중 계속 깜박인다. 650ms 머문 뒤에만 띄운다. */
const TOOLTIP_DELAY_MS = 650;
const BUFFER = 8;

/* 원본 ResultsTable.COLUMNS (results_table.py:600) — 라벨/기본 너비 그대로.
   ⚠ 순서는 원본의 **화면 순서**다. 원본은 논리 순서를 file_name 부터 두지만
      _setup 에서 `header.moveSection(duration → 0)` 으로 길이를 맨 앞에 보낸다
      (results_table.py:781 "기본 표시 순서 — 길이를 맨 앞에"). file_name 이
      논리 0 이어야 하는 것은 delegate/UserRole/경로 매핑 때문이고 시각 순서와 무관하다.
   기본 표시(DEFAULT_VISIBLE) = file_name, duration, sample_rate, channels,
      bit_depth, codec, file_path. 나머지는 숨김.
   정렬: file_name / file_path / comments 만 좌측, 나머지는 가운데
      (원본 _item_for_value, results_table.py:949).
   file_name 은 잠금 컬럼 — 숨기면 행 매핑/재생/드래그가 끊기므로 X 가 막혀 있다.
   저장된 레이아웃(로컬 → config columns)이 있으면 그것이 이 순서를 덮어쓴다. */
const BASE_COLUMNS: Column[] = [
  { key: "duration", label: "길이", width: 60, min: 52, visible: true, align: "center" },
  { key: "file_name", label: "파일명", width: 360, min: 120, visible: true, locked: true },
  { key: "file_path", label: "PATH", width: 320, min: 140, visible: true },
  { key: "sample_rate", label: "SR", width: 90, min: 54, visible: true, align: "center" },
  { key: "channels", label: "CH", width: 44, min: 42, visible: true, align: "center" },
  { key: "bit_depth", label: "BIT DEPTH", width: 88, min: 72, visible: true, align: "center" },
  { key: "codec", label: "코덱", width: 70, min: 56, visible: true, align: "center" },
  { key: "file_size", label: "크기", width: 70, min: 60, visible: false, align: "center" },
  { key: "title", label: "제목", width: 150, min: 80, visible: false, align: "center" },
  { key: "artist", label: "아티스트", width: 120, min: 80, visible: false, align: "center" },
  { key: "album", label: "앨범", width: 120, min: 80, visible: false, align: "center" },
  { key: "genre", label: "장르", width: 100, min: 70, visible: false, align: "center" },
  { key: "comments", label: "코멘트", width: 200, min: 100, visible: false },
  { key: "bitrate", label: "비트레이트", width: 80, min: 74, visible: false, align: "center" },
];

/* 원본 results_table._restore_layout — 저장된 순서/표시/너비를 그대로 복원하고,
   저장에 없는 컬럼은 뒤에 기본값으로 붙인다. file_name 은 잠금(항상 표시). */
export function mergeColumns(saved: Array<{ key?: string; visible?: boolean; width?: number }>): Column[] {
  const byKey = new Map(BASE_COLUMNS.map((col) => [col.key, col]));
  const used = new Set<ColumnKey>();
  const merged: Column[] = [];
  for (const item of saved) {
    const key = item.key as ColumnKey | undefined;
    if (!key || !byKey.has(key)) continue;
    const base = byKey.get(key)!;
    used.add(base.key);
    merged.push({
      ...base,
      width: Math.max(base.min, Number(item.width) || base.width),
      visible: base.locked ? true : Boolean(item.visible),
    });
  }
  for (const base of BASE_COLUMNS) {
    if (!used.has(base.key)) merged.push({ ...base });
  }
  return merged;
}

function loadColumns(): Column[] {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return BASE_COLUMNS.map((col) => ({ ...col }));
    return mergeColumns(JSON.parse(raw) as Partial<Column>[]);
  } catch {
    return BASE_COLUMNS.map((col) => ({ ...col }));
  }
}

/* 원본 _item_for_value (results_table.py:929) — 표시 문자열 규칙
     duration  : "{:.2f}s"
     file_size : _fmt_size (B 는 정수, KB 이상은 소수 1자리, 단위 붙여 씀)
     그 외      : str(val) 그대로 — SR/비트레이트에 단위 접미사를 붙이지 않는다.
   (컬럼 헤더가 이미 SR / BIT DEPTH / 비트레이트 라고 알려준다) */
function cellText(row: Row, key: ColumnKey) {
  switch (key) {
    case "duration": return `${row.dur.toFixed(2)}s`;
    case "file_name": return row.name;
    case "file_path": return row.path;
    case "sample_rate": return String(row.sr);
    case "channels": return String(row.ch);
    case "bit_depth": return row.bitDepth ? String(row.bitDepth) : "";
    case "codec": return row.codec;
    case "file_size": return fmtSize(row.size);
    case "title": return row.title;
    case "artist": return row.artist;
    case "album": return row.album;
    case "genre": return row.genre;
    case "comments": return row.comments;
    case "bitrate": return row.bitrate ? String(row.bitrate) : "";
  }
}

function sortValue(row: Row, key: ColumnKey) {
  switch (key) {
    case "duration": return row.dur;
    case "sample_rate": return row.sr;
    case "channels": return row.ch;
    case "bit_depth": return row.bitDepth;
    case "file_size": return row.size;
    case "bitrate": return row.bitrate;
    default: return cellText(row, key).toLowerCase();
  }
}

export function ResultsTable({
  rows,
  playing,
  compact,
  doubleClickToPlay,
  onSelect,
  onActivate,
  onPlay,
  onToggleRow,
  onContext,
  savedColumns,
  onColumnsChange,
  onDragDropped,
  pip,
}: {
  rows: Row[];
  playing: number | null;
  /* 원본 ResultsTable.set_pip — 파일 하나에 붙는 재생 표시 상태.
     loading(빨강 박동) / playing(초록 박동) / ended(파란 점등) / "" (없음).
     ⚠ 행 강조(그라데이션 배경 + 좌측 accent 선 + 파일명 accent)는 **상태와 무관하게**
        pip 이 붙은 행에 적용된다 (원본 _playing_row 는 ended 에도 그 행을 가리킨다).
        따라서 일시정지/정지 뒤에도 그 행 강조는 남고 점만 파랗게 바뀐다. */
  pip?: { path: string; state: "loading" | "playing" | "ended" | "" };
  compact: boolean;
  /* 원본 앱 config 의 columns — localStorage 레이아웃이 없을 때만 쓴다
     (PoC 에서 사용자가 옮긴 배치를 원본 값이 덮어쓰지 않도록) */
  savedColumns?: Array<{ key: string; visible: boolean; width: number }> | null;
  /* 원본 config "columns" 저장 — 순서·표시·너비 (열 이동/크기/표시 변경 때마다) */
  onColumnsChange?: (columns: Array<{ key: string; visible: boolean; width: number }>) => void;
  doubleClickToPlay: boolean;
  /** 선택이 바뀌었을 때. 새 결과로 선택이 사라지면 null 이 온다 (조사 S04) */
  onSelect: (row: Row | null) => void;
  onActivate: (row: Row) => void;
  onPlay: (row: Row) => void;
  onToggleRow?: (row: Row) => void;
  onContext: (event: React.MouseEvent, row: Row, selectedRows: Row[]) => void;
  /** 원본 dragDropped — DAW 등 외부로 드롭이 성사됐을 때 (stop_on_drag 판단은 상위에서) */
  onDragDropped?: () => void;
}) {
  const [columns, setColumns] = useState<Column[]>(loadColumns);
  /* 원본 앱이 저장해 둔 컬럼 배치를 최초 1회 적용 (PoC 로컬 배치가 없을 때만).
     ⚠ "로컬 배치가 있는가" 는 **첫 그림을 그리기 전에** 판정해 둬야 한다
     (조사 C03). 아래 [columns] 효과가 마운트 직후 곧바로 localStorage 에 기본
     배치를 쓰기 때문에, 뒤늦게 도착하는 savedColumns 를 그때 다시 확인하면
     "이미 로컬 배치가 있다" 로 읽혀 원본 배치가 영원히 무시됐다. */
  const hadLocalColumns = useRef<boolean | null>(null);
  if (hadLocalColumns.current === null) {
    try {
      hadLocalColumns.current = localStorage.getItem(STORE_KEY) !== null;
    } catch {
      hadLocalColumns.current = false;
    }
  }
  const appliedSaved = useRef(false);
  useEffect(() => {
    if (appliedSaved.current || !savedColumns?.length) return;
    appliedSaved.current = true;
    if (hadLocalColumns.current) return;
    setColumns(mergeColumns(savedColumns));
  }, [savedColumns]);
  const [sort, setSort] = useState<SortState>(null);
  const [scrollTop, setScrollTop] = useState(0);
  /* 휠 한 번에 scroll 이벤트가 여러 번 오고, 그때마다 setState 하면 한 프레임에
     리렌더가 여러 번 돈다 (5,000행 가상 목록에서 체감된다). 프레임당 한 번으로
     묶는다 — 마지막 값만 반영되므로 표시 결과는 같다. */
  const scrollRaf = useRef<number | null>(null);
  const scrollPending = useRef(0);
  /* 헤더는 스크롤되지 않으므로 본문의 가로 위치를 그대로 옮겨 준다 (위 onScroll 주석) */
  const headRef = useRef<HTMLDivElement>(null);

  const onScrollCoalesced = (next: number) => {
    scrollPending.current = next;
    if (scrollRaf.current !== null) return;
    scrollRaf.current = requestAnimationFrame(() => {
      scrollRaf.current = null;
      setScrollTop(scrollPending.current);
    });
  };
  useEffect(() => () => {
    if (scrollRaf.current !== null) cancelAnimationFrame(scrollRaf.current);
  }, []);
  const [height, setHeight] = useState(300);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [headerMenu, setHeaderMenu] = useState<{ x: number; y: number } | null>(null);
  const [draggingKey, setDraggingKey] = useState<ColumnKey | null>(null);

  const boxRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  /* ── 컬럼 순서 드래그의 "따라오는" 애니메이션 ─────────────────────────────
     원본은 QHeaderView.setSectionsMovable(True) 라 끌면 컬럼이 커서를 따라오고
     지나친 컬럼이 스스로 자리를 비킨다. 웹에는 그런 기본 동작이 없어 FLIP 으로
     같은 결을 만든다: 순서를 바꾸기 **직전**에 헤더/셀의 x 를 기록해 두고,
     새 레이아웃이 잡힌 뒤 옛 위치에서 새 위치로 미끄러뜨린다.
     (트리 루트 재정렬과 같은 140ms OutCubic — folder_tree._animate_root_reorder) */
  const colFlipRef = useRef<Map<HTMLElement, number> | null>(null);
  /* 한 번 순서를 바꾸면 **다시 그려질 때까지** 다음 판정을 막는다.
     막지 않으면 setColumns 가 반영되기 전에 mousemove 가 계속 들어와 같은 판정이
     반복되고, 한 번에 여러 칸이 밀리면서 애니메이션이 겹쳐 요동친다. */
  const colMovePendingRef = useRef(false);
  /* 끌고 있는 컬럼은 커서를 따라 translate 하므로 FLIP 애니메이션 대상에서 뺀다
     (둘이 같은 transform 을 다투면 칸이 튄다) */
  const dragKeyRef = useRef<ColumnKey | null>(null);
  const captureColLefts = () => {
    const root = rootRef.current;
    if (!root) return;
    const snap = new Map<HTMLElement, number>();
    const cells = root.querySelectorAll<HTMLElement>("[data-col]");
    /* 화면에 있는 셀이 너무 많으면 헤더만 움직인다 (끌기 중 프레임 유지) */
    const headerOnly = cells.length > 600;
    cells.forEach((el) => {
      if (headerOnly && !el.classList.contains("th")) return;
      if (el.getAttribute("data-col") === dragKeyRef.current) return;
      snap.set(el, el.getBoundingClientRect().left);
    });
    colFlipRef.current = snap;
  };
  const ROW_H = compact ? ROW_H_COMPACT : ROW_H_NORMAL;
  const suppressSortRef = useRef(false);
  /* 원본 set_pip 의 대상 경로 (없으면 빈 문자열) */
  const pipPath = pip?.path ?? "";
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const tipTimer = useRef<number | null>(null);

  const cancelTip = () => {
    if (tipTimer.current !== null) {
      window.clearTimeout(tipTimer.current);
      tipTimer.current = null;
    }
    setTip((value) => (value === null ? value : null));
  };

  /* 원본 정책: 좌버튼을 누른 채 이동(드래그 의도)일 때는 툴팁을 띄우지 않는다. */
  const scheduleTip = (event: React.MouseEvent, text: string) => {
    if (event.buttons !== 0 || !text) { cancelTip(); return; }
    const x = event.clientX;
    const y = event.clientY;
    if (tipTimer.current !== null) window.clearTimeout(tipTimer.current);
    tipTimer.current = window.setTimeout(() => {
      tipTimer.current = null;
      setTip({ x, y, text });
    }, TOOLTIP_DELAY_MS);
  };

  useEffect(() => () => {
    if (tipTimer.current !== null) window.clearTimeout(tipTimer.current);
  }, []);
  const anchorRef = useRef<number | null>(null);
  const activationTimer = useRef<number | null>(null);

  useLayoutEffect(() => {
    const snap = colFlipRef.current;
    colFlipRef.current = null;
    if (!snap) return;
    colMovePendingRef.current = false;
    snap.forEach((oldLeft, el) => {
      if (!el.isConnected) return;
      const delta = oldLeft - el.getBoundingClientRect().left;
      if (!delta) return;
      el.animate(
        [{ transform: `translateX(${delta}px)` }, { transform: "translateX(0)" }],
        { duration: 140, easing: "cubic-bezier(0.215, 0.61, 0.355, 1)" },
      );
    });
  }, [columns]);

  const visibleColumns = columns.filter((col) => col.visible);
  /* 드래그 판정용 — 렌더마다 최신 순서/폭을 담아 둔다.
     getBoundingClientRect 는 FLIP 애니메이션의 transform 이 섞인 좌표를 주므로
     (애니메이션 중 오판 → 재이동 → 요동) 판정은 이 폭 데이터로만 한다. */
  const visColRef = useRef<Array<{ key: ColumnKey; width: number }>>([]);
  visColRef.current = visibleColumns.map((col) => ({ key: col.key, width: col.width }));
  const totalWidth = visibleColumns.reduce((sum, col) => sum + col.width, 0);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const resize = () => setHeight(el.clientHeight);
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const data = columns.map(({ key, width, visible }) => ({ key, width, visible }));
    localStorage.setItem(STORE_KEY, JSON.stringify(data));
    /* 원본은 config "columns" 에도 같은 배열(순서·표시·너비)을 저장한다
       (_actual_save_config → results.column_layout(), main_window.py:4981). */
    onColumnsChange?.(data);
  }, [columns]);

  useEffect(() => {
    const close = () => setHeaderMenu(null);
    window.addEventListener("mousedown", close);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("resize", close);
      if (activationTimer.current !== null) window.clearTimeout(activationTimer.current);
    };
  }, []);

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const copy = [...rows];
    /* 원본 _NumericSortItem.__lt__ (results_table.py:297): 숫자 컬럼에서 값이 없는
       (미분석) 셀은 **정렬 방향과 무관하게 항상 맨 뒤**로 보낸다. */
    const missing = (v: unknown) => typeof v === "number" && !(v > 0);
    copy.sort((a, b) => {
      const av = sortValue(a, sort.key);
      const bv = sortValue(b, sort.key);
      if (missing(av) !== missing(bv)) return missing(av) ? 1 : -1;
      const cmp = typeof av === "number" && typeof bv === "number"
        ? av - bv
        : String(av).localeCompare(String(bv), "ko");
      return sort.dir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [rows, sort]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - BUFFER);
  const count = Math.ceil(height / ROW_H) + BUFFER * 2;
  const last = Math.min(sortedRows.length, first + count);
  const virtualRows = sortedRows.slice(first, last);

  const selectedRowsFor = (row: Row) => {
    if (selected.has(row.id)) return sortedRows.filter((item) => selected.has(item.id));
    return [row];
  };

  /* 원본 activate 경로 (results_table.py)
       - 선택은 즉시 바뀐다.
       - itemSelectionChanged → 35ms 단발 타이머 → _fire_activate → fileActivated
       - 원클릭(double_click_to_play=False)은 _on_cell_clicked 에서 즉시 fileActivated
       - _fire_activate 는 마지막으로 emit 한 경로와 같으면 건너뛴다 (중복 재생 방지)
     실제 재생 여부는 상위(_on_file_activated)에서 auto_preview 로 결정한다. */
  const lastActivatedRef = useRef<number | null>(null);

  /* ── 새 결과가 오면 선택을 비운다 (조사 S04) ────────────────────────────────
     원본 begin_results_stream (results_table.py:978) 은 결과를 채우기 시작할 때
     clearSelection() 과 _last_emitted_path 초기화를 함께 한다.
     PoC 는 이걸 하지 않아
       · 새로 검색한 뒤 다른 곳에 포커스를 두고 Space 를 누르면 **화면에 없는
         이전 행**이 재생되고,
       · Shift 범위 선택의 기준점(앵커)이 새 목록의 엉뚱한 줄을 가리켰다.
     ⚠ rows 는 같은 검색을 다시 돌려도 새 배열로 온다 — 원본도 그때 선택을
       비우므로 같은 동작이다. */
  const rowsSeenRef = useRef<Row[] | null>(null);
  useEffect(() => {
    if (rowsSeenRef.current === rows) return;
    rowsSeenRef.current = rows;
    setSelected(new Set());
    anchorRef.current = null;
    lastActivatedRef.current = null;
    onSelect(null);
  }, [rows]);

  /* 선택 변경(35ms 타이머) 경로 전용 — 같은 행이면 건너뛴다.
     원본 _fire_activate 의 `paths[0] == self._last_emitted_path` 가드다.
     더블클릭 모드에서는 이 함수를 호출하지 않는다. */
  const activate = (row: Row) => {
    if (lastActivatedRef.current === row.id) return;
    lastActivatedRef.current = row.id;
    onActivate(row);
  };

  /* 원클릭 모드는 **가드 없이 무조건** 발동한다 — 원본 _on_cell_clicked
     (results_table.py:703) 은 _last_emitted_path 를 검사하지 않고 갱신만 한다.
     ⚠ 여기에 가드를 걸면 끝까지 재생된 파일을 다시 클릭해도 아무 일이 없고
     (재생바도 끝에 멈춘 채) 더블클릭만 먹히는 증상이 된다 (사용자 보고 B07). */
  const activateNow = (row: Row) => {
    lastActivatedRef.current = row.id;
    onActivate(row);
  };

  const scheduleActivation = (row: Row) => {
    if (activationTimer.current !== null) window.clearTimeout(activationTimer.current);
    activationTimer.current = window.setTimeout(() => activate(row), 35);
  };

  const selectRow = (event: React.MouseEvent, row: Row, visualIndex: number) => {
    let next = new Set<number>();
    if (event.shiftKey && anchorRef.current !== null) {
      const a = Math.min(anchorRef.current, visualIndex);
      const b = Math.max(anchorRef.current, visualIndex);
      for (let i = a; i <= b; i++) next.add(sortedRows[i].id);
    } else if (event.ctrlKey || event.metaKey) {
      next = new Set(selected);
      if (next.has(row.id)) next.delete(row.id);
      else next.add(row.id);
      anchorRef.current = visualIndex;
    } else {
      next.add(row.id);
      anchorRef.current = visualIndex;
    }
    if (next.size === 0) next.add(row.id);
    setSelected(next);
    /* 선택은 즉시 반영 — Space 단축키가 바로 이 행을 대상으로 삼아야 한다. */
    onSelect(row);
    /* ── 정책 변경 (사용자 지시 2026-09-03) ────────────────────────────────────
       **더블클릭 모드에서는 단일 클릭으로 절대 재생하지 않는다.**

       원본은 더블클릭 모드에서도 선택이 바뀌면 35ms 타이머로 fileActivated 를 내고,
       상위가 auto_preview 를 보고 재생한다 (results_table.py _fire_activate).
       그래서 auto_preview 가 켜져 있으면 **처음 고르는 사운드는 한 번 클릭에 재생되고,
       같은 사운드를 다시 재생할 때만 더블클릭이 필요한** 어정쩡한 동작이 됐다.
       사용자 지적: "더블클릭모드는 그냥 처음 선택하는 사운드도 더블클릭해야 재생되도록 해".

       그래서 더블클릭 모드에서는 예약 활성화를 아예 걸지 않는다. 원클릭 모드에서는
       auto_preview 값과 무관하게 단일 클릭이 곧 재생이다. 더블클릭 모드의 뜻은
       "더블클릭해야 재생"으로 단순해진다. 원본과 의도적으로 다른 지점이다.
       (onDoubleClick 은 모드·auto_preview 와 무관하게 항상 재생하므로 그대로 둔다.) */
    if (!doubleClickToPlay) activateNow(row);
  };

  const keyboardRow = () => {
    const selectedIds = selected;
    const at = sortedRows.findIndex((row) => selectedIds.has(row.id));
    return at >= 0 ? at : (sortedRows.length ? 0 : -1);
  };

  const onTableKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!sortedRows.length) return;
    const at = keyboardRow();
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault(); event.stopPropagation();
      const nextAt = Math.max(0, Math.min(sortedRows.length - 1,
        at + (event.key === "ArrowDown" ? 1 : -1)));
      const row = sortedRows[nextAt];
      setSelected(new Set([row.id]));
      anchorRef.current = nextAt;
      onSelect(row);
      if (!doubleClickToPlay) scheduleActivation(row);
      boxRef.current?.scrollTo({
        top: Math.max(0, nextAt * ROW_H - Math.max(0, height - ROW_H) / 2),
      });
    } else if (event.key === " " && !event.repeat && at >= 0) {
      event.preventDefault(); event.stopPropagation();
      onToggleRow?.(sortedRows[at]);
    }
  };

  const resizeColumn = (event: React.MouseEvent, key: ColumnKey) => {
    event.stopPropagation();
    event.preventDefault();
    const startX = event.clientX;
    const col = columns.find((item) => item.key === key);
    if (!col) return;
    const startW = col.width;
    const move = (e: MouseEvent) => {
      setColumns((prev) => prev.map((item) => (
        item.key === key ? { ...item, width: Math.max(item.min, startW + e.clientX - startX) } : item
      )));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const toggleSort = (key: ColumnKey) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      return { key, dir: prev.dir === "asc" ? "desc" : "asc" };
    });
  };

  const setColumnVisible = (key: ColumnKey, visible: boolean) => {
    setColumns((prev) => {
      const current = prev.find((col) => col.key === key);
      if (current?.locked) return prev;
      const visibleCount = prev.filter((col) => col.visible).length;
      if (!visible && visibleCount <= 1) return prev;
      return prev.map((col) => col.key === key ? { ...col, visible } : col);
    });
  };

  const closeColumn = (event: React.MouseEvent, key: ColumnKey) => {
    event.stopPropagation();
    setColumnVisible(key, false);
  };

  const moveColumn = (from: ColumnKey, to: ColumnKey) => {
    if (from === to) return;
    setColumns((prev) => {
      const next = [...prev];
      const fromIndex = next.findIndex((col) => col.key === from);
      const toIndex = next.findIndex((col) => col.key === to);
      if (fromIndex < 0 || toIndex < 0) return prev;
      const [item] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, item);
      return next;
    });
  };

  /* 헤더 마우스 드래그로 컬럼 순서 변경 (원본 setSectionsMovable 대응).
     6px 이상 움직이면 재배치 모드로 들어가고, 놓은 위치의 컬럼과 자리를 바꾼다.
     재배치가 일어났으면 그 직후의 click(정렬)은 무시한다. */
  const beginHeaderDrag = (event: React.MouseEvent, key: ColumnKey) => {
    if (event.button !== 0) return;
    const target = event.target as HTMLElement;
    /* 폭 조절 그립 / 닫기 버튼에서 시작한 경우는 각자의 동작에 맡긴다 */
    if (target.closest(".th-grip") || target.closest(".th-close")) return;
    const startX = event.clientX;
    const head = (event.currentTarget as HTMLElement).parentElement;
    let moved = false;

    /* 원본 QHeaderView 의 MoveSection 규칙:
       커서가 이웃 칸에 들어가는 순간 바꾸는 게 아니라, **끌고 있는 칸이 이웃 칸의
       중앙선을 넘어야** 한 칸 이동한다. 그래서 조금 흔들려도 자리가 요동치지 않는다.
       좌표는 전부 "헤더 왼쪽 기준"으로 컬럼 폭을 더해 구한다 — DOM 측정을 쓰면
       FLIP 애니메이션의 transform 이 섞여 애니메이션이 도는 동안 오판한다
       (이게 "여전히 요동친다" 의 실제 원인이었다). */
    const selfEl = event.currentTarget as HTMLElement;
    const grabOffset = startX - selfEl.getBoundingClientRect().left;
    /* 컬럼 전체(헤더+셀)를 커서 따라 희미하게 끌어내는 표현.
       매 mousemove 마다 리렌더하지 않도록 CSS 변수만 갱신한다. */
    const setGhostDx = (dx: number) =>
      rootRef.current?.style.setProperty("--col-drag-dx", `${Math.round(dx)}px`);

    const move = (ev: MouseEvent) => {
      if (!moved && Math.abs(ev.clientX - startX) < 6) return;
      if (!moved) { moved = true; dragKeyRef.current = key; setDraggingKey(key); }
      if (!head) return;

      const cols = visColRef.current;
      const at = cols.findIndex((c) => c.key === key);
      if (at < 0) return;
      const lefts: number[] = [];
      let acc = 0;
      for (const c of cols) { lefts.push(acc); acc += c.width; }

      /* 헤더 자체는 애니메이션하지 않으므로 이 값만 DOM 에서 읽는다 */
      const headLeft = head.getBoundingClientRect().left;
      const virtualLeft = (ev.clientX - headLeft) - grabOffset;
      const virtualRight = virtualLeft + cols[at].width;
      /* 실제 자리와의 차이만큼 유령을 밀어 둔다 (자리를 바꾸면 0 에 가까워진다) */
      setGhostDx(virtualLeft - lefts[at]);
      if (colMovePendingRef.current) return;

      const swap = (to: ColumnKey) => {
        captureColLefts();
        colMovePendingRef.current = true;
        moveColumn(key, to);
      };

      /* 앞/뒤 임계값을 60%/40% 로 갈라 둔다 — 같은 중앙선을 쓰면 두 조건이 겹쳐
         손떨림에 바꿨다 되돌렸다를 반복한다 (탭에서 실제로 그렇게 보였다). */
      if (at > 0 && virtualLeft < lefts[at - 1] + cols[at - 1].width * 0.4) {
        swap(cols[at - 1].key);
        return;
      }
      if (at + 1 < cols.length
          && virtualRight > lefts[at + 1] + cols[at + 1].width * 0.6) {
        swap(cols[at + 1].key);
      }
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      /* 순서는 끌면서 이미 적용됐다 — 여기서는 정렬 클릭만 막는다 */
      if (moved) suppressSortRef.current = true;
      dragKeyRef.current = null;
      rootRef.current?.style.removeProperty("--col-drag-dx");
      setDraggingKey(null);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const onWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    /* 원본 viewportEvent: Leave/Wheel 이면 지연 툴팁을 숨긴다 */
    cancelTip();
    /* 원본 results_table.wheelEvent: Shift+휠 = 가로 스크롤, 틱당 50px
       (hbar.setValue(value - delta * 50 / 120)) */
    if (!event.shiftKey || !boxRef.current) return;
    event.preventDefault();
    boxRef.current.scrollLeft += Math.sign(event.deltaY) * 50;
  };

  return (
    <div className={"results" + (compact ? " compact" : "")} ref={rootRef}>
      <div
        ref={headRef}
        className="thead"
        style={{ width: Math.max(totalWidth, 1) }}
        onContextMenu={(event) => {
          event.preventDefault();
          setHeaderMenu({ x: event.clientX, y: event.clientY });
        }}
      >
        {visibleColumns.map((col) => (
          <div
            key={col.key}
            data-col={col.key}
            className={`th ${draggingKey === col.key ? "dragging col-dragging" : ""} ${col.align === "center" ? "center" : ""} ${sort?.key === col.key ? "sorted" : ""}`}
            style={{ width: col.width, flex: `0 0 ${col.width}px` }}
            onMouseDown={(event) => beginHeaderDrag(event, col.key)}
            onClick={() => {
              if (suppressSortRef.current) { suppressSortRef.current = false; return; }
              toggleSort(col.key);
            }}
          >
            <span>{col.label}</span>
            {sort?.key === col.key && (
              <span className={"th-sort" + (sort.dir === "asc" ? " asc" : "")}><IcoSortTri size={7} /></span>
            )}
            {!col.locked && (
              <button className="th-close" data-tip={`${col.label} 숨기기`} onClick={(event) => closeColumn(event, col.key)}>
                <IcoX size={6} />
              </button>
            )}
            <span className="th-grip" onMouseDown={(event) => resizeColumn(event, col.key)} />
          </div>
        ))}
      </div>

      <div
        className="tbody"
        ref={boxRef}
        tabIndex={0}
        onKeyDown={onTableKeyDown}
        onScroll={(event) => {
          onScrollCoalesced(event.currentTarget.scrollTop);
          /* ⚠ 가로 스크롤도 헤더에 옮겨야 한다. 헤더(.thead)와 본문(.tbody)은
             별개 요소이고 스크롤되는 쪽은 본문뿐이라, 예전에는 본문만 옆으로
             밀려 **컬럼 제목이 내용과 어긋났다** (사용자 신고 2026-09-14:
             "채널 수·비트뎁스가 목록엔 보이는데 헤더는 PATH 가 늘어난 것처럼
             보인다"). 세로 스크롤만 전달하고 있던 것이 원인이다.
             상태(state)가 아니라 style 을 직접 건드린다 — 스크롤마다 리렌더를
             일으키면 수천 행 가상 스크롤이 끊긴다. */
          if (headRef.current) {
            headRef.current.style.transform =
              `translateX(${-event.currentTarget.scrollLeft}px)`;
          }
          cancelTip();
        }}
        onWheel={onWheel}
      >
        <div className="tbody-inner" style={{ height: sortedRows.length * ROW_H, minWidth: totalWidth }}>
          {virtualRows.map((row, i) => {
            const visualIndex = first + i;
            const rowSelected = selected.has(row.id);
            return (
              <div
                key={row.id}
                className={"tr"
                  + (rowSelected ? " selected" : "")
                  /* pip 이 붙은 행이면 상태와 무관하게 행 강조 */
                  + (pipPath && (row.fullPath ?? "") === pipPath ? " playing" : "")
                  + (pipPath && (row.fullPath ?? "") === pipPath && pip?.state
                      ? ` pip-${pip.state}` : "")}
                style={{ top: visualIndex * ROW_H, width: totalWidth }}
                onMouseDown={(event) => {
                  /* DAW 드래그 아웃 — 원본과 같은 조건(누른 행의 세로 띠를 벗어나야
                     시작, 10px 미만 흔들림은 무시)으로만 OS 드래그를 띄운다. */
                  if (event.button !== 0) return;
                  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
                  /* 원본 판정은 행의 세로 띠 + **표 가로폭**을 함께 본다
                     (_within_press_row: QRect(0, top, viewport().width(), height)).
                     표 스크롤 영역의 좌우 경계를 같이 넘겨야 옆으로 끌 때도 잡힌다. */
                  const box = boxRef.current?.getBoundingClientRect();
                  const watch = watchRowDrag(
                    event.clientX, event.clientY, rect.top, rect.bottom,
                    () => {
                      /* 원본 selected_paths(): 선택 전체. 선택 밖 행에서 시작하면 그 행 */
                      const targets = selected.has(row.id) ? selectedRowsFor(row) : [row];
                      return targets.map((r) => r.fullPath || "").filter(Boolean);
                    },
                    () => onDragDropped?.(),
                    box?.left, box?.right,
                    /* 드래그 그림 — 끌고 있는 행 모습 그대로. 여러 개면 겹친 카드
                       + "N개" (사용자 결정 2026-09-07). 화면과 같은 열 구성/폭을
                       넘겨 같은 모양이 나오게 한다. */
                    () => {
                      const n = selected.has(row.id) ? selectedRowsFor(row).length : 1;
                      return makeRowDragImage(
                        visibleColumns.map((col) => ({
                          text: cellText(row, col.key),
                          width: col.width,
                          align: col.key === "duration" || col.key === "file_size"
                                 || col.key === "sample_rate" || col.key === "channels"
                                 || col.key === "bitrate" ? "right" as const : undefined,
                        })),
                        n, ROW_H);
                    },
                  );
                  const move = (ev: MouseEvent) => { if (watch.move(ev)) cleanup(); };
                  const up = () => cleanup();
                  const cleanup = () => {
                    watch.cancel();
                    window.removeEventListener("mousemove", move);
                    window.removeEventListener("mouseup", up);
                  };
                  window.addEventListener("mousemove", move);
                  window.addEventListener("mouseup", up);
                }}
                onClick={(event) => selectRow(event, row, visualIndex)}
                onDoubleClick={() => {
                  /* 더블클릭은 명시 재생 — 모드/auto_preview 와 무관하게 항상 재생 */
                  lastActivatedRef.current = row.id;
                  onPlay(row);
                }}
                onContextMenu={(event) => onContext(event, row, selectedRowsFor(row))}
              >
                {visibleColumns.map((col) => {
                  const content = cellText(row, col.key);
                  return (
                    <div
                      key={col.key}
                      data-col={col.key}
                      className={`td ${draggingKey === col.key ? "col-dragging" : ""} ${col.align ?? ""} ${col.key === "file_name" ? "td-name" : ""} ${["duration", "sample_rate", "file_size", "bitrate"].includes(col.key) ? "num" : ""}`}
                      style={{ width: col.width, flex: `0 0 ${col.width}px` }}
                      /* 원본은 파일명 셀의 ToolTipRole 에 **전체 경로**를 넣는다
                         (results_table._item_for_value: file_name → full_path).
                         나머지 셀은 셀 텍스트가 잘릴 때만 의미가 있다. */
                      onMouseMove={(event) => scheduleTip(event,
                        col.key === "file_name" ? (row.fullPath || content) : content)}
                      onMouseLeave={cancelTip}
                      onMouseDown={cancelTip}
                    >
                      {col.key === "file_name" ? (
                        <>
                          <span className="dot" />
                          <span className={`fmt ${row.fmt.toLowerCase()}`}>{row.fmt}</span>
                          <span>{content}</span>
                        </>
                      ) : content}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>

      {tip && (
        <div className="list-tip"
             /* 원본과 같은 커서 오프셋 (+16, +18) — folder_tree/results_table 공통 */
             style={{ left: Math.min(tip.x + 16, window.innerWidth - 430), top: tip.y + 18 }}>
          {tip.text}
        </div>
      )}

      {headerMenu && (
        <div
          className="ctxmenu column-menu"
          style={{ left: Math.min(headerMenu.x, window.innerWidth - 230), top: Math.min(headerMenu.y, window.innerHeight - 430) }}
          onMouseDown={(event) => event.stopPropagation()}
        >
          {/* 원본 _show_header_menu (results_table.py:891): 제목이나 구분선 없이
              컬럼 14개를 체크 항목으로만 나열한다. 잠금 컬럼(file_name)은
              체크된 상태로 비활성. */}
          {BASE_COLUMNS.map((base) => {
            const col = columns.find((item) => item.key === base.key) ?? base;
            return (
            <button
              key={col.key}
              className={`ctxitem menu-check ${col.locked ? "disabled" : ""}`}
              disabled={col.locked}
              onClick={() => setColumnVisible(col.key, !col.visible)}
            >
              <span className="checkmark">{col.visible && <IcoCheck size={13} />}</span>
              {col.label}
            </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
