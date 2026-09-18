import { getVersion } from "@tauri-apps/api/app";
import { useEffect, useState } from "react";

/* ── 앱 버전 ────────────────────────────────────────────────────────────────
   ⚠ 버전을 화면에 **손으로 적어 두지 않는다.**
   예전에는 Header 와 Splash 에 "2.0.0" 을 글자로 박아 놨는데, 실제 버전을
   2.1.0 으로 올린 뒤에도 화면에는 계속 2.0.0 이 보였다 (사용자 보고 2026-09-07:
   "버전을 업데이트했으면 앱 내 표시도 다 동시에 업데이트해야지").

   여기서는 실행 중인 exe 에 박힌 버전을 물어본다. 그 값의 출처는
   src-tauri/tauri.conf.json 의 version 이고, 설치 파일(soundfield_installer.iss)
   과 Cargo.toml 도 같은 번호를 쓴다. 그래서 버전을 올릴 때 화면 문구를 따로
   고칠 일이 없다.

   물어보는 데 시간이 조금 걸리므로, 값을 알기 전에는 **빈 문자열**을 준다.
   틀린 번호를 잠깐이라도 보여주는 것보다 잠깐 안 보이는 편이 낫다. */

let cached: string | null = null;
let inflight: Promise<string> | null = null;

/** 앱 버전 문자열 ("2.1.0"). 아직 모르면 빈 문자열. */
export function useAppVersion(): string {
  const [version, setVersion] = useState(cached ?? "");
  useEffect(() => {
    if (cached !== null) return;
    inflight ??= getVersion().then((value) => {
      cached = value;
      return value;
    }).catch(() => {
      /* 브라우저에서 화면만 열어 본 경우 등 — 물어볼 상대가 없다. */
      cached = "";
      return "";
    });
    let alive = true;
    void inflight.then((value) => { if (alive) setVersion(value); });
    return () => { alive = false; };
  }, []);
  return version;
}
