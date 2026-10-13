"""출처 C5 — 라이브러리에서 실제로 쓰이는 줄임말 (mvmt ↔ movement). → groups_abbr.json
조건: 줄임말 t 는 3~6글자, 모음 1개 이하, 원래 말 w 와 첫 글자 같고 w 의 글자 순서를 따른다(부분열),
영어 단어가 아니거나 아주 드문 말(100만 단어당 0.5 미만), 라이브러리 출현 10회 이상,
그리고 t 와 w 가 같은 종류 소리에 쓰인다(대분류 0.7·하위분류 0.45 이상). 여러 w 에 들어맞으면 가장 비슷한
것 하나 — 둘째와 차이가 작으면(0.05 미만) 모호해서 버린다."""
import json, os, re
from simlib import CAT, DF, lab, sim
from wordcheck import check_many
HERE = os.path.dirname(os.path.abspath(__file__))
V = re.compile(r"[aeiou]")
def subseq(t, w):
    it = iter(w)
    return all(ch in it for ch in t)
full = [w for w in CAT if w.isalpha() and len(w) >= 5 and DF.get(w, 0) >= 100 and lab(w) >= 30]
OK3 = {"fwd", "nav", "mtn", "ltr", "lav", "fwy", "bkg", "mvt", "atm"} - {"atm"}
abbr = [t for t in CAT if t.isalpha() and (4 <= len(t) <= 6 or t in OK3) and len(V.findall(t)) <= 1 and DF.get(t, 0) >= 10 and lab(t) >= 8]
print("원래 말 후보", len(full), "/ 줄임말 후보", len(abbr), flush=True)
by_first = {}
for w in full:
    by_first.setdefault(w[0], []).append(w)
pairs = {}
for t in abbr:
    best = []
    for w in by_first.get(t[0], []):
        if w == t or len(w) <= len(t) or not subseq(t, w) or w.startswith(t) and len(t) >= len(w) - 1:
            continue
        s = sim(t, w)
        if s and s[0] >= 0.7 and (s[1] or 0) >= 0.45:
            best.append((s[0] + s[1], w, s))
    if best:
        best.sort(reverse=True)
        if len(best) == 1 or best[0][0] - best[1][0] >= 0.05:
            pairs[t] = best[0]
print("분포 통과", len(pairs), flush=True)
wc = check_many(list(pairs) + [v[1] for v in pairs.values()])
groups, rejected = [], []
for t, (sc, w, s) in pairs.items():
    r = wc.get(t)
    rw = wc.get(w)
    common = (r is None) or bool(r[0] and r[1] >= 0.1)          # 확인 실패도 탈락 (보수적으로)
    if rw is None or not rw[0]:
        common = True                                              # 원래 말은 실제 영어 단어여야
    if r and r[0] and t in {"perc", "dyno", "gliss", "trad"}:
        common = False
    rec = {"src": "라이브러리 줄임말", "words": [w, t], "why": f"분포 {s[0]:.2f},{s[1]:.2f} / {t} 출현 {DF[t]}" + (f" / 영어 빈도 {r[1]:.2f}" if r and r[0] else "")}
    (rejected if common else groups).append(rec)
json.dump({"groups": groups, "rejected": rejected}, open(os.path.join(HERE, "groups_abbr.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("통과", len(groups), "/ 흔한 영어 단어라 탈락", len(rejected))
print([g["words"] for g in groups][:150])
print("탈락:", [g["words"] for g in rejected][:60])
