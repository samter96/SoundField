/* 원본은 QFileDialog 로 폴더를 고른다. Tauri 에서는 dialog 플러그인을 쓴다.
   브라우저(vite dev)로 열었을 때는 플러그인이 없어 조용히 null 을 반환한다. */
export async function pickLibraryFolder(): Promise<string | null> {
  const { debugLog } = await import("./backend");
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const picked = await open({ directory: true, multiple: false, title: "라이브러리 폴더 선택" });
    return typeof picked === "string" ? picked : null;
  } catch (err) {
    /* 조용히 null 을 반환하면 "버튼을 눌러도 아무 반응이 없다"로만 보인다 → 사유를 남긴다 */
    debugLog("PICK error=" + String(err));
    return null;
  }
}
