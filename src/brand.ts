/* ── 브랜드 표기 ──────────────────────────────────────────────────────────────
   이 저장소는 **YSG Audio Labs 판 전용**이다 (사용자 결정 2026-09-15).

   브랜드 표기는 여기 한 곳에서만 정한다.
   **이 저장소는 공개다.** 공개해도 되는 문구·이미지·경로만 넣는다.

   ⚠ 화면에 브랜드 문구를 직접 박지 말 것. 새 자리가 생기면 여기에 항목을 더한다. */

export type BrandInfo = {
  /** 최상단 타이틀바 문구 */
  chromeTitle: string;
  /** 시작 화면 왼쪽 가로 락업 이미지 (엠블럼 + 워드마크 한 덩어리) */
  splashLockup: string;
  /** 시작 화면 배경 사진. 이게 깔리면 눈금 격자는 끈다. */
  splashBg: string;
  /** 타이틀바 HUB 버튼이 여는 주소 */
  hubUrl: string;
};

export const BRAND: BrandInfo = {
  chromeTitle: "YSG Audio Labs",
  /* 시작 화면 소속 표기는 가로 락업 한 장이다 — 허브 페이지 상단바와 같은 그림.
     자산은 tools/build_ysg_lockup.py 가 만든다 (완성 락업을 줄여 쓰지 말 것). */
  splashLockup: "/ysg-lockup-2026.png",
  /* 허브의 SoundField 제품 사진을 흐리게 깐 배경. 자산은 tools/build_ysg_splash_bg.py. */
  splashBg: "/ysg-splash-bg.jpg",
  hubUrl: "https://samter96.github.io/YSGAudioTools/",
};
