import { useEffect, useLayoutEffect, useRef, useState } from "react";

/* ── 드롭다운 (네이티브 <select> 대체) ────────────────────────────────────────
   네이티브 select 의 팝업은 OS 가 그린다 — 어두운 라운드 버튼을 눌렀는데 흰
   외곽선의 각진 목록이 내려와 앱과 일치감이 전혀 없었다 (사용자 보고).
   버튼 모양은 기존 `.select` 를 그대로 쓰고, 목록만 앱의 컨텍스트 메뉴와 같은
   결(라운드 + 헤어라인 + elev 배경 + 행 호버)로 직접 그린다.

   동작은 네이티브와 같게 맞췄다:
     · 클릭으로 열고 닫기, 바깥 클릭 / Escape / 창 리사이즈·스크롤 시 닫기
     · ↑↓ 로 이동, Home/End, Enter/Space 로 선택, Tab 이탈 시 닫기
     · 열릴 때 현재 값에 하이라이트가 가 있다
     · 아래 공간이 부족하면 위로 뒤집어 띄운다
   `onWheel` 은 호출부에서 막던 것과 같게 여기서 막는다 (휠로 값이 바뀌면
   결과가 통째로 다시 조회돼 위험하다 — 원본도 휠을 막는다). */

type Props = {
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
  /** 버튼에 붙는 추가 클래스 (op-select / field-select / meta-select / active-filter …) */
  className?: string;
  title?: string;
  ariaLabel?: string;
  /** 보이는 글자만 바꾼다 (언어 전환용). ⚠ options/value 는 **식별자**이므로
   *  번역하면 안 된다 — 검색 필드 이름이 그대로 DB 필드 열쇠로 쓰인다. */
  label?: (value: string) => string;
};

const ROW_H = 26;
const PAD = 5;
const MARGIN = 6;

export function Dropdown({ value, options, onChange, className = "", title, ariaLabel,
                          label = (v) => v }: Props) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);

  /* 팝업 위치 — 버튼 기준. 아래가 좁으면 위로 뒤집는다. */
  useLayoutEffect(() => {
    if (!open) { setBox(null); return; }
    const btn = btnRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const height = options.length * ROW_H + PAD * 2 + 2;
    const below = window.innerHeight - rect.bottom - MARGIN;
    const up = below < height && rect.top > below;
    setBox({
      left: Math.min(rect.left, Math.max(MARGIN, window.innerWidth - rect.width - MARGIN)),
      top: up ? Math.max(MARGIN, rect.top - height - 4) : rect.bottom + 4,
      width: rect.width,
      up,
    });
    setHi(Math.max(0, options.indexOf(value)));
  }, [open, options, value]);

  /* 바깥 클릭 / 창 변화로 닫기 — 열려 있는 동안에만 듣는다 */
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (btnRef.current?.contains(event.target as Node)) return;
      if (popRef.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const close = () => setOpen(false);
    window.addEventListener("mousedown", onDown, true);
    window.addEventListener("resize", close);
    /* capture 로 들어야 목록 밖 스크롤(결과표 등)에서도 닫힌다 */
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  const pick = (next: string) => { setOpen(false); if (next !== value) onChange(next); };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") { if (open) { event.stopPropagation(); setOpen(false); } return; }
    if (event.key === "Tab") { setOpen(false); return; }
    if (!open) {
      if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); setOpen(true);
      }
      return;
    }
    if (event.key === "ArrowDown") { event.preventDefault(); setHi((i) => Math.min(options.length - 1, i + 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); setHi((i) => Math.max(0, i - 1)); }
    else if (event.key === "Home") { event.preventDefault(); setHi(0); }
    else if (event.key === "End") { event.preventDefault(); setHi(options.length - 1); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(options[hi]); }
  };

  return (
    <>
      <button ref={btnRef} type="button"
              className={"select sf-select" + (open ? " open" : "") + (className ? " " + className : "")}
              data-tip={title} aria-label={ariaLabel}
              aria-haspopup="listbox" aria-expanded={open}
              onWheel={(event) => event.preventDefault()}
              onKeyDown={onKeyDown}
              onClick={() => setOpen((v) => !v)}>
        <span className="sf-select-label">{label(value)}</span>
      </button>

      {open && box && (
        <div ref={popRef} className={"sf-pop" + (box.up ? " up" : "")} role="listbox"
             style={{ left: box.left, top: box.top, minWidth: box.width }}>
          {options.map((option, index) => (
            <button key={option} type="button" role="option" aria-selected={option === value}
                    className={"sf-opt" + (option === value ? " on" : "") + (index === hi ? " hi" : "")}
                    onMouseEnter={() => setHi(index)}
                    onClick={() => pick(option)}>
              <span className="sf-opt-mark" aria-hidden="true" />
              <span className="sf-opt-label">{label(option)}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
