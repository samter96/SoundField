"""분포 유사도 — 두 단어가 같은 종류의 소리에 쓰이는지."""
import math, os, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
P = pickle.load(open(os.path.join(HERE, "profiles.pkl"), "rb"))
CAT, SUB, DF = P["cat"], P["sub"], P["df"]

def _cos(a, b):
    if not a or not b:
        return None
    na = math.sqrt(sum(v * v for v in a.values())); nb = math.sqrt(sum(v * v for v in b.values()))
    return sum(v * b.get(k, 0) for k, v in a.items()) / (na * nb)

def lab(w):
    return sum(CAT.get(w, {}).values())

def sim(a, b, min_lab=8):
    """(대분류 유사도, 하위분류 유사도) — 근거(분류 있는 파일)가 적으면 None."""
    a, b = a.lower(), b.lower()
    if lab(a) < min_lab or lab(b) < min_lab:
        return None
    return _cos(CAT.get(a), CAT.get(b)), _cos(SUB.get(a), SUB.get(b))

def top(w, n=3):
    d = CAT.get(w.lower(), {})
    s = sum(d.values()) or 1
    return [(k, round(v / s, 2)) for k, v in sorted(d.items(), key=lambda x: -x[1])[:n]]

# ── PPMI 유사도: '평균보다 특히 많이 쓰이는 분류' 가 겹치는지 (어디에나 쓰이는 말은 자동으로 낮다) ──
_TOT_C = {}
_TOT_S = {}
for _d in CAT.values():
    for _k, _v in _d.items():
        _TOT_C[_k] = _TOT_C.get(_k, 0) + _v
for _d in SUB.values():
    for _k, _v in _d.items():
        _TOT_S[_k] = _TOT_S.get(_k, 0) + _v
_NC = sum(_TOT_C.values()); _NS = sum(_TOT_S.values())
_ppmi_cache = {}

def _ppmi(w, kind):
    key = (w, kind)
    if key in _ppmi_cache:
        return _ppmi_cache[key]
    d = (CAT if kind == "c" else SUB).get(w, {})
    tot, N = (_TOT_C, _NC) if kind == "c" else (_TOT_S, _NS)
    s = sum(d.values())
    out = {}
    if s:
        for k, v in d.items():
            lift = (v / s) / (tot[k] / N)
            if lift > 1:
                out[k] = math.log(lift) * math.sqrt(v)       # 근거 많은 분류에 무게
    _ppmi_cache[key] = out
    return out

def spec(w):
    """특정성 — 가장 두드러진 분류의 배율(lift). 1 근처면 어디에나 쓰이는 말."""
    d = CAT.get(w, {}); s = sum(d.values())
    if not s:
        return 0
    return max((v / s) / (_TOT_C[k] / _NC) for k, v in d.items())

def sim2(a, b, min_lab=8):
    a, b = a.lower(), b.lower()
    if lab(a) < min_lab or lab(b) < min_lab:
        return None
    return _cos(_ppmi(a, "c"), _ppmi(b, "c")) or 0.0, _cos(_ppmi(a, "s"), _ppmi(b, "s")) or 0.0
