import { useEffect, useRef, useState } from "react";
import { loadHidden, onAdminOpProgress, revealInExplorer,
         type AdminRequest, type HiddenResult } from "../backend";

/* ── 검색 제외 목록 (원본 _open_suppressed_manager, main_window.py:3421) ────────
   제목 "검색 제외 목록 — N건", 780x480.
   안내문(10px, text_secondary, 줄바꿈 허용):
     "검색에서 제외된 파일들입니다 (디스크의 파일·인덱스는 그대로).
      체크 후 복원하면 즉시 검색 결과에 다시 나타납니다. Shift/Ctrl+클릭 다중 선택 시
      자동으로 체크됩니다.
      [중복 아님 · 미복원] = 상대 사본이 삭제되어 더 이상 중복이 아니지만, 복원하지
      않기로 한 항목입니다."
   검색창 placeholder "검색 (파일명/경로 — 전체 목록에서 찾음)" — 표시 상한과 무관하게
     숨김 **전체**에서 찾는다. 입력 250ms 디바운스로 DB 재조회.
   표시 상한 _SUPPRESSED_LIST_CAP = 2000 (2만 개를 한 번에 올리면 프리즈/크래시).
   목록 아래 요약(10px):
     검색어 있음 → "일치 N건 · 표시 M건" (+ 상한에 걸리면 " (상한 2,000)")
     없음        → "표시 M건" (+ " · 미표시 K건 (상한 2,000 — 검색은 전체에서 찾음)")
   [중복 아님 · 미복원] 태그는 항목 앞에 붙고 색은 미완료색(#ffa64d)이다.
   우클릭: 탐색기에서 보기 / 경로 복사 / 이 항목만 즉시 복원 / 같은 폴더 전부 체크.
   하단: [체크 항목 복원 (N)] [전체 복원] ... [닫기]
   ⚠ 복원(unhide)은 index.db 쓰기라 PoC 에서는 안내만 한다. 목록·검색·체크는 동작한다. */

const LIST_CAP = 2000;
const ORPHAN_TAG = "[중복 아님 · 미복원]  ";

/* runAdmin 은 App 에서 **가드가 씌워진 것**을 받는다 — backend 의 것을 직접 부르면
   중복 실행 가드를 우회한다 (Settings.tsx 와 같은 이유). */
export function SuppressedManager({ onClose, runAdmin, onBusyChange }: {
  onClose: () => void;
  runAdmin: (req: AdminRequest) => Promise<Record<string, unknown>>;
  /** 복원 중임을 부모에 알린다 — 부모의 닫기/저장 잠금과 App 의 Ctrl+P 차단에 쓰인다
      (조사 M02: 자식 작업 중인데 부모가 닫히던 문제). */
  onBusyChange?: (busy: boolean) => void;
}) {
  const [term, setTerm] = useState("");
  const [data, setData] = useState<HiddenResult>({ rows: [], matched: 0, total: 0 });
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [ctxAt, setCtxAt] = useState<{ x: number; y: number; path: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState<string | null>(null);
  /* ── 복원 중 정책 (사용자 지시 2026-09-08) ──────────────────────────────────
     복원은 DB 쓰기이고 수십만 건이 걸릴 수 있다. 검색 제외와 **같은 정책**을 쓴다:
       · 진행 상황을 진행바 + 글자로 보여준다 (사이드카가 500개 묶음마다 알려준다)
       · 도는 동안 창을 닫지 못하고 버튼도 잠긴다
     ⚠ 닫는 길이 넷이다 — [닫기] / [✕] / 바깥 클릭 / (부모의 Esc). 한 곳(guardedClose)
       에서 막아 넷 다 덮는다. 개별 버튼에 조건을 달지 말 것.
     닫히면 결과 표시·목록 갱신이 유실돼 "복원했는데 목록에 그대로" 로 보인다. */
  const [busy, setBusy] = useState("");
  const [prog, setProg] = useState<{ done: number; total: number } | null>(null);

  /* 원본: 검색 입력 250ms 디바운스 후 DB 재조회 */
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadHidden(term, LIST_CAP).then(setData);
    }, term ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [term]);

  const loaded = data.rows.length;
  const summary = term.trim()
    ? `일치 ${data.matched.toLocaleString()}건 · 표시 ${loaded.toLocaleString()}건`
      + (data.matched > loaded ? ` (상한 ${LIST_CAP.toLocaleString()})` : "")
    : `표시 ${loaded.toLocaleString()}건`
      + (data.total > loaded
          ? ` · 미표시 ${(data.total - loaded).toLocaleString()}건 `
            + `(상한 ${LIST_CAP.toLocaleString()} — 검색은 전체에서 찾음)`
          : "");

  const toggle = (path: string) => setChecked((prev) => {
    const next = new Set(prev);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    return next;
  });

  /* ── 여러 개 고르기 (조사 D07) ─────────────────────────────────────────────
     화면에 "Shift/Ctrl+클릭 다중 선택 시 자동으로 체크됩니다" 라고 적어 놨는데
     실제로는 누른 한 줄만 켜졌다 (원본은 QListWidget 의 다중 선택을 그대로 쓴다,
     main_window.py:3538). 여기서는 목록이 그냥 div 라 직접 처리한다.
       · 그냥 클릭      : 그 줄만 (기준점도 여기로 옮긴다)
       · Ctrl+클릭      : 그 줄만 켜고 끄기 (기준점 이동)
       · Shift+클릭     : 기준점부터 이 줄까지 전부 켜기
     기준점(앵커)이 없거나 목록이 바뀌어 사라졌으면 그냥 클릭과 같이 다룬다. */
  const anchorRef = useRef<string | null>(null);
  const pickRow = (path: string, event: React.MouseEvent) => {
    const paths = data.rows.map((row) => row.path);
    const at = paths.indexOf(path);
    const from = anchorRef.current ? paths.indexOf(anchorRef.current) : -1;
    if (event.shiftKey && from >= 0 && at >= 0) {
      const lo = Math.min(from, at);
      const hi = Math.max(from, at);
      setChecked((prev) => {
        const next = new Set(prev);
        for (let i = lo; i <= hi; i += 1) next.add(paths[i]);
        return next;
      });
      return;
    }
    anchorRef.current = path;
    toggle(path);
  };
  const reload = () => { void loadHidden(term, LIST_CAP).then(setData); };
  /* 복원 계열 공용 실행기 — 진행 상황 구독 + 잠금 + 끝난 뒤 목록 갱신을 한자리에서.
     비슷한 작업이 또 생기면 이 함수를 쓸 것 (따로 만들면 정책이 갈린다). */
  const runRestore = (
    req: AdminRequest, label: string, total: number, doneText: (n: number) => string,
  ) => {
    setBusy(label);
    /* total 0 = 총량 미정 → 흐르는 진행바 (unhide_all 은 단일 UPDATE 라 진행률이 없다) */
    setProg({ done: 0, total });
    const stopProg = onAdminOpProgress((value) => {
      if (!String(value.op).startsWith("unhide")) return;
      setProg({ done: value.done, total: value.total });
    });
    void runAdmin(req).finally(() => {
      stopProg();
      setBusy("");
      setProg(null);
    }).then((result) => {
      const n = Number(result.count ?? 0);
      setNotice(result.success === false
        ? String(result.message || "복원 실패") : doneText(n));
      setChecked(new Set());
      reload();
    });
  };

  const restore = (paths: string[]) =>
    runRestore({ op: "unhide_paths", paths }, "복원", paths.length,
               (n) => `${n.toLocaleString()}건을 복원했습니다.`);

  const busyText = prog
    ? (prog.total > 0
        ? `${busy} 중... ${prog.done.toLocaleString()} / ${prog.total.toLocaleString()}`
          + ` (${Math.floor(prog.done * 100 / Math.max(1, prog.total))}%)`
        : `${busy} 중...`)
    : "";

  /* 작업 상태를 부모에 알린다 — 위 onBusyChange 주석 참고 */
  useEffect(() => { onBusyChange?.(Boolean(busy)); }, [busy, onBusyChange]);
  /* ⚠ 이 창은 Modal 이 아니라 직접 그린 겹침 창이라 Esc 를 스스로 받아야 한다.
     안 받으면 부모(환경설정)의 Modal 이 Esc 를 먹어 **부모까지 닫힌다** (조사 M02). */
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      guardedCloseRef.current();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  const guardedClose = () => {
    if (busy) {
      setNotice(`${busy} 처리 중에는 창을 닫을 수 없습니다. 끝날 때까지 기다려 주세요.`);
      return;
    }
    onClose();
  };
  /* 위 Esc 리스너는 한 번만 등록하므로 최신 함수를 ref 로 본다 */
  const guardedCloseRef = useRef(guardedClose);
  guardedCloseRef.current = guardedClose;

  return (
    <div className="scrim inner" onMouseDown={(event) => {
      if (event.target === event.currentTarget) guardedClose();
    }}>
      <div className="modal suppressed" role="dialog" aria-modal="true">
        <div className="modal-head">
          <span className="modal-title">검색 제외 목록 — {data.total.toLocaleString()}건</span>
          <button className="layout-dialog-x" aria-label="닫기" onClick={guardedClose}>✕</button>
        </div>
        <div className="modal-body">
          <div className="form-hint">
            검색에서 제외된 파일들입니다 (디스크의 파일·인덱스는 그대로).
            <br />체크 후 복원하면 즉시 검색 결과에 다시 나타납니다. Shift/Ctrl+클릭 다중 선택 시
            자동으로 체크됩니다.
            <br />[중복 아님 · 미복원] = 상대 사본이 삭제되어 더 이상 중복이 아니지만, 복원하지
            않기로 한 항목입니다.
          </div>

          <input className="input" placeholder="검색 (파일명/경로 — 전체 목록에서 찾음)"
                 value={term} onChange={(event) => setTerm(event.target.value)} />

          <div className="suppressed-list">
            {data.rows.map((row) => (
              <div key={row.path}
                   className={"suppressed-row" + (checked.has(row.path) ? " on" : "")}
                   data-tip={row.path}
                   onContextMenu={(event) => {
                     event.preventDefault();
                     setCtxAt({ x: event.clientX, y: event.clientY, path: row.path });
                   }}
                   /* Shift/Ctrl 을 함께 본다 — pickRow 주석 참고 (조사 D07) */
                   onClick={(event) => pickRow(row.path, event)}>
                <span className={"check" + (checked.has(row.path) ? " on" : "")}
                      role="checkbox" aria-checked={checked.has(row.path)} />
                <span className={"suppressed-path" + (row.orphan ? " orphan" : "")}>
                  {row.orphan ? ORPHAN_TAG + row.path : row.path}
                </span>
              </div>
            ))}
            {!data.rows.length && (
              <div className="blacklist-empty">검색 제외된 항목이 없습니다.</div>
            )}
          </div>

          <div className="form-hint">{summary}</div>

          {/* 진행바 — 검색 제외와 같은 모양(.dup-progress). 총량 미정이면 흐른다 */}
          {prog && (
            <div className="dup-progress">
              <div className={"dup-progress-bar" + (prog.total > 0 ? "" : " indet")}
                   style={prog.total > 0
                     ? { width: `${Math.min(100, (prog.done / prog.total) * 100)}%` }
                     : undefined} />
            </div>
          )}
        </div>
        <div className="modal-foot">
          {busyText && <span className="dup-status">{busyText}</span>}
          <button className="btn" disabled={!checked.size || !!busy}
                  onClick={() => restore([...checked])}>
            {checked.size ? `체크 항목 복원 (${checked.size.toLocaleString()})` : "체크 항목 복원"}
          </button>
          {/* 원본 _release_all: 전체 복원만 확인을 묻는다
              ("검색 제외된 N건 전부를 검색 결과에 복원합니다.\n진행할까요?").
              체크 항목 복원은 확인 없이 바로 실행한다. */}
          <button className="btn" disabled={!!busy}
                  onClick={() => setConfirmAll(
                    `검색 제외된 ${data.total.toLocaleString()}건 전부를 검색 결과에 복원합니다.\n진행할까요?`)}>전체 복원</button>
          <div className="grow" />
          <button className="btn btn-primary" disabled={!!busy}
                  data-tip={busy ? `${busy} 처리 중에는 닫을 수 없습니다` : undefined}
                  onClick={guardedClose}>닫기</button>
        </div>

        {ctxAt && (
          <>
            <div className="ctx-scrim" onMouseDown={() => setCtxAt(null)} />
            <div className="ctxmenu" style={{ left: ctxAt.x, top: ctxAt.y }}>
              <button className="ctxitem"
                      onClick={() => { void revealInExplorer(ctxAt.path); setCtxAt(null); }}>
                탐색기에서 보기
              </button>
              <button className="ctxitem"
                      onClick={() => {
                        /* 원본 결과표 메뉴와 같은 항목 (요청으로 추가) */
                        void navigator.clipboard?.writeText(
                          ctxAt.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || ctxAt.path);
                        setCtxAt(null);
                      }}>
                이 파일 이름 복사
              </button>
              <button className="ctxitem"
                      onClick={() => {
                        void navigator.clipboard?.writeText(ctxAt.path);
                        setCtxAt(null);
                      }}>
                경로 복사
              </button>
              <div className="ctxsep" />
              <button className="ctxitem"
                      onClick={() => { const path = ctxAt.path; setCtxAt(null); restore([path]); }}>
                이 항목만 즉시 복원
              </button>
              <button className="ctxitem"
                      onClick={() => {
                        /* 원본: 같은 폴더의 항목을 모두 체크한다 (대소문자 무시) */
                        const dir = ctxAt.path.replace(/[\\/][^\\/]*$/, "").toLowerCase();
                        setChecked((prev) => {
                          const next = new Set(prev);
                          for (const row of data.rows) {
                            if (row.path.replace(/[\\/][^\\/]*$/, "").toLowerCase() === dir)
                              next.add(row.path);
                          }
                          return next;
                        });
                        setCtxAt(null);
                      }}>
                같은 폴더 전부 체크
              </button>
            </div>
          </>
        )}

        {confirmAll && (
          <div className="scrim inner"
               /* ⚠ 대상 검사가 없으면 **버튼을 누르는 순간(mousedown)**
                  창이 닫혀 클릭이 성립하지 않는다 — 제거/복원 버튼이
                  전부 죽어 있었다 (로그 실측: 확인창은 열리는데
                  "확인 누름" 이 한 번도 안 찍혔다). 바깥을 눌렀을 때만 닫는다. */
               onMouseDown={(event) => {
                 if (event.target === event.currentTarget) setConfirmAll(null);
               }}>
            <div className="modal sm" role="dialog" aria-modal="true">
              <div className="modal-head"><span className="modal-title">전체 복원</span></div>
              <div className="modal-body"><div className="confirm-text pre">{confirmAll}</div></div>
              <div className="modal-foot">
                <div className="grow" />
                <button className="btn btn-primary"
                        onClick={() => {
                          setConfirmAll(null);
                          /* total 0 → 총량 미정 (unhide_all 은 단일 UPDATE) */
                          runRestore({ op: "unhide_all" }, "전체 복원", 0,
                                     (n) => `${n.toLocaleString()}건을 복원했습니다.`);
                        }}>예</button>
                <button className="btn" onClick={() => setConfirmAll(null)}>아니오</button>
              </div>
            </div>
          </div>
        )}

        {notice && (
          <div className="scrim inner"
               /* ⚠ 대상 검사가 없으면 **버튼을 누르는 순간(mousedown)**
                  창이 닫혀 클릭이 성립하지 않는다 — 제거/복원 버튼이
                  전부 죽어 있었다 (로그 실측: 확인창은 열리는데
                  "확인 누름" 이 한 번도 안 찍혔다). 바깥을 눌렀을 때만 닫는다. */
               onMouseDown={(event) => {
                 if (event.target === event.currentTarget) setNotice(null);
               }}>
            <div className="modal sm" role="dialog" aria-modal="true">
              <div className="modal-head"><span className="modal-title">복원</span></div>
              <div className="modal-body"><div className="confirm-text pre">{notice}</div></div>
              <div className="modal-foot">
                <div className="grow" />
                <button className="btn btn-primary" onClick={() => setNotice(null)}>확인</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
