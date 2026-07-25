"""把整個 App 與知識庫壓成一個 HTML 檔。

產物是單一檔案：雙擊就能在瀏覽器開啟，不需要 Python、不需要伺服器、不需要網路，
適合拿來試用或分享給別人（例如寄給同事一份「某某法規整理」）。

限制：單檔版無法匯入新法規，標註（標籤／筆記／收藏）存在瀏覽器本機儲存空間。
"""

from __future__ import annotations

import base64
from pathlib import Path

from .exporter import export
from .storage import LawStore

WEB_ROOT = Path(__file__).parent / "web"


def _escape_for_script(json_text: str) -> str:
    """避免資料中的 ``</script>`` 提早結束 script 區塊。"""
    return json_text.replace("</", "<\\/")


def build_single_file(store: LawStore, *, title: str = "") -> str:
    """組出單檔版 HTML。"""
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    css = (WEB_ROOT / "styles.css").read_text(encoding="utf-8")
    script = (WEB_ROOT / "app.js").read_text(encoding="utf-8")
    icon = (WEB_ROOT / "icons" / "icon.svg").read_text(encoding="utf-8")
    bundle = _escape_for_script(export(store, "bundle", None).strip())

    icon_data = "data:image/svg+xml;base64," + base64.b64encode(icon.encode("utf-8")).decode("ascii")

    replacements = {
        '<link rel="stylesheet" href="styles.css" />': f"<style>\n{css}\n</style>",
        '<link rel="manifest" href="manifest.webmanifest" />': "",
        '<link rel="icon" href="icons/icon.svg" type="image/svg+xml" />':
            f'<link rel="icon" href="{icon_data}" type="image/svg+xml" />',
        '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png" />':
            f'<link rel="apple-touch-icon" href="{icon_data}" />',
        '<script src="app.js" type="module"></script>':
            f'<script>window.__LAWKIT_BUNDLE__ = {bundle};</script>\n'
            f'<script type="module">\n{script}\n</script>',
    }
    for old, new in replacements.items():
        if old not in html:
            raise RuntimeError(f"index.html 缺少預期的片段，無法組成單檔版：{old}")
        html = html.replace(old, new)

    if title:
        html = html.replace("<title>法規整理</title>", f"<title>{title}</title>")
    return html


def write_single_file(store: LawStore, target: Path, *, title: str = "") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_single_file(store, title=title), encoding="utf-8")
    return target
