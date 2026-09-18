import { useEffect, useRef } from "react";
import { type WavePeaks } from "../backend";

/* ═══════════════════════════════════════════════════════════════
   파형 렌더 — 원본 player_widget.py:WaveformView 정책을 따른다.

   가로 줌은 폐지했다 (2026-09-04, 사용자 결정). 파형은 항상 **전체가 폭에 꽉 차게**
   한 배율로만 그려진다. 이유는 캐시 용량이었다:
     · 원본 캐시는 줌 단계별 피크 4개(1024/4096/16384/65536 = 87,040 슬라이스)를
       미리 계산해 저장했다 → 파일당 1.33MB, 실측 17,875개에 17.58GB.
     · 그 중 최고 확대 레벨(65536) 하나가 용량의 75%였고, 확대할 때만 쓰였다.
     · 이제 단일 8192 레벨 + int16 만 저장한다 → 파일당 64KB (-95%).
   세로(진폭) 줌은 그리는 배율만 바꿔 캐시와 무관하므로 그대로 남는다 (ampZoom).

   같이 사라진 것: 샘플 단위 raw 창 읽기(원본 _read_raw_samples / _build_polys_raw),
   샘플 폴리라인 모드, 가로 스크롤/팬.

   ⚠ 실제 피크가 없으면 **아무 파형도 그리지 않는다.** 예전에는 합성 파형으로
     폴백했고(모의 데이터 시절 잔재) 그러면 파일과 전혀 무관한 그림이 떴다
     (사용자 신고 2026-09-04: "러프한 전혀 연관없는 더미파형이 뜬다").
     그 생성기(makeShape/sampleAt/getPeaks)는 제거했다. 러프한 파형이 정당한
     경우는 원본에서 실제로 뽑은 quick(16슬라이스) 피크뿐이고, 그건 파일 내용과
     연관이 있다.
   ═══════════════════════════════════════════════════════════════ */

type Props = {
  channels: number;
  ampZoom: number;
  duration: number;
  sampleRate: number;
  /** 원본 peaks_cache 에서 읽은 실제 피크. 없으면 격자만 그리고 비워 둔다 */
  peaks?: WavePeaks | null;
};

/* 격자 후보 간격 (초) */
const GRID_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600];

/* 원본은 18px 이상이면 격자를 그렸지만 엔벌로프 렌더에서는 촘촘해 파형을 가린다.
   26px 로 올렸다 (시각 조정, 동작 영향 없음). */
function pickGridStep(pxPerSec: number) {
  for (const s of GRID_STEPS) if (pxPerSec * s >= 26) return s;
  return GRID_STEPS[GRID_STEPS.length - 1];
}

function channelLabel(index: number, total: number) {
  if (total === 1) return "M";
  if (total === 2) return index === 0 ? "L" : "R";
  return String(index + 1);
}

function clamp(v: number, lo: number, hi: number) {
  return v < lo ? lo : v > hi ? hi : v;
}

export function Waveform({ channels, ampZoom, duration, sampleRate, peaks }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    let raf = 0;

    const draw = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const w = cv.clientWidth;
      const h = cv.clientHeight;
      if (!w || !h) return;
      const tw = Math.round(w * dpr);
      const th = Math.round(h * dpr);
      if (cv.width !== tw || cv.height !== th) {
        cv.width = tw;
        cv.height = th;
      }
      const g = cv.getContext("2d");
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);

      const cs = getComputedStyle(document.documentElement);
      const tok = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
      const cGradEdge = tok("--wave-grad-edge", "rgba(200,200,200,.37)");
      const cGradMid = tok("--wave-grad-mid", "rgba(220,220,220,.72)");
      const cGradCenter = tok("--wave-grad-center", "rgb(245,245,245)");
      const cEnvEdge = tok("--wave-env-edge", "rgba(226,234,255,.22)");
      const cGrid = tok("--wave-grid", "rgba(255,255,255,.051)");
      const cGuide = tok("--wave-guide", "rgba(255,255,255,.055)");
      const cGuideSoft = tok("--wave-guide-soft", "rgba(255,255,255,.04)");
      const cDivider = tok("--wave-divider", "rgba(255,255,255,.125)");
      const cLabel = tok("--wave-label", "rgba(213,221,235,.48)");

      /* ─── 시간 격자 — 가로 줌 폐지로 항상 전체 구간 ─────────────── */
      if (duration > 0) {
        const pxPerSec = w / Math.max(1e-6, duration);
        const step = pickGridStep(pxPerSec);
        g.strokeStyle = cGrid;
        g.lineWidth = 1;
        g.beginPath();
        for (let t = step; t < duration; t += step) {
          const x = Math.round(t * pxPerSec) + 0.5;
          if (x >= 0 && x < w) {
            g.moveTo(x, 2);
            g.lineTo(x, h - 2);
          }
        }
        g.stroke();
      }

      /* 실제 피크가 없으면 여기서 끝 — 격자만 남고 파형은 그리지 않는다 (위 주석). */
      const real = peaks && peaks.levels.length ? peaks : null;
      if (!real) return;

      /* 가로 줌 폐지로 레벨은 항상 1개(원본 LEVEL_SLICES 단일 항목).
         옛 v8 캐시가 남아 여러 레벨이 오더라도 가장 촘촘한 것을 쓰면 된다. */
      const level = real.levels[real.levels.length - 1];
      const lvData = level.data;
      const lvSlices = level.slices;
      const dataCh = real.channels;

      const laneH = h / channels;

      for (let c = 0; c < channels; c++) {
        const laneTop = laneH * c;
        const mid = laneTop + laneH / 2;
        const baseAmp = Math.max(1, laneH / 2 - 4);
        const amp = baseAmp * ampZoom;
        const clipLimit = Math.max(1, laneH / 2 - 1);
        const yOf = (v: number) => mid - clamp(v * amp, -clipLimit, clipLimit);

        /* 가이드선 — 중앙 0선 + ±0.5 진폭선 */
        g.strokeStyle = cGuide;
        g.lineWidth = 1;
        g.beginPath();
        g.moveTo(0, Math.round(mid) + 0.5);
        g.lineTo(w, Math.round(mid) + 0.5);
        g.stroke();
        g.strokeStyle = cGuideSoft;
        for (const k of [-0.5, 0.5]) {
          const y = Math.round(mid + baseAmp * k) + 0.5;
          if (y > laneTop + 1 && y < laneTop + laneH - 1) {
            g.beginPath();
            g.moveTo(0, y);
            g.lineTo(w, y);
            g.stroke();
          }
        }

        /* min/max 엔벌로프 폴리곤 — 실제 피크 (slices, channels, 2) C-order, int16 */
        const n = Math.max(240, Math.floor(w * 1.8));
        const xs = new Float32Array(n);
        const ups = new Float32Array(n);
        const dns = new Float32Array(n);
        /* 데이터 채널이 레인 수보다 적으면 마지막 채널로 클램프 */
        const dc = Math.min(c, dataCh - 1);

        for (let k = 0; k < n; k++) {
          let mn = 0;
          let mx = 0;
          let seeded = false;
          const s0 = clamp(Math.floor((k / n) * lvSlices), 0, lvSlices - 1);
          const s1 = clamp(Math.ceil(((k + 1) / n) * lvSlices), s0 + 1, lvSlices);
          for (let sIdx = s0; sIdx < s1; sIdx++) {
            const base = (sIdx * dataCh + dc) * 2;
            const a = lvData[base] / 32767;
            const b = lvData[base + 1] / 32767;
            if (!seeded) { mn = a; mx = b; seeded = true; continue; }
            if (a < mn) mn = a;
            if (b > mx) mx = b;
          }
          xs[k] = (k / Math.max(1, n - 1)) * w;
          ups[k] = yOf(mx);
          dns[k] = yOf(mn);
        }

        const grad = g.createLinearGradient(0, laneTop, 0, laneTop + laneH);
        grad.addColorStop(0, cGradEdge);
        grad.addColorStop(0.3, cGradMid);
        grad.addColorStop(0.5, cGradCenter);
        grad.addColorStop(0.7, cGradMid);
        grad.addColorStop(1, cGradEdge);

        g.beginPath();
        g.moveTo(xs[0], ups[0]);
        for (let k = 1; k < n; k++) g.lineTo(xs[k], ups[k]);
        for (let k = n - 1; k >= 0; k--) g.lineTo(xs[k], dns[k]);
        g.closePath();
        g.fillStyle = grad;
        g.globalAlpha = 0.82;
        g.fill();
        g.globalAlpha = 1;
        g.strokeStyle = cEnvEdge;
        g.lineWidth = 0.72;
        g.stroke();

        /* 채널 구분선 */
        if (c > 0) {
          g.strokeStyle = cDivider;
          g.lineWidth = 1;
          g.beginPath();
          g.moveTo(0, Math.round(laneTop) + 0.5);
          g.lineTo(w, Math.round(laneTop) + 0.5);
          g.stroke();
        }

        /* 채널 라벨 — 레인 좌상단 */
        if (laneH >= 26) {
          g.font = '600 9px "Inter Variable", Inter, "Malgun Gothic", sans-serif';
          g.textBaseline = "top";
          g.fillStyle = cLabel;
          g.fillText(channelLabel(c, channels), 6, laneTop + 4);
        }
      }
    };

    raf = requestAnimationFrame(draw);
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    });
    ro.observe(cv);
    const mo = new MutationObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    });
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      mo.disconnect();
    };
  }, [channels, ampZoom, duration, sampleRate, peaks]);

  return <canvas className="wave-canvas" ref={ref} />;
}
