// SoundField — Sidebar / Topbar / Search / Filters / Results / History / StatusBar
// All small interactive components.

const { useState: useState_p, useEffect: useEffect_p, useRef: useRef_p, useMemo: useMemo_p } = React;

// ─── Topbar ───────────────────────────────────────────────────
function Topbar({ libCount, indexing, onAdd, onUpdate, onCancel }) {
  return (
    <div className="topbar">
      <div className="brand">
        <span className="mark"><span></span></span>
        <span className="name">SoundField</span>
        <span className="sub">SOUND EFFECT LIBRARY</span>
      </div>
      <div className="spacer"></div>
      <button className="btn primary" onClick={onAdd}>
        <span>＋</span>
        <span>라이브러리 추가</span>
      </button>
      <button className="btn pill" onClick={onUpdate} disabled={indexing}>
        <span>↻</span>
        <span>인덱스 갱신</span>
      </button>
      <button className="btn pill danger" onClick={onCancel} disabled={!indexing}>
        <span>■</span>
        <span>취소</span>
      </button>
    </div>
  );
}

// ─── Folder Tree ──────────────────────────────────────────────
function TreeNode({ node, depth, expanded, onToggle, selectedPath, onSelect, isRoot, onRootContext }) {
  const hasKids = node.children && node.children.length > 0;
  const isOpen = expanded[node.path];
  const isSel  = selectedPath === node.path;
  return (
    <>
      <div className={"tree-node" + (hasKids ? (isOpen ? " expanded" : "") : " leaf") + (isSel ? " selected" : "")}
           style={{ paddingLeft: 8 + depth * 12 }}
           onClick={() => onSelect(node.path)}
           onContextMenu={(node.isRoot || isRoot) && onRootContext
             ? (e) => { e.preventDefault(); onRootContext(e, node); } : undefined}>
        <span className="tree-caret"
              onClick={(e) => { e.stopPropagation(); if (hasKids) onToggle(node.path); }}>
          {hasKids ? "▸" : ""}
        </span>
        {isRoot && <span className="lib-dot"></span>}
        <span className="tree-label">{node.name}</span>
        <span className="tree-count num">{node.count.toLocaleString()}</span>
      </div>
      {hasKids && isOpen && node.children.map(c =>
        <TreeNode key={c.path} node={c} depth={depth + 1} expanded={expanded}
                  onToggle={onToggle} selectedPath={selectedPath} onSelect={onSelect}
                  onRootContext={onRootContext} />
      )}
    </>
  );
}

function Sidebar({ tree, selectedPath, onSelect, onRootContext }) {
  const [expanded, setExpanded] = useState_p(() => {
    const m = {}; m[tree.path] = true;
    return m;
  });
  function onToggle(path) { setExpanded(p => ({ ...p, [path]: !p[path] })); }
  return (
    <div className="sidebar">
      <div className="sidebar-head">
        <span>폴더</span>
        <span className="badge num">{tree.count.toLocaleString()}</span>
      </div>
      <div className="tree">
        <TreeNode node={tree} depth={0} expanded={expanded} onToggle={onToggle}
                  selectedPath={selectedPath} onSelect={onSelect} isRoot={true}
                  onRootContext={onRootContext} />
      </div>
    </div>
  );
}

// ─── Dropdown ─────────────────────────────────────────────────
function Dropdown({ items, value, onChange, onClose, anchorRef }) {
  const ref = useRef_p(null);
  useEffect_p(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);
  const pos = useMemo_p(() => {
    if (!anchorRef.current) return { left: 0, top: 0 };
    const r = anchorRef.current.getBoundingClientRect();
    return { left: r.left, top: r.bottom + 4 };
  }, [anchorRef.current]);
  return (
    <div className="dropdown" ref={ref} style={{ left: pos.left, top: pos.top }}>
      {items.map(it => (
        <div key={it.value}
             className={"dropdown-item" + (it.value === value ? " selected" : "")}
             onClick={() => { onChange(it.value); onClose(); }}>
          <span className="check">{it.value === value ? "●" : ""}</span>
          <span>{it.label}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Multi Search Row ─────────────────────────────────────────
const FIELDS = [
  { value: "file_name",   label: "File Name" },
  { value: "file_path",   label: "Path" },
  { value: "title",       label: "Title" },
  { value: "description", label: "Description" },
  { value: "keywords",    label: "Keywords" },
  { value: "category",    label: "Category" },
  { value: "all",         label: "ALL FIELDS" },
];
const MATCHERS = ["AND", "OR", "NOT"];

const SearchRow = React.forwardRef(function SearchRow(
  { row, idx, onChange, onRemove, canRemove, onTabNext }, inputRef
) {
  const [fieldOpen, setFieldOpen] = useState_p(false);
  const fieldRef = useRef_p(null);
  return (
    <div className="search-row">
      <div ref={fieldRef} className="field-picker" onClick={() => setFieldOpen(o => !o)}>
        <span>{(FIELDS.find(f => f.value === row.field) || FIELDS[0]).label}</span>
        <span className="caret">▼</span>
      </div>
      {fieldOpen && (
        <Dropdown items={FIELDS} value={row.field}
                  onChange={v => onChange({ ...row, field: v })}
                  onClose={() => setFieldOpen(false)}
                  anchorRef={fieldRef} />
      )}
      {idx > 0 && (
        <div className={"matcher " + row.matcher}
             onClick={() => {
               const next = MATCHERS[(MATCHERS.indexOf(row.matcher) + 1) % MATCHERS.length];
               onChange({ ...row, matcher: next });
             }}>{row.matcher}</div>
      )}
      <input ref={inputRef} className="search-input"
             placeholder={idx === 0 ? "사운드 검색... (예: footstep wood)" : "추가 키워드"}
             value={row.query}
             onChange={e => onChange({ ...row, query: e.target.value })}
             onKeyDown={e => {
               if (e.key === "Tab" && !e.shiftKey) {
                 e.preventDefault();
                 onTabNext && onTabNext();
               }
             }} />
      {canRemove && (
        <button className="row-clear" onClick={onRemove} title="제거">✕</button>
      )}
    </div>
  );
});

function MultiSearch({ rows, onChange }) {
  const refs = useRef_p([]);
  refs.current = rows.map((_, i) => refs.current[i] || React.createRef());
  const focusNext = useRef_p(-1);

  function updateRow(i, r) {
    const next = rows.slice();
    next[i] = r;
    onChange(next);
  }
  function removeRow(i) {
    const next = rows.slice();
    next.splice(i, 1);
    onChange(next);
  }
  function addRow() {
    onChange([...rows, { field: "all", matcher: "AND", query: "" }]);
  }
  function onTabNext(i) {
    const nextIdx = i + 1;
    if (nextIdx < rows.length) {
      refs.current[nextIdx]?.current?.focus();
    } else {
      focusNext.current = nextIdx;  // 새 row 생성 후 focus
      addRow();
    }
  }
  // 새 row 생성 후 focus
  useEffect_p(() => {
    if (focusNext.current >= 0 && refs.current[focusNext.current]) {
      refs.current[focusNext.current].current?.focus();
      focusNext.current = -1;
    }
  }, [rows.length]);

  return (
    <div className="search-zone">
      <div className="search-rows">
        {rows.map((row, i) => (
          <SearchRow key={i} row={row} idx={i} ref={refs.current[i]}
                     onChange={r => updateRow(i, r)}
                     onRemove={() => removeRow(i)}
                     onTabNext={() => onTabNext(i)}
                     canRemove={rows.length > 1} />
        ))}
        <button className="row-add" onClick={addRow}>+ 검색 조건 추가</button>
      </div>
    </div>
  );
}

// ─── Filters ──────────────────────────────────────────────────
function Filters({ minDur, maxDur, sr, ch, onChange, libInfo, historyOpen, onToggleHistory }) {
  const srRef = useRef_p(null), chRef = useRef_p(null);
  const [srOpen, setSrOpen] = useState_p(false), [chOpen, setChOpen] = useState_p(false);
  const srItems = [
    { value: 0, label: "전체" }, { value: 44100, label: "44.1k" },
    { value: 48000, label: "48k" }, { value: 96000, label: "96k" },
  ];
  const chItems = [
    { value: 0, label: "전체" }, { value: 1, label: "Mono" },
    { value: 2, label: "Stereo" }, { value: 6, label: "5.1 / 6ch" },
  ];
  return (
    <div className="filters">
      <div className="filt">
        <span>Length</span>
        <input className="ctrl num" type="number" min="0" value={minDur}
               onChange={e => onChange({ minDur: +e.target.value, maxDur, sr, ch })} placeholder="0" />
        <span className="sep">~</span>
        <input className="ctrl num" type="number" min="0" value={maxDur}
               onChange={e => onChange({ minDur, maxDur: +e.target.value, sr, ch })} placeholder="∞" />
        <span style={{ color: "var(--text-3)" }}>s</span>
      </div>
      <div className="filt">
        <span>Sample Rate</span>
        <div ref={srRef} className="ctrl" onClick={() => setSrOpen(o => !o)}>
          {(srItems.find(i => i.value === sr) || srItems[0]).label}
          <span style={{ marginLeft: "auto", color: "var(--text-3)", fontSize: 9 }}>▼</span>
        </div>
        {srOpen && (
          <Dropdown items={srItems} value={sr}
                    onChange={v => onChange({ minDur, maxDur, sr: v, ch })}
                    onClose={() => setSrOpen(false)} anchorRef={srRef} />
        )}
      </div>
      <div className="filt">
        <span>Channels</span>
        <div ref={chRef} className="ctrl" onClick={() => setChOpen(o => !o)}>
          {(chItems.find(i => i.value === ch) || chItems[0]).label}
          <span style={{ marginLeft: "auto", color: "var(--text-3)", fontSize: 9 }}>▼</span>
        </div>
        {chOpen && (
          <Dropdown items={chItems} value={ch}
                    onChange={v => onChange({ minDur, maxDur, sr, ch: v })}
                    onClose={() => setChOpen(false)} anchorRef={chRef} />
        )}
      </div>
      <div style={{ flex: 1 }}></div>
      <div className="lib-status-pill tip" data-tip={`마지막 스캔: ${libInfo.lastScan}`}>
        <span className="dot"></span>
        <span>Library</span>
        <span className="num">{libInfo.count.toLocaleString()}</span>
      </div>
      <button className={"btn pill" + (historyOpen ? " active" : "")}
              onClick={onToggleHistory}>
        <span>{historyOpen ? "▶" : "◀"}</span>
        <span>히스토리</span>
      </button>
    </div>
  );
}

// ─── Results Table ────────────────────────────────────────────
const ALL_COLS = [
  { key: "file_name",   label: "파일명",      cls: "fname" },
  { key: "file_path",   label: "경로",        cls: "path" },
  { key: "duration",    label: "길이",        cls: "num", align: "right" },
  { key: "sample_rate", label: "샘플레이트",  cls: "num", align: "right" },
  { key: "channels",    label: "Ch",          cls: "num", align: "right" },
  { key: "bit_depth",   label: "Bit",         cls: "num", align: "right" },
  { key: "category",    label: "카테고리",    cls: "" },
  { key: "size_mb",     label: "크기",        cls: "num", align: "right" },
];

function fmtDur(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec - m * 60);
  const cs = Math.floor((sec - Math.floor(sec)) * 10);
  return m > 0 ? `${m}:${String(s).padStart(2,"0")}` : `${s}.${cs}s`;
}

// row 1개 렌더 — 컬럼 순서/표시 동적
function renderCell(c, f) {
  switch (c.key) {
    case "file_name":
      return (<>
        <span className="play-pip"></span>
        <span className={"fmt " + f.format}>{f.format}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{f.file_name}</span>
      </>);
    case "file_path":   return (f.folder || "").replace("Y:/[Library]/", "/");
    case "duration":    return fmtDur(f.duration_sec);
    case "sample_rate": return (f.sample_rate / 1000).toFixed(1) + "k";
    case "channels":    return f.channels;
    case "bit_depth":   return f.bit_depth;
    case "category":    return (<>{f.category} <span style={{ color: "var(--text-3)" }}>/ {f.sub_category}</span></>);
    case "size_mb":     return f.size_mb + "MB";
    default: return "";
  }
}

const ResultsRow = React.memo(function ResultsRow({ f, cols, isSelected, isPlaying, onSelect, onActivate, onContextMenu }) {
  return (
    <div className={"tbl-row" + (isSelected ? " selected" : "") + (isPlaying ? " playing" : "")}
         onClick={() => onSelect(f.id)}
         onDoubleClick={() => onActivate(f.id)}
         onContextMenu={onContextMenu ? (e) => onContextMenu(e, f) : undefined}>
      {cols.map(c => (
        <div key={c.key} className={"col " + c.cls}
             style={{ textAlign: c.align || "left" }}>
          {renderCell(c, f)}
        </div>
      ))}
    </div>
  );
});

function ResultsTable({ rows, cols, selectedId, playingId, onSelect, onActivate,
                        sortKey, sortDir, onSort, onRowContext, onCloseCol, onReorderCols }) {
  const dragRef = useRef_p({ from: -1 });
  function onDragStart(i) { dragRef.current.from = i; }
  function onDragOver(e) { e.preventDefault(); }
  function onDrop(i) {
    const from = dragRef.current.from;
    dragRef.current.from = -1;
    if (from < 0 || from === i || !onReorderCols) return;
    onReorderCols(from, i);
  }
  return (
    <div className="results">
      <div className="tbl-head">
        {cols.map((c, i) => (
          <div key={c.key}
               className={"col " + c.cls + (sortKey === c.key ? " sorted" : "")}
               style={{ textAlign: c.align || "left" }}
               draggable={true}
               onDragStart={() => onDragStart(i)}
               onDragOver={onDragOver}
               onDrop={() => onDrop(i)}
               onClick={() => onSort(c.key)}>
            <span>{c.label}</span>
            <span className="sort">{sortDir === "desc" ? "▼" : "▲"}</span>
            {onCloseCol && cols.length > 1 && (
              <span className="col-close"
                    title="컬럼 숨김"
                    onClick={(e) => { e.stopPropagation(); onCloseCol(c.key); }}>✕</span>
            )}
          </div>
        ))}
      </div>
      <div className="tbl-body">
        {rows.length === 0 && (
          <div className="empty-state">
            <div className="emoji">◯</div>
            <h4>검색 결과 없음</h4>
            <p>필터나 검색어를 조정해보세요</p>
          </div>
        )}
        {rows.map(f => (
          <ResultsRow key={f.id} f={f} cols={cols}
                      isSelected={f.id === selectedId}
                      isPlaying={f.id === playingId}
                      onSelect={onSelect} onActivate={onActivate}
                      onContextMenu={onRowContext} />
        ))}
      </div>
    </div>
  );
}

// ─── Context Menu ─────────────────────────────────────────────
function ContextMenu({ open, x, y, items, onClose }) {
  useEffect_p(() => {
    if (!open) return;
    function onDoc(e) { onClose(); }
    function onKey(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  if (!open) return null;
  return (
    <div className="context-menu" style={{ left: x, top: y }}
         onMouseDown={(e) => e.stopPropagation()}>
      {items.map((it, i) => (
        <div key={i} className="context-item"
             onClick={() => { it.onClick(); onClose(); }}>{it.label}</div>
      ))}
    </div>
  );
}

// ─── History Drawer ───────────────────────────────────────────
function HistoryDrawer({ open, history, files, onPick, onClear }) {
  const items = useMemo_p(() => {
    const map = new Map(files.map(f => [f.id, f]));
    return history.map(id => map.get(id)).filter(Boolean).reverse();
  }, [history, files]);
  return (
    <div className={"history" + (open ? " open" : "")}>
      <div className="history-head">
        <span>재생 히스토리</span>
        <span className="count num">{items.length}</span>
        <button className="clear" onClick={onClear}>지우기</button>
      </div>
      <div className="history-list">
        {items.length === 0 && (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text-3)", fontSize: 11 }}>
            아직 재생한 사운드가 없습니다
          </div>
        )}
        {items.map((f, i) => (
          <div key={f.id + "_" + i} className="history-item" onClick={() => onPick(f.id)}>
            <span className="num">{i + 1}</span>
            <span className="name">{f.file_name}</span>
            <span className="meta num">{fmtDur(f.duration_sec)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Status Bar ───────────────────────────────────────────────
function StatusBar({ status, indexing, progress, count }) {
  return (
    <div className="statusbar">
      {indexing && (
        <span className="index-badge">● 인덱싱 중</span>
      )}
      <span className="status-text">{status}</span>
      {indexing && (
        <div className={"progress" + (progress === 0 ? " indeterminate" : "")}>
          <div className="bar" style={{ width: progress + "%" }}></div>
          <div className="shimmer"></div>
        </div>
      )}
      <span className="count">인덱싱됨 {count.toLocaleString()}</span>
    </div>
  );
}

Object.assign(window, {
  SF_Topbar: Topbar,
  SF_Sidebar: Sidebar,
  SF_MultiSearch: MultiSearch,
  SF_Filters: Filters,
  SF_Results: ResultsTable,
  SF_History: HistoryDrawer,
  SF_Status: StatusBar,
  SF_ContextMenu: ContextMenu,
  SF_ALL_COLS: ALL_COLS,
});
