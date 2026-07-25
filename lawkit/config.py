"""資料夾與設定管理：決定「知識庫存在哪裡」。

資料根目錄（data root）的決定順序：

1. 程式參數 ``--data-dir`` / :func:`resolve_data_root` 的 ``explicit``
2. 環境變數 ``LAWKIT_HOME``
3. 目前目錄或上層目錄中的 ``lawkit.json`` 所指定的 ``data_dir``
4. 平台預設值：Windows 為 ``D:\\Vivian_Law``（D 槽不存在時退回使用者家目錄），
   其他系統為 ``~/Vivian_Law``

資料根目錄的結構：

    D:\\Vivian_Law\\
    ├── lawkit.db        SQLite 知識庫（條文、參照、標籤、筆記、收藏）
    ├── config.json      本地設定
    ├── sources\\        匯入時保留的法規原始文字檔
    ├── exports\\        匯出的 Markdown／JSON／CSV
    ├── bundles\\        App 離線資料包（bundle.json）
    └── backups\\        資料庫備份
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ENV_VAR = "LAWKIT_HOME"
CONFIG_FILE_NAME = "lawkit.json"
DB_FILE_NAME = "lawkit.db"
WINDOWS_DEFAULT = Path(r"D:\Vivian_Law")
POSIX_DEFAULT_NAME = "Vivian_Law"

SUBDIRECTORIES = ("sources", "exports", "bundles", "backups")

DEFAULT_SETTINGS: dict = {
    "data_dir": "",
    "db_file": DB_FILE_NAME,
    "keep_sources": True,
    "default_label_style": "arabic",
    "app_title": "法規整理 Vivian Law",
}


def platform_default_root() -> Path:
    """平台預設資料夾。"""
    if os.name == "nt":
        drive = Path(WINDOWS_DEFAULT.anchor)
        if drive.exists():
            return WINDOWS_DEFAULT
        return Path.home() / POSIX_DEFAULT_NAME
    override = os.environ.get("LAWKIT_POSIX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / POSIX_DEFAULT_NAME


def _find_project_config(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        config_path = candidate / CONFIG_FILE_NAME
        if config_path.is_file():
            return config_path
    return None


def resolve_data_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """依優先順序決定資料根目錄（不會建立資料夾）。"""
    if explicit:
        return Path(explicit).expanduser()

    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()

    config_path = _find_project_config()
    if config_path:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        configured = data.get("data_dir")
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                candidate = config_path.parent / candidate
            return candidate

    return platform_default_root()


@dataclass
class Workspace:
    """代表一個資料根目錄，以及其中的檔案位置。"""

    root: Path
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))

    @classmethod
    def open(
        cls,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        create: bool = True,
        db_path: str | os.PathLike[str] | None = None,
    ) -> "Workspace":
        root = resolve_data_root(data_dir)
        workspace = cls(root=root)
        if create:
            workspace.ensure()
        workspace.settings = {**DEFAULT_SETTINGS, **workspace.read_settings()}
        if db_path:
            workspace.settings["db_file"] = str(db_path)
        return workspace

    # ---------------------------------------------------------------- 路徑
    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def db_path(self) -> Path:
        configured = Path(self.settings.get("db_file") or DB_FILE_NAME).expanduser()
        return configured if configured.is_absolute() else self.root / configured

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def bundles_dir(self) -> Path:
        return self.root / "bundles"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    # ---------------------------------------------------------------- 操作
    def ensure(self) -> "Workspace":
        """建立資料夾結構（可重複執行）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRECTORIES:
            (self.root / name).mkdir(exist_ok=True)
        if not self.config_path.exists():
            self.write_settings({**DEFAULT_SETTINGS, "data_dir": str(self.root)})
        return self

    def read_settings(self) -> dict:
        if not self.config_path.is_file():
            return {}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def write_settings(self, settings: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.settings = {**DEFAULT_SETTINGS, **settings}

    def update_settings(self, **changes: object) -> dict:
        settings = {**DEFAULT_SETTINGS, **self.read_settings(), **changes}
        self.write_settings(settings)
        return settings

    def keep_source(self, path: str | os.PathLike[str]) -> Path | None:
        """把匯入的原始檔複製到 ``sources``，方便日後重新解析或比對。"""
        if not self.settings.get("keep_sources", True):
            return None
        source = Path(path)
        if not source.is_file():
            return None
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        target = self.sources_dir / source.name
        if target.resolve() == source.resolve():
            return target
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.sources_dir / f"{source.stem}-{stamp}{source.suffix}"
        shutil.copy2(source, target)
        return target

    def backup_database(self) -> Path | None:
        """備份資料庫檔案，回傳備份路徑。"""
        if not self.db_path.is_file():
            return None
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backups_dir / f"{self.db_path.stem}-{stamp}.db"
        shutil.copy2(self.db_path, target)
        return target

    def describe(self) -> dict:
        """回報目前使用的所有路徑，供 ``law where`` 顯示。"""
        return {
            "資料根目錄": str(self.root),
            "資料庫": str(self.db_path),
            "設定檔": str(self.config_path),
            "原始檔": str(self.sources_dir),
            "匯出": str(self.exports_dir),
            "離線資料包": str(self.bundles_dir),
            "備份": str(self.backups_dir),
            "來源": self.source_of_root(),
            "資料庫存在": self.db_path.is_file(),
        }

    @staticmethod
    def source_of_root() -> str:
        if os.environ.get(ENV_VAR):
            return f"環境變數 {ENV_VAR}"
        if _find_project_config():
            return f"專案設定檔 {CONFIG_FILE_NAME}"
        return "平台預設值"
