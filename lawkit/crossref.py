"""條文交互參照擷取。

從條文文字中找出「第 X 條」、「準用第 Y 條第一項」、「民法第 758 條」等引用，
並判斷引用關係（準用、適用、依據、除外、引用），是法規整理最核心的加值資訊。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .numerals import NUMBER_PATTERN, chinese_to_int, format_article_label

_LAW_SUFFIXES = (
    "法",
    "條例",
    "通則",
    "細則",
    "辦法",
    "規則",
    "標準",
    "準則",
    "要點",
    "綱要",
    "公約",
    "協定",
    "章程",
)

_SELF_REFERENCES = {"本法", "本條例", "本通則", "本細則", "本辦法", "本規則", "本標準", "本準則", "本要點"}

_LAW_NAME = r"(?:{self_refs}|[\u4e00-\u9fff]{{2,24}}?(?:{suffixes}))".format(
    self_refs="|".join(_SELF_REFERENCES | {"同法", "該法"}),
    suffixes="|".join(_LAW_SUFFIXES),
)

_REFERENCE_RE = re.compile(
    r"(?:(?P<law>{law})\s*)?"
    r"第\s*(?P<article>{num})\s*條(?:\s*之\s*(?P<sub>{num}))?"
    r"(?:\s*至\s*第\s*(?P<to_article>{num})\s*條(?:\s*之\s*(?P<to_sub>{num}))?)?"
    r"(?:\s*第\s*(?P<paragraph>{num})\s*項)?"
    r"(?:\s*第\s*(?P<item>{num})\s*款)?"
    r"(?:\s*第\s*(?P<subitem>{num})\s*目)?".format(law=_LAW_NAME, num=NUMBER_PATTERN)
)

# 法規名稱前常誤黏的動詞或連接詞，需要剝除
_LAW_PREFIX_TRIM = re.compile(
    r"^(?:準用|適用|類推|依照|依據|依|按|參照|參酌|違反|觸犯|符合|不符|及|或|與|暨|但|由|自|在|經|得|應|不|其|該當|"
    r"前|後|本條|規定|所定|所稱|視為|處|明定|另|如|於|以|對|向|受|至|有|無|為|者|並|及其|包括)+"
)

_RELATION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("準用", ("準用", "類推適用")),
    ("除外", ("除外", "不適用", "不在此限", "除")),
    ("適用", ("適用",)),
    ("依據", ("依", "依照", "依據", "按")),
    ("參照", ("參照", "參酌")),
    ("處罰", ("處", "罰", "科", "沒收")),
)

#: 關係的中文說明，供介面顯示
RELATION_LABELS = {
    "準用": "準用",
    "適用": "適用",
    "依據": "依據",
    "參照": "參照",
    "除外": "除外",
    "處罰": "處罰",
    "引用": "引用",
}


@dataclass
class Reference:
    """一筆交互參照。``target_law`` 為 ``None`` 時代表引用本法。"""

    article: int
    sub: int = 0
    target_law: str | None = None
    to_article: int | None = None
    to_sub: int = 0
    paragraph: int | None = None
    item: int | None = None
    subitem: int | None = None
    relation: str = "引用"
    text: str = ""
    context: str = ""
    position: int = 0

    @property
    def is_range(self) -> bool:
        return self.to_article is not None

    def targets(self) -> list[tuple[int, int]]:
        """展開範圍引用（第 5 條至第 8 條）為個別條號。"""
        if self.to_article is None:
            return [(self.article, self.sub)]
        start, end = self.article, self.to_article
        if end < start:
            start, end = end, start
        return [(number, 0) for number in range(start, end + 1)]

    def describe(self) -> str:
        parts = [self.target_law or "本法", format_article_label(self.article, self.sub)]
        if self.to_article is not None:
            parts.append(f"至 {format_article_label(self.to_article, self.to_sub)}")
        if self.paragraph:
            parts.append(f"第 {self.paragraph} 項")
        if self.item:
            parts.append(f"第 {self.item} 款")
        if self.subitem:
            parts.append(f"第 {self.subitem} 目")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "target_law": self.target_law,
            "article": self.article,
            "sub": self.sub,
            "to_article": self.to_article,
            "to_sub": self.to_sub,
            "paragraph": self.paragraph,
            "item": self.item,
            "subitem": self.subitem,
            "relation": self.relation,
            "text": self.text,
            "context": self.context,
            "describe": self.describe(),
        }


def _normalize_law_name(raw: str | None) -> tuple[str | None, bool, str]:
    """回傳 (法規名稱, 是否沿用前一個法規名稱, 被剝除的前綴)。本法回傳 ``None``。

    前綴要一併回傳，因為「準用民法第X條」的關係詞會被法規名稱吃掉，
    判斷引用關係時必須把它加回上下文。
    """
    if not raw:
        return None, False, ""
    stripped = raw.strip()
    name = _LAW_PREFIX_TRIM.sub("", stripped)
    prefix = stripped[: len(stripped) - len(name)]
    if len(name) < 2 or not name.endswith(_LAW_SUFFIXES):
        return None, False, stripped
    if name in _SELF_REFERENCES:
        return None, False, prefix
    if name in {"同法", "該法"}:
        return None, True, prefix
    return name, False, prefix


def _detect_relation(text: str, start: int, end: int, extra_before: str = "") -> str:
    before = text[max(0, start - 14) : start] + extra_before
    after = text[end : end + 10]
    for relation, keywords in _RELATION_RULES:
        for keyword in keywords:
            if keyword in before or keyword in after:
                return relation
    return "引用"


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return chinese_to_int(value)
    except ValueError:
        return None


def extract_references(text: str) -> list[Reference]:
    """擷取條文中的所有交互參照。"""
    references: list[Reference] = []
    previous_law: str | None = None
    previous_end = -99

    for match in _REFERENCE_RE.finditer(text):
        article = _to_int(match.group("article"))
        if article is None or article <= 0:
            continue

        law_name, inherit, prefix = _normalize_law_name(match.group("law"))
        if law_name is None and (
            inherit or (match.group("law") is None and match.start() - previous_end <= 2)
        ):
            law_name = previous_law

        reference = Reference(
            article=article,
            sub=_to_int(match.group("sub")) or 0,
            target_law=law_name,
            to_article=_to_int(match.group("to_article")),
            to_sub=_to_int(match.group("to_sub")) or 0,
            paragraph=_to_int(match.group("paragraph")),
            item=_to_int(match.group("item")),
            subitem=_to_int(match.group("subitem")),
            relation=_detect_relation(text, match.start(), match.end(), prefix),
            text=match.group(0).strip(),
            context=text[max(0, match.start() - 24) : match.end() + 24].replace("\n", " "),
            position=match.start(),
        )
        references.append(reference)
        previous_law = law_name
        previous_end = match.end()
    return references


def reference_summary(references: list[Reference]) -> dict[str, int]:
    """依關係統計引用數量。"""
    summary: dict[str, int] = {}
    for reference in references:
        summary[reference.relation] = summary.get(reference.relation, 0) + 1
    return summary
