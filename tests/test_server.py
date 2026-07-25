import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lawkit.config import Workspace
from lawkit.parser import parse_file
from lawkit.server import create_server
from lawkit.storage import LawStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.workspace = Workspace.open(Path(cls.tmp.name) / "Vivian_Law")
        store = LawStore(str(cls.workspace.db_path))
        cls.law_id = store.import_law(parse_file(str(SAMPLES / "民法-節錄.txt")))
        cls.old_id = store.import_law(parse_file(str(SAMPLES / "示範資料保護法-舊版.txt")))
        cls.new_id = store.import_law(parse_file(str(SAMPLES / "示範資料保護法-新版.txt")))
        cls.article_id = store.find_article(cls.law_id, 148)["id"]
        store.close()

        cls.httpd = create_server(cls.workspace, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str):
        with urllib.request.urlopen(self.url(path), timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def send(self, path: str, method: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.url(path), data=data, method=method, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def raw(self, path: str):
        with urllib.request.urlopen(self.url(path), timeout=10) as response:
            return response.status, response.headers.get("Content-Type"), response.read()


class ApiTests(ServerTestCase):
    def test_health(self):
        status, payload = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_meta_includes_paths_and_exporters(self):
        _, payload = self.get("/api/meta")
        self.assertIn("資料根目錄", payload["paths"])
        self.assertTrue(any(item["name"] == "markdown" for item in payload["exporters"]))
        self.assertEqual(payload["stats"]["laws"], 3)

    def test_laws_and_tree(self):
        _, laws = self.get("/api/laws")
        self.assertEqual(len(laws), 3)
        _, law = self.get(f"/api/laws/{self.law_id}")
        self.assertEqual(law["name"], "中華民國民法（節錄）")
        self.assertTrue(law["tree"])
        _, tree = self.get(f"/api/laws/{self.law_id}/tree")
        self.assertEqual(tree[0]["kind"], "編")

    def test_law_not_found(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/laws/9999")
        self.assertEqual(caught.exception.code, 404)

    def test_article_detail(self):
        _, article = self.get(f"/api/articles/{self.article_id}")
        self.assertEqual(article["label"], "第 148 條")
        self.assertIn("誠實及信用方法", article["text"])
        self.assertIsInstance(article["structure"], list)

    def test_search(self):
        _, payload = self.get("/api/search?q=%E6%90%8D%E5%AE%B3%E8%B3%A0%E5%84%9F")
        self.assertGreater(payload["count"], 0)
        self.assertIn("snippet", payload["hits"][0])

    def test_tags_notes_bookmarks_flow(self):
        status, payload = self.send(f"/api/articles/{self.article_id}/tags", "POST", {"name": "誠信"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["tags"][0]["name"], "誠信")

        _, payload = self.send(f"/api/articles/{self.article_id}/note", "PUT", {"body": "帝王條款"})
        self.assertEqual(payload["note"]["body"], "帝王條款")

        _, payload = self.send(f"/api/articles/{self.article_id}/bookmark", "POST", {})
        self.assertTrue(payload["bookmarked"])
        _, bookmarks = self.get("/api/bookmarks")
        self.assertEqual(len(bookmarks), 1)
        _, payload = self.send(f"/api/articles/{self.article_id}/bookmark", "POST", {})
        self.assertFalse(payload["bookmarked"])

        _, notes = self.get("/api/notes")
        self.assertEqual(len(notes), 1)

        _, tagged = self.get("/api/tags/%E8%AA%A0%E4%BF%A1")
        self.assertEqual(len(tagged), 1)

        _, payload = self.send(
            f"/api/articles/{self.article_id}/tags/%E8%AA%A0%E4%BF%A1", "DELETE"
        )
        self.assertEqual(payload["tags"], [])

    def test_add_tag_requires_name(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.send(f"/api/articles/{self.article_id}/tags", "POST", {"name": " "})
        self.assertEqual(caught.exception.code, 400)

    def test_diff(self):
        _, payload = self.get(f"/api/diff?from={self.old_id}&to={self.new_id}")
        self.assertEqual(payload["law_name"], "示範資料保護法")
        labels = {change["label"]: change["status"] for change in payload["changes"]}
        self.assertEqual(labels["第 11-1 條"], "新增")

    def test_diff_requires_parameters(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/diff")
        self.assertEqual(caught.exception.code, 400)

    def test_bundle_for_offline(self):
        _, payload = self.get("/api/bundle")
        self.assertEqual(payload["kind"], "lawkit-bundle")
        self.assertEqual(len(payload["laws"]), 3)

    def test_export_api(self):
        _, payload = self.get(f"/api/export?format=markdown&law={self.law_id}")
        self.assertIn("# 中華民國民法（節錄）", payload["content"])

    def test_import_then_delete_via_api(self):
        text = "法規名稱：API 測試法\n第 1 條\n本法自公布日施行。\n第 2 條\n準用第一條之規定。"
        status, payload = self.send("/api/import", "POST", {"text": text, "replace": True})
        self.assertEqual(status, 200)
        self.assertEqual(payload["name"], "API 測試法")
        self.assertEqual(payload["stats"]["articles"], 2)

        # 刪除以維持其他測試看到的法規數量（同一個 class 共用伺服器）
        _, deleted = self.send(f"/api/laws/{payload['law_id']}", "DELETE")
        self.assertTrue(deleted["deleted"])

    def test_import_rejects_empty(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.send("/api/import", "POST", {"text": "  "})
        self.assertEqual(caught.exception.code, 400)

    def test_unknown_api(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/nope")
        self.assertEqual(caught.exception.code, 404)


class StaticFileTests(ServerTestCase):
    def test_index(self):
        status, content_type, body = self.raw("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("法規整理", body.decode("utf-8"))

    def test_app_assets(self):
        for path, expected in [
            ("/app.js", "javascript"),
            ("/styles.css", "text/css"),
            ("/manifest.webmanifest", "application/manifest+json"),
            ("/sw.js", "javascript"),
            ("/icons/icon.svg", "image/svg+xml"),
            ("/icons/icon-192.png", "image/png"),
        ]:
            with self.subTest(path=path):
                status, content_type, body = self.raw(path)
                self.assertEqual(status, 200)
                self.assertIn(expected, content_type)
                self.assertTrue(body)

    def test_manifest_is_valid_json(self):
        _, _, body = self.raw("/manifest.webmanifest")
        manifest = json.loads(body.decode("utf-8"))
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(any(icon["sizes"] == "512x512" for icon in manifest["icons"]))

    def test_unknown_path_falls_back_to_index(self):
        status, _, body = self.raw("/some/deep/route")
        self.assertEqual(status, 200)
        self.assertIn("<title>", body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
