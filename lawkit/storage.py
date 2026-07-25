"""SQLite 儲存層：匯入法規、全文檢索、標籤、筆記、交互參照。

資料庫以 ``schema_version`` 搭配 :data:`MIGRATIONS` 管理版本，
未來新增欄位或資料表時，只要在 :data:`MIGRATIONS` 末端追加一段 SQL 即可，
既有資料庫會在開啟時自動升級。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .crossref import extract_references
from .models import Article, Law
from .numerals import format_article_label

DEFAULT_DB_PATH = "lawkit.db"

#: 每一項為一個版本的 SQL 腳本；只能往後追加，不可修改既有內容。
MIGRATIONS: list[str] = [
    # v1：法規本體、結構、條文、參照、標籤、筆記與全文索引
    """
    CREATE TABLE laws (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        short_name TEXT DEFAULT '',
        category TEXT DEFAULT '',
        authority TEXT DEFAULT '',
        promulgated TEXT DEFAULT '',
        amended TEXT DEFAULT '',
        version_label TEXT DEFAULT '',
        source TEXT DEFAULT '',
        imported_at TEXT NOT NULL,
        UNIQUE (name, version_label)
    );

    CREATE TABLE divisions (
        id INTEGER PRIMARY KEY,
        law_id INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        parent_seq INTEGER,
        kind TEXT NOT NULL,
        number INTEGER NOT NULL,
        sub INTEGER DEFAULT 0,
        title TEXT DEFAULT '',
        level INTEGER NOT NULL,
        heading TEXT NOT NULL
    );

    CREATE TABLE articles (
        id INTEGER PRIMARY KEY,
        law_id INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
        division_seq INTEGER,
        division_path TEXT DEFAULT '',
        number INTEGER NOT NULL,
        sub INTEGER DEFAULT 0,
        label TEXT NOT NULL,
        label_chinese TEXT NOT NULL,
        sort_key INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        is_deleted INTEGER DEFAULT 0,
        text TEXT NOT NULL,
        structure TEXT NOT NULL,
        UNIQUE (law_id, number, sub)
    );
    CREATE INDEX idx_articles_law ON articles(law_id, sort_key);

    CREATE TABLE refs (
        id INTEGER PRIMARY KEY,
        law_id INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
        from_article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        target_law TEXT,
        target_article INTEGER NOT NULL,
        target_sub INTEGER DEFAULT 0,
        target_paragraph INTEGER,
        target_item INTEGER,
        relation TEXT NOT NULL,
        raw_text TEXT DEFAULT '',
        context TEXT DEFAULT '',
        to_article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL
    );
    CREATE INDEX idx_refs_from ON refs(from_article_id);
    CREATE INDEX idx_refs_to ON refs(to_article_id);

    CREATE TABLE tags (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        color TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE article_tags (
        article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (article_id, tag_id)
    );

    CREATE TABLE notes (
        id INTEGER PRIMARY KEY,
        article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (article_id)
    );

    -- trigram 分詞器讓中文可以做子字串比對；不使用 contentless 以便直接刪除列
    CREATE VIRTUAL TABLE articles_fts USING fts5(
        label, text, law_name, tokenize='trigram'
    );
    """,
    # v2：收藏（我的最愛）與檢索紀錄，供 App 首頁使用
    """
    CREATE TABLE bookmarks (
        article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    );

    CREATE TABLE search_history (
        id INTEGER PRIMARY KEY,
        query TEXT NOT NULL,
        hits INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class SearchHit:
    """檢索結果。"""

    article_id: int
    law_id: int
    law_name: str
    label: str
    division_path: str
    snippet: str
    law_version: str = ""
    score: float = 0.0
    fuzzy: bool = False

    def to_dict(self) -> dict:
        return {
            "article_id": self.article_id,
            "law_id": self.law_id,
            "law_name": self.law_name,
            "law_version": self.law_version,
            "label": self.label,
            "division_path": self.division_path,
            "snippet": self.snippet,
            "score": self.score,
            "fuzzy": self.fuzzy,
        }


class LawStore:
    """法規資料庫。可作為 context manager 使用。"""

    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def __enter__(self) -> "LawStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    # ------------------------------------------------------------------ 遷移
    def _migrate(self) -> None:
        cursor = self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = self.connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = row["v"] or 0
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            self.connection.executescript(script)
            self.connection.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        self.connection.commit()
        cursor.close()

    @property
    def schema_version(self) -> int:
        row = self.connection.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return row["v"] or 0

    # ------------------------------------------------------------------ 匯入
    def import_law(self, law: Law, *, version_label: str = "", replace: bool = False) -> int:
        """匯入一部法規（一個版本）。同名同版本時可用 ``replace`` 覆蓋。"""
        label = version_label or law.version_label
        existing = self.connection.execute(
            "SELECT id FROM laws WHERE name = ? AND version_label = ?", (law.name, label)
        ).fetchone()
        if existing:
            if not replace:
                raise ValueError(
                    f"已存在相同版本：{law.name}（{label}）。可加上 --replace 覆蓋，"
                    "或用 --version 指定其他版本標籤。"
                )
            self.delete_law(existing["id"])

        cursor = self.connection.execute(
            """
            INSERT INTO laws (name, short_name, category, authority, promulgated,
                              amended, version_label, source, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                law.name,
                law.short_name,
                law.category,
                law.authority,
                law.promulgated,
                law.amended,
                label,
                law.source,
                _now(),
            ),
        )
        law_id = int(cursor.lastrowid)

        for division in law.divisions:
            self.connection.execute(
                """
                INSERT INTO divisions (law_id, seq, parent_seq, kind, number, sub,
                                       title, level, heading)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    law_id,
                    division.seq,
                    division.parent_seq,
                    division.kind,
                    division.number,
                    division.sub,
                    division.title,
                    division.level,
                    division.heading,
                ),
            )

        for article in law.articles:
            self._insert_article(law_id, law.name, article)

        self.connection.commit()
        self.resolve_references(law_id)
        return law_id

    def _insert_article(self, law_id: int, law_name: str, article: Article) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO articles (law_id, division_seq, division_path, number, sub, label,
                                  label_chinese, sort_key, seq, is_deleted, text, structure)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                law_id,
                article.division_seq,
                " > ".join(article.division_path),
                article.number,
                article.sub,
                article.label(),
                article.label("chinese"),
                article.sort_key,
                article.seq,
                1 if article.is_deleted else 0,
                article.text,
                json.dumps([p.to_dict() for p in article.paragraphs], ensure_ascii=False),
            ),
        )
        article_id = int(cursor.lastrowid)
        self.connection.execute(
            "INSERT INTO articles_fts (rowid, label, text, law_name) VALUES (?, ?, ?, ?)",
            (article_id, f"{article.label()} {article.label('chinese')}", article.text, law_name),
        )
        for reference in extract_references(article.text):
            for number, sub in reference.targets():
                self.connection.execute(
                    """
                    INSERT INTO refs (law_id, from_article_id, target_law, target_article,
                                      target_sub, target_paragraph, target_item, relation,
                                      raw_text, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        law_id,
                        article_id,
                        reference.target_law,
                        number,
                        sub,
                        reference.paragraph,
                        reference.item,
                        reference.relation,
                        reference.text,
                        reference.context,
                    ),
                )
        return article_id

    def delete_law(self, law_id: int) -> None:
        rows = self.connection.execute(
            "SELECT id FROM articles WHERE law_id = ?", (law_id,)
        ).fetchall()
        for row in rows:
            self.connection.execute("DELETE FROM articles_fts WHERE rowid = ?", (row["id"],))
        self.connection.execute("DELETE FROM laws WHERE id = ?", (law_id,))
        self.connection.commit()

    def resolve_references(self, law_id: int | None = None) -> int:
        """把引用連到實際條文（本法優先，其次比對其他法規名稱）。"""
        params: tuple[Any, ...] = ()
        clause = ""
        if law_id is not None:
            clause = "WHERE r.law_id = ?"
            params = (law_id,)
        rows = self.connection.execute(
            f"""
            SELECT r.id, r.law_id, r.target_law, r.target_article, r.target_sub
            FROM refs r {clause}
            """,
            params,
        ).fetchall()

        resolved = 0
        for row in rows:
            if row["target_law"]:
                target = self.connection.execute(
                    """
                    SELECT a.id FROM articles a JOIN laws l ON l.id = a.law_id
                    WHERE (l.name = ? OR l.short_name = ?) AND a.number = ? AND a.sub = ?
                    ORDER BY l.imported_at DESC LIMIT 1
                    """,
                    (row["target_law"], row["target_law"], row["target_article"], row["target_sub"]),
                ).fetchone()
            else:
                target = self.connection.execute(
                    "SELECT id FROM articles WHERE law_id = ? AND number = ? AND sub = ?",
                    (row["law_id"], row["target_article"], row["target_sub"]),
                ).fetchone()
            if target:
                self.connection.execute(
                    "UPDATE refs SET to_article_id = ? WHERE id = ?", (target["id"], row["id"])
                )
                resolved += 1
        self.connection.commit()
        return resolved

    # ------------------------------------------------------------------ 查詢
    def list_laws(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT l.*, (SELECT COUNT(*) FROM articles a WHERE a.law_id = l.id) AS article_count
            FROM laws l ORDER BY l.name, l.version_label
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_law(self, law_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM laws WHERE id = ?", (law_id,)).fetchone()
        return dict(row) if row else None

    def find_law(self, keyword: str, version_label: str = "") -> dict | None:
        """依名稱、簡稱或 id 找一部法規；未指定版本時取最新匯入者。"""
        if str(keyword).isdigit():
            found = self.get_law(int(keyword))
            if found:
                return found
        params: list[Any] = [keyword, keyword, f"%{keyword}%"]
        clause = "WHERE (name = ? OR short_name = ? OR name LIKE ?)"
        if version_label:
            clause += " AND version_label = ?"
            params.append(version_label)
        row = self.connection.execute(
            f"SELECT * FROM laws {clause} ORDER BY imported_at DESC LIMIT 1", params
        ).fetchone()
        return dict(row) if row else None

    def law_versions(self, name: str) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM laws WHERE name = ? ORDER BY version_label", (name,)
        ).fetchall()
        return [dict(row) for row in rows]

    def divisions(self, law_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM divisions WHERE law_id = ? ORDER BY seq", (law_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def articles(self, law_id: int) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM articles WHERE law_id = ? ORDER BY sort_key", (law_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_article(self, article_id: int) -> dict | None:
        row = self.connection.execute(
            """
            SELECT a.*, l.name AS law_name FROM articles a
            JOIN laws l ON l.id = a.law_id WHERE a.id = ?
            """,
            (article_id,),
        ).fetchone()
        if not row:
            return None
        article = dict(row)
        article["structure"] = json.loads(article["structure"] or "[]")
        article["tags"] = self.article_tags(article_id)
        article["note"] = self.get_note(article_id)
        article["bookmarked"] = self.is_bookmarked(article_id)
        article["outgoing"] = self.outgoing_references(article_id)
        article["incoming"] = self.incoming_references(article_id)
        return article

    def find_article(self, law_id: int, number: int, sub: int = 0) -> dict | None:
        row = self.connection.execute(
            "SELECT id FROM articles WHERE law_id = ? AND number = ? AND sub = ?",
            (law_id, number, sub),
        ).fetchone()
        return self.get_article(row["id"]) if row else None

    def tree(self, law_id: int) -> list[dict]:
        """回傳編章節與條文交錯的巡覽樹。"""
        divisions = {division["seq"]: {**division, "children": []} for division in self.divisions(law_id)}
        roots: list[dict] = []
        for division in divisions.values():
            parent = divisions.get(division["parent_seq"]) if division["parent_seq"] is not None else None
            (parent["children"] if parent else roots).append(division)

        loose: list[dict] = []
        for article in self.articles(law_id):
            entry = {
                "type": "article",
                "id": article["id"],
                "label": article["label"],
                "label_chinese": article["label_chinese"],
                "is_deleted": article["is_deleted"],
                "preview": article["text"].split("\n")[0][:60],
            }
            owner = divisions.get(article["division_seq"]) if article["division_seq"] is not None else None
            (owner["children"] if owner else loose).append(entry)

        for division in divisions.values():
            division["type"] = "division"
        return roots + loose

    # ------------------------------------------------------------------ 檢索
    def search(
        self,
        query: str,
        *,
        law_ids: Sequence[int] | None = None,
        tags: Sequence[str] | None = None,
        limit: int = 50,
        record: bool = False,
        fuzzy: bool = True,
    ) -> list[SearchHit]:
        """全文檢索。

        三字以上使用 FTS5 trigram 索引，較短則退回 LIKE 比對。
        完全相符找不到時，若 ``fuzzy`` 為真會再做一次「字序寬鬆比對」，
        讓「誠實信用」也能找到條文中的「誠實及信用」。
        """
        term = query.strip()
        if not term:
            return []

        hits = self._search_exact(term, law_ids=law_ids, tags=tags, limit=limit)
        if not hits and fuzzy and 2 <= len(term) <= 10:
            hits = self._search_loose(term, law_ids=law_ids, tags=tags, limit=limit)
        if record:
            self.connection.execute(
                "INSERT INTO search_history (query, hits, created_at) VALUES (?, ?, ?)",
                (term, len(hits), _now()),
            )
            self.connection.commit()
        return hits

    def _search_exact(
        self,
        term: str,
        *,
        law_ids: Sequence[int] | None,
        tags: Sequence[str] | None,
        limit: int,
    ) -> list[SearchHit]:
        params: list[Any] = []
        joins = ""
        where: list[str] = []

        if len(term) >= 3:
            joins += " JOIN articles_fts f ON f.rowid = a.id"
            where.append("articles_fts MATCH ?")
            params.append(f'"{term}"')
            order = "f.rank"
            score = "f.rank"
        else:
            where.append("(a.text LIKE ? OR a.label LIKE ? OR a.label_chinese LIKE ?)")
            like = f"%{term}%"
            params.extend([like, like, like])
            order = "a.law_id, a.sort_key"
            score = "0"

        if law_ids:
            placeholders = ",".join("?" * len(law_ids))
            where.append(f"a.law_id IN ({placeholders})")
            params.extend(law_ids)
        if tags:
            placeholders = ",".join("?" * len(tags))
            joins += (
                " JOIN article_tags at ON at.article_id = a.id"
                " JOIN tags t ON t.id = at.tag_id"
            )
            where.append(f"t.name IN ({placeholders})")
            params.extend(tags)

        sql = f"""
            SELECT DISTINCT a.id, a.law_id, a.label, a.division_path, a.text,
                   l.name AS law_name, l.version_label, {score} AS score
            FROM articles a JOIN laws l ON l.id = a.law_id{joins}
            WHERE {' AND '.join(where)}
            ORDER BY {order} LIMIT ?
        """
        params.append(limit)
        rows = self.connection.execute(sql, params).fetchall()
        return [
            SearchHit(
                article_id=row["id"],
                law_id=row["law_id"],
                law_name=row["law_name"],
                law_version=row["version_label"] or "",
                label=row["label"],
                division_path=row["division_path"],
                snippet=make_snippet(row["text"], term),
                score=float(row["score"] or 0),
            )
            for row in rows
        ]

    def _search_loose(
        self,
        term: str,
        *,
        law_ids: Sequence[int] | None,
        tags: Sequence[str] | None,
        limit: int,
    ) -> list[SearchHit]:
        """字序寬鬆比對：字元順序相同即可，中間允許插入其他文字。"""
        pattern = "%" + "%".join(term) + "%"
        params: list[Any] = [pattern]
        joins = ""
        where = ["a.text LIKE ?"]

        if law_ids:
            placeholders = ",".join("?" * len(law_ids))
            where.append(f"a.law_id IN ({placeholders})")
            params.extend(law_ids)
        if tags:
            placeholders = ",".join("?" * len(tags))
            joins = (
                " JOIN article_tags at ON at.article_id = a.id"
                " JOIN tags t ON t.id = at.tag_id"
            )
            where.append(f"t.name IN ({placeholders})")
            params.extend(tags)
        params.append(limit)

        rows = self.connection.execute(
            f"""
            SELECT DISTINCT a.id, a.law_id, a.label, a.division_path, a.text,
                   l.name AS law_name, l.version_label
            FROM articles a JOIN laws l ON l.id = a.law_id{joins}
            WHERE {' AND '.join(where)}
            ORDER BY a.law_id, a.sort_key LIMIT ?
            """,
            params,
        ).fetchall()

        return [
            SearchHit(
                article_id=row["id"],
                law_id=row["law_id"],
                law_name=row["law_name"],
                law_version=row["version_label"] or "",
                label=row["label"],
                division_path=row["division_path"],
                snippet=make_snippet(row["text"], term[0]),
                fuzzy=True,
            )
            for row in rows
        ]

    def search_history(self, limit: int = 10) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT query, MAX(created_at) AS created_at, SUM(hits) AS hits
            FROM search_history GROUP BY query ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ 參照
    def outgoing_references(self, article_id: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT r.*, l.name AS target_law_name, a.label AS target_label
            FROM refs r
            LEFT JOIN articles a ON a.id = r.to_article_id
            LEFT JOIN laws l ON l.id = a.law_id
            WHERE r.from_article_id = ?
            ORDER BY r.target_article, r.target_sub
            """,
            (article_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def incoming_references(self, article_id: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT r.*, a.label AS from_label, a.id AS from_id, l.name AS from_law_name
            FROM refs r
            JOIN articles a ON a.id = r.from_article_id
            JOIN laws l ON l.id = a.law_id
            WHERE r.to_article_id = ?
            ORDER BY l.name, a.sort_key
            """,
            (article_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def reference_report(self, law_id: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT a.label AS from_label, r.relation, r.target_law, r.target_article,
                   r.target_sub, r.raw_text, r.to_article_id
            FROM refs r JOIN articles a ON a.id = r.from_article_id
            WHERE r.law_id = ? ORDER BY a.sort_key, r.target_article
            """,
            (law_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------- 標籤／筆記／收藏
    def ensure_tag(self, name: str, color: str = "") -> int:
        self.connection.execute(
            "INSERT OR IGNORE INTO tags (name, color, created_at) VALUES (?, ?, ?)",
            (name, color, _now()),
        )
        if color:
            self.connection.execute("UPDATE tags SET color = ? WHERE name = ?", (color, name))
        self.connection.commit()
        row = self.connection.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def list_tags(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT t.*, (SELECT COUNT(*) FROM article_tags at WHERE at.tag_id = t.id) AS usage
            FROM tags t ORDER BY t.name
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def add_tag(self, article_id: int, name: str, color: str = "") -> None:
        tag_id = self.ensure_tag(name, color)
        self.connection.execute(
            "INSERT OR IGNORE INTO article_tags (article_id, tag_id, created_at) VALUES (?, ?, ?)",
            (article_id, tag_id, _now()),
        )
        self.connection.commit()

    def remove_tag(self, article_id: int, name: str) -> None:
        self.connection.execute(
            """
            DELETE FROM article_tags
            WHERE article_id = ? AND tag_id = (SELECT id FROM tags WHERE name = ?)
            """,
            (article_id, name),
        )
        self.connection.commit()

    def article_tags(self, article_id: int) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT t.name, t.color FROM article_tags at
            JOIN tags t ON t.id = at.tag_id WHERE at.article_id = ? ORDER BY t.name
            """,
            (article_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def articles_by_tag(self, name: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT a.id, a.label, a.text, l.name AS law_name FROM article_tags at
            JOIN tags t ON t.id = at.tag_id
            JOIN articles a ON a.id = at.article_id
            JOIN laws l ON l.id = a.law_id
            WHERE t.name = ? ORDER BY l.name, a.sort_key
            """,
            (name,),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_note(self, article_id: int, body: str) -> None:
        now = _now()
        self.connection.execute(
            """
            INSERT INTO notes (article_id, body, created_at, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET body = excluded.body, updated_at = excluded.updated_at
            """,
            (article_id, body, now, now),
        )
        self.connection.commit()

    def get_note(self, article_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT body, created_at, updated_at FROM notes WHERE article_id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_notes(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT n.*, a.label, l.name AS law_name FROM notes n
            JOIN articles a ON a.id = n.article_id
            JOIN laws l ON l.id = a.law_id ORDER BY n.updated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def toggle_bookmark(self, article_id: int) -> bool:
        if self.is_bookmarked(article_id):
            self.connection.execute("DELETE FROM bookmarks WHERE article_id = ?", (article_id,))
            self.connection.commit()
            return False
        self.connection.execute(
            "INSERT INTO bookmarks (article_id, created_at) VALUES (?, ?)", (article_id, _now())
        )
        self.connection.commit()
        return True

    def is_bookmarked(self, article_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM bookmarks WHERE article_id = ?", (article_id,)
        ).fetchone()
        return row is not None

    def list_bookmarks(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT b.created_at, a.id, a.label, a.text, l.name AS law_name
            FROM bookmarks b JOIN articles a ON a.id = b.article_id
            JOIN laws l ON l.id = a.law_id ORDER BY b.created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ 統計
    def stats(self) -> dict:
        def scalar(sql: str, params: Iterable[Any] = ()) -> int:
            row = self.connection.execute(sql, tuple(params)).fetchone()
            return int(row[0] or 0)

        relations = self.connection.execute(
            "SELECT relation, COUNT(*) AS count FROM refs GROUP BY relation ORDER BY count DESC"
        ).fetchall()
        return {
            "laws": scalar("SELECT COUNT(*) FROM laws"),
            "articles": scalar("SELECT COUNT(*) FROM articles"),
            "deleted_articles": scalar("SELECT COUNT(*) FROM articles WHERE is_deleted = 1"),
            "references": scalar("SELECT COUNT(*) FROM refs"),
            "resolved_references": scalar("SELECT COUNT(*) FROM refs WHERE to_article_id IS NOT NULL"),
            "tags": scalar("SELECT COUNT(*) FROM tags"),
            "notes": scalar("SELECT COUNT(*) FROM notes"),
            "bookmarks": scalar("SELECT COUNT(*) FROM bookmarks"),
            "relations": [dict(row) for row in relations],
            "schema_version": self.schema_version,
        }

    def law_stats(self, law_id: int) -> dict:
        articles = self.articles(law_id)
        lengths = [len(article["text"]) for article in articles if not article["is_deleted"]]
        row = self.connection.execute(
            "SELECT COUNT(*) FROM refs WHERE law_id = ?", (law_id,)
        ).fetchone()
        return {
            "articles": len(articles),
            "deleted": sum(1 for article in articles if article["is_deleted"]),
            "divisions": len(self.divisions(law_id)),
            "references": int(row[0] or 0),
            "average_length": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "longest": max(
                (
                    {"label": article["label"], "length": len(article["text"])}
                    for article in articles
                ),
                key=lambda item: item["length"],
                default={"label": "-", "length": 0},
            ),
        }


def make_snippet(text: str, term: str, width: int = 60) -> str:
    """產生含關鍵字的摘要，關鍵字以 ``【】`` 標示。"""
    flat = text.replace("\n", " ")
    position = flat.find(term)
    if position < 0:
        return flat[: width * 2] + ("…" if len(flat) > width * 2 else "")
    start = max(0, position - width // 2)
    end = min(len(flat), position + len(term) + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    highlighted = (
        flat[start:position] + "【" + flat[position : position + len(term)] + "】" + flat[position + len(term) : end]
    )
    return f"{prefix}{highlighted}{suffix}"


def article_label(number: int, sub: int = 0) -> str:
    return format_article_label(number, sub)
