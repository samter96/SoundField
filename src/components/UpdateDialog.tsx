import { useEffect, useRef, useState } from "react";
import {
  onUpdateDone, onUpdateProgress, updateApply, updateCancel, updateCheck, updateDownload,
  type UpdateInfo, type UpdateProgress,
} from "../backend";
import { t } from "../i18n";

/* ── 자동 업데이트 창 (2026-10-14 준비 — 사용자 결정) ─────────────────────────────
   켤 때 새 버전이 있으면 "업데이트 / 나중에" 를 묻는다. 업데이트를 고르면 바뀐 파일만
   받는 진행 막대를 보여 주고, 다 받으면 설정을 저장한 뒤 앱이 다시 시작된다
   (교체는 앱이 꺼진 뒤 도우미가 한다 — src-tauri/src/updater.rs).
   올리는 곳(update.cfg)이 비어 있으면 확인이 늘 null 이라 이 창은 영영 안 뜬다. */

type Phase = "ask" | "down" | "restart" | "error";

const toMb = (bytes: number) => (bytes / 1048576).toFixed(bytes < 10 * 1048576 ? 1 : 0);

/* 같은 버전 번호를 다시 올린 경우에는 빌드 시각으로 구분해 보여 준다 ("2.2.0+20261014T1015Z") */
const label = (info: UpdateInfo) =>
  info.version === info.current ? `${info.version} (${info.build.split("+")[1] ?? info.build})` : info.version;

export function UpdateDialog({ onBeforeRestart, onOpenChange }: {
  /** 다시 시작 전에 설정을 저장한다 (App 의 flushConfigSave) */
  onBeforeRestart: () => Promise<void>;
  /** 창이 떠 있는 동안 앱 단축키(스페이스 재생 등)를 막도록 알린다 */
  onOpenChange?: (open: boolean) => void;
}) {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [phase, setPhase] = useState<Phase>("ask");
  const [progress, setProgress] = useState<UpdateProgress>({ done: 0, total: 0 });
  const [error, setError] = useState("");
  const cancelledRef = useRef(false);

  useEffect(() => { onOpenChange?.(info !== null); }, [info]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    /* 시작 직후의 트리·검색 준비와 겹치지 않게 조금 뒤에 확인한다 */
    const timer = window.setTimeout(() => {
      void updateCheck().then((value) => { if (value) setInfo(value); });
    }, 2500);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const stopProgress = onUpdateProgress(setProgress);
    const stopDone = onUpdateDone((value) => {
      if (!value.ok) {
        if (cancelledRef.current) { setInfo(null); return; }   // 취소는 조용히 닫는다
        setError(value.error);
        setPhase("error");
        return;
      }
      setPhase("restart");
      void (async () => {
        /* 저장이 멈춰도 업데이트까지 묶이지 않게 잠깐만 기다린다 (종료 확인창과 같은 규칙) */
        try {
          await Promise.race([onBeforeRestart(), new Promise<void>((r) => window.setTimeout(r, 1200))]);
        } catch { /* 저장 실패로 업데이트를 막지 않는다 */ }
        try { await updateApply(); } catch (e) { setError(String(e)); setPhase("error"); }
      })();
    });
    return () => { stopProgress(); stopDone(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = () => {
    if (!info) return;
    cancelledRef.current = false;
    setProgress({ done: 0, total: info.bytes });
    setPhase("down");
    updateDownload().catch((e) => { setError(String(e)); setPhase("error"); });
  };
  const cancel = () => { cancelledRef.current = true; void updateCancel(); };
  const later = () => setInfo(null);

  /* Esc = 나중에(받는 중이면 취소), Enter = 업데이트 */
  useEffect(() => {
    if (!info) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (phase === "ask" || phase === "error") later();
        else if (phase === "down") cancel();
      } else if (event.key === "Enter" && phase === "ask") {
        event.preventDefault();
        start();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [info, phase]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!info) return null;
  const pct = progress.total > 0 ? Math.min(100, (progress.done / progress.total) * 100) : 0;
  return (
    <div className="scrim">
      <div className="modal sm" role="dialog" aria-modal="true">
        <div className="modal-head"><span className="modal-title">{t("업데이트")}</span></div>
        <div className="modal-body">
          {phase === "ask" && (
            <div className="confirm-text pre">
              {t("새 버전이 있습니다.")}{"\n\n"}
              {t("지금 {0} → 새 버전 {1}", info.current, label(info))}{"\n"}
              {t("바뀐 파일 {0}개 · 받을 크기 {1} MB", info.files, toMb(info.bytes))}{"\n\n"}
              {t("다 받으면 SoundField 가 다시 시작됩니다.")}
            </div>
          )}
          {phase === "down" && (
            <div className="update-progress">
              <div className="confirm-text">
                {t("받는 중… {0} / {1} MB", toMb(progress.done), toMb(progress.total))}
              </div>
              <div className="progress"><div style={{ width: `${pct}%` }} /></div>
            </div>
          )}
          {phase === "restart" && (
            <div className="confirm-text">{t("다시 시작하는 중…")}</div>
          )}
          {phase === "error" && (
            <div className="confirm-text pre">
              {t("업데이트를 받지 못했습니다. 지금 버전으로 계속 사용합니다.")}{"\n\n"}{error}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <div className="grow" />
          {phase === "ask" && (
            <>
              <button className="btn btn-primary" autoFocus onClick={start}>{t("업데이트")}</button>
              <button className="btn" onClick={later}>{t("나중에")}</button>
            </>
          )}
          {phase === "down" && <button className="btn" onClick={cancel}>{t("취소")}</button>}
          {phase === "error" && <button className="btn btn-primary" autoFocus onClick={later}>{t("확인")}</button>}
        </div>
      </div>
    </div>
  );
}
