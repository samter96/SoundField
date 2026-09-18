import { useEffect, useRef, useState } from "react";

/* ── 툴팁 정책 (원본 대조 2026-09-03) ────────────────────────────────────────
   **원본은 툴팁을 전역 QSS 로 테마화한다** (theme.py:207):
       QToolTip { background: bg_elev; color: text; border: 1px border_strong;
                  padding: 6px 9px; border-radius: 4px }
   즉 원본에는 OS 기본(흰색) 툴팁이 **한 곳도 없다.** PoC 는 `title=` 속성을 섞어 써서
   어떤 건 테마 툴팁, 어떤 건 흰 시스템 툴팁으로 나왔고 타이밍도 OS 마음대로였다
   (사용자 신고: "정확한 검색만 흰 시스템 툴팁, 나오다 말다 한다").

   그래서 툴팁을 이 한 곳에서만 그린다. 요소는 `data-tip="문구"` 로 표시한다.

   타이밍도 원본을 따른다:
   · 일반 위젯 — Qt 기본 지연과 같은 감각으로 **700ms** 뒤 표시
   · **밀집 목록**(결과표 행 / 폴더 트리 행) — `_DENSE_LIST_TOOLTIP_DELAY_MS = 650`
     (results_table.py:28, folder_tree.py:40). 목록 쪽은 `data-tip-dense` 를 붙인다.
   · **같은 항목 안에서 마우스가 움직이면 타이머를 다시 시작하지 않는다**
     (원본 _schedule_delayed_list_tooltip: 텍스트가 같으면 위치만 갱신).
     텍스트가 바뀌면 즉시 감추고 새로 센다.
   · 표시 위치는 커서 + (16, 18) — 원본과 같은 오프셋.
   · 벗어나면 즉시 감춘다. 누르면 감춘다 (원본도 클릭 시 툴팁이 사라진다).

   여러 줄 문구는 원본처럼 줄바꿈을 그대로 보여준다 (\n → pre-line). */

const DELAY_NORMAL = 700;
const DELAY_DENSE = 650;      // 원본 _DENSE_LIST_TOOLTIP_DELAY_MS
const OFFSET_X = 16;
const OFFSET_Y = 18;

type Shown = { text: string; x: number; y: number };

export function TooltipLayer() {
  const [shown, setShown] = useState<Shown | null>(null);
  const timer = useRef<number | null>(null);
  const pending = useRef<{ text: string; x: number; y: number } | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const clear = () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
    };
    const hide = () => { clear(); pending.current = null; setShown(null); };

    const onMove = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      const host = target?.closest?.("[data-tip]") as HTMLElement | null;
      const text = host?.getAttribute("data-tip")?.trim() || "";
      if (!text) { hide(); return; }

      /* 같은 문구 위에서의 이동 — 이미 떠 있으면 그대로 두고, 대기 중이면
         위치만 갱신한다 (원본과 같이 타이머를 다시 시작하지 않는다). */
      if (pending.current?.text === text || shown?.text === text) {
        if (pending.current) {
          pending.current.x = event.clientX;
          pending.current.y = event.clientY;
        }
        return;
      }

      clear();
      setShown(null);
      pending.current = { text, x: event.clientX, y: event.clientY };
      const dense = host?.hasAttribute("data-tip-dense");
      timer.current = window.setTimeout(() => {
        timer.current = null;
        const p = pending.current;
        if (p) setShown({ ...p });
      }, dense ? DELAY_DENSE : DELAY_NORMAL);
    };

    window.addEventListener("mousemove", onMove, true);
    window.addEventListener("mousedown", hide, true);
    window.addEventListener("wheel", hide, true);
    window.addEventListener("keydown", hide, true);
    window.addEventListener("blur", hide);
    return () => {
      clear();
      window.removeEventListener("mousemove", onMove, true);
      window.removeEventListener("mousedown", hide, true);
      window.removeEventListener("wheel", hide, true);
      window.removeEventListener("keydown", hide, true);
      window.removeEventListener("blur", hide);
    };
  }, [shown?.text]);

  /* 화면 밖으로 나가지 않게 접어 넣는다 (원본 QToolTip 도 화면 안으로 보정한다) */
  useEffect(() => {
    const box = boxRef.current;
    if (!box || !shown) return;
    const rect = box.getBoundingClientRect();
    let x = shown.x + OFFSET_X;
    let y = shown.y + OFFSET_Y;
    if (x + rect.width > window.innerWidth - 6) x = Math.max(6, shown.x - rect.width - 8);
    if (y + rect.height > window.innerHeight - 6) y = Math.max(6, shown.y - rect.height - 8);
    box.style.left = `${Math.round(x)}px`;
    box.style.top = `${Math.round(y)}px`;
  }, [shown]);

  if (!shown) return null;
  return (
    <div ref={boxRef} className="apptip" role="tooltip"
         style={{ left: shown.x + OFFSET_X, top: shown.y + OFFSET_Y }}>
      {shown.text}
    </div>
  );
}
