# -*- coding: utf-8 -*-
"""UCS 분류 약어 — 검색어를 UCS 파일명 약어로도 넓힌다 (사용자 결정 2026-10-07).

UCS 이름 규칙 파일은 분류 ID 로 시작한다: 'VEHCar_Pass By...', 'GLASBrk_Bottle...'.
분류 ID = 분류 약어(VEH, GLAS) + 하위분류 약어(Car, Brk). 이런 파일은 이름에 분류 이름
단어(vehicle, glass)가 없는 경우가 많다 — 실측: UCS 이름 파일 47만 개 중 24.5만 개.
  분류 이름   VEHICLES → veh       GLASS → glas      AMBIENCE → amb
  하위분류    BREAK    → brk       INTERIOR → int    ROCKET → rckt
파일명은 단어색인에 쪼개 넣으므로('VEHCar' → VEH Car) 약어를 단어로 찾을 수 있다.

UCS '동의어' 칸(thesaurus.py)과 달리 표준에 정해진 정확한 대응이라 1단계(새 사전과 같이)로
붙인다. 칩에는 보이지 않는다. 데이터는 tools/build_ucs_codes.py 로 UCS 엑셀에서 만든다.
로드 실패 시 빈 표로 동작한다 (검색 자체는 정상, 약어만 없음)."""
import json
import logging
import os
import re
import threading
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "ucs_codes.json")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _key(text: str) -> str:
    """'VEHICLES' / 'vehicle' / 'Sci-Fi' 를 같은 열쇠로 — 소문자, 기호 제거, 끝의 s 하나 제거."""
    k = _NON_ALNUM.sub("", (text or "").lower())
    return k[:-1] if len(k) > 3 and k.endswith("s") else k


class UcsCodes:
    def __init__(self, path: str = _DATA_PATH):
        self._codes: Dict[str, List[str]] = {}
        self._phrases: Set[str] = set()
        try:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)["rows"]
            for category, subcategory, catid, catshort in rows:
                self._add(category, catshort)
                suffix = catid[len(catshort):]
                if suffix:
                    self._add(subcategory, suffix)
            logger.info(f"UCS 분류 약어 로드 — {len(rows)} 분류 / {len(self._codes)} 열쇠")
        except Exception as e:
            logger.warning(f"UCS 분류 약어 로드 실패 (약어 없이 검색): {e}")

    def _add(self, name: str, code: str):
        key = _key(name)
        code = (code or "").lower()
        if not key or not code:
            return
        codes = self._codes.setdefault(key, [])
        if code not in codes:
            codes.append(code)
        words = (name or "").lower().split()
        if len(words) > 1 and all(w.isalnum() for w in words):
            self._phrases.add(" ".join(words))     # 'user interface' 같은 여러 단어 분류명

    def lookup(self, term: str) -> List[str]:
        """검색어가 분류·하위분류 이름이면 그 약어들. 약어가 검색어와 같으면 뺀다."""
        key = _key(term)
        return [c for c in self._codes.get(key, ()) if _key(c) != key]

    def has_phrase(self, text: str) -> bool:
        return " ".join((text or "").lower().split()) in self._phrases

    def count(self) -> int:
        return len(self._codes)


_inst = None
_inst_lock = threading.Lock()


def get() -> UcsCodes:
    global _inst
    if _inst is None:
        with _inst_lock:
            if _inst is None:
                _inst = UcsCodes()
    return _inst
