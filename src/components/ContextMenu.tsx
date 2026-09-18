import { useEffect } from "react";
import { IcoBan, IcoCaret, IcoExport, IcoFolder, IcoFolderMinus, IcoRefresh, IcoStar, IcoTrash } from "../icons";
import { revealInExplorer } from "../backend";

/* 원본 folder_tree.py:_build_context_menu (1979) 와 library_tabs.py:_on_tab_context (1001)
   을 그대로 옮긴 것. 항목 순서 · 문구 · 다중 선택 변형 · 위험 항목 색까지 원본 기준이다.

   트리 메뉴 순서
     1) 모두 접기                (자식 있는 항목이 있을 때만) + 구분선
     2) 빠른 재스캔 / 전체 재스캔  (2개 이상이면 "선택 폴더 N개 …")
     3) 탭별                     사용자 탭: 이 탭에서 삭제 / 즐겨찾기 탭: ★ 즐겨찾기에서 제거
     4) root 한정                전체 탭 + 1개면 위로/아래로 이동, 라이브러리 제거,
                                라이브러리 완전 제거(빨강), 1개면 이름 변경
     5) 블랙리스트에 추가
     6) 1개 + 즐겨찾기 탭 아님 → ★ 즐겨찾기에 추가 / 에서 제거

   우클릭은 선택을 바꾸지 않는다. 우클릭한 항목이 선택 안에 있으면 선택 전체,
   아니면 그 항목만 대상이다 (원본 1954). */

export type Ctx =
  | {
      kind: "tree";
      x: number;
      y: number;
      label: string;
      /** 실제 전체 경로 — 원본 이름변경/블랙리스트 대화상자는 라벨이 아니라 경로를 보여준다 */
      path?: string;
      /** 선택이 여러 개일 때의 대상 경로들 (원본 selected_paths) */
      paths?: string[];
    /* 트리 안에서만 처리되는 동작들 — 상태가 Sidebar 에 있으므로 클로저로 싣는다
       (원본은 트리 위젯이 직접 처리하거나 시그널로 MainWindow 에 올린다) */
    actions?: {
      collapseAll: () => void;
      favoriteToggle: (add: boolean) => void;
      removeFromTab: () => void;
    };
      /** 대상 개수 — 2개 이상이면 문구가 "N개" 형태로 바뀐다 */
      count?: number;
      isRoot?: boolean;
      hasChildren?: boolean;
      /** all | fav | user */
      tab?: string;
      isFavorite?: boolean;
      canMoveUp?: boolean;
      canMoveDown?: boolean;
    }
  | {
      kind: "tab"; x: number; y: number; label: string;
      /* 사용자 탭에서만 뜬다 (시스템 탭은 메뉴 없음 — 원본 _on_tab_context) */
      actions?: { rename: () => void; remove: () => void };
    }
  | { kind: "result"; x: number; y: number; label: string; path: string;
      selectedCount: number;
      /** 다중 선택일 때의 선택 경로 전체 (원본 selected_paths) */
      paths?: string[] }
  | null;

function copy(text: string) {
  void navigator.clipboard?.writeText(text);
}

export function ContextMenu({ ctx, onClose, onRevealInBrowser, onRename, onBlacklistAdd, onMoveRoot,
                              onBlacklistLibrary, onCollapseAll, onFavoriteToggle,
                              onRemoveFromTab, onRescan, onRemoveRoots }: {
  ctx: Ctx;
  onClose: () => void;
  /** 원본 revealInBrowserRequested — 파일 브라우저에서 그 폴더를 선택한다 */
  onRevealInBrowser?: (path: string) => void;
  /** 원본 folder_tree._prompt_rename — QInputDialog("이름 변경", "새 이름:\n{path}"),
      기본값은 현재 표시명(text(0)) 이다 */
  onRename?: (path: string, current: string) => void;
  /** 원본 blacklist_panel.add_paths — 경로마다 설명을 물어 등록한다 */
  onBlacklistAdd?: (paths: string[]) => void;
  /** 원본 folder_tree._move_root(item, delta) — 전체 탭에서 라이브러리 순서를 바꾼다 */
  onMoveRoot?: (path: string, delta: -1 | 1) => void;
  /** 원본 _on_results_add_to_blacklist — 파일이 속한 라이브러리 루트를 블랙리스트에 */
  onBlacklistLibrary?: (filePath: string) => void;
  /** 원본 _collapse_all — 우클릭한 폴더 자신과 그 아래를 모두 접는다 */
  onCollapseAll?: () => void;
  /** 원본 rescanRequested / fullRescanRequested — 선택 폴더 재스캔 (인덱스 쓰기) */
  onRescan?: (full: boolean) => void;
  /** 원본 removeRootsRequested / purgeRootsRequested — 라이브러리 제거 / 완전 제거 */
  onRemoveRoots?: (purge: boolean) => void;
  /** 원본 favoriteAdded / favoriteRemoved — 단일 선택 + 즐겨찾기 탭이 아닐 때만 */
  onFavoriteToggle?: (add: boolean) => void;
  /** 원본 removeFromTabRequested — 사용자 탭 items 에서 제거 (탭에서만 가려진다) */
  onRemoveFromTab?: () => void;
}) {
  useEffect(() => {
    if (!ctx) return;
    const close = () => onClose();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", close);
    };
  }, [ctx, onClose]);

  if (!ctx) return null;

  const x = Math.min(ctx.x, window.innerWidth - 240);
  const y = Math.min(ctx.y, window.innerHeight - 300);

  /* ── 결과표 행 메뉴 (원본 results_table.py:840) ── */
  if (ctx.kind === "result") {
    const multi = ctx.selectedCount > 1;
    return (
      <div className="ctxmenu" style={{ left: x, top: y }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="ctxitem ctxlabel">
          {multi ? `선택한 사운드 ${ctx.selectedCount}개` : ctx.label}
        </div>
        <div className="ctxsep" />
        <button className="ctxitem"
                onClick={() => { onRevealInBrowser?.(ctx.path); onClose(); }}>
          <IcoFolder size={13} /> 이 라이브러리 파일 브라우저에서 보기
        </button>
        <button className="ctxitem"
                onClick={() => { void revealInExplorer(ctx.path); onClose(); }}>
          <IcoFolder size={13} /> 탐색기에서 보기
        </button>
        <div className="ctxsep" />
        <button className="ctxitem" onClick={() => { copy(ctx.label); onClose(); }}><IcoExport size={13} /> 이 파일 이름 복사</button>
        <button className="ctxitem" onClick={() => { copy(ctx.path); onClose(); }}><IcoExport size={13} /> 경로 복사</button>
        <div className="ctxsep" />
        {/* 원본 addFileToBlacklistRequested — 파일 경로 자체를 블랙리스트에 넣는다
            (DB query 가 정확일치로 그 파일만 검색에서 뺀다). 다중 선택이면 선택분 전체. */}
        <button className="ctxitem"
                onClick={() => {
                  onBlacklistAdd?.(multi && ctx.paths?.length ? ctx.paths : [ctx.path]);
                  onClose();
                }}>
          <IcoBan size={13} /> {multi ? `선택한 사운드 ${ctx.selectedCount}개 블랙리스트에 추가` : "이 사운드 블랙리스트에 추가"}
        </button>
        {/* 원본 addToBlacklistRequested → _on_results_add_to_blacklist:
            그 파일이 속한 **가장 긴 매칭 라이브러리 루트**를 넣고,
            루트를 못 찾으면 상위 폴더를 넣는다. */}
        <button className="ctxitem"
                onClick={() => { onBlacklistLibrary?.(ctx.path); onClose(); }}>
          <IcoBan size={13} /> 이 라이브러리 블랙리스트에 추가
        </button>
      </div>
    );
  }

  /* ── 탭 메뉴 — 사용자 탭만. 시스템 탭은 메뉴 없음 (원본 1005) ── */
  if (ctx.kind === "tab") {
    return (
      <div className="ctxmenu" style={{ left: x, top: y }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="ctxitem ctxlabel">{ctx.label}</div>
        <div className="ctxsep" />
        <button className="ctxitem"
                onClick={() => { ctx.actions?.rename(); onClose(); }}>
          <IcoFolder size={13} /> 이름 변경
        </button>
        <button className="ctxitem"
                onClick={() => { ctx.actions?.remove(); onClose(); }}>
          <IcoTrash size={13} /> 탭 삭제
        </button>
      </div>
    );
  }

  /* ── 트리 메뉴 ── */
  const n = ctx.count ?? 1;
  const many = n >= 2;
  const suffix = many ? ` (${n}개)` : "";

  return (
    <div className="ctxmenu" style={{ left: x, top: y }} onMouseDown={(e) => e.stopPropagation()}>
      <div className="ctxitem ctxlabel">{many ? `선택 폴더 ${n}개` : ctx.label}</div>
      <div className="ctxsep" />

      {ctx.hasChildren && (
        <>
          <button className="ctxitem"
                  onClick={() => { (ctx.actions?.collapseAll ?? onCollapseAll)?.(); onClose(); }}>
            <IcoCaret size={13} /> 모두 접기
          </button>
          <div className="ctxsep" />
        </>
      )}

      <button className="ctxitem"
              onClick={() => { onRescan?.(false); onClose(); }}>
        <IcoRefresh size={13} /> {many ? `선택 폴더 ${n}개 빠른 재스캔` : "빠른 재스캔"}
      </button>
      <button className="ctxitem"
              onClick={() => { onRescan?.(true); onClose(); }}>
        <IcoRefresh size={13} /> {many ? `선택 폴더 ${n}개 전체 재스캔` : "전체 재스캔"}
      </button>

      {ctx.tab === "user" && (
        <>
          <div className="ctxsep" />
          <button className="ctxitem"
                  onClick={() => { (ctx.actions?.removeFromTab ?? onRemoveFromTab)?.(); onClose(); }}>
            <IcoTrash size={13} /> 이 탭에서 삭제{suffix}
          </button>
        </>
      )}
      {ctx.tab === "fav" && (
        <>
          <div className="ctxsep" />
          <button className="ctxitem"
                  onClick={() => { (ctx.actions?.favoriteToggle ?? onFavoriteToggle)?.(false); onClose(); }}>
            <IcoStar size={13} /> ★ 즐겨찾기에서 제거{suffix}
          </button>
        </>
      )}

      {ctx.isRoot && (
        <>
          {ctx.tab === "all" && !many && (
            <>
              <div className="ctxsep" />
              <button className="ctxitem" disabled={ctx.canMoveUp === false}
                      onClick={() => { onMoveRoot?.(ctx.path ?? ctx.label, -1); onClose(); }}>
                <IcoCaret className="rot-up" size={13} /> 위로 이동
              </button>
              <button className="ctxitem" disabled={ctx.canMoveDown === false}
                      onClick={() => { onMoveRoot?.(ctx.path ?? ctx.label, 1); onClose(); }}>
                <IcoCaret className="rot-down" size={13} /> 아래로 이동
              </button>
            </>
          )}
          <div className="ctxsep" />
          <button className="ctxitem"
                  onClick={() => { onRemoveRoots?.(false); onClose(); }}>
            <IcoFolderMinus size={13} /> {many ? `라이브러리 ${n}개 제거` : "라이브러리 제거"}
          </button>
          {/* 원본 _add_danger_action — 빨강 #e05555, 호버 배경 rgba(224,85,85,.16) */}
          <button className="ctxitem danger"
                  onClick={() => { onRemoveRoots?.(true); onClose(); }}>
            <IcoTrash size={13} /> {many ? `라이브러리 ${n}개 완전 제거` : "라이브러리 완전 제거"}
          </button>
          {!many && (
            <>
              <div className="ctxsep" />
              <button className="ctxitem"
                      onClick={() => { onRename?.(ctx.path ?? ctx.label, ctx.label); onClose(); }}>
                <IcoFolder size={13} /> 이름 변경
              </button>
            </>
          )}
        </>
      )}

      <div className="ctxsep" />
      <button className="ctxitem"
              onClick={() => {
                /* 원본 add_paths(paths) — 다중 선택이면 경로마다 순서대로 물어본다 */
                onBlacklistAdd?.(ctx.paths ?? [ctx.path ?? ctx.label]);
                onClose();
              }}>
        <IcoBan size={13} /> 블랙리스트에 추가{suffix}
      </button>

      {!many && ctx.tab !== "fav" && (
        ctx.isFavorite ? (
          <button className="ctxitem"
                  onClick={() => { (ctx.actions?.favoriteToggle ?? onFavoriteToggle)?.(false); onClose(); }}>
            <IcoStar size={13} /> ★ 즐겨찾기에서 제거
          </button>
        ) : (
          <button className="ctxitem"
                  onClick={() => { (ctx.actions?.favoriteToggle ?? onFavoriteToggle)?.(true); onClose(); }}>
            <IcoStar size={13} /> ★ 즐겨찾기에 추가
          </button>
        )
      )}
    </div>
  );
}
