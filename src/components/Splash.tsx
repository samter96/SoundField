import { useEffect, useState } from "react";
import { useAppVersion } from "../version";
import { BRAND } from "../brand";

/* ── 시작 스플래시 ─────────────────────────────────────────────────────────────
   **동작은 원본 그대로** (app/ui/splash.py + main.py:279/316):
     앱을 켜면 곧바로 띄우고, 초기 데이터(설정·첫 결과·폴더 트리)가 준비되면
     240ms(WINDOW_FADE_MS) 페이드로 걷는다. 테마와 무관하게 항상 다크 톤이다.

   **디자인은 Tauri 판으로 다시 짰다** (사용자 지시 2026-09-02).
   원본은 QSplashScreen 픽스맵을 직접 그린 것이라 640x360 고정 + 손으로 찍은
   동심원 파형이었다. 여기서는 같은 정보 구성(소속 3줄 / 워드마크 / 버전 /
   로딩 문구)을 유지하면서:
     · 층이 보이는 배경 — 아래쪽 가중 라디얼 + 미세 격자 + 상단 하이라이트
     · 좌측 액센트 레일 + 소속 3줄은 작은 대문자 + 넓은 자간
     · 워드마크는 큰 글자 + 세로 그라데이션, 아래 액센트 밑줄
     · 로딩 표현은 중앙 로더와 **같은 파형 박동**(좌→우)으로 통일
     · 하단 전폭 진행 헤어라인 */

export function Splash({ done }: { done: boolean }) {
  /* 버전은 실행 중인 exe 에서 읽는다 — version.ts 주석 참고 (글자로 박지 말 것) */
  const version = useAppVersion();
  const [hidden, setHidden] = useState(false);

  /* 원본은 splash.finish(win) 로 메인 창 표시와 동시에 닫는다 — 여기서는
     초기 데이터가 준비되면 240ms(WINDOW_FADE_MS) 페이드로 걷는다. */
  useEffect(() => {
    if (!done) return;
    const t = window.setTimeout(() => setHidden(true), 240);
    return () => window.clearTimeout(t);
  }, [done]);

  if (hidden) return null;

  return (
    <div className={"splash" + (done ? " leaving" : "")} role="status" aria-live="polite">
      <div className="splash-card">
        {/* 배경은 제품 사진이다. 눈금 격자는 쓰지 않는다 — 사진 위에 겹치면
            지저분하다 (사용자 지적 2026-09-15). 네이티브 시작창도 같은 규칙. */}
        <span className="splash-photo" aria-hidden="true"
              style={{ backgroundImage: `url(${BRAND.splashBg})` }} />
        <span className="splash-sheen" aria-hidden="true" />

        {/* 소속 표기는 가로 락업 이미지 한 장이다 (사용자 결정 2026-09-15).
            ⚠ 글자·이미지를 직접 박지 말 것 — src/brand.ts 를 거친다.
            ⚠ 네이티브 시작창(splash.html + vite.config.ts 의 brandSplash)이 같은
              구성을 따로 갖고 있다. 한쪽만 고치면 시작할 때 로고가 떴다가 바뀐다
              (2026-09-08~09-14 에 실제로 그 상태였다). */}
        <div className="splash-org">
          <img className="splash-lockup" src={BRAND.splashLockup} alt="YSG Audio Labs" />
        </div>

        <div className="splash-mark">
          <div className="splash-word">SoundField</div>
          <span className="splash-underline" aria-hidden="true" />
          <div className="splash-meta">
            <span className="splash-ver">{version && `v${version}`}</span>
            <span className="splash-load">
              <span className="splash-pulse" aria-hidden="true">
                {Array.from({ length: 5 }, (_, i) => (
                  <i key={i} style={{ ["--i" as string]: String(i) }} />
                ))}
              </span>
              라이브러리 로딩 중…
            </span>
          </div>
        </div>

        <span className="splash-progress" aria-hidden="true" />
      </div>
    </div>
  );
}
