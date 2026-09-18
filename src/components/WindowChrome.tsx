import { IcoMin, IcoMax, IcoX, YsgMark } from "../icons";
import { winMinimize, winToggleMax, winClose } from "../win";
/* 허브 열기 — 원본 QDesktopServices.openUrl 자리를 그대로 쓴다 (backend.ts).
   Rust 쪽 sf_open_url 이 **https 주소만** 통과시킨다. */
import { openExternal } from "../backend";
import { LangToggle } from "./LangToggle";
import { t, type Lang } from "../i18n";
import { BRAND } from "../brand";

export function WindowChrome({ onLangChange }: { onLangChange?: (lang: Lang) => void }) {
  return (
    <div className="chrome" data-tauri-drag-region>
      {/* 윗줄(타이틀바) = 브랜드 로고 + 브랜드명. 아랫줄(헤더) = 툴 로고 + 제품명 + 버전.
          ⚠ 문구를 여기 직접 박지 말 것 — src/brand.ts 의 BRAND 를 거친다.
          마크는 색을 박지 않고 currentColor 를 쓰므로 무채색·네온·라이트
          세 테마에서 글자색을 그대로 따라간다. */}
      {/* 매듭 마크는 **아랫줄 SoundField 마크와 같은 17px** 이다
          (사용자 지시 2026-09-15 — 26px 은 너무 컸다). */}
      <span className="chrome-logo" data-tauri-drag-region>
        <YsgMark size={17} />
      </span>
      <span className="chrome-title" data-tauri-drag-region>{BRAND.chromeTitle}</span>
      <div className="chrome-spacer" data-tauri-drag-region />
      <div className="chrome-actions">
        {/* 허브 바로가기 — 언어 토글 **왼쪽** (사용자 결정 2026-09-15).
            ⚠ 창 끌기 영역 안이라 이 버튼에는 data-tauri-drag-region 을 넣지 않는다 —
              넣으면 클릭이 창 끌기로 먹혀 안 눌린다 (LangToggle 과 같은 이유). */}
        <button className="chrome-hub"
                type="button"
                aria-label={t("YSG Audio Labs 허브 열기")}
                data-tip={t("YSG Audio Labs 허브 열기")}
                onClick={() => openExternal(BRAND.hubUrl)}>
          {/* 컬러 엠블럼 — 타이틀바에서 유일한 컬러 요소다 (사용자 지시 2026-09-15).
              시작 화면과 같은 자산이라 두 화면이 같은 로고로 읽힌다. */}
          <img className="chrome-hub-mark" src="/ysg-emblem-2026.png" alt="" />
          HUB
        </button>
        {/* 표시 언어 전환 — 창 버튼 왼쪽 (사용자 지시 2026-09-08) */}
        <LangToggle onChange={onLangChange} />
        <button className="chrome-btn" aria-label={t("최소화")} onClick={winMinimize}><IcoMin size={13} /></button>
        <button className="chrome-btn" aria-label={t("최대화")} onClick={winToggleMax}><IcoMax size={11} /></button>
        <button className="chrome-btn close" aria-label={t("닫기")} onClick={winClose}><IcoX size={13} /></button>
      </div>
    </div>
  );
}
