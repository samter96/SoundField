"""출처 C — 라이브러리에 실제로 쓰인 표기 변형. → groups_ortho.json
 C1 붙여 쓰기 ↔ 띄어 쓰기 (gunshot ↔ 'gun shot') — 라이브러리에서 띄어 쓴 꼴이 실제로 나오고, 두 꼴이
    같은 종류 소리에 쓰일 때만 (outdoor ↔ 'out door' 같은 우연한 겹침을 거른다).
 C2 미국/영국 철자 (armor ↔ armour) — 규칙으로 만든 짝 중 둘 다 라이브러리에 있는 것.
 C3 늘여 쓴 의성어 (whooosh ↔ whoosh) — 같은 글자 3번 이상을 줄인 꼴이 있는 것."""
import json, math, os, re, sqlite3, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py"))
from app.database import Database
from simlib import CAT, SUB, DF, lab, sim, _cos
from wordcheck import check_many, is_word
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.environ["LOCALAPPDATA"], "SoundField", "index.db")
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py", "app", "data", "ucs_codes.json"), encoding="utf-8"))["rows"]
CATS = {r[0].upper() for r in rows}; CATID = {r[2].upper(): (r[0].upper(), r[2]) for r in rows}
SUBOF = {(r[0].upper(), r[1].upper()): r[2] for r in rows}
# rowid → (대분류, 하위분류ID)
lab_of = {}
for fid, fn, cat, sub in c.execute("SELECT id, file_name, category, sub_category FROM audio_files WHERE IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0"):
    k = sid = None
    if cat and cat.strip().upper() in CATS:
        k = cat.strip().upper(); sid = SUBOF.get((k, (sub or "").strip().upper()))
    else:
        m = re.match(r"([A-Za-z]+)[_ -]", fn or "")
        if m and m.group(1).upper() in CATID:
            k, sid = CATID[m.group(1).upper()]
    if k:
        lab_of[Database.fts_rowid(fid)] = (k, sid)
print("분류 있는 파일", len(lab_of), flush=True)

def phrase_profile(words):
    cc, ss, n = Counter(), Counter(), 0
    for (rid,) in c.execute("SELECT rowid FROM search_fts_term WHERE search_fts_term MATCH ?", ('"' + " ".join(words) + '"',)):
        n += 1
        v = lab_of.get(rid)
        if v:
            cc[v[0]] += 1
            if v[1]:
                ss[v[1]] += 1
    return n, cc, ss

def ok_ctx(cat_a, sub_a, cat_b, sub_b, la, lb):
    if la < 8 or lb < 8:
        return None
    s1 = _cos(cat_a, cat_b) or 0; s2 = _cos(sub_a, sub_b) or 0
    return s1 >= 0.7 and s2 >= 0.45, s1, s2

vocab = {w for w in DF if w.isalpha()}
CODES = set()
for r in rows:
    names = {r[0].lower(), r[1].lower()} | {x for n in (r[0], r[1]) for x in n.lower().replace("&", " ").split()}
    for code in (r[3].lower(), r[2].lower(), r[2][len(r[3]):].lower()):
        if code not in names:
            CODES.add(code)     # 진짜 약어만 (DOOR·BIRD 처럼 이름 그대로인 건 보통 단어)
CODES.discard("")
groups, rejected = [], []
# C1
cands = []
for w in vocab:
    if not (6 <= len(w) <= 16) or DF[w] < 5:
        continue
    best = None
    for i in range(3, len(w) - 2):
        a, b = w[:i], w[i:]
        if DF.get(a, 0) >= 100 and DF.get(b, 0) >= 100 and a not in CODES and b not in CODES and w not in CODES:
            sc = min(DF[a], DF[b])
            if not best or sc > best[0]:
                best = (sc, a, b)
    if best:
        cands.append((w, best[1], best[2]))
check_many([w for w, a, b in cands])
cands = [x for x in cands if is_word(x[0])]   # 실제 영어 단어인 붙여 쓴 말만 (파일명에서 우연히 붙은 덩어리 제외)
print("붙여 쓰기 후보(실제 단어)", len(cands), flush=True)
for i, (w, a, b) in enumerate(cands):
    n, cc, ss = phrase_profile([a, b])
    if n < 5:
        continue
    la, lb = lab(w), sum(cc.values())
    rec = {"src": "붙여쓰기", "words": [w, f"{a} {b}"], "why": f"띄어쓴 꼴 {n}건"}
    if la >= 8 and lb >= 8:
        s1 = _cos(CAT.get(w, {}), cc) or 0
        rec["why"] += f" / 분포 {s1:.2f}"
        (groups if s1 >= 0.6 else rejected).append(rec)
    elif n >= 10:
        rec["why"] += f" / 근거부족(분류 {la},{lb}) — 표기 변형으로 인정"; groups.append(rec)
    else:
        rec["why"] += f" / 근거부족(분류 {la},{lb})"; rejected.append(rec)
    if i % 500 == 0:
        print(f"  {i}/{len(cands)}", flush=True)
# C2 미국/영국 — 실제 쌍 목록(직접 정리)에서 라이브러리에 둘 다 나오는 것
UKUS = json.load(open(os.path.join(HERE, "uk_us_pairs.json"), encoding="utf-8"))
for us, uk in UKUS:
    if us in vocab or uk in vocab:
        groups.append({"src": "미국/영국 철자", "words": [us, uk], "why": f"출현 {DF.get(us,0)}/{DF.get(uk,0)}"})
# C3 늘여 쓴 의성어
for w in vocab:
    if re.search(r"(.)\1\1", w) and DF.get(w, 0) >= 2:
        if w.endswith("iii") or re.search(r"([^aeiouhmsy])\1\1", w):
            continue      # 로마 숫자(mkiii)·자음 늘임(brrring = 전화벨 ring) 제외 — 모음·h·m·s·y 늘임만
        for base in (re.sub(r"(.)\1{2,}", r"\1\1", w),):
            if base != w and base in vocab and DF.get(base, 0) >= 20 and len(base) >= 3:
                groups.append({"src": "늘여 쓴 의성어", "words": [base, w], "why": f"{w} {DF[w]}건 → {base}"})
                break
json.dump({"groups": groups, "rejected": rejected}, open(os.path.join(HERE, "groups_ortho.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
cnt = Counter(g["src"] for g in groups)
print("통과", dict(cnt), "/ 탈락", len(rejected))
for s in cnt:
    print(s, [g["words"] for g in groups if g["src"] == s][:40])
print("탈락 예", [(g["words"], g["why"]) for g in rejected[:40]])
