import { setLang, t, useLang, type Lang } from "../i18n";

/* ── 표시 언어 슬라이드 토글 (사용자 지시 2026-09-08) ──────────────────────────
   자리는 타이틀바의 **ㅡ ㅁ ✕ 왼쪽**이다.

   손잡이가 좌우로 미끄러지는 스위치 하나로 두 언어를 다 보여준다. 두 칸(KR|EN)을
   각각 버튼으로 두는 방식은 지금 어느 쪽인지 한눈에 안 잡혀서, 켜진 쪽을 손잡이가
   덮는 형태로 만들었다.

   ⚠ 창 끌기 영역(`data-tauri-drag-region`) 안에 있으므로 이 버튼에는 그 속성을
     넣지 않는다. 넣으면 클릭이 창 끌기로 먹혀 토글이 안 눌린다. */
export function LangToggle({ onChange }: { onChange?: (lang: Lang) => void }) {
  const lang = useLang();
  const next: Lang = lang === "ko" ? "en" : "ko";
  return (
    <button className={"lang-toggle" + (lang === "en" ? " en" : "")}
            type="button"
            role="switch"
            aria-checked={lang === "en"}
            aria-label={t("표시 언어 전환 (한국어 / English)")}
            data-tip={t("표시 언어 전환 (한국어 / English)")}
            onClick={() => { setLang(next); onChange?.(next); }}>
      {/* 손잡이가 먼저 깔리고 글자가 그 위에 온다 — 켜진 쪽 글자가 손잡이 색을 받는다 */}
      <span className="lang-knob" aria-hidden="true" />
      <span className="lang-side">KR</span>
      <span className="lang-side">EN</span>
    </button>
  );
}
