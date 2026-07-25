import tempfile
import unittest
from pathlib import Path

from lawkit.parser import parse_file, parse_law
from lawkit.storage import LawStore, make_snippet

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LawStore(str(Path(self.tmp.name) / "test.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def import_sample(self, filename: str, **kwargs) -> int:
        law = parse_file(str(SAMPLES / filename))
        return self.store.import_law(law, **kwargs)


class SchemaTests(StoreTestCase):
    def test_migrations_applied(self):
        from lawkit.storage import MIGRATIONS

        self.assertEqual(self.store.schema_version, len(MIGRATIONS))

    def test_reopen_is_idempotent(self):
        path = self.store.path
        self.store.close()
        first = LawStore(path)
        version = first.schema_version
        first.close()
        second = LawStore(path)
        self.assertEqual(second.schema_version, version)
        second.close()


class ImportTests(StoreTestCase):
    def test_import_and_stats(self):
        law_id = self.import_sample("民法-節錄.txt")
        stats = self.store.law_stats(law_id)
        self.assertEqual(stats["articles"], 10)
        self.assertGreater(stats["divisions"], 5)
        self.assertGreater(stats["average_length"], 0)
        self.assertEqual(stats["longest"]["label"], "第 3 條")  # 三項的簽名條文最長

    def test_reference_count_in_stats(self):
        law_id = self.import_sample("示範資料保護法-舊版.txt")
        self.assertGreater(self.store.law_stats(law_id)["references"], 0)

    def test_duplicate_version_rejected(self):
        self.import_sample("民法-節錄.txt")
        with self.assertRaises(ValueError):
            self.import_sample("民法-節錄.txt")

    def test_replace_allows_reimport(self):
        self.import_sample("民法-節錄.txt")
        law_id = self.import_sample("民法-節錄.txt", replace=True)
        self.assertEqual(len(self.store.list_laws()), 1)
        self.assertEqual(len(self.store.articles(law_id)), 10)
        self.assertTrue(self.store.search("損害賠償"))  # 全文索引也要重建

    def test_two_versions_coexist(self):
        self.import_sample("示範資料保護法-舊版.txt")
        self.import_sample("示範資料保護法-新版.txt")
        versions = self.store.law_versions("示範資料保護法")
        self.assertEqual(len(versions), 2)

    def test_find_law_by_partial_name_and_id(self):
        law_id = self.import_sample("民法-節錄.txt")
        self.assertEqual(self.store.find_law("民法")["id"], law_id)
        self.assertEqual(self.store.find_law(str(law_id))["id"], law_id)
        self.assertIsNone(self.store.find_law("不存在的法"))

    def test_delete_law_clears_index(self):
        law_id = self.import_sample("民法-節錄.txt")
        self.store.delete_law(law_id)
        self.assertEqual(self.store.list_laws(), [])
        self.assertEqual(self.store.search("損害賠償"), [])


class TreeTests(StoreTestCase):
    def test_tree_nests_articles_under_divisions(self):
        law_id = self.import_sample("民法-節錄.txt")
        tree = self.store.tree(law_id)
        self.assertTrue(tree)
        first = tree[0]
        self.assertEqual(first["kind"], "編")
        chapter = first["children"][0]
        self.assertEqual(chapter["kind"], "章")
        articles = [node for node in chapter["children"] if node["type"] == "article"]
        self.assertEqual(articles[0]["label"], "第 1 條")


class SearchTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.law_id = self.import_sample("民法-節錄.txt")

    def test_fts_search(self):
        hits = self.store.search("損害賠償")
        self.assertTrue(hits)
        self.assertIn("184", hits[0].label)
        self.assertIn("【損害賠償】", hits[0].snippet)
        self.assertEqual(hits[0].law_version, "民國 110 年 01 月 13 日")

    def test_search_distinguishes_versions(self):
        self.import_sample("示範資料保護法-舊版.txt")
        self.import_sample("示範資料保護法-新版.txt")
        versions = {hit.law_version for hit in self.store.search("准駁之決定")}
        self.assertEqual(len(versions), 2)

    def test_short_term_falls_back_to_like(self):
        hits = self.store.search("習慣")
        self.assertTrue(hits)
        self.assertTrue(any(hit.label == "第 1 條" for hit in hits))

    def test_search_limited_to_law(self):
        self.import_sample("示範資料保護法-舊版.txt")
        all_hits = self.store.search("損害賠償")
        civil_hits = self.store.search("損害賠償", law_ids=[self.law_id])
        self.assertGreater(len(all_hits), len(civil_hits))

    def test_search_by_tag(self):
        article = self.store.find_article(self.law_id, 184)
        self.store.add_tag(article["id"], "侵權")
        hits = self.store.search("損害賠償", tags=["侵權"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].article_id, article["id"])

    def test_history_recorded(self):
        self.store.search("誠實信用", record=True)
        history = self.store.search_history()
        self.assertEqual(history[0]["query"], "誠實信用")

    def test_snippet_without_match(self):
        self.assertTrue(make_snippet("一二三四五", "沒有"))


class ReferenceTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.law_id = self.import_sample("示範資料保護法-舊版.txt")

    def test_internal_reference_resolved(self):
        article = self.store.find_article(self.law_id, 11)
        outgoing = article["outgoing"]
        self.assertTrue(outgoing)
        self.assertTrue(all(ref["to_article_id"] for ref in outgoing))
        self.assertEqual(outgoing[0]["target_article"], 10)

    def test_incoming_reference(self):
        article = self.store.find_article(self.law_id, 10)
        self.assertTrue(any(ref["from_label"] == "第 11 條" for ref in article["incoming"]))

    def test_range_reference_expanded(self):
        article = self.store.find_article(self.law_id, 16)
        targets = sorted(ref["target_article"] for ref in article["outgoing"])
        self.assertEqual(targets, [5, 6, 7, 8])

    def test_cross_law_reference_links_after_import(self):
        civil_id = self.import_sample("民法-節錄.txt")
        self.store.resolve_references()
        article = self.store.find_article(self.law_id, 15)
        external = [ref for ref in article["outgoing"] if ref["target_law"] == "民法"]
        self.assertTrue(external)
        # 民法節錄沒有第 197 條，因此仍為未連結，但法規名稱要正確擷取
        self.assertEqual(external[0]["target_article"], 197)
        self.assertIsNotNone(civil_id)

    def test_reference_report(self):
        rows = self.store.reference_report(self.law_id)
        self.assertTrue(rows)
        self.assertIn("relation", rows[0])


class AnnotationTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.law_id = self.import_sample("民法-節錄.txt")
        self.article = self.store.find_article(self.law_id, 148)

    def test_tags(self):
        self.store.add_tag(self.article["id"], "誠信原則", color="#c00")
        self.store.add_tag(self.article["id"], "常考")
        names = [tag["name"] for tag in self.store.article_tags(self.article["id"])]
        self.assertEqual(sorted(names), ["常考", "誠信原則"])

        self.store.remove_tag(self.article["id"], "常考")
        self.assertEqual(len(self.store.article_tags(self.article["id"])), 1)

        listed = {tag["name"]: tag for tag in self.store.list_tags()}
        self.assertEqual(listed["誠信原則"]["usage"], 1)
        self.assertEqual(listed["誠信原則"]["color"], "#c00")

    def test_tag_is_reusable_across_articles(self):
        other = self.store.find_article(self.law_id, 71)
        self.store.add_tag(self.article["id"], "重點")
        self.store.add_tag(other["id"], "重點")
        self.assertEqual(len(self.store.articles_by_tag("重點")), 2)

    def test_notes(self):
        self.store.set_note(self.article["id"], "誠信原則的具體化。")
        self.assertEqual(self.store.get_note(self.article["id"])["body"], "誠信原則的具體化。")
        self.store.set_note(self.article["id"], "更新後的筆記。")
        self.assertEqual(self.store.get_note(self.article["id"])["body"], "更新後的筆記。")
        self.assertEqual(len(self.store.list_notes()), 1)

    def test_bookmarks(self):
        self.assertTrue(self.store.toggle_bookmark(self.article["id"]))
        self.assertTrue(self.store.is_bookmarked(self.article["id"]))
        self.assertEqual(len(self.store.list_bookmarks()), 1)
        self.assertFalse(self.store.toggle_bookmark(self.article["id"]))
        self.assertEqual(self.store.list_bookmarks(), [])

    def test_stats_counts_everything(self):
        self.store.add_tag(self.article["id"], "重點")
        self.store.set_note(self.article["id"], "筆記")
        self.store.toggle_bookmark(self.article["id"])
        stats = self.store.stats()
        self.assertEqual(stats["laws"], 1)
        self.assertEqual(stats["tags"], 1)
        self.assertEqual(stats["notes"], 1)
        self.assertEqual(stats["bookmarks"], 1)
        self.assertGreater(stats["articles"], 0)


class ParsedLawImportTests(StoreTestCase):
    def test_import_from_text(self):
        law = parse_law("測試法\n第 1 條\n本法自公布日施行。", name="測試法")
        law_id = self.store.import_law(law, version_label="v1")
        self.assertEqual(self.store.get_law(law_id)["version_label"], "v1")
        self.assertEqual(len(self.store.articles(law_id)), 1)


if __name__ == "__main__":
    unittest.main()
