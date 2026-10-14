"""ucs_codes.json → py/app/data/ucs_catid.json (순위 가산점용 작은 대응표, 설치본에 들어간다).

    python tools/build_ucs_catid.py

ucs_codes.json 은 보관 중인 약어 실험 자료라 설치본에서 빠진다 (build_bridge.ps1 이 확인).
순위 점수가 쓰는 건 '분류 ID → 분류·하위분류 이름' 뿐이라 그 부분만 따로 뽑는다.
행마다 [분류 ID, 분류, 하위분류]. ARCHIVED(ADR·MIX·WIP 같은 작업 상태 표시)는 소리 내용이 아니라 뺀다.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "py" / "app" / "data"


def main() -> None:
    src = json.loads((DATA / "ucs_codes.json").read_text(encoding="utf-8"))
    rows = [[cid, cat, sub] for cat, sub, cid, _ in src["rows"] if cat != "ARCHIVED"]
    out = DATA / "ucs_catid.json"
    out.write_text(json.dumps({"source": src.get("source", ""), "rows": rows}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    print(f"OK: {len(rows)} 분류 ID -> {out}")


if __name__ == "__main__":
    main()
