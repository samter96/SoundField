# -*- coding: utf-8 -*-
"""검색어 문법 해석 — 입력 문자열을 검색 나무(AST)로 바꾼다. SQL 은 database.py 가 만든다.

문법 (Soundminer · BaseHead 와 같은 관례, 2026-10-07 사용자 확정)
  띄어쓰기        = AND            glass break
  AND / OR / NOT  = 대문자만       sword OR knife,  footstep NOT rain
  쉼표            = 띄어쓰기와 같음 (2026-10-08 사용자 결정 — OR 는 'OR' 로만)
  -단어 / -"구문" / -(묶음)  = 빼기   door -slam
  "구문"          = 그대로 (동의어 없음, 그 순서로 붙은 단어)
  ( )             = 묶기           (sword OR knife) fight
우선순위는 FTS5 와 같다: NOT > AND > OR (필터 줄을 이어 붙일 때도 같은 규칙).

'경로' 필드는 이 문법을 쓰지 않는다 — 경로에는 괄호·하이픈이 흔하다
(예: 'Boom (2019)'). 띄어쓰기로만 나눠 모두 포함(AND)으로 찾는다.

단어 중간의 하이픈('sci-fi')은 빼기가 아니다. 맨 앞의 '-' 만 빼기다.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Union

# 토큰: -( 묶음 빼기 / 괄호 / (-)"구문" / 나머지 단어. 닫는 따옴표가 없으면 끝까지 구문으로 본다.
# ⚠ '-(' 는 붙어 있을 때만 한 토큰이다. 띄어 쓴 ' - '(파일명에 흔함: 'Toyed - Weapon')는
#   빼기가 아니다 — 예전엔 다음 단어를 빼 버려 파일명을 붙여 넣으면 0건이었다 (2026-10-08 신고).
# ⚠ 쉼표는 어떤 토큰에도 안 걸려 띄어쓰기처럼 사라진다. 예전엔 OR 였는데, 쉼표 든 파일명
#   ('1009_59-3 DOOR, WOOD OPEN, SQUEAK')을 붙여 넣으면 엉뚱한 5,000개가 나왔다 (2026-10-08).
_TOKEN_RE = re.compile(r'-\(|\(|\)|-?"[^"]*"?|[^\s(),"]+')
_OPS = {"AND", "OR", "NOT"}


@dataclass
class Term:
    """검색 단위 하나. words 가 여러 개면 두 단어 이상짜리 표현('pass by')."""
    words: List[str]
    quoted: bool = False
    field: str = "any"          # 찾는 필드 (필터 줄의 필드 선택)

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass
class Node:
    """kind='and': items 모두 + negs 하나도 없음 / kind='or': items 중 하나."""
    kind: str
    items: List["Expr"] = field(default_factory=list)
    negs: List["Expr"] = field(default_factory=list)


Expr = Union[Term, Node]


def _tokens(value: str) -> List[str]:
    return _TOKEN_RE.findall(value or "")


class _Parser:
    def __init__(self, tokens: List[str], phrase_lookup, field: str):
        self.toks = tokens
        self.i = 0
        self.phrase_lookup = phrase_lookup   # (words) -> bool, 사전의 여러 단어 표현인지
        self.field = field

    def peek(self) -> Optional[str]:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> Optional[str]:
        tok = self.peek()
        self.i += 1
        return tok

    def parse_or(self) -> Optional[Expr]:
        parts = []
        part = self.parse_and()
        if part is not None:
            parts.append(part)
        while self.peek() == "OR":
            self.take()
            part = self.parse_and()
            if part is not None:
                parts.append(part)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else Node("or", parts)

    def parse_and(self) -> Optional[Expr]:
        items: List[Expr] = []
        negs: List[Expr] = []
        while True:
            tok = self.peek()
            if tok is None or tok in (")", "OR"):
                break
            if tok == "AND":
                self.take()
                continue
            negate = False
            if tok == "NOT":
                self.take()
                negate = True
            elif tok == "-(":                     # '-(' 묶음 빼기 — 괄호는 parse_unit 이 읽는다
                self.toks[self.i] = "("
                negate = True
            unit = self.parse_unit()
            if unit is None:
                continue
            unit_expr, unit_neg = unit
            if negate or unit_neg:
                negs.append(unit_expr)
            else:
                items.append(unit_expr)
        items = self._merge_phrases(items)
        if not items and not negs:
            return None
        if len(items) == 1 and not negs:
            return items[0]
        return Node("and", items, negs)

    def parse_unit(self):
        """(식, 빼기여부) — 괄호 묶음 / 구문 / 단어."""
        tok = self.take()
        if tok is None:
            return None
        if tok == "(":
            inner = self.parse_or()
            if self.peek() == ")":
                self.take()
            return (inner, False) if inner is not None else None
        if tok == ")":
            return None
        neg = False
        if tok.startswith("-") and len(tok) > 1:
            neg = True
            tok = tok[1:]
        has_alnum = lambda w: any(ch.isalnum() for ch in w)
        if tok.startswith('"'):
            inner = tok[1:-1] if len(tok) >= 2 and tok.endswith('"') else tok[1:]
            words = [w for w in inner.split() if has_alnum(w)]
            return (Term(words, True, self.field), neg) if words else None
        # 글자·숫자가 없는 토큰(' - ', '&', '/')은 검색어가 아니다 — 버린다
        if tok in _OPS or not has_alnum(tok):
            return None
        return Term([tok], False, self.field), neg

    def _merge_phrases(self, items: List[Expr]) -> List[Expr]:
        """나란히 놓인 단어가 사전의 여러 단어 표현이면 하나로 묶는다 ('pass' 'by' → 'pass by').
        긴 표현부터 본다. 따옴표 구문·괄호 묶음은 건드리지 않는다."""
        if self.phrase_lookup is None:
            return items
        out: List[Expr] = []
        i = 0
        while i < len(items):
            merged = False
            for size in (4, 3, 2):
                group = items[i:i + size]
                if len(group) < size or not all(isinstance(t, Term) and not t.quoted
                                                and len(t.words) == 1 for t in group):
                    continue
                words = [t.words[0] for t in group]
                if self.phrase_lookup(words):
                    out.append(Term(words, False, self.field))
                    i += size
                    merged = True
                    break
            if not merged:
                out.append(items[i])
                i += 1
        return out


def parse(value: str, field: str = "any", phrase_lookup=None) -> Optional[Expr]:
    """입력 한 줄 → 식. '경로'(file_path) 필드는 문법 없이 띄어쓰기 AND 만."""
    if field == "file_path":
        words = (value or "").split()
        if not words:
            return None
        terms: List[Expr] = [Term([w], False, field) for w in words]
        return terms[0] if len(terms) == 1 else Node("and", terms)
    return _Parser(_tokens(value), phrase_lookup, field).parse_or()


def combine_rows(rows) -> Optional[Expr]:
    """필터 줄들 [(op, 식)] → 하나의 식. 첫 줄의 op 는 무시한다.
    우선순위 NOT > AND > OR (예전 FTS5 문자열 이어붙이기와 같은 결과)."""
    seq = [(op if i else None, e) for i, (op, e) in enumerate(rows) if e is not None]
    if not seq:
        return None
    # 1) NOT — 바로 앞 항목에서 뺀다. 맨 앞 줄이 사라져 NOT 이 첫째가 되면 버린다 (예전과 같음).
    folded = []
    for op, e in seq:
        if op == "NOT":
            if not folded:
                continue
            prev_op, prev = folded[-1]
            folded[-1] = (prev_op, Node("and", [prev], [e]))
        else:
            folded.append((op, e))
    if folded and folded[0][0] is not None:
        folded[0] = (None, folded[0][1])
    # 2) AND 로 이어진 구간을 묶고 3) 구간끼리 OR
    segments: List[List[Expr]] = [[]]
    for op, e in folded:
        if op == "OR":
            segments.append([])
        segments[-1].append(e)
    ors = [s[0] if len(s) == 1 else Node("and", s) for s in segments if s]
    return ors[0] if len(ors) == 1 else Node("or", ors)


def positive_terms(expr: Optional[Expr]) -> List[Term]:
    """빼기가 아닌 검색 단위 전부 (정렬 점수 · 동의어 칩 · 철자 제안용)."""
    out: List[Term] = []

    def walk(e):
        if isinstance(e, Term):
            out.append(e)
        elif isinstance(e, Node):
            for it in e.items:
                walk(it)
    if expr is not None:
        walk(expr)
    return out
