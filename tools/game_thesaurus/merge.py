"""모든 출처를 합쳐 최종 사전을 만든다 → game_sfx_thesaurus.tsv (베이스헤드와 같은 꼴) + merge_report.json
검사(모든 출처 공통):
 1) 분포: 묶음의 기준 말(근거가 가장 많은 말)과 같은 종류 소리에 쓰이는지. 근거(분류 있는 파일 8개 이상)가 있는
    말만 판정 — 대분류 0.5·하위분류 0.25 미만이면 뺀다 (직접 정리·베이스헤드 묶음은 사람이 고른 것이라 기준을 조금 낮춤.
    자동 출처는 이미 0.7/0.45 로 걸렀다).
 2) 회사·라이브러리 이름: 그 말로 걸리는 파일의 절반 이상이 그 말이 이름에 든 라이브러리 폴더 안이면 뺀다 (boom).
 3) 꼴만 다른 말뿐인 묶음(lion·lions)은 검색이 이미 찾으므로 셈에서 뺀다 (단어 꼴 처리 = porter).
"""
import collections, json, os, re, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py"))
from app.database import Database
from simlib import CAT, DF, lab, sim, sim2
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.environ["LOCALAPPDATA"], "SoundField", "index.db")
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

def load(name):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding="utf-8"))["groups"] if os.path.exists(p) else []

src = []
# 베이스헤드 원본
lines = [l for l in open(r"C:\Program Files\basehead\Thesaurus.tsv", encoding="utf-8-sig").read().splitlines() if l.strip()]
for l in lines[1:]:
    ws = [x.strip() for x in l.split("\t")[1:] if x.strip()]
    if len(ws) >= 2:
        src.append(("베이스헤드 원본", ws, True))
cur = json.load(open(os.path.join(HERE, "curated_game.json"), encoding="utf-8"))["groups"]
for sec, gs in cur.items():
    for g in gs:
        src.append((f"직접 정리·{sec}", g, True))
for sec, gs in json.load(open(os.path.join(HERE, "curated_game2.json"), encoding="utf-8"))["groups"].items():
    for g in gs:
        src.append((f"직접 정리·{sec}", g, True))
for f in ("groups_ucs.json", "groups_ortho.json", "groups_abbr.json", "groups_wn.json"):
    for g in load(f):
        src.append((g["src"], g["words"], False))

# 단어 꼴(porter) — 꼴만 다른 묶음 판정용
allw = sorted({w.lower() for _, g, _ in src for w in g if " " not in w})
stem = Database._porter_stems(c, allw)

# 회사·라이브러리 이름 겹침
def lib_of(path):
    p = path.split("\\")
    return p[2] if len(p) > 3 else ""
lib_cache = {}
def company(word):
    if word in lib_cache:
        return lib_cache[word]
    rows = c.execute("SELECT a.file_path FROM search_fts_term f JOIN audio_files a ON a.id = f.file_id WHERE search_fts_term MATCH ? "
                     "AND IFNULL(a.hidden,0)=0 AND IFNULL(a.removed,0)=0 LIMIT 20000",
                     ('{file_name file_name_norm title description keywords comments category sub_category}: "' + word.replace('"', '') + '"',)).fetchall()
    n = len(rows)
    libs = collections.Counter(lib_of(r[0]) for r in rows)
    top, tn = (libs.most_common(1) or [("", 0)])[0]
    hit = bool(n >= 20 and tn / n >= 0.5 and word.replace(" ", "").lower() in top.replace(" ", "").lower())
    lib_cache[word] = (hit, top, n)
    return lib_cache[word]

final, dropped = [], []
for name, ws, human in src:
    ws = list(dict.fromkeys(w.strip().lower() for w in ws if w and w.strip()))
    keep = []
    anchor = max((w for w in ws if lab(w) >= 8), key=lambda w: lab(w), default=None)
    for w in ws:
        hit, top, n = company(w) if " " not in w else (False, "", 0)
        if hit:
            dropped.append({"src": name, "group": ws, "word": w, "why": f"라이브러리 이름 '{top}' ({n}건 중 절반 이상)"}); continue
        if human and anchor and w != anchor and lab(w) >= 30 and lab(anchor) >= 30:   # 근거가 적으면 분포가 들쭉날쭉 — 사람이 고른 말은 근거 충분할 때만 판정
            s = sim2(anchor, w)
            if s and s[0] < (0.1 if name == "베이스헤드 원본" else 0.3):   # 사람이 고른 묶음은 분포가 확실히 다를 때만 (imp↔impact 0.00) — 베이스헤드 원본은 더 너그럽게
                dropped.append({"src": name, "group": ws, "word": w, "why": f"'{anchor}' 와 분포 다름 {s[0]:.2f},{(s[1] or 0):.2f}"}); continue
        keep.append(w)
    if len(keep) < 2:
        continue
    singles = [w for w in keep if " " not in w]
    only_forms = len(singles) == len(keep) and len({stem.get(w, w) for w in singles}) == 1
    final.append({"src": name, "words": keep, "only_forms": only_forms})

# 직접 검토 결과 적용 (review_rules.json)
RULES = json.load(open(os.path.join(HERE, "review_rules.json"), encoding="utf-8"))
reviewed = []
for g in final:
    ws = set(g["words"])
    if any(set(x) <= ws for x in RULES["drop_group"]):
        dropped.append({"src": g["src"], "group": g["words"], "word": "*", "why": "직접 검토 — 묶음 제외"}); continue
    for a, b in RULES["drop_word"]:
        if a in ws and b in ws:
            g["words"] = [w for w in g["words"] if w != b]
            dropped.append({"src": g["src"], "group": sorted(ws), "word": b, "why": "직접 검토 — 말 제외"})
    if len(g["words"]) >= 2:
        reviewed.append(g)
final = reviewed

# 같은 묶음 중복 제거 (말 집합이 같으면 하나만, 출처는 합침)
merged = {}
for g in final:
    k = frozenset(g["words"])
    if k in merged:
        merged[k]["src"] += " + " + g["src"]
    else:
        merged[k] = dict(g)
groups = list(merged.values())
useful = [g for g in groups if not g["only_forms"]]
with open(os.path.join(HERE, "game_sfx_thesaurus.tsv"), "w", encoding="utf-8") as f:
    f.write("# SoundField 게임 사운드 동의어 사전 — 베이스헤드 형식 (ID<TAB>말<TAB>말…). 생성: merge.py\n")
    for i, g in enumerate(useful, 1):
        f.write(f"{i}\t" + "\t".join(g["words"]) + "\n")
terms = [w for g in useful for w in g["words"]]
by = collections.Counter(g["src"].split(" + ")[0].split("·")[0] for g in useful)
rep = {"묶음": len(useful), "말": len(terms), "서로 다른 말": len(set(terms)), "꼴만 다른 묶음(뺌)": len(groups) - len(useful),
       "출처별 묶음": dict(by), "검사로 뺀 말": len(dropped)}
json.dump({"report": rep, "dropped": dropped, "groups": useful}, open(os.path.join(HERE, "merge_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(json.dumps(rep, ensure_ascii=False, indent=1))
print("뺀 말 예:", [(d["word"], d["why"]) for d in dropped[:60]])
