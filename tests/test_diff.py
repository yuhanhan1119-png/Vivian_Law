import tempfile
import unittest
from pathlib import Path

from lawkit.diff import (
    STATUS_ADDED,
    STATUS_MODIFIED,
    STATUS_REMOVED,
    STATUS_UNCHANGED,
    diff_article_lists,
    diff_law_versions,
    diff_texts,
    render_diff_text,
)
from lawkit.parser import parse_file
from lawkit.storage import LawStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class TextDiffTests(unittest.TestCase):
    def test_segments(self):
        segments = diff_texts("處二萬元以下罰鍰", "處五萬元以下罰鍰")
        kinds = [segment.kind for segment in segments]
        self.assertIn("delete", kinds)
        self.assertIn("insert", kinds)
        joined_new = "".join(s.text for s in segments if s.kind != "delete")
        self.assertEqual(joined_new, "處五萬元以下罰鍰")

    def test_identical_text_has_only_equal(self):
        segments = diff_texts("相同的文字", "相同的文字")
        self.assertEqual([segment.kind for segment in segments], ["equal"])


class ArticleListDiffTests(unittest.TestCase):
    def test_statuses(self):
        old = [
            {"number": 1, "sub": 0, "label": "第 1 條", "text": "原文一"},
            {"number": 2, "sub": 0, "label": "第 2 條", "text": "原文二"},
            {"number": 3, "sub": 0, "label": "第 3 條", "text": "要被刪掉"},
        ]
        new = [
            {"number": 1, "sub": 0, "label": "第 1 條", "text": "原文一"},
            {"number": 2, "sub": 0, "label": "第 2 條", "text": "修正後的原文二"},
            {"number": 4, "sub": 0, "label": "第 4 條", "text": "新增條文"},
        ]
        result = diff_article_lists(old, new, law_name="測試法")
        statuses = {change.label: change.status for change in result.changes}
        self.assertEqual(statuses["第 1 條"], STATUS_UNCHANGED)
        self.assertEqual(statuses["第 2 條"], STATUS_MODIFIED)
        self.assertEqual(statuses["第 3 條"], STATUS_REMOVED)
        self.assertEqual(statuses["第 4 條"], STATUS_ADDED)
        self.assertEqual(result.summary[STATUS_MODIFIED], 1)
        self.assertEqual(len(result.changed()), 3)

    def test_exclude_unchanged(self):
        old = [{"number": 1, "sub": 0, "label": "第 1 條", "text": "一樣"}]
        new = [{"number": 1, "sub": 0, "label": "第 1 條", "text": "一樣"}]
        result = diff_article_lists(old, new, include_unchanged=False)
        self.assertEqual(result.changes, [])

    def test_sub_article_sorted_after_parent(self):
        old = [{"number": 11, "sub": 0, "label": "第 11 條", "text": "舊"}]
        new = [
            {"number": 11, "sub": 0, "label": "第 11 條", "text": "舊"},
            {"number": 11, "sub": 1, "label": "第 11-1 條", "text": "新增之一"},
        ]
        result = diff_article_lists(old, new)
        self.assertEqual([change.label for change in result.changes], ["第 11 條", "第 11-1 條"])


class LawVersionDiffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LawStore(str(Path(self.tmp.name) / "diff.db"))
        self.old_id = self.store.import_law(parse_file(str(SAMPLES / "示範資料保護法-舊版.txt")))
        self.new_id = self.store.import_law(parse_file(str(SAMPLES / "示範資料保護法-新版.txt")))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_sample_versions_diff(self):
        result = diff_law_versions(self.store, self.old_id, self.new_id, include_unchanged=False)
        statuses = {change.label: change.status for change in result.changes}
        self.assertEqual(statuses["第 5 條"], STATUS_MODIFIED)
        self.assertEqual(statuses["第 11 條"], STATUS_MODIFIED)
        self.assertEqual(statuses["第 11-1 條"], STATUS_ADDED)
        self.assertEqual(statuses["第 17 條"], STATUS_MODIFIED)  # 內容改為（刪除）
        self.assertEqual(statuses["第 18 條"], STATUS_ADDED)
        self.assertNotIn("第 1 條", statuses)

    def test_report_text(self):
        result = diff_law_versions(self.store, self.old_id, self.new_id)
        report = render_diff_text(result)
        self.assertIn("示範資料保護法 修法比對", report)
        self.assertIn("第 11-1 條", report)

    def test_missing_law_raises(self):
        with self.assertRaises(ValueError):
            diff_law_versions(self.store, 9999, self.new_id)


if __name__ == "__main__":
    unittest.main()
