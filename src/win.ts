import { useEffect, useState } from "react";
/* Tauri 창 제어. 브라우저(vite dev)에서는 조용히 무시한다. */
async function w() {
  try {
    const m = await import("@tauri-apps/api/window");
    return m.getCurrentWindow();
  } catch {
    return null;
  }
}
export const winMinimize = async () => (await w())?.minimize();
export const winToggleMax = async () => (await w())?.toggleMaximize();

/* 창이 최대화돼 있는지 — 최대화 버튼 아이콘을 ㅁ / 겹친 ㅁ 로 바꾸는 데 쓴다 (2026-10-14 사용자 신고:
   전체화면이든 아니든 늘 ㅁ 였다). 크기가 바뀔 때마다 다시 묻는다 (더블클릭·창 끌기·Win+↑ 모두 포함). */
export function useWinMaximized(): boolean {
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    let stop: (() => void) | null = null;
    let dead = false;
    void (async () => {
      const win = await w();
      if (!win || dead) return;
      const sync = () => { void win.isMaximized().then((v) => { if (!dead) setMaximized(v); }).catch(() => {}); };
      sync();
      const un = await win.onResized(sync);
      if (dead) un(); else stop = un;
    })();
    return () => { dead = true; stop?.(); };
  }, []);
  return maximized;
}
export const winClose = async () => (await w())?.close();
