# 동의어 칩 화면 (보관 — 빌드·설치본 제외)

2026-10-07 에 '같은 뜻' 새 사전 + UCS 전부를 붙이며 만든 검색 줄 아래 칩 화면이다.
결과가 덜 직관적이라 동의어를 예전 방식(UCS 단어마다 3개)으로 되돌리면서 화면에서 뺐다.
`src` 밖이라 tsc·vite 가 읽지 않는다. 파이썬 쪽 짝은 `py/app/synonym_lab.py` (역시 설치본 제외).

다시 쓰려면: 아래 조각을 원래 자리에 되돌리고, 검색 요청에 `synonym_overrides`, 응답에
`synonyms` 를 다시 싣는다 (sf_query.py · lib.rs SearchRequest/응답 · backend.ts).
전부 연결돼 있던 버전은 개인판 커밋 7e3c45b 다.

## SearchPanel — 도우미

```tsx
/* 단어 경계로 포함 여부 — 'glass breaking' 안의 'break' 는 아니고 'glass break' 는 맞다 */
const hasWord = (text: string, word: string) =>
  new RegExp(`(^|[^a-z0-9])${escapeRe(word)}([^a-z0-9]|$)`).test(text);
```

## SearchPanel — 상태

```tsx
  /* 동의어 칩에서 뺀/더한 말 — **이번 검색에만** (사용자 결정 2026-10-07).
     검색창에서 그 단어가 사라지면 같이 지운다 (아래 effect). 사전은 바꾸지 않는다. */
  const [synOverrides, setSynOverrides] = useState<Record<string, { off: string[]; add: string[] }>>({});
  const [synAdding, setSynAdding] = useState<string | null>(null);
  const [synAddText, setSynAddText] = useState("");
```

## SearchPanel — 칩 편집 정리 effect

```tsx
  /* 검색창에서 사라진 단어의 칩 편집은 버린다 — 같은 단어를 다시 치면 사전대로 시작한다 */
  useEffect(() => {
    const text = filters.map((f) => f.text.toLowerCase()).join("\n");
    setSynOverrides((prev) => {
      const keep = Object.entries(prev).filter(([term]) => hasWord(text, term));
      return keep.length === Object.keys(prev).length ? prev : Object.fromEntries(keep);
    });
  }, [filters]);
```

## SearchPanel — 칩 조작 함수

```tsx
  const setSynWord = (term: string, word: string, on: boolean) => setSynOverrides((prev) => {
    const cur = prev[term] ?? { off: [], add: [] };
    let off = cur.off.filter((w) => w !== word);
    let add = cur.add;
    if (!on) {
      if (add.includes(word)) add = add.filter((w) => w !== word);   // 내가 더한 말 → 그냥 지움
      else off = [...off, word];                                     // 사전의 말 → 이번 검색에서 뺌
    }
    const next = { ...prev, [term]: { off, add } };
    if (!off.length && !add.length) delete next[term];
    return next;
  });
  const addSynWord = (term: string, raw: string) => {
    const word = raw.trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ");
    setSynAdding(null);
    if (!word || word === term) return;
    setSynOverrides((prev) => {
      const cur = prev[term] ?? { off: [], add: [] };
      return { ...prev, [term]: { off: cur.off.filter((w) => w !== word),
                                  add: cur.add.includes(word) ? cur.add : [...cur.add, word] } };
    });
  };
```

## SearchPanel — 칩 JSX

```tsx
          {/* 동의어 칩 — 이번 검색에 붙은 '같은 뜻' 말. × 로 빼고, 흐린 칩을 누르면
              다시 넣고, + 로 더한다 (BaseHead 의 T-Blocks 와 같은 역할, 사용자 결정 2026-10-07). */}
          {synonymsEnabled && synonymInfo.length > 0 && (
            <div className="syn-row">
              <span className="syn-label">함께 찾는 말</span>
              {synonymInfo.map((info) => {
                const removed = info.available.filter((w) => !info.used.includes(w));
                return (
                  <span className="syn-group" key={info.term}>
                    <span className="syn-term">{info.term}</span>
                    {info.used.map((word) => (
                      <span className="syn-chip" key={word}>
                        {word}
                        <button className="syn-x" data-tip={t("이 말 빼기")} aria-label={t("이 말 빼기")}
                                onClick={() => setSynWord(info.term, word, false)}>
                          <IcoX size={9} />
                        </button>
                      </span>
                    ))}
                    {removed.map((word) => (
                      <button className="syn-chip off" key={word} data-tip={t("다시 넣기")}
                              onClick={() => setSynWord(info.term, word, true)}>
                        {word}
                      </button>
                    ))}
                    {synAdding === info.term ? (
                      <input className="syn-input" autoFocus value={synAddText}
                             placeholder={t("말 입력 후 Enter")}
                             onChange={(event) => setSynAddText(event.target.value)}
                             onKeyDown={(event) => {
                               if (event.key === "Enter") addSynWord(info.term, synAddText);
                               else if (event.key === "Escape") setSynAdding(null);
                             }}
                             onBlur={() => setSynAdding(null)} />
                    ) : (
                      <button className="syn-add" data-tip={t("이 검색에 동의어 더하기")}
                              aria-label={t("이 검색에 동의어 더하기")}
                              onClick={() => { setSynAdding(info.term); setSynAddText(""); }}>
                        <IcoPlus size={10} />
                      </button>
                    )}
                  </span>
                );
              })}
            </div>
          )}
```

## backend.ts — 요청 필드

```tsx
  /** 동의어 칩에서 이번 검색에만 뺀/더한 말 — {검색 단어: {off, add}} */
  synonym_overrides?: Record<string, { off: string[]; add: string[] }> | null;
```

## backend.ts — 타입

```tsx
/** 이번 검색에 붙은 동의어 — available: 사전에 있는 말, used: 실제로 함께 찾은 말 */
export type SynonymInfo = { term: string; available: string[]; used: string[] };
```

## app.css — 칩 스타일

```css
/* ── 동의어 칩 (2026-10-07) — 이번 검색에 붙은 '같은 뜻' 말. 동의어가 붙을 때만 한 줄 생긴다.
   × 로 빼고, 흐린 칩(뺀 말)을 누르면 다시 넣고, + 로 더한다. 이번 검색에만 적용. */
.syn-row {
  display: flex; flex-wrap: wrap; align-items: center;
  gap: 4px 10px;
  min-height: 24px;
  padding: 2px 0 0;
  font-size: 11px;
}
.syn-label { color: var(--text-secondary); font-size: 10.5px; margin-right: 2px; }
.syn-group { display: inline-flex; align-items: center; gap: 4px; }
.syn-term { color: var(--text); font-weight: 600; margin-right: 1px; }
.syn-term::after { content: ":"; color: var(--text-secondary); font-weight: 400; }
.syn-chip {
  display: inline-flex; align-items: center; gap: 3px;
  height: 20px;
  padding: 0 4px 0 8px;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  border-radius: var(--r-pill);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  color: var(--text);
  font-size: 11px;
  white-space: nowrap;
}
.syn-chip.off {
  padding: 0 8px;
  border-style: dashed;
  border-color: var(--line-strong);
  background: transparent;
  color: var(--text-secondary);
  text-decoration: line-through;
  cursor: pointer;
}
.syn-chip.off:hover { color: var(--text); border-color: var(--accent); text-decoration: none; }
.syn-x, .syn-add {
  display: grid; place-items: center;
  width: 16px; height: 16px;
  border: 0; border-radius: var(--r-pill);
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background var(--t-hover) var(--ease), color var(--t-hover) var(--ease);
}
.syn-x:hover, .syn-add:hover { color: var(--text); background: color-mix(in srgb, var(--accent) 22%, transparent); }
.syn-add { width: 20px; height: 20px; border: 1px dashed var(--line-strong); }
.syn-input {
  height: 20px; width: 110px;
  padding: 0 8px;
  border: 1px solid var(--accent);
  border-radius: var(--r-pill);
  background: var(--bg-elev);
  color: var(--text);
  font-size: 11px;
  outline: none;
}
```

## i18n_en.ts — 칩 문구

```tsx
  "함께 찾는 말": "Also searching",
  "이 말 빼기": "Remove this word",
  "다시 넣기": "Add back",
  "이 검색에 동의어 더하기": "Add a synonym to this search",
  "말 입력 후 Enter": "Type a word, then Enter",
```
