"""출처 B — 영어 동의어 데이터베이스(Datamuse rel_syn = WordNet) 후보를 라이브러리 분포로 거른다. → groups_wn.json
· 후보 쌍 (a, b): b 가 a 의 동의어로 나오고, 둘 다 라이브러리에 근거(분류 있는 파일 8개 이상)가 있을 것.
· 같은 종류 소리에 쓰일 것: 대분류 유사도 0.7 이상 그리고 하위분류 유사도 0.45 이상.
· 묶음은 '모든 쌍이 서로 통과'하는 것만 (a~b, b~c 라도 a~c 가 안 되면 따로) — 사슬로 번지는 혼입 방지.
· 한쪽이 다른 쪽을 품는 넓은 말(splash ⊂ water)은 이 출처에서 거의 안 나오지만, 출현 수가 20배 넘게 차이 나면
  넓은 말일 가능성이 커서 뺀다."""
import json, os
from simlib import CAT, DF, lab, sim, sim2
from wordcheck import check_many
HERE = os.path.dirname(os.path.abspath(__file__))
SYN = json.load(open(os.path.join(HERE, "datamuse_syn.json"), encoding="utf-8"))
FREQ = {}
def ok(a, b):
    s = sim2(a, b)
    if not s:
        return False, s
    if FREQ.get(a, 0) >= 40 or FREQ.get(b, 0) >= 40:      # 아주 흔한 일반 영어 단어(by·off·go·set·sound)는 이 출처에서 제외
        return False, s
    ratio = max(DF.get(a, 1), DF.get(b, 1)) / max(1, min(DF.get(a, 1), DF.get(b, 1)))
    return (s[0] >= 0.75 and s[1] >= 0.5 and ratio <= 20), s
cand = {a for a in SYN} | {b.lower() for v in SYN.values() for b in v}
cand = [w for w in cand if w in CAT and w.isalpha()]
wc = check_many(cand)
FREQ.update({w: (r[1] if r else 0) for w, r in wc.items()})
pairs = {}
rej = []
for a, cands in SYN.items():
    for b in cands:
        b = b.lower()
        if b == a or " " in b or not b.isalpha() or b not in CAT:
            continue
        key = tuple(sorted((a, b)))
        if key in pairs:
            continue
        good, s = ok(a, b)
        pairs[key] = good
        if not good and s:
            rej.append({"words": list(key), "why": f"분포 {s[0]:.2f},{(s[1] or 0):.2f}"})
adj = {}
for (a, b), good in pairs.items():
    if good:
        adj.setdefault(a, set()).add(b); adj.setdefault(b, set()).add(a)
# 모든 쌍이 서로 통과하는 묶음 (욕심 방식: 단어마다 이웃 중 서로 통과하는 것만 모은다)
groups, seen = [], set()
for a in sorted(adj, key=lambda w: -DF.get(w, 0)):
    g = [a]
    for b in sorted(adj[a], key=lambda w: -DF.get(w, 0)):
        if all(b in adj.get(x, ()) for x in g):
            g.append(b)
    key = frozenset(g)
    if len(g) >= 2 and key not in seen:
        seen.add(key)
        groups.append({"src": "영어 동의어(WordNet)+라이브러리 분포", "words": g, "why": ""})
# 다른 묶음에 통째로 들어 있는 묶음은 뺀다
gs = sorted(groups, key=lambda g: -len(g["words"]))
final = []
for g in gs:
    if not any(set(g["words"]) < set(f["words"]) for f in final):
        final.append(g)
json.dump({"groups": final, "rejected": rej}, open(os.path.join(HERE, "groups_wn.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("후보 쌍", len(pairs), "/ 통과 쌍", sum(pairs.values()), "/ 묶음", len(final))
print([g["words"] for g in final[:120]])
print("탈락 예:", [(r["words"], r["why"]) for r in rej[:40]])
