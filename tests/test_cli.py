import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from lawkit.cli import main

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def run(*args: str) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(args))
    return code, buffer.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Vivian_Law"
        self.base = ["--data-dir", str(self.root)]

    def tearDown(self):
        self.tmp.cleanup()

    def law(self, *args: str) -> tuple[int, str]:
        return run(*self.base, *args)

    def import_samples(self) -> None:
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        self.law("import", str(SAMPLES / "示範資料保護法-舊版.txt"))
        self.law("import", str(SAMPLES / "示範資料保護法-新版.txt"))

    def test_init_creates_workspace(self):
        code, output = self.law("init")
        self.assertEqual(code, 0)
        self.assertIn("知識庫已就緒", output)
        self.assertTrue((self.root / "lawkit.db").is_file())
        self.assertTrue((self.root / "sources").is_dir())

    def test_where(self):
        self.law("init")
        code, output = self.law("where")
        self.assertEqual(code, 0)
        self.assertIn(str(self.root), output)

    def test_import_and_laws(self):
        code, output = self.law("import", str(SAMPLES / "民法-節錄.txt"))
        self.assertEqual(code, 0)
        self.assertIn("中華民國民法（節錄）", output)
        self.assertIn("條文 10 條", output)

        code, output = self.law("laws")
        self.assertIn("中華民國民法（節錄）", output)

    def test_import_keeps_source_copy(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        copies = list((self.root / "sources").glob("*.txt"))
        self.assertEqual(len(copies), 1)

    def test_import_duplicate_reports_error(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        code, output = self.law("import", str(SAMPLES / "民法-節錄.txt"))
        self.assertEqual(code, 1)
        self.assertIn("已存在相同版本", output)

    def test_import_missing_file(self):
        code, output = self.law("import", str(self.root / "沒有這個檔案.txt"))
        self.assertEqual(code, 1)
        self.assertIn("找不到檔案", output)

    def test_tree_and_show(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        _, tree = self.law("tree", "民法")
        self.assertIn("第一編 總則", tree)
        self.assertIn("第五款 侵權行為", tree)

        _, shown = self.law("show", "民法", "184")
        self.assertIn("第一百八十四條", shown)
        self.assertIn("損害賠償", shown)

    def test_show_sub_article(self):
        self.law("import", str(SAMPLES / "示範資料保護法-新版.txt"))
        _, output = self.law("show", "示範資料保護法", "11-1")
        self.assertIn("第十一條之一", output)
        self.assertIn("本條引用", output)

    def test_search(self):
        self.import_samples()
        _, output = self.law("search", "損害賠償")
        self.assertIn("【損害賠償】", output)
        _, limited = self.law("search", "損害賠償", "--law", "民法")
        self.assertIn("中華民國民法", limited)
        self.assertNotIn("示範資料保護法", limited)

    def test_refs_incoming_and_outgoing(self):
        self.law("import", str(SAMPLES / "示範資料保護法-舊版.txt"))
        _, outgoing = self.law("refs", "示範資料保護法", "11")
        self.assertIn("第 10 條", outgoing)
        _, incoming = self.law("refs", "示範資料保護法", "10", "--incoming")
        self.assertIn("第 11 條", incoming)

    def test_tag_note_bookmark_flow(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        _, output = self.law("tag", "add", "誠信原則", "--law", "民法", "--article", "148")
        self.assertIn("已為", output)
        _, listing = self.law("tag", "list")
        self.assertIn("誠信原則", listing)
        _, tagged = self.law("tag", "show", "誠信原則")
        self.assertIn("第 148 條", tagged)

        self.law("note", "set", "--law", "民法", "--article", "148", "--body", "帝王條款")
        _, note = self.law("note", "show", "--law", "民法", "--article", "148")
        self.assertIn("帝王條款", note)

        _, marked = self.law("bookmark", "民法", "148")
        self.assertIn("已收藏", marked)
        _, marks = self.law("bookmark")
        self.assertIn("第 148 條", marks)

    def test_diff_between_versions(self):
        self.import_samples()
        _, output = self.law(
            "diff", "示範資料保護法",
            "--from", "民國 108 年 05 月 10 日",
            "--to", "民國 112 年 11 月 20 日",
        )
        self.assertIn("修法比對", output)
        self.assertIn("[新增] 第 11-1 條", output)
        self.assertIn("[修正] 第 11 條", output)

    def test_diff_json(self):
        self.import_samples()
        _, output = self.law(
            "diff", "示範資料保護法",
            "--from", "民國 108 年 05 月 10 日",
            "--to", "民國 112 年 11 月 20 日",
            "--json",
        )
        payload = json.loads(output)
        self.assertEqual(payload["law_name"], "示範資料保護法")
        self.assertIn("summary", payload)

    def test_export_writes_file(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        code, output = self.law("export", "markdown", "--law", "民法")
        self.assertEqual(code, 0)
        exported = list((self.root / "exports").glob("*.md"))
        self.assertEqual(len(exported), 1)
        self.assertIn("已匯出", output)
        self.assertIn("# 中華民國民法（節錄）", exported[0].read_text(encoding="utf-8"))

    def test_export_stdout(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        _, output = self.law("export", "csv", "--stdout")
        self.assertIn("法規,版本", output)

    def test_bundle(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        code, output = self.law("bundle")
        self.assertEqual(code, 0)
        bundle = self.root / "bundles" / "bundle.json"
        self.assertTrue(bundle.is_file())
        payload = json.loads(bundle.read_text(encoding="utf-8"))
        self.assertEqual(payload["kind"], "lawkit-bundle")
        self.assertIn("離線資料包", output)

    def test_stats_and_backup(self):
        self.law("import", str(SAMPLES / "民法-節錄.txt"))
        _, stats = self.law("stats")
        self.assertIn("法規 1 部", stats)
        _, backup = self.law("backup")
        self.assertIn("已備份", backup)
        self.assertEqual(len(list((self.root / "backups").glob("*.db"))), 1)

    def test_config_set(self):
        self.law("init")
        _, output = self.law("config", "--set", "app_title=我的法規庫")
        self.assertIn("我的法規庫", output)

    def test_no_command_prints_help(self):
        code, output = run()
        self.assertEqual(code, 0)
        self.assertIn("法規整理工具", output)

    def test_unknown_law_exits(self):
        with self.assertRaises(SystemExit):
            self.law("show", "不存在的法", "1")


if __name__ == "__main__":
    unittest.main()
