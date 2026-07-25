import unittest

from lawkit.numerals import (
    ArticleNumber,
    chinese_to_int,
    format_article_label,
    int_to_chinese,
    parse_article_number,
)


class ChineseToIntTests(unittest.TestCase):
    def test_simple_digits(self):
        self.assertEqual(chinese_to_int("一"), 1)
        self.assertEqual(chinese_to_int("九"), 9)
        self.assertEqual(chinese_to_int("零"), 0)

    def test_tens_and_hundreds(self):
        cases = {
            "十": 10,
            "十二": 12,
            "二十": 20,
            "二十一": 21,
            "一百": 100,
            "一百十四": 114,
            "一百一十四": 114,
            "一百二十三": 123,
            "三百二十": 320,
            "一千零五": 1005,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(chinese_to_int(text), expected)

    def test_arabic_and_fullwidth(self):
        self.assertEqual(chinese_to_int("42"), 42)
        self.assertEqual(chinese_to_int("１２３"), 123)
        self.assertEqual(chinese_to_int(" 7 "), 7)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            chinese_to_int("條")
        with self.assertRaises(ValueError):
            chinese_to_int("")


class IntToChineseTests(unittest.TestCase):
    def test_legal_style(self):
        self.assertEqual(int_to_chinese(13), "十三")
        self.assertEqual(int_to_chinese(113), "一百十三")
        self.assertEqual(int_to_chinese(120), "一百二十")
        self.assertEqual(int_to_chinese(1000), "一千")
        self.assertEqual(int_to_chinese(197), "一百九十七")

    def test_round_trip(self):
        for value in list(range(1, 300)) + [320, 758, 1234]:
            with self.subTest(value=value):
                self.assertEqual(chinese_to_int(int_to_chinese(value)), value)


class ArticleNumberTests(unittest.TestCase):
    def test_parse_variants(self):
        cases = {
            "第 12 條": ArticleNumber(12, 0),
            "第12條": ArticleNumber(12, 0),
            "第12-1條": ArticleNumber(12, 1),
            "第 12-1 條": ArticleNumber(12, 1),
            "第十二條": ArticleNumber(12, 0),
            "第一百十四條之二": ArticleNumber(114, 2),
            "第 1 條之 3": ArticleNumber(1, 3),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_article_number(text), expected)

    def test_sort_key_orders_sub_articles(self):
        keys = [ArticleNumber(12).sort_key, ArticleNumber(12, 1).sort_key, ArticleNumber(13).sort_key]
        self.assertEqual(keys, sorted(keys))

    def test_labels(self):
        self.assertEqual(format_article_label(12), "第 12 條")
        self.assertEqual(format_article_label(12, 1), "第 12-1 條")
        self.assertEqual(format_article_label(12, 1, style="chinese"), "第十二條之一")

    def test_reject_non_article(self):
        with self.assertRaises(ValueError):
            parse_article_number("第一章")


if __name__ == "__main__":
    unittest.main()
