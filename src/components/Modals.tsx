import { useEffect, useState } from "react";
import { Modal } from "./Modal";
import { fmtSize, type Lib } from "../data";
import { IcoCheck } from "../icons";
import { Settings } from "./Settings";
import { loadIncomplete, removeBlacklistPath, revealInExplorer,
         type AdminRequest, type IncompleteResult } from "../backend";
import type { UserConfig } from "../config";
import { Dropdown } from "./Dropdown";
import { t } from "../i18n";

/* 한 창 안에서 열리는 모달 모음. 원본은 별도 QDialog 지만 제목/크기/열 구성/문구/
   버튼 순서/툴팁은 원본 그대로다.
     status        → LibraryStatusDialog   (main_window.py:2305)   "현재 라이브러리"
     failed:<root> → FailedPendingDialog   (main_window.py:1838)   "미완료 항목 — {폴더}"
     blacklist     → BlacklistStatusDialog (blacklist_panel.py:285) "블랙리스트 상세"
     settings      → SettingsDialog        (main_window.py:3100)   "환경설정"
   중복 검수는 원본에서도 별도 창이 아니라 환경설정의 탭이다. */

type Props = {
  id: string;
  onClose: () => void;
  /** 자식 창에서 부모(라이브러리 현황)로 돌아가기 */
  onBack?: () => void;
  onOpenFailed: (root: string) => void;
  /** 실제 DB 에서 읽은 라이브러리 루트 (없으면 더미) */
  libraries?: Lib[];
  /** 실제 DB 에서 읽은 블랙리스트 (0개면 0개로 표시 — 더미로 대체하지 않는다) */
  blacklistEntries?: Array<{ path: string; description: string }> | null;
  config: UserConfig;
  onSaveConfig: (next: UserConfig) => void;
  onDataChanged?: () => void;
  onOrphans?: (ids: string[]) => void;
  onRunAdmin?: (req: AdminRequest) => Promise<Record<string, unknown>>;
  /** 창 안에서 DB 쓰기가 도는 중임을 App 에 알린다 — App 이 Ctrl+P·뒤로 같은
      **창 밖 경로**를 막는 데 쓴다 (조사 M01/M03). */
  onBusyChange?: (busy: boolean) => void;
};

/* 재시도 결과 문구 — 원본 _show_retry_result (main_window.py:7032) 의 사유
   한글 표와 줄 구성을 그대로 옮긴 것이다 (조사 I05). 사유는 많이 난 순으로
   8종까지 보여주고 나머지는 개수만 알린다. */
const RETRY_REASON_KOR: Array<[string, string]> = [
  ["Unsupported format", "지원하지 않는 파일 형식"],
  ["FileNotFoundError", "파일을 찾을 수 없음"],
  ["PermissionError", "접근 권한 없음"],
  ["Timeout", "분석 시간 초과 (대용량/네트워크 지연)"],
  ["Ignored sidecar file", "무시된 사이드카 파일"],
  ["Unknown extraction error", "알 수 없는 추출 오류"],
  ["OSError", "OS 입출력 오류"],
  ["EOFError", "파일 끝(EOF) 오류 (손상 가능성)"],
  ["ValueError", "데이터 형식 오류"],
];

function retryReasonText(reason: string) {
  for (const [eng, kor] of RETRY_REASON_KOR) if (reason.includes(eng)) return kor;
  return reason;
}

function retrySummary(result: Record<string, unknown>): string {
  const info = result.retry as
    { success?: number; failed?: number; reasons?: Array<[string, number]> } | undefined;
  if (!info) return "재분석을 완료했습니다.";
  const ok = Number(info.success || 0);
  const failed = Number(info.failed || 0);
  if (!ok && !failed) return "재분석 대상이 없습니다.";
  const lines = [`성공: ${ok.toLocaleString()}개`, `실패: ${failed.toLocaleString()}개`];
  const reasons = info.reasons ?? [];
  if (reasons.length) {
    lines.push("", "실패 사유:");
    for (const [err, count] of reasons.slice(0, 8))
      lines.push(`  · ${retryReasonText(String(err))} — ${Number(count).toLocaleString()}개`);
    if (reasons.length > 8) lines.push(`  · 그 외 ${reasons.length - 8}종`);
  }
  return lines.join("\n");
}

export function Modals({ onBack, id, onClose, onOpenFailed, libraries, blacklistEntries,
                         config, onSaveConfig, onDataChanged, onOrphans,
                         onRunAdmin, onBusyChange }: Props) {
  /* onRunAdmin 은 App.runDialogAdmin — 중복 실행 가드가 씌워져 있다.
     없을 리 없지만, 없으면 DB 쓰기를 하지 않는 편이 안전하다 (거절 결과 반환). */
  if (id === "settings") {
    return <Settings onClose={onClose} config={config} onSave={onSaveConfig}
                     onBusyChange={onBusyChange}
                     runAdmin={onRunAdmin ?? (async () => ({
                       success: false, message: "작업 실행기가 연결되지 않았습니다" }))} />;
  }
  if (id === "status") {
    return <Status onClose={onClose} onOpenFailed={onOpenFailed} libraries={libraries}
                   onDataChanged={onDataChanged} onOrphans={onOrphans}
                   onBusyChange={onBusyChange}
                   onRunAdmin={onRunAdmin} />;
  }
  if (id.startsWith("failed:")) return <Failed onClose={onClose} onBack={onBack} root={id.slice(7)}
                                               onBusyChange={onBusyChange}
                                                   onDataChanged={onDataChanged}
                                                   onOrphans={onOrphans}
                                                   onRunAdmin={onRunAdmin} />;
  if (id === "blacklist") return <Blacklist onClose={onClose} entries={blacklistEntries}
                                                onDataChanged={onDataChanged} />;
  if (id === "dupes") return <Dupes onClose={onClose} />;
  return null;
}

/* ⚠ backend 의 runAdmin 으로 폴백하지 않는다 — App.guardAdmin(중복 실행 가드 +
   메타 분석 양보 확인)을 우회한다. App 이 onRunAdmin 을 항상 넘기지만, 없으면
   실행하지 않는 편이 안전하다. */
const NO_ADMIN = async (): Promise<Record<string, unknown>> =>
  ({ success: false, message: "작업 실행기가 연결되지 않았습니다" });

/* 재시도/제거가 막힌 이유 — PoC 는 원본 index.db 를 읽기 전용으로만 연다 */
const BLOCKED_MSG = "PoC 는 인덱스 DB 를 읽기 전용으로 열어 이 작업을 실행하지 않습니다.";

/* ── 현재 라이브러리 (원본 LibraryStatusDialog) ──
   창 제목 "현재 라이브러리", 920x360, 6열 상태/경로/파일 수/대기/실패/마지막 스캔.
   경로 열만 늘어나고 행은 교대 배경. 행 더블클릭 → 미완료 항목 상세.
   하단: 선택 라이브러리 상세 보기… / 실패 전체 재시도 / 실패 항목 제거 / 닫기
   (PoC 가 임의로 넣었던 진행률 패널과 "목록 내보내기" 는 원본에 없어 제거했다.) */
function Status({ onClose, onOpenFailed, libraries, onDataChanged, onOrphans, onRunAdmin,
                 onBusyChange }: {
  onClose: () => void;
  onBusyChange?: (busy: boolean) => void;
  onOpenFailed: (root: string) => void;
  libraries?: Lib[];
  onDataChanged?: () => void;
  onOrphans?: (ids: string[]) => void;
  onRunAdmin?: (req: AdminRequest) => Promise<Record<string, unknown>>;
}) {
  const rows = libraries ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  /* 원본 QMessageBox.information/question 대체 — 문구를 원본과 같게 쓴다 */
  const [notice, setNotice] = useState<{ title: string; text: string } | null>(null);
  const [confirmText, setConfirmText] = useState<{ title: string; text: string } | null>(null);
  /* ── 작업 중 잠금 + 닫기 차단 (사용자 지시 2026-09-08 — DB 를 쓰는 창은 모두 같은
     정책) ─────────────────────────────────────────────────────────────────────
     여기는 잠금이 아예 없었다. 실패 재시도/제거는 DB 쓰기라 수 초 이상 걸릴 수
     있는데, 그동안 버튼을 다시 누를 수 있었고 창도 닫을 수 있었다. 닫으면 작업은
     계속 도는데 결과 안내와 개수 갱신(onDataChanged)이 유실된다.
     ⚠ 닫는 길이 넷이다 — [닫기] / [✕] / Esc / 바깥 클릭. Modal 이 넷 다 onClose 로
       보내므로 guardedClose 한 곳에서 막으면 전부 덮인다. */
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  /* 작업 상태를 App 에 올린다 — 창 밖 경로(Ctrl+P 등) 차단에 필요하다 */
  useEffect(() => { onBusyChange?.(Boolean(busyLabel)); }, [busyLabel, onBusyChange]);
  const guardedClose = () => {
    if (busyLabel) {
      setNotice({ title: "처리 중",
                  text: `${busyLabel} — 끝날 때까지 창을 닫을 수 없습니다.` });
      return;
    }
    onClose();
  };
  /* 행 더블클릭도 다른 창으로 **교체**하는 길이므로 같이 막는다 (조사 M03) */
  const guardedOpenFailed = (path: string) => {
    if (busyLabel) {
      setNotice({ title: "처리 중",
                  text: `${busyLabel} — 끝날 때까지 다른 화면으로 넘어갈 수 없습니다.` });
      return;
    }
    onOpenFailed(path);
  };
  const current = rows.find((l) => l.path === selected) ?? null;
  /* 원본 _selected_failed_count: 표에 **표시된 값**을 재사용한다 (재조회하지 않음) */
  const selectedFailed = Math.max(0, current?.failed ?? 0);

  /* 원본 _retry_failed_selected / _delete_failed_selected 의 앞단 검사 순서:
       ① 선택 없음 → "선택 필요" / "처리할 라이브러리를 선택하세요."
       ② 실패 0    → "실패 없음" / "선택한 라이브러리에 실패 항목이 없습니다."
       ③ 확인 질문 */
  const guard = () => {
    if (!selected) {
      setNotice({ title: "선택 필요", text: "처리할 라이브러리를 선택하세요." });
      return false;
    }
    if (selectedFailed <= 0) {
      setNotice({ title: "실패 없음", text: "선택한 라이브러리에 실패 항목이 없습니다." });
      return false;
    }
    return true;
  };
  const fmtScan = (at?: number | null) => {
    if (!at) return "-";
    const d = new Date(at * 1000);
    const p = (n: number) => String(n).padStart(2, "0");
    /* 원본 표기: %m-%d %H:%M */
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  return (
    <Modal
      title="현재 라이브러리"
      size="lg"
      /* 작업 중에는 닫기를 막는다 — guardedClose 주석 참고 */
      onClose={guardedClose}
      /* 위에 뜬 안내/확인창이 Esc 를 받아야 한다 (조사 M02) */
      blockEscape={Boolean(notice || confirmText)}
      foot={
        <>
          <button className="btn" disabled={!selected || Boolean(busyLabel)}
                  data-tip="선택한 라이브러리의 대기/실패 파일 리스트 — 체크박스로 선택 후 재시도(Phase2)/제거. 더블클릭으로도 열림."
                  onClick={() => selected && guardedOpenFailed(selected)}>
            선택 라이브러리 상세 보기…
          </button>
          <button className="btn" disabled={Boolean(busyLabel)}
                  data-tip="선택한 라이브러리의 모든 실패 항목을 대기 상태로 리셋 후 백그라운드 Phase2 즉시 실행 (Phase1 스캔 스킵)"
                  onClick={() => {
                    if (!guard()) return;
                    setConfirmText({
                      title: "실패 재시도",
                      text: `실패 항목 ${selectedFailed.toLocaleString()}개를 백그라운드에서 재분석할까요?\n`
                        + "(Phase1 스캔 스킵 · 잠금/취소/진행률 지원)",
                    });
                  }}>
            실패 전체 재시도
          </button>
          <button className="btn" disabled={Boolean(busyLabel)}
                  data-tip="선택한 라이브러리의 실패 항목을 인덱스에서 제거"
                  onClick={() => {
                    if (!guard()) return;
                    setConfirmText({
                      title: "실패 항목 제거",
                      text: `실패 항목 ${selectedFailed.toLocaleString()}개를 인덱스에서 제거할까요?\n`
                        + "실제 파일은 삭제되지 않습니다.\n\n"
                        + "※ 제거분은 '전체 갱신' 시 디스크에 파일이 있으면 다시 돌아옵니다 "
                        + "(영구 제외는 중복 숨김/블랙리스트).",
                    });
                  }}>
            실패 항목 제거
          </button>
          {busyLabel && <span className="dup-status">{busyLabel}…</span>}
          <div className="grow" />
          <button className="btn btn-primary" disabled={Boolean(busyLabel)}
                  data-tip={busyLabel ? `${busyLabel} — 끝날 때까지 닫을 수 없습니다` : undefined}
                  onClick={guardedClose}>닫기</button>
        </>
      }
    >
      <table className="mtable alt">
        <thead>
          <tr><th>상태</th><th>경로</th><th>파일 수</th><th>대기</th><th>실패</th><th>마지막 스캔</th></tr>
        </thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.id}
                className={selected === l.path ? "sel" : ""}
                onClick={() => setSelected(l.path)}
                onDoubleClick={() => guardedOpenFailed(l.path)}>
              <td>
                {/* 원본 _status_color: 경로 없음(missing)만 속 빈 원, 실패는 채운 원 */}
                <span className={"legend-dot " + (
                  l.state === "ok" ? "ok"
                  : l.state === "busy" ? "pending"
                  : l.state === "fail" ? "fail" : "missing")} />
              </td>
              <td className="strong" data-tip={l.path}>{l.path}</td>
              {/* -1 = 아직 카운트 계산 중 (원본도 워커 결과 도착 전에는 비워 둔다) */}
              <td className="num">{l.count < 0 ? "…" : l.count.toLocaleString()}</td>
              <td className="num">{(l.pending ?? 0) < 0 ? "…" : (l.pending ?? 0).toLocaleString()}</td>
              <td className="num">{(l.failed ?? 0) < 0 ? "…" : (l.failed ?? 0).toLocaleString()}</td>
              <td>{fmtScan(l.lastIndexedAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* 원본은 워커 결과가 오기 전 한 줄 placeholder 를 보여준다
          ("…" / "라이브러리 정보 로딩 중...") — 30개 루트 동기 조회로 3초 멈추던 것을
          백그라운드로 옮긴 흔적이다. */}
      {!rows.length && (
        <table className="mtable alt">
          <tbody>
            <tr><td>…</td><td>라이브러리 정보 로딩 중...</td><td /><td /><td /><td /></tr>
          </tbody>
        </table>
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
            <div className="modal-head"><span className="modal-title">{notice.title}</span></div>
            <div className="modal-body"><div className="confirm-text pre">{notice.text}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" onClick={() => setNotice(null)}>확인</button>
            </div>
          </div>
        </div>
      )}
      {confirmText && (
        <div className="scrim inner"
               /* ⚠ 대상 검사가 없으면 **버튼을 누르는 순간(mousedown)**
                  창이 닫혀 클릭이 성립하지 않는다 — 제거/복원 버튼이
                  전부 죽어 있었다 (로그 실측: 확인창은 열리는데
                  "확인 누름" 이 한 번도 안 찍혔다). 바깥을 눌렀을 때만 닫는다. */
               onMouseDown={(event) => {
                 if (event.target === event.currentTarget) setConfirmText(null);
               }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">{confirmText.title}</span></div>
            <div className="modal-body"><div className="confirm-text pre">{confirmText.text}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary"
                      onClick={() => {
                        const action = confirmText.title === "실패 재시도" ? "retry_scope" : "delete_scope";
                        const title = confirmText.title;
                        setConfirmText(null);
                        if (!selected || busyLabel) return;
                        setBusyLabel(`${title} 처리 중`);
                        void (onRunAdmin ?? NO_ADMIN)({ op: action, path: selected,
                          include_pending: false, include_failed: true })
                          .finally(() => setBusyLabel(null))
                          .then((result) => {
                            setNotice({ title, text: result.success === false
                              ? String(result.message || "작업 실패") : "작업을 완료했습니다." });
                            if (!onRunAdmin && Array.isArray(result.orphan_ids)) onOrphans?.(result.orphan_ids.map(String));
                            onDataChanged?.();
                          });
                      }}>예</button>
              <button className="btn" onClick={() => setConfirmText(null)}>아니오</button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

/* ── 미완료 항목 (원본 FailedPendingDialog, main_window.py:1828) ──
   제목 "미완료 항목 — {루트 폴더명}", 900x520.
   머리글: 경로(굵게) + "대기 N"(#7adfc4) · "실패 N"(#ff7878) — DB 전체 집계값이다
           (표에 실린 행 수가 아니다).
   재분석 배너: 인덱싱/재시도 중에만 "● 메타 재분석 진행 중 — {진행문구}"
           (문구 없으면 "● 메타 재분석 진행 중...") accent 12px 700.
   1줄: 필터 콤보(전체 (대기+실패)/대기만/실패만) · 전체 선택 · 선택 해제 ·
        (우) 카운트 라벨 — 불러오는 중엔 "불러오는 중...",
        끝나면 total > 표시분이면 "표시 N / 필터 전체 T — limit 초과분은
        [필터된 전체] 버튼으로 처리", 아니면 "표시 N (전체 T)".
   표 5열: 체크(32) / 상태(60) / 파일명(260) / 경로(380, 전체 경로) / 사유(남은 폭).
        줄바꿈 없음 + 가로 스크롤, Shift+휠 가로 스크롤(감도 delta*1.5).
        우클릭 → "파일 위치 열기".
        사유는 meta_error 를 한글로 바꿔 보여주고 툴팁에 원문을 남긴다.
   하단 1줄: ↻ 새로고침 / (우) 선택 항목 재시도 (Phase2) / 선택 항목 제거
   하단 2줄: "필터된 전체 (표시 limit 무시):" / (우) 필터된 전체 재시도 / 필터된 전체 제거
   ⚠ 재시도·제거는 index.db 쓰기라 PoC 에서 막혀 있다 — 누르면 안내만 한다. */

/* 원본 _on_fetch_done 의 ERR_MAP (main_window.py:2081) 그대로 */
const ERR_MAP: Array<[string, string]> = [
  ["Unsupported format", "지원하지 않는 파일 형식"],
  ["FileNotFoundError", "파일을 찾을 수 없음"],
  ["PermissionError", "접근 권한 없음"],
  ["Timeout", "분석 시간 초과 (대용량/네트워크 지연)"],
  ["Ignored sidecar file", "무시된 사이드카 파일"],
  ["Unknown extraction error", "알 수 없는 추출 오류"],
  ["OSError", "OS 입출력 오류"],
  ["EOFError", "파일 끝(EOF) 도달 오류 (손상된 파일 가능성)"],
  ["ValueError", "데이터 형식 오류"],
];

function reasonText(err: string, status: string) {
  if (!err) return status === "pending" ? "분석 대기 중" : "알 수 없는 오류";
  for (const [eng, kor] of ERR_MAP) if (err.includes(eng)) return kor;
  return err;
}


function Failed({ onClose, onBack, root, onDataChanged, onOrphans, onRunAdmin,
                 onBusyChange }: {
  onClose: () => void; onBack?: () => void; root: string; onDataChanged?: () => void;
  onBusyChange?: (busy: boolean) => void;
  onOrphans?: (ids: string[]) => void;
  onRunAdmin?: (req: AdminRequest) => Promise<Record<string, unknown>>;
}) {
  const [result, setResult] = useState<IncompleteResult | null>(null);
  const [filter, setFilter] = useState("전체 (대기+실패)");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState<{ title: string; text: string } | null>(null);
  const [confirmText, setConfirmText] = useState<string | null>(null);
  const [confirmMode, setConfirmMode] = useState<"selected" | "scope">("selected");
  /* ── 중복 실행 가드 + 즉시 피드백 (사용자 지시 2026-09-03) ────────────────────
     재시도/제거는 수 초가 걸린다. 예전에는 누른 직후 아무 표시가 없어 "동작을 안 한다"고
     느끼고 계속 누르게 됐다. 진행 중에는 (1) 배너를 띄우고 (2) 모든 실행 버튼을 잠근다.
     같은 작업이 겹쳐 큐에 쌓이는 일 자체를 막는다. */
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const runGuarded = (label: string, req: AdminRequest,
                      done: (result: Record<string, unknown>) => void) => {
    if (busyLabel) return;                 // 이미 진행 중이면 무시 (중복 실행 방지)
    setBusyLabel(label);
    void (onRunAdmin ?? NO_ADMIN)(req)
      .then(done)
      .finally(() => setBusyLabel(null));
  };
  /* ── 작업 중에는 창을 닫지 못한다 (사용자 지시 2026-09-08 — 검색 제외·제외 관리와
     같은 정책) ────────────────────────────────────────────────────────────────
     버튼 잠금은 이미 있었지만 **닫기는 열려 있었다.** 닫으면 작업은 계속 도는데
     결과 안내·목록 갱신·부모 개수 갱신(onDataChanged)이 유실돼, 끝나도 화면에는
     옛 숫자가 남는다.
     ⚠ 닫는 길이 넷이다 — [닫기] 버튼 / [✕] / Esc / 바깥 클릭. Modal 이 넷 다
       onClose 로 보내므로 여기 한 곳에서 막으면 전부 덮인다. */
  /* 작업 상태를 App 에 올린다 — 창 밖 경로(Ctrl+P 등) 차단에 필요하다 */
  useEffect(() => { onBusyChange?.(Boolean(busyLabel)); }, [busyLabel, onBusyChange]);
  const guardedClose = () => {
    if (busyLabel) {
      setNotice({ title: "처리 중",
                  text: `${busyLabel} — 끝날 때까지 창을 닫을 수 없습니다.` });
      return;
    }
    onClose();
  };
  /* ⚠ [뒤로] 도 닫기와 **같은 길**이다 (다른 창으로 교체된다). 여기서 막지 않으면
     작업 중에 화면이 바뀌어 완료 안내·선택 상태가 유실된다 (조사 M03). */
  const guardedBack = onBack && (() => {
    if (busyLabel) {
      setNotice({ title: "처리 중",
                  text: `${busyLabel} — 끝날 때까지 다른 화면으로 넘어갈 수 없습니다.` });
      return;
    }
    onBack();
  });
  const [ctxAt, setCtxAt] = useState<{ x: number; y: number; path: string } | null>(null);
  useEffect(() => {
    const timer = window.setInterval(() => setReloadKey((key) => key + 1), 2000);
    return () => window.clearInterval(timer);
  }, []);
  const name = root.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || root;

  /* ⚠ 2초 주기 새로고침에서 result 를 null 로 되돌리면 목록이 매번 사라지고
     라벨이 "불러오는 중..." 으로 돌아간다 (사용자 보고: 목록이 아무것도 안 뜸).
     원본의 2초 타이머는 배너/카운트만 갱신하고 표를 비우지 않는다.
     → 루트·필터가 바뀔 때만 비우고, 주기 갱신은 조용히 값만 갈아끼운다. */
  useEffect(() => { setResult(null); }, [root, filter]);
  useEffect(() => {
    let alive = true;
    loadIncomplete(root, filter !== "실패만", filter !== "대기만").then((got) => {
      if (alive) setResult(got ?? { rows: [], total: 0, pending: 0, failed: 0 });
    });
    return () => { alive = false; };
  }, [root, filter, reloadKey]);

  const list = result?.rows ?? [];
  /* 원본 count_lbl 문구 */
  const countLabel = result === null
    ? "불러오는 중..."
    : result.total > list.length
      ? `표시 ${list.length.toLocaleString()} / 필터 전체 ${result.total.toLocaleString()} — `
        + "limit 초과분은 [필터된 전체] 버튼으로 처리"
      : `표시 ${list.length.toLocaleString()} (전체 ${result.total.toLocaleString()})`;

  /* 원본 _delete_selected / _delete_all_filtered 의 경고 문구 */
  const kinds = [filter !== "실패만" ? "대기" : "", filter !== "대기만" ? "실패" : ""]
    .filter(Boolean).join(" + ");
  const removeTail = "(실제 파일은 삭제되지 않습니다.)\n\n"
    + "※ 제거분은 검색/목록에서만 빠집니다. 디스크에 파일이 남아 있으면\n"
    + "   '전체 갱신' 시 다시 돌아옵니다 (영구 제외는 중복 숨김/블랙리스트).";

  return (
    <Modal
      title={`미완료 항목 — ${name}`}
      size="lg"
      /* 작업 중에는 닫기를 막는다 — guardedClose 주석 참고 */
      onClose={guardedClose}
      onBack={guardedBack}
      /* 위에 뜬 안내/확인창이 Esc 를 받아야 한다 (조사 M02) */
      blockEscape={Boolean(notice || confirmText)}
      foot={
        <>
          <button disabled={Boolean(busyLabel)} className="btn pill"
                  data-tip="Phase2 가 백그라운드에서 처리 중이면 잠시 후 다시 클릭하여 결과 확인"
                  onClick={() => setReloadKey((k) => k + 1)}>
            {busyLabel ? "진행 중..." : "↻ 새로고침"}
          </button>
          <div className="grow" />
          <button className="btn btn-primary"
                  data-tip="선택한 항목들의 meta_extracted=0 으로 리셋 후 백그라운드에서 메타 추출만 즉시 실행 (Phase1 스캔 스킵)"
                  disabled={Boolean(busyLabel)}
                  onClick={() => {
                    if (!checked.size) {
                      setNotice({ title: "선택 필요", text: "재시도할 항목을 선택하세요." });
                      return;
                    }
                    runGuarded("선택 항목 재분석 중", { op: "retry_paths", paths: [...checked] },
                      (result) => {
                        setNotice({ title: "재시도 완료", text: result.success === false
                          ? String(result.message || "작업 실패")
                          : retrySummary(result as Record<string, unknown>) });
                        setReloadKey((key) => key + 1); onDataChanged?.();
                      });
                  }}>
            {busyLabel === "선택 항목 재분석 중" ? "재분석 중..." : "선택 항목 재시도 (Phase2)"}
          </button>
          <button className="btn btn-danger" disabled={Boolean(busyLabel)}
                  onClick={() => {
                    if (!checked.size) {
                      setNotice({ title: "선택 필요", text: "제거할 항목을 선택하세요." });
                      return;
                    }
                    setConfirmMode("selected");
                    setConfirmText(`선택한 ${checked.size.toLocaleString()}개 항목을 인덱스에서 제거할까요?\n`
                      + removeTail);
                  }}>
            선택 항목 제거
          </button>
        </>
      }
    >
      <div className="failed-head">
        <b>{root}</b>
        <div>
          <span className="cnt-pending">대기 {(result?.pending ?? 0).toLocaleString()}</span>
          <span className="cnt-sep"> · </span>
          <span className="cnt-failed">실패 {(result?.failed ?? 0).toLocaleString()}</span>
        </div>
      </div>

      {/* ⚠ Modal 의 `.modal-body` 는 **블록 + overflow:auto** 다. 그래서 표가 길어지면
          본문 자체가 스크롤되고, 표 아래에 있는 "필터된 전체 재시도/제거" 줄이
          화면 밖으로 밀려 보이지 않았다 (사용자: PoC 엔 버튼이 하나뿐이라고 신고 —
          `flex: 0 0 auto` 만으론 부모가 flex 가 아니라 효과가 없었다).
          여기서 flex 열로 감싸 표만 스크롤되고 아래 줄은 항상 보이게 한다. */}
      <div className="failed-body">
      {busyLabel && (
        <div className="retry-banner" role="status" aria-live="polite">
          <span className="dot" aria-hidden="true" />
          {busyLabel} — 완료되면 목록이 갱신됩니다
        </div>
      )}
      <div className="failed-tools">
        <Dropdown value={filter} ariaLabel="표시 항목 필터"
                  options={["전체 (대기+실패)", "대기만", "실패만"]}
                  onChange={setFilter} />
        <button className="btn btn-sm pill"
                onClick={() => setChecked(new Set(list.map((r) => r.file_path)))}>전체 선택</button>
        <button className="btn btn-sm pill" onClick={() => setChecked(new Set())}>선택 해제</button>
        <div className="grow" />
        <span className="failed-count">{countLabel}</span>
      </div>

      {/* 원본: 줄바꿈 없이 가로로 길게 + Shift+휠 가로 스크롤(감도 delta*1.5) */}
      <div className="failed-scroll"
           onWheel={(event) => {
             if (!event.shiftKey) return;
             event.preventDefault();
             event.currentTarget.scrollLeft -= event.deltaY * 1.5;
           }}>
        <table className="mtable alt failed-table">
          <thead><tr><th /><th>상태</th><th>파일명</th><th>경로</th><th>사유</th></tr></thead>
          <tbody>
            {/* 비어 있어도 표가 통째로 사라지면 "안 불러온 건지, 없는 건지" 알 수 없다
                (사용자 지시 2026-09-14). 한 줄짜리 안내를 대신 보여준다. */}
            {list.length === 0 && (
              <tr className="empty-row">
                <td colSpan={5}>
                  {filter === "대기만" ? "대기 중인 항목이 없습니다"
                    : filter === "실패만" ? "실패한 항목이 없습니다"
                    : "미완료 항목이 없습니다 — 이 라이브러리는 분석이 모두 끝났습니다"}
                </td>
              </tr>
            )}
            {list.map((f) => (
              <tr key={f.file_path}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setCtxAt({ x: event.clientX, y: event.clientY, path: f.file_path });
                  }}>
                <td>
                  <span className={"check" + (checked.has(f.file_path) ? " on" : "")}
                        role="checkbox" aria-checked={checked.has(f.file_path)}
                        onClick={() => setChecked((prev) => {
                          const next = new Set(prev);
                          if (next.has(f.file_path)) next.delete(f.file_path);
                          else next.add(f.file_path);
                          return next;
                        })}>
                    <IcoCheck size={10} />
                  </span>
                </td>
                {/* 원본 색: 대기 #7adfc4 / 실패 #ff7878 */}
                <td className={f.status === "failed" ? "cnt-failed" : "cnt-pending"}>
                  {f.status === "failed" ? "실패" : "대기"}
                </td>
                <td className="strong">{f.file_name}</td>
                {/* 원본 3열은 폴더가 아니라 전체 경로 */}
                <td data-tip={f.file_path}>{f.file_path}</td>
                <td data-tip={f.reason || reasonText(f.reason, f.status)}>
                  {reasonText(f.reason, f.status)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 원본 bar3 — 표시 limit 을 넘는 라이브러리용 */}
      <div className="failed-tools bar3">
        <span className="failed-count">필터된 전체 (표시 limit 무시):</span>
        <div className="grow" />
        <button className="btn btn-primary btn-sm"
                data-tip="현재 필터 조건(전체/대기만/실패만)에 해당하는 모든 항목을 SQL 한 번에 재시도 큐로 — 5000+ 도 처리"
                disabled={Boolean(busyLabel)}
                onClick={() => runGuarded("필터된 전체 재분석 중",
                  { op: "retry_scope", path: root,
                    include_pending: filter !== "실패만", include_failed: filter !== "대기만" },
                  (result) => {
                    setNotice({ title: "재시도 완료", text: result.success === false
                      ? String(result.message || "작업 실패")
                      : retrySummary(result as Record<string, unknown>) });
                    setReloadKey((key) => key + 1); onDataChanged?.();
                  })}>
          필터된 전체 재시도
        </button>
        <button className="btn btn-danger btn-sm" disabled={Boolean(busyLabel)}
                data-tip="현재 필터 조건에 해당하는 모든 항목을 인덱스에서 제거"
                onClick={() => { setConfirmMode("scope");
                  setConfirmText(`이 라이브러리의 ${kinds} 항목 전체를\n`
                  + "인덱스에서 제거할까요?\n" + removeTail); }}>
          필터된 전체 제거
        </button>
      </div>
      </div>

      {ctxAt && (
        <>
          <div className="ctx-scrim" onMouseDown={() => setCtxAt(null)} />
          <div className="ctxmenu" style={{ left: ctxAt.x, top: ctxAt.y }}>
            <button className="ctxitem"
                    onClick={() => { void revealInExplorer(ctxAt.path); setCtxAt(null); }}>
              파일 위치 열기
            </button>
          </div>
        </>
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
            <div className="modal-head"><span className="modal-title">{notice.title}</span></div>
            <div className="modal-body"><div className="confirm-text pre">{notice.text}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" onClick={() => setNotice(null)}>확인</button>
            </div>
          </div>
        </div>
      )}

      {confirmText && (
        <div className="scrim inner"
               /* ⚠ 대상 검사가 없으면 **버튼을 누르는 순간(mousedown)**
                  창이 닫혀 클릭이 성립하지 않는다 — 제거/복원 버튼이
                  전부 죽어 있었다 (로그 실측: 확인창은 열리는데
                  "확인 누름" 이 한 번도 안 찍혔다). 바깥을 눌렀을 때만 닫는다. */
               onMouseDown={(event) => {
                 if (event.target === event.currentTarget) setConfirmText(null);
               }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">제거 확인</span></div>
            <div className="modal-body"><div className="confirm-text pre">{confirmText}</div></div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary"
                      onClick={() => {
                        setConfirmText(null);
                        const request = confirmMode === "selected"
                          ? { op: "delete_paths" as const, paths: [...checked] }
                          : { op: "delete_scope" as const, path: root,
                              include_pending: filter !== "실패만",
                              include_failed: filter !== "대기만" };
                        runGuarded("인덱스에서 제거 중", request, (result) => {
                          setNotice({ title: "제거", text: result.success === false
                            ? String(result.message || "작업 실패") : "인덱스에서 제거했습니다." });
                          if (!onRunAdmin && Array.isArray(result.orphan_ids)) onOrphans?.(result.orphan_ids.map(String));
                          setChecked(new Set()); setReloadKey((key) => key + 1); onDataChanged?.();
                        });
                      }}>제거</button>
              <button className="btn" onClick={() => setConfirmText(null)}>취소</button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

/* ── 블랙리스트 상세 (원본 BlacklistStatusDialog, blacklist_panel.py:285) ──
   제목 "블랙리스트 상세", 720x420. 본문 머리글 "등록된 블랙리스트 (N개)" 13px 700 +
   힌트 "검색 결과에서만 가려집니다 (인덱스/DB 는 유지).".
   표 4열 라이브러리(내용맞춤) / 경로(늘어남) / 사유(늘어남) / ✕(48px 고정), 행 교대 배경.
   제거는 확인 후 ("블랙리스트에서 제거할까요?" + 경로). 하단 닫기(pill). */
function Blacklist({ onClose, entries, onDataChanged }: {
  onClose: () => void;
  entries?: Array<{ path: string; description: string }> | null;
  onDataChanged?: () => void;
}) {
  /* entries 가 빈 배열이면 "0개" 가 실제 상태다 — 더미로 대체하지 않는다. */
  const [rows, setRows] = useState(entries ?? []);
  const [confirmPath, setConfirmPath] = useState<string | null>(null);
  return (
    <Modal
      title="블랙리스트 상세"
      size="lg"
      onClose={onClose}
      foot={
        <>
          <div className="grow" />
          <button className="btn pill" onClick={onClose}>닫기</button>
        </>
      }
    >
      <div className="bl-detail-title">{t("등록된 블랙리스트 ({0}개)", rows.length)}</div>
      <div className="form-hint">검색 결과에서만 가려집니다 (인덱스/DB 는 유지).</div>

      <table className="mtable alt bl-table">
        <thead><tr><th>라이브러리</th><th>경로</th><th>사유</th><th /></tr></thead>
        <tbody>
          {rows.map(({ path: p, description }) => (
            <tr key={p}>
              <td>{p.split(/[\\/]/).filter(Boolean).at(-1) ?? p}</td>
              <td className="strong" data-tip={p}>{p}</td>
              <td>{description}</td>
              <td>
                <button className="bl-remove" data-tip="이 항목 제거" aria-label="이 항목 제거"
                        onClick={() => setConfirmPath(p)}>✕</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {confirmPath && (
        <div className="scrim" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setConfirmPath(null);
        }}>
          <div className="modal sm" role="dialog" aria-modal="true">
            <div className="modal-head"><span className="modal-title">블랙리스트 제거</span></div>
            <div className="modal-body">
              <div className="confirm-text pre">{`블랙리스트에서 제거할까요?\n${confirmPath}`}</div>
            </div>
            <div className="modal-foot">
              <div className="grow" />
              <button className="btn btn-primary" autoFocus
                      onClick={() => {
                        const path = confirmPath;
                        void removeBlacklistPath(path).then((ok) => {
                          if (ok) {
                            setRows((r) => r.filter((x) => x.path !== path));
                            onDataChanged?.();
                          }
                          setConfirmPath(null);
                        });
                      }}>
                예
              </button>
              <button className="btn" onClick={() => setConfirmPath(null)}>아니오</button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

/* 중복 검수는 원본에서 환경설정의 "중복 검수" 탭이다. 이 창은 결과 목록만 따로 볼 때
   쓰는 보조 뷰로 남겨 둔다 (원본에는 없으므로 기본 진입점도 없다). */
function Dupes({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="중복 검수 결과" size="lg" onClose={onClose}
           foot={<><div className="grow" /><button className="btn pill" onClick={onClose}>닫기</button></>}>
      <table className="mtable alt">
        <thead><tr><th>파일명 / 경로</th><th>크기</th><th>개수</th></tr></thead>
        <tbody>
          {[].map((g: { name: string; size: number; n: number; paths: string[] }) => (
            <tr key={g.name}>
              <td className="strong">{g.name}</td>
              <td className="num">{fmtSize(g.size)}</td>
              <td className="num">{g.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  );
}
