"""修法比對：比較同一部法規的兩個版本，逐條標示新增、刪除、修正。"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

STATUS_ADDED = "新增"
STATUS_REMOVED = "刪除"
STATUS_MODIFIED = "修正"
STATUS_UNCHANGED = "未變動"


@dataclass
class Segment:
    """文字差異片段。``kind`` 為 equal／insert／delete。"""

    kind: str
    text: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text}


@dataclass
class ArticleChange:
    label: str
    number: int
    sub: int
    status: str
    old_text: str = ""
    new_text: str = ""
    similarity: float = 1.0
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "number": self.number,
            "sub": self.sub,
            "status": self.status,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "similarity": round(self.similarity, 4),
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass
class LawDiff:
    law_name: str
    old_version: str
    new_version: str
    changes: list[ArticleChange] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        counts = {STATUS_ADDED: 0, STATUS_REMOVED: 0, STATUS_MODIFIED: 0, STATUS_UNCHANGED: 0}
        for change in self.changes:
            counts[change.status] = counts.get(change.status, 0) + 1
        return counts

    def changed(self) -> list[ArticleChange]:
        return [change for change in self.changes if change.status != STATUS_UNCHANGED]

    def to_dict(self) -> dict:
        return {
            "law_name": self.law_name,
            "old_version": self.old_version,
            "new_version": self.new_version,
            "summary": self.summary,
            "changes": [change.to_dict() for change in self.changes],
        }


def diff_texts(old: str, new: str) -> list[Segment]:
    """逐字比對兩段條文，合併相鄰的同類片段。"""
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    segments: list[Segment] = []

    def push(kind: str, text: str) -> None:
        if not text:
            return
        if segments and segments[-1].kind == kind:
            segments[-1].text += text
            return
        segments.append(Segment(kind, text))

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            push("equal", old[i1:i2])
        elif tag == "delete":
            push("delete", old[i1:i2])
        elif tag == "insert":
            push("insert", new[j1:j2])
        else:
            push("delete", old[i1:i2])
            push("insert", new[j1:j2])
    return segments


def similarity(old: str, new: str) -> float:
    if not old and not new:
        return 1.0
    return SequenceMatcher(None, old, new, autojunk=False).ratio()


def diff_article_lists(
    old_articles: list[dict],
    new_articles: list[dict],
    *,
    law_name: str = "",
    old_version: str = "",
    new_version: str = "",
    include_unchanged: bool = True,
) -> LawDiff:
    """比對兩組條文（以條號對應）。"""
    old_map = {(item["number"], item["sub"]): item for item in old_articles}
    new_map = {(item["number"], item["sub"]): item for item in new_articles}
    keys = sorted(set(old_map) | set(new_map), key=lambda key: key[0] * 1000 + key[1])

    result = LawDiff(law_name=law_name, old_version=old_version, new_version=new_version)
    for key in keys:
        old_item = old_map.get(key)
        new_item = new_map.get(key)
        label = (new_item or old_item)["label"]

        if old_item is None and new_item is not None:
            result.changes.append(
                ArticleChange(
                    label=label,
                    number=key[0],
                    sub=key[1],
                    status=STATUS_ADDED,
                    new_text=new_item["text"],
                    similarity=0.0,
                    segments=[Segment("insert", new_item["text"])],
                )
            )
            continue
        if new_item is None and old_item is not None:
            result.changes.append(
                ArticleChange(
                    label=label,
                    number=key[0],
                    sub=key[1],
                    status=STATUS_REMOVED,
                    old_text=old_item["text"],
                    similarity=0.0,
                    segments=[Segment("delete", old_item["text"])],
                )
            )
            continue

        assert old_item is not None and new_item is not None
        old_text = old_item["text"]
        new_text = new_item["text"]
        if old_text == new_text:
            if include_unchanged:
                result.changes.append(
                    ArticleChange(
                        label=label,
                        number=key[0],
                        sub=key[1],
                        status=STATUS_UNCHANGED,
                        old_text=old_text,
                        new_text=new_text,
                    )
                )
            continue
        result.changes.append(
            ArticleChange(
                label=label,
                number=key[0],
                sub=key[1],
                status=STATUS_MODIFIED,
                old_text=old_text,
                new_text=new_text,
                similarity=similarity(old_text, new_text),
                segments=diff_texts(old_text, new_text),
            )
        )
    return result


def diff_law_versions(store, old_law_id: int, new_law_id: int, *, include_unchanged: bool = True) -> LawDiff:
    """比對資料庫中同一部法規的兩個版本。"""
    old_law = store.get_law(old_law_id)
    new_law = store.get_law(new_law_id)
    if old_law is None or new_law is None:
        raise ValueError("找不到指定的法規版本")
    return diff_article_lists(
        store.articles(old_law_id),
        store.articles(new_law_id),
        law_name=new_law["name"],
        old_version=old_law["version_label"],
        new_version=new_law["version_label"],
        include_unchanged=include_unchanged,
    )


def render_diff_text(law_diff: LawDiff, *, only_changed: bool = True) -> str:
    """把比對結果輸出為終端機／純文字報表。"""
    lines = [
        f"# {law_diff.law_name} 修法比對",
        f"舊版：{law_diff.old_version}    新版：{law_diff.new_version}",
        "摘要："
        + "、".join(f"{status} {count}" for status, count in law_diff.summary.items() if count),
        "",
    ]
    changes = law_diff.changed() if only_changed else law_diff.changes
    for change in changes:
        lines.append(f"[{change.status}] {change.label}")
        if change.status == STATUS_MODIFIED:
            lines.append(f"  相似度：{change.similarity:.0%}")
            for segment in change.segments:
                if segment.kind == "equal":
                    continue
                mark = "＋" if segment.kind == "insert" else "－"
                lines.append(f"  {mark} {segment.text}")
        elif change.status == STATUS_ADDED:
            lines.append(f"  ＋ {change.new_text}")
        elif change.status == STATUS_REMOVED:
            lines.append(f"  － {change.old_text}")
        lines.append("")
    if not changes:
        lines.append("兩個版本沒有差異。")
    return "\n".join(lines)
