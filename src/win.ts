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
export const winClose = async () => (await w())?.close();
