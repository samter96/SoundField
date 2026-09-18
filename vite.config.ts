import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

/* 시작 화면(splash.html)의 소속 표기.
   ⚠ splash.html 은 정적 HTML 이라 React 쪽 코드(src/brand.ts)가 닿지 못한다.
     그래서 여기서 자리를 채운다. **두 곳이 같은 구성을 따로 갖고 있으므로**
     한쪽만 고치면 시작할 때 로고가 떴다가 바뀐다 (실제로 겪은 일이다).
   ⚠ 워드마크를 글꼴로 찍지 말 것. "YSG" 는 그라데이션이 입혀진 그림이라
     어떤 글꼴을 골라도 같은 모양이 안 나온다 (자산: tools/build_ysg_lockup.py). */
const ORG_HTML = `<div class="org">
        <img class="lockup" src="/ysg-lockup-2026.png" alt="YSG Audio Labs" />
      </div>`;

/* 카드 맨 아래 배경 — 허브의 SoundField 제품 사진을 흐리게 깐 것.
   ⚠ 사진을 깔면 눈금 격자를 끈다 (겹치면 지저분하다 — 사용자 지적 2026-09-15). */
const BG_HTML = `<span class="photo" style="background-image:url(/ysg-splash-bg.jpg)"></span>
      <style>.grid { display: none }</style>`;

/** splash.html 의 <!--BRAND_ORG--> · <!--BRAND_BG--> 자리를 채운다. */
function brandSplash() {
  return {
    name: "soundfield-brand-splash",
    transformIndexHtml(html: string) {
      if (!html.includes("<!--BRAND_ORG-->")) return html;   // index.html 은 건너뜀
      return html
        .replace("<!--BRAND_ORG-->", ORG_HTML)
        .replace("<!--BRAND_BG-->", BG_HTML);
    },
  };
}

export default defineConfig({
  plugins: [react(), brandSplash()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  /* 시작 화면(splash.html)을 두 번째 진입점으로 둔다.
     public/ 에 두면 그대로 복사만 돼서 위 플러그인이 손대지 못한다
     (출력 위치는 dist/splash.html 이라 tauri.conf 의 창 url 은 그대로다). */
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        splash: resolve(__dirname, "splash.html"),
      },
    },
  },
});
