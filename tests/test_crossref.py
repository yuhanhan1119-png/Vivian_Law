import unittest

from lawkit.crossref import extract_references, reference_summary


class ExtractReferenceTests(unittest.TestCase):
    def test_internal_reference(self):
        refs = extract_references("違反第五條規定者，處罰之。")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].article, 5)
        self.assertIsNone(refs[0].target_law)

    def test_chinese_and_arabic_numbers(self):
        refs = extract_references("依第一百九十七條及第 12 條辦理。")
        self.assertEqual([ref.article for ref in refs], [197, 12])

    def test_sub_article(self):
        refs = extract_references("準用第十一條之一之規定。")
        self.assertEqual((refs[0].article, refs[0].sub), (11, 1))

    def test_paragraph_and_item(self):
        refs = extract_references("依第十條第一項第五款請求刪除者。")
        self.assertEqual((refs[0].article, refs[0].paragraph, refs[0].item), (10, 1, 5))

    def test_external_law_name(self):
        refs = extract_references("損害賠償之請求，適用民法第一百九十七條之規定。")
        self.assertEqual(refs[0].target_law, "民法")
        self.assertEqual(refs[0].article, 197)

    def test_verb_is_not_part_of_law_name(self):
        for text, expected in [
            ("適用刑法第三百二十條規定。", "刑法"),
            ("依民法第七百五十八條辦理。", "民法"),
            ("準用行政程序法第九十六條之規定。", "行政程序法"),
            ("違反個人資料保護法第五條者。", "個人資料保護法"),
        ]:
            with self.subTest(text=text):
                refs = extract_references(text)
                self.assertEqual(refs[0].target_law, expected)

    def test_self_reference_words_are_internal(self):
        refs = extract_references("本法第五條所定事項。")
        self.assertIsNone(refs[0].target_law)

    def test_range_expands(self):
        refs = extract_references("違反第五條至第八條規定者，處罰鍰。")
        self.assertTrue(refs[0].is_range)
        self.assertEqual(refs[0].targets(), [(5, 0), (6, 0), (7, 0), (8, 0)])

    def test_list_inherits_law_name(self):
        refs = extract_references("適用民法第一百八十四條、第一百九十五條之規定。")
        self.assertEqual([ref.target_law for ref in refs], ["民法", "民法"])

    def test_relations(self):
        cases = {
            "準用前條及第八條之規定。": "準用",
            "適用第十五條規定。": "適用",
            "依第十條所為之請求。": "依據",
            "參照第三條意旨。": "參照",
            "不適用第九條之規定。": "除外",
        }
        for text, relation in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_references(text)[0].relation, relation)

    def test_describe_and_dict(self):
        ref = extract_references("準用民法第十二條之一第二項第三款規定。")[0]
        self.assertEqual(ref.describe(), "民法 第 12-1 條 第 2 項 第 3 款")
        self.assertEqual(ref.to_dict()["relation"], "準用")

    def test_no_reference(self):
        self.assertEqual(extract_references("本法自公布日施行。"), [])

    def test_summary(self):
        refs = extract_references("準用第五條；適用第六條；依第七條辦理。")
        summary = reference_summary(refs)
        self.assertEqual(sum(summary.values()), 3)


if __name__ == "__main__":
    unittest.main()
