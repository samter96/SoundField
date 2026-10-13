"""영어 단어인지 + 얼마나 흔한지 (Datamuse sp=, 100만 단어당 빈도 f). 캐시 → wordcheck.json"""
import json, os, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "wordcheck.json")
_cache = json.load(open(PATH, encoding="utf-8")) if os.path.exists(PATH) else {}
_lock = threading.Lock()

def _fetch(w):
    for i in range(4):
        try:
            u = "https://api.datamuse.com/words?sp=" + urllib.parse.quote(w) + "&md=f&max=1"
            d = json.load(urllib.request.urlopen(u, timeout=20))
            if d and d[0]["word"].lower() == w.lower():
                f = next((float(t[2:]) for t in d[0].get("tags", []) if t.startswith("f:")), 0.0)
                return [True, f]
            return [False, 0.0]
        except Exception:
            time.sleep(2 * (i + 1))
    return None

def check_many(words):
    todo = [w for w in set(words) if w not in _cache]
    if todo:
        with ThreadPoolExecutor(8) as ex:
            for w, r in zip(todo, ex.map(_fetch, todo)):
                if r is not None:
                    with _lock:
                        _cache[w] = r
        json.dump(_cache, open(PATH, "w", encoding="utf-8"))
    return {w: _cache.get(w) for w in words}

def is_word(w):
    r = _cache.get(w) or check_many([w])[w]
    return bool(r and r[0])

def freq(w):
    r = _cache.get(w) or check_many([w])[w]
    return r[1] if r else 0.0
