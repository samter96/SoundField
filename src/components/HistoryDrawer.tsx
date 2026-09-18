import { useEffect, useRef, useState } from "react";
import { clearHistory, loadHistory } from "../backend";
import { watchListDrag } from "../drag";

/* 원본 app/ui/history_panel.py:HistoryPanel 을 그대로 옮긴 것.
   - 제목은 "히스토리" (닫기 버튼 없음 — 히스토리 토글 버튼으로 닫는다)
   - 시작 접힘(폭 0), 펼침 기본 280 / 최소 180 / 최대 800, 애니메이션 168ms
   - 좌측 8px 리사이즈 grip 으로 폭 조절, 놓을 때 폭 저장
   - 항목은 파일명만 표시하고 tooltip 에 전체 경로. 최신이 위
   - 더블클릭(itemActivated)으로 재생. 드래그로 DAW 로 내보낼 수 있다
   - 푸터: "N개" + "지우기"
   실제 기록은 %LOCALAPPDATA%\SoundField\history.json 이다. */

const EXPANDED_WIDTH = 280;
const MIN_WIDTH = 180;
const MAX_WIDTH = 800;
const SAVED_WIDTH_KEY = "soundfield.historyWidth";

type Props = {
  open: boolean;
  /** 이 세션에서 재생한 경로 (최신이 마지막) — 원본 _record_history 와 같은 순서 */
  sessionPlays?: string[];
  /** 원본 history_panel.dragDropped — DAW 로 드롭 성사 시 (stop_on_drag 판단은 상위) */
  onDragDropped?: () => void;
  onPick: (name: string) => void;
  /* 원본 config "history_width" — 저장된 드로어 폭 */
  initialWidth?: number;
  /* grip 을 놓았을 때 (원본 widthChanged → _on_history_width_changed → _save_config) */
  onWidthCommit?: (width: number) => void;
  onClear?: () => void;
};

export function HistoryDrawer({ open, onPick, initialWidth, sessionPlays = [],
                               onDragDropped, onWidthCommit, onClear }: Props) {
  /* 원본 history.json 은 "오래된 -> 최신" 순 배열이고 화면은 최신이 위로 간다.
     PoC 는 원본 파일을 덮어쓰지 않으므로, 이 세션 재생분은 메모리에서만 얹는다. */
  const [stored, setStored] = useState<string[]>([]);
  const [cleared, setCleared] = useState(false);

  useEffect(() => {
    let alive = true;
    loadHistory().then((got) => { if (alive && got) setStored(got); });
    return () => { alive = false; };
  }, []);

  /* 원본 _record_history: 같은 경로는 지우고 맨 뒤에 다시 넣는다 (최대 100개) */
  const items = (() => {
    if (cleared) return sessionPlays.slice(-100);
    const merged = [...stored];
    for (const path of sessionPlays) {
      const at = merged.indexOf(path);
      if (at >= 0) merged.splice(at, 1);
      merged.push(path);
    }
    return merged.slice(-100);
  })();
  /* ── 키보드 조작 (조사 H03) ────────────────────────────────────────────────
     원본은 QListWidget 이라 클릭으로 고르고 ↑/↓ 로 옮기고 Enter(itemActivated)로
     재생한다. PoC 항목은 포커스도 키 처리도 없는 그냥 div 여서 **마우스 더블클릭
     말고는 재생할 방법이 없었다.**
     아래 목록은 최신이 위(reverse)이므로, 여기서 세는 위치도 그 순서 기준이다. */
  const shown = [...items].reverse();
  const [cursor, setCursor] = useState(-1);
  const listRef = useRef<HTMLDivElement>(null);
  /* 목록이 바뀌면(재생으로 새 항목이 얹히거나 지우면) 위치를 되돌린다 */
  useEffect(() => { setCursor(-1); }, [items.length, cleared]);
  /* 고른 항목이 화면 밖이면 보이게 — 원본 QListWidget 도 자동 스크롤한다 */
  useEffect(() => {
    if (cursor < 0) return;
    const box = listRef.current;
    const row = box?.children[cursor] as HTMLElement | undefined;
    row?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (!shown.length) return;
    const key = event.key;
    if (key === "ArrowDown" || key === "ArrowUp") {
      event.preventDefault();
      const step = key === "ArrowDown" ? 1 : -1;
      setCursor((at) => {
        const next = at < 0 ? (step > 0 ? 0 : shown.length - 1) : at + step;
        return Math.max(0, Math.min(shown.length - 1, next));
      });
    } else if (key === "Home" || key === "End") {
      event.preventDefault();
      setCursor(key === "Home" ? 0 : shown.length - 1);
    } else if (key === "Enter") {
      event.preventDefault();
      if (cursor >= 0) onPick(shown[cursor]);
    }
  };

  const [width, setWidth] = useState(() => {
    const saved = Number(localStorage.getItem(SAVED_WIDTH_KEY));
    return saved >= MIN_WIDTH && saved <= MAX_WIDTH ? saved : EXPANDED_WIDTH;
  });
  /* config 가 도착하면 그 폭을 쓴다 (원본 앱이 저장해 둔 값) */
  useEffect(() => {
    if (initialWidth && initialWidth >= MIN_WIDTH && initialWidth <= MAX_WIDTH)
      setWidth(initialWidth);
  }, [initialWidth]);
  const dragRef = useRef<{ x0: number; w0: number } | null>(null);
  const [resizing, setResizing] = useState(false);
  const liveWidthRef = useRef(width);
  liveWidthRef.current = width;

  useEffect(() => {
    localStorage.setItem(SAVED_WIDTH_KEY, String(width));
  }, [width]);

  const startResize = (event: React.MouseEvent) => {
    event.preventDefault();
    dragRef.current = { x0: event.clientX, w0: width };
    setResizing(true);
    const move = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      /* 왼쪽으로 끌면 넓어진다 (패널이 우측에 있으므로) */
      setWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, d.w0 - (ev.clientX - d.x0))));
    };
    const up = () => {
      dragRef.current = null;
      setResizing(false);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
      /* 원본은 grip 을 놓는 순간에만 폭을 저장한다 (드래그 중에는 저장 안 함) */
      onWidthCommit?.(Math.round(liveWidthRef.current));
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <aside className={"drawer" + (open ? " open" : "") + (resizing ? " resizing" : "")}
           aria-hidden={!open}
           style={{
             width: open ? width : 0,
             maxWidth: open ? width : 0,
             flexBasis: open ? width : 0,
           }}>
      <div className="history-grip" onMouseDown={startResize} aria-hidden="true">
        <span /><span /><span />
      </div>
      <div className="history-content">
        <div className="history-head">
          <span className="section-title">히스토리</span>
        </div>
        <div className="history-list"
             ref={listRef}
             /* 목록 자체가 포커스를 받아 ↑/↓/Enter 를 처리한다 (조사 H03) */
             tabIndex={0}
             onKeyDown={onListKeyDown}
             /* 원본 _DraggableHistoryList.wheelEvent — Shift+휠 = 가로 스크롤
                (delta 그대로 반영, 결과표의 50px 틱과 다르다) */
             onWheel={(event) => {
               if (!event.shiftKey) return;
               event.preventDefault();
               event.currentTarget.scrollLeft -= event.deltaY || event.deltaX;
             }}>
          {/* 원본 set_items: 최신이 위. 라벨은 파일명만, 툴팁에 전체 경로 */}
          {shown.map((h, i) => (
            <div className={"history-item" + (i === cursor ? " selected" : "")}
                 key={h + i} data-tip={h}
                 /* 클릭은 고르기까지만 — 재생은 Enter 나 더블클릭이다 (원본과 같음) */
                 onClick={() => setCursor(i)}
                 /* 원본 history_panel 도 DAW 로 드래그 아웃을 지원한다 (dragDropped) */
                 onMouseDown={(event) => {
                   if (event.button !== 0) return;
                   setCursor(i);
                   /* 원본 _DraggableHistoryList 는 결과표와 달리 행 이탈 조건 없이
                      맨해튼 10px 만 넘으면 바로 드래그를 시작한다. */
                   const watch = watchListDrag(event.clientX, event.clientY,
                     () => [h], onDragDropped);
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
                 onDoubleClick={() => onPick(h)}>
              {h.split(/[\\/]/).filter(Boolean).at(-1) || h}
            </div>
          ))}
        </div>
        <div className="history-foot">
          <span className="caps-num">{items.length}개</span>
          <div style={{ flex: 1 }} />
          <button className="btn btn-sm pill" onClick={() => {
            void clearHistory(); setStored([]); setCleared(true); onClear?.();
          }}>지우기</button>
        </div>
      </div>
    </aside>
  );
}
