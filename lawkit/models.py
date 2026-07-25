"""法規結構的資料模型：法規 → 編章節 → 條 → 項 → 款 → 目。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .numerals import ArticleNumber, format_article_label

#: 編章節款目的層級深度，用於建立階層樹
DIVISION_LEVELS = {"編": 1, "章": 2, "節": 3, "款": 4, "目": 5}


@dataclass
class Item:
    """條文中的款或目。"""

    kind: str  # "款" 或 "目"
    label: str  # 例如「一、」或「（一）」
    text: str
    children: list["Item"] = field(default_factory=list)

    def walk(self) -> Iterator["Item"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "text": self.text,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class Paragraph:
    """條文中的項（第一項、第二項……）。"""

    index: int
    text: str
    items: list[Item] = field(default_factory=list)

    def flat_text(self) -> str:
        lines = [self.text] if self.text else []
        for item in self.items:
            for node in item.walk():
                lines.append(f"{node.label}{node.text}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass
class Division:
    """編、章、節、款、目等結構單元。"""

    kind: str
    number: int
    sub: int
    title: str
    seq: int
    parent_seq: int | None = None

    @property
    def level(self) -> int:
        return DIVISION_LEVELS.get(self.kind, 9)

    @property
    def label(self) -> str:
        from .numerals import int_to_chinese

        body = f"第{int_to_chinese(self.number)}{self.kind}"
        if self.sub:
            body += f"之{int_to_chinese(self.sub)}"
        return body

    @property
    def heading(self) -> str:
        return f"{self.label} {self.title}".strip()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "number": self.number,
            "sub": self.sub,
            "title": self.title,
            "seq": self.seq,
            "parent_seq": self.parent_seq,
            "level": self.level,
            "label": self.label,
            "heading": self.heading,
        }


@dataclass
class Article:
    """條文。"""

    number: int
    sub: int
    text: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    division_seq: int | None = None
    division_path: list[str] = field(default_factory=list)
    is_deleted: bool = False
    seq: int = 0

    @property
    def article_number(self) -> ArticleNumber:
        return ArticleNumber(self.number, self.sub)

    @property
    def sort_key(self) -> int:
        return self.article_number.sort_key

    def label(self, style: str = "arabic") -> str:
        return format_article_label(self.number, self.sub, style=style)

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "sub": self.sub,
            "label": self.label(),
            "label_chinese": self.label("chinese"),
            "is_deleted": self.is_deleted,
            "division_seq": self.division_seq,
            "division_path": list(self.division_path),
            "text": self.text,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
        }


@dataclass
class Law:
    """一部法規（單一版本）。"""

    name: str
    short_name: str = ""
    category: str = ""
    authority: str = ""
    promulgated: str = ""
    amended: str = ""
    source: str = ""
    divisions: list[Division] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)

    @property
    def version_label(self) -> str:
        """版本標籤，優先採用修正日期，其次公布日期。"""
        return self.amended or self.promulgated or "未註明版本"

    def article(self, number: int, sub: int = 0) -> Article | None:
        for item in self.articles:
            if item.number == number and item.sub == sub:
                return item
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "short_name": self.short_name,
            "category": self.category,
            "authority": self.authority,
            "promulgated": self.promulgated,
            "amended": self.amended,
            "version_label": self.version_label,
            "source": self.source,
            "divisions": [d.to_dict() for d in self.divisions],
            "articles": [a.to_dict() for a in self.articles],
        }
