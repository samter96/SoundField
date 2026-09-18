import { useEffect, useState } from "react";

/* 중앙 알림 — 원본 main_window.py:_CenterToast (1106).

   **동작 정책은 원본 그대로**:
   - 화면 **중앙**, 입력을 막지 않는다(클릭 통과), 패널 밖은 딤 없음
   - 완전 불투명 500ms 유지 → 1000ms 페이드아웃
   - 글리프/색: add "+" #2fb344 / remove "−" #db4343 / info "!" #e0a82b
   원본 문구는 그대로 쓴다 ("'탭이름' 에 추가" / "'탭이름' 에서 삭제" 등).

   **재질은 Tauri 판으로 다시 짰다** (사용자 지시 2026-09-03): 원본의 330x134 회색
   상자 + accent 1.5px 외곽선 + 58px 단색 원 대신, 스플래시·중앙 로더와 같은 결로
   라디얼 바닥 + 미세 격자 + 대각 시트 + 헤어라인을 쓰고, 상태색은 글로우와 하단
   헤어라인·아이콘에만 쓴다. 상태색은 `--toast-color` 하나로 전달한다. */

export type ToastKind = "add" | "remove" | "info";
export type ToastMessage = { kind: ToastKind; text: string; id: number } | null;

const KINDS: Record<ToastKind, { glyph: string; color: string }> = {
  add: { glyph: "+", color: "#2fb344" },
  remove: { glyph: "−", color: "#db4343" },
  info: { glyph: "!", color: "#e0a82b" },
};

const HOLD_MS = 500;
const FADE_MS = 1000;

export function CenterToast({ message }: { message: ToastMessage }) {
  const [phase, setPhase] = useState<"hidden" | "hold" | "fade">("hidden");

  useEffect(() => {
    if (!message) return;
    setPhase("hold");
    const t1 = window.setTimeout(() => setPhase("fade"), HOLD_MS);
    const t2 = window.setTimeout(() => setPhase("hidden"), HOLD_MS + FADE_MS);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [message]);

  if (!message || phase === "hidden") return null;
  const kind = KINDS[message.kind] ?? KINDS.info;

  return (
    <div className={"center-toast " + phase} aria-live="polite">
      <div className="center-toast-panel"
           style={{ ["--toast-color" as string]: kind.color }}>
        <span className="center-toast-grid" aria-hidden="true" />
        <span className="center-toast-sheen" aria-hidden="true" />
        <div className="center-toast-icon" aria-hidden="true">{kind.glyph}</div>
        <div className="center-toast-msg">{message.text}</div>
        <span className="center-toast-rule" aria-hidden="true" />
      </div>
    </div>
  );
}
