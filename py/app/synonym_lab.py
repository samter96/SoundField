# -*- coding: utf-8 -*-
"""동의어 실험 보관소 — 2026-10-07 에 써 보고 되돌린 코드 (**설치본 제외**).

무엇이었나
  · 1단계: '같은 뜻' 새 사전(app/synonyms.py, data/sfx_synonyms.json) + UCS 분류 약어
    (app/ucs_codes.py, data/ucs_codes.json — vehicle→veh). 단어색인에서 단어 단위로,
    소리 설명 칸에서만 찾았다. 검색 줄 아래 칩으로 보여 주고 이번 검색에만 빼거나 더했다.
  · 2단계: UCS 분류 목록의 단어 전부 — 1단계로 limit 를 못 채울 때 남은 자리를 채움.
왜 되돌렸나 (사용자 결정)
  · 좁은 검색도 늘 5,000개로 채워지고, 보이지 않는 말로 결과가 들어와 이유를 알 수 없었다.
  · 새 사전은 손으로 고른 118묶음뿐이라 양이 부족했다. 공개 사전(WordNet) 자동 생성은
    엄격하면 wind–gust·slam–bang 이 빠지고, 느슨하면 wind–fart 가 들어왔다.
다시 쓰려면
  · 이 클래스를 Database 에 섞고(class Database(SynonymLabMixin, ...)), 개인판 커밋 7e3c45b 의
    Database.query / _set_sql / _pred_sql / _fts_string 연결부를 참고한다 (아래 TIER2 도 그때 것).
  · 화면 칩은 parked/synonym_chips/ 에 보관돼 있다.
  · ⚠ build_bridge.ps1 의 --exclude-module 과 번들 검사도 같이 풀어야 한다.
"""
import re
from typing import Dict, List, Optional, Tuple

from app import thesaurus
from app import synonyms as sfx_synonyms
from app import ucs_codes  # noqa: F401  (1단계 약어 — query 에서 ucs_codes.get().lookup(term.text))


class SynonymLabMixin:
    """Database 에서 떼어낸 메서드 그대로. self._token_variants / self._phrase 는 Database 것."""

    _SYNONYM_FIELDS = ("file_name", "title", "comments", "description",
                       "keywords", "category", "sub_category")

    def _syn_cols(self, field: str) -> Optional[str]:
        """동의어를 찾을 단어색인 칸 (FTS5 칸 지정 문법). 이름 칸이면 None = 동의어 없음."""
        if field == "any":
            cols = ("file_name", "file_name_norm") + self._SYNONYM_FIELDS[1:]
        elif field == "file_name":
            cols = ("file_name", "file_name_norm")
        elif field in self._SYNONYM_FIELDS:
            cols = (field,)
        else:
            return None
        return "{" + " ".join(cols) + "}"

    def _term_synonyms(self, term, overrides: Dict) -> Tuple[List[str], List[str]]:
        """(사전에 있는 동의어, 이번 검색에 쓸 동의어). overrides 는 화면 칩에서 뺀/더한 것."""
        key = term.text.lower()
        book = sfx_synonyms.get()
        available = book.lookup(key)
        if not available and len(term.words) == 1:
            # 'breaking' → break 묶음, 'sci-fi' → scifi 묶음. 단어색인이 동의어 쪽 어형
            # (shattering 등)은 어간 처리로 알아서 잡는다.
            for variant in self._token_variants(term.words[0])[1:]:
                available = book.lookup(variant)
                if available:
                    break
        ov = overrides.get(key) or {}
        off = {" ".join(str(o).lower().split()) for o in (ov.get("off") or [])}
        used = [s for s in available if s not in off]
        for extra in ov.get("add") or []:
            extra = " ".join(str(extra).lower().split())
            if extra and extra != key and extra not in used:
                used.append(extra)
        return available, used

    # UCS 동의어 정렬 점수 — 원래 단어 > 새 사전(_SYNONYM_RANK_FACTOR) > UCS (사용자 결정 2026-10-07)
    _UCS_RANK_FACTOR = 0.1

    def _ucs_synonyms(self, term, exclude: set) -> List[str]:
        """UCS 사전에서 이 단어가 든 분류 목록의 영어 단어 **전부** (사용자 결정 2026-10-07:
        '다 붙여'). 분류별 관련어라 엉뚱한 말도 섞이지만(실측: door slam → 창고 총소리),
        정렬 점수를 가장 낮게 줘서 아래로 보낸다. 화면 칩에는 보이지 않는다.
        exclude — 검색어의 다른 단어·이미 붙은 말. 'door close' 에서 close 의 UCS 말에
        door 가 있으면 close 칸이 모든 door 파일에 맞아 검색이 그냥 'door' 가 된다."""
        book = thesaurus.get()
        words = [term.text]
        if len(term.words) == 1:
            words += self._token_variants(term.words[0])[1:]
        out: List[str] = []
        seen = set(exclude) | {term.text.lower()}
        for word in words:
            for t in book.expand(word, cap=100_000, max_groups=1_000_000):
                low = " ".join(t.lower().split())
                if low in seen or len(low) < 3 or not low.isascii():
                    continue
                seen.add(low)
                out.append(low)
        return out

    def _syn_match(self, term, used: List[str]) -> Optional[str]:
        cols = self._syn_cols(term.field)
        if not used or cols is None:
            return None
        return f"{cols}: (" + " OR ".join(self._phrase(s.split()) for s in used) + ")"

    @staticmethod
    def _word_re(term: str):
        """정렬 점수용 단어 경계 — 'shut' 이 'Shutter' 에 점수를 주지 않게."""
        return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?:s|es|ed|ing)?(?![a-z])")


# 2단계(UCS 전부) 를 Database.query 에서 돌리던 부분 — 참고용 원문
TIER2 = r"""
                # 2단계 — UCS 동의어. 1단계(원래 단어 + 새 사전)로 limit 를 못 채웠을 때만
                # 단어색인 MATCH 하나로 남은 자리를 채운다. 실측(158만 행): 이 방식 0.2~0.3초 /
                # 1단계에 섞어 집합 SQL 로 하면 1~4초 / 정확한 검색 bm25 에 섞으면 12~16초 +
                # UCS 말이 맨 위로 올라와 '원래 단어 > 새 사전 > UCS' 순서(사용자 결정)가 깨졌다.
                # 이미 담은 행은 run() 이 건너뛰므로 LIMIT 은 limit 그대로 준다.
                if (ucs_words and len(results) < int(limit)
                        and (cancel_check is None or not cancel_check())):
                    fts_all = self._fts_string(expr, syn_all)
                    if fts_all is not None:
                        run(f"{with_sql}SELECT a.* FROM audio_files a "
                            "JOIN search_fts_term ON search_fts_term.file_id = a.id "
                            f"WHERE search_fts_term MATCH ? AND {where}",
                            [fts_all] + cond_params, int(limit))
"""
