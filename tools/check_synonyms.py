"""동의어 사전(py/app/data/sfx_synonyms.json) 점검 — 실제 인덱스를 **읽기 전용**으로 연다.

단어마다 '소리를 설명하는 칸'(파일명·제목·설명·키워드·코멘트·카테고리)에서
단어 단위로 몇 개가 걸리는지 세고, 아래를 경고한다.
  · 0건        — 사전에 있어도 아무것도 안 더한다
  · 회사 이름   — 걸린 파일의 절반 이상이 그 단어가 이름에 든 라이브러리 폴더 안에 있다
                  (예: boom → BOOM Library). 동의어로 넣으면 그 라이브러리가 통째로 섞인다.
  · 너무 넓음   — 5만 건 이상

    python tools/check_synonyms.py            # 전체
    python tools/check_synonyms.py shatter    # 단어 몇 개만
"""
import collections
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DICT = os.path.join(HERE, "..", "py", "app", "data", "sfx_synonyms.json")
DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "SoundField", "index.db")
COLS = "{file_name file_name_norm title description keywords comments category sub_category}"
VIS = "IFNULL(a.hidden,0)=0 AND IFNULL(a.removed,0)=0"
BROAD = 50_000


def library_of(path: str) -> str:
    parts = path.split("\\")
    return parts[2] if len(parts) > 3 else ""


def main() -> int:
    groups = json.load(open(DICT, encoding="utf-8"))["groups"]
    only = {w.lower() for w in sys.argv[1:]}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    warn = 0
    for group in groups:
        for word in group:
            if only and word.lower() not in only:
                continue
            match = f'{COLS}: "{word}"'
            rows = conn.execute(
                "SELECT a.file_path FROM search_fts_term f JOIN audio_files a ON a.id = f.file_id "
                f"WHERE search_fts_term MATCH ? AND {VIS}", (match,)).fetchall()
            n = len(rows)
            libs = collections.Counter(library_of(r[0]) for r in rows)
            top, top_n = (libs.most_common(1) or [("", 0)])[0]
            flags = []
            if n == 0:
                flags.append("0건")
            if n >= BROAD:
                flags.append("너무 넓음")
            if n and top_n / n >= 0.5 and word.replace(" ", "").lower() in top.replace(" ", "").lower():
                flags.append(f"회사 이름 ({top} {top_n * 100 // n}%)")
            warn += bool(flags)
            mark = "  ⚠ " + ", ".join(flags) if flags else ""
            print(f"{group[0]:14} {word:16} {n:7d}{mark}")
    print(f"\n경고 {warn}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
