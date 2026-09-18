// SoundField — Main App
// Wires everything together. Manages all state, runs playback simulation rAF.

const { useState, useEffect, useMemo, useRef, useCallback } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "layout": "classic",
  "theme": "slate",
  "density": "regular",
  "radius": "2",
  "waveStyle": "bars",
  "segStyle": "bracket"
}/*EDITMODE-END*/;

function applyTweaksToBody(t) {
  document.body.dataset.theme   = t.theme;
  document.body.dataset.density = t.density;
  document.body.dataset.radius  = t.radius;
}

function filterAndSort(files, query, filters, folderPath, sortKey, sortDir) {
  const { minDur, maxDur, sr, ch } = filters;
  const queries = query.filter(r => r.query.trim()).map(r => ({
    field: r.field, matcher: r.matcher,
    q: r.query.trim().toLowerCase()
  }));

  function match(f) {
    if (folderPath && folderPath !== "Y:/[Library]" && !f.file_path.startsWith(folderPath + "/")) return false;
    if (minDur > 0 && f.duration_sec < minDur) return false;
    if (maxDur > 0 && f.duration_sec > maxDur) return false;
    if (sr > 0 && f.sample_rate !== sr) return false;
    if (ch > 0 && f.channels !== ch) return false;
    if (queries.length === 0) return true;
    // First always AND. Then evaluate left-to-right with AND/OR/NOT
    let result = true;
    let firstHandled = false;
    for (const q of queries) {
      const hay = q.field === "all"
        ? (f.file_name + " " + f.file_path + " " + f.title + " " + f.description + " " + f.keywords).toLowerCase()
        : String(f[q.field] || "").toLowerCase();
      const has = hay.includes(q.q);
      if (!firstHandled) {
        result = has; firstHandled = true;
      } else if (q.matcher === "AND") result = result && has;
      else if (q.matcher === "OR")    result = result || has;
      else if (q.matcher === "NOT")   result = result && !has;
    }
    return result;
  }

  const list = files.filter(match);

  const key = sortKey === "duration" ? "duration_sec" : sortKey;
  list.sort((a, b) => {
    let va = a[key], vb = b[key];
    if (typeof va === "string") {
      return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    }
    return sortDir === "asc" ? (va - vb) : (vb - va);
  });

  return list;
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // Bridge & backend data
  const [bridge, setBridge] = useState(null);
  const [tree, setTree] = useState(SF_DATA.TREE);   // 폴백: mock
  const [libraries, setLibraries] = useState([]);

  // Search & filter state
  const [searchRows, setSearchRows] = useState([{ field: "all", matcher: "AND", query: "" }]);
  const [filters, setFilters] = useState({ minDur: 0, maxDur: 0, sr: 0, ch: 0 });
  const [selectedFolder, setSelectedFolder] = useState("Y:/[Library]");
  const [sortKey, setSortKey] = useState("file_name");
  const [sortDir, setSortDir] = useState("asc");
  const [backendResults, setBackendResults] = useState(null);  // null = 아직 미로딩

  // Selection / playback
  const [selectedId, setSelectedId] = useState(null);
  const [playingId, setPlayingId]   = useState(null);
  const [isPlaying, setIsPlaying]   = useState(false);
  const [posRatio, setPosRatio]     = useState(0);
  const [volume, setVolume]         = useState(0.8);
  const [speed, setSpeed]           = useState(1.0);
  const [segmentsVisible, setSegVis] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState([]);

  // Indexing simulation
  const [indexing, setIndexing] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus]     = useState("라이브러리 1개 · 준비됨");

  // Columns (X 닫기 + 드래그 reorder) — localStorage 저장
  const [cols, setCols] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("sf_cols") || "null");
      if (Array.isArray(saved) && saved.length) {
        const map = Object.fromEntries(SF_ALL_COLS.map(c => [c.key, c]));
        return saved.map(k => map[k]).filter(Boolean);
      }
    } catch (e) {}
    return SF_ALL_COLS.slice();
  });
  useEffect(() => {
    try { localStorage.setItem("sf_cols", JSON.stringify(cols.map(c => c.key))); } catch (e) {}
  }, [cols]);
  function closeCol(key) { setCols(cs => cs.filter(c => c.key !== key)); }
  function reorderCols(from, to) {
    setCols(cs => {
      const next = cs.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }
  function resetCols() { setCols(SF_ALL_COLS.slice()); }

  // Context menu (결과 + 트리 root 공용)
  const [ctxMenu, setCtxMenu] = useState({ open: false, x: 0, y: 0, file: null, rootNode: null });

  // Apply tweaks to body
  useEffect(() => { applyTweaksToBody(t); }, [t.theme, t.density, t.radius]);

  // Bridge 연결 + 초기 데이터 + 인덱싱 시그널 구독
  useEffect(() => {
    if (!window.__bridgeReady) return;
    window.__bridgeReady.then(async (b) => {
      if (!b) { console.warn("bridge unavailable, using mock"); return; }
      setBridge(b);
      try {
        const treeJson = await b.buildTree();
        if (treeJson) setTree(JSON.parse(treeJson));
        const libsJson = await b.listLibraries();
        if (libsJson) setLibraries(JSON.parse(libsJson));
      } catch (e) { console.error("initial load failed:", e); }

      // 인덱싱 진행률 시그널
      if (b.indexProgress && b.indexProgress.connect) {
        b.indexProgress.connect((js) => {
          try {
            const p = JSON.parse(js);
            const folder = p.folder ? ` · 폴더: ${p.folder}` : "";
            if (p.phase === "scan") {
              setIndexing(true);
              setProgress(0);
              setStatus(
                `1단계/2 · ${p.scanned.toLocaleString()}개 발견${p.elapsed_text ? " · " + p.elapsed_text : ""}` +
                ` · 예상시간: 산정 중${folder}`
              );
            } else if (p.phase === "meta") {
              setIndexing(true);
              setProgress(p.percent || 0);
              const eta = p.eta_text ? ` · 예상시간: ${p.eta_text}` : " · 예상시간: 계산 중";
              setStatus(
                `2단계/2 · 분석 중 · ${p.percent}% (${(p.indexed + p.errors).toLocaleString()}/${p.total.toLocaleString()})${eta}${folder}`
              );
            } else if (p.phase === "done") {
              setIndexing(false);
              setProgress(100);
              setStatus(p.message || "완료");
            }
          } catch (e) { console.error("indexProgress parse:", e); }
        });
      }
      if (b.indexFinished && b.indexFinished.connect) {
        b.indexFinished.connect(async (js) => {
          setIndexing(false);
          setProgress(100);
          try {
            const treeJson = await b.buildTree();
            if (treeJson) setTree(JSON.parse(treeJson));
            const libsJson = await b.listLibraries();
            if (libsJson) setLibraries(JSON.parse(libsJson));
          } catch (e) { /* ignore */ }
        });
      }
      if (b.libraryChanged && b.libraryChanged.connect) {
        b.libraryChanged.connect(async (js) => {
          try { setLibraries(JSON.parse(js)); } catch (e) { /* ignore */ }
          try {
            const treeJson = await b.buildTree();
            if (treeJson) setTree(JSON.parse(treeJson));
          } catch (e) { /* ignore */ }
        });
      }
      if (b.statusChanged && b.statusChanged.connect) {
        b.statusChanged.connect((msg) => setStatus(msg));
      }
    });
  }, []);

  // 서버 검색 (bridge 가 있을 때만). debounce 80ms.
  useEffect(() => {
    if (!bridge) return;
    const params = {
      matchers: searchRows
        .filter(r => r.query && r.query.trim())
        .map(r => ({
          field: r.field === "all" ? "any" : r.field,
          value: r.query.trim(),
        })),
      min_duration: filters.minDur || 0,
      max_duration: filters.maxDur || 0,
      sample_rate: filters.sr || 0,
      channels: filters.ch || 0,
      path_prefix: (typeof selectedFolder === "string" && selectedFolder && selectedFolder !== "Y:/[Library]"
                    ? selectedFolder.replace(/\//g, "\\") : ""),
      limit: 500,
    };
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const resJson = await bridge.search(JSON.stringify(params));
        const res = JSON.parse(resJson);
        if (cancelled) return;
        if (res.ok) setBackendResults(res.rows);
        else console.error("search err:", res.error);
      } catch (e) { console.error("search failed:", e); }
    }, 200);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [bridge, searchRows, filters, selectedFolder]);

  // Filter and sort — backend 결과가 있으면 사용, 없으면 mock
  const results = useMemo(() => {
    if (backendResults !== null) {
      // 정렬만 client-side
      const key = sortKey === "duration" ? "duration_sec" : sortKey;
      const sorted = [...backendResults].sort((a, b) => {
        let va = a[key], vb = b[key];
        if (typeof va === "string") {
          return sortDir === "asc" ? (va || "").localeCompare(vb || "") : (vb || "").localeCompare(va || "");
        }
        return sortDir === "asc" ? ((va || 0) - (vb || 0)) : ((vb || 0) - (va || 0));
      });
      return sorted;
    }
    return filterAndSort(SF_DATA.FILES, searchRows, filters, selectedFolder, sortKey, sortDir);
  }, [backendResults, searchRows, filters, selectedFolder, sortKey, sortDir]);

  // Status text
  useEffect(() => {
    if (indexing) return;
    const scope = selectedFolder !== "Y:/[Library]" ? ` · 폴더: ${selectedFolder.split("/").pop()}` : "";
    setStatus(`결과 ${results.length.toLocaleString()}개${scope}`);
  }, [results.length, selectedFolder, indexing]);

  // Playback — bridge audio 모드면 player 의 <audio> 가 posRatio 갱신.
  // mock 모드 (bridge 없음) 일 때만 rAF 로 시뮬레이션.
  const playStart = useRef({ t0: 0, p0: 0 });
  const fileById = useCallback((id) => {
    if (id == null) return null;
    return (results && results.find(f => f.id === id))
        || SF_DATA.FILES.find(f => f.id === id)
        || null;
  }, [results]);
  const playingFile = useMemo(() => fileById(playingId), [playingId, fileById]);
  useEffect(() => {
    if (!isPlaying || !playingFile) return;
    // bridge 모드: <audio> 가 onTimeUpdate 로 posRatio 갱신 — rAF 불필요
    if (bridge) return;
    // mock 모드 fallback: rAF 시뮬레이션
    playStart.current = { t0: performance.now(), p0: posRatio };
    let raf;
    function tick(now) {
      const dt = (now - playStart.current.t0) / 1000;
      const dur = playingFile.duration_sec;
      const newPos = playStart.current.p0 + (dt * speed) / dur;
      if (newPos >= 1) {
        setPosRatio(1);
        setIsPlaying(false);
        return;
      }
      setPosRatio(newPos);
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [isPlaying, playingFile, speed, bridge]);

  // Activate (double-click): play this file
  function onActivate(id) {
    const f = fileById(id);
    if (!f) return;
    setSelectedId(id);
    setPlayingId(id);
    setPosRatio(0);
    setIsPlaying(true);
    setHistory(h => [...h.filter(x => x !== id), id].slice(-100));
  }
  function onTogglePlay() {
    if (!playingId) {
      if (selectedId) onActivate(selectedId);
      return;
    }
    setIsPlaying(p => !p);
  }
  function onStop() {
    setPosRatio(0);
    setIsPlaying(false);
  }
  function onSeek(r) {
    setPosRatio(r);
    playStart.current = { t0: performance.now(), p0: r };
  }
  function onSort(key) {
    if (sortKey === key) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDir("asc"); }
  }

  // Indexing — bridge 가 있으면 real, 없으면 시뮬레이션 (롤백 안전망)
  function startIndex() {
    if (indexing) return;
    if (bridge && bridge.startIndex) {
      bridge.startIndex("");  // 전체 인덱스
      return;
    }
    // 시뮬레이션 fallback (mock 모드)
    setIndexing(true);
    setProgress(0);
    setStatus("1단계/2 · 파일 목록 작성 중...");
    let p = 0;
    const step = () => {
      p += 1.6 + Math.random() * 2.4;
      if (p < 20) {
        setProgress(0);
        setStatus(`1단계/2 · ${Math.floor(p * 380).toLocaleString()}개 발견`);
      } else if (p < 100) {
        const indexed = Math.floor((p - 20) / 80 * SF_DATA.FILES.length);
        setProgress(Math.round((p - 20) / 80 * 100));
        setStatus(`2단계/2 · 분석 중 · ${indexed.toLocaleString()}/${SF_DATA.FILES.length.toLocaleString()}`);
      } else {
        setIndexing(false); setProgress(100);
        setStatus(`완료 · ${SF_DATA.FILES.length.toLocaleString()}개 인덱싱`);
        return;
      }
      setTimeout(step, 180 + Math.random() * 220);
    };
    setTimeout(step, 200);
  }
  function cancelIndex() {
    if (bridge && bridge.cancelIndex) { bridge.cancelIndex(); return; }
    setIndexing(false);
    setStatus("취소됨 — 스캔된 파일은 보관됨.");
  }
  function addLibrary() {
    if (bridge && bridge.pickAndAddLibrary) {
      bridge.pickAndAddLibrary();
    } else {
      startIndex();  // mock 모드 fallback
    }
  }

  // Keyboard shortcuts (PyQt 버전과 동일: Space=재생토글, S=세그먼트토글)
  useEffect(() => {
    function onKey(e) {
      const t = e.target;
      const tag = t && t.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (t && t.isContentEditable) return;
      if (e.code === "Space") { e.preventDefault(); onTogglePlay(); }
      else if (e.key === "s" || e.key === "S") setSegVis(v => !v);
      else if (e.key === "Escape" && indexing) { cancelIndex(); }
      else if (e.key === "F5") { e.preventDefault(); if (!indexing) startIndex(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playingId, selectedId, indexing]);

  const libInfo = (libraries.length > 0)
    ? {
        count: libraries.reduce((a, b) => a + (b.count || 0), 0),
        lastScan: libraries[0]?.last_indexed_at
          ? new Date(libraries[0].last_indexed_at * 1000).toLocaleString("ko-KR")
          : "—",
      }
    : { count: SF_DATA.FILES.length, lastScan: "2026-05-13 14:22" };
  const layoutClass = "main " + t.layout;

  return (
    <div className="app">
      <SF_Topbar libCount={libInfo.count} indexing={indexing}
                 onAdd={addLibrary} onUpdate={startIndex} onCancel={cancelIndex} />
      <div className={layoutClass}>
        <SF_Sidebar tree={tree}
                    selectedPath={selectedFolder}
                    onSelect={setSelectedFolder}
                    onRootContext={(e, node) => {
                      setCtxMenu({ open: true, x: e.clientX, y: e.clientY, file: null, rootNode: node });
                    }} />
        <div className="center">
          <SF_MultiSearch rows={searchRows} onChange={setSearchRows} />
          <SF_Filters {...filters}
                      onChange={setFilters}
                      libInfo={libInfo}
                      historyOpen={historyOpen}
                      onToggleHistory={() => setHistoryOpen(o => !o)} />
          <div className="results-wrap">
            <SF_Results rows={results}
                        cols={cols}
                        selectedId={selectedId}
                        playingId={playingId}
                        onSelect={setSelectedId}
                        onActivate={onActivate}
                        sortKey={sortKey} sortDir={sortDir}
                        onSort={onSort}
                        onCloseCol={closeCol}
                        onReorderCols={reorderCols}
                        onRowContext={(e, f) => {
                          e.preventDefault();
                          setCtxMenu({ open: true, x: e.clientX, y: e.clientY, file: f });
                        }} />
            {t.layout !== "triptych" && (
              <SF_History open={historyOpen}
                          history={history}
                          files={results}
                          onPick={onActivate}
                          onClear={() => setHistory([])} />
            )}
          </div>
          <SoundFieldPlayer file={playingFile}
                            isPlaying={isPlaying}
                            posRatio={posRatio}
                            onSeek={onSeek}
                            onTogglePlay={onTogglePlay}
                            onStop={onStop}
                            volume={volume}
                            speed={speed}
                            onVolume={setVolume}
                            onSpeed={setSpeed}
                            segmentsVisible={segmentsVisible}
                            onToggleSegments={() => setSegVis(v => !v)}
                            waveStyle={t.waveStyle}
                            segStyle={t.segStyle}
                            themeName={t.theme} />
        </div>
        {t.layout === "triptych" && (
          <SF_History open={true}
                      history={history}
                      files={results}
                      onPick={onActivate}
                      onClear={() => setHistory([])} />
        )}
      </div>
      <SF_Status status={status} indexing={indexing} progress={progress}
                 count={libInfo.count} />

      <SF_ContextMenu open={ctxMenu.open} x={ctxMenu.x} y={ctxMenu.y}
                      onClose={() => setCtxMenu({ open: false, x: 0, y: 0, file: null, rootNode: null })}
                      items={
                        ctxMenu.file ? [
                          { label: "탐색기에서 보기",
                            onClick: () => bridge && bridge.revealInExplorer(ctxMenu.file.file_path) },
                          { label: "경로 복사",
                            onClick: () => navigator.clipboard.writeText(ctxMenu.file.file_path) },
                          { label: "컬럼 초기화",
                            onClick: () => resetCols() },
                        ]
                        : ctxMenu.rootNode ? [
                          { label: "이 라이브러리 재스캔",
                            onClick: () => {
                              if (!bridge) return;
                              const p = ctxMenu.rootNode.path.replace(/\//g, "\\");
                              bridge.startIndex(p);
                            } },
                          { label: "라이브러리 제거",
                            onClick: () => {
                              if (!bridge) return;
                              const p = ctxMenu.rootNode.path.replace(/\//g, "\\");
                              if (confirm(`[${p}]\n이 라이브러리 인덱스를 제거할까요?\n(실제 파일은 삭제되지 않습니다.)`)) {
                                bridge.removeLibrary(p);
                              }
                            } },
                          { label: "탐색기에서 열기",
                            onClick: () => bridge && bridge.revealInExplorer(ctxMenu.rootNode.path.replace(/\//g, "\\")) },
                        ]
                        : []
                      } />

      <TweaksPanel>
        <TweakSection label="Layout" />
        <TweakRadio label="Arrangement" value={t.layout}
                    options={["classic", "stage", "triptych"]}
                    onChange={v => setTweak("layout", v)} />
        <TweakSection label="Visual" />
        <TweakRadio label="Theme" value={t.theme}
                    options={["slate", "carbon", "ember"]}
                    onChange={v => setTweak("theme", v)} />
        <TweakRadio label="Density" value={t.density}
                    options={["compact", "regular", "comfy"]}
                    onChange={v => setTweak("density", v)} />
        <TweakRadio label="Corner radius" value={t.radius}
                    options={["0", "2", "4", "8"]}
                    onChange={v => setTweak("radius", v)} />
        <TweakSection label="Waveform" />
        <TweakRadio label="Style" value={t.waveStyle}
                    options={["bars", "lines", "mirror"]}
                    onChange={v => setTweak("waveStyle", v)} />
        <TweakRadio label="Segments" value={t.segStyle}
                    options={["bracket", "underline"]}
                    onChange={v => setTweak("segStyle", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
