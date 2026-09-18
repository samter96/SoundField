import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { applyTheme, loadTheme } from "./theme";
import { invoke } from "@tauri-apps/api/core";
import { debugLog } from "./backend";

/* 이 환경에서는 Tauri 창의 콘솔을 볼 수 없다 (창 캡처는 검은 화면, 포커스 강제 실패).
   그래서 프런트 예외를 파일 로그(poc_debug.log)로 남긴다 — 개발용. */
window.addEventListener("error", (e) =>
  debugLog("JSERR " + String((e as ErrorEvent).error?.stack || (e as ErrorEvent).message).slice(0, 600)));
window.addEventListener("unhandledrejection", (e) =>
  debugLog("JSREJ " + String((e as PromiseRejectionEvent).reason).slice(0, 600)));

applyTheme(loadTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

/* ── 시작 시 검은 창 프레임 없애기 ───────────────────────────────────────────
   창을 먼저 띄우면 WebView 가 첫 페인트를 하기 전까지 **빈(검은) 창**이 보인다.
   HTML/창 배경색(backgroundColor)으로는 막히지 않았다 (사용자 2회 재보고).
   그래서 창은 `visible: false` 로 만들고, 첫 화면이 실제로 그려진 뒤 창을 띄운다.

   표시 책임은 **Rust(sf_ready)** 에 둔다 — JS 의 window.show() 는 api 모듈 로딩과
   권한에 의존해 조용히 실패할 수 있다. 준비 판정은 세 단계를 모두 기다린다:
     ① 폰트 로딩 완료(document.fonts.ready) — 폰트 교체로 첫 프레임이 다시 그려지는 것 방지
     ② requestAnimationFrame 2회 — 레이아웃 → 합성
     ③ setTimeout(0) — 합성 프레임이 화면에 올라갈 여유
   Rust 쪽에 5초 안전망이 있어, 이 신호가 못 가도 창은 반드시 나타난다. */
void (async () => {
  const t0 = performance.now();
  try {
    await Promise.race([
      document.fonts?.ready ?? Promise.resolve(),
      new Promise((done) => window.setTimeout(done, 1200)),   // 폰트가 늦어도 1.2초까지만
    ]);
    await new Promise<void>((done) =>
      requestAnimationFrame(() => requestAnimationFrame(() => window.setTimeout(done, 0))));
    await invoke("sf_ready");
    debugLog(`READY 요청 완료 ${Math.round(performance.now() - t0)}ms`);
  } catch (err) {
    debugLog("READY 실패 " + String(err));
  }
})();
