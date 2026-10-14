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
        self._head_count = None      # 묶음 이름이 되풀이되는 횟수 (related 가 처음 쓸 때 셈)
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


    def related(self, token: str, cap: int = 40) -> List[str]:
        """같이 찾을 말 (2026-10-12 사용자 결정 — 넓게·정확 모두에 쓴다).
          · token 이 묶음 **이름**이면(예: SWORD) → 그 묶음 전체 (Machete, Dagger, Katana …)
          · token 이 묶음 **안의 말**이면(예: Machete ∈ SWORD·KNIFE) → 그 묶음들의 **이름만** (SWORD, KNIFE)
        묶음 전체를 다 펼치지 않는 이유: sword 는 STAB 묶음 안에도 있어서, 그 묶음을 펼치면
        Behead·Syringe·Needle 까지 붙는다 (10-07 'UCS 전부' 를 되돌린 이유와 같다).
        영어 검색만 쓰므로 한글 용어는 붙이지 않는다."""
        t = (token or "").strip().lower()
        out = self._related_one(t, cap)
        if not out:
            # 붙는 말이 없으면 복수형만 떼서 다시 본다 (사용자 결정 2026-10-14 — 다른 꼬리는 안 뗀다).
            # 'guns' 는 사전에 있지만 묶음 7개 이상에 걸쳐 위 규칙으로 아무것도 안 붙는다 → gun 규칙대로.
            # 뗀 말(gun)도 붙인다 — 넓게 찾기는 'guns' 글자로만 찾아 'gun' 만 든 파일을 못 본다.
            for c in _singulars(t):
                if c in self._term_map:
                    return ([c] + self._related_one(c, cap))[:cap]
        return out

    def _related_one(self, t: str, cap: int) -> List[str]:
        gids = self._term_map.get(t)
        if not gids:
            return []
        if self._head_count is None:
            from collections import Counter
            self._head_count = Counter(g[0].lower() for g in self._groups if g)
        heads = [gi for gi in gids if self._groups[gi] and self._groups[gi][0].lower() == t]
        members = [gi for gi in gids if self._groups[gi] and self._groups[gi][0].lower() != t]
        out: List[str] = []
        seen: Set[str] = {t}

        def add(term: str):
            tl = term.lower()
            if tl in seen or not term.isascii():
                return
            seen.add(tl)
            out.append(term)

        # 보완 규칙 (사용자 결정 2026-10-12, 자주 쓰는 말 100개 실측: 그냥 펼치면 평균 25.6개·
        # door → Airplane·Helicopter, 보완하면 평균 4.8개)
        #  ① 이름인 묶음이 **하나뿐**일 때만 펼친다 — UCS 묶음은 '대분류-하위분류' 단위라 DOOR 같은
        #     하위분류 이름이 비행기·차·건물 등 여러 대분류에 되풀이된다. 그 묶음 안의 말은 동의어가
        #     아니라 '그 분류 소리를 설명하는 말'(비행기 문 → Airplane, Cockpit)이다.
        if len(heads) == 1:
            for term in self._groups[heads[0]][1:]:
                add(term)
        #  ② 묶음 이름은 그 말이 걸친 묶음이 6개 이하일 때만, 그리고 4개 이상 대분류에 되풀이되는
        #     이름(MISC·IMPACT·MOVEMENT·TONAL …)은 뜻이 없으므로 빼고 붙인다.
        if len(members) <= 6:
            for gi in members:
                head = self._groups[gi][0]
                if self._head_count[head.lower()] < 4:
                    add(head)
        return out[:cap]


def _singulars(t: str) -> List[str]:
    """복수형만 뗀 후보 (bodies→body, slices→slice, crashes→crash). ss 로 끝나면(glass) 떼지 않는다."""
    out: List[str] = []
    if len(t) > 4 and t.endswith("ies"):
        out.append(t[:-3] + "y")
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        out.append(t[:-1])
        if t.endswith("es"):
            out.append(t[:-2])
    return [c for c in out if len(c) >= 3]


class BaseheadThesaurus:
    """베이스헤드 형식 동의어 사전 — 한 줄 = 'ID<TAB>말<TAB>말…' 한 묶음, 첫 줄은 머리.
    2.1.7 부터 기본 사전은 data/game_sfx_thesaurus.tsv (게임 사운드 사전 1,822묶음 — 베이스헤드 원본 +
    직접 정리 + 표기 변형·약어). 사용자 결정 2026-10-13: UCS 사전은 철자 제안·되돌리기용으로 보관.
    규칙: 친 단어가 묶음의 말과 똑같으면 그 묶음의 나머지 말 전부."""

    def __init__(self, path: str):
        self._groups: List[List[str]] = []
        self._term_map: Dict[str, List[int]] = {}
        try:
            with open(path, encoding="utf-8-sig") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            for ln in lines[1:]:
                terms = [t.strip() for t in ln.split("\t")[1:] if t.strip()]
                if len(terms) < 2:
                    continue
                gi = len(self._groups)
                self._groups.append(terms)
                for t in terms:
                    self._term_map.setdefault(t.lower(), []).append(gi)
            logger.info(f"베이스헤드 동의어 사전 로드 — {len(self._groups)} 묶음 ({path})")
        except Exception as e:
            logger.warning(f"베이스헤드 동의어 사전 로드 실패 (동의어 없음): {e}")

    @staticmethod
    def _singulars(t: str) -> List[str]:
        """복수형만 뗀 후보 (bodies→body, slices→slice, crashes→crash). ss 로 끝나면(glass) 떼지 않는다."""
        out: List[str] = []
        if len(t) > 4 and t.endswith("ies"):
            out.append(t[:-3] + "y")
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            out.append(t[:-1])
            if t.endswith("es"):
                out.append(t[:-2])
        return [c for c in out if len(c) >= 3]

    def related(self, token: str) -> List[str]:
        t = (token or "").strip().lower()
        out: List[str] = []
        seen: Set[str] = {t}
        gids = self._term_map.get(t)
        if not gids:
            # 사전에 그대로 없으면 복수형만 떼서 다시 본다 (사용자 결정 2026-10-14 — 다른 꼬리는 안 뗀다).
            # 뗀 말(gun)도 묶음 구성원이라 함께 붙는다 — 넓게 찾기는 'guns' 글자로만 찾아 'gun' 만 든 파일을 못 본다.
            for c in self._singulars(t):
                if c in self._term_map:
                    gids = self._term_map[c]
                    break
        for gi in gids or []:
            for term in self._groups[gi]:
                if term.lower() not in seen:
                    seen.add(term.lower())
                    out.append(term)
        return out


# 동의어 사전 선택 — 기본(설치본)은 게임 사운드 사전. SOUNDFIELD_THESAURUS=ucs 면 예전 UCS 규칙(되돌리기용).
# 다른 파일로 시험: SOUNDFIELD_BASEHEAD_THESAURUS=<경로> 를 정하고 run_dev.bat.
# ⚠ 사전 파일이 번들에 빠지면 동의어만 조용히 사라진다 — build_bridge.ps1 이 포함을 확인한다.
BASEHEAD_ENV = "SOUNDFIELD_THESAURUS"
BASEHEAD_PATH_ENV = "SOUNDFIELD_BASEHEAD_THESAURUS"
_BASEHEAD_DEFAULT = os.path.join(os.path.dirname(__file__), "data", "game_sfx_thesaurus.tsv")
_bh = None


def basehead_mode() -> bool:
    return os.environ.get(BASEHEAD_ENV, "").strip().lower() != "ucs"


def basehead() -> BaseheadThesaurus:
    global _bh
    if _bh is None:
        with _inst_lock:
            if _bh is None:
                _bh = BaseheadThesaurus(os.environ.get(BASEHEAD_PATH_ENV) or _BASEHEAD_DEFAULT)
    return _bh


_inst = None
_inst_lock = threading.Lock()


def get() -> Thesaurus:
    global _inst
    if _inst is None:
        with _inst_lock:
            if _inst is None:
                _inst = Thesaurus()
    return _inst
