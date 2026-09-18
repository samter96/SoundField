// Player — Canvas-rendered waveform + segments + selection + transport.
// Uses rAF for 120fps playback animation. Segments derived from data.

const { useRef, useEffect, useState, useCallback, useMemo } = React;

function fmtTime(ms) {
  if (!ms || ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const ss = s % 60;
  const cs = Math.floor((ms % 1000) / 10);
  return `${m}:${String(ss).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}

function drawWaveform(ctx, opts) {
  const {
    w, h, peaks, channels, segments,
    posRatio, hoverSegIdx, activeSegIdx,
    selStart, selEnd, segmentsVisible, waveStyle,
    theme, segStyle, scanX, isLoading
  } = opts;

  ctx.clearRect(0, 0, w, h);

  // Background gradient
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, theme.waveBg1);
  bg.addColorStop(1, theme.waveBg2);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  // Grid
  ctx.strokeStyle = theme.waveGrid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 1; i < 12; i++) {
    const x = Math.floor(w * i / 12) + 0.5;
    ctx.moveTo(x, 0); ctx.lineTo(x, h);
  }
  ctx.stroke();

  if (isLoading) {
    // Scan bar
    const grad = ctx.createLinearGradient(scanX - 40, 0, scanX + 40, 0);
    grad.addColorStop(0, "rgba(0,0,0,0)");
    grad.addColorStop(0.5, theme.accentDim);
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.fillRect(scanX - 40, 0, 80, h);
    ctx.fillStyle = theme.text2;
    ctx.font = "600 11px Inter, system-ui";
    ctx.textAlign = "center";
    ctx.fillText("파형 로딩 중...", w / 2, h / 2 + 4);
    return;
  }

  if (!peaks || peaks.length === 0) return;

  const headerH = segmentsVisible && segments && segments.length > 1 ? 18 : 0;
  const waveTop = segStyle === "bracket" ? headerH : 0;
  const waveH = h - waveTop - (segStyle === "underline" && segmentsVisible ? 8 : 0);
  const chH = waveH / channels;
  const N = peaks.length / channels / 2;
  const posX = posRatio * w;

  // Draw waveform per channel
  function pathChannel(c, clipStart, clipEnd, fillGrad) {
    const mid = waveTop + (c + 0.5) * chH;
    const amp = chH / 2 - 3;
    ctx.save();
    ctx.beginPath();
    ctx.rect(clipStart, 0, clipEnd - clipStart, h);
    ctx.clip();

    if (waveStyle === "lines") {
      // Min and max as separate strokes
      ctx.strokeStyle = fillGrad;
      ctx.lineWidth = 1.2;
      ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = 0; i < N; i++) {
        const x = (i / N) * w;
        const k = (i * channels + c) * 2;
        const mn = peaks[k]; const mx = peaks[k + 1];
        ctx.moveTo(x + 0.5, mid - mx * amp);
        ctx.lineTo(x + 0.5, mid - mn * amp);
      }
      ctx.stroke();
    } else if (waveStyle === "mirror") {
      ctx.fillStyle = fillGrad;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      for (let i = 0; i < N; i++) {
        const x = (i / N) * w;
        const k = (i * channels + c) * 2;
        ctx.lineTo(x, mid - peaks[k + 1] * amp);
      }
      ctx.lineTo(w, mid);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(0, mid);
      for (let i = 0; i < N; i++) {
        const x = (i / N) * w;
        const k = (i * channels + c) * 2;
        ctx.lineTo(x, mid - peaks[k] * amp);
      }
      ctx.lineTo(w, mid);
      ctx.closePath();
      ctx.fill();
    } else {
      // bars (default) — polygon
      ctx.fillStyle = fillGrad;
      ctx.beginPath();
      for (let i = 0; i < N; i++) {
        const x = (i / N) * w;
        const k = (i * channels + c) * 2;
        if (i === 0) ctx.moveTo(x, mid - peaks[k + 1] * amp);
        else ctx.lineTo(x, mid - peaks[k + 1] * amp);
      }
      for (let i = N - 1; i >= 0; i--) {
        const x = (i / N) * w;
        const k = (i * channels + c) * 2;
        ctx.lineTo(x, mid - peaks[k] * amp);
      }
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }

  // Pre-build gradients
  const gUn = ctx.createLinearGradient(0, waveTop, 0, waveTop + waveH);
  gUn.addColorStop(0, theme.waveU1);
  gUn.addColorStop(0.5, theme.waveU2);
  gUn.addColorStop(1, theme.waveU3);
  const gPl = ctx.createLinearGradient(0, waveTop, 0, waveTop + waveH);
  gPl.addColorStop(0, theme.wavePl1);
  gPl.addColorStop(0.5, theme.wavePl2);
  gPl.addColorStop(1, theme.wavePl3);

  // Dim unplayed area before posX  (subtle)
  if (posX > 0) {
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.fillRect(0, waveTop, posX, waveH);
  }

  for (let c = 0; c < channels; c++) {
    // unplayed (right of posX)
    if (posX < w) pathChannel(c, posX, w, gUn);
    // played (left of posX)
    if (posX > 0) pathChannel(c, 0, posX, gPl);
  }

  // Channel separators
  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  ctx.lineWidth = 1;
  for (let c = 1; c < channels; c++) {
    const y = Math.floor(waveTop + c * chH) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  // Selection overlay
  if (selStart != null && selEnd != null && selEnd > selStart) {
    const x1 = selStart * w; const x2 = selEnd * w;
    ctx.fillStyle = "rgba(93,226,199,0.16)";
    ctx.fillRect(x1, 0, x2 - x1, h);
    ctx.strokeStyle = theme.teal;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x1 + 0.5, 0); ctx.lineTo(x1 + 0.5, h);
    ctx.moveTo(x2 - 0.5, 0); ctx.lineTo(x2 - 0.5, h);
    ctx.stroke();
  }

  // Segments
  if (segmentsVisible && segments && segments.length > 1) {
    for (let i = 0; i < segments.length; i++) {
      const [s, e] = segments[i];
      const x1 = s * w; const x2 = e * w;
      let fill, border;
      if (i === activeSegIdx)      { fill = "rgba(160,108,255,0.32)"; border = "rgba(192,144,255,0.7)"; }
      else if (i === hoverSegIdx)  { fill = "rgba(93,226,199,0.22)";  border = "rgba(125,240,205,0.7)"; }
      else                          { fill = "rgba(120,140,170,0.12)"; border = "rgba(140,160,190,0.34)"; }

      if (segStyle === "bracket") {
        ctx.fillStyle = fill;
        ctx.fillRect(x1, 0, x2 - x1, headerH);
        ctx.strokeStyle = border;
        ctx.lineWidth = 1;
        ctx.strokeRect(x1 + 0.5, 0.5, x2 - x1 - 1, headerH - 1);
        // segment index label
        if (x2 - x1 > 28) {
          ctx.fillStyle = i === activeSegIdx ? "#fff" : "rgba(255,255,255,0.6)";
          ctx.font = "700 9px JetBrains Mono, monospace";
          ctx.textAlign = "left";
          ctx.fillText(String(i + 1).padStart(2, "0"), x1 + 5, headerH - 5);
        }
      } else { // underline
        const uy = h - 4;
        ctx.strokeStyle = border;
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(x1 + 2, uy);
        ctx.lineTo(x2 - 2, uy);
        ctx.stroke();
        // tick marks above
        ctx.strokeStyle = "rgba(255,255,255,0.16)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x1 + 0.5, 0); ctx.lineTo(x1 + 0.5, h - 8);
        ctx.stroke();
      }
    }
    // vertical dividers in wave body
    if (segStyle === "bracket") {
      ctx.strokeStyle = "rgba(255,255,255,0.08)";
      ctx.lineWidth = 1;
      for (let i = 1; i < segments.length; i++) {
        const x = Math.floor(segments[i][0] * w) + 0.5;
        ctx.beginPath();
        ctx.moveTo(x, headerH); ctx.lineTo(x, h - 4); ctx.stroke();
      }
    }
  }

  // Playhead
  if (posX >= 0 && posX <= w) {
    // glow
    const glowGrad = ctx.createLinearGradient(posX - 8, 0, posX + 8, 0);
    glowGrad.addColorStop(0, "rgba(255,184,77,0)");
    glowGrad.addColorStop(0.5, "rgba(255,184,77,0.5)");
    glowGrad.addColorStop(1, "rgba(255,184,77,0)");
    ctx.fillStyle = glowGrad;
    ctx.fillRect(posX - 8, waveTop, 16, waveH);
    // line
    ctx.strokeStyle = theme.warm;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(Math.floor(posX) + 0.5, 0);
    ctx.lineTo(Math.floor(posX) + 0.5, h);
    ctx.stroke();
    // head dot
    ctx.fillStyle = theme.warm;
    ctx.beginPath();
    ctx.arc(posX, waveTop + 5, 4, 0, Math.PI * 2);
    ctx.fill();
  }
}

function getTheme(themeName) {
  const styles = getComputedStyle(document.body);
  return {
    text:      styles.getPropertyValue("--text").trim(),
    text2:     styles.getPropertyValue("--text-2").trim(),
    accent:    styles.getPropertyValue("--accent").trim(),
    accentDim: styles.getPropertyValue("--accent-dim").trim(),
    warm:      styles.getPropertyValue("--warm").trim(),
    teal:      styles.getPropertyValue("--teal").trim(),
    waveBg1:   styles.getPropertyValue("--wave-bg-1").trim(),
    waveBg2:   styles.getPropertyValue("--wave-bg-2").trim(),
    waveGrid:  styles.getPropertyValue("--wave-grid").trim(),
    waveU1:    styles.getPropertyValue("--wave-u-1").trim(),
    waveU2:    styles.getPropertyValue("--wave-u-2").trim(),
    waveU3:    styles.getPropertyValue("--wave-u-3").trim(),
    wavePl1:   styles.getPropertyValue("--wave-p-1").trim(),
    wavePl2:   styles.getPropertyValue("--wave-p-2").trim(),
    wavePl3:   styles.getPropertyValue("--wave-p-3").trim(),
  };
}

function Player({ file, isPlaying, posRatio, onSeek, onTogglePlay, onStop,
                  volume, speed, onVolume, onSpeed,
                  segmentsVisible, onToggleSegments, waveStyle, segStyle,
                  themeName }) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const audioRef = useRef(null);
  const [hoverSeg, setHoverSeg] = useState(-1);
  const [selRange, setSelRange] = useState({ start: null, end: null });
  const [isLoading, setIsLoading] = useState(false);
  const dragState = useRef({ mode: null, anchor: 0 });
  const scanXRef = useRef(0);
  const themeRef = useRef(null);
  const [bridgePeaks, setBridgePeaks] = useState(null);
  const [mediaUrl, setMediaUrl] = useState(null);
  const lastSeekRef = useRef(0);

  // 실제 audio 재생: mediaUrl 가 있을 때만 활성. 없으면 onSeek 으로 들어오는 시뮬레이션.
  useEffect(() => {
    const a = audioRef.current;
    if (!a) return;
    if (mediaUrl) { a.src = mediaUrl; a.load(); }
    else { try { a.removeAttribute("src"); a.load(); } catch (e) {} }
  }, [mediaUrl]);

  useEffect(() => {
    const a = audioRef.current;
    if (!a || !mediaUrl) return;
    if (isPlaying) a.play().catch(err => console.warn("audio play:", err));
    else a.pause();
  }, [isPlaying, mediaUrl]);

  useEffect(() => {
    const a = audioRef.current;
    if (a) a.volume = Math.max(0, Math.min(1, volume));
  }, [volume]);
  useEffect(() => {
    const a = audioRef.current;
    if (a) a.playbackRate = Math.max(0.25, Math.min(4, speed));
  }, [speed]);

  // 외부 onSeek (클릭/드래그) → audio.currentTime
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !mediaUrl || !file) return;
    // posRatio 가 audio 자체 timeupdate 와 충돌하지 않도록 — 차이가 0.01 이상일 때만 점프
    const target = posRatio * (file.duration_sec || 0);
    if (!isFinite(a.duration) || a.duration === 0) return;
    if (Math.abs(a.currentTime - target) > 0.15) {
      try { a.currentTime = target; } catch (e) {}
    }
  }, [posRatio, mediaUrl, file?.id]);

  // bridge 있으면 실제 peaks 로드, 없으면 mock generate
  useEffect(() => {
    if (!file) { setBridgePeaks(null); setMediaUrl(null); setIsLoading(false); return; }
    const b = window.bridge;
    if (b && b.loadPeaks && file.file_path) {
      setIsLoading(true);
      setBridgePeaks(null);
      let cancelled = false;
      b.loadPeaks(file.file_path).then((js) => {
        if (cancelled) return;
        try {
          const r = JSON.parse(js);
          if (r.ok) {
            setBridgePeaks({ peaks: r.peaks, channels: r.channels, segments: r.segments });
            setMediaUrl(r.mediaUrl || null);
          } else {
            console.warn("loadPeaks err:", r.error);
            setBridgePeaks({ peaks: [], channels: file.channels || 1, segments: [] });
          }
        } catch (e) { console.error("loadPeaks parse:", e); }
        setIsLoading(false);
      });
      return () => { cancelled = true; };
    } else {
      // mock fallback
      setIsLoading(true);
      const t = setTimeout(() => setIsLoading(false), 280);
      return () => clearTimeout(t);
    }
  }, [file?.id]);

  const peaksData = useMemo(() => {
    if (!file) return null;
    if (bridgePeaks) return bridgePeaks;
    // mock generation
    return SF_DATA.generatePeaks(file.kind, file.duration_sec, file.channels, file.seed);
  }, [file?.id, bridgePeaks]);

  // Reset selection when file changes
  useEffect(() => { setSelRange({ start: null, end: null }); }, [file?.id]);

  // Active segment index
  const activeSeg = useMemo(() => {
    if (!peaksData) return -1;
    const segs = peaksData.segments;
    for (let i = 0; i < segs.length; i++) {
      if (posRatio >= segs[i][0] && posRatio < segs[i][1]) return i;
    }
    return -1;
  }, [posRatio, peaksData]);

  // Refresh theme tokens when theme changes
  useEffect(() => {
    themeRef.current = getTheme(themeName);
  }, [themeName]);

  // Draw loop — mount 시 1회만 setup. tick 은 ref 에서 최신값 읽음 (re-effect 제거 → 안 끊김)
  const drawStateRef = useRef({});
  drawStateRef.current = {
    peaksData, posRatio, hoverSeg, activeSeg,
    selRange, segmentsVisible, waveStyle, segStyle, isLoading, themeName
  };
  useEffect(() => {
    let raf;
    function tick() {
      const canvas = canvasRef.current;
      if (!canvas || !containerRef.current) { raf = requestAnimationFrame(tick); return; }
      const rect = containerRef.current.getBoundingClientRect();
      const w = Math.max(1, Math.floor(rect.width));
      const h = Math.max(1, Math.floor(rect.height));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr; canvas.height = h * dpr;
        canvas.style.width = w + "px"; canvas.style.height = h + "px";
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const s = drawStateRef.current;
      const theme = themeRef.current || getTheme(s.themeName);
      scanXRef.current = (scanXRef.current + (w + 40) / 28) % (w + 80);

      drawWaveform(ctx, {
        w, h,
        peaks: s.peaksData?.peaks,
        channels: s.peaksData?.channels || 1,
        segments: s.peaksData?.segments || [],
        posRatio: s.posRatio,
        hoverSegIdx: s.hoverSeg,
        activeSegIdx: s.activeSeg,
        selStart: s.selRange.start,
        selEnd: s.selRange.end,
        segmentsVisible: s.segmentsVisible,
        waveStyle: s.waveStyle,
        segStyle: s.segStyle,
        theme,
        scanX: scanXRef.current,
        isLoading: s.isLoading
      });

      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Mouse handling
  function ratioAt(e) {
    const rect = containerRef.current.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  }
  function pixelY(e) {
    const rect = containerRef.current.getBoundingClientRect();
    return e.clientY - rect.top;
  }
  function hitSegment(e) {
    if (!peaksData || !segmentsVisible) return -1;
    if (segStyle === "bracket" && pixelY(e) > 18) return -1;
    if (segStyle === "underline" && pixelY(e) < containerRef.current.clientHeight - 12) return -1;
    const r = ratioAt(e);
    const segs = peaksData.segments;
    for (let i = 0; i < segs.length; i++) {
      if (r >= segs[i][0] && r < segs[i][1]) return i;
    }
    return -1;
  }

  function onMouseDown(e) {
    if (!file) return;
    if (e.button === 2) {
      setSelRange({ start: null, end: null });
      e.preventDefault();
      return;
    }
    const segIdx = hitSegment(e);
    if (segIdx >= 0) {
      const segs = peaksData.segments;
      onSeek(Math.max(0, segs[segIdx][0] - 0.005));
      return;
    }
    const r = ratioAt(e);
    if (selRange.start !== null && selRange.end !== null &&
        r >= selRange.start && r <= selRange.end) {
      dragState.current = { mode: "extdrag", anchor: r };
    } else {
      dragState.current = { mode: "select", anchor: r };
      setSelRange({ start: null, end: null });
    }
  }
  function onMouseMove(e) {
    setHoverSeg(hitSegment(e));
    if (!dragState.current.mode) return;
    const r = ratioAt(e);
    const dpx = Math.abs(r - dragState.current.anchor) * containerRef.current.clientWidth;
    if (dragState.current.mode === "select" && dpx >= 4) {
      setSelRange({
        start: Math.min(dragState.current.anchor, r),
        end:   Math.max(dragState.current.anchor, r)
      });
    } else if (dragState.current.mode === "extdrag" && dpx >= 6) {
      // simulate DAW drag (visual only)
      dragState.current = { mode: null, anchor: 0 };
      const el = document.createElement("div");
      el.textContent = "🎵 " + (file.file_name || "");
      el.className = "drag-ghost";
      Object.assign(el.style, {
        position: "fixed", left: e.clientX + 10 + "px", top: e.clientY + 10 + "px",
        background: "var(--bg-elev)", color: "var(--accent)", padding: "6px 12px",
        border: "1px solid var(--accent)", borderRadius: "4px", fontSize: "11px",
        pointerEvents: "none", zIndex: 9999, fontWeight: 700,
        boxShadow: "0 4px 16px rgba(0,0,0,0.4)"
      });
      document.body.appendChild(el);
      function move(ev) { el.style.left = ev.clientX + 10 + "px"; el.style.top = ev.clientY + 10 + "px"; }
      function up()    {
        el.style.transition = "opacity 200ms";
        el.style.opacity = 0;
        setTimeout(() => el.remove(), 220);
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", up);
      }
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", up);
    }
  }
  function onMouseUp(e) {
    if (!dragState.current.mode) return;
    if (dragState.current.mode === "select" && selRange.start === null) {
      onSeek(dragState.current.anchor);
    }
    dragState.current = { mode: null, anchor: 0 };
  }
  function onLeave() { setHoverSeg(-1); }

  const dur = file ? file.duration_sec * 1000 : 0;
  const posMs = dur * posRatio;
  const volPct = Math.round(volume * 100);
  const spdLabel = speed.toFixed(1) + "x";

  return (
    <div className="player">
      <audio ref={audioRef} preload="auto"
             onTimeUpdate={() => {
               const a = audioRef.current;
               if (!a || !file) return;
               const d = a.duration || file.duration_sec || 0;
               if (d > 0) onSeek(Math.max(0, Math.min(1, a.currentTime / d)));
             }}
             onEnded={() => { onStop && onStop(); }} />
      <div ref={containerRef}
           className={"wave-container" + (dragState.current.mode === "extdrag" ? " dragging" : "")}
           onMouseDown={onMouseDown}
           onMouseMove={onMouseMove}
           onMouseUp={onMouseUp}
           onMouseLeave={onLeave}
           onContextMenu={e => e.preventDefault()}>
        <canvas ref={canvasRef} />
        {!file && (
          <div className="wave-empty">
            <div className="icon">◐</div>
            <div>파일을 선택하면 파형이 표시됩니다</div>
          </div>
        )}
      </div>
      <div className="player-controls">
        <div className="transport">
          <button className={"btn play" + (isPlaying ? " playing" : "")}
                  onClick={onTogglePlay}
                  disabled={!file}
                  data-tip={isPlaying ? "정지 (Space)" : "재생 (Space)"}>
            {isPlaying ? "⏸" : "▶"}
          </button>
          <button className="btn icon"
                  onClick={onStop}
                  disabled={!file}
                  data-tip="처음으로">⏮</button>
        </div>
        <div className="time-display">
          <span>{fmtTime(posMs)}</span>
          <span className="total"> / {fmtTime(dur)}</span>
        </div>
        <div className="now-playing">
          {file ? (
            <>
              <span className="name">{file.file_name}</span>
              <span className="meta">{file.channels}ch · {(file.sample_rate/1000).toFixed(1)}k · {file.bit_depth}b</span>
            </>
          ) : (
            <span className="label" style={{ color: "var(--text-3)" }}>NO FILE LOADED</span>
          )}
        </div>
        <div className="knob-group">
          <span className="knob-label">SPD</span>
          <input type="range" min="0.5" max="2" step="0.1" value={speed}
                 className="slider"
                 onChange={e => onSpeed(parseFloat(e.target.value))}
                 onContextMenu={e => { e.preventDefault(); onSpeed(1.0); }}
                 title="우클릭: 1.0x 리셋 / 더블클릭: 직접 입력"
                 style={{ "--fill": ((speed - 0.5) / 1.5 * 100) + "%" }} />
          <span className="knob-value" title="더블클릭: 직접 입력"
                onDoubleClick={() => {
                  const v = prompt("재생 속도 (0.5 ~ 2.0)", speed.toFixed(2));
                  if (v == null) return;
                  const n = parseFloat(String(v).replace(/x/i, ""));
                  if (!isNaN(n)) onSpeed(Math.max(0.5, Math.min(2, n)));
                }}>{spdLabel}</span>
        </div>
        <div className="knob-group">
          <span className="knob-label">VOL</span>
          <input type="range" min="0" max="1" step="0.01" value={volume}
                 className="slider"
                 onChange={e => onVolume(parseFloat(e.target.value))}
                 onContextMenu={e => { e.preventDefault(); onVolume(0.5); }}
                 title="우클릭: 50 리셋 / 더블클릭: 직접 입력"
                 style={{ "--fill": (volume * 100) + "%" }} />
          <span className="knob-value" title="더블클릭: 직접 입력"
                onDoubleClick={() => {
                  const v = prompt("볼륨 (0 ~ 100)", String(volPct));
                  if (v == null) return;
                  const n = parseFloat(v);
                  if (!isNaN(n)) onVolume(Math.max(0, Math.min(1, n / 100)));
                }}>{volPct}</span>
        </div>
        <button className={"btn icon" + (segmentsVisible ? " active" : "")}
                onClick={onToggleSegments}
                data-tip="세그먼트 토글 (S)">▥</button>
      </div>
    </div>
  );
}

window.SoundFieldPlayer = Player;
