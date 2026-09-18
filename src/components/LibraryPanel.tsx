import type { Lib } from "../data";
import { useMemo, useState } from "react";
import { IcoCaret } from "../icons";

type Props = {
  onContext: (e: React.MouseEvent, label: string, opts?: {
    isRoot?: boolean; hasChildren?: boolean; tab?: string; count?: number;
    isFavorite?: boolean; canMoveUp?: boolean; canMoveDown?: boolean; kind?: "tree" | "tab";
    path?: string; paths?: string[];
  }) => void;
  onStatus: () => void;
  /* 폴더 선택으로 추가된 경로 — 아직 인덱싱 전이라 '대기'(busy) 상태로 표시한다. */
  addedLibs: string[];
  libraries: Lib[];
};

function fmtScan(ts?: number | null) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

export function LibraryPanel({ onContext, onStatus, addedLibs, libraries }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const groups = useMemo(() => {
    const added: Lib[] = addedLibs.map((path, i) => ({
      id: `added-${i}`,
      name: path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path,
      path,
      count: 0,
      state: "busy",
    }));
    const map = new Map<string, Lib[]>();
    for (const lib of [...libraries, ...added]) {
      const drive = /^[A-Za-z]:/.test(lib.path) ? `${lib.path.slice(0, 2).toUpperCase()}\\` : "";
      map.set(drive, [...(map.get(drive) ?? []), lib]);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [addedLibs, libraries]);

  const toggleDrive = (drive: string) => {
    setCollapsed((value) => {
      const next = new Set(value);
      next.has(drive) ? next.delete(drive) : next.add(drive);
      return next;
    });
  };

  return (
    <aside className="lib-inline">
      <div className="lib-inline-head">
        <span className="section-title">라이브러리 현황</span>
        <button className="abtn icon-only lib-detail" data-tip="자세한 라이브러리 정보" onClick={onStatus}>..</button>
        {/* 원본 _make_legend_chip: 세 칩 모두 같은 도형(채운 점 14px)이고 색만 다르다.
            사용자 지시로 범례 묶음은 우측 정렬한다. */}
        <span className="legend-spacer" />
        <span className="legend"><span className="legend-dot ok" />정상</span>
        <span className="legend"><span className="legend-dot pending" />대기</span>
        <span className="legend"><span className="legend-dot fail" />실패</span>
        {/* 원본은 검색 도움말과 같은 "?" 버튼(_HelpButton)이다. 정보 아이콘이 아니다. */}
        <button className="help-dot status-help" aria-label="라이브러리 상태 도움말"
                data-tip={`라이브러리 인덱싱 상태

● 정상 — 모든 메타데이터 추출 완료
● 대기 — 메타데이터 추출 대기 중인 파일 존재
● 실패 — 추출 실패 파일 존재 (또는 경로 자체 없음)`}>?</button>
      </div>
      <div className="lib-inline-list">
        {/* 원본 _apply_data: 데이터가 비면 행 대신 "라이브러리 없음" 한 줄만 둔다 */}
        {!groups.length && <div className="lib-empty">라이브러리 없음</div>}
        {groups.map(([drive, libs]) => {
          const closed = collapsed.has(drive);
          return (
            <div className="lib-drive" key={drive || "other"}>
              {drive && (
                <button className="lib-drive-head" onClick={() => toggleDrive(drive)}>
                  <IcoCaret className={closed ? "" : "open"} size={11} />
                  <span>{drive}</span>
                  <span>({libs.length}개)</span>
                </button>
              )}
              {!closed && libs.map((l) => (
                <div key={l.id} className="lib-root"
                     onContextMenu={(e) => onContext(e, l.name, { isRoot: true, hasChildren: false, tab: "all", path: l.path })}>
                  {/* 원본 _build_root_row: 툴팁은 **점**에만 붙고 " · " 로 잇는다.
                      경로 없음 → "{경로} · 경로 없음"
                      그 외     → "{경로}" + 실패 N / 대기 N, 둘 다 없으면 "정상" */}
                  <span className={"led " + l.state}
                        data-tip={(() => {
                          const gone = l.state === "missing" || l.state === "off";
                          const parts = [l.path];
                          if (gone) parts.push("경로 없음");
                          else {
                            if (l.failed) parts.push(`실패 ${l.failed.toLocaleString()}`);
                            if (l.pending) parts.push(`대기 ${l.pending.toLocaleString()}`);
                            if (!l.failed && !l.pending) parts.push("정상");
                          }
                          return parts.join(" · ");
                        })()} />
                  {/* 원본: 이름 라벨의 툴팁은 경로 하나뿐 */}
                  <span className="lib-name" data-tip={l.path}>{l.name}</span>
                  {/* -1 = 카운트 계산 중 (원본도 워커 결과가 오기 전에는 비워 둔다) */}
                  <span className="lib-count">{l.count < 0 ? "…" : l.count.toLocaleString()}</span>
                  <span className="lib-scan">{fmtScan(l.lastIndexedAt)}</span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
