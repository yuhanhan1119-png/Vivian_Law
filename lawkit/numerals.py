"""中文數字與條號的解析與格式化。

法規文字中的條號有多種寫法（第 12 條、第12-1條、第十二條之一），
本模組負責把它們正規化成 (條號, 之號) 的數值形式，以便排序與比對。
"""

from __future__ import annotations

import re
from typing import NamedTuple

_DIGITS = {
    "零": 0, "〇": 0, "○": 0,
    "一": 1, "壹": 1,
    "二": 2, "貳": 2, "兩": 2,
    "三": 3, "參": 3, "叁": 3,
    "四": 4, "肆": 4,
    "五": 5, "伍": 5,
    "六": 6, "陸": 6,
    "七": 7, "柒": 7,
    "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_SMALL_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_BIG_UNITS = {"萬": 10000, "億": 100000000}

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

CHINESE_NUMBER_CHARS = "".join(_DIGITS) + "".join(_SMALL_UNITS) + "".join(_BIG_UNITS)

#: 條號可能出現的數字字元（阿拉伯數字或中文數字）
NUMBER_PATTERN = r"[0-9０-９{}]+".format(CHINESE_NUMBER_CHARS)


class ArticleNumber(NamedTuple):
    """條號。``sub`` 為「之 N」的數字，0 表示沒有之號。"""

    number: int
    sub: int = 0

    @property
    def sort_key(self) -> int:
        """排序鍵：第 12 條 < 第 12 條之 1 < 第 13 條。"""
        return self.number * 1000 + self.sub

    def label(self, style: str = "arabic") -> str:
        return format_article_label(self.number, self.sub, style=style)

    def __str__(self) -> str:  # pragma: no cover - 便於除錯
        return self.label()


def normalize_digits(text: str) -> str:
    """把全形數字轉半形，並移除數字之間的空白。"""
    return text.translate(_FULLWIDTH_DIGITS)


def chinese_to_int(text: str) -> int:
    """將中文數字轉為整數，也接受阿拉伯數字。

    >>> chinese_to_int("一百二十三")
    123
    >>> chinese_to_int("十二")
    12
    >>> chinese_to_int("一百十四")
    114
    >>> chinese_to_int("42")
    42
    """
    raw = normalize_digits(text).strip().replace(" ", "").replace("\u3000", "")
    if not raw:
        raise ValueError("空字串無法轉為數字")
    if raw.isdigit():
        return int(raw)

    total = 0
    section = 0
    digit = None
    for ch in raw:
        if ch in _DIGITS:
            digit = _DIGITS[ch]
        elif ch in _SMALL_UNITS:
            unit = _SMALL_UNITS[ch]
            section += (digit if digit is not None else 1) * unit
            digit = None
        elif ch in _BIG_UNITS:
            section += digit or 0
            total += (section or 1) * _BIG_UNITS[ch]
            section = 0
            digit = None
        elif ch.isdigit():
            # 中文與阿拉伯數字混用，例如「第 1 章之 2」
            digit = int(ch)
        else:
            raise ValueError(f"無法解析的數字：{text!r}")
    return total + section + (digit or 0)


def int_to_chinese(value: int, legal_style: bool = True) -> str:
    """整數轉中文數字。

    ``legal_style`` 依法規慣例省略十位的「一」（一百十三而非一百一十三）。

    >>> int_to_chinese(13)
    '十三'
    >>> int_to_chinese(113)
    '一百十三'
    >>> int_to_chinese(1000)
    '一千'
    """
    if value < 0:
        raise ValueError("不支援負數")
    if value == 0:
        return "零"
    if value >= 100000000:
        raise ValueError("條號不應大於億")

    digit_chars = "零一二三四五六七八九"
    out: list[str] = []

    def render_below_10000(n: int) -> str:
        parts: list[str] = []
        for unit_value, unit_char in ((1000, "千"), (100, "百"), (10, "十")):
            count = n // unit_value
            n %= unit_value
            if count:
                # 法規慣例：十位的「一」省略，寫成三百十五而非三百一十五
                if not (legal_style and count == 1 and unit_value == 10):
                    parts.append(digit_chars[count])
                parts.append(unit_char)
            elif parts and n:
                parts.append("零")
        if n:
            parts.append(digit_chars[n])
        return "".join(parts).rstrip("零")

    wan, rest = divmod(value, 10000)
    if wan:
        out.append(render_below_10000(wan))
        out.append("萬")
        if rest and rest < 1000:
            out.append("零")
    if rest:
        out.append(render_below_10000(rest))
    return "".join(out)


_ARTICLE_LABEL_RE = re.compile(
    r"^第\s*(?P<number>{num})\s*條(?:\s*(?:之|[-－‐‑‒–—])\s*(?P<sub>{num}))?".format(
        num=NUMBER_PATTERN
    )
)
_ARTICLE_DASH_RE = re.compile(
    r"^第\s*(?P<number>[0-9０-９]+)\s*[-－‐‑‒–—]\s*(?P<sub>[0-9０-９]+)\s*條"
)


def parse_article_number(text: str) -> ArticleNumber:
    """解析條號字串。

    >>> parse_article_number("第 12 條")
    ArticleNumber(number=12, sub=0)
    >>> parse_article_number("第12-1條")
    ArticleNumber(number=12, sub=1)
    >>> parse_article_number("第一百十四條之二")
    ArticleNumber(number=114, sub=2)
    """
    stripped = text.strip()
    match = _ARTICLE_DASH_RE.match(stripped) or _ARTICLE_LABEL_RE.match(stripped)
    if not match:
        raise ValueError(f"不是合法的條號：{text!r}")
    sub_text = match.group("sub")
    return ArticleNumber(
        chinese_to_int(match.group("number")),
        chinese_to_int(sub_text) if sub_text else 0,
    )


def format_article_label(number: int, sub: int = 0, style: str = "arabic") -> str:
    """組出條號顯示字串。``style`` 可為 ``arabic`` 或 ``chinese``。"""
    if style == "chinese":
        body = int_to_chinese(number)
        return f"第{body}條之{int_to_chinese(sub)}" if sub else f"第{body}條"
    if style == "arabic":
        return f"第 {number}-{sub} 條" if sub else f"第 {number} 條"
    raise ValueError(f"未知的格式：{style!r}")
