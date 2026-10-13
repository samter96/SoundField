"""Datamuse rel_syn (WordNet 동의어) 조회 → datamuse_syn.json (이어받기 가능)."""
import json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from simlib import CAT, lab
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "datamuse_syn.json")
done = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
words = sorted(w for w in CAT if w.isalpha() and len(w) >= 3 and lab(w) >= 8 and w not in done)
print("조회할 단어", len(words), "/ 이미", len(done), flush=True)
def get(w):
    for i in range(4):
        try:
            u = "https://api.datamuse.com/words?rel_syn=" + urllib.parse.quote(w) + "&max=40"
            return w, [x["word"] for x in json.load(urllib.request.urlopen(u, timeout=20))]
        except Exception:
            time.sleep(2 * (i + 1))
    return w, None
t0 = time.time()
with ThreadPoolExecutor(8) as ex:
    for i, (w, r) in enumerate(ex.map(get, words), 1):
        if r is not None:
            done[w] = r
        if i % 1000 == 0:
            json.dump(done, open(OUT, "w", encoding="utf-8"))
            print(f"  {i}/{len(words)} {time.time()-t0:.0f}s", flush=True)
json.dump(done, open(OUT, "w", encoding="utf-8"))
print("끝", len(done), flush=True)
