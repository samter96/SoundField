import { useEffect } from "react";
/* ── 중앙 로더 (원본 main_window.py:_CenterLoaderOverlay 912) ────────────────
   **동작 정책은 원본 그대로다:**
   - 부모 전체를 덮어 **입력을 막는다** (UI 가 실질적으로 못 쓰는 작업 전용)
   - 오버레이가 마우스뿐 아니라 **키보드·휠까지** 막는다 (원본은 이벤트를 accept)
   - start(key,msg)/stop(key) 의 ref-count 구조 → 겹쳐도 마지막 작업 하나만 뜬다
   - 진행률을 모르는 구간이면 무한 게이지로 둔다
   원본 사용처: 폴더 트리 갱신, 검색 색인 마무리/재생성, 라이브러리 제거,
   인덱싱, 변경 감지.

   **크기와 구성은 원본 그대로 둔다** (사용자 지시 2026-09-02):
   패널 300x150 (게이지 있으면 176), 세로 배치 — 로딩 표현 / 메시지 / 진행 텍스트 /
   게이지 220x8. 원본의 회전 링 자리에 **파형 박동**(좌→우)이 들어간다.
   재질만 스플래시와 같은 결로 올렸다 — 라디얼 바닥 + 미세 격자 + 대각 시트 +
   헤어라인 + 액센트 글로우, 메시지 아래 짧은 액센트 밑줄. */

export type LoaderTask = { key: string; msg: string; progress?: string; gauge?: number | null };

export function CenterLoader({ tasks }: { tasks: LoaderTask[] }) {
  /* 원본은 오버레이가 keyPressEvent/wheelEvent 를 그대로 accept() 해서
     키보드와 휠까지 막는다 (main_window.py:1016). 마우스만 막으면 로딩 중
     단축키/휠이 그대로 먹혀 원본과 동작이 달라진다. */
  useEffect(() => {
    if (!tasks.length) return;
    const swallow = (event: Event) => { event.preventDefault(); event.stopPropagation(); };
    window.addEventListener("keydown", swallow, true);
    window.addEventListener("wheel", swallow, { capture: true, passive: false });
    return () => {
      window.removeEventListener("keydown", swallow, true);
      window.removeEventListener("wheel", swallow, true);
    };
  }, [tasks.length]);

  if (!tasks.length) return null;
  /* 원본은 마지막에 start 된 작업의 메시지를 보여준다 */
  const task = tasks[tasks.length - 1];
  const gauge = task.gauge;
  const showGauge = gauge !== undefined && gauge !== null;
  const known = typeof gauge === "number" && gauge >= 0;
  const pct = known ? Math.max(0, Math.min(100, gauge)) : 0;

  return (
    <div className="center-loader" role="status" aria-live="polite">
      <div className={"cl-card" + (showGauge ? " has-gauge" : "")}>
        <span className="cl-grid" aria-hidden="true" />
        <span className="cl-sheen" aria-hidden="true" />

        {/* 원본 회전 링 자리 — 좌→우로 흐르는 파형 박동 */}
        <div className="cl-pulse" aria-hidden="true">
          <span className="cl-baseline" />
          {Array.from({ length: 9 }, (_, i) => (
            <i key={i} style={{ ["--i" as string]: String(i) }} />
          ))}
        </div>

        <div className="cl-title">{task.msg}</div>
        <span className="cl-underline" aria-hidden="true" />
        {/* 원본은 메시지 아래 진행 텍스트만 쓰고 퍼센트를 따로 찍지 않는다
            (진행 텍스트 안에 이미 수치가 들어온다). 게이지만 있고 텍스트가
            없는 구간에서는 확정 퍼센트를 대신 보여준다. */}
        {(task.progress || showGauge) && (
          <div className="cl-sub">
            {task.progress || (known ? `${pct}%` : "진행 중")}
          </div>
        )}
        {showGauge && (
          <div className="cl-gauge">
            <div className={"cl-gauge-fill" + (known ? "" : " indet")}
                 style={known ? { width: `${pct}%` } : undefined} />
          </div>
        )}
      </div>
    </div>
  );
}
