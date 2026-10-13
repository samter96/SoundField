"""단어마다 '어떤 UCS 분류의 소리에 쓰이나' 분포표 (읽기 전용). → profiles.pkl
분류 근거: category 칸이 UCS 대분류 이름이면 그것, 아니면 파일명 앞 CatID(예: EXPLReal_)의 대분류.
하위분류(CatID)까지도 센다. 단어는 파일명(붙여 쓴 대소문자 경계도 나눔)·제목·키워드·설명·코멘트·하위분류 칸에서."""
import json, os, pickle, re, sqlite3, time
from collections import Counter, defaultdict
DB = os.path.join(os.environ["LOCALAPPDATA"], "SoundField", "index.db")
HERE = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "py", "app", "data", "ucs_codes.json"), encoding="utf-8"))["rows"]
CATS = {r[0].upper() for r in rows}
CATID = {r[2].upper(): (r[0].upper(), r[2]) for r in rows}
SUBOF = {(r[0].upper(), r[1].upper()): r[2] for r in rows}
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
def toks(s):
    out = set()
    for part in re.split(r"[^A-Za-z0-9]+", s or ""):
        if not part:
            continue
        out.add(part.lower())
        for p in CAMEL.findall(part):
            out.add(p.lower())
    return {t for t in out if len(t) >= 2 and not t.isdigit()}
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df_all = Counter()
cat_of = defaultdict(Counter)
sub_of = defaultdict(Counter)
t0 = time.time(); n = lab = 0
for fn, title, kw, desc, com, cat, sub in c.execute(
        "SELECT file_name, title, keywords, description, comments, category, sub_category FROM audio_files "
        "WHERE IFNULL(hidden,0)=0 AND IFNULL(removed,0)=0"):
    n += 1
    ts = toks(fn) | toks(title) | toks(kw) | toks(desc) | toks(com) | toks(sub)
    for t in ts:
        df_all[t] += 1
    k = sid = None
    if cat and cat.strip().upper() in CATS:
        k = cat.strip().upper()
        sid = SUBOF.get((k, (sub or "").strip().upper()))
    else:
        m = re.match(r"([A-Za-z]+)[_ -]", fn or "")
        if m and m.group(1).upper() in CATID:
            k, sid = CATID[m.group(1).upper()]
    if k:
        lab += 1
        for t in ts:
            cat_of[t][k] += 1
            if sid:
                sub_of[t][sid] += 1
    if n % 200000 == 0:
        print(f"  {n:,} {time.time()-t0:.0f}s", flush=True)
keep = {t for t, v in df_all.items() if v >= 5}
prof = {"n": n, "lab": lab, "df": {t: df_all[t] for t in keep},
        "cat": {t: dict(cat_of[t]) for t in keep if t in cat_of},
        "sub": {t: dict(sub_of[t]) for t in keep if t in sub_of}}
pickle.dump(prof, open(os.path.join(HERE, "profiles.pkl"), "wb"))
print(f"파일 {n:,} / 분류 있는 {lab:,} / 단어(5회 이상) {len(keep):,} / 분포 있는 단어 {len(prof['cat']):,} / {time.time()-t0:.0f}s")
