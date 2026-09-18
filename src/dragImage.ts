import { invoke } from "@tauri-apps/api/core";

/* ── 드래그 중 커서에 붙는 그림 ─────────────────────────────────────────────
   이전에는 고정 아이콘 하나만 붙어서 **무엇을 끌고 있는지 알 수 없었다**
   (사용자 지시 2026-09-07).
     · 파일 드래그 → 결과표 **행 모습 그대로**. 여러 개면 겹친 카드 + "N개"
     · 영역 드래그 → 끌어다 준 **구간의 파형** + 구간 길이

   startDrag 는 이미지를 **파일 경로**로만 받으므로, 여기서 캔버스에 그린 뒤
   PNG(base64)를 Rust(sf_drag_image)로 넘겨 임시 파일 경로를 받아온다.

   ⚠ 이 함수들은 드래그 시작 직전에 불린다. 늦으면 드래그가 뚝뚝 끊겨 보이므로
     무거운 작업을 넣지 말 것 (캔버스 드로잉만, 파일/네트워크 접근 금지).
   ⚠ 실패하면 **throw 하지 않고 null** 을 돌려준다. 그림이 없다고 드래그 자체를
     막을 이유가 없다 — 호출부가 기존 고정 아이콘으로 넘어간다. */

const DPR = Math.min(window.devicePixelRatio || 1, 2);

/** 화면에서 실제로 쓰는 색/폰트를 그대로 읽어온다 (테마 따라감) */
function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const get = (name: string, fallback: string) =>
    cs.getPropertyValue(name).trim() || fallback;
  return {
    bg: get("--bg-panel", "#232323"),
    bgAlt: get("--bg-control", "#2b2b2b"),
    text: get("--text", "#e8e8e8"),
    muted: get("--text-muted", "#9a9a9a"),
    line: get("--line-strong", "#3a3a3a"),
    accent: get("--accent", "#cf7676"),
    waveFill: get("--wave-grad-center", "rgb(245,245,245)"),
    waveEdge: get("--wave-grad-edge", "rgba(200,200,200,.37)"),
  };
}

function newCanvas(w: number, h: number) {
  const cv = document.createElement("canvas");
  cv.width = Math.round(w * DPR);
  cv.height = Math.round(h * DPR);
  const g = cv.getContext("2d");
  if (g) g.setTransform(DPR, 0, 0, DPR, 0, 0);
  return { cv, g };
}

function roundRect(g: CanvasRenderingContext2D,
                   x: number, y: number, w: number, h: number, r: number) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

async function upload(kind: "row" | "region", cv: HTMLCanvasElement): Promise<string | null> {
  try {
    const url = cv.toDataURL("image/png");
    const base64 = url.slice(url.indexOf(",") + 1);
    if (!base64) return null;
    return await invoke<string>("sf_drag_image", { kind, pngBase64: base64 });
  } catch {
    return null;   // 그림 없이도 드래그는 되어야 한다
  }
}

/* ── 파일 드래그: 결과표 행 모습 ─────────────────────────────────────────── */

export type DragRowCell = { text: string; width: number; align?: "right" };

/**
 * 결과표에서 끌고 있는 행을 그대로 그린다.
 * @param cells  보이는 열의 (글자, 폭) — 화면과 같은 구성/순서
 * @param count  선택 개수. 2 이상이면 겹친 카드 + "N개" 배지
 */
export async function makeRowDragImage(
  cells: DragRowCell[], count: number, rowHeight: number,
): Promise<string | null> {
  if (!cells.length) return null;
  const t = tokens();
  const PAD = 8;
  const H = Math.max(20, Math.round(rowHeight));
  /* 폭은 화면 열 폭을 그대로 쓰되, 커서에 붙는 그림이라 너무 넓으면 방해된다 */
  const rawW = cells.reduce((sum, c) => sum + c.width, 0);
  const W = Math.max(160, Math.min(560, Math.round(rawW)));
  /* 여러 개면 뒤에 카드를 겹쳐 보인다 */
  const stack = Math.min(3, Math.max(1, count));
  const OFF = 4;                       // 카드 한 장당 밀림
  const totalW = W + (stack - 1) * OFF;
  const totalH = H + (stack - 1) * OFF;

  const { cv, g } = newCanvas(totalW, totalH);
  if (!g) return null;

  /* 뒤쪽 카드부터 — 어둡게 깔아 겹친 느낌만 준다 */
  for (let i = stack - 1; i >= 1; i--) {
    const x = i * OFF;
    const y = i * OFF;
    g.globalAlpha = 0.45 - (i - 1) * 0.12;
    g.fillStyle = t.bgAlt;
    roundRect(g, x, y, W, H, 3);
    g.fill();
    g.strokeStyle = t.line;
    g.lineWidth = 1;
    g.stroke();
  }
  g.globalAlpha = 1;

  /* 맨 앞 카드 = 실제 행 */
  g.fillStyle = t.bg;
  roundRect(g, 0, 0, W, H, 3);
  g.fill();
  g.strokeStyle = t.line;
  g.lineWidth = 1;
  g.stroke();

  g.font = '12px "Inter Variable", Inter, "Malgun Gothic", sans-serif';
  g.textBaseline = "middle";
  let x = PAD;
  for (const c of cells) {
    const w = Math.max(0, Math.min(c.width, W - x - PAD));
    if (w <= 4) break;
    g.save();
    g.beginPath();
    g.rect(x, 0, w, H);
    g.clip();
    g.fillStyle = c === cells[0] ? t.text : t.muted;
    if (c.align === "right") {
      g.textAlign = "right";
      g.fillText(c.text, x + w - 4, H / 2);
    } else {
      g.textAlign = "left";
      g.fillText(c.text, x, H / 2);
    }
    g.restore();
    x += c.width;
    if (x > W - PAD) break;
  }

  /* 개수 배지 — 오른쪽 아래 모서리 */
  if (count > 1) {
    const label = `${count}개`;
    g.font = '600 11px "Inter Variable", Inter, "Malgun Gothic", sans-serif';
    const tw = g.measureText(label).width;
    const bw = tw + 12;
    const bh = 16;
    const bx = totalW - bw - 1;
    const by = totalH - bh - 1;
    g.fillStyle = t.accent;
    roundRect(g, bx, by, bw, bh, 8);
    g.fill();
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(label, bx + bw / 2, by + bh / 2 + 0.5);
  }

  return upload("row", cv);
}

/* ── 영역 드래그: 구간 파형 + 구간 길이 ──────────────────────────────────── */

/** 구간 길이를 사람이 읽는 형식으로 — 0:03.2 / 1:24.0 */
function fmtSpan(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${m}:${rest < 10 ? "0" : ""}${rest.toFixed(1)}`;
}

/**
 * 끌어다 준 구간의 파형을 그린다.
 * @param peaks   구간에 해당하는 (min,max) 쌍 배열 — 채널 하나로 합친 값
 * @param seconds 구간 길이(초)
 */
export async function makeRegionDragImage(
  peaks: Array<[number, number]>, seconds: number,
): Promise<string | null> {
  const t = tokens();
  const W = 220;
  const WAVE_H = 56;
  const LABEL_H = 18;
  const H = WAVE_H + LABEL_H;
  const { cv, g } = newCanvas(W, H);
  if (!g) return null;

  g.fillStyle = t.bg;
  roundRect(g, 0, 0, W, H, 4);
  g.fill();
  g.strokeStyle = t.line;
  g.lineWidth = 1;
  g.stroke();

  /* 중앙 0선 */
  const mid = WAVE_H / 2;
  g.strokeStyle = t.line;
  g.beginPath();
  g.moveTo(6, Math.round(mid) + 0.5);
  g.lineTo(W - 6, Math.round(mid) + 0.5);
  g.stroke();

  if (peaks.length) {
    const amp = WAVE_H / 2 - 6;
    const grad = g.createLinearGradient(0, 0, 0, WAVE_H);
    grad.addColorStop(0, t.waveEdge);
    grad.addColorStop(0.5, t.waveFill);
    grad.addColorStop(1, t.waveEdge);
    const n = peaks.length;
    const innerW = W - 12;
    g.beginPath();
    for (let i = 0; i < n; i++) {
      const x = 6 + (i / Math.max(1, n - 1)) * innerW;
      g.lineTo(x, mid - Math.max(-1, Math.min(1, peaks[i][1])) * amp);
    }
    for (let i = n - 1; i >= 0; i--) {
      const x = 6 + (i / Math.max(1, n - 1)) * innerW;
      g.lineTo(x, mid - Math.max(-1, Math.min(1, peaks[i][0])) * amp);
    }
    g.closePath();
    g.fillStyle = grad;
    g.globalAlpha = 0.9;
    g.fill();
    g.globalAlpha = 1;
  }

  /* 구간 길이 */
  g.font = '600 11px "Inter Variable", Inter, "Malgun Gothic", sans-serif';
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.fillStyle = t.muted;
  g.fillText("영역", 8, WAVE_H + LABEL_H / 2);
  g.textAlign = "right";
  g.fillStyle = t.text;
  g.fillText(fmtSpan(seconds), W - 8, WAVE_H + LABEL_H / 2);

  return upload("region", cv);
}
