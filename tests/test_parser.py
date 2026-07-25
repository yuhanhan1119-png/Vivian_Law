import unittest
from pathlib import Path

from lawkit.parser import parse_article_body, parse_file, parse_law

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

SIMPLE = """法規名稱：測試法
修正日期：民國 112 年 01 月 01 日

第 一 編 總則
第 一 章 法例
第 1 條
民事，法律所未規定者，依習慣；無習慣者，依法理。
第 2 條
本法用詞，定義如下：
一、甲：指第一種情形。
二、乙：指第二種情形。
　　前項各款情形，準用第一條之規定。
第 2-1 條
本條為之一條號測試。
第 3 條
（刪除）
"""


class MetadataTests(unittest.TestCase):
    def test_metadata_and_counts(self):
        law = parse_law(SIMPLE)
        self.assertEqual(law.name, "測試法")
        self.assertEqual(law.amended, "民國 112 年 01 月 01 日")
        self.assertEqual(law.version_label, "民國 112 年 01 月 01 日")
        self.assertEqual(len(law.articles), 4)

    def test_name_falls_back_to_first_line(self):
        law = parse_law("勞動基準法\n第 1 條\n為規定勞動條件最低標準，特制定本法。")
        self.assertEqual(law.name, "勞動基準法")
        self.assertEqual(len(law.articles), 1)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.law = parse_law(SIMPLE)

    def test_divisions(self):
        kinds = [(division.kind, division.number, division.title) for division in self.law.divisions]
        self.assertEqual(kinds, [("編", 1, "總則"), ("章", 1, "法例")])
        self.assertEqual(self.law.divisions[1].parent_seq, 0)
        self.assertEqual(self.law.divisions[0].heading, "第一編 總則")

    def test_article_division_path(self):
        first = self.law.article(1)
        self.assertEqual(first.division_path, ["第一編 總則", "第一章 法例"])

    def test_sub_article_number(self):
        article = self.law.article(2, 1)
        self.assertIsNotNone(article)
        self.assertEqual(article.label(), "第 2-1 條")
        self.assertEqual(article.label("chinese"), "第二條之一")

    def test_deleted_article(self):
        article = self.law.article(3)
        self.assertTrue(article.is_deleted)
        self.assertEqual(article.text, "（刪除）")

    def test_ordering_places_sub_article_after_parent(self):
        labels = [article.label() for article in self.law.articles]
        self.assertEqual(labels, ["第 1 條", "第 2 條", "第 2-1 條", "第 3 條"])


class ParagraphTests(unittest.TestCase):
    def test_paragraphs_items(self):
        law = parse_law(SIMPLE)
        article = law.article(2)
        self.assertEqual(len(article.paragraphs), 2)
        first = article.paragraphs[0]
        self.assertEqual(first.text, "本法用詞，定義如下：")
        self.assertEqual([item.label for item in first.items], ["一、", "二、"])
        self.assertEqual(first.items[0].text, "甲：指第一種情形。")
        self.assertIn("準用第一條", article.paragraphs[1].text)

    def test_nested_items(self):
        body = [
            "本法之主管機關如下：",
            "一、在中央：為某部。",
            "二、在地方：",
            "（一）直轄市：為直轄市政府。",
            "（二）縣（市）：為縣（市）政府。",
        ]
        paragraphs = parse_article_body(body)
        self.assertEqual(len(paragraphs), 1)
        items = paragraphs[0].items
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1].label, "二、")
        self.assertEqual([child.label for child in items[1].children], ["（一）", "（二）"])
        self.assertEqual(items[1].children[0].kind, "目")

    def test_paragraph_indexes_are_sequential(self):
        paragraphs = parse_article_body(["第一項內容。", "第二項內容。", "第三項內容。"])
        self.assertEqual([p.index for p in paragraphs], [1, 2, 3])

    def test_continuation_line_merges_into_item(self):
        paragraphs = parse_article_body(["下列事項：", "一、這一款的文字很長，換行了", "所以要接回同一款。"])
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(paragraphs[0].items[0].text, "這一款的文字很長，換行了所以要接回同一款。")

    def test_flat_text_contains_items(self):
        law = parse_law(SIMPLE)
        text = law.article(2).text
        self.assertIn("一、甲：指第一種情形。", text)


class FalsePositiveTests(unittest.TestCase):
    """條文內文提到「第一款」「第五條」時，不可誤判為章節或條號。"""

    def test_body_mentioning_division_is_not_heading(self):
        law = parse_law("測試法\n第 5 條\n第一款所定之事項，應由主管機關公告。\n第 6 條\n本法自公布日施行。")
        self.assertEqual(len(law.divisions), 0)
        self.assertEqual(len(law.articles), 2)
        self.assertIn("第一款所定之事項", law.article(5).text)

    def test_reference_in_text_is_not_article_heading(self):
        law = parse_law("測試法\n第 5 條\n違反第三條規定者，處罰之。第四條亦同。")
        self.assertEqual(len(law.articles), 1)
        self.assertIn("違反第三條規定者", law.article(5).text)

    def test_article_heading_with_inline_text(self):
        law = parse_law("測試法\n第 1 條　本法所稱主管機關，指某部。")
        self.assertEqual(law.article(1).text, "本法所稱主管機關，指某部。")


class SampleFileTests(unittest.TestCase):
    def test_civil_code_sample(self):
        law = parse_file(str(SAMPLES / "民法-節錄.txt"))
        self.assertEqual(law.name, "中華民國民法（節錄）")
        self.assertEqual(law.amended, "民國 110 年 01 月 13 日")
        self.assertTrue(any(division.kind == "款" for division in law.divisions))

        article_3 = law.article(3)
        self.assertEqual(len(article_3.paragraphs), 3)
        self.assertTrue(article_3.paragraphs[1].text.startswith("如有用印章代簽名者"))

        article_184 = law.article(184)
        self.assertEqual(article_184.division_path[-1], "第五款 侵權行為")
        self.assertEqual(len(article_184.paragraphs), 2)

    def test_demo_law_versions(self):
        old = parse_file(str(SAMPLES / "示範資料保護法-舊版.txt"))
        new = parse_file(str(SAMPLES / "示範資料保護法-新版.txt"))
        self.assertEqual(old.name, "示範資料保護法")
        self.assertEqual(new.name, "示範資料保護法")
        self.assertNotEqual(old.version_label, new.version_label)
        self.assertIsNone(old.article(11, 1))
        self.assertIsNotNone(new.article(11, 1))
        self.assertTrue(new.article(17).is_deleted)
        self.assertTrue(old.article(9).is_deleted)

        nested = old.article(3)
        self.assertEqual(len(nested.paragraphs[0].items), 2)
        self.assertEqual(len(nested.paragraphs[0].items[1].children), 2)


if __name__ == "__main__":
    unittest.main()
