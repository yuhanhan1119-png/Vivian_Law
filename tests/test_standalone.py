import json
import re
import tempfile
import unittest
from pathlib import Path

from lawkit.parser import parse_file
from lawkit.standalone import build_single_file, write_single_file
from lawkit.storage import LawStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class SingleFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = LawStore(str(Path(cls.tmp.name) / "standalone.db"))
        cls.store.import_law(parse_file(str(SAMPLES / "民法-節錄.txt")))
        cls.store.import_law(parse_file(str(SAMPLES / "示範資料保護法-舊版.txt")))
        cls.html = build_single_file(cls.store, title="測試單檔版")

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def test_is_self_contained(self):
        """不可再引用任何外部檔案，否則雙擊開啟時會壞掉。"""
        for reference in ('href="styles.css"', 'src="app.js"', 'href="manifest.webmanifest"',
                          'href="icons/icon.svg"', 'href="icons/apple-touch-icon.png"'):
            with self.subTest(reference=reference):
                self.assertNotIn(reference, self.html)

    def test_contains_app_and_data(self):
        self.assertIn("window.__LAWKIT_BUNDLE__", self.html)
        self.assertIn("<style>", self.html)
        self.assertIn("法規整理 App", self.html)  # app.js 的檔頭註解
        self.assertIn("中華民國民法（節錄）", self.html)
        self.assertIn("<title>測試單檔版</title>", self.html)

    def test_bundle_is_valid_json(self):
        match = re.search(r"window\.__LAWKIT_BUNDLE__ = (\{.*?\});</script>", self.html, re.S)
        self.assertIsNotNone(match)
        bundle = json.loads(match.group(1).replace("<\\/", "</"))
        self.assertEqual(bundle["kind"], "lawkit-bundle")
        self.assertEqual(len(bundle["laws"]), 2)
        self.assertTrue(bundle["laws"][0]["articles"])

    def test_script_terminator_is_escaped(self):
        """條文若含有 </script> 會提早結束區塊，必須轉義。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = LawStore(str(Path(tmp) / "escape.db"))
            from lawkit.parser import parse_law

            store.import_law(parse_law("測試法\n第 1 條\n本條含有 </script> 字樣。"))
            html = build_single_file(store)
            store.close()
        self.assertNotIn("</script> 字樣", html)
        self.assertIn("<\\/script> 字樣", html)

    def test_write_single_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "巢狀" / "app.html"
            written = write_single_file(self.store, target)
            self.assertTrue(written.is_file())
            self.assertGreater(written.stat().st_size, 10_000)


class StandaloneCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Vivian_Law"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str]:
        import contextlib
        import io

        from lawkit.cli import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--data-dir", str(self.root), *args])
        return code, buffer.getvalue()

    def test_requires_imported_law(self):
        with self.assertRaises(SystemExit):
            self.run_cli("standalone")

    def test_default_output_in_exports(self):
        self.run_cli("import", str(SAMPLES / "民法-節錄.txt"))
        code, output = self.run_cli("standalone")
        self.assertEqual(code, 0)
        produced = list((self.root / "exports").glob("*.html"))
        self.assertEqual(len(produced), 1)
        self.assertIn("已產生單檔 App", output)
        self.assertIn("window.__LAWKIT_BUNDLE__", produced[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
