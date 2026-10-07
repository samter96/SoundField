# -*- coding: utf-8 -*-
"""사운드 작업용 동의어 사전 — 검색어 확장 (Soundminer / BaseHead 방식).

app/data/sfx_synonyms.json 의 '같은 뜻' 묶음을 lazy 로드한다. 검색어가 묶음 안의
단어와 일치하면 같은 묶음의 나머지 단어를 돌려준다. 두 단어 이상('pass by')도 된다.

UCS 사전(thesaurus.py)과 다르다 — UCS 는 '분류마다 관련 단어' 목록이라 같은 뜻이
아닌 단어(close → Automobile)가 섞인다. 그래서 UCS 는 이 사전 다음 단계로만 붙인다
(Database.query 2단계 — 남은 자리를 채우고 정렬은 가장 아래).

로드 실패 시 빈 사전으로 동작한다 (검색 자체는 정상, 동의어만 없음)."""
import json
import logging
import os
import threading
from typing import Dict, List

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "sfx_synonyms.json")


def _key(term: str) -> str:
    """사전 열쇠 — 소문자 + 공백 하나로. 'Pass  By' → 'pass by'."""
    return " ".join((term or "").lower().split())


class Synonyms:
    def __init__(self, path: str = _DATA_PATH):
        self._map: Dict[str, List[str]] = {}
        self.max_words = 1          # 사전에서 가장 긴 표현의 단어 수 (구문 탐색 범위)
        try:
            with open(path, encoding="utf-8") as f:
                groups = json.load(f)["groups"]
            for group in groups:
                terms = [_key(t) for t in group if _key(t)]
                for t in terms:
                    others = self._map.setdefault(t, [])
                    for o in terms:
                        if o != t and o not in others:
                            others.append(o)
                    self.max_words = max(self.max_words, len(t.split()))
            logger.info(f"동의어 사전 로드 — {len(groups)} 묶음 / {len(self._map)} 단어")
        except Exception as e:
            logger.warning(f"동의어 사전 로드 실패 (동의어 없이 검색): {e}")

    def lookup(self, term: str) -> List[str]:
        """term 과 같은 묶음의 다른 단어들 (없으면 빈 목록). term 자신은 빠진다."""
        return list(self._map.get(_key(term), ()))

    def has(self, term: str) -> bool:
        return _key(term) in self._map

    def words(self) -> List[str]:
        """사전의 모든 단어 — 철자 제안 후보로 쓴다."""
        return list(self._map)


_inst = None
_inst_lock = threading.Lock()


def get() -> Synonyms:
    global _inst
    if _inst is None:
        with _inst_lock:
            if _inst is None:
                _inst = Synonyms()
    return _inst
