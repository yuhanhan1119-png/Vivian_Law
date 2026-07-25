"""匯出功能。

新增格式只要用 :func:`register` 裝飾一個函式即可，CLI 與 API 會自動出現該選項。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Callable

from .storage import LawStore

ExporterFunc = Callable[[LawStore, int | None], str]

EXPORTERS: dict[str, ExporterFunc] = {}
EXPORTER_DESCRIPTIONS: dict[str, str] = {}
EXPORTER_EXTENSIONS: dict[str, str] = {}


def register(name: str, description: str, extension: str) -> Callable[[ExporterFunc], ExporterFunc]:
    def decorator(func: ExporterFunc) -> ExporterFunc:
        EXPORTERS[name] = func
        EXPORTER_DESCRIPTIONS[name] = description
        EXPORTER_EXTENSIONS[name] = extension
        return func

    return decorator


def export(store: LawStore, fmt: str, law_id: int | None = None) -> str:
    if fmt not in EXPORTERS:
        raise ValueError(f"未知的匯出格式：{fmt}（可用：{'、'.join(sorted(EXPORTERS))}）")
    return EXPORTERS[fmt](store, law_id)


def _require_law(law_id: int | None) -> int:
    if law_id is None:
        raise ValueError("此格式需要指定法規")
    return law_id


@register("markdown", "Markdown 條文彙編（含標籤與筆記）", ".md")
def to_markdown(store: LawStore, law_id: int | None = None) -> str:
    law_ids = [law_id] if law_id is not None else [law["id"] for law in store.list_laws()]
    out: list[str] = []
    for current in law_ids:
        law = store.get_law(current)
        if law is None:
            continue
        out.append(f"# {law['name']}")
        meta = [
            f"- 版本：{law['version_label']}" if law["version_label"] else "",
            f"- 法規類別：{law['category']}" if law["category"] else "",
            f"- 主管機關：{law['authority']}" if law["authority"] else "",
            f"- 匯入時間：{law['imported_at']}",
        ]
        out.extend(line for line in meta if line)
        out.append("")

        headings = {division["seq"]: division for division in store.divisions(current)}
        printed: set[int] = set()
        for article in store.articles(current):
            division = headings.get(article["division_seq"]) if article["division_seq"] is not None else None
            if division and division["seq"] not in printed:
                chain: list[dict] = []
                cursor = division
                while cursor is not None:
                    chain.append(cursor)
                    cursor = headings.get(cursor["parent_seq"]) if cursor["parent_seq"] is not None else None
                for node in reversed(chain):
                    if node["seq"] in printed:
                        continue
                    out.append(f"{'#' * min(node['level'] + 1, 6)} {node['heading']}")
                    out.append("")
                    printed.add(node["seq"])

            out.append(f"##### {article['label_chinese']}（{article['label']}）")
            if article["is_deleted"]:
                out.append("")
                out.append("（刪除）")
            else:
                out.append("")
                for line in article["text"].split("\n"):
                    out.append(line)
            tags = store.article_tags(article["id"])
            if tags:
                out.append("")
                out.append("標籤：" + "、".join(f"`{tag['name']}`" for tag in tags))
            note = store.get_note(article["id"])
            if note:
                out.append("")
                out.append("> 筆記：" + note["body"].replace("\n", "\n> "))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


@register("json", "完整結構 JSON（含參照、標籤、筆記）", ".json")
def to_json(store: LawStore, law_id: int | None = None) -> str:
    return json.dumps(_collect(store, law_id), ensure_ascii=False, indent=2) + "\n"


@register("bundle", "App 離線資料包（供 PWA 讀取）", ".json")
def to_bundle(store: LawStore, law_id: int | None = None) -> str:
    payload = _collect(store, law_id)
    payload["kind"] = "lawkit-bundle"
    payload["bundle_version"] = 1
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


@register("csv", "條文清單 CSV（Excel 可開）", ".csv")
def to_csv(store: LawStore, law_id: int | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["法規", "版本", "編章節", "條號", "條號（中文）", "是否刪除", "標籤", "條文", "筆記"])
    law_ids = [law_id] if law_id is not None else [law["id"] for law in store.list_laws()]
    for current in law_ids:
        law = store.get_law(current)
        if law is None:
            continue
        for article in store.articles(current):
            note = store.get_note(article["id"])
            writer.writerow(
                [
                    law["name"],
                    law["version_label"],
                    article["division_path"],
                    article["label"],
                    article["label_chinese"],
                    "是" if article["is_deleted"] else "",
                    "、".join(tag["name"] for tag in store.article_tags(article["id"])),
                    article["text"].replace("\n", "\\n"),
                    note["body"] if note else "",
                ]
            )
    return buffer.getvalue()


@register("refs", "交互參照表 CSV（準用／適用／依據）", ".csv")
def to_reference_csv(store: LawStore, law_id: int | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["法規", "來源條文", "關係", "被引法規", "被引條號", "是否已連結", "原文片段"])
    law_ids = [law_id] if law_id is not None else [law["id"] for law in store.list_laws()]
    for current in law_ids:
        law = store.get_law(current)
        if law is None:
            continue
        for row in store.reference_report(current):
            target_label = f"第 {row['target_article']}"
            if row["target_sub"]:
                target_label += f"-{row['target_sub']}"
            target_label += " 條"
            writer.writerow(
                [
                    law["name"],
                    row["from_label"],
                    row["relation"],
                    row["target_law"] or "（本法）",
                    target_label,
                    "是" if row["to_article_id"] else "",
                    row["raw_text"],
                ]
            )
    return buffer.getvalue()


@register("text", "純文字條文（可再次匯入）", ".txt")
def to_text(store: LawStore, law_id: int | None = None) -> str:
    current = _require_law(law_id)
    law = store.get_law(current)
    if law is None:
        raise ValueError("找不到法規")
    lines = [f"法規名稱：{law['name']}"]
    if law["category"]:
        lines.append(f"法規類別：{law['category']}")
    if law["amended"]:
        lines.append(f"修正日期：{law['amended']}")
    elif law["promulgated"]:
        lines.append(f"公布日期：{law['promulgated']}")
    lines.append("")

    headings = {division["seq"]: division for division in store.divisions(current)}
    printed: set[int] = set()
    for article in store.articles(current):
        division = headings.get(article["division_seq"]) if article["division_seq"] is not None else None
        if division:
            chain: list[dict] = []
            cursor: dict | None = division
            while cursor is not None:
                chain.append(cursor)
                cursor = headings.get(cursor["parent_seq"]) if cursor["parent_seq"] is not None else None
            for node in reversed(chain):
                if node["seq"] not in printed:
                    lines.append(node["heading"])
                    printed.add(node["seq"])
        lines.append(article["label_chinese"])
        lines.append("（刪除）" if article["is_deleted"] else article["text"])
    return "\n".join(lines) + "\n"


def _collect(store: LawStore, law_id: int | None) -> dict:
    law_ids = [law_id] if law_id is not None else [law["id"] for law in store.list_laws()]
    laws: list[dict] = []
    for current in law_ids:
        law = store.get_law(current)
        if law is None:
            continue
        articles: list[dict] = []
        for article in store.articles(current):
            articles.append(
                {
                    "id": article["id"],
                    "number": article["number"],
                    "sub": article["sub"],
                    "label": article["label"],
                    "label_chinese": article["label_chinese"],
                    "division_seq": article["division_seq"],
                    "division_path": article["division_path"],
                    "is_deleted": bool(article["is_deleted"]),
                    "text": article["text"],
                    "paragraphs": json.loads(article["structure"] or "[]"),
                    "tags": [tag["name"] for tag in store.article_tags(article["id"])],
                    "note": (store.get_note(article["id"]) or {}).get("body", ""),
                    "refs": [
                        {
                            "relation": ref["relation"],
                            "target_law": ref["target_law"],
                            "target_article": ref["target_article"],
                            "target_sub": ref["target_sub"],
                            "target_paragraph": ref["target_paragraph"],
                            "raw_text": ref["raw_text"],
                            "to_article_id": ref["to_article_id"],
                        }
                        for ref in store.outgoing_references(article["id"])
                    ],
                }
            )
        laws.append(
            {
                "id": law["id"],
                "name": law["name"],
                "short_name": law["short_name"],
                "category": law["category"],
                "authority": law["authority"],
                "version_label": law["version_label"],
                "amended": law["amended"],
                "promulgated": law["promulgated"],
                "divisions": store.divisions(current),
                "articles": articles,
                "stats": store.law_stats(current),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "laws": laws,
        "tags": store.list_tags(),
        "bookmarks": [row["id"] for row in store.list_bookmarks()],
    }
