"""출처 A — UCS v8.2.1 공식 약어 ↔ 원래 이름. → groups_ucs.json
· 대분류 약어(CatShort) ↔ 대분류 이름: EXPL ↔ explosions/explosion
· 하위분류 약어(CatID - CatShort) ↔ 하위분류 이름: Brk ↔ break
혼입 검사: 약어가 라이브러리에서 그 분류의 소리에 실제로 쓰이는지 (그 분류 비율). 근거가 적은데 다른 데서
흔히 쓰이는 말(분류 없는 파일에 많이 나옴)이면 확인할 수 없으므로 뺀다."""
import json, os, re
from collections import defaultdict
from simlib import CAT, DF, lab
HERE = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py", "app", "data", "ucs_codes.json"), encoding="utf-8"))["rows"]
SKIP_CATS = {"ARCHIVED"}

def forms(name):
    """분류 이름 → 검색 말: 'EXPLOSIONS' → explosions, explosion / 'USER INTERFACE' → 'user interface' / 'DIRT & SAND' → 없음."""
    n = name.lower().strip()
    if "&" in n or "/" in n:
        return []
    out = [n]
    if " " not in n:
        if n.endswith("ies") and len(n) > 5:
            out.append(n[:-3] + "y")
        elif n.endswith("es") and n[:-2] + "" in DF and len(n) > 5 and n[-3] in "sxzh":
            out.append(n[:-2])
        elif n.endswith("s") and not n.endswith("ss") and len(n) > 4:
            out.append(n[:-1])
    return [f for f in out if f]

def frac_in(abbr, cats):
    d = CAT.get(abbr, {})
    s = sum(d.values())
    return (sum(d.get(c, 0) for c in cats) / s) if s else None

groups, rejected = [], []
# 대분류 약어
by_cat = defaultdict(set)
for c, s, cid, cs in rows:
    if c not in SKIP_CATS:
        by_cat[c].add(cs)
sub_cats = defaultdict(set)   # 하위 약어 → 그 약어가 쓰이는 대분류들
sub_names = defaultdict(set)
for c, s, cid, cs in rows:
    if c in SKIP_CATS:
        continue
    ss = cid[len(cs):]
    if ss:
        sub_cats[ss.lower()].add(c)
        sub_names[ss.lower()].add(s)

def check(abbr, cats):
    a = abbr.lower()
    f = frac_in(a, cats)
    if lab(a) >= 8:
        return (f is not None and f >= 0.5), f"분류비율 {f:.2f} (근거 {lab(a)})"
    if DF.get(a, 0) >= 200:
        return False, f"근거부족인데 흔한 말 (출현 {DF.get(a,0)})"
    return True, f"근거부족·드문 말 (출현 {DF.get(a,0)})"

for c, shorts in by_cat.items():
    fs = forms(c)
    if not fs:
        continue
    for cs in shorts:
        if cs.lower() in fs:
            continue
        ok, why = check(cs, {c})
        (groups if ok else rejected).append({"src": "UCS 대분류 약어", "words": fs + [cs.lower()], "why": why})
for ss, names in sub_names.items():
    cats = sub_cats[ss]
    stems = {n.split()[0][:4] for n in names}
    if len(stems) > 1 and len(names) > 1:
        rejected.append({"src": "UCS 하위 약어", "words": [ss] + sorted(n.lower() for n in names), "why": "여러 이름에 쓰이는 약어"}); continue
    fs = []
    for n in sorted(names, key=len):
        fs += [f for f in forms(n) if f not in fs]
    if not fs or ss in fs or len(ss) < 2:
        continue
    ok, why = check(ss, cats)
    (groups if ok else rejected).append({"src": "UCS 하위 약어", "words": fs + [ss], "why": why})
json.dump({"groups": groups, "rejected": rejected}, open(os.path.join(HERE, "groups_ucs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("통과", len(groups), "/ 탈락", len(rejected))
for g in rejected[:60]:
    print("  탈락:", g["words"], g["why"])
print("통과 예:", [g["words"] for g in groups[:40]])
