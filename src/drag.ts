import { invoke } from "@tauri-apps/api/core";
import { debugLog } from "./backend";

/* ── DAW 로 파일 드래그 아웃 (원본 results_table.mouseMoveEvent, 1263) ────────
   원본 정책을 그대로 따른다.
     · 시작 조건: **누른 행의 세로 띠를 벗어나야** 드래그 (행 안 이동은 오디션/클릭).
       단 10px 미만 미세 흔들림은 무조건 행 안으로 간주 (`_within_press_row`).
       ⚠ 시간 조건을 넣으면 빠른 드래그가 깨진다 — 거리 조건만 쓴다.
     · 대상: `selected_paths()` — 선택된 행 전부
     · mime: QMimeData.setUrls(...) 표준 파일 URL → Cubase/Nuendo/Pro Tools/Reaper 공통
     · 드롭이 성사되면 `dragDropped` → MainWindow 가 `stop_on_drag` 설정을 보고 정지
   WebView 안에서는 OS 드래그를 시작할 수 없어 tauri-plugin-drag 를 쓴다. */

const DRAG_JITTER_PX = 10;

let iconPath: string | null = null;
let dragging = false;

/* 상태바로 사유를 알리는 통로 — App 이 한 번 등록한다.
   드래그가 막혔을 때 아무 일도 안 일어나면 사용자는 이유를 알 수 없다. */
let statusSink: ((message: string) => void) | null = null;
export function setDragStatusSink(sink: (message: string) => void) {
  statusSink = sink;
}

/* 원본 _draggable_path (results_table.py:326) — MAX_PATH(256) 를 넘는 파일은
   짧은 임시 복사본 경로로 바꿔 넘긴다. 그러지 않으면 누엔도/탐색기가 경로를 못 연다.
   길이가 짧은 경로는 그대로 돌아온다.

   ⚠ 여기서 오류를 삼켜 원본 경로로 진행하면 안 된다. `sf_drag_paths` 는 셸이 그
   경로를 실제로 다룰 수 있는지(drag 크레이트가 실패하는 바로 그 호출로) 확인한다.
   확인에 실패했는데 그대로 startDrag 를 부르면 **앱이 즉사한다** (실측: drag-2.1.1
   windows/mod.rs:370 패닉 → PID 소멸). 그래서 던지고, 호출부가 드래그를 포기한다. */
async function draggablePaths(paths: string[]): Promise<string[]> {
  const ready = await invoke<string[]>("sf_drag_paths", { paths });
  /* 크레이트에 실제로 넘어가는 값을 남긴다 — 검사한 값과 다르면 그게 원인이다 */
  void debugLog("DRAG item n=" + ready.length + " 첫=" + (ready[0] ?? "(없음)"));
  return ready;
}

async function ensureIcon(): Promise<string> {
  if (iconPath) return iconPath;
  iconPath = await invoke<string>("sf_drag_icon");
  return iconPath;
}

/* 드래그 그림을 만드는 함수를 호출부가 등록한다 (결과표는 행 모습, 파형은 구간).
   그림 만들기가 실패하거나 등록이 없으면 기존 고정 아이콘으로 넘어간다 —
   **그림 때문에 드래그가 막히면 안 된다.** */
type ImageMaker = () => Promise<string | null>;

async function pickIcon(make?: ImageMaker): Promise<string> {
  if (make) {
    try {
      const drawn = await make();
      if (drawn) return drawn;
    } catch {
      /* 무시하고 고정 아이콘 */
    }
  }
  return ensureIcon();
}

/* ── 왼쪽 버튼이 지금도 눌려 있는가 ────────────────────────────────────────
   원본 results_table._left_button_down_now (results_table.py:33) 와 같은 목적이다.
   드래그를 시작하기 전에 경로 준비와 그림 만들기를 기다리는데, 그 사이에 사용자가
   버튼을 놓았을 수 있다. 그때 OS 드래그를 시작하면 **놓을 이벤트가 없어서** 끌던
   그림이 커서에 붙어 안 떨어진다 (원본이 실제로 겪고 막아 둔 문제).

   창이 포커스를 잃으면(다른 앱으로 드롭이 끝난 뒤 등) 눌림 상태를 푼다 — 그 상태가
   남으면 다음 드래그가 잘못 허용된다. */
let leftDown = false;
if (typeof window !== "undefined") {
  window.addEventListener("mousedown", (event) => {
    if (event.button === 0) leftDown = true;
  }, true);
  window.addEventListener("mouseup", (event) => {
    if (event.button === 0) leftDown = false;
  }, true);
  window.addEventListener("blur", () => { leftDown = false; });
}

export type DragWatch = {
  /** 마우스가 움직일 때마다 호출 — 드래그를 시작했으면 true */
  move: (event: MouseEvent) => boolean;
  cancel: () => void;
};

/**
 * 행에서 마우스를 누른 순간 호출한다. 반환된 watch 를 mousemove/up 에 물리면
 * 원본과 같은 조건(행 이탈)에서만 OS 드래그가 시작된다.
 *
 * @param startX,startY 누른 지점 (clientX/clientY)
 * @param rowTop,rowBottom 누른 행의 세로 범위 (viewport 기준)
 * @param getPaths 드래그 시작 시점의 대상 경로들 (선택 전체)
 * @param onDropped 실제로 외부에 떨어졌을 때 (원본 dragDropped)
 */
export function watchRowDrag(startX: number, startY: number,
                             rowTop: number, rowBottom: number,
                             getPaths: () => string[],
                             onDropped?: () => void,
                             tableLeft?: number, tableRight?: number,
                             makeImage?: ImageMaker): DragWatch {
  let cancelled = false;
  return {
    move(event: MouseEvent) {
      if (cancelled || dragging) return false;
      const dx = Math.abs(event.clientX - startX);
      const dy = Math.abs(event.clientY - startY);
      /* 미세 흔들림은 무시 (원본 manhattanLength < 10) */
      if (dx + dy < DRAG_JITTER_PX) return false;
      /* 원본 정책(_within_press_row, results_table.py:1246): 누른 행의 사각형을
         벗어나야 드래그. 그 사각형은
             QRect(0, r.top(), viewport().width(), r.height())
         로 **표 가로폭 전체**를 덮는다. 그래서 벗어나는 경우가 두 가지다.
           · 세로 — 다른 행으로 넘어감
           · 가로 — 커서가 표 밖으로 나감

         ⚠ 예전 이식은 clientY 만 봤다. 그러면 DAW 로 **옆으로** 끌 때 Y 가 행 안에
           머물러 드래그가 영원히 시작되지 않고, 커서를 위아래로 흔들어 행 경계를
           넘는 순간에만 잡혔다 (사용자 신고 2026-09-07: "커서를 막 흔들어야 그제서야
           인식이 돼"). 가로 조건을 빼지 말 것. */
      const insideRowBand = event.clientY >= rowTop && event.clientY <= rowBottom;
      const insideTableX = tableLeft === undefined || tableRight === undefined
        ? true
        : event.clientX >= tableLeft && event.clientX <= tableRight;
      if (insideRowBand && insideTableX) return false;
      const paths = getPaths().filter(Boolean);
      if (!paths.length) return false;
      dragging = true;
      void (async () => {
        try {
          void debugLog("DRAG start n=" + paths.length);
          const { startDrag } = await import("@crabnebula/tauri-plugin-drag");
          const item = await draggablePaths(paths);
          const icon = await pickIcon(makeImage);
          /* 준비하는 동안 버튼을 놓았으면 시작하지 않는다 (조사 Q13) */
          if (!leftDown) {
            dragging = false;
            void debugLog("DRAG 취소 — 준비 중 버튼을 놓았다");
            return;
          }
          await startDrag({ item, icon, mode: "copy" },
            (payload) => {
              dragging = false;
              void debugLog("DRAG done " + String(payload?.result ?? "?"));
              /* 원본은 결과가 IgnoreAction 이 아닐 때만 dragDropped 를 낸다 */
              if (payload?.result === "Dropped") onDropped?.();
            });
        } catch (err) {
          dragging = false;   // Tauri 밖(브라우저)이면 조용히 무시
          void debugLog("DRAG error " + String(err).slice(0, 300));
          reportDragFailure(err);
        }
      })();
      return true;
    },
    cancel() {
      cancelled = true;
    },
  };
}

/**
 * 히스토리 목록용 — 원본 `_DraggableHistoryList.mouseMoveEvent` 는 결과표와 달리
 * **행 이탈 조건이 없고** 맨해튼 거리 10px 만 넘으면 바로 드래그를 시작한다
 * (`(e.pos() - self._drag_start).manhattanLength() < 10` 만 검사).
 * 대상은 선택된 항목들의 경로(중복 제거)다.
 */
export function watchListDrag(startX: number, startY: number,
                              getPaths: () => string[],
                              onDropped?: () => void): DragWatch {
  let cancelled = false;
  return {
    move(event: MouseEvent) {
      if (cancelled || dragging) return false;
      const manhattan = Math.abs(event.clientX - startX) + Math.abs(event.clientY - startY);
      if (manhattan < DRAG_JITTER_PX) return false;
      const paths = [...new Set(getPaths().filter(Boolean))];
      if (!paths.length) return false;
      dragging = true;
      void (async () => {
        try {
          void debugLog("DRAG start n=" + paths.length);
          const { startDrag } = await import("@crabnebula/tauri-plugin-drag");
          const item = await draggablePaths(paths);
          const icon = await ensureIcon();
          /* 준비하는 동안 버튼을 놓았으면 시작하지 않는다 (조사 Q13) */
          if (!leftDown) {
            dragging = false;
            void debugLog("DRAG 취소 — 준비 중 버튼을 놓았다");
            return;
          }
          await startDrag({ item, icon, mode: "copy" },
            (payload) => {
              dragging = false;
              if (payload?.result === "Dropped") onDropped?.();
            });
        } catch (err) {
          dragging = false;
          reportDragFailure(err);
        }
      })();
      return true;
    },
    cancel() { cancelled = true; },
  };
}

/**
 * 파형 영역 드래그 아웃 — 원본 `_on_drag_region` 은 임계값을 넘은 순간
 * 이미 만들어 둔 crop 파일 하나로 `QDrag.exec` 를 바로 띄운다 (추가 조건 없음).
 * 드롭이 성사되면(`IgnoreAction` 이 아니면) 콜백을 부른다 — 상위가 stop_on_drag
 * 설정을 보고 재생을 멈춘다.
 */
export function startRegionDrag(path: string, onDropped?: () => void,
                                makeImage?: ImageMaker) {
  if (!path || dragging) return;
  dragging = true;
  void (async () => {
    try {
      const { startDrag } = await import("@crabnebula/tauri-plugin-drag");
      await startDrag({ item: await draggablePaths([path]),
                        icon: await pickIcon(makeImage), mode: "copy" },
        (payload) => {
          dragging = false;
          if (payload?.result === "Dropped") onDropped?.();
        });
    } catch (err) {
      dragging = false;
      reportDragFailure(err);
    }
  })();
}

/* 드래그가 막힌 사유를 상태바에 남긴다. 우리 명령이 돌려준 한글 메시지면 그대로,
   그 밖의 오류(Tauri 밖 실행 등)는 조용히 넘긴다. */
function reportDragFailure(err: unknown) {
  const message = typeof err === "string" ? err : String((err as Error)?.message ?? err ?? "");
  if (message.includes("드래그를 시작할 수 없습니다")) statusSink?.(message);
}
