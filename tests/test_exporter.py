import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from lawkit.exporter import EXPORTERS, export
from lawkit.parser import parse_file
from lawkit.storage import LawStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LawStore(str(Path(self.tmp.name) / "export.db"))
        self.law_id = self.store.import_law(parse_file(str(SAMPLES / "民法-節錄.txt")))
        article = self.store.find_article(self.law_id, 148)
        self.store.add_tag(article["id"], "誠信原則")
        self.store.set_note(article["id"], "帝王條款。")
        self.store.toggle_bookmark(article["id"])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_all_formats_registered(self):
        self.assertEqual(
            sorted(EXPORTERS), ["bundle", "csv", "json", "markdown", "refs", "text"]
        )

    def test_markdown(self):
        content = export(self.store, "markdown", self.law_id)
        self.assertIn("# 中華民國民法（節錄）", content)
        self.assertIn("第一編 總則", content)
        self.assertIn("第一百四十八條", content)
        self.assertIn("`誠信原則`", content)
        self.assertIn("> 筆記：帝王條款。", content)

    def test_json_structure(self):
        payload = json.loads(export(self.store, "json", self.law_id))
        law = payload["laws"][0]
        self.assertEqual(law["name"], "中華民國民法（節錄）")
        self.assertEqual(len(law["articles"]), 10)
        article = next(item for item in law["articles"] if item["number"] == 3)
        self.assertEqual(len(article["paragraphs"]), 3)

    def test_bundle_has_offline_fields(self):
        payload = json.loads(export(self.store, "bundle", None))
        self.assertEqual(payload["kind"], "lawkit-bundle")
        self.assertTrue(payload["bookmarks"])
        law = payload["laws"][0]
        self.assertIn("divisions", law)
        self.assertIn("refs", law["articles"][0])

    def test_csv(self):
        rows = list(csv.reader(io.StringIO(export(self.store, "csv", self.law_id))))
        self.assertEqual(rows[0][0], "法規")
        self.assertEqual(len(rows), 11)
        tagged = [row for row in rows if "誠信原則" in row[6]]
        self.assertEqual(len(tagged), 1)

    def test_reference_csv(self):
        content = export(self.store, "refs", self.law_id)
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(rows[0][2], "關係")

    def test_text_round_trip(self):
        content = export(self.store, "text", self.law_id)
        path = Path(self.tmp.name) / "round-trip.txt"
        path.write_text(content, encoding="utf-8")
        reparsed = parse_file(str(path))
        self.assertEqual(reparsed.name, "中華民國民法（節錄）")
        self.assertEqual(len(reparsed.articles), 10)
        self.assertEqual(reparsed.article(184).division_path[-1], "第五款 侵權行為")

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            export(self.store, "不存在", self.law_id)

    def test_export_all_laws(self):
        self.store.import_law(parse_file(str(SAMPLES / "示範資料保護法-舊版.txt")))
        content = export(self.store, "markdown", None)
        self.assertIn("中華民國民法（節錄）", content)
        self.assertIn("示範資料保護法", content)


if __name__ == "__main__":
    unittest.main()
