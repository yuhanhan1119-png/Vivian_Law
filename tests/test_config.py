import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lawkit.config import ENV_VAR, Workspace, platform_default_root, resolve_data_root


class ResolveDataRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        os.environ.pop(ENV_VAR, None)

    def tearDown(self):
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_explicit_wins(self):
        self.assertEqual(resolve_data_root(self.root / "explicit"), self.root / "explicit")

    def test_environment_variable(self):
        os.environ[ENV_VAR] = str(self.root / "from-env")
        self.assertEqual(resolve_data_root(), self.root / "from-env")

    def test_project_config_file(self):
        (self.root / "lawkit.json").write_text(
            json.dumps({"data_dir": str(self.root / "from-config")}), encoding="utf-8"
        )
        with mock.patch("lawkit.config.Path.cwd", return_value=self.root):
            self.assertEqual(resolve_data_root(), self.root / "from-config")

    def test_relative_path_in_config_is_resolved(self):
        (self.root / "lawkit.json").write_text(json.dumps({"data_dir": "data"}), encoding="utf-8")
        with mock.patch("lawkit.config.Path.cwd", return_value=self.root):
            self.assertEqual(resolve_data_root(), self.root / "data")

    def test_windows_default_is_d_drive(self):
        with mock.patch("lawkit.config.os.name", "nt"), mock.patch("lawkit.config.Path.exists", return_value=True):
            self.assertIn("Vivian_Law", str(platform_default_root()))

    def test_posix_default_in_home(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAWKIT_POSIX_HOME", None)
            with mock.patch("lawkit.config.os.name", "posix"):
                self.assertEqual(platform_default_root(), Path.home() / "Vivian_Law")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Vivian_Law"
        self.workspace = Workspace.open(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_creates_folder_structure(self):
        for name in ("sources", "exports", "bundles", "backups"):
            self.assertTrue((self.root / name).is_dir(), name)
        self.assertTrue(self.workspace.config_path.is_file())

    def test_default_db_path(self):
        self.assertEqual(self.workspace.db_path, self.root / "lawkit.db")

    def test_custom_db_path(self):
        workspace = Workspace.open(self.root, db_path="其他.db")
        self.assertEqual(workspace.db_path, self.root / "其他.db")

    def test_settings_round_trip(self):
        self.workspace.update_settings(app_title="我的法規庫", keep_sources=False)
        reopened = Workspace.open(self.root)
        self.assertEqual(reopened.settings["app_title"], "我的法規庫")
        self.assertFalse(reopened.settings["keep_sources"])

    def test_keep_source_copies_file(self):
        source = Path(self.tmp.name) / "法規.txt"
        source.write_text("第 1 條\n本法自公布日施行。", encoding="utf-8")
        kept = self.workspace.keep_source(source)
        self.assertIsNotNone(kept)
        self.assertTrue(kept.is_file())
        self.assertEqual(kept.parent, self.workspace.sources_dir)

    def test_keep_source_does_not_overwrite(self):
        source = Path(self.tmp.name) / "法規.txt"
        source.write_text("內容", encoding="utf-8")
        first = self.workspace.keep_source(source)
        second = self.workspace.keep_source(source)
        self.assertNotEqual(first, second)

    def test_keep_source_disabled(self):
        self.workspace.update_settings(keep_sources=False)
        source = Path(self.tmp.name) / "略過.txt"
        source.write_text("內容", encoding="utf-8")
        self.assertIsNone(self.workspace.keep_source(source))

    def test_backup(self):
        self.assertIsNone(self.workspace.backup_database())
        self.workspace.db_path.write_bytes(b"fake-db")
        backup = self.workspace.backup_database()
        self.assertIsNotNone(backup)
        self.assertEqual(backup.read_bytes(), b"fake-db")

    def test_describe_lists_paths(self):
        described = self.workspace.describe()
        self.assertEqual(described["資料根目錄"], str(self.root))
        self.assertIn("資料庫", described)


if __name__ == "__main__":
    unittest.main()
