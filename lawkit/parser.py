"""法規純文字解析器。

支援全國法規資料庫（law.moj.gov.tw）「條文檢索／下載」的純文字格式，例如：

    法規名稱：中華民國民法
    修正日期：民國 110 年 01 月 13 日

    第 一 編 總則
    第 一 章 法例
    第 1 條
    民事，法律所未規定者，依習慣；無習慣者，依法理。
    第 2 條
    民事所適用之習慣，以不背於公共秩序或善良風俗者為限。

解析結果為 :class:`~lawkit.models.Law`，包含編章節階層、條、項、款、目。
"""

from __future__ import annotations

import re

from .models import Article, Division, Item, Law, Paragraph
from .numerals import NUMBER_PATTERN, chinese_to_int, parse_article_number

_SPACES = " \t\u3000\ufeff"

_METADATA_KEYS = {
    "法規名稱": "name",
    "法規名稱及編章節": "name",
    "簡稱": "short_name",
    "法規類別": "category",
    "主管機關": "authority",
    "公布日期": "promulgated",
    "制定日期": "promulgated",
    "發布日期": "promulgated",
    "修正日期": "amended",
    "最新修正日期": "amended",
    "name": "name",
    "short_name": "short_name",
    "category": "category",
    "authority": "authority",
    "promulgated": "promulgated",
    "amended": "amended",
}

_METADATA_RE = re.compile(r"^\s*([\w\u4e00-\u9fff]{2,10})\s*[:：]\s*(.*)$")

_DIVISION_RE = re.compile(
    r"^第\s*(?P<number>{num})\s*(?P<kind>[編章節款目])"
    r"(?:\s*之\s*(?P<sub>{num}))?\s*(?P<title>.*)$".format(num=NUMBER_PATTERN)
)

_ARTICLE_HEAD_RE = re.compile(
    r"^第\s*(?P<number>{num})\s*(?:條之\s*(?P<sub>{num})|[-－‐‑‒–—]\s*(?P<sub2>[0-9]+)\s*條|條)"
    r"(?P<rest>.*)$".format(num=NUMBER_PATTERN)
)

_SUBPARAGRAPH_RE = re.compile(r"^(?P<label>[一二三四五六七八九十百]+\s*、)\s*(?P<text>.*)$")
_ITEM_RE = re.compile(r"^(?P<label>[（(]\s*[一二三四五六七八九十]+\s*[）)])\s*(?P<text>.*)$")
_NUMERIC_ITEM_RE = re.compile(r"^(?P<label>[0-9]{1,2}\s*[、.．])\s*(?P<text>.*)$")

_DELETED_RE = re.compile(r"^[（(]?\s*(?:刪除|删除)\s*[）)]?\s*$")
_PUNCT_END = "。；：！？」』）)."


def _clean(line: str) -> str:
    return line.strip(_SPACES).rstrip("\r")


def _is_division_heading(line: str) -> re.Match | None:
    """判斷是否為編章節標題行。

    需排除條文內文中的「第一款所定之……」，因此要求標題不含句讀且不過長。
    """
    if len(line) > 40:
        return None
    match = _DIVISION_RE.match(line)
    if not match:
        return None
    title = match.group("title").strip(_SPACES)
    if any(ch in title for ch in "。，、；：？！"):
        return None
    if title.startswith(("所", "之", "規定", "至", "或", "及", "但", "情形", "第")):
        return None
    return match


def _is_article_heading(line: str) -> re.Match | None:
    """判斷是否為條號行；條號後必須是行尾、空白或「（刪除）」。"""
    match = _ARTICLE_HEAD_RE.match(line)
    if not match:
        return None
    rest = match.group("rest")
    if rest and rest[0] not in _SPACES and not _DELETED_RE.match(rest.strip(_SPACES)):
        return None
    return match


def parse_article_body(lines: list[str]) -> list[Paragraph]:
    """把條文內文行轉成項／款／目結構。"""
    paragraphs: list[Paragraph] = []
    last_item: Item | None = None
    last_subparagraph: Item | None = None

    def current_paragraph() -> Paragraph:
        if not paragraphs:
            paragraphs.append(Paragraph(index=1, text=""))
        return paragraphs[-1]

    for raw in lines:
        line = _clean(raw)
        if not line:
            continue

        sub_match = _SUBPARAGRAPH_RE.match(line)
        item_match = _ITEM_RE.match(line)
        numeric_match = _NUMERIC_ITEM_RE.match(line)

        if sub_match:
            item = Item(
                kind="款",
                label=sub_match.group("label").replace(" ", ""),
                text=sub_match.group("text").strip(_SPACES),
            )
            current_paragraph().items.append(item)
            last_subparagraph = item
            last_item = item
            continue

        if item_match or (numeric_match and last_subparagraph is not None):
            match = item_match or numeric_match
            item = Item(
                kind="目",
                label=match.group("label").replace(" ", ""),
                text=match.group("text").strip(_SPACES),
            )
            if last_subparagraph is not None:
                last_subparagraph.children.append(item)
            else:
                current_paragraph().items.append(item)
            last_item = item
            continue

        # 續行：上一個款／目尚未以句讀結尾，視為同一單元的換行
        if last_item is not None and last_item.text and last_item.text[-1] not in _PUNCT_END:
            last_item.text = f"{last_item.text}{line}"
            continue

        if paragraphs and not paragraphs[-1].text and not paragraphs[-1].items:
            paragraphs[-1].text = line
        else:
            paragraphs.append(Paragraph(index=len(paragraphs) + 1, text=line))
        last_item = None
        last_subparagraph = None

    for index, paragraph in enumerate(paragraphs, start=1):
        paragraph.index = index
    return paragraphs


def parse_law(text: str, *, name: str | None = None, source: str = "") -> Law:
    """解析單一法規的純文字內容。"""
    law = Law(name=name or "", source=source)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    division_stack: list[Division] = []
    pending_article: Article | None = None
    pending_lines: list[str] = []
    seen_article = False

    def flush_article() -> None:
        nonlocal pending_article, pending_lines
        if pending_article is None:
            return
        body_lines = [line for line in pending_lines if _clean(line)]
        joined = "\n".join(_clean(line) for line in body_lines)
        if not body_lines or _DELETED_RE.match(joined.strip()):
            pending_article.is_deleted = True
            pending_article.text = "（刪除）"
            pending_article.paragraphs = []
        else:
            pending_article.paragraphs = parse_article_body(body_lines)
            pending_article.text = "\n".join(
                p.flat_text() for p in pending_article.paragraphs
            )
        law.articles.append(pending_article)
        pending_article = None
        pending_lines = []

    for raw_line in lines:
        line = _clean(raw_line)
        if not line:
            if pending_article is not None:
                pending_lines.append(raw_line)
            continue

        article_match = _is_article_heading(line)
        if article_match:
            flush_article()
            number = chinese_to_int(article_match.group("number"))
            sub_text = article_match.group("sub") or article_match.group("sub2")
            sub = chinese_to_int(sub_text) if sub_text else 0
            pending_article = Article(
                number=number,
                sub=sub,
                text="",
                division_seq=division_stack[-1].seq if division_stack else None,
                division_path=[d.heading for d in division_stack],
                seq=len(law.articles),
            )
            rest = article_match.group("rest").strip(_SPACES)
            if rest:
                pending_lines.append(rest)
            seen_article = True
            continue

        division_match = _is_division_heading(line)
        if division_match:
            flush_article()
            kind = division_match.group("kind")
            division = Division(
                kind=kind,
                number=chinese_to_int(division_match.group("number")),
                sub=chinese_to_int(division_match.group("sub"))
                if division_match.group("sub")
                else 0,
                title=division_match.group("title").strip(_SPACES),
                seq=len(law.divisions),
            )
            while division_stack and division_stack[-1].level >= division.level:
                division_stack.pop()
            division.parent_seq = division_stack[-1].seq if division_stack else None
            law.divisions.append(division)
            division_stack.append(division)
            continue

        if pending_article is not None:
            pending_lines.append(raw_line)
            continue

        if not seen_article:
            metadata_match = _METADATA_RE.match(line)
            if metadata_match:
                key = _METADATA_KEYS.get(metadata_match.group(1).strip())
                if key:
                    value = metadata_match.group(2).strip()
                    if not getattr(law, key):
                        setattr(law, key, value)
                    continue
            if not law.name:
                law.name = line

    flush_article()
    law.articles.sort(key=lambda article: (article.sort_key, article.seq))
    for index, article in enumerate(law.articles):
        article.seq = index
    if not law.name:
        law.name = source or "未命名法規"
    return law


def parse_file(path: str, *, name: str | None = None, encoding: str = "") -> Law:
    """讀取檔案並解析；未指定編碼時自動嘗試常見中文編碼。"""
    encodings = [encoding] if encoding else ["utf-8-sig", "utf-8", "big5hkscs", "cp950", "gb18030"]
    last_error: Exception | None = None
    for candidate in encodings:
        try:
            with open(path, "r", encoding=candidate) as handle:
                content = handle.read()
        except (UnicodeDecodeError, LookupError) as exc:
            last_error = exc
            continue
        return parse_law(content, name=name, source=path)
    raise ValueError(f"無法判斷檔案編碼：{path}") from last_error
