import { useEffect, useMemo, useRef, useState, useLayoutEffect } from "react";
import type { TreeNode } from "../data";
import { IcoCaret, IcoMore, IcoPlus, IcoSearch, IcoStar, IcoX } from "../icons";
import { removeBlacklistPath } from "../backend";
import { t } from "../i18n";

type Props = {
  onContext: (e: React.MouseEvent, label: string, opts?: {
    isRoot?: boolean; hasChildren?: boolean; tab?: string; count?: number;
    isFavorite?: boolean; canMoveUp?: boolean; canMoveDown?: boolean; kind?: "tree" | "tab";
    /* 원본 이름변경/블랙리스트 대화상자는 실제 경로를 보여준다 */
    path?: string; paths?: string[];
    /* 트리/탭 안에서 처리하는 동작 (모두 접기 / 즐겨찾기 / 이 탭에서 삭제 /
       탭 이름 변경 / 탭 삭제) */
    actions?: {
      collapseAll?: () => void;
      favoriteToggle?: (add: boolean) => void;
      removeFromTab?: () => void;
      rename?: () => void;
      remove?: () => void;
    };
  }) => void;
  onBlacklistDetail: () => void;
  onNotify: (kind: "add" | "remove" | "info", text: string) => void;
  tree?: TreeNode;
  blacklistEntries?: Array<{ path: string; description: string }>;
  /* 원본 config "blacklist_expanded_h" — 블랙리스트 패널 펼침 높이 */
  blacklistH?: number;
  /* 원본 side_count 가 쓰는 등록 라이브러리 수 (manager.get_roots() 길이) */
  rootCount?: number;
  /* 원본 config "folder_display_names" — 트리 라벨만 사용자 지정으로 바뀐다.
     key 는 normpath.rstrip.lower 이므로 조회할 때 같은 형식으로 맞춘다. */
  displayNames?: Record<string, string>;
  /* 값이 바뀌면 블랙리스트 패널을 펼친다 (원본 add_paths 의 _set_expanded(True)) */
  openBlacklistTick?: number;
  /* 원본 rootOrderChangeRequested — 드래그로 바뀐 라이브러리 전체 순서 */
  onReorderRoots?: (paths: string[]) => void;
  /* 원본 config "favorites" / "tabs" / "last_selection" 복원값 (main_window.py:4925) */
  initialFavorites?: string[];
  initialTabs?: Array<{ name: string; items: string[]; excluded?: string[] }>;
  initialSelection?: { tab: string; path: string } | null;
  pruneSignal?: { id: number; paths: string[] } | null;
  /* 즐겨찾기·탭이 바뀔 때 상위에 알려 config 에 저장한다 */
  onFavoritesChange?: (favorites: string[]) => void;
  onTabsChange?: (tabs: Array<{ name: string; items: string[]; excluded: string[] }>) => void;
  /* 마지막 선택(탭 + 경로) — 원본은 재시작 시 그 경로만 펼쳐 복원한다 */
  onSelectionChange?: (selection: { tab: string; path: string }) => void;
  /* 블랙리스트 패널 높이 — 원본 config "blacklist_expanded_h" */
  onBlacklistHeightCommit?: (height: number) => void;
  onBlacklistChanged?: () => void;
  /* 결과 우클릭 → "이 라이브러리 파일 브라우저에서 보기" 로 들어오는 경로.
     원본 FolderTree.focus_path 처럼 조상까지 펼치고 그 노드를 선택한다. */
  focusPath?: string | null;
  /** 등록된 라이브러리 루트 경로 — 원본 ROLE_IS_ROOT 판정에 쓴다 (깊이가 아니라 등록 여부) */
  rootPaths?: string[];
  onFocusHandled?: () => void;
  /* 선택한 폴더(들) — 상태바 검색 기준 브레드크럼용.
     원본 _current_prefix / _current_prefixes (main_window.py:7208) */
  onScope?: (prefixes: string[]) => void;
};

const SAVED_BLACKLIST_H = "soundfield.blacklist.height";
/* 검색 제안 목록에 한 번에 그리는 최대 행 수. 원본은 상한이 없지만 DOM 목록은
   3만 행을 그리면 멈춘다 → 그리는 수만 제한하고 남은 개수는 목록 끝에 표시한다. */
const SEARCH_RENDER_CAP = 500;

/* .tree-row 높이 / .tree 의 padding-top — 상시 헤더 슬롯 계산에 쓴다 */
const ROW_H = 26;
/* 원본 _DROPDOWN_MAX_MATCHES — 드롭다운에 나열하는 매치 상한 */
const DROPDOWN_MAX_ROWS = 500;
const TREE_PAD_TOP = 6;
/* 드롭다운 행 높이 — .library-search-hit 과 같아야 연결선이 이어진다 */
const HIT_ROW_H = 22;
/* 트리 들여쓰기 — 원본 setIndentation(18) 의 1/2 */
const TREE_INDENT = 9;

/* 폴더 이름을 단어 구분 기호로 쪼갠 소문자 토큰 (원본 _tokenize).

   ⚠ `/[\W_]+/` 를 쓰지 말 것. JS 의 \W 는 **ASCII 전용**이라 한글을 구분자로 보고,
     "발소리" 같은 검색어가 통째로 쪼개져 토큰이 0개가 된다 → 한글 폴더 검색이
     아예 안 됐다 (실측 2026-09-08: split 결과 []).
     원본 파이썬의 \W 는 유니코드 기준이라 한글이 단어 문자다. 같은 뜻은
     "글자·숫자가 아닌 것"이며, 밑줄도 여기에 포함된다. */
const WORD_SPLIT = /[^\p{L}\p{N}]+/u;

function tokens(text: string) {
  return text.toLowerCase().split(WORD_SPLIT).filter(Boolean);
}

function matchesQuery(label: string, query: string) {
  const queryTokens = tokens(query);
  if (!queryTokens.length) return false;
  const labelTokens = tokens(label);
  let qi = 0;
  for (const token of labelTokens) {
    if (token.startsWith(queryTokens[qi])) {
      qi += 1;
      if (qi >= queryTokens.length) return true;
    }
  }
  return false;
}

function normPath(path: string) {
  return path.replace(/\//g, "\\").replace(/[\\/]+$/g, "").toLowerCase();
}

/* 즐겨찾기·사용자 탭 맨 위의 '전체' 행 (사용자 결정 2026-09-14).
   전체 탭에는 트리 루트가 곧 '전체'(id "all")지만, 다른 탭은 담긴 항목들이
   나란히 놓일 뿐이라 **한꺼번에 검색 범위로 잡을 방법이 없었다.**
   실제 트리 노드가 아니라 화면에서만 만드는 행이므로, 이 id 로 오는 드래그·
   우클릭·이름변경은 전부 막는다 (nodeById 에 없어 경로가 빈 값이다). */
const TAB_ALL_ID = "__tab_all__";

export function Sidebar({ onContext, onBlacklistDetail, onNotify, tree, blacklistEntries, blacklistH: savedBlacklistH, onScope, focusPath, onFocusHandled, rootPaths,
                         displayNames, openBlacklistTick, onReorderRoots,
                         initialFavorites, initialTabs, initialSelection, pruneSignal,
                         onFavoritesChange, onTabsChange, onSelectionChange,
                         onBlacklistHeightCommit, onBlacklistChanged, rootCount }: Props) {
  const treeData = tree ?? { id: "root", label: "", path: "", count: 0, children: [] };
  /* 원본 _can_move_root 판정용 — 루트(라이브러리)의 형제 안 위치 */
  const rootIndexOf = (id: string) =>
    (previewRootIds ?? (treeData.children ?? []).map((n) => n.id)).indexOf(id);

  /* ── 루트 드래그 재정렬 (원본 folder_tree.startDrag / _preview_root_drag_at /
       _restore_root_drag_snapshot / dropEvent, folder_tree.py:1673~1800) ──
     · 전체 탭 + 단일 선택 + 루트 + 형제 2개 이상일 때만 재정렬 드래그가 된다
       (_is_reorderable_root + parent.childCount() > 1)
     · 끌고 다니는 동안 실제 순서를 미리 바꿔 보여주고, 트리 밖으로 나가면
       스냅샷으로 되돌린 뒤 드래그를 취소한다 (dragLeaveEvent)
     · 놓으면 전체 루트 순서를 rootOrderChangeRequested 로 올린다
     · 자리를 옮긴 행들은 140ms OutCubic 으로 옛 위치에서 새 위치로 미끄러진다 */
  const rootDragRef = useRef<{ id: string; base: string[] } | null>(null);
  const [previewRootIds, setPreviewRootIds] = useState<string[] | null>(null);
  const rowElRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const flipRef = useRef<Map<string, number> | null>(null);

  const rootIdOrder = () => (treeData.children ?? []).map((n) => n.id);

  const captureRootTops = () => {
    const snap = new Map<string, number>();
    rowElRef.current.forEach((el, id) => {
      if (el.isConnected) snap.set(id, el.getBoundingClientRect().top);
    });
    flipRef.current = snap;
  };

  useLayoutEffect(() => {
    const snap = flipRef.current;
    flipRef.current = null;
    if (!snap) return;
    snap.forEach((oldTop, id) => {
      const el = rowElRef.current.get(id);
      if (!el || !el.isConnected) return;
      const delta = oldTop - el.getBoundingClientRect().top;
      if (!delta) return;
      /* 원본 _animate_root_reorder: 140ms, QEasingCurve.OutCubic */
      el.animate(
        [{ transform: `translateY(${delta}px)` }, { transform: "translateY(0)" }],
        { duration: 140, easing: "cubic-bezier(0.215, 0.61, 0.355, 1)" },
      );
    });
  }, [previewRootIds]);


  const isReorderableRoot = (node: TreeNode, depth: number) =>
    tab === "all" && depth === 1 && Boolean(node.path)
    && (treeData.children?.length ?? 0) > 1;

  /* 원본 _root_insert_index_at — 행 중앙선을 기준으로 삽입 위치를 정한다 */
  const rootInsertIndexAt = (clientY: number, order: string[]) => {
    let fallback = order.length;
    for (let i = 0; i < order.length; i += 1) {
      const el = rowElRef.current.get(order[i]);
      if (!el || !el.isConnected) continue;
      const rect = el.getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) return i;
      fallback = i + 1;
    }
    return fallback;
  };

  const previewRootDragAt = (insertIndex: number) => {
    const drag = rootDragRef.current;
    if (!drag) return;
    const order = previewRootIds ?? drag.base;
    const current = order.indexOf(drag.id);
    if (current < 0) return;
    let target = Math.max(0, Math.min(insertIndex, order.length));
    /* 원본과 같은 무동작 조건 — 제자리이거나 바로 아래면 아무것도 하지 않는다 */
    if (target === current || target === current + 1) return;
    const next = [...order];
    next.splice(current, 1);
    if (current < target) target -= 1;
    next.splice(target, 0, drag.id);
    captureRootTops();
    setPreviewRootIds(next);
  };

  const restoreRootDrag = () => {
    const drag = rootDragRef.current;
    if (!drag || !previewRootIds) return;
    captureRootTops();
    setPreviewRootIds(null);
  };
  const [open, setOpen] = useState<Set<string>>(new Set(["all"]));
  const [sel, setSel] = useState("all");
  /* 원본 setSelectionMode(ExtendedSelection) — Ctrl 로 개별 토글, Shift 로 범위.
     선택 행과 조상 행 모두 좌측 3px 바를 받는다. */
  const [multiSel, setMultiSel] = useState<Set<string>>(new Set(["all"]));
  const [tab, setTab] = useState("all");
  /* 사용자 탭 — 원본은 경로 목록만 보관하고 DB 는 건드리지 않는다. */
  const [userTabs, setUserTabs] = useState<string[]>([]);

  /* ── 사용자 탭 순서 드래그 (원본에 없는 기능 — 사용자 요청 개선) ────────────
     끌면 지나친 탭과 즉시 자리를 바꾸고, 비켜난 탭은 옛 위치에서 새 위치로
     미끄러진다 (트리 루트 재정렬/결과표 컬럼과 같은 140ms OutCubic).
     탭바는 여러 줄로 감기므로 가로·세로 둘 다 보정한다. */
  const [draggingTab, setDraggingTab] = useState<string | null>(null);
  const tabElRef = useRef<Map<string, HTMLButtonElement>>(new Map());
  const tabFlipRef = useRef<Map<string, { x: number; y: number }> | null>(null);

  const captureTabRects = () => {
    const snap = new Map<string, { x: number; y: number }>();
    tabElRef.current.forEach((el, name) => {
      if (!el.isConnected) return;
      const r = el.getBoundingClientRect();
      snap.set(name, { x: r.left, y: r.top });
    });
    tabFlipRef.current = snap;
  };

  /* 결과표 컬럼 이동과 **같은 방식**으로 판정한다 (사용자 요청):
       ① 좌표는 offsetLeft/offsetTop — layout 값이라 FLIP 애니메이션의 transform 에
          오염되지 않는다. getBoundingClientRect 를 쓰면 미끄러지는 중의 위치를 읽어
          오판 → 재이동 → 요동이 생긴다.
       ② 이웃에 커서가 닿는 순간 바꾸지 않고, **끌고 있는 탭이 이웃의 중앙선**을
          넘어야 한 칸 움직인다 (탭바는 여러 줄로 감기므로 같은 줄이면 가로,
          다른 줄이면 세로 중앙선으로 판정한다).
       ③ 한 번 바꾸면 화면이 다시 그려질 때까지 다음 판정을 막는다.
       ④ 끌고 있는 탭은 커서를 따라 희미하게 밀려 나온다 (CSS 변수만 갱신). */
  const tabMovePendingRef = useRef(false);
  const dragTabRef = useRef<string | null>(null);

  const beginTabDrag = (event: React.MouseEvent, name: string) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest(".tab-close")) return;
    if (userTabs.length < 2) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const selfEl = event.currentTarget as HTMLElement;
    const startRect = selfEl.getBoundingClientRect();
    const grabX = startX - startRect.left;
    const grabY = startY - startRect.top;
    const host = (selfEl.offsetParent as HTMLElement | null);
    let moved = false;

    const boxes = () => userTabs.map((key) => {
      const el = tabElRef.current.get(key);
      if (!el || !el.isConnected) return null;
      return { key, l: el.offsetLeft, t: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight };
    }).filter(Boolean) as Array<{ key: string; l: number; t: number; w: number; h: number }>;

    const setGhost = (dx: number, dy: number) => {
      const bar = selfEl.parentElement;
      bar?.style.setProperty("--tab-drag-dx", `${Math.round(dx)}px`);
      bar?.style.setProperty("--tab-drag-dy", `${Math.round(dy)}px`);
    };

    const move = (ev: MouseEvent) => {
      if (!moved) {
        /* 결과표 컬럼과 같은 6px 임계값 — 클릭으로 탭을 고르는 동작을 막지 않는다 */
        if (Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) < 6) return;
        moved = true;
        dragTabRef.current = name;
        setDraggingTab(name);
      }
      if (!host) return;
      const list = boxes();
      const at = list.findIndex((b) => b.key === name);
      if (at < 0) return;
      const me = list[at];

      const hostRect = host.getBoundingClientRect();
      const px = ev.clientX - hostRect.left;
      const py = ev.clientY - hostRect.top;
      const vLeft = px - grabX;
      const vTop = py - grabY;
      setGhost(vLeft - me.l, vTop - me.t);

      if (tabMovePendingRef.current) return;

      /* ⚠ 탭바는 **여러 줄로 감긴다**. 그래서 "순서상 다음"이 화면에서 오른쪽/아래라는
         보장이 없다. 자리를 바꾸면 그 이웃이 반대쪽으로 가는데, 방향을 순서로 가정하면
         같은 조건이 영구히 성립해 무한 왕복한다 (실측 로그:
           me=139@t30  N=8@t60 ->FWD  →  me=8@t60  N=139@t30 ->FWD  → ...).
         → 방향은 **좌표에서 직접** 구하고, 그 방향으로 이웃의 중앙선을 넘어야 움직인다.
         왕복 임계값도 60%/40% 로 갈라 손떨림에 흔들리지 않게 한다. */
      const passes = (nb: typeof me | undefined) => {
        if (!nb) return false;
        const sameRow = Math.abs(nb.t - me.t) < me.h * 0.5;
        if (sameRow) {
          const rightward = nb.l > me.l;         /* 이웃이 실제로 오른쪽에 있나 */
          return rightward
            ? vLeft + me.w > nb.l + nb.w * 0.6
            : vLeft < nb.l + nb.w * 0.4;
        }
        const downward = nb.t > me.t;            /* 이웃이 실제로 아래 줄인가 */
        return downward
          ? vTop + me.h > nb.t + nb.h * 0.6
          : vTop < nb.t + nb.h * 0.4;
      };

      const swap = (to: string) => {
        captureTabRects();
        tabMovePendingRef.current = true;
        setUserTabs((prev) => {
          const next = [...prev];
          const from = next.indexOf(name);
          const dest = next.indexOf(to);
          if (from < 0 || dest < 0) return prev;
          next.splice(dest, 0, next.splice(from, 1)[0]);
          return next;
        });
      };

      const back = list[at - 1];
      const fwd = list[at + 1];
      if (passes(back)) swap(back.key);
      else if (passes(fwd)) swap(fwd.key);
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      /* 끌었으면 그 직후의 click(탭 선택)은 무시한다 */
      if (moved) tabDragSuppressRef.current = true;
      dragTabRef.current = null;
      const bar = selfEl.parentElement;
      bar?.style.removeProperty("--tab-drag-dx");
      bar?.style.removeProperty("--tab-drag-dy");
      setDraggingTab(null);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  const tabDragSuppressRef = useRef(false);

  useLayoutEffect(() => {
    const snap = tabFlipRef.current;
    tabFlipRef.current = null;
    if (!snap) return;
    tabMovePendingRef.current = false;
    snap.forEach((old, name) => {
      if (name === dragTabRef.current) return;   /* 끌고 있는 탭은 유령이 담당 */
      const el = tabElRef.current.get(name);
      if (!el || !el.isConnected) return;
      const r = el.getBoundingClientRect();
      const dx = old.x - r.left;
      const dy = old.y - r.top;
      if (!dx && !dy) return;
      el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "translate(0, 0)" }],
        { duration: 140, easing: "cubic-bezier(0.215, 0.61, 0.355, 1)" },
      );
    });
  }, [userTabs]);
  /* ── 탭 소속 모델 (원본 library_tabs.py:513 의 _tabs 메타) ──
     items    = 사용자가 '이 탭에 추가'(드롭)한 경로. 탭에서 root 로 보인다.
     excluded = '이 탭에서 삭제'로 가려진 하위 경로 (가시성 차집합).
     전체 탭은 둘 다 쓰지 않는다(자동). 즐겨찾기 탭의 excluded 는 세션 한정. */
  const [tabItems, setTabItems] = useState<Record<string, string[]>>({});
  const [tabExcluded, setTabExcluded] = useState<Record<string, string[]>>({});
  const [favorites, setFavorites] = useState<string[]>([]);
  /* 드래그가 올라온 탭 — 원본 _TabButton.dragEnterEvent 의 강조 상태 */
  const [dragOverTab, setDragOverTab] = useState<string | null>(null);
  const [newTabName, setNewTabName] = useState<string | null>(null);
  const [tabError, setTabError] = useState("");
  /* 원본은 DB 에서 제외 목록을 관리한다. 백엔드 연결 전까지 로컬 상태로만 반영하고,
     실제 검색 제외는 DB 연결 시 붙인다. */
  const [blacklist, setBlacklist] = useState(blacklistEntries ?? []);
  /* 검색 결과 드랍다운 접기/펼치기 — 원본 돋보기 검색의 목록 토글 */
  const [dropdownOpen, setDropdownOpen] = useState(true);
  /* ── 검색 드롭다운 위치/폭 (원본 _show_dropdown, library_tabs.py:1476) ──────
     원본은 팝업창이라 **파일 브라우저 폭에 갇히지 않는다**:
       left  = 패널 왼쪽
       top   = 순차찾기(nav) 행 **아래**  (nav 행을 가리지 않아 ↑↓ 를 바로 누를 수 있다)
       width = max(패널폭, min(콘텐츠폭, 화면 오른쪽-8px))
     PoC 는 패널 안에 끼운 div 여서 폭이 패널에 묶여 있었다 → 화면 좌표 팝업으로 옮긴다.
     콘텐츠폭 계산은 브라우저에게 맡긴다 (width:max-content + min/max-width). */
  const navRowRef = useRef<HTMLDivElement>(null);
  const searchRowRef = useRef<HTMLDivElement>(null);
  const dropElRef = useRef<HTMLDivElement>(null);
  const [dropAnchor, setDropAnchor] = useState<{ left: number; top: number; minW: number } | null>(null);
  const newTabRef = useRef<HTMLInputElement>(null);
  const [blacklistOpen, setBlacklistOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [matchIndex, setMatchIndex] = useState(-1);
  const searchRef = useRef<HTMLInputElement>(null);
  const [treeScroll, setTreeScroll] = useState(0);
  /* 원본 reposition(): 오버레이를 **viewport** 우측 가장자리에 맞춘다 —
     세로 스크롤바 위에 겹치지 않는다. 스크롤바 폭만큼 안으로 들인다. */
  const [treeScrollbarW, setTreeScrollbarW] = useState(0);

  /* ── 라이브러리 루트 판정 (원본 ROLE_IS_ROOT) ────────────────────────────────
     원본은 **등록된 라이브러리인지**로 정한다. folder_tree.py:1106 은 다른
     라이브러리 안에 중첩된 라이브러리(is_nested_root)도 root 로 표시한다.
     PoC 는 `depth <= 1` 로 판정해, 중첩되거나 깊은 라이브러리에서는
     [라이브러리 제거]/[완전 제거] 메뉴가 아예 나오지 않았다 (사용자 보고). */
  const rootKeySet = useMemo(() => new Set((rootPaths ?? []).map((path) =>
    normPath(path).replace(/[\\/]+$/, "").toLowerCase())), [rootPaths]);
  const isLibraryRoot = (node: TreeNode) => Boolean(node.path
    && rootKeySet.has(normPath(node.path).replace(/[\\/]+$/, "").toLowerCase()));

  
  const [centerOnId, setCenterOnId] = useState<string | null>(null);

  /* 원본 _prompt_rename_tab (library_tabs.py:1015) — 빈 이름/같은 이름이면 무시,
     다른 탭과 겹치면 "중복" 경고. 확정되면 탭 이름만 바꾼다 (items/excluded 유지). */
  const [renameTab, setRenameTab] = useState<{ from: string; value: string } | null>(null);
  const commitRenameTab = () => {
    if (!renameTab) return;
    const name = renameTab.value.trim();
    const from = renameTab.from;
    if (!name || name === from) { setRenameTab(null); setTabError(""); return; }
    if (name === "전체" || name === "즐겨찾기" || userTabs.includes(name)) {
      setTabError("같은 이름의 탭이 이미 있습니다.");
      return;
    }
    setUserTabs((tabs) => tabs.map((value) => (value === from ? name : value)));
    setTabItems((prev) => {
      const next = { ...prev };
      if (`user:${from}` in next) {
        next[`user:${name}`] = next[`user:${from}`];
        delete next[`user:${from}`];
      }
      return next;
    });
    setTabExcluded((prev) => {
      const next = { ...prev };
      if (`user:${from}` in next) {
        next[`user:${name}`] = next[`user:${from}`];
        delete next[`user:${from}`];
      }
      return next;
    });
    setTab((cur) => (cur === `user:${from}` ? `user:${name}` : cur));
    setRenameTab(null);
    setTabError("");
  };

  const commitNewTab = () => {
    const name = (newTabName ?? "").trim();
    if (!name) { setNewTabName(null); setTabError(""); return; }
    if (name === "전체" || name === "즐겨찾기" || userTabs.includes(name)) {
      setTabError("같은 이름의 탭이 이미 있습니다.");
      return;
    }
    setUserTabs((tabs) => [...tabs, name]);
    setTab(`user:${name}`);
    setNewTabName(null);
    setTabError("");
  };

  /* 원본 _remove_tab (1031) — QMessageBox 확인 후 삭제.
     "'{name}' 탭을 삭제할까요? / (라이브러리 자체는 그대로 유지됩니다)" */
  const [confirmRemoveTab, setConfirmRemoveTab] = useState<string | null>(null);
  const [confirmRemoveBl, setConfirmRemoveBl] = useState<string | null>(null);
  const removeUserTab = (name: string) => {
    setUserTabs((tabs) => tabs.filter((value) => value !== name));
    setTabItems((prev) => { const next = { ...prev }; delete next[`user:${name}`]; return next; });
    setTabExcluded((prev) => { const next = { ...prev }; delete next[`user:${name}`]; return next; });
    setTab((current) => (current === `user:${name}` ? "all" : current));
  };
  const [blacklistH, setBlacklistH] = useState(() => {
    const saved = Number(localStorage.getItem(SAVED_BLACKLIST_H));
    return Number.isFinite(saved) && saved >= 80 ? saved : 220;
  });

  useEffect(() => {
    if (savedBlacklistH && savedBlacklistH >= 80) setBlacklistH(savedBlacklistH);
  }, [savedBlacklistH]);

  useEffect(() => {
    if (blacklistEntries) setBlacklist(blacklistEntries);
  }, [blacklistEntries]);

  /* 원본 add_paths: 하나라도 추가되면 패널을 펼친다 */
  useEffect(() => {
    if (openBlacklistTick) setBlacklistOpen(true);
  }, [openBlacklistTick]);

  /* ── 저장된 즐겨찾기 / 사용자 탭 복원 (원본 load_user_tabs + favorites) ──
     한 번만 적용한다. 탭 이름은 원본 저장 형식({name, items, excluded})과 같다. */
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    if (!initialFavorites?.length && !initialTabs?.length) return;
    restoredRef.current = true;
    if (initialFavorites?.length) setFavorites(initialFavorites);
    if (initialTabs?.length) {
      setUserTabs(initialTabs.map((t) => t.name));
      setTabItems(Object.fromEntries(
        initialTabs.map((t) => [`user:${t.name}`, t.items ?? []])));
      setTabExcluded(Object.fromEntries(
        initialTabs.map((t) => [`user:${t.name}`, t.excluded ?? []])));
    }
  }, [initialFavorites, initialTabs]);

  /* 라이브러리 제거 직후 원본 prune_removed_roots와 같이 현재 열린 사이드바
     상태에서도 그 루트 이하 즐겨찾기/사용자 탭 참조를 즉시 걷어낸다. */
  const lastPruneRef = useRef(0);
  useEffect(() => {
    if (!pruneSignal || pruneSignal.id === lastPruneRef.current) return;
    lastPruneRef.current = pruneSignal.id;
    const roots = pruneSignal.paths.map(normPath);
    const under = (value: string) => {
      const key = normPath(value);
      return roots.some((root) => key === root || key.startsWith(root + "\\"));
    };
    setFavorites((items) => items.filter((path) => !under(path)));
    setTabItems((map) => Object.fromEntries(Object.entries(map).map(
      ([name, items]) => [name, items.filter((path) => !under(path))])));
    setTabExcluded((map) => Object.fromEntries(Object.entries(map).map(
      ([name, items]) => [name, items.filter((path) => !under(path))])));
    setSel((value) => value && under(value) ? "" : value);
  }, [pruneSignal]);

  /* 변경 알림 — 원본은 바뀔 때마다 _save_config (1초 디바운스) 한다 */
  useEffect(() => { onFavoritesChange?.(favorites); }, [favorites]);
  useEffect(() => {
    onTabsChange?.(userTabs.map((name) => ({
      name,
      items: tabItems[`user:${name}`] ?? [],
      excluded: tabExcluded[`user:${name}`] ?? [],
    })));
  }, [userTabs, tabItems, tabExcluded]);
  useEffect(() => {
    const node = sel ? nodeById[sel] : null;
    onSelectionChange?.({
      tab: tab === "all" ? "전체" : tab === "fav" ? "즐겨찾기" : tab.slice(5),
      path: node?.path ?? "",
    });
  }, [sel, tab]);

  /* ── 접기/펼치기 (원본 _on_item_expanded / _on_item_collapsed, folder_tree.py:763) ──
     접는 쪽에 정책이 하나 더 있다: **선택(검색 기준) 폴더의 상위를 접으면 선택이
     그 상위로 올라간다** (접힌 폴더의 자식은 안 보이므로 "검색 범위를 그 상위로
     올림"으로 해석 — 일반 탐색기 UX). 원본은 스크롤 위치도 보존한다. */
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        /* 접히는 노드가 현재 선택의 조상이면 선택을 그 노드로 올린다 */
        if (sel !== id && (chainOf[sel] ?? []).includes(id)) {
          const keep = treeBoxRef.current?.scrollTop ?? 0;
          setSel(id);
          setMultiSel(new Set([id]));
          /* 원본처럼 스크롤 위치를 그대로 지킨다 (선택 이동이 뷰를 튀게 하지 않도록) */
          requestAnimationFrame(() => {
            if (treeBoxRef.current) treeBoxRef.current.scrollTop = keep;
          });
        }
      } else {
        next.add(id);
      }
      return next;
    });

  /* ── 기본 펼침 깊이 (원본 load_folders, folder_tree.py:1112) ──
     전체 탭   : '전체' + 드라이브 그룹 노드까지만 펼친다 (라이브러리 안쪽은 접힘).
     그 외 탭  : 그룹 노드만 펼쳐 라이브러리 root 들이 보이게 한다.
     ⚠ 라이브러리 자체를 펼치지 않는 것이 정책이다 (시인성). 사용자가 직접 펼친
        상태는 세션 안에서 유지된다 (_user_expanded_state).
     ⚠ 펼침 상태는 재시작 간 저장하지 않는다 — 원본 config 주석의 명시 정책. */
  const treeReadyRef = useRef("");
  useEffect(() => {
    const signature = (treeData.children ?? []).map((n) => n.id).join("|");
    if (!signature || treeReadyRef.current === signature) return;
    treeReadyRef.current = signature;
    /* 그룹 노드 = 자식이 있는 최상위 노드 중 **등록된 라이브러리가 아닌** 것
       (드라이브/공통경로 묶음). 라이브러리 자체는 펼치지 않는 것이 원본 정책인데
       (folder_tree.py:1112 "라이브러리 안쪽은 접힘"), 예전에는 자식이 있으면
       라이브러리도 펼쳐서 트리가 전부 열려 보였다 (사용자 보고). */
    const groups = (treeData.children ?? [])
      .filter((n) => (n.children?.length ?? 0) > 0 && !isLibraryRoot(n))
      .map((n) => n.id);
    setOpen(new Set(["all", ...groups]));
  }, [treeData]);

  const flatNodes = useMemo(() => {
    const out: { id: string; label: string; path?: string; count?: number; incomplete?: number; depth: number; chain: string[] }[] = [];
    const walk = (node: TreeNode, depth: number, chain: string[]) => {
      out.push({ id: node.id, label: node.label, path: node.path, count: node.count, incomplete: node.incomplete, depth, chain });
      node.children?.forEach((child) => walk(child, depth + 1, [...chain, node.id]));
    };
    walk(treeData, 0, []);
    return out;
  }, [treeData]);

  const nodeById = useMemo(() => {
    const map: Record<string, TreeNode> = {};
    const walk = (node: TreeNode) => { map[node.id] = node; node.children?.forEach(walk); };
    walk(treeData);
    return map;
  }, [treeData]);

  const chainOf = useMemo(() => {
    const map: Record<string, string[]> = {};
    flatNodes.forEach((node) => { map[node.id] = node.chain; });
    return map;
  }, [flatNodes]);

  /* 원본 _norm_key 기반 "자신 또는 하위" 판정을 트리 구조로 대체한 것.
     경로 문자열 대신 노드 id 를 쓰지만 판정 결과는 같다. */
  const isUnder = (child: string, ancestor: string) =>
    child === ancestor || (chainOf[child] ?? []).includes(ancestor);

  /* ── 탭 드롭 (원본 library_tabs.py:_on_paths_dropped 1048) ──
     added / dup(이미 포함) / dup_child(이미 추가된 상위 폴더 하위) / absorbed(하위 흡수)
     네 갈래를 그대로 구분하고, 추가된 경로와 그 하위의 excluded 항목은 해제한다
     (_strip_excluded). 피드백 문구도 _emit_drop_feedback 그대로. */
  const dropOnTab = (tabKey: string, dropped: string[]) => {
    if (tabKey === "all") return;  // 전체 탭은 자동, 사용자 추가 불가

    const excluded = tabExcluded[tabKey] ?? [];
    const stripped = excluded.filter((e) => !dropped.some((d) => isUnder(e, d)));
    const excChanged = stripped.length !== excluded.length;

    if (tabKey === "fav") {
      const added: string[] = [];
      const dup: string[] = [];
      const next = [...favorites];
      dropped.forEach((id) => {
        if (next.includes(id)) { dup.push(id); return; }
        next.push(id);
        added.push(id);
      });
      if (added.length) setFavorites(next);
      if (excChanged) setTabExcluded((prev) => ({ ...prev, [tabKey]: stripped }));
      emitDropFeedback("★ 즐겨찾기", added, dup, [], []);
      if (added.length) flash("fav");
      return;
    }

    const items = [...(tabItems[tabKey] ?? [])];
    const added: string[] = [];
    const dup: string[] = [];
    const dupChild: string[] = [];
    const absorbed: string[] = [];
    let changed = false;

    dropped.forEach((id) => {
      if (items.includes(id)) { dup.push(id); return; }
      /* 더 큰 상위가 이미 있으면 커버됨 — 단 그 하위가 excluded 였다면
         이번 드롭으로 제외가 해제되므로 '재추가'로 센다 (원본 1093). */
      if (items.some((it) => isUnder(id, it))) {
        if (excluded.some((e) => isUnder(e, id) || isUnder(id, e))) added.push(id);
        else dupChild.push(id);
        return;
      }
      /* 새 경로가 기존 하위 items 를 포함하면 흡수 → items 에서 제거 */
      for (let i = items.length - 1; i >= 0; i -= 1) {
        if (isUnder(items[i], id) && items[i] !== id) absorbed.push(...items.splice(i, 1));
      }
      items.push(id);
      added.push(id);
      changed = true;
    });

    if (changed) setTabItems((prev) => ({ ...prev, [tabKey]: items }));
    if (excChanged) setTabExcluded((prev) => ({ ...prev, [tabKey]: stripped }));
    emitDropFeedback(tabLabel(tabKey), added, dup, absorbed, dupChild);
    if (added.length) flash(tabKey);
  };

  /* 어느 탭에 들어갔는지 눈으로 보이게 잠시 깜빡인다 (사용자 결정 2026-09-14).
     토스트만으로는 "어디에" 들어갔는지 알 수 없다는 지적을 반영한 것이다. */
  const [flashTab, setFlashTab] = useState("");
  const flashTimer = useRef(0);
  const flash = (key: string) => {
    setFlashTab(key);
    window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlashTab(""), 1100);
  };
  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  /* 원본 _emit_drop_feedback (1142) — + 는 타겟 탭만, ! 는 문제 상황만 */
  const emitDropFeedback = (tabName: string, added: string[], dup: string[],
                            absorbed: string[], dupChild: string[]) => {
    if (!(added.length || dup.length || absorbed.length || dupChild.length)) return;
    if (added.length) onNotify("add", `'${tabName}' 에 추가`);
    else if (dupChild.length && !dup.length) onNotify("info", "이미 추가된 상위 폴더에\n포함되어 있습니다");
    else onNotify("info", "이미 포함된 라이브러리입니다");
  };

  const tabLabel = (key: string) =>
    key === "all" ? "전체" : key === "fav" ? "★ 즐겨찾기" : key.slice(5);

  /* 원본 _resolve_roots (472) — excluded 에 가려지지 않은 included 만 root 로 */
  const activeExcluded = tab === "all" ? [] : (tabExcluded[tab] ?? []);
  const activeRoots = useMemo(() => {
    if (tab === "all") return ["all"];
    const included = tab === "fav" ? favorites : (tabItems[tab] ?? []);
    return included.filter((id) => !activeExcluded.some((e) => isUnder(id, e)));
  }, [tab, favorites, tabItems, activeExcluded, chainOf]);

  /* 노드 → 표시 경로. 원본은 절대경로를 그대로 쓰지만 PoC 트리는 라벨만 있으므로
     조상 라벨을 역슬래시로 이어 만든다 ('전체' 는 경로가 없어 제외). */
  const pathOf = (id: string) => nodeById[id]?.path ?? "";

  /* ⚠ activeRoots 를 아래 의존 목록에 넣지 말 것. 매 렌더마다 새 배열이라
     onScope → 상위 상태 변경 → 재렌더 → 새 배열 → 다시 실행으로 **무한 루프**가
     돌아 화면이 통째로 죽는다 (2026-09-14 실제로 겪음: React error #185). */
  const activeRootsRef = useRef<string[]>([]);
  activeRootsRef.current = activeRoots;
  useEffect(() => {
    if (!onScope) return;
    /* '전체' 행을 고르면 그 탭에 담긴 항목 **전부**가 검색 범위가 된다.
       (합성 행이라 경로가 없다 — 여기서 실제 항목들로 펼쳐 준다) */
    const ids = multiSel.has(TAB_ALL_ID)
      ? activeRootsRef.current
      : [...multiSel];
    const paths = ids.map(pathOf).filter(Boolean);
    onScope(paths);
  }, [multiSel, chainOf, nodeById]);

  /* ── 키보드 조작 (원본은 QTreeWidget 기본 동작을 그대로 쓴다) ──────────
     Qt QTreeView 의 기본 키 정책을 그대로 옮긴다.
       ↓ / ↑        : 다음 / 이전 **보이는** 행
       →            : 접혀 있으면 펼치기, 이미 펼쳐졌으면 첫 자식으로
       ←            : 펼쳐져 있으면 접기, 접혀 있으면 부모로
       Home / End   : 첫 행 / 마지막 행
       PageUp/Down  : 뷰포트 한 화면만큼
       Enter        : 활성화(= 선택 확정, 원본 itemActivated)
       *            : 현재 항목의 모든 하위 펼치기
       글자 입력    : 그 글자로 시작하는 다음 항목으로 (Qt keyboardSearch)
     Shift 조합은 범위 선택, Ctrl+방향키는 선택을 옮기지 않고 커서만 이동한다. */
  const treeBoxRef = useRef<HTMLDivElement>(null);
  const searchBufRef = useRef<{ text: string; at: number }>({ text: "", at: 0 });

  const rowIndexOf = (id: string) => visibleRows.findIndex((row) => row.node.id === id);

  const moveTo = (index: number, extend: boolean) => {
    if (index < 0 || index >= visibleRows.length) return;
    const target = visibleRows[index].node.id;
    setSel(target);
    setMultiSel((prev) => {
      if (!extend) return new Set([target]);
      const from = rowIndexOf(sel);
      if (from < 0) return new Set([target]);
      const [lo, hi] = from < index ? [from, index] : [index, from];
      return new Set(visibleRows.slice(lo, hi + 1).map((row) => row.node.id));
    });
    /* 선택이 화면 밖이면 스크롤 (Qt scrollTo 기본 동작) */
    const box = treeBoxRef.current;
    if (box) {
      const top = TREE_PAD_TOP + index * ROW_H;
      if (top < box.scrollTop) box.scrollTop = top;
      else if (top + ROW_H > box.scrollTop + box.clientHeight)
        box.scrollTop = top + ROW_H - box.clientHeight;
    }
  };

  const expandAllUnder = (id: string) => {
    const node = nodeById[id];
    if (!node) return;
    const ids: string[] = [];
    const walk = (n: TreeNode) => {
      if (n.children?.length) { ids.push(n.id); n.children.forEach(walk); }
    };
    walk(node);
    setOpen((prev) => new Set([...prev, ...ids]));
  };

  const onTreeKeyDown = (event: React.KeyboardEvent) => {
    const index = rowIndexOf(sel);
    const row = index >= 0 ? visibleRows[index] : null;
    const node = row?.node;
    const hasKids = Boolean(node?.children?.length);
    const isOpenNow = node ? open.has(node.id) : false;
    const pageRows = Math.max(1, Math.floor((treeBoxRef.current?.clientHeight ?? ROW_H * 10) / ROW_H) - 1);
    const extend = event.shiftKey;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        moveTo(index < 0 ? 0 : index + 1, extend);
        return;
      case "ArrowUp":
        event.preventDefault();
        moveTo(index < 0 ? 0 : index - 1, extend);
        return;
      case "ArrowRight":
        event.preventDefault();
        if (!node) return;
        if (hasKids && !isOpenNow) setOpen((prev) => new Set([...prev, node.id]));
        else if (hasKids) moveTo(index + 1, false);   // 첫 자식 = 다음 보이는 행
        return;
      case "ArrowLeft":
        event.preventDefault();
        if (!node) return;
        if (hasKids && isOpenNow) {
          setOpen((prev) => { const next = new Set(prev); next.delete(node.id); return next; });
        } else {
          const parent = (chainOf[node.id] ?? []).at(-1);
          if (parent) moveTo(rowIndexOf(parent), false);
        }
        return;
      case "Home":
        event.preventDefault();
        moveTo(0, extend);
        return;
      case "End":
        event.preventDefault();
        moveTo(visibleRows.length - 1, extend);
        return;
      case "PageDown":
        event.preventDefault();
        moveTo(Math.min(visibleRows.length - 1, (index < 0 ? 0 : index) + pageRows), extend);
        return;
      case "PageUp":
        event.preventDefault();
        moveTo(Math.max(0, (index < 0 ? 0 : index) - pageRows), extend);
        return;
      case "Enter":
        event.preventDefault();
        if (node) { setSel(node.id); setMultiSel(new Set([node.id])); }
        return;
      case "*":
        event.preventDefault();
        if (node) expandAllUnder(node.id);
        return;
      default:
        break;
    }
    /* Qt keyboardSearch — 1초 안에 이어 친 글자를 접두로 다음 항목 찾기 */
    if (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) {
      const now = Date.now();
      const buf = searchBufRef.current;
      buf.text = now - buf.at > 1000 ? event.key : buf.text + event.key;
      buf.at = now;
      const needle = buf.text.toLowerCase();
      const start = index < 0 ? 0 : index + (buf.text.length > 1 ? 0 : 1);
      for (let step = 0; step < visibleRows.length; step += 1) {
        const at = (start + step) % visibleRows.length;
        if (visibleRows[at].node.label.toLowerCase().startsWith(needle)) {
          event.preventDefault();
          moveTo(at, false);
          return;
        }
      }
    }
  };

  /* 원본 FolderTree.focus_path — 주어진 경로(파일이면 그 폴더)를 트리에서 찾아
     조상을 모두 펼치고 선택한다. 정확히 일치하는 노드가 없으면 가장 깊은 조상. */
  useEffect(() => {
    if (!focusPath) return;
    const target = normPath(focusPath).toLowerCase();
    let best: { id: string; len: number } | null = null;
    for (const node of flatNodes) {
      const nodePath = normPath(node.path || "").toLowerCase();
      if (!nodePath) continue;
      if (target === nodePath || target.startsWith(nodePath + "\\")) {
        if (!best || nodePath.length > best.len) best = { id: node.id, len: nodePath.length };
      }
    }
    if (best) {
      setOpen((prev) => new Set([...prev, ...(chainOf[best!.id] ?? []), best!.id]));
      setSel(best.id);
      setMultiSel(new Set([best.id]));
      /* 원본 focus_path 3단계 (folder_tree.py:2168): setCurrentItem 다음에
         scrollToItem(PositionAtCenter) + horizontalScrollBar().setValue(0).
         이 스크롤이 빠져 있어 트리가 이전 위치에 그대로 있었다. */
      setCenterOnId(best.id);
    }
    onFocusHandled?.();
  }, [focusPath, flatNodes, chainOf]);

  /* 원본 _pending_last_selection — 저장된 경로를 그 탭에서 펼쳐 선택한다.
     원본 select_saved (library_tabs.py:783) 는 순서가 정해져 있다:
       ① 저장된 탭을 먼저 활성화한다 (못 찾으면 지금 탭에서 시도)
       ② 그 탭의 트리에서 경로를 선택한다
       ③ 노드가 아직 없으면 False 를 돌려주고 **다음 기회에 다시 시도**한다
     PoC 는 ①을 아예 하지 않아 사용자 탭에서 끄고 다시 켜면 '전체' 로 돌아왔고,
     ③도 노드를 찾기 **전에** 완료 표시를 세워 뒤늦게 트리가 도착해도 다시
     복원하지 않았다 (조사 S03). */
  const selRestoredRef = useRef(false);
  useEffect(() => {
    if (selRestoredRef.current || !initialSelection?.path) return;
    if (!flatNodes.length) return;
    const target = normPath(initialSelection.path).toLowerCase();
    const hit = flatNodes.find((node) =>
      node.path && normPath(node.path).toLowerCase() === target);
    if (!hit) return;                 /* 아직 트리에 없다 — 다음 데이터에서 다시 */
    selRestoredRef.current = true;    /* 찾은 뒤에만 '복원 끝' 으로 표시한다 */
    /* 저장된 탭 이름 → 탭 키. 없는 사용자 탭이면 지금 탭에 그대로 둔다 (원본과 같음) */
    const name = initialSelection.tab || "";
    if (name === "전체") setTab("all");
    else if (name === "즐겨찾기") setTab("fav");
    else if (name && userTabs.includes(name)) setTab(`user:${name}`);
    setOpen((prev) => new Set([...prev, ...(chainOf[hit.id] ?? []), hit.id]));
    setSel(hit.id);
    setMultiSel(new Set([hit.id]));
  }, [initialSelection, flatNodes, chainOf, userTabs]);


  /* 트리 항목 드래그 — 원본 mimeData(630): 선택 노드들의 경로를 JSON 으로 싣고,
     '전체'(빈 경로) 는 제외한다. 우클릭과 같은 규칙으로 드래그한 항목이 선택
     안에 있으면 선택 전체가 대상이다. */
  const dragPayload = (id: string) => {
    const ids = multiSel.has(id) ? [...multiSel] : [id];
    return ids.filter((value) => value !== "all");
  };

  /* ── 라이브러리 검색 매치 (원본 _apply_search + iter_folder_nodes) ──
     ⚠ 정렬하지 않는다. 원본은 iter_folder_nodes() 의 **트리 순회 순서**를 그대로
        _search_matches 로 쓰므로 '하나씩 찾기(↑/↓)' 도 트리 위→아래 순서로 돈다.
        (개수 내림차순 정렬은 폐기된 legacy 완성기 경로의 정책이다 — 여기 아님)
     · 이름은 실제 폴더명(경로 마지막 칸) 기준
     · 블랙리스트 경로와 그 하위는 매치 목록에서 빼고 따로 모아
       드롭다운에서 빗금+빨강+사유로만 보여준다 (클릭 불가) */
  const searchNodes = useMemo(() => {
    const reasonOf = new Map(blacklist.map((bp) =>
      [normPath(bp.path).toLowerCase(), bp.description ?? ""]));
    const out: Array<{ id: string; label: string; path?: string; count?: number;
                       reason: string | null }> = [];
    const reasonKey = (node: TreeNode) => {
      const key = node.path ? normPath(node.path).toLowerCase() : "";
      return key && reasonOf.has(key) ? reasonOf.get(key)! : null;
    };
    const push = (node: TreeNode, reason: string | null) => {
      if (node.path) out.push({ id: node.id, label: node.label,
                                path: node.path, count: node.count, reason });
    };
    const walk = (node: TreeNode, inherited: string | null) => {
      (node.children ?? []).forEach((child) => {
        /* '이 탭에서 삭제' 로 가려진 하위는 검색 후보에서도 뺀다 — 화면에 없는
           폴더가 검색 결과로 나오면 클릭해도 갈 곳이 없다 */
        if (activeExcluded.some((e) => isUnder(child.id, e))) return;
        const reason = inherited ?? reasonKey(child);
        push(child, reason);
        walk(child, reason);
      });
    };
    /* ⚠ 후보는 **지금 탭에 보이는 범위** 로 제한한다 (조사 Q08, 사용자 지시).
       원본은 current_tree().iter_folder_nodes() 로 현재 탭만 돈다
       (library_tabs.py:1534). PoC 는 항상 전체 트리를 돌아, 사용자 탭에 담지도
       않은 폴더가 검색에 잡히고 눌러도 그 탭에서는 드러낼 수 없었다. */
    if (tab === "all") {
      walk(treeData, null);
    } else {
      activeRoots.forEach((id) => {
        const node = nodeById[id];
        if (!node) return;
        /* 탭에 담긴 폴더 자신도 후보다. 그 위 조상이 블랙리스트면 사유를 물려받는다. */
        const inherited = (chainOf[id] ?? [])
          .map((ancestorId) => nodeById[ancestorId])
          .filter(Boolean)
          .map((ancestor) => reasonKey(ancestor))
          .find((reason) => reason !== null) ?? null;
        const reason = inherited ?? reasonKey(node);
        push(node, reason);
        walk(node, reason);
      });
    }
    return out;
  }, [treeData, blacklist, tab, activeRoots, activeExcluded, nodeById, chainOf]);

  /* ⚠ 원본의 라이브러리 검색 제안에는 **개수 상한이 없다**
     (folder_tree.iter_folder_nodes 는 전부 돌려주고, library_tabs 의 소비부도 자르지 않는다).
     PoC 가 임의로 500개에서 잘라 **나머지를 조용히 감췄다**.
     DOM 목록은 3만 행을 그리면 멈추므로, 자르는 건 유지하되 **감추지 않는다** —
     아래 목록 끝에 "…외 N개" 를 붙여 전체 수를 드러낸다
     (원본도 목록이 길면 `children[:12]` + "… 외 N개" 로 같은 방식을 쓴다). */
  const allMatches = useMemo(() => searchNodes
    .filter((node) => node.reason === null && matchesQuery(node.label, searchText)),
    [searchNodes, searchText]);
  /* ⚠ `matches` 는 **드롭다운에 그리는 목록**일 뿐이다 (500개 상한).
     개수 표시와 [하나씩 찾기 ↑/↓] 는 반드시 `allMatches`(전부)를 써야 한다
     (조사 Q07). 예전에는 여기서도 잘린 목록을 써서, 501번째 이후 폴더는 개수에도
     안 잡히고 ↓ 로도 절대 갈 수 없었다 — 원본은 상한 없이 전부 돈다. */
  const matches = useMemo(() => allMatches.slice(0, SEARCH_RENDER_CAP), [allMatches]);
  const hiddenMatchCount = allMatches.length - matches.length;

  useEffect(() => {
    if (!(dropdownOpen && searchText.trim())) { setDropAnchor(null); return; }
    const measure = () => {
      const nav = navRowRef.current;
      if (!nav) return;
      const navRect = nav.getBoundingClientRect();
      /* 원본 left = self.mapToGlobal(0,0).x — 패널(파일 브라우저) 왼쪽 */
      const panel = nav.closest(".sidebar") as HTMLElement | null;
      const panelRect = (panel ?? nav).getBoundingClientRect();
      setDropAnchor({
        left: Math.round(panelRect.left),
        top: Math.round(navRect.bottom),
        minW: Math.round(panelRect.width),
      });
    };
    measure();
    window.addEventListener("resize", measure);
    /* 스플리터로 패널 폭이 바뀌어도 따라오게 */
    const ro = new ResizeObserver(measure);
    const panel = navRowRef.current?.closest(".sidebar");
    if (panel) ro.observe(panel);
    return () => {
      window.removeEventListener("resize", measure);
      ro.disconnect();
    };
  }, [dropdownOpen, searchText, matches.length]);

  /* ── 검색과 무관한 동작이면 결과 목록만 닫는다 (검색어는 그대로 남긴다) ──────
     원본 _show_dropdown 은 app.installEventFilter 로 **바깥 클릭**을 감시해 닫는다
     (library_tabs.py:1494). 여기서는 검색행/순차찾기행/드롭다운 밖의 mousedown,
     그리고 검색창에 포커스가 없는 상태의 키 입력을 '다른 동작' 으로 본다. */
  useEffect(() => {
    if (!(dropdownOpen && searchText.trim())) return;
    const inside = (target: Node | null) =>
      Boolean(target) && [searchRowRef, navRowRef, dropElRef]
        .some((ref) => ref.current?.contains(target as Node));
    const onDown = (event: MouseEvent) => {
      if (!inside(event.target as Node)) setDropdownOpen(false);
    };
    const onKey = () => {
      if (document.activeElement !== searchRef.current) setDropdownOpen(false);
    };
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [dropdownOpen, searchText]);

  /* 원본 _search_blacklisted — 검색어에 걸린 블랙리스트 경로(비활성 표시 전용) */
  const blacklistedMatches = useMemo(() => searchNodes
    .filter((node) => node.reason !== null && matchesQuery(node.label, searchText))
    .slice(0, 500), [searchNodes, searchText]);

  /* ── 검색 드롭다운 행 구성 (원본 _dropdown_rows_for_matches, library_tabs.py:1547) ──
     매치된 폴더만 나열하지 않고 **조상 체인까지 포함해 트리 형태**로 보여준다.
     · 조상(비매치) 행은 dim(#5b636f) + 클릭 불가 — 문맥 표시용
     · depth 만큼 14px 들여쓰고, 연결선(│ ├ └)을 그려 구조를 한눈에 보이게 한다
     · cont[k] = k 단계 조상이 "아직 형제가 남았는가" → 통과 세로선 여부
     · 우측에 개수(rgba(78,144,232,200)) */
  type HitRow = {
    id: string; label: string; depth: number; isMatch: boolean;
    isLast: boolean; cont: boolean[]; count?: number; chain: string[];
    /* 블랙리스트면 사유 문자열 (사유 없으면 ""), 아니면 null */
    reason: string | null;
  };

  const hitRows = useMemo<HitRow[]>(() => {
    if (!searchText.trim() || !(matches.length || blacklistedMatches.length)) return [];
    const matchIds = new Set(matches.map((m) => m.id));
    const reasonById = new Map(blacklistedMatches.map((m) => [m.id, m.reason ?? ""]));
    /* 매치(+블랙리스트 매치) + 그 조상들만 남긴 부분 트리를 만든다 */
    const keep = new Set<string>();
    for (const m of [...matches, ...blacklistedMatches]) {
      keep.add(m.id);
      (chainOf[m.id] ?? []).forEach((id) => keep.add(id));
    }
    const out: HitRow[] = [];
    const walk = (node: TreeNode, depth: number, cont: boolean[], chain: string[]) => {
      const kids = (node.children ?? []).filter((child) => keep.has(child.id));
      kids.forEach((child, i) => {
        const isLast = i === kids.length - 1;
        out.push({
          id: child.id,
          label: child.label,
          depth,
          isMatch: matchIds.has(child.id),
          isLast,
          cont: [...cont],
          /* 원본: 개수는 '매치 + 비블랙리스트' 행에만 표시한다 (조상 문맥 행은 0) */
          count: matchIds.has(child.id) ? child.count : undefined,
          reason: reasonById.has(child.id) ? reasonById.get(child.id)! : null,
          chain: [...chain, node.id],
        });
        walk(child, depth + 1, [...cont, !isLast], [...chain, node.id]);
      });
    };
    /* 루트('전체')는 헤더처럼 항상 최상단 문맥이므로 그 자식부터 depth 0 으로 */
    if (keep.size) walk(treeData, 0, [], []);
    return out;
  }, [matches, blacklistedMatches, searchText, chainOf, treeData]);

  useEffect(() => {
    if (!searchOpen) return;
    searchRef.current?.focus();
    searchRef.current?.select();
  }, [searchOpen]);

  useEffect(() => {
    setMatchIndex(allMatches.length ? 0 : -1);
  }, [searchText, allMatches.length]);

  /* 원본 _move_search_match + _select_search_match (library_tabs.py:1319) —
     ↑/↓ 순차찾기는 **선택하지 않는다** (Ctrl+F 식 찾기: 위치만 드러내고 검색어를
     빨강으로 강조한다. 실제 검색 범위 변경은 사용자가 직접 클릭할 때만).
     순차찾기로 들어가면 드롭다운은 닫는다 (_hide_dropdown). */
  const revealMatch = (step: number) => {
    if (!allMatches.length) return;
    setDropdownOpen(false);
    const total = allMatches.length;
    const next = matchIndex < 0 ? 0 : (matchIndex + step + total) % total;
    const match = allMatches[next];
    setMatchIndex(next);
    setOpen((value) => new Set([...value, ...(chainOf[match.id] ?? [])]));
    /* 원본 reveal_folder: 찾은 항목을 **화면 가운데**로 스크롤하고 가로 스크롤은 0 으로
       되돌린다 (PositionAtCenter + horizontalScrollBar().setValue(0)). */
    setCenterOnId(match.id);
  };



  /* 원본 drawRow: 선택 행과 **조상 행** 모두 좌측 3px #4E90E8 바를 받는다. */
  const selectedChain = useMemo(() => {
    const chain = new Set<string>();
    const walk = (node: TreeNode, path: string[]): boolean => {
      if (node.id === sel) { path.forEach((id) => chain.add(id)); return true; }
      return (node.children ?? []).some((c) => walk(c, [...path, node.id]));
    };
    walk(treeData, []);
    return chain;
  }, [sel, treeData]);

  /* 원본 _token_prefix_ranges: 검색어가 토큰(구분자로 쪼갠 단위) **시작에서 접두로**
     맞는 구간만 강조한다. 단어 중간 매칭은 제외. 색은 #cf7676 + bold. */
  const highlightLabel = (label: string, query: string) => {
    const qs = query.trim().toLowerCase().split(WORD_SPLIT).filter(Boolean);
    if (!qs.length) return label;
    const low = label.toLowerCase();
    const out: React.ReactNode[] = [];
    let i = 0;
    let qi = 0;
    let last = 0;
    while (i < low.length && qi < qs.length) {
      /* 토큰 시작 판정도 같은 기준이어야 한다 — ASCII 전용 \W 를 쓰면 한글
         바로 뒤를 "토큰 시작"으로 오판해 강조 위치가 어긋난다. */
      const isStart = i === 0 || !/[\p{L}\p{N}]/u.test(low[i - 1]);
      if (isStart && low.startsWith(qs[qi], i)) {
        const len = qs[qi].length;
        if (i > last) out.push(label.slice(last, i));
        out.push(<b className="tree-hit" key={`${i}`}>{label.slice(i, i + len)}</b>);
        i += len;
        last = i;
        qi += 1;
      } else {
        i += 1;
      }
    }
    if (last < label.length) out.push(label.slice(last));
    return out;
  };

  /* 화면에 실제로 보이는 행(펼침·블랙리스트·탭 제외 반영)을 평평한 배열로.
     상시 헤더(_StickyAncestorHeader) 계산과 본문 렌더가 같은 목록을 쓴다. */
  type FlatRow = { node: TreeNode; depth: number };
  const visibleRows = useMemo(() => {
    const out: FlatRow[] = [];
    const blacklistSet = new Set(blacklist.map((bp) => normPath(bp.path)));
    const hidden = (node: TreeNode) =>
      /* 원본은 블랙리스트 경로 노드를 setHidden(True) 로 감춘다 (표시 변형 아님).
         제거하면 원래 위치로 그대로 복원되도록 노드 자체는 트리에 남긴다. */
      Boolean(node.path && blacklistSet.has(normPath(node.path)))
      /* 탭 excluded — '이 탭에서 삭제'된 하위는 그 탭에서만 감춘다 */
      || activeExcluded.some((e) => isUnder(node.id, e));
    const walk = (node: TreeNode, depth: number) => {
      if (hidden(node)) return;
      out.push({ node, depth });
      if (node.children?.length && open.has(node.id))
        node.children.forEach((child) => walk(child, depth + 1));
    };
    /* 드래그 중이면 미리보기 순서로 루트를 재배열해 보여준다 (원본은 트리 항목을
       실제로 takeChild/insertChild 한다) */
    const ordered = previewRootIds && treeData.children
      ? { ...treeData, children: previewRootIds
            .map((id) => treeData.children!.find((c) => c.id === id))
            .filter(Boolean) as TreeNode[] }
      : treeData;
    if (tab === "all") walk(ordered, 0);
    else {
      activeRoots.forEach((id) => { const n = nodeById[id]; if (n) walk(n, 0); });
      /* 담긴 항목이 있을 때만 '전체' 행을 맨 위에 둔다 (위 TAB_ALL_ID 주석) */
      if (out.length) {
        const total = activeRoots.reduce(
          (sum, id) => sum + (nodeById[id]?.count ?? 0), 0);
        out.unshift({
          node: { id: TAB_ALL_ID, label: "전체", path: "", count: total, children: [] },
          depth: 0,
        });
      }
    }
    return out;
  }, [tab, open, blacklist, activeExcluded, activeRoots, nodeById, chainOf, treeData,
      previewRootIds]);

  /* ── 상시 헤더 (원본 folder_tree._StickyAncestorHeader, 236) ──
     '전체'는 있으면 항상 맨 위 고정. 선택 항목의 조상은 자기 슬롯
     (= 이미 고정된 행 수 × 행높이)에 닿는 순간 고정되고, 선택 항목 자신도
     자기 슬롯에 닿으면 맨 아래 고정된다. 클릭하면 그 폴더를 선택(브레드크럼),
     화살표를 누르면 접기/펼치기, 우클릭은 본문과 같은 메뉴. */
  const stickyRows = useMemo(() => {
    if (!visibleRows.length) return [] as FlatRow[];
    const out: FlatRow[] = [];
    const topOf = (index: number) => TREE_PAD_TOP + index * ROW_H - treeScroll;
    if (tab === "all" && visibleRows[0]?.node.id === "all") out.push(visibleRows[0]);
    if (sel && sel !== "all") {
      for (const anc of (chainOf[sel] ?? [])) {
        if (anc === "all") continue;
        const index = visibleRows.findIndex((row) => row.node.id === anc);
        if (index < 0) break;
        if (topOf(index) < out.length * ROW_H) out.push(visibleRows[index]);
        else break;
      }
      const index = visibleRows.findIndex((row) => row.node.id === sel);
      if (index >= 0 && topOf(index) < out.length * ROW_H) out.push(visibleRows[index]);
    }
    return out;
  }, [visibleRows, sel, treeScroll, tab, chainOf]);

  useLayoutEffect(() => {
    const box = treeBoxRef.current;
    if (!box) return;
    const measure = () => {
      const w = box.offsetWidth - box.clientWidth;
      setTreeScrollbarW((prev) => (prev === w ? prev : w));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(box);
    return () => ro.disconnect();
  }, [visibleRows.length]);

  /* 펼침이 반영된 뒤에 실제 행 위치를 알 수 있으므로 다음 렌더에서 스크롤한다 */
  useEffect(() => {
    if (!centerOnId) return;
    const index = visibleRows.findIndex((row) => row.node.id === centerOnId);
    setCenterOnId(null);
    const box = treeBoxRef.current;
    if (index < 0 || !box) return;
    const top = TREE_PAD_TOP + index * ROW_H;
    box.scrollTop = Math.max(0, top - Math.round(box.clientHeight / 2) + ROW_H / 2);
    box.scrollLeft = 0;
  }, [centerOnId, visibleRows]);

  const renderRow = ({ node, depth }: FlatRow, sticky = false): React.ReactNode => {
    const has = !!node.children?.length;
    const isOpen = open.has(node.id);
    /* ⚠ `searchOpen` 조건이 필요하다. 돋보기 버튼으로 검색창을 닫아도 검색어는
       남아 있어서, 예전에는 닫은 뒤에도 파일 브라우저에 강조가 계속 떠 있었다
       (사용자 신고 2026-09-03). 검색창이 보일 때만 현재 항목을 표시한다. */
    const currentSearchHit = !sticky && searchOpen && Boolean(searchText.trim())
      && allMatches[matchIndex]?.id === node.id;
    const isAncestor = selectedChain.has(node.id);
    /* 펼쳐진 자식(그 아래 재귀)에 미완료가 있으면 이 행에서는 감춘다 */
    const descendantIncomplete = (() => {
      if (!isOpen || !node.children?.length) return false;
      const walk = (n: TreeNode): boolean => {
        if ((n.incomplete ?? 0) > 0) return true;
        if (!open.has(n.id)) return false;
        return (n.children ?? []).some(walk);
      };
      return (node.children ?? []).some(walk);
    })();
    const showIncomplete = (node.incomplete ?? 0) > 0 && !descendantIncomplete;
    return (
      <div
        key={(sticky ? "s:" : "") + node.id}
        ref={(el) => {
          if (sticky) return;
          if (el) rowElRef.current.set(node.id, el);
          else rowElRef.current.delete(node.id);
        }}
        className={"tree-row" + (multiSel.has(node.id) ? " selected" : "")
          + (isAncestor ? " ancestor" : "") + (depth > 1 ? " dim" : "")
          + (currentSearchHit ? " search-hit-row" : "") + (sticky ? " sticky" : "")
          /* 카운트 폰트 정책: '전체' · 그룹 행 · 등록된 라이브러리 (사용자 지시) */
          + (node.id === "all" || depth <= 1 || isLibraryRoot(node) ? " is-root" : "")
          /* 원본 drawRow 의 dragging 표시 (folder_tree.py:2191) */
          + (!sticky && previewRootIds && rootDragRef.current?.id === node.id
              ? " root-dragging" : "")}
        /* 들여쓰기 — 원본 setIndentation(18) 의 절반(9). 사용자 지시(2026-09-02):
           들여쓰기가 깊어 라이브러리 이름 표시 공간이 부족했다. */
        style={{ paddingLeft: 6 + depth * TREE_INDENT }}
        /* 원본 setDragEnabled(True) — 탭바 위에 놓으면 그 탭에 추가된다 */
        draggable={!sticky && node.id !== "all" && node.id !== TAB_ALL_ID}
        onDragStart={(event) => {
          const ids = dragPayload(node.id);
          if (!ids.length) { event.preventDefault(); return; }
          event.dataTransfer.setData("application/x-soundfield-paths", JSON.stringify(ids));
          event.dataTransfer.effectAllowed = "copyMove";
          /* 원본 startDrag: 재정렬 가능한 루트를 단독 선택해 끌면 순서 바꾸기 드래그가 된다.
             (mimeData 는 그대로 실려 있어 탭바에 놓는 것도 계속 된다) */
          if (isReorderableRoot(node, depth) && multiSel.size <= 1) {
            rootDragRef.current = { id: node.id, base: rootIdOrder() };
          } else {
            rootDragRef.current = null;
          }
        }}
        onDragEnd={() => {
          /* 커밋 없이 끝났으면 스냅샷 복원 (원본 startDrag 의 finally) */
          if (rootDragRef.current) {
            restoreRootDrag();
            rootDragRef.current = null;
          }
        }}
        onClick={(event) => {
          setSel(node.id);
          setMultiSel((prev) => {
            if (event.ctrlKey || event.metaKey) {
              const next = new Set(prev);
              if (next.has(node.id)) next.delete(node.id);
              else next.add(node.id);
              return next.size ? next : new Set([node.id]);
            }
            if (event.shiftKey) {
              const ids = visibleRows.map((row) => row.node.id);
              const a = ids.indexOf(sel);
              const b = ids.indexOf(node.id);
              if (a >= 0 && b >= 0) {
                const [lo, hi] = a < b ? [a, b] : [b, a];
                return new Set(ids.slice(lo, hi + 1));
              }
            }
            return new Set([node.id]);
          });
        }}
        onDoubleClick={() => has && toggle(node.id)}
        /* 원본 setToolTip(0, 절대경로) — 창이 좁아도 전체 경로를 확인할 수 있게 */
        data-tip={node.path || undefined}
        /* 원본 folder_tree 의 밀집 목록 정책(650ms 정착 지연) — TooltipLayer 참고 */
        data-tip-dense=""
        onContextMenu={(e) => {
          /* 합성 '전체' 행은 실제 폴더가 아니다 — 이름변경·블랙리스트·즐겨찾기
             같은 항목이 뜨면 엉뚱한 대상에 적용된다. 메뉴 자체를 막는다. */
          if (node.id === TAB_ALL_ID) { e.preventDefault(); return; }
          return onContext(e, node.label, {
          /* 원본 대화상자는 라벨이 아니라 실제 경로를 보여준다 */
          path: node.path,
          /* 원본 _can_move_root — 전체 탭의 루트만, 형제 안에서 위/아래 여유가 있을 때 */
          canMoveUp: depth <= 1 && rootIndexOf(node.id) > 0,
          canMoveDown: depth <= 1
            && rootIndexOf(node.id) >= 0
            && rootIndexOf(node.id) < (treeData.children?.length ?? 0) - 1,
          paths: multiSel.has(node.id)
            ? [...multiSel].map((id) => nodeById[id]?.path).filter(Boolean) as string[]
            : (node.path ? [node.path] : []),
          isRoot: isLibraryRoot(node),
          hasChildren: has,
          tab,
          /* 원본 _favorites_set 기준 토글 판정 */
          isFavorite: favorites.includes(node.id),
          actions: {
            /* 원본 _collapse_all — 우클릭한 폴더 자신과 그 아래 전부 접는다 */
            collapseAll: () => {
              const ids: string[] = [];
              const walk = (n: TreeNode) => {
                ids.push(n.id);
                n.children?.forEach(walk);
              };
              walk(node);
              setOpen((prev) => {
                const next = new Set(prev);
                ids.forEach((id) => next.delete(id));
                return next;
              });
            },
            /* 원본 favoriteAdded / favoriteRemoved.
               ⚠ 알림을 빼먹지 말 것. 예전에는 상태만 바꾸고 아무 표시가 없어서
                 눌렀는지 안 눌렀는지 알 수 없었다 (사용자 신고 2026-09-14:
                 "동작했다는 피드백이 너무 약하다"). 드래그로 넣는 경로
                 (emitDropFeedback)에는 원래 있었는데 우클릭 경로에만 없었다.
                 중앙 토스트 + ★즐겨찾기 탭 깜빡임 두 가지로 알린다. */
            favoriteToggle: (add: boolean) => {
              const already = favorites.includes(node.id);
              setFavorites((prev) => (add
                ? (prev.includes(node.id) ? prev : [...prev, node.id])
                : prev.filter((id) => id !== node.id)));
              if (add) {
                onNotify?.(already ? "info" : "add",
                           already ? "이미 '★ 즐겨찾기' 에 있습니다"
                                   : `'${node.label}' 을(를) '★ 즐겨찾기' 에 추가`);
                flash("fav");
              } else {
                onNotify?.("info", `'${node.label}' 을(를) '★ 즐겨찾기' 에서 제거`);
              }
            },
            /* 원본 removeFromTabRequested — 그 탭에서만 가린다 (DB 는 손대지 않는다).
               대상은 우클릭한 항목이 선택 안에 있으면 선택 전체다. */
            removeFromTab: () => {
              const targets = multiSel.has(node.id) ? [...multiSel] : [node.id];
              if (tab === "fav") {
                setFavorites((prev) => prev.filter((id) => !targets.includes(id)));
                return;
              }
              if (!tab.startsWith("user:")) return;
              setTabExcluded((prev) => ({
                ...prev,
                [tab]: [...new Set([...(prev[tab] ?? []), ...targets])],
              }));
            },
          },
          /* 원본: 우클릭한 항목이 선택 안에 있으면 선택 전체가 대상 */
          count: multiSel.has(node.id) ? multiSel.size : 1,
        });
        }}
      >
        {has ? (
          <span
            className={"tree-caret" + (isOpen ? " open" : "")}
            onClick={(e) => { e.stopPropagation(); toggle(node.id); }}
          >
            <IcoCaret size={11} />
          </span>
        ) : (
          <span className="tree-caret" />
        )}
        <span className="tree-label">
          {(() => {
            /* 원본 ROLE_DEFAULT_NAME/display_names: 사용자가 이름을 바꾸면 라벨만 바뀐다 */
            const key = node.path
              ? normPath(node.path).replace(/[\\/]+$/, "").toLowerCase() : "";
            const shown = (key && displayNames?.[key] !== undefined)
              ? displayNames[key] : node.label;
            return currentSearchHit ? highlightLabel(shown, searchText) : shown;
          })()}
        </span>
        {/* 원본 folder_tree 는 개수 컬럼(1)을 setColumnHidden 으로 숨기고 개수를
            _CountOverlay 가 그린다. 고정 헤더(_StickyAncestorHeader)만 자기 개수를
            직접 그리므로, 여기서도 sticky 행에서만 그린다. */}
        {sticky && node.count !== undefined && (
          /* 원본 _refresh_incomplete_item (folder_tree.py:1530): 펼쳐진 자식 쪽에
             미완료가 보이면 이 행의 미완료 강조는 감춘다 — 같은 미완료를 상위·하위
             양쪽에서 주황으로 두 번 알리지 않기 위한 정책이다. */
          <span className={"tree-count" + (showIncomplete ? " incomplete" : "")}
                /* 원본 툴팁 전문 (folder_tree.py:876) */
                data-tip={showIncomplete
                  ? `미완료(대기/실패) ${node.incomplete?.toLocaleString()}개 포함 — 라이브러리 현황 다이얼로그에서 재시도/제거`
                  : ""}>
            {node.count.toLocaleString()}
          </span>
        )}
      </div>
    );
  };

  return (
    <>
      {newTabName !== null && (
        <div className="scrim" onMouseDown={(event) => {
          if (event.target === event.currentTarget) { setNewTabName(null); setTabError(""); }
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">새 탭</span></div>
            <div className="modal-body">
              <div className="form-row">
                <span className="form-label" style={{ flex: "0 0 70px" }}>탭 이름:</span>
                <input className="input" autoFocus value={newTabName}
                       onChange={(event) => { setNewTabName(event.target.value); setTabError(""); }}
                       onKeyDown={(event) => {
                         if (event.key === "Enter") { event.preventDefault(); commitNewTab(); }
                         if (event.key === "Escape") { event.preventDefault(); setNewTabName(null); setTabError(""); }
                       }} />
              </div>
              {tabError && <div className="dialog-error">{tabError}</div>}
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" onClick={commitNewTab}>확인</button>
              <button className="btn" onClick={() => { setNewTabName(null); setTabError(""); }}>취소</button>
            </div>
          </div>
        </div>
      )}

      {/* 원본 _prompt_rename_tab — 제목 "이름 변경", 라벨 "새 이름:", 기본값 현재 이름.
             같은 이름이 이미 있으면 "중복" / "같은 이름의 탭이 이미 있습니다." */}
      {renameTab !== null && (
        <div className="scrim" onMouseDown={(event) => {
          if (event.target === event.currentTarget) { setRenameTab(null); setTabError(""); }
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">이름 변경</span></div>
            <div className="modal-body">
              <div className="form-row">
                <span className="form-label" style={{ flex: "0 0 70px" }}>새 이름:</span>
                <input className="input" autoFocus value={renameTab.value}
                       onChange={(event) => {
                         setRenameTab({ from: renameTab.from, value: event.target.value });
                         setTabError("");
                       }}
                       onKeyDown={(event) => {
                         if (event.key === "Enter") { event.preventDefault(); commitRenameTab(); }
                         if (event.key === "Escape") {
                           event.preventDefault();
                           setRenameTab(null);
                           setTabError("");
                         }
                       }} />
              </div>
              {tabError && <div className="dialog-error">{tabError}</div>}
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" onClick={commitRenameTab}>확인</button>
              <button className="btn"
                      onClick={() => { setRenameTab(null); setTabError(""); }}>취소</button>
            </div>
          </div>
        </div>
      )}

      {confirmRemoveTab !== null && (
        <div className="scrim" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setConfirmRemoveTab(null);
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">탭 삭제</span></div>
            <div className="modal-body">
              <div className="confirm-text">
                '{confirmRemoveTab}' 탭을 삭제할까요?<br />
                (라이브러리 자체는 그대로 유지됩니다)
              </div>
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" autoFocus
                      onClick={() => { removeUserTab(confirmRemoveTab); setConfirmRemoveTab(null); }}>
                예
              </button>
              <button className="btn" onClick={() => setConfirmRemoveTab(null)}>아니오</button>
            </div>
          </div>
        </div>
      )}

      {confirmRemoveBl !== null && (
        <div className="scrim" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setConfirmRemoveBl(null);
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">블랙리스트 제거</span></div>
            <div className="modal-body">
              <div className="confirm-text pre">{`블랙리스트에서 제거할까요?\n${confirmRemoveBl}`}</div>
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" autoFocus
                      onClick={() => {
                        /* 원본 _on_remove: DB 에서 제거 후 refresh + changed 시그널.
                           PoC 는 오버레이 DB 에 "가림" 으로 기록한다. */
                        const path = confirmRemoveBl!;
                        void removeBlacklistPath(path).then((ok) => {
                          if (!ok) { onNotify("remove", "블랙리스트 제거 실패"); return; }
                          setBlacklist((rows) => rows.filter((row) => row.path !== path));
                          onNotify("remove", "블랙리스트에서 제거");
                          onBlacklistChanged?.();
                        });
                        setConfirmRemoveBl(null);
                      }}>
                예
              </button>
              <button className="btn" onClick={() => setConfirmRemoveBl(null)}>아니오</button>
            </div>
          </div>
        </div>
      )}

      <div className="sidebar-tree-area">
        <div className="sidebar-head">
          <span className="sidebar-title">{t("파일 브라우저")}</span>
          {/* 원본 side_count: "경로 폴더 N" — N 은 **등록된 라이브러리 루트 수**다
              (드라이브 그룹 노드 수가 아니다, main_window.py:5929). */}
          <span className="sidebar-meta">{t("경로 폴더")} {rootCount ?? 0}</span>
        </div>

        <div className="tabbar">
          {/* 코너 버튼은 float 로 우상단에 두고 첫 줄만 그만큼 비운다 (원본 set_corner_widget) */}
          <div className="tab-corner">
            <button className="tab-add" data-tip="새 탭" aria-label="새 탭"
                    onClick={() => { setNewTabName(""); setTabError(""); }}>
              <IcoPlus size={13} />
            </button>
            <button className={"tab-add search-tab" + (searchOpen ? " active" : "")}
                    data-tip="라이브러리 검색" aria-label="라이브러리 검색"
                    onClick={() => setSearchOpen((value) => !value)}>
              <IcoSearch size={13} />
            </button>
          </div>

          {/* 전체 탭은 드롭을 받지 않는다 (원본 1052: 자동 구성) */}
          <button className={"tab" + (tab === "all" ? " active" : "")} onClick={() => setTab("all")}>전체</button>
          <button className={"tab" + (tab === "fav" ? " active" : "")
                      + (flashTab === "fav" ? " tab-flash" : "") + (dragOverTab === "fav" ? " drop-target" : "")}
                  onClick={() => setTab("fav")}
                  onDragOver={(event) => {
                    if (!event.dataTransfer.types.includes("application/x-soundfield-paths")) return;
                    event.preventDefault();
                    setDragOverTab("fav");
                  }}
                  onDragLeave={() => setDragOverTab(null)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragOverTab(null);
                    const raw = event.dataTransfer.getData("application/x-soundfield-paths");
                    if (raw) dropOnTab("fav", JSON.parse(raw) as string[]);
                  }}>
            <IcoStar size={11} /> 즐겨찾기
          </button>
          {userTabs.map((name) => (
            <button key={name}
                    ref={(el) => {
                      if (el) tabElRef.current.set(name, el);
                      else tabElRef.current.delete(name);
                    }}
                    className={"tab user-tab" + (tab === `user:${name}` ? " active" : "")
                      + (flashTab === `user:${name}` ? " tab-flash" : "")
                      + (dragOverTab === `user:${name}` ? " drop-target" : "")
                      + (draggingTab === name ? " tab-dragging" : "")}
                    data-tip={name}
                    onMouseDown={(event) => beginTabDrag(event, name)}
                    onClick={() => {
                      if (tabDragSuppressRef.current) { tabDragSuppressRef.current = false; return; }
                      setTab(`user:${name}`);
                    }}
                    onDragOver={(event) => {
                      if (!event.dataTransfer.types.includes("application/x-soundfield-paths")) return;
                      event.preventDefault();
                      setDragOverTab(`user:${name}`);
                    }}
                    onDragLeave={() => setDragOverTab(null)}
                    onDrop={(event) => {
                      event.preventDefault();
                      setDragOverTab(null);
                      const raw = event.dataTransfer.getData("application/x-soundfield-paths");
                      if (raw) dropOnTab(`user:${name}`, JSON.parse(raw) as string[]);
                    }}
                    /* 원본 _on_tab_context — 사용자 탭에서만 메뉴가 뜨고
                       [이름 변경] / [탭 삭제] 두 항목뿐이다. */
                    onContextMenu={(event) => onContext(event, name, {
                      kind: "tab",
                      actions: {
                        rename: () => { setRenameTab({ from: name, value: name }); setTabError(""); },
                        remove: () => setConfirmRemoveTab(name),
                      },
                    })}>
              <span className="user-tab-name">{name}</span>
              <span className="tab-close" role="button" aria-label={`${name} 탭 삭제`}
                    data-tip="탭 삭제"
                    onClick={(event) => { event.stopPropagation(); setConfirmRemoveTab(name); }}>
                <IcoX size={9} />
              </span>
            </button>
          ))}
        </div>

        {searchOpen && (
          <>
            <div className="library-search-row" ref={searchRowRef}>
              {/* 지우기 X 는 **입력창 기준**으로 중앙 정렬해야 한다.
                  검색행에는 위쪽 5px 여백이 있어 행 기준 50% 로 잡으면 2.5px 떠 보인다. */}
              <div className="library-search-field">
              <input
                ref={searchRef}
                className="input library-search-input"
                placeholder="폴더 이름으로 라이브러리 검색"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                /* 원본: 검색창을 다시 만지면 결과 목록이 다시 내려온다 */
                onMouseDown={() => setDropdownOpen(true)}
                onFocus={() => setDropdownOpen(true)}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" || event.key === "Enter") {
                    event.preventDefault();
                    revealMatch(1);
                  } else if (event.key === "ArrowUp") {
                    event.preventDefault();
                    revealMatch(-1);
                  } else if (event.key === "Escape") {
                    setSearchOpen(false);
                  }
                }}
              />
              {/* 원본 setClearButtonEnabled(True) — 입력이 있을 때만 나타나는 X.
                  누르면 검색어만 지우고 검색창은 열린 채로 둔다. */}
              {searchText && (
                <button className="library-search-clear" aria-label="검색어 지우기"
                        onClick={() => { setSearchText(""); searchRef.current?.focus(); }}>
                  <IcoX size={9} />
                </button>
              )}
              </div>
              <button className={"search-dropdown" + (dropdownOpen ? " open" : "")}
                      /* 원본 툴팁은 상태와 무관하게 "검색 결과 목록" 하나다 */
                      data-tip="검색 결과 목록"
                      aria-label="검색 결과 목록"
                      onClick={() => setDropdownOpen((value) => !value)}>
                <IcoCaret size={12} />
              </button>
            </div>
            <div className="library-search-nav" ref={navRowRef}>
              <span className="search-count">{allMatches.length ? matchIndex + 1 : 0}/{allMatches.length}</span>
              {/* 원본 _update_search_controls: '하나씩 찾기' 안내는 결과가 있을 때만 보인다 */}
              {allMatches.length > 0 && <span className="search-seq">하나씩 찾기</span>}
              <button className="search-nav-btn" data-tip="이전 검색 결과" disabled={!allMatches.length} onClick={() => revealMatch(-1)}>↑</button>
              <button className="search-nav-btn" data-tip="다음 검색 결과" disabled={!allMatches.length} onClick={() => revealMatch(1)}>↓</button>
            </div>
            {dropdownOpen && searchText.trim() && dropAnchor && (
              <div className="library-search-dropdown" ref={dropElRef}
                   style={{
                     left: dropAnchor.left,
                     top: dropAnchor.top,
                     minWidth: dropAnchor.minW,
                     /* 원본 safe_right = 화면 오른쪽 - 8px */
                     maxWidth: `calc(100vw - ${dropAnchor.left}px - 8px)`,
                   }}>
                {hitRows.length ? (() => {
                  const shown = hitRows.slice(0, DROPDOWN_MAX_ROWS);
                  /* ── 안내선 계산 (사용자 신고 2026-09-03: "계층이 깊어지면 선이 끊어져버려") ──
                     예전에는 walk 가 **전체 트리**에서 만든 `cont`(형제가 남았는가)를 썼다.
                     그런데 드롭다운은 일치 항목 + 조상 문맥만 보여주므로,
                     자식이 하나뿐인 체인(예: JP → HIT_JP → ANIKA → Sound)에서는
                     모든 조상이 '막내'라 통과 세로선이 **하나도 그려지지 않았다.**
                     결과가 꺾쇠만 남은 계단 모양 → 한 묶음으로 안 보였다.

                     그래서 **화면에 실제로 보이는 목록만** 보고 판단한다.
                       · through[L-1] : 조상 레벨 L 뒤에 같은 레벨 행이 더 있나 (통과 세로선)
                       · last         : 자기 레벨에서 마지막인가 (꺾쇠를 중간까지만)
                       · hasChild     : 바로 아래 행이 내 자식인가 → **부모→자식 연결선**
                     hasChild 가 핵심이다. 이걸 그려야 체인이 끊기지 않고 이어진다. */
                  const guides = shown.map((hit, i) => {
                    const d = hit.depth;
                    const through: boolean[] = [];
                    for (let level = 1; level <= d - 1; level++) {
                      let cont = false;
                      for (let j = i + 1; j < shown.length; j++) {
                        if (shown[j].depth <= level) { cont = shown[j].depth === level; break; }
                      }
                      through.push(cont);
                    }
                    let last = true;
                    for (let j = i + 1; j < shown.length; j++) {
                      if (shown[j].depth <= d) { last = shown[j].depth < d; break; }
                    }
                    return { through, last, hasChild: i + 1 < shown.length && shown[i + 1].depth > d };
                  });
                  return shown.map((hit, hitIndex) => {
                  const guide = guides[hitIndex];
                  const isCurrent = allMatches[matchIndex]?.id === hit.id;
                  return (
                    <button
                      key={hit.id}
                      className={"library-search-hit"
                        + (isCurrent ? " current" : "")
                        + (hit.reason !== null ? " blacklisted"
                            : hit.isMatch ? "" : " context")}
                      disabled={!hit.isMatch || hit.reason !== null}
                      /* ⚠ 오프셋 7 → 14. 부모→자식 연결선을 gx(depth)=4+14*depth+7 에
                         그리는데, 라벨이 +7 이면 **정확히 같은 x** 라 선이 글자를 뚫고
                         지나갔다 (사용자 신고 2026-09-03, 스크린샷). +14 로 밀어
                         연결선과 글자 사이에 7px 여백을 둔다. */
                      style={{ paddingLeft: 4 + hit.depth * 14 + (hit.depth > 0 ? 14 : 0) }}
                      onClick={() => {
                        /* 원본 _dropdown_pick: 클릭은 **명시 선택**이다 (select=True →
                           folderSelected 까지 나가 검색 범위가 바뀐다). 클릭하면
                           드롭다운은 닫는다. 조상(문맥) 행은 클릭이 무시되고 목록은
                           그대로 열려 있다 (위 disabled 처리). */
                        const at = allMatches.findIndex((m) => m.id === hit.id);
                        if (at >= 0) setMatchIndex(at);
                        setOpen((value) => new Set([...value, ...(chainOf[hit.id] ?? [])]));
                        setSel(hit.id);
                        setMultiSel(new Set([hit.id]));
                        setDropdownOpen(false);
                      }}
                    >
                      {/* 연결선 — 원본 _paint_guides (library_tabs.py:408) 좌표 그대로.
                          ind=14, base_x=4, cx = base_x + k*ind + ind/2

                          ⚠ 인덱스가 한 칸 밀려 있었다. `cont` 는 walk 에서
                          `[...cont, !isLast]` 로 쌓이므로, **depth d 행의 cont[k] 가
                          곧 k 단계 조상이 아직 형제를 남겼는지**를 뜻한다.
                          그런데 `cont[k+1]` 을 봤기 때문에 0번 칸의 세로선을 부모의
                          형제 유무로 판단했다 → 선이 엉뚱한 행에 생기고 중간중간
                          끊겨 보였다 (사용자 신고). 통과 칸은 k = 0..depth-2,
                          조건은 `cont[k]` 가 맞다. elbow 칸(depth-1)은 아래에서
                          따로 그리므로 겹치지 않는다.

                          · 통과 세로선: k = 0..depth-2 중 cont[k] 인 칸
                          · 자기 elbow: 세로 top→mid, 막내가 아니면 mid→bottom
                          · 가로: cxe → base_x + depth*ind (즉 7px)
                          정수 좌표 rect + crispEdges 로 1px 또렷하게 그린다. */}
                      {(hit.depth > 0 || guide.hasChild) && (() => {
                        const gx = (k: number) => 4 + k * 14 + 7;
                        const elbowX = gx(hit.depth - 1);
                        const childX = gx(hit.depth);
                        const mid = Math.floor(HIT_ROW_H / 2);
                        const width = Math.max(hit.depth > 0 ? elbowX + 8 : 0,
                                               guide.hasChild ? childX + 1 : 0);
                        return (
                          <svg className="hit-guides" aria-hidden="true"
                               width={width} height={HIT_ROW_H}
                               shapeRendering="crispEdges">
                            {guide.through.map((on, k) => (
                              on ? (
                                <rect key={k} x={gx(k)} y={0} width={1} height={HIT_ROW_H}
                                      fill="currentColor" />
                              ) : null
                            ))}
                            {hit.depth > 0 && (
                              <rect x={elbowX} y={0} width={1}
                                    height={guide.last ? mid : HIT_ROW_H} fill="currentColor" />
                            )}
                            {hit.depth > 0 && (
                              <rect x={elbowX} y={mid} width={7} height={1} fill="currentColor" />
                            )}
                            {/* 부모→자식 연결선: 내 중간에서 아래 행의 꺾쇠 기둥까지 내린다.
                                이게 없으면 자식이 하나뿐인 체인이 계단처럼 끊겨 보인다. */}
                            {guide.hasChild && (
                              <rect x={childX} y={mid} width={1} height={HIT_ROW_H - mid}
                                    fill="currentColor" />
                            )}
                          </svg>
                        );
                      })()}
                      <span className="hit-label">
                        {hit.reason !== null
                          /* 원본 델리게이트: 빗금 + 전각 공백 + "블랙리스트 · 사유" */
                          ? (<>
                              <s>{hit.label}</s>
                              <span className="hit-bl">
                                {"　블랙리스트" + (hit.reason.trim() ? ` · ${hit.reason.trim()}` : "")}
                              </span>
                            </>)
                          : hit.isMatch ? highlightLabel(hit.label, searchText) : hit.label}
                      </span>
                      {hit.count !== undefined && (
                        <span className="hit-count">{hit.count.toLocaleString()}</span>
                      )}
                    </button>
                  );
                  });
                })() : (
                  <div className="library-search-empty">검색 결과 없음</div>
                )}
                {hiddenMatchCount > 0 && (
                  /* 잘린 나머지를 숨기지 않고 드러낸다 — 원본은 상한이 없으므로
                     최소한 "몇 개가 더 있는지"는 보여야 한다 */
                  <div className="library-search-more">
                    … 외 {hiddenMatchCount.toLocaleString()}개 (검색어를 더 입력하면 좁혀집니다)
                  </div>
                )}
              </div>
            )}
          </>
        )}

        <div className="tree-wrap">
          {/* tabIndex — 키보드로 트리를 조작할 수 있게 포커스를 받는다 (Qt 위젯과 동일) */}
          <div className="tree" ref={treeBoxRef} tabIndex={0} onKeyDown={onTreeKeyDown}
               /* 원본 folder_tree.wheelEvent: Shift+휠 = 가로 스크롤 (틱당 50px) */
               onWheel={(event) => {
                 if (!event.shiftKey) return;
                 event.preventDefault();
                 event.currentTarget.scrollLeft += Math.sign(event.deltaY) * 50;
               }}
               onScroll={(event) => setTreeScroll(event.currentTarget.scrollTop)}
               /* 원본 dragMoveEvent — 재정렬 드래그면 삽입 위치를 계산해 미리보기 */
               onDragOver={(event) => {
                 if (!rootDragRef.current) return;
                 event.preventDefault();
                 event.dataTransfer.dropEffect = "move";
                 previewRootDragAt(rootInsertIndexAt(
                   event.clientY, previewRootIds ?? rootDragRef.current.base));
               }}
               /* 원본 dragLeaveEvent — 트리 밖으로 나가면 스냅샷 복원 + 드래그 취소 */
               onDragLeave={(event) => {
                 if (!rootDragRef.current) return;
                 const box = event.currentTarget.getBoundingClientRect();
                 if (event.clientX >= box.left && event.clientX <= box.right
                     && event.clientY >= box.top && event.clientY <= box.bottom) return;
                 restoreRootDrag();
               }}
               /* 원본 dropEvent — 전체 루트 순서를 올린다 */
               onDrop={(event) => {
                 const drag = rootDragRef.current;
                 if (!drag) return;
                 event.preventDefault();
                 const order = previewRootIds ?? drag.base;
                 rootDragRef.current = null;
                 setPreviewRootIds(null);
                 const paths = order
                   .map((id) => nodeById[id]?.path)
                   .filter(Boolean) as string[];
                 if (paths.length) onReorderRoots?.(paths);
               }}>
            {visibleRows.map((row) => renderRow(row))}
          </div>
          {/* ── 개수 오버레이 (원본 _CountOverlay, folder_tree.py:126) ─────────────
              원본은 트리(스크롤 영역)의 **자식 위젯**이라 가로 스크롤에 끌려가지 않고
              뷰포트 우측에 폭 50 으로 고정된다. 자기 배경(bg_panel)을 칠하고 좌측에
              1px 구분선을 그은 뒤, 보이는 행의 개수를 우측 정렬(우측 여백 6)로 그린다.
              마우스는 투과한다(WA_TransparentForMouseEvents).
              선택된 행은 오버레이도 row_selected 로 칠한다 (folder_tree.py:213) —
              이걸 빼면 선택 행에서 개수 칸만 어두워져 '검은 판' 처럼 보인다. */}
          <div className="tree-count-col" aria-hidden="true"
               style={{ right: treeScrollbarW }}>
            {/* 폭을 재던 보이지 않는 자(sizer)는 없앴다 — 행마다 자기 숫자 폭만
                쓰므로 공용 폭이 필요 없다 (사용자 지시 2026-09-08).
                옛 방식은 보이는 숫자 중 가장 긴 것에 칸을 맞춰서, `0` 인 행도
                `1,141,352` 만큼 라벨이 덮였다. */}
            {visibleRows.map((row, index) => {
              const count = row.node.count;
              if (count === undefined) return null;
              const y = TREE_PAD_TOP + index * ROW_H - treeScroll;
              const viewH = treeBoxRef.current?.clientHeight ?? 0;
              if (y + ROW_H < 0 || (viewH && y > viewH)) return null;
              /* 고정 헤더가 덮는 구간은 그리지 않는다 — 고정 행이 자기 개수를 직접
                 그리므로(원본과 동일) 여기서 또 그리면 숫자가 두 겹으로 보인다. */
              if (y < TREE_PAD_TOP + stickyRows.length * ROW_H) return null;
              /* 행 렌더와 같은 규칙 — 펼쳐진 자식에 미완료가 보이면 여기선 감춘다 */
              const descendantIncomplete = (() => {
                if (!open.has(row.node.id) || !row.node.children?.length) return false;
                const walk = (n: TreeNode): boolean => {
                  if ((n.incomplete ?? 0) > 0) return true;
                  if (!open.has(n.id)) return false;
                  return (n.children ?? []).some(walk);
                };
                return (row.node.children ?? []).some(walk);
              })();
              const incomplete = (row.node.incomplete ?? 0) > 0 && !descendantIncomplete;
              return (
                <div key={row.node.id}
                     className={"tree-count-cell"
                       /* 사용자 지시(2026-09-02): 드라이브/공통경로 **그룹 행도 파랑**.
                          원본은 그룹 행을 회색·작게 두지만(ROLE_IS_ROOT False),
                          묶음 숫자도 라이브러리 합계라 같은 급으로 읽는 게 낫다.
                          → '전체' + 깊이 1(그룹·최상위 라이브러리) + 등록된 라이브러리 */
                       + (row.node.id === "all" || row.depth <= 1 || isLibraryRoot(row.node)
                           ? " is-root" : "")
                       + (incomplete ? " incomplete" : "")
                       + (multiSel.has(row.node.id) || sel === row.node.id ? " selected" : "")}
                     style={{ top: y }}>
                  {count.toLocaleString()}
                </div>
              );
            })}
          </div>
          {stickyRows.length > 0 && (
            /* 원본 _StickyAncestorHeader.reposition 은 **viewport** 우측 가장자리에
               맞춘다. 스크롤바 레인까지 덮으면 개수 오버레이(뷰포트 기준)와 어긋난
               10px 틈이 생기고, 그 틈으로 고정 행의 긴 이름이 삐져나온다 (실측:
               tree off/cli=290/280, col=230..280 → 280..290 이 빈 틈). */
            <div className="tree-sticky"
                 style={{ height: stickyRows.length * ROW_H, right: treeScrollbarW }}>
              {stickyRows.map((row) => renderRow(row, true))}
            </div>
          )}
        </div>
      </div>

      {blacklistOpen && (
        <div
          className="blacklist-resizer"
          onMouseDown={(event) => {
            event.preventDefault();
            const y0 = event.clientY;
            const h0 = blacklistH;
            const sidebar = event.currentTarget.parentElement;
            const move = (moveEvent: MouseEvent) => {
              const max = Math.max(80, (sidebar?.clientHeight ?? 400) - 100);
              setBlacklistH(Math.max(80, Math.min(max, h0 - (moveEvent.clientY - y0))));
            };
            const up = () => {
              window.removeEventListener("mousemove", move);
              window.removeEventListener("mouseup", up);
              setBlacklistH((height) => {
                localStorage.setItem(SAVED_BLACKLIST_H, String(Math.round(height)));
                /* 원본은 config "blacklist_expanded_h" 에 저장한다
                   (expandedChanged/splitter 조정 뒤 _save_config) */
                onBlacklistHeightCommit?.(Math.round(height));
                return height;
              });
            };
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
          }}
        />
      )}

      <section
        className={"blacklist-panel" + (blacklistOpen ? " open" : "")}
        style={{ flexBasis: blacklistOpen ? blacklistH : 40 }}
      >
        <div className="blacklist-head">
          <button
            className="blacklist-toggle"
            data-tip="블랙리스트 펼치기/접기"
            onClick={() => setBlacklistOpen((value) => !value)}
          >
            <IcoCaret className={blacklistOpen ? "open" : ""} size={13} />
            <span>{t("블랙리스트")}&nbsp;&nbsp;({blacklist.length})</span>
          </button>
          <button className="blacklist-detail" data-tip="블랙리스트 상세 보기" aria-label="블랙리스트 상세 보기" onClick={onBlacklistDetail}>
            <IcoMore size={15} />
          </button>
        </div>
        {blacklistOpen && (
          <div className="blacklist-body">
            {blacklist.length === 0 ? (
              <div className="blacklist-empty">등록된 블랙리스트가 없습니다.</div>
            ) : blacklist.map(({ path, description }) => (
              <div className="blacklist-row" key={path}>
                {/* 원본 _BlacklistRow: 경로(굵게) + 사용자 설명(작게) 두 줄 */}
                <div className="blacklist-col">
                  <span className="blacklist-path" data-tip={path}>{path}</span>
                  {description && <span className="blacklist-desc">{description}</span>}
                </div>
                {/* 원본 _on_remove (266) 은 확인 대화상자를 띄운다 */}
                <button data-tip="블랙리스트에서 제거" aria-label="블랙리스트에서 제거"
                        onClick={() => setConfirmRemoveBl(path)}>
                  <IcoX size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
