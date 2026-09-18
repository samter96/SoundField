# -*- coding: utf-8 -*-
"""UCS 동의어 사전 — 검색어 확장 (Soundminer thesaurus 방식).

app/data/ucs_thesaurus.json (tools/build_thesaurus.py 로 생성, UCS 퍼블릭 도메인)
을 lazy 로드. 검색 토큰이 사전 용어와 정확히 일치하면 같은 그룹(UCS CatID 단위)
의 다른 용어들을 돌려줘 쿼리를 (원어 OR 동의어...) 로 펼친다.
한국어 용어 포함 — '총' 검색 → gun/firearm 계열 영어 파일명 매칭.
로드 실패 시 빈 사전으로 동작 (검색 자체는 정상)."""
import json
import logging
import os
import threading
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "ucs_thesaurus.json")

# 한 토큰당 동의어 확장 상한 — FTS OR 절 비대화 방지.
# 카테고리 단어(GUNS 등)는 수백 용어 그룹과 연결될 수 있어 cap 필수.
EXPAND_CAP = 60

# 토큰이 이 개수보다 많은 그룹에 걸치면 확장 스킵 — 'door' 같은 흔한 단어는
# 수십 그룹에 등장해 무관 용어(Jet, Fire...)까지 끌려옴. 흔한 단어는 trigram
# substring 만으로 이미 잘 잡히므로 특정적 단어만 확장하는 게 정확.
MAX_GROUPS = 6
# 3글자 미만 토큰('총', '비' 등 한글 짧은 단어)은 trigram 직접 매칭이 불가능해
# 동의어 확장이 유일한 검색 경로 → 상한을 느슨하게.
MAX_GROUPS_SHORT = 20


class Thesaurus:
    def __init__(self, path: str = _DATA_PATH):
        self._groups: List[List[str]] = []
        self._term_map: Dict[str, List[int]] = {}
        try:
            with open(path, encoding="utf-8") as f:
                self._groups = json.load(f)["groups"]
            for gi, terms in enumerate(self._groups):
                for t in terms:
                    self._term_map.setdefault(t.lower(), []).append(gi)
            logger.info(
                f"UCS 동의어 사전 로드 — {len(self._groups)} 그룹 / "
                f"{len(self._term_map)} 용어"
            )
        except Exception as e:
            logger.warning(f"동의어 사전 로드 실패 (확장 비활성): {e}")

    def expand(self, token: str, cap: int = EXPAND_CAP,
               max_groups: Optional[int] = None) -> List[str]:
        """token 과 정확히 일치하는 용어의 그룹 동료 용어들 (token 자신 제외).
        그룹별 라운드로빈으로 cap 을 채워 한 그룹이 상한을 독식하지 않게 함."""
        if max_groups is None:
            max_groups = MAX_GROUPS if len(token) >= 3 else MAX_GROUPS_SHORT
        gids = self._term_map.get(token.lower())
        if not gids or len(gids) > max_groups:
            return []
        out: List[str] = []
        seen: Set[str] = {token.lower()}
        iters = [iter(self._groups[gi]) for gi in gids]
        while iters and len(out) < cap:
            nxt = []
            for it in iters:
                term = next(it, None)
                if term is None:
                    continue
                nxt.append(it)
                tl = term.lower()
                if tl in seen:
                    continue
                seen.add(tl)
                out.append(term)
                if len(out) >= cap:
                    break
            iters = nxt
        return out


_inst = None
_inst_lock = threading.Lock()


def get() -> Thesaurus:
    global _inst
    if _inst is None:
        with _inst_lock:
            if _inst is None:
                _inst = Thesaurus()
    return _inst
