# -*- coding: utf-8 -*-
"""영문 사전에 빠진 문장을 찾는다.

    python tools/check_i18n.py

무엇을 보나
  · src/** 의 `t("...")` 호출을 모두 모아 src/i18n_en.ts 의 열쇠와 맞춰 본다.
  · 사전에 없는 문장 → 영어 모드에서 **한글로 나온다**. 목록으로 알려 준다.
  · 사전에만 있고 코드에 없는 문장 → 문구를 고친 뒤 사전을 안 고친 흔적이다.
  · `t(변수)` 처럼 문자열이 아닌 호출은 자동으로 못 따라가므로 따로 알려 준다
    (그 변수가 담은 원문이 사전에 있는지 사람이 확인할 것).

한글 원문을 열쇠로 쓰기 때문에 필요한 검사다 — 원문을 한 글자만 고쳐도
사전이 조용히 어긋나기 때문이다 (src/i18n.ts 주석 참고).
종료 코드: 빠진 문장이 있으면 1.
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
DICT = os.path.join(SRC, "i18n_en.ts")
HANGUL = re.compile(r"[가-힣]")


def unescape(text):
    """소스에 적힌 \\n \\" 같은 표기를 실제 문자로 돌린다."""
    return (text.replace("\\n", "\n").replace("\\t", "\t")
                .replace('\\"', '"').replace("\\'", "'")
                .replace("\\`", "`").replace("\\\\", "\\"))


def strip_comments(text):
    out, i, n, quote = [], 0, len(text), None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ── 사전 열쇠 읽기 ──────────────────────────────────────────────────────────
dict_src = strip_comments(io.open(DICT, encoding="utf-8").read())
keys = set()
for m in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:', dict_src):
    keys.add(unescape(m.group(1)))

# ── 코드에서 t() 호출 모으기 ────────────────────────────────────────────────
used, dynamic = set(), []
for base, _dirs, files in os.walk(SRC):
    for name in sorted(files):
        if os.path.splitext(name)[1] not in (".ts", ".tsx"):
            continue
        path = os.path.join(base, name)
        if os.path.abspath(path) == os.path.abspath(DICT):
            continue
        rel = os.path.relpath(path, ROOT)
        code = strip_comments(io.open(path, encoding="utf-8").read())
        # t("...") / t('...') / t(`...`)  — 여러 줄 이어붙이기(+)도 한 덩어리로 본다
        for m in re.finditer(r'(?<![A-Za-z0-9_$.])t\(\s*("(?:[^"\\]|\\.)*"'
                             r"|'(?:[^'\\]|\\.)*'"
                             r'|`(?:[^`\\]|\\.)*`)'
                             r'((?:\s*\+\s*(?:"(?:[^"\\]|\\.)*"'
                             r"|'(?:[^'\\]|\\.)*'"
                             r'|`(?:[^`\\]|\\.)*`))*)', code):
            parts = [m.group(1)] + re.findall(
                r'"(?:[^"\\]|\\.)*"' + "|'(?:[^'\\\\]|\\\\.)*'" + r'|`(?:[^`\\]|\\.)*`',
                m.group(2) or "")
            joined = "".join(unescape(p[1:-1]) for p in parts)
            if HANGUL.search(joined):
                used.add(joined)
        for m in re.finditer(r'(?<![A-Za-z0-9_$.])t\(\s*([A-Za-z_$][\w$.]*)\s*[,)]', code):
            dynamic.append((rel, m.group(1)))

missing = sorted(used - keys)
unused = sorted(keys - used)

print("사전 항목 %d개 / 코드에서 쓰는 문장 %d개" % (len(keys), len(used)))

if missing:
    print("\n[영문 없음] %d개 — 영어 모드에서 한글로 나온다" % len(missing))
    for text in missing:
        print("  · %s" % text.replace("\n", "\\n")[:110])

if unused:
    print("\n[사전에만 있음] %d개 — 문구를 고치고 사전을 안 고쳤을 수 있다" % len(unused))
    for text in unused:
        print("  · %s" % text.replace("\n", "\\n")[:110])

if dynamic:
    seen = sorted(set(dynamic))
    print("\n[변수로 넘긴 호출] %d곳 — 그 변수의 원문이 사전에 있는지 직접 확인" % len(seen))
    for rel, name in seen:
        print("  · %s : t(%s)" % (rel, name))

print("\n결과:", "빠진 문장 있음" if missing else "빠진 문장 없음")
raise SystemExit(1 if missing else 0)
