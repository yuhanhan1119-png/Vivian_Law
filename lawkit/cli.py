"""命令列介面。

新增指令的方式：寫一個 ``handler(args) -> int``，用 :func:`command` 裝飾註冊，
argparse 的子命令與說明會自動產生。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import __version__
from .config import ENV_VAR, Workspace
from .diff import diff_law_versions, render_diff_text
from .exporter import EXPORTER_DESCRIPTIONS, EXPORTER_EXTENSIONS, export
from .numerals import format_article_label, parse_article_number
from .parser import parse_file
from .storage import LawStore

Configure = Callable[[argparse.ArgumentParser], None]
Handler = Callable[[argparse.Namespace], int]


@dataclass
class Command:
    name: str
    help: str
    handler: Handler
    configure: Configure | None = None


COMMANDS: dict[str, Command] = {}


def command(name: str, help: str, configure: Configure | None = None) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        COMMANDS[name] = Command(name=name, help=help, handler=handler, configure=configure)
        return handler

    return decorator


# --------------------------------------------------------------------- 工具
def open_workspace(args: argparse.Namespace) -> Workspace:
    return Workspace.open(getattr(args, "data_dir", None), db_path=getattr(args, "db", None))


def open_store(args: argparse.Namespace) -> tuple[Workspace, LawStore]:
    workspace = open_workspace(args)
    return workspace, LawStore(str(workspace.db_path))


def parse_article_argument(text: str) -> tuple[int, int]:
    raw = str(text).strip()
    try:
        number = parse_article_number(raw if raw.startswith("第") else f"第{raw}條")
    except ValueError:
        raise SystemExit(f"無法解析條號：{text}")
    return number.number, number.sub


def resolve_law(store: LawStore, keyword: str, version: str = "") -> dict:
    law = store.find_law(keyword, version)
    if law is None:
        raise SystemExit(f"找不到法規：{keyword}")
    return law


def echo(*parts: object) -> None:
    print(*parts)


def _iter_input_files(paths: Iterable[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*.txt")))
        elif any(char in raw for char in "*?["):
            files.extend(sorted(Path().glob(raw)))
        else:
            files.append(path)
    return files


# --------------------------------------------------------------------- 指令
def _configure_init(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", help="資料根目錄（預設沿用設定或平台預設值）")


@command("init", "建立資料夾結構（知識庫位置）", _configure_init)
def cmd_init(args: argparse.Namespace) -> int:
    workspace = Workspace.open(args.path or getattr(args, "data_dir", None))
    workspace.update_settings(data_dir=str(workspace.root))
    LawStore(str(workspace.db_path)).close()
    echo(f"知識庫已就緒：{workspace.root}")
    for key, value in workspace.describe().items():
        echo(f"  {key}：{value}")
    echo()
    echo(f"提示：可用環境變數 {ENV_VAR} 覆寫位置，例如 set {ENV_VAR}=D:\\Vivian_Law")
    return 0


@command("where", "顯示目前使用的資料夾與資料庫位置")
def cmd_where(args: argparse.Namespace) -> int:
    workspace = Workspace.open(getattr(args, "data_dir", None), create=False, db_path=getattr(args, "db", None))
    for key, value in workspace.describe().items():
        echo(f"{key}：{value}")
    return 0


def _configure_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--set", dest="assignments", action="append", default=[], help="設定項，例如 --set keep_sources=false")


@command("config", "檢視或修改設定檔", _configure_config)
def cmd_config(args: argparse.Namespace) -> int:
    workspace = open_workspace(args)
    if args.assignments:
        changes: dict[str, object] = {}
        for assignment in args.assignments:
            if "=" not in assignment:
                raise SystemExit(f"格式應為 key=value：{assignment}")
            key, value = assignment.split("=", 1)
            lowered = value.strip().lower()
            parsed: object = value.strip()
            if lowered in {"true", "false"}:
                parsed = lowered == "true"
            changes[key.strip()] = parsed
        workspace.update_settings(**changes)
    echo(json.dumps({**workspace.read_settings()}, ensure_ascii=False, indent=2))
    return 0


def _configure_import(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("paths", nargs="+", help="法規純文字檔、資料夾或萬用字元")
    parser.add_argument("--name", help="指定法規名稱（預設由檔案內容判斷）")
    parser.add_argument("--version", default="", help="版本標籤（預設取修正／公布日期）")
    parser.add_argument("--encoding", default="", help="指定檔案編碼")
    parser.add_argument("--replace", action="store_true", help="同名同版本時覆蓋")


@command("import", "匯入法規純文字檔", _configure_import)
def cmd_import(args: argparse.Namespace) -> int:
    workspace, store = open_store(args)
    files = _iter_input_files(args.paths)
    if not files:
        raise SystemExit("沒有找到可匯入的檔案")

    failures = 0
    for path in files:
        if not path.is_file():
            echo(f"× 找不到檔案：{path}")
            failures += 1
            continue
        try:
            law = parse_file(str(path), name=args.name, encoding=args.encoding)
            kept = workspace.keep_source(path)
            if kept:
                law.source = str(kept)
            law_id = store.import_law(law, version_label=args.version, replace=args.replace)
        except ValueError as error:
            echo(f"× {path.name}：{error}")
            failures += 1
            continue
        stats = store.law_stats(law_id)
        echo(
            f"✓ {law.name}（{law.version_label}）"
            f" 條文 {stats['articles']} 條、結構 {stats['divisions']} 個、參照 {stats['references']} 筆"
            f" → id={law_id}"
        )
    store.close()
    return 1 if failures else 0


@command("laws", "列出已匯入的法規")
def cmd_laws(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    laws = store.list_laws()
    if not laws:
        echo("尚未匯入任何法規。可執行：law import <檔案>")
    for law in laws:
        echo(
            f"[{law['id']:>3}] {law['name']}"
            f"（{law['version_label'] or '未註明版本'}）"
            f" 條文 {law['article_count']} 條"
            + (f"　{law['category']}" if law["category"] else "")
        )
    store.close()
    return 0


def _configure_tree(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("law", help="法規名稱或 id")
    parser.add_argument("--version", default="", help="版本標籤")
    parser.add_argument("--articles", action="store_true", help="同時列出條文")


@command("tree", "顯示編章節結構", _configure_tree)
def cmd_tree(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    law = resolve_law(store, args.law, args.version)
    echo(f"{law['name']}（{law['version_label']}）")

    def walk(nodes: list[dict], depth: int) -> None:
        for node in nodes:
            if node.get("type") == "article":
                if args.articles:
                    echo("  " * depth + f"· {node['label']} {node['preview']}")
                continue
            echo("  " * depth + node["heading"])
            walk(node.get("children", []), depth + 1)

    walk(store.tree(law["id"]), 1)
    store.close()
    return 0


def _configure_show(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("law", help="法規名稱或 id")
    parser.add_argument("article", help="條號，例如 12 或 12-1")
    parser.add_argument("--version", default="", help="版本標籤")


@command("show", "顯示單一條文（含參照、標籤、筆記）", _configure_show)
def cmd_show(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    law = resolve_law(store, args.law, args.version)
    number, sub = parse_article_argument(args.article)
    article = store.find_article(law["id"], number, sub)
    if article is None:
        raise SystemExit(f"{law['name']} 沒有這一條：{args.article}")

    echo(f"{law['name']} {article['label_chinese']}（{article['label']}）")
    if article["division_path"]:
        echo(f"結構：{article['division_path']}")
    echo("-" * 40)
    echo("（刪除）" if article["is_deleted"] else article["text"])
    if article["tags"]:
        echo("-" * 40)
        echo("標籤：" + "、".join(tag["name"] for tag in article["tags"]))
    if article["note"]:
        echo("-" * 40)
        echo("筆記：" + article["note"]["body"])
    if article["outgoing"]:
        echo("-" * 40)
        echo("本條引用：")
        for ref in article["outgoing"]:
            target = ref["target_law"] or law["name"]
            mark = "→" if ref["to_article_id"] else "·"
            label = format_article_label(ref["target_article"], ref["target_sub"])
            echo(f"  {mark} [{ref['relation']}] {target} {label}　{ref['raw_text']}")
    if article["incoming"]:
        echo("-" * 40)
        echo("被引用於：")
        for ref in article["incoming"]:
            echo(f"  ← [{ref['relation']}] {ref['from_law_name']} {ref['from_label']}")
    store.close()
    return 0


def _configure_search(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", help="關鍵字")
    parser.add_argument("--law", default="", help="限定法規")
    parser.add_argument("--tag", action="append", default=[], help="限定標籤（可重複）")
    parser.add_argument("--limit", type=int, default=20, help="結果數量上限")


@command("search", "全文檢索條文", _configure_search)
def cmd_search(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    law_ids = None
    if args.law:
        law_ids = [resolve_law(store, args.law)["id"]]
    hits = store.search(args.query, law_ids=law_ids, tags=args.tag, limit=args.limit, record=True)
    if not hits:
        echo("沒有符合的條文。")
    for hit in hits:
        version = f"（{hit.law_version}）" if hit.law_version else ""
        echo(f"{hit.law_name}{version} {hit.label}　#{hit.article_id}")
        if hit.division_path:
            echo(f"  {hit.division_path}")
        echo(f"  {hit.snippet}")
    echo(f"\n共 {len(hits)} 筆")
    store.close()
    return 0


def _configure_refs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("law", help="法規名稱或 id")
    parser.add_argument("article", nargs="?", help="條號；省略時輸出整部法規的參照表")
    parser.add_argument("--incoming", action="store_true", help="只看被引用")


@command("refs", "查詢交互參照（準用／適用／依據）", _configure_refs)
def cmd_refs(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    law = resolve_law(store, args.law)
    if args.article:
        number, sub = parse_article_argument(args.article)
        article = store.find_article(law["id"], number, sub)
        if article is None:
            raise SystemExit("找不到條文")
        rows = article["incoming"] if args.incoming else article["outgoing"]
        if not rows:
            echo("沒有參照紀錄。")
        for ref in rows:
            if args.incoming:
                echo(f"← [{ref['relation']}] {ref['from_law_name']} {ref['from_label']}　{ref['raw_text']}")
            else:
                label = format_article_label(ref["target_article"], ref["target_sub"])
                echo(f"→ [{ref['relation']}] {ref['target_law'] or law['name']} {label}　{ref['raw_text']}")
    else:
        for row in store.reference_report(law["id"]):
            label = format_article_label(row["target_article"], row["target_sub"])
            echo(f"{row['from_label']} → [{row['relation']}] {row['target_law'] or '本法'} {label}")
    store.close()
    return 0


def _configure_tag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["add", "rm", "list", "show"], help="操作")
    parser.add_argument("name", nargs="?", help="標籤名稱")
    parser.add_argument("--law", default="", help="法規名稱或 id")
    parser.add_argument("--article", default="", help="條號")
    parser.add_argument("--color", default="", help="標籤顏色（供 App 顯示）")


@command("tag", "管理標籤（自訂分類）", _configure_tag)
def cmd_tag(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    if args.action == "list":
        for tag in store.list_tags():
            echo(f"{tag['name']}（{tag['usage']} 條）{tag['color']}")
        store.close()
        return 0
    if args.action == "show":
        if not args.name:
            raise SystemExit("請指定標籤名稱")
        for row in store.articles_by_tag(args.name):
            echo(f"{row['law_name']} {row['label']}　{row['text'].splitlines()[0][:50]}")
        store.close()
        return 0

    if not (args.name and args.law and args.article):
        raise SystemExit("需要 name、--law 與 --article")
    law = resolve_law(store, args.law)
    number, sub = parse_article_argument(args.article)
    article = store.find_article(law["id"], number, sub)
    if article is None:
        raise SystemExit("找不到條文")
    if args.action == "add":
        store.add_tag(article["id"], args.name, args.color)
        echo(f"已為 {law['name']} {article['label']} 加上標籤「{args.name}」")
    else:
        store.remove_tag(article["id"], args.name)
        echo(f"已移除標籤「{args.name}」")
    store.close()
    return 0


def _configure_note(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["set", "show", "list"], help="操作")
    parser.add_argument("--law", default="", help="法規名稱或 id")
    parser.add_argument("--article", default="", help="條號")
    parser.add_argument("--body", default="", help="筆記內容；省略時從標準輸入讀取")


@command("note", "管理條文筆記", _configure_note)
def cmd_note(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    if args.action == "list":
        for note in store.list_notes():
            echo(f"{note['law_name']} {note['label']}（{note['updated_at']}）")
            echo(f"  {note['body'].splitlines()[0][:60]}")
        store.close()
        return 0

    if not (args.law and args.article):
        raise SystemExit("需要 --law 與 --article")
    law = resolve_law(store, args.law)
    number, sub = parse_article_argument(args.article)
    article = store.find_article(law["id"], number, sub)
    if article is None:
        raise SystemExit("找不到條文")
    if args.action == "show":
        note = store.get_note(article["id"])
        echo(note["body"] if note else "（尚無筆記）")
    else:
        body = args.body or sys.stdin.read()
        store.set_note(article["id"], body.strip())
        echo(f"已儲存筆記：{law['name']} {article['label']}")
    store.close()
    return 0


def _configure_bookmark(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("law", nargs="?", help="法規名稱或 id；省略時列出收藏")
    parser.add_argument("article", nargs="?", help="條號")


@command("bookmark", "收藏／取消收藏條文", _configure_bookmark)
def cmd_bookmark(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    if not args.law:
        for row in store.list_bookmarks():
            echo(f"{row['law_name']} {row['label']}　{row['text'].splitlines()[0][:50]}")
        store.close()
        return 0
    if not args.article:
        raise SystemExit("請指定條號")
    law = resolve_law(store, args.law)
    number, sub = parse_article_argument(args.article)
    article = store.find_article(law["id"], number, sub)
    if article is None:
        raise SystemExit("找不到條文")
    added = store.toggle_bookmark(article["id"])
    echo(("已收藏：" if added else "已取消收藏：") + f"{law['name']} {article['label']}")
    store.close()
    return 0


def _configure_diff(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("law", help="法規名稱")
    parser.add_argument("--from", dest="old", required=True, help="舊版版本標籤或 id")
    parser.add_argument("--to", dest="new", required=True, help="新版版本標籤或 id")
    parser.add_argument("--all", action="store_true", help="含未變動條文")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")


@command("diff", "比對同一部法規的兩個版本（修法比對）", _configure_diff)
def cmd_diff(args: argparse.Namespace) -> int:
    _, store = open_store(args)
    old = resolve_law(store, args.old if args.old.isdigit() else args.law, "" if args.old.isdigit() else args.old)
    new = resolve_law(store, args.new if args.new.isdigit() else args.law, "" if args.new.isdigit() else args.new)
    result = diff_law_versions(store, old["id"], new["id"], include_unchanged=args.all)
    if args.json:
        echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        echo(render_diff_text(result, only_changed=not args.all))
    store.close()
    return 0


def _configure_export(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "format",
        choices=sorted(EXPORTER_DESCRIPTIONS),
        help="；".join(f"{name}={desc}" for name, desc in sorted(EXPORTER_DESCRIPTIONS.items())),
    )
    parser.add_argument("--law", default="", help="限定法規；省略時匯出全部")
    parser.add_argument("--out", "-o", default="", help="輸出檔案；省略時存到 exports 資料夾")
    parser.add_argument("--stdout", action="store_true", help="直接輸出到畫面")


@command("export", "匯出 Markdown／JSON／CSV／離線資料包", _configure_export)
def cmd_export(args: argparse.Namespace) -> int:
    workspace, store = open_store(args)
    law_id = resolve_law(store, args.law)["id"] if args.law else None
    content = export(store, args.format, law_id)
    if args.stdout:
        sys.stdout.write(content)
        store.close()
        return 0

    if args.out:
        target = Path(args.out).expanduser()
    else:
        law = store.get_law(law_id) if law_id else None
        stem = (law["name"] if law else "全部法規").replace("/", "_")
        target = workspace.exports_dir / f"{stem}-{args.format}{EXPORTER_EXTENSIONS[args.format]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    echo(f"已匯出：{target}")
    store.close()
    return 0


def _configure_bundle(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", "-o", default="", help="輸出路徑（預設 bundles/bundle.json）")
    parser.add_argument("--for-app", action="store_true", help="同時寫入 App 靜態目錄，供離線使用")


@command("bundle", "產生 App 離線資料包", _configure_bundle)
def cmd_bundle(args: argparse.Namespace) -> int:
    workspace, store = open_store(args)
    content = export(store, "bundle", None)
    target = Path(args.out).expanduser() if args.out else workspace.bundles_dir / "bundle.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    echo(f"已產生離線資料包：{target}（{len(content) / 1024:.1f} KB）")
    if args.for_app:
        app_target = Path(__file__).parent / "web" / "data" / "bundle.json"
        app_target.parent.mkdir(parents=True, exist_ok=True)
        app_target.write_text(content, encoding="utf-8")
        echo(f"已同步至 App 目錄：{app_target}")
    store.close()
    return 0


@command("stats", "統計整體知識庫")
def cmd_stats(args: argparse.Namespace) -> int:
    workspace, store = open_store(args)
    stats = store.stats()
    echo(f"資料庫：{workspace.db_path}")
    echo(f"法規 {stats['laws']} 部、條文 {stats['articles']} 條（其中刪除 {stats['deleted_articles']} 條）")
    echo(f"交互參照 {stats['references']} 筆，已連結 {stats['resolved_references']} 筆")
    echo(f"標籤 {stats['tags']} 個、筆記 {stats['notes']} 則、收藏 {stats['bookmarks']} 條")
    if stats["relations"]:
        echo("引用關係分布：" + "、".join(f"{row['relation']} {row['count']}" for row in stats["relations"]))
    echo(f"資料庫結構版本：v{stats['schema_version']}")
    store.close()
    return 0


@command("backup", "備份資料庫到 backups 資料夾")
def cmd_backup(args: argparse.Namespace) -> int:
    workspace = open_workspace(args)
    target = workspace.backup_database()
    if target is None:
        echo("資料庫還不存在，無需備份。")
        return 0
    echo(f"已備份：{target}")
    return 0


def _configure_serve(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="0.0.0.0", help="監聽位址（預設 0.0.0.0，手機可連）")
    parser.add_argument("--port", type=int, default=8383, help="連接埠（預設 8383）")
    parser.add_argument("--no-browser", action="store_true", help="啟動後不自動開啟瀏覽器")


@command("serve", "啟動 App 伺服器（手機可安裝的網頁版）", _configure_serve)
def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    workspace = open_workspace(args)
    serve(workspace, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


# --------------------------------------------------------------------- 進入點
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="law",
        description="法規整理工具 lawkit：把法規文字整理成可檢索、可註記、可比對的知識庫。",
        epilog=f"資料預設整理在 D:\\Vivian_Law（Windows），可用 {ENV_VAR} 或 --data-dir 變更。",
    )
    parser.add_argument("--version", action="version", version=f"lawkit {__version__}")
    parser.add_argument("--data-dir", help="資料根目錄")
    parser.add_argument("--db", help="指定資料庫檔案（預設為資料根目錄下的 lawkit.db）")

    subparsers = parser.add_subparsers(dest="command", metavar="指令")
    for name in sorted(COMMANDS):
        entry = COMMANDS[name]
        subparser = subparsers.add_parser(name, help=entry.help, description=entry.help)
        if entry.configure:
            entry.configure(subparser)
        subparser.set_defaults(_handler=entry.handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler | None = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return handler(args)
    except KeyboardInterrupt:
        echo("\n已中斷。")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
