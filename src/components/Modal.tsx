import { useEffect } from "react";
import { IcoCaret, IcoX } from "../icons";

type Props = {
  title: string;
  desc?: string;
  size?: "sm" | "md" | "lg" | "settings";
  onClose: () => void;
  /** 있으면 제목 왼쪽에 뒤로 버튼이 나온다 */
  onBack?: () => void;
  /** 이 창 **위에 다른 창이 떠 있으면** true — 그때는 Esc 를 이 창이 먹지 않는다.
      ⚠ 없으면 자식 확인창/제외 관리에서 Esc 를 눌렀을 때 **부모까지 함께 닫힌다.**
        원본은 자식이 별도 대화상자라 키를 자식이 받는다 (조사 M02). */
  blockEscape?: boolean;
  foot?: React.ReactNode;
  children: React.ReactNode;
};

export function Modal({ title, desc, size = "md", onClose, onBack, foot,
                       blockEscape = false, children }: Props) {
  useEffect(() => {
    if (blockEscape) return;      // 위에 다른 창이 떠 있다 — 그 창이 키를 받는다
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose, blockEscape]);

  /* ⚠ 바깥(scrim) 을 눌러도 닫지 않는다 — 사용자 지시 2026-09-15:
     "닫기 버튼이 존재하는 모든 창"은 바깥 클릭으로 닫히면 안 된다.
     중복 검수처럼 오래 걸린 작업 결과를 띄워둔 창이, 스크롤하려다 빗나간 클릭
     한 번에 사라졌다. 닫는 길은 [X] 버튼과 Esc 두 가지다.
     여기에 onMouseDown 을 다시 넣지 말 것. */
  return (
    <div className="scrim">
      <div className={"modal" + (size === "sm" ? " sm" : size === "lg" ? " lg" : size === "settings" ? " settings" : "")} role="dialog" aria-modal="true">
        <div className="modal-head">
          {/* 계층에서 한 단계 위로 — 원본은 자식 대화상자가 부모 위에 겹쳐 떠서
              닫으면 부모가 드러난다. PoC 는 한 번에 하나만 띄우므로 명시적으로
              돌아가는 버튼을 둔다 (사용자 요청: 닫으면 전부 닫혀버림). */}
          {onBack && (
            <button className="btn btn-icon btn-ghost modal-back" aria-label="뒤로"
                    data-tip="라이브러리 현황으로 돌아가기" onClick={onBack}>
              <IcoCaret size={13} className="rot-left" />
            </button>
          )}
          <span className="modal-title">{title}</span>
          {desc && <span className="modal-desc">{desc}</span>}
          <div style={{ flex: 1 }} />
          <button className="btn btn-icon btn-ghost" aria-label="닫기" onClick={onClose}><IcoX size={14} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

export function Check(
  { on, onChange, label, hint }:
  { on: boolean; onChange: (v: boolean) => void; label: string; hint?: string }
) {
  return (
    <div className="field">
      <div className="field-label">
        <div className="field-name">{label}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <div className="field-ctrl">
        <button className={"switch" + (on ? " on" : "")} role="switch" aria-checked={on}
                aria-label={label} onClick={() => onChange(!on)} />
      </div>
    </div>
  );
}
