"""lawkit：法規整理工具。

把法規純文字整理成結構化知識庫（編章節條項款目），提供全文檢索、
交互參照、標籤筆記、修法比對、匯出，以及可安裝到手機的 App 介面。
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "Workspace",
    "Law",
    "Article",
    "LawStore",
    "parse_law",
    "parse_file",
    "extract_references",
]


def __getattr__(name: str):  # 延遲載入，避免 CLI 啟動時載入全部模組
    if name == "Workspace":
        from .config import Workspace

        return Workspace
    if name in {"Law", "Article"}:
        from . import models

        return getattr(models, name)
    if name == "LawStore":
        from .storage import LawStore

        return LawStore
    if name in {"parse_law", "parse_file"}:
        from . import parser

        return getattr(parser, name)
    if name == "extract_references":
        from .crossref import extract_references

        return extract_references
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
