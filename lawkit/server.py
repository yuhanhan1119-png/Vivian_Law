"""本機 HTTP 伺服器：提供 JSON API 與 App 靜態檔案（僅用標準函式庫）。

新增 API 的方式：寫一個函式，用 :func:`route` 裝飾即可，例如::

    @route("GET", r"^/api/hello$")
    def hello(store, match, query, body):
        return {"message": "hi"}

回傳 ``dict``／``list`` 會自動序列化為 JSON；回傳 ``(status, payload)`` 可自訂狀態碼。
"""

from __future__ import annotations

import json
import mimetypes
import re
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from .config import Workspace
from .diff import diff_law_versions
from .exporter import EXPORTER_DESCRIPTIONS, EXPORTER_EXTENSIONS, export
from .parser import parse_law
from .storage import LawStore

WEB_ROOT = Path(__file__).parent / "web"

RouteHandler = Callable[..., Any]
ROUTES: list[tuple[str, re.Pattern[str], RouteHandler]] = []


def route(method: str, pattern: str) -> Callable[[RouteHandler], RouteHandler]:
    def decorator(handler: RouteHandler) -> RouteHandler:
        ROUTES.append((method.upper(), re.compile(pattern), handler))
        return handler

    return decorator


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
        self.message = message


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


# --------------------------------------------------------------------- API
@route("GET", r"^/api/health$")
def api_health(store: LawStore, match, query, body) -> dict:
    return {"ok": True, "schema_version": store.schema_version}


@route("GET", r"^/api/meta$")
def api_meta(store: LawStore, match, query, body) -> dict:
    workspace: Workspace = store.workspace  # type: ignore[attr-defined]
    return {
        "app_title": workspace.settings.get("app_title", "法規整理"),
        "paths": workspace.describe(),
        "stats": store.stats(),
        "exporters": [
            {"name": name, "description": description, "extension": EXPORTER_EXTENSIONS[name]}
            for name, description in sorted(EXPORTER_DESCRIPTIONS.items())
        ],
    }


@route("GET", r"^/api/laws$")
def api_laws(store: LawStore, match, query, body) -> list[dict]:
    return store.list_laws()


@route("GET", r"^/api/laws/(?P<law_id>\d+)$")
def api_law(store: LawStore, match, query, body) -> dict:
    law_id = int(match.group("law_id"))
    law = store.get_law(law_id)
    if law is None:
        raise ApiError("找不到法規", 404)
    return {
        **law,
        "stats": store.law_stats(law_id),
        "versions": store.law_versions(law["name"]),
        "tree": store.tree(law_id),
    }


@route("GET", r"^/api/laws/(?P<law_id>\d+)/tree$")
def api_law_tree(store: LawStore, match, query, body) -> list[dict]:
    return store.tree(int(match.group("law_id")))


@route("GET", r"^/api/laws/(?P<law_id>\d+)/articles$")
def api_law_articles(store: LawStore, match, query, body) -> list[dict]:
    return store.articles(int(match.group("law_id")))


@route("DELETE", r"^/api/laws/(?P<law_id>\d+)$")
def api_delete_law(store: LawStore, match, query, body) -> dict:
    store.delete_law(int(match.group("law_id")))
    return {"deleted": True}


@route("GET", r"^/api/articles/(?P<article_id>\d+)$")
def api_article(store: LawStore, match, query, body) -> dict:
    article = store.get_article(int(match.group("article_id")))
    if article is None:
        raise ApiError("找不到條文", 404)
    return article


@route("GET", r"^/api/search$")
def api_search(store: LawStore, match, query, body) -> dict:
    keyword = _first(query, "q")
    law_ids = [int(value) for value in query.get("law", []) if value.isdigit()]
    hits = store.search(
        keyword,
        law_ids=law_ids or None,
        tags=query.get("tag") or None,
        limit=_int(_first(query, "limit"), 50),
        record=bool(keyword),
    )
    return {"query": keyword, "count": len(hits), "hits": [hit.to_dict() for hit in hits]}


@route("GET", r"^/api/history$")
def api_history(store: LawStore, match, query, body) -> list[dict]:
    return store.search_history(_int(_first(query, "limit"), 10))


@route("GET", r"^/api/tags$")
def api_tags(store: LawStore, match, query, body) -> list[dict]:
    return store.list_tags()


@route("GET", r"^/api/tags/(?P<name>.+)$")
def api_tag_articles(store: LawStore, match, query, body) -> list[dict]:
    return store.articles_by_tag(unquote(match.group("name")))


@route("POST", r"^/api/articles/(?P<article_id>\d+)/tags$")
def api_add_tag(store: LawStore, match, query, body) -> dict:
    name = (body or {}).get("name", "").strip()
    if not name:
        raise ApiError("請提供標籤名稱")
    article_id = int(match.group("article_id"))
    store.add_tag(article_id, name, (body or {}).get("color", ""))
    return {"tags": store.article_tags(article_id)}


@route("DELETE", r"^/api/articles/(?P<article_id>\d+)/tags/(?P<name>.+)$")
def api_remove_tag(store: LawStore, match, query, body) -> dict:
    article_id = int(match.group("article_id"))
    store.remove_tag(article_id, unquote(match.group("name")))
    return {"tags": store.article_tags(article_id)}


@route("PUT", r"^/api/articles/(?P<article_id>\d+)/note$")
def api_set_note(store: LawStore, match, query, body) -> dict:
    article_id = int(match.group("article_id"))
    store.set_note(article_id, (body or {}).get("body", ""))
    return {"note": store.get_note(article_id)}


@route("POST", r"^/api/articles/(?P<article_id>\d+)/bookmark$")
def api_bookmark(store: LawStore, match, query, body) -> dict:
    article_id = int(match.group("article_id"))
    return {"bookmarked": store.toggle_bookmark(article_id)}


@route("GET", r"^/api/bookmarks$")
def api_bookmarks(store: LawStore, match, query, body) -> list[dict]:
    return store.list_bookmarks()


@route("GET", r"^/api/notes$")
def api_notes(store: LawStore, match, query, body) -> list[dict]:
    return store.list_notes()


@route("GET", r"^/api/diff$")
def api_diff(store: LawStore, match, query, body) -> dict:
    old_id = _int(_first(query, "from"), 0)
    new_id = _int(_first(query, "to"), 0)
    if not (old_id and new_id):
        raise ApiError("需要 from 與 to 兩個法規 id")
    include_all = _first(query, "all") in {"1", "true", "yes"}
    return diff_law_versions(store, old_id, new_id, include_unchanged=include_all).to_dict()


@route("GET", r"^/api/bundle$")
def api_bundle(store: LawStore, match, query, body) -> dict:
    return json.loads(export(store, "bundle", None))


@route("GET", r"^/api/export$")
def api_export(store: LawStore, match, query, body):
    fmt = _first(query, "format", "markdown")
    law = _first(query, "law")
    law_id = int(law) if law.isdigit() else None
    try:
        content = export(store, fmt, law_id)
    except ValueError as error:
        raise ApiError(str(error)) from error
    return 200, {"format": fmt, "content": content}


@route("POST", r"^/api/import$")
def api_import(store: LawStore, match, query, body) -> dict:
    payload = body or {}
    text = payload.get("text", "")
    if not text.strip():
        raise ApiError("請提供法規文字")
    law = parse_law(text, name=payload.get("name") or None, source="App 貼上匯入")
    try:
        law_id = store.import_law(
            law,
            version_label=payload.get("version", ""),
            replace=bool(payload.get("replace")),
        )
    except ValueError as error:
        raise ApiError(str(error), 409) from error
    return {"law_id": law_id, "name": law.name, "stats": store.law_stats(law_id)}


# --------------------------------------------------------------- HTTP handler
class LawRequestHandler(BaseHTTPRequestHandler):
    server_version = "lawkit"
    workspace: Workspace

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.path.startswith("/api/"):
            print(f"  {self.command} {self.path}")

    # ------------------------------------------------------------ 基礎工具
    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict | None:
        length = _int(self.headers.get("Content-Length"), 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file():
            # 單頁應用：未知路徑回傳首頁
            target = WEB_ROOT / "index.html"
            if not target.is_file():
                self._send_json({"error": "找不到 App 檔案"}, 404)
                return
        content = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        if target.suffix in {".html", ".js", ".css", ".json", ".webmanifest", ".svg"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header(
            "Cache-Control", "no-cache" if target.suffix in {".html", ".js"} else "public, max-age=3600"
        )
        self.end_headers()
        self.wfile.write(content)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            if method == "GET":
                self._serve_static(parsed.path)
            else:
                self._send_json({"error": "不支援的請求"}, 405)
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        body = self._read_body() if method in {"POST", "PUT", "PATCH", "DELETE"} else None

        for route_method, pattern, handler in ROUTES:
            match = pattern.match(parsed.path)
            if not match:
                continue
            if route_method != method:
                continue
            store = LawStore(str(self.workspace.db_path))
            store.workspace = self.workspace  # type: ignore[attr-defined]
            try:
                result = handler(store, match, query, body)
                if isinstance(result, tuple):
                    status, payload = result
                else:
                    status, payload = 200, result
                self._send_json(payload, status)
            except ApiError as error:
                self._send_json({"error": error.message}, error.status)
            except Exception as error:  # pragma: no cover - 保護伺服器不因單一請求崩潰
                self._send_json({"error": f"伺服器錯誤：{error}"}, 500)
            finally:
                store.close()
            return
        self._send_json({"error": f"沒有這個 API：{parsed.path}"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


def local_ip_addresses() -> list[str]:
    """猜測本機在區域網路上的 IP，方便手機連線安裝 App。"""
    addresses: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("8.8.8.8", 80))
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass
    return addresses


def create_server(workspace: Workspace, host: str = "0.0.0.0", port: int = 8383) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (LawRequestHandler,), {"workspace": workspace})
    return ThreadingHTTPServer((host, port), handler)


def serve(
    workspace: Workspace,
    host: str = "0.0.0.0",
    port: int = 8383,
    *,
    open_browser: bool = True,
) -> None:
    LawStore(str(workspace.db_path)).close()  # 確保資料庫與結構已建立
    httpd = create_server(workspace, host, port)
    urls = [f"http://127.0.0.1:{port}/"] + [f"http://{ip}:{port}/" for ip in local_ip_addresses()]

    print("法規整理 App 已啟動")
    print(f"  資料夾：{workspace.root}")
    print(f"  資料庫：{workspace.db_path}")
    for url in urls:
        print(f"  網址　：{url}")
    print("  手機安裝：連上同一個 Wi-Fi，用手機瀏覽器開啟上面的網址，選擇「加入主畫面」")
    print("按 Ctrl+C 結束。")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
